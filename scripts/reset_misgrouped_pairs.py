"""Return a grouping decision to untouched, so it can be made again.

For each seed set it takes the closure of everything sharing a canonical group
with those tracks at any tier, ungroups the whole closure back to singletons,
and deletes every reviewed_pair among it -- so the affected candidate groups
return to the main queue exactly as if they had never been reviewed, and the
prefill starts clean instead of _same_real re-asserting the old decision.

Two seed sources:

  --step-e   The three wrong groupings named in
             docs/specs/grouping-catch-up-E.md §0.3. One of them (City of
             Angels - Neanderthal Remix) was the *only* disagreement between
             the validation baseline and the step-E auto-group rule, so
             correcting it is what makes that rule 114/114. Already applied;
             kept as the record of what happened.

  --stale-recordings
             Every saved recording group holding a pair the *current* rules
             would no longer merge -- decisions made under an older rule set.
             Computed, not hardcoded, so it stays honest as the rules change.

    venv/bin/python scripts/reset_misgrouped_pairs.py --stale-recordings --dry-run
    venv/bin/python scripts/reset_misgrouped_pairs.py --stale-recordings

Safe to re-run: with no reviewed_pair rows left among a seed set there is
nothing to undo, and it says so. Makes no Spotify API calls.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import canonical  # noqa: E402
import canonical_detect  # noqa: E402
import db  # noqa: E402
from config import DB_PATH  # noqa: E402

# The three step-E corrections, from that spec's table. Each entry is
# (label, seeds).
STEP_E_SEEDS = (
    (
        "City of Angels - Neanderthal Remix (ISRC QZK6H2019075, both 210,000 ms)",
        ("6Iqs50fjfNdphBdsg2SLFs", "1UgSHLYqInYkxcCbSx8LzI"),
    ),
    (
        "Lemonade x3 (ISRC QZJ842000368, all 195,428 ms)",
        ("7hxHWCCAIIxFLCzvDgnQHX", "02kDW379Yfd5PzW5A6vuGt", "1p0rEzrK7YtdRZVtiyV7RN"),
    ),
    (
        "In The Morning (ISRC USQX91100167, 234,386 / 234,192 ms)",
        ("4OkiWfrZKmmVoILXk8JEtl", "124IHGAzY9F3unizZ08iRc"),
    ),
)


def stale_recording_seeds(conn):
    """One seed set per saved recording group the current rules disagree
    with, labelled with what is actually in it."""
    seeds = []
    for group in canonical_detect.stale_recording_groups(conn):
        names = []
        for track_id in group["track_ids"]:
            row = conn.execute(
                "SELECT name, duration_ms FROM track WHERE track_id = ?", (track_id,)
            ).fetchone()
            names.append(f"{row['name']} [{(row['duration_ms'] or 0) / 1000:.1f}s]")
        seeds.append((f"recording {group['recording_id']}: " + " / ".join(names),
                      tuple(group["track_ids"])))
    return tuple(seeds)


def closure(conn, seeds):
    """Every track reachable from `seeds` by sharing a canonical group at any
    tier. Ungrouping only the seeds would leave the rest of a group they were
    part of half-decided; "imagine they were never grouped" means the whole
    group goes back to the queue together."""
    found = set(seeds)
    frontier = set(seeds)
    while frontier:
        neighbours = set()
        for track_id in frontier:
            row = conn.execute(
                "SELECT song_id, version_id, recording_id, release_id "
                "FROM track_group WHERE track_id = ?",
                (track_id,),
            ).fetchone()
            if row is None:
                continue
            for column in canonical.TIER_COLUMN.values():
                neighbours.update(
                    r["track_id"]
                    for r in conn.execute(
                        f"SELECT track_id FROM track_group WHERE {column} = ?", (row[column],)
                    )
                )
        frontier = neighbours - found
        found |= frontier
    return sorted(found)


def backup():
    """Deleting a review decision can only be undone by making it again, so
    take a copy first. Checkpoint the WAL into the main file so a plain file
    copy is complete (same reason as migrate_track_metadata.py)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    path = f"{DB_PATH}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB_PATH, path)
    print(f"Backed up to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step-e", action="store_true",
                        help="the three step-E §0.3 corrections (already applied)")
    parser.add_argument("--stale-recordings", action="store_true",
                        help="saved recording groups the current rules would not merge")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be reset and exit without writing")
    args = parser.parse_args()
    if not (args.step_e or args.stale_recordings):
        parser.error("pick a seed source: --step-e and/or --stale-recordings")

    conn = db.connect()
    seed_sets = []
    if args.step_e:
        seed_sets.extend(STEP_E_SEEDS)
    if args.stale_recordings:
        seed_sets.extend(stale_recording_seeds(conn))

    if args.dry_run:
        print(f"DRY RUN — {len(seed_sets)} seed set(s), nothing will be written.\n")
    else:
        conn.close()
        backup()
        conn = db.connect()

    total_ungrouped = 0
    total_pairs = 0

    for label, seeds in seed_sets:
        print(f"\n{label}")
        tracks = closure(conn, seeds)
        print(f"  closure: {len(tracks)} track(s)")
        for track_id in tracks:
            name = conn.execute(
                "SELECT name FROM track WHERE track_id = ?", (track_id,)
            ).fetchone()
            marker = " " if track_id in seeds else "+"
            print(f"    {marker} {track_id}  {name['name'] if name else '<missing>'}")

        placeholders = ",".join("?" for _ in tracks)
        pairs = conn.execute(
            f"SELECT track_id_a, track_id_b FROM reviewed_pair "
            f"WHERE track_id_a IN ({placeholders}) AND track_id_b IN ({placeholders})",
            tracks + tracks,
        ).fetchall()
        if not pairs:
            print("  already reset — no reviewed pairs among the closure.")
            continue

        if args.dry_run:
            print(f"  would ungroup {len(tracks)} track(s), delete {len(pairs)} reviewed pair(s)")
            total_ungrouped += len(tracks)
            total_pairs += len(pairs)
            continue

        # A fresh, per-track label at every tier: full detachment. The closure
        # is complete, so apply_partition's upward closure pulls in nothing
        # further and no group outside it is disturbed.
        labels = {
            track_id: {tier: f"reset:{track_id}:{tier}" for tier in canonical.TIER_ORDER}
            for track_id in tracks
        }
        canonical.apply_partition(conn, labels)

        for pair in pairs:
            conn.execute(
                "DELETE FROM reviewed_pair WHERE track_id_a = ? AND track_id_b = ?",
                (pair["track_id_a"], pair["track_id_b"]),
            )
        conn.commit()

        print(f"  ungrouped {len(tracks)} track(s), deleted {len(pairs)} reviewed pair(s)")
        total_ungrouped += len(tracks)
        total_pairs += len(pairs)

    print(f"\nDone. {total_ungrouped} track(s) ungrouped, {total_pairs} reviewed pair(s) deleted.")
    conn.close()


if __name__ == "__main__":
    main()
