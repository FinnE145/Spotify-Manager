"""Artist identity: Spotify issues more than one artist id for the same
artist, so detection and every artist-level aggregate must compare resolved
ids (see docs/specs/detection-artist-model.md). Owns artist_alias and
reviewed_artist_pair only; never touches track or membership.

Read-only w.r.t. Spotify -- no calls here."""

from collections import defaultdict

import normalize
import scoring


def _pair_key(a, b):
    return (a, b) if a < b else (b, a)


# -- Per-track artist sets, for detection --------------------------------


def artist_sets(conn):
    """{track_id: {"artist_ids", "primary_ids", "featured_ids"}} with every
    id resolved through artist_alias. Tracks with no credits are absent."""
    sets = defaultdict(lambda: {"artist_ids": set(), "primary_ids": set(), "featured_ids": set()})
    for row in conn.execute("SELECT track_id, artist_id, role FROM track_artist_role"):
        rec = sets[row["track_id"]]
        rec["artist_ids"].add(row["artist_id"])
        rec["primary_ids" if row["role"] == "primary" else "featured_ids"].add(row["artist_id"])
    return sets


# -- Alias curation ------------------------------------------------------


def _alias_group(conn, artist_id):
    """Every id that resolves to the same canonical artist as artist_id,
    including artist_id and the canonical id itself."""
    row = conn.execute(
        "SELECT canonical_artist_id FROM artist_alias WHERE artist_id = ?", (artist_id,)
    ).fetchone()
    canonical = row["canonical_artist_id"] if row else artist_id
    members = {
        r["artist_id"]
        for r in conn.execute(
            "SELECT artist_id FROM artist_alias WHERE canonical_artist_id = ?", (canonical,)
        )
    }
    return members | {canonical, artist_id}


def _canonical_of(conn, artist_ids):
    """The id with the highest all_time score (docs/specs/scoring-H.md
    §11.3); ties broken by id ascending, so the choice is stable across
    runs."""
    scores = scoring.artist_scores(conn, list(artist_ids))
    return sorted(artist_ids, key=lambda a: (-scores.get(a, {}).get("all_time", 0.0), a))[0]


def mark_same(conn, artist_id_a, artist_id_b):
    """Merges both artists' alias groups onto one canonical id. Merging an
    already-merged artist folds its whole group in, so a third id joining
    two doesn't strand the first pair."""
    group = _alias_group(conn, artist_id_a) | _alias_group(conn, artist_id_b)
    canonical = _canonical_of(conn, group)
    conn.execute(
        "DELETE FROM artist_alias WHERE artist_id IN ({}) OR canonical_artist_id IN ({})".format(
            ",".join("?" * len(group)), ",".join("?" * len(group))
        ),
        [*group, *group],
    )
    conn.executemany(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        [(a, canonical) for a in sorted(group) if a != canonical],
    )
    _mark_reviewed(conn, artist_id_a, artist_id_b)
    conn.commit()
    scoring.request_recompute()


def mark_not_same(conn, artist_id_a, artist_id_b):
    _mark_reviewed(conn, artist_id_a, artist_id_b)
    conn.commit()


def _mark_reviewed(conn, artist_id_a, artist_id_b):
    a, b = _pair_key(artist_id_a, artist_id_b)
    conn.execute(
        "INSERT OR IGNORE INTO reviewed_artist_pair (artist_id_a, artist_id_b) VALUES (?, ?)",
        (a, b),
    )


def unmerge(conn, artist_id):
    """Drops the alias row and forgets every review tying this artist to its
    former group, returning those pairs to the queue -- nothing here is a
    one-way door.

    Clearing only the pair against the canonical id would strand the rest: in
    a 3+-id group the review that merged this artist may have been recorded
    against a sibling, not the canonical, so that pair would stay suppressed
    forever despite no longer being merged. The group has to be read before
    the alias row goes, or it's no longer derivable."""
    row = conn.execute(
        "SELECT canonical_artist_id FROM artist_alias WHERE artist_id = ?", (artist_id,)
    ).fetchone()
    if row is None:
        return
    group = _alias_group(conn, artist_id)
    conn.execute("DELETE FROM artist_alias WHERE artist_id = ?", (artist_id,))
    for other in group - {artist_id}:
        a, b = _pair_key(artist_id, other)
        conn.execute(
            "DELETE FROM reviewed_artist_pair WHERE artist_id_a = ? AND artist_id_b = ?", (a, b)
        )
    conn.commit()
    scoring.request_recompute()


# -- Candidate detection -------------------------------------------------


def _artist_rows(conn):
    counts_t = {
        r["artist_id"]: r["n"]
        for r in conn.execute("SELECT artist_id, COUNT(*) AS n FROM track_artist GROUP BY artist_id")
    }
    counts_a = {
        r["artist_id"]: r["n"]
        for r in conn.execute("SELECT artist_id, COUNT(*) AS n FROM album_artist GROUP BY artist_id")
    }
    rows = {}
    for r in conn.execute("SELECT artist_id, name, external_url FROM artist"):
        rows[r["artist_id"]] = {
            "artist_id": r["artist_id"],
            "name": r["name"],
            "external_url": r["external_url"],
            "track_count": counts_t.get(r["artist_id"], 0),
            "album_count": counts_a.get(r["artist_id"], 0),
        }
    return rows


def _sample_tracks(conn, artist_id, limit=4):
    return [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM track_artist ta JOIN track t ON t.track_id = ta.track_id "
            "WHERE ta.artist_id = ? ORDER BY t.name LIMIT ?",
            (artist_id, limit),
        )
    ]


def candidate_pairs(conn):
    """Pairs whose names normalize equal but whose ids differ, minus those
    already judged or already resolving to the same canonical artist.

    Name collision is the only automatic signal, so a Kanye West / Ye split
    needs a hand-written row -- nothing depends on this being exhaustive."""
    rows = _artist_rows(conn)
    reviewed = {
        (r["artist_id_a"], r["artist_id_b"])
        for r in conn.execute("SELECT artist_id_a, artist_id_b FROM reviewed_artist_pair")
    }
    resolved = {
        r["artist_id"]: r["canonical_artist_id"]
        for r in conn.execute("SELECT artist_id, canonical_artist_id FROM artist_alias")
    }

    buckets = defaultdict(list)
    for artist_id, rec in rows.items():
        buckets[normalize.base_string(rec["name"])].append(artist_id)

    pairs = []
    for _norm, ids in buckets.items():
        ids.sort()
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if _pair_key(a, b) in reviewed:
                    continue
                if resolved.get(a, a) == resolved.get(b, b):
                    continue
                pairs.append(
                    {
                        "a": {**rows[a], "sample_tracks": _sample_tracks(conn, a)},
                        "b": {**rows[b], "sample_tracks": _sample_tracks(conn, b)},
                    }
                )
    # Scored as one 2-artist collection rather than comparing two separate
    # numbers (docs/specs/scoring-H.md §11.1) -- the combiner doesn't know or
    # care whether it's combining one artist or several, and "the pair's
    # score" is the score of everything either one credits.
    for p in pairs:
        p["score"] = scoring.artist_group_score(
            conn, [p["a"]["artist_id"], p["b"]["artist_id"]]
        )["all_time"]
    pairs.sort(key=lambda p: (-p["score"], p["a"]["name"].casefold(), p["a"]["artist_id"]))
    return pairs


def merged_groups(conn):
    """Existing merges, for the unmerge list: one entry per canonical artist
    carrying the aliased ids folded into it."""
    rows = _artist_rows(conn)
    groups = defaultdict(list)
    for r in conn.execute(
        "SELECT artist_id, canonical_artist_id, decided_at FROM artist_alias "
        "ORDER BY decided_at DESC"
    ):
        groups[r["canonical_artist_id"]].append(
            {**rows.get(r["artist_id"], {"artist_id": r["artist_id"], "name": "(unknown)"}),
             "decided_at": r["decided_at"]}
        )
    return [
        {"canonical": rows.get(cid, {"artist_id": cid, "name": "(unknown)"}), "aliases": aliases}
        for cid, aliases in groups.items()
    ]
