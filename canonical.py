"""The four-tier canonical track grouping engine (see
docs/specs/canonical-tracks.md and docs/canonical-tracks/grouping-engine.md).
Read-only w.r.t. Spotify; owns canonical_group/track_group/reviewed_pair only
and never touches track or membership.

None of these functions commit — callers own the transaction."""

from collections import defaultdict

TIER_ORDER = ("release", "recording", "version", "song")
TIER_COLUMN = {tier: f"{tier}_id" for tier in TIER_ORDER}

# created_at is written explicitly rather than left to the column default: a
# DB created before the default was corrected still carries the old naive-UTC
# one, and CREATE TABLE IF NOT EXISTS never updates it.
_INSERT_GROUP_SQL = (
    "INSERT INTO canonical_group (tier, created_at) "
    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
)


def ensure_track_groups(conn):
    """For every track lacking a track_group row, allocate four fresh
    singleton groups and insert it. Idempotent."""
    rows = conn.execute(
        """
        SELECT t.track_id FROM track t
        LEFT JOIN track_group tg ON tg.track_id = t.track_id
        WHERE tg.track_id IS NULL
        """
    ).fetchall()
    for row in rows:
        ids = {}
        for tier in TIER_ORDER:
            cur = conn.execute(_INSERT_GROUP_SQL, (tier,))
            ids[tier] = cur.lastrowid
        conn.execute(
            "INSERT INTO track_group (track_id, song_id, version_id, recording_id, release_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["track_id"], ids["song"], ids["version"], ids["recording"], ids["release"]),
        )


def _get_column(conn, track_id, column):
    row = conn.execute(
        f"SELECT {column} FROM track_group WHERE track_id = ?", (track_id,)
    ).fetchone()
    return row[0] if row else None


def _validate_labels(conn, labels):
    if not labels:
        return

    for track_id, tier_labels in labels.items():
        missing_tiers = set(TIER_ORDER) - set(tier_labels)
        if missing_tiers:
            raise ValueError(f"{track_id}: missing label(s) for tier(s) {sorted(missing_tiers)}")

    placeholders = ",".join("?" for _ in labels)
    existing = {
        row["track_id"]
        for row in conn.execute(
            f"SELECT track_id FROM track WHERE track_id IN ({placeholders})", list(labels)
        )
    }
    missing_tracks = set(labels) - existing
    if missing_tracks:
        raise ValueError(f"unknown track ids: {sorted(missing_tracks)}")

    for finer, coarser in zip(TIER_ORDER, TIER_ORDER[1:]):
        finer_to_coarser = {}
        for tier_labels in labels.values():
            f, c = tier_labels[finer], tier_labels[coarser]
            if f in finer_to_coarser and finer_to_coarser[f] != c:
                raise ValueError(
                    f"labels not nested-consistent: {finer}={f!r} maps to both "
                    f"{coarser}={finer_to_coarser[f]!r} and {coarser}={c!r}"
                )
            finer_to_coarser[f] = c


def _cleanup_tier(conn, tier, column):
    empty_groups = conn.execute(
        f"""
        SELECT cg.id FROM canonical_group cg
        LEFT JOIN track_group tg ON tg.{column} = cg.id
        WHERE cg.tier = ? AND tg.track_id IS NULL
        """,
        (tier,),
    ).fetchall()
    for row in empty_groups:
        conn.execute("DELETE FROM canonical_group WHERE id = ?", (row["id"],))

    reps = conn.execute(
        "SELECT id, representative_track_id FROM canonical_group "
        "WHERE tier = ? AND representative_track_id IS NOT NULL",
        (tier,),
    ).fetchall()
    for row in reps:
        actual_group = _get_column(conn, row["representative_track_id"], column)
        if actual_group != row["id"]:
            conn.execute(
                "UPDATE canonical_group SET representative_track_id = NULL WHERE id = ?",
                (row["id"],),
            )


def cleanup_all_tiers(conn):
    """The four _cleanup_tier passes, for a caller that ran a batch of
    apply_partition(cleanup=False) calls and now owes the cleanup once."""
    for tier in TIER_ORDER:
        _cleanup_tier(conn, tier, TIER_COLUMN[tier])


def apply_partition(conn, labels, cleanup=True):
    """The one write operation for merge/detach/ungroup. See
    grouping-engine.md for the full reconciliation algorithm this
    implements.

    cleanup=False skips the four _cleanup_tier passes, which are a full
    canonical_group x track_group LEFT JOIN each and dominate the cost of a
    batch of calls (11.75s -> 1.15s over the 568-group auto-group run). Only
    a caller that runs cleanup_all_tiers() itself once its batch is done may
    pass it."""
    _validate_labels(conn, labels)
    if not labels:
        return {"tracks": {}, "dragged_in": []}

    ensure_track_groups(conn)

    # Ids of everything outside the item that any closure touches, snapshotted
    # up front so the return value can report only what genuinely moved.
    before = {}

    for i, tier in enumerate(TIER_ORDER):
        column = TIER_COLUMN[tier]
        finer_column = TIER_COLUMN[TIER_ORDER[i - 1]] if i > 0 else None

        # 1. Build parts: group the item's tracks by their tier label. This is
        # the only hard partition -- two item tracks with different labels can
        # never end up in the same part.
        parts = defaultdict(set)
        claimed = {}
        for track_id, tier_labels in labels.items():
            label = tier_labels[tier]
            parts[label].add(track_id)
            claimed[track_id] = label

        def claim(label, track_id):
            if track_id in claimed:
                return
            parts[label].add(track_id)
            claimed[track_id] = label
            if track_id not in labels and track_id not in before:
                before[track_id] = groups_for_track(conn, track_id)

        # 2. Downward closure (nesting-mandatory): pull in every track sharing
        # a member's just-assigned finer-tier group. Empty for the finest tier.
        if finer_column:
            for label, members in list(parts.items()):
                finer_ids = {_get_column(conn, tid, finer_column) for tid in list(members)}
                for finer_id in finer_ids:
                    for row in conn.execute(
                        f"SELECT track_id FROM track_group WHERE {finer_column} = ?", (finer_id,)
                    ).fetchall():
                        claim(label, row["track_id"])

        # 3. Upward closure (preservation): pull in the existing tier-t
        # group-mates of each member, so a group the item only partly covers
        # is preserved rather than silently cut loose. Anything already
        # claimed above is left alone -- that's the item genuinely splitting.
        for label, members in list(parts.items()):
            group_ids = {_get_column(conn, tid, column) for tid in list(members)}
            for group_id in group_ids:
                if group_id is None:
                    continue
                for row in conn.execute(
                    f"SELECT track_id FROM track_group WHERE {column} = ?", (group_id,)
                ).fetchall():
                    claim(label, row["track_id"])

        # 4. Choose the group id for each part.
        assignments = {}
        for label, members in parts.items():
            candidates = set()
            for tid in members:
                current_id = _get_column(conn, tid, column)
                if current_id is None:
                    continue
                full_membership = {
                    row["track_id"]
                    for row in conn.execute(
                        f"SELECT track_id FROM track_group WHERE {column} = ?", (current_id,)
                    )
                }
                if full_membership <= members:
                    candidates.add(current_id)
            if candidates:
                assignments[label] = min(candidates)
            else:
                cur = conn.execute(_INSERT_GROUP_SQL, (tier,))
                assignments[label] = cur.lastrowid

        # 5. Write.
        for label, members in parts.items():
            group_id = assignments[label]
            for tid in members:
                conn.execute(
                    f"UPDATE track_group SET {column} = ? WHERE track_id = ?", (group_id, tid)
                )

        # 6. Clean up orphaned groups and stale pinned representatives.
        if cleanup:
            _cleanup_tier(conn, tier, column)

    all_touched = set(labels) | set(before)
    tracks = {
        tid: {tier: _get_column(conn, tid, TIER_COLUMN[tier]) for tier in TIER_ORDER}
        for tid in all_touched
    }
    # Only outside tracks whose ids actually moved are worth a note -- ones the
    # upward closure merely preserved kept every id they had.
    dragged_in = [tid for tid, old in before.items() if old != tracks[tid]]
    return {"tracks": tracks, "dragged_in": sorted(dragged_in)}


def mark_reviewed_pairs(conn, pairs):
    """Inserts each given (track_id_a, track_id_b) pair into reviewed_pair,
    refreshing decided_at on conflict. The pair-level writer -- callers that
    mean "every pair in this bucket" go through mark_reviewed() below;
    callers that mean something narrower (e.g. cross-component pairs only,
    see canonical_detect.cross_component_pairs) call this directly."""
    for a, b in pairs:
        conn.execute(
            """
            INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT(track_id_a, track_id_b) DO UPDATE SET decided_at = excluded.decided_at
            """,
            (a, b),
        )


def mark_reviewed(conn, track_ids):
    """Inserts every unordered pair from track_ids into reviewed_pair."""
    ids = sorted(set(track_ids))
    mark_reviewed_pairs(conn, [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))])


def representative(conn, group_id):
    """The pinned track for a group, or the computed default: highest score
    (docs/specs/scoring-H.md §11.3) -> oldest added_at -> lowest track_id.
    Reads the `score` table directly by raw SQL rather than importing
    scoring.py, which imports this module for ensure_track_groups()."""
    row = conn.execute(
        "SELECT tier, representative_track_id FROM canonical_group WHERE id = ?", (group_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no canonical_group with id {group_id}")
    if row["representative_track_id"] is not None:
        return row["representative_track_id"]

    column = TIER_COLUMN[row["tier"]]
    members = conn.execute(
        f"""
        SELECT tg.track_id,
               COALESCE(sc.all_time, 0.0) AS score,
               (SELECT MIN(added_at) FROM membership m WHERE m.track_id = tg.track_id) AS oldest_added
        FROM track_group tg
        LEFT JOIN score sc ON sc.tier = 'track' AND sc.group_id = tg.track_id
        WHERE tg.{column} = ?
        """,
        (group_id,),
    ).fetchall()
    if not members:
        return None
    best = min(members, key=lambda r: (-r["score"], r["oldest_added"] or "9999", r["track_id"]))
    return best["track_id"]


def pin_representative(conn, track_id):
    """Pins track_id as the representative for its song group. Song is the
    only tier anything displays or consumes a representative for."""
    row = conn.execute(
        "SELECT song_id FROM track_group WHERE track_id = ?", (track_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no track_group row for track {track_id}")
    conn.execute(
        "UPDATE canonical_group SET representative_track_id = ? WHERE id = ?",
        (track_id, row["song_id"]),
    )


def group_members(conn, group_id):
    row = conn.execute("SELECT tier FROM canonical_group WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise ValueError(f"no canonical_group with id {group_id}")
    column = TIER_COLUMN[row["tier"]]
    return [
        r["track_id"]
        for r in conn.execute(f"SELECT track_id FROM track_group WHERE {column} = ?", (group_id,))
    ]


def groups_for_track(conn, track_id):
    row = conn.execute(
        "SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "song": row["song_id"],
        "version": row["version_id"],
        "recording": row["recording_id"],
        "release": row["release_id"],
    }


# The tier one step finer than each key (mirrors the finer_column apply_partition
# computes via TIER_ORDER). "release" has no finer tier -- subtree() and
# _enrich() below both special-case it as where nesting bottoms out at tracks.
_FINER_TIER = {"song": "version", "version": "recording", "recording": "release"}

# subtree()'s field name for a node's nested children, keyed by the child
# tier (i.e. what a version-node nests its recordings under, a recording-node
# its releases under, a release-node its plain track ids under).
_SUBTREE_FIELD = {"version": "recordings", "recording": "releases", "release": "track_ids"}

# group_tree()'s equivalent: additionally covers "song" (the top wrapper when
# called at song tier), and renames a release's plain "track_ids" to "tracks"
# now that its members carry the full display, not just an id.
_ENRICHED_FIELD = {"song": "versions", "version": "recordings", "recording": "releases", "release": "tracks"}


def subtree(conn, tier, group_id):
    """The nesting from `tier` down to member track ids. nested_tree(conn, song_id)
    is subtree(conn, "song", song_id); a release has no finer tier, so its
    subtree is simply its member track ids."""
    if tier == "release":
        return [
            row["track_id"]
            for row in conn.execute("SELECT track_id FROM track_group WHERE release_id = ?", (group_id,))
        ]
    finer = _FINER_TIER[tier]
    finer_column = TIER_COLUMN[finer]
    return [
        {f"{finer}_id": row[finer_column], _SUBTREE_FIELD[finer]: subtree(conn, finer, row[finer_column])}
        for row in conn.execute(
            f"SELECT DISTINCT {finer_column} FROM track_group WHERE {TIER_COLUMN[tier]} = ?", (group_id,)
        )
    ]


def nested_tree(conn, song_id):
    """The song's version -> recording -> release -> track nesting."""
    return subtree(conn, "song", song_id)


def _is_pinned(conn, group_id):
    row = conn.execute(
        "SELECT representative_track_id FROM canonical_group WHERE id = ?", (group_id,)
    ).fetchone()
    return bool(row and row["representative_track_id"])


def _artist_display(conn, track_id):
    """Rendered artist strings, loaded once per connection.

    track_artists is cheap to scan whole (~36ms) but not to probe one row at
    a time: SQLite won't push a track_id filter through the view's GROUP BY,
    so each single lookup rebuilds the entire credit chain. song_tree does
    hundreds of these, which put /dev/canonical over 20s. Flask hands out a
    fresh connection per request and the pull thread has its own, so the
    cache can never outlive the data it was built from."""
    cache = getattr(conn, "_artist_display_cache", None)
    if cache is None:
        cache = {
            row["track_id"]: row["artists"]
            for row in conn.execute("SELECT track_id, artists FROM track_artists")
        }
        conn._artist_display_cache = cache
    return cache.get(track_id, "")


def track_display(conn, track_id):
    row = conn.execute(
        """
        SELECT t.track_id, t.name, t.album_id, t.duration_ms, t.explicit,
               t.external_url, t.uri, t.isrc, t.track_number, t.disc_number, t.is_playable,
               t.linked_from, t.linked_from_id,
               a.name AS album_name, a.image_url AS album_image_url
        FROM track t
        LEFT JOIN album a ON a.album_id = t.album_id
        WHERE t.track_id = ?
        """,
        (track_id,),
    ).fetchone()
    live_count = conn.execute(
        "SELECT COUNT(*) FROM membership WHERE track_id = ? AND removed_at IS NULL", (track_id,)
    ).fetchone()[0]
    info = dict(row)
    info["artists"] = _artist_display(conn, track_id)
    info["live_count"] = live_count
    return info


def artist_credits_for_tracks(conn, track_ids):
    """{track_id: [{"artist_id", "name"}, ...]} in credit order, for linking
    individual artist names -- track_artists/track_display's "artists" field
    is a pre-joined display string with no per-artist id to link to. One
    batched query over exactly the tracks a page needs, the same shape as
    _artist_display's whole-view preload: track_artist_credit still has to
    compute its GROUP BY once regardless, so batching beats a lookup per
    track for the same reason a per-track probe of track_artists does."""
    if not track_ids:
        return {}
    placeholders = ",".join("?" for _ in track_ids)
    credits = defaultdict(list)
    for row in conn.execute(
        f"SELECT track_id, artist_id, name FROM track_artist_credit "
        f"WHERE track_id IN ({placeholders}) ORDER BY track_id, position",
        list(track_ids),
    ):
        credits[row["track_id"]].append({"artist_id": row["artist_id"], "name": row["name"]})
    return dict(credits)


def song_group_rows(conn, query="", include_singletons=False):
    """Song-tier groups for the /dev/canonical browser -- id and size only,
    unordered. The caller ranks by score (docs/specs/scoring-H.md §11.1) --
    that needs scoring.py, which imports this module for ensure_track_groups(),
    so the ranking can't live here without a cycle.

    Deliberately unhydrated regardless: the listing renders a capped slice, and
    filling in a representative track_display for every group cost 307ms of
    that page's 800ms only to discard most of them. Pass the slice you
    actually render to hydrate_song_groups()."""
    rows = conn.execute(
        "SELECT tg.song_id, COUNT(*) AS track_count FROM track_group tg GROUP BY tg.song_id"
    ).fetchall()

    like = f"%{query}%" if query else None
    results = []
    for row in rows:
        if not include_singletons and row["track_count"] <= 1:
            continue
        song_id = row["song_id"]
        if like:
            match = conn.execute(
                """
                SELECT 1 FROM track_group tg
                JOIN track t ON t.track_id = tg.track_id
                WHERE tg.song_id = ?
                  AND (t.name LIKE ?
                       OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id)
                                  WHERE x.track_id = t.track_id AND ar.name LIKE ?))
                LIMIT 1
                """,
                (song_id, like, like),
            ).fetchone()
            if not match:
                continue
        results.append({"song_id": song_id, "track_count": row["track_count"]})
    return results


def hydrate_song_groups(conn, rows):
    """song_group_rows() output with each group's representative track and
    pinned flag filled in -- several queries per group, so only ever call it on
    the rows being rendered."""
    out = []
    for row in rows:
        rep_id = representative(conn, row["song_id"])
        out.append(
            {
                **row,
                "representative_track_id": rep_id,
                "representative": track_display(conn, rep_id) if rep_id else None,
                "pinned": _is_pinned(conn, row["song_id"]),
            }
        )
    return out


def _enrich(conn, tier, nodes):
    """subtree()'s output for one tier, with track-id leaves replaced by
    track_display() dicts. Returns (enriched_nodes, flat_track_ids)."""
    if tier == "release":
        return [track_display(conn, tid) for tid in nodes], list(nodes)
    finer = _FINER_TIER[tier]
    field = _SUBTREE_FIELD[finer]
    enriched_field = _ENRICHED_FIELD[finer]
    enriched, flat = [], []
    for node in nodes:
        children, child_flat = _enrich(conn, finer, node[field])
        enriched.append({f"{finer}_id": node[f"{finer}_id"], enriched_field: children})
        flat.extend(child_flat)
    return enriched, flat


def group_tree(conn, tier, group_id):
    """Full nesting from `tier` down to tracks, enriched with track display
    fields. song_tree(conn, song_id) is group_tree(conn, "song", song_id).
    Only the top node carries a flat track-id list and a representative --
    representative() is tier-agnostic, but in practice only song-tier groups
    are ever pinned."""
    enriched, flat = _enrich(conn, tier, subtree(conn, tier, group_id))
    return {
        f"{tier}_id": group_id,
        "representative_track_id": representative(conn, group_id),
        "pinned": _is_pinned(conn, group_id),
        "track_ids": flat,
        _ENRICHED_FIELD[tier]: enriched,
    }


def song_tree(conn, song_id):
    """Full version -> recording -> release -> track nesting for one song
    group, enriched with track display fields. Only the song node carries a
    track-id list (for its "Edit in queue" link) and a representative -- the
    finer nodes have neither: the queue edits all four tiers of the whole
    song group at once, and representative is a song-level-only concept."""
    return group_tree(conn, "song", song_id)


def auto_grouped_ids(conn):
    """Group ids an auto-group run created, for the viewer's badge. A later
    manual edit that reconciles a group away takes the flag with it, which is
    correct -- the group is no longer purely machine-decided."""
    return {
        row["id"]
        for row in conn.execute("SELECT id FROM canonical_group WHERE auto_run_id IS NOT NULL")
    }


def tier_counts(conn):
    """Distinct group counts per tier, and how many are non-singleton."""
    result = {}
    for tier in TIER_ORDER:
        column = TIER_COLUMN[tier]
        total = conn.execute(
            "SELECT COUNT(*) FROM canonical_group WHERE tier = ?", (tier,)
        ).fetchone()[0]
        non_singleton = conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT {column} FROM track_group GROUP BY {column} HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        result[tier] = {"total": total, "non_singleton": non_singleton}
    return result
