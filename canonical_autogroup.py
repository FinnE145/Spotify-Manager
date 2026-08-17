"""The deterministic auto-group run (docs/specs/grouping-catch-up-E.md §3).

Closes every unreviewed main-queue candidate group whose tracks are pairwise
"same ISRC + identical normalized full title + duration within 2s", writing one
shared song/version/recording group and a release group per album. Scored
114/114 against the 491-pair reviewed baseline before adoption (corrected
figures per grouping-catch-up-E.md's own post-implementation amendment; the
pre-correction 116/116-against-503-pairs figure this docstring cited until
P1-013, 2026-08-17, was superseded the same day E was written) -- re-score
after any change to canonical_detect._auto_group_pair, because that baseline is
the only ground truth in the project.

canonical_detect owns the rule (pure); this module owns the writing, the run
log, and the whole-table undo snapshot. No Spotify requests: nothing here
reads or writes the library.

Unlike canonical.py, these functions *do* commit -- a run is one transaction
per public call.
"""

import canonical
import canonical_detect
import scoring

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

# (live table, snapshot table, columns). Order matters for the restore:
# track_group carries REFERENCES canonical_group(id), so it is emptied first
# and refilled last.
_SNAPSHOT_TABLES = (
    (
        "canonical_group",
        "auto_group_snapshot_canonical_group",
        ("id", "tier", "representative_track_id", "created_at", "auto_run_id"),
    ),
    (
        "reviewed_pair",
        "auto_group_snapshot_reviewed_pair",
        ("track_id_a", "track_id_b", "decided_at"),
    ),
    (
        "track_group",
        "auto_group_snapshot_track_group",
        ("track_id", "song_id", "version_id", "recording_id", "release_id"),
    ),
)


def preview(conn):
    """What a run would do, without writing anything."""
    closable, queue_total = canonical_detect.auto_group_candidates(conn)
    return {
        "groups_closed": len(closable),
        "tracks_affected": sum(len(g["track_ids"]) for g in closable),
        "queue_total": queue_total,
        "remaining": queue_total - len(closable),
    }


def _take_snapshot(conn, run_id):
    for live, snap, columns in _SNAPSHOT_TABLES:
        # Only the most recent run's snapshot is kept -- undo is one level deep.
        conn.execute(f"DELETE FROM {snap}")
        column_list = ", ".join(columns)
        conn.execute(
            f"INSERT INTO {snap} (run_id, {column_list}) "
            f"SELECT ?, {column_list} FROM {live}",
            (run_id,),
        )


def run(conn):
    """Close every qualifying group, then commit. Returns the same shape as
    preview(), plus the run id (None when there was nothing to do)."""
    closable, queue_total = canonical_detect.auto_group_candidates(conn)
    result = {
        "groups_closed": len(closable),
        "tracks_affected": sum(len(g["track_ids"]) for g in closable),
        "queue_total": queue_total,
        "remaining": queue_total - len(closable),
        "run_id": None,
    }
    if not closable:
        return result

    canonical.ensure_track_groups(conn)
    run_id = conn.execute("INSERT INTO auto_group_run DEFAULT VALUES").lastrowid
    _take_snapshot(conn, run_id)

    decided_group_ids = set()
    for group in closable:
        album_norms = group["album_norms"]
        labels = {
            track_id: {
                "song": "auto:song",
                "version": "auto:version",
                "recording": "auto:recording",
                "release": f"auto:release:{album_norms[track_id]}",
            }
            for track_id in group["track_ids"]
        }
        # cleanup=False: _cleanup_tier is a full canonical_group x track_group
        # LEFT JOIN per tier and dominates the run (11.75s -> 1.15s). Paid once
        # below instead.
        applied = canonical.apply_partition(conn, labels, cleanup=False)
        canonical.mark_reviewed(conn, group["track_ids"])
        # The groups this run decided -- taken from what apply_partition
        # actually wrote, which includes anything its closure pulled in.
        #
        # NOT "the ids absent from the snapshot": apply_partition *reuses* an
        # existing group id whenever a part fully covers one, so a run creates
        # almost no new canonical_group rows (this one shrinks the table by
        # ~2,100) and an id-diff against the snapshot finds nothing at all.
        for tiers in applied["tracks"].values():
            decided_group_ids.update(tiers.values())

    canonical.cleanup_all_tiers(conn)

    # Chunked well under SQLITE_MAX_VARIABLE_NUMBER -- a full run decides a few
    # thousand groups.
    ids = sorted(decided_group_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(
            f"UPDATE canonical_group SET auto_run_id = ? WHERE id IN ({placeholders})",
            [run_id, *chunk],
        )
    conn.execute(
        f"UPDATE auto_group_run SET finished_at = {_NOW}, groups_closed = ?, "
        "tracks_affected = ? WHERE id = ?",
        (result["groups_closed"], result["tracks_affected"], run_id),
    )
    conn.commit()
    scoring.recompute(conn)
    result["run_id"] = run_id
    return result


def last_run(conn):
    """The most recent run, or None. `undoable` is the one thing the page
    needs beyond display: only a finished, not-yet-undone run still has its
    snapshot."""
    row = conn.execute(
        "SELECT id, started_at, finished_at, groups_closed, tracks_affected, undone_at "
        "FROM auto_group_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    info = dict(row)
    info["undoable"] = row["finished_at"] is not None and row["undone_at"] is None
    return info


def undo(conn):
    """Restore track_group, canonical_group and reviewed_pair wholesale from
    the last run's snapshot.

    Wholesale means wholesale: any review decided *since* that run is rolled
    back too. That's the documented cost of one blunt snapshot, and the page
    says so.
    """
    run_row = last_run(conn)
    if run_row is None or not run_row["undoable"]:
        raise ValueError("no auto-group run to undo")
    run_id = run_row["id"]

    for live, _snap, _columns in reversed(_SNAPSHOT_TABLES):
        conn.execute(f"DELETE FROM {live}")
    for live, snap, columns in _SNAPSHOT_TABLES:
        column_list = ", ".join(columns)
        conn.execute(
            f"INSERT INTO {live} ({column_list}) "
            f"SELECT {column_list} FROM {snap} WHERE run_id = ?",
            (run_id,),
        )
        conn.execute(f"DELETE FROM {snap}")

    conn.execute(f"UPDATE auto_group_run SET undone_at = {_NOW} WHERE id = ?", (run_id,))
    conn.commit()
    scoring.recompute(conn)
    return {"run_id": run_id}
