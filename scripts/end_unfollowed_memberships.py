"""End the memberships of playlists that were already unfollowed.

Unfollowing a playlist stamps `snapshot.unfollowed_at`, and since
docs/specs/ui-framework-W.md §9.14b it also stamps `removed_at` on that
playlist's live membership rows. This is the one-off for playlists unfollowed
*before* that change, whose rows are still live.

Why it matters: nothing filters a live-membership query on `unfollowed_at` --
there are six such queries, in scoring, canonical, entities and generations --
so a stale row is live everywhere. It is scored, it inflates `live_count`, and
it lists a deleted playlist on every one of its tracks' pages. Finn's rule:
Symr has no record at all of the playlists deleted before it existed, so a
newly-deleted one must not count for more than those do.

`removed_at` is set to that playlist's own `unfollowed_at`, not to now -- that
is when Symr observed the playlist gone, and it is what the code path this
backfills would have written at the time.

    venv/bin/python scripts/end_unfollowed_memberships.py
    venv/bin/python scripts/end_unfollowed_memberships.py --apply

Reports without writing unless --apply is given. Safe to re-run: the UPDATE is
guarded on `removed_at IS NULL`, so a second run finds nothing and says so, and
it can never rewrite a removal that happened earlier for a real reason. Makes
no Spotify API calls.

Already applied against symr.db on 2026-09-01; kept as the record of what
happened, not meant to be re-run.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import scoring  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    conn = db.connect()

    playlists = conn.execute(
        """
        SELECT s.playlist_id, s.name, s.unfollowed_at,
               COUNT(m.id) AS live_rows
        FROM snapshot s
        JOIN membership m ON m.playlist_id = s.playlist_id AND m.removed_at IS NULL
        WHERE s.unfollowed_at IS NOT NULL
        GROUP BY s.playlist_id
        ORDER BY s.unfollowed_at
        """
    ).fetchall()

    if not playlists:
        print("Nothing to do: no unfollowed playlist holds a live membership row.")
        return

    total = 0
    for row in playlists:
        print(
            f"  {row['name']} ({row['playlist_id']}) "
            f"unfollowed {row['unfollowed_at']}: {row['live_rows']} live rows"
        )
        total += row["live_rows"]
    print(f"{total} live membership row(s) across {len(playlists)} playlist(s).")

    # The tracks that lose their last live membership -- the ones only in the
    # library because of a playlist that no longer exists. Reported because it
    # is the number that actually changes what the site says.
    orphaned = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT m.track_id
            FROM membership m
            JOIN snapshot s ON s.playlist_id = m.playlist_id
            WHERE m.removed_at IS NULL
            GROUP BY m.track_id
            HAVING SUM(CASE WHEN s.unfollowed_at IS NULL THEN 1 ELSE 0 END) = 0
        )
        """
    ).fetchone()[0]
    print(f"{orphaned} track(s) will be left in no live playlist.")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return

    for row in playlists:
        conn.execute(
            "UPDATE membership SET removed_at = ? "
            "WHERE playlist_id = ? AND removed_at IS NULL",
            (row["unfollowed_at"], row["playlist_id"]),
        )
    conn.commit()
    print(f"\nEnded {total} membership row(s).")

    # Live memberships are a scoring input, so the scores are now stale by
    # exactly this change. Done here rather than left to scoring.ensure_fresh()
    # so the script leaves the database consistent on its own.
    print("Recomputing scores…")
    scoring.recompute(conn)
    conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
