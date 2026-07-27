"""The four-tier canonical track grouping engine (see
docs/specs/canonical-tracks.md and docs/canonical-tracks/grouping-engine.md).
Read-only w.r.t. Spotify; owns canonical_group/track_group/reviewed_pair only
and never touches track or membership.

None of these functions commit — callers own the transaction."""

from collections import defaultdict

TIER_ORDER = ("release", "recording", "version", "song")
TIER_COLUMN = {tier: f"{tier}_id" for tier in TIER_ORDER}


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
            cur = conn.execute("INSERT INTO canonical_group (tier) VALUES (?)", (tier,))
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


def apply_partition(conn, labels):
    """The one write operation for merge/detach/ungroup. See
    grouping-engine.md for the full reconciliation algorithm this
    implements."""
    _validate_labels(conn, labels)
    if not labels:
        return {"tracks": {}, "dragged_in": []}

    ensure_track_groups(conn)

    dragged_in = set()

    for i, tier in enumerate(TIER_ORDER):
        column = TIER_COLUMN[tier]
        finer_column = TIER_COLUMN[TIER_ORDER[i - 1]] if i > 0 else None

        # 1. Build parts: group the item's tracks by their tier label.
        parts = defaultdict(set)
        for track_id, tier_labels in labels.items():
            parts[tier_labels[tier]].add(track_id)

        # 2. Downward closure: pull in every track sharing a member's
        # just-assigned finer-tier group (empty for the finest tier).
        if finer_column:
            for members in parts.values():
                finer_ids = {_get_column(conn, tid, finer_column) for tid in members}
                for finer_id in finer_ids:
                    extra = conn.execute(
                        f"SELECT track_id FROM track_group WHERE {finer_column} = ?", (finer_id,)
                    ).fetchall()
                    for row in extra:
                        tid = row["track_id"]
                        if tid not in members:
                            members.add(tid)
                            if tid not in labels:
                                dragged_in.add(tid)

        # 3. Choose the group id for each part.
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
                cur = conn.execute("INSERT INTO canonical_group (tier) VALUES (?)", (tier,))
                assignments[label] = cur.lastrowid

        # 4. Write.
        for label, members in parts.items():
            group_id = assignments[label]
            for tid in members:
                conn.execute(
                    f"UPDATE track_group SET {column} = ? WHERE track_id = ?", (group_id, tid)
                )

        # 5. Clean up orphaned groups and stale pinned representatives.
        _cleanup_tier(conn, tier, column)

    all_touched = set(labels) | dragged_in
    tracks = {
        tid: {tier: _get_column(conn, tid, TIER_COLUMN[tier]) for tier in TIER_ORDER}
        for tid in all_touched
    }
    return {"tracks": tracks, "dragged_in": sorted(dragged_in)}


def mark_reviewed(conn, track_ids):
    """Inserts every unordered pair from track_ids into reviewed_pair,
    refreshing decided_at on conflict."""
    ids = sorted(set(track_ids))
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            conn.execute(
                """
                INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(track_id_a, track_id_b) DO UPDATE SET decided_at = excluded.decided_at
                """,
                (a, b),
            )


def representative(conn, group_id):
    """The pinned track for a group, or the computed default: most live
    memberships -> oldest added_at -> lowest track_id."""
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
               (SELECT COUNT(*) FROM membership m
                WHERE m.track_id = tg.track_id AND m.removed_at IS NULL) AS live_count,
               (SELECT MIN(added_at) FROM membership m WHERE m.track_id = tg.track_id) AS oldest_added
        FROM track_group tg
        WHERE tg.{column} = ?
        """,
        (group_id,),
    ).fetchall()
    if not members:
        return None
    best = min(members, key=lambda r: (-r["live_count"], r["oldest_added"] or "9999", r["track_id"]))
    return best["track_id"]


def pin_representative(conn, track_id):
    """Pins track_id as the representative for its groups at all four tiers."""
    row = conn.execute(
        "SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no track_group row for track {track_id}")
    for tier in TIER_ORDER:
        conn.execute(
            "UPDATE canonical_group SET representative_track_id = ? WHERE id = ?",
            (track_id, row[TIER_COLUMN[tier]]),
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


def nested_tree(conn, song_id):
    """The song's version -> recording -> release -> track nesting."""
    tree = []
    for v in conn.execute(
        "SELECT DISTINCT version_id FROM track_group WHERE song_id = ?", (song_id,)
    ):
        recordings = []
        for r in conn.execute(
            "SELECT DISTINCT recording_id FROM track_group WHERE version_id = ?", (v["version_id"],)
        ):
            releases = []
            for rel in conn.execute(
                "SELECT DISTINCT release_id FROM track_group WHERE recording_id = ?",
                (r["recording_id"],),
            ):
                track_ids = [
                    row["track_id"]
                    for row in conn.execute(
                        "SELECT track_id FROM track_group WHERE release_id = ?", (rel["release_id"],)
                    )
                ]
                releases.append({"release_id": rel["release_id"], "track_ids": track_ids})
            recordings.append({"recording_id": r["recording_id"], "releases": releases})
        tree.append({"version_id": v["version_id"], "recordings": recordings})
    return tree


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
