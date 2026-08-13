"""One-off seed for roadmap step B (docs/specs/generations-B.md).

Inserts the verified chain of 36 current-favs generations into the new
`generation` table: (ordinal, playlist_id), resolved by playlist name against
`snapshot`. Naming cannot be pattern-matched -- see the spec's "The 36 --
verified" section for why this exact list is the authority. `favourties 5`
(14) is a typo in the real playlist name, kept verbatim on purpose.

    venv/bin/python scripts/seed_generations.py --dry-run
    venv/bin/python scripts/seed_generations.py

Aborts the whole run, writing nothing, if any name fails to resolve to
exactly one snapshot row -- a partial seed is worse than none. Idempotent
(INSERT OR IGNORE): safe to re-run. Makes no Spotify API calls.

Kept afterwards as the record of what happened, like migrate_track_metadata.py
-- not meant to be re-run in earnest.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

# (ordinal, name), verified 1:1 against snapshot -- see docs/specs/
# generations-B.md's "The 36 -- verified" table. Positions 1-24 are the
# pre-numbering era; from 25 on, the ordinal is the playlist's own major
# version number.
GENERATIONS = (
    (1, "Songs I Wanna Listen To Rn"),
    (2, "Songs I wanna listen to 2"),
    (3, "Songs I wanna listen to 3"),
    (4, "Good Songs"),
    (5, "Fairly Ok Songs"),
    (6, "Pretty Good Songs"),
    (7, "music im sick of"),
    (8, "(no longer) current music"),
    (9, "current music"),
    (10, "favourites"),
    (11, "favourites 2"),
    (12, "favourites 3"),
    (13, "favourites 4"),
    (14, "favourties 5"),
    (15, "favourites 6"),
    (16, "favourites 7"),
    (17, "music idk"),
    (18, "music 2"),
    (19, "music 3"),
    (20, "music 4"),
    (21, "music 5"),
    (22, "music 6"),
    (23, "music 7"),
    (24, "music 8"),
    (25, "25"),
    (26, "26"),
    (27, "27"),
    (28, "28"),
    (29, "29"),
    (30, "v30.1.2"),
    (31, "v31.7.0"),
    (32, "v32.23.5"),
    (33, "v33.14.3"),
    (34, "v34.12.4"),
    (35, "v35.18.1"),
    (36, "v36.4.2"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="resolve and print, write nothing")
    args = parser.parse_args()

    conn = db.connect()

    resolved = []
    errors = []
    for ordinal, name in GENERATIONS:
        rows = conn.execute("SELECT playlist_id FROM snapshot WHERE name = ?", (name,)).fetchall()
        if len(rows) != 1:
            errors.append(f"  {ordinal:2d}  {name!r}: {len(rows)} matches (need exactly 1)")
            continue
        resolved.append((ordinal, name, rows[0]["playlist_id"]))

    if errors:
        print(f"Aborting -- {len(errors)} name(s) did not resolve to exactly one playlist:")
        print("\n".join(errors))
        conn.close()
        sys.exit(1)

    for ordinal, name, playlist_id in resolved:
        started_at = conn.execute(
            "SELECT MIN(added_at) AS started_at FROM membership "
            "WHERE playlist_id = ? AND removed_at IS NULL",
            (playlist_id,),
        ).fetchone()["started_at"]
        print(f"  {ordinal:2d}  {name:<30s} {playlist_id}  starts {started_at}")

    if args.dry_run:
        print(f"\nDRY RUN -- would insert {len(resolved)} generation(s). Nothing written.")
        conn.close()
        return

    inserted = 0
    for ordinal, name, playlist_id in resolved:
        cur = conn.execute(
            "INSERT OR IGNORE INTO generation (ordinal, playlist_id) VALUES (?, ?)",
            (ordinal, playlist_id),
        )
        if cur.rowcount:
            inserted += 1
    conn.commit()
    conn.close()

    print(f"\nDone. {inserted} generation(s) inserted, {len(resolved) - inserted} already present.")


if __name__ == "__main__":
    main()
