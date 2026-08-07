"""Mop-up backfill of track.isrc and track.album_image_url.

Both columns were added with the canonical-tracks feature (docs/specs/canonical-tracks.md).
A normal snapshot pull captures them for every track it sees, so this script only
exists for the leftovers a pull can't reach: tracks whose playlists 403 on item
reads, and tracks that survive only in removed memberships.

Note this goes one track per request. `GET /v1/tracks?ids=` (the 50-at-a-time
batch endpoint) returns 403 for this app — see docs/spotify_constraints.md — so
there is no cheap bulk path. Run a full pull first and let this handle the
remainder; it refuses to run against a large backlog for that reason.

Reads only; nothing here writes to Spotify. Run from the project root:

    venv/bin/python scripts/backfill_track_details.py [--limit N]

Safe to re-run: it only selects rows still missing a value, and commits as it
goes, so an interrupted run resumes where it left off.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from jobs import RateLimited
from snapshot import _album_image_url, _call
from spotify_client import get_spotify_client

PAUSE_SECONDS = 0.2  # gentle on the rolling 30s window
# One request per track, and a few hundred in a burst is what exhausts the
# app-level quota (~24h lockout). Past this, a full pull is the right tool.
CONFIRM_THRESHOLD = 300


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="only process the first N tracks")
    parser.add_argument(
        "--yes", action="store_true", help="skip the large-backlog confirmation"
    )
    args = parser.parse_args()

    sp = get_spotify_client()
    if sp is None:
        sys.exit("No cached Spotify token — log in through the app first.")

    conn = db.connect()
    query = (
        "SELECT track_id FROM track "
        "WHERE isrc IS NULL OR album_image_url IS NULL ORDER BY track_id"
    )
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    track_ids = [row["track_id"] for row in conn.execute(query)]

    if not track_ids:
        print("Nothing to backfill — every track already has an ISRC and a cover.")
        return

    print(f"{len(track_ids)} tracks missing details — one request each.")
    if len(track_ids) > CONFIRM_THRESHOLD and not args.yes:
        sys.exit(
            f"Refusing to send {len(track_ids)} requests in one burst — that risks\n"
            "app-level quota exhaustion (~24h lockout, see docs/spotify_constraints.md).\n"
            "Run a full snapshot pull first; it captures these ~10x cheaper.\n"
            "Override with --yes, or chip away with --limit N."
        )

    updated = 0
    skipped = []
    for number, track_id in enumerate(track_ids, start=1):
        try:
            track = _call(sp.track, track_id)
        except RateLimited as e:
            # _call already refuses to sleep through a long Retry-After; surface
            # it loudly rather than hanging. App-level quota exhaustion returns
            # waits in the tens of thousands of seconds.
            conn.commit()
            sys.exit(
                f"\nRate limited: Spotify asked for {e.retry_after_seconds}s "
                f"(retry after {e.retry_at}).\n"
                f"{updated} tracks were saved. Re-run this script after that time."
            )
        except Exception as e:  # unavailable / relinked-away / region-locked id
            skipped.append((track_id, str(e).splitlines()[0][:80]))
            continue

        album = track.get("album") or {}
        external_ids = track.get("external_ids") or {}
        conn.execute(
            """
            UPDATE track SET
                isrc = COALESCE(?, isrc),
                album_image_url = COALESCE(?, album_image_url)
            WHERE track_id = ?
            """,
            (external_ids.get("isrc"), _album_image_url(album), track_id),
        )
        updated += 1

        if number % 25 == 0 or number == len(track_ids):
            conn.commit()
            print(f"  {number}/{len(track_ids)} — {updated} updated")
        time.sleep(PAUSE_SECONDS)

    conn.commit()
    missing_isrc = conn.execute(
        "SELECT COUNT(*) FROM track WHERE isrc IS NULL"
    ).fetchone()[0]
    missing_cover = conn.execute(
        "SELECT COUNT(*) FROM track WHERE album_image_url IS NULL"
    ).fetchone()[0]
    print(
        f"\nDone. {updated} tracks updated. "
        f"Still missing: {missing_isrc} ISRC, {missing_cover} cover."
    )
    if skipped:
        print(f"{len(skipped)} unreadable:")
        for track_id, reason in skipped[:20]:
            print(f"  {track_id} — {reason}")
    conn.close()


if __name__ == "__main__":
    main()
