"""Scrobbling from recently-played (docs/specs/scrobbling-R.md): polls
GET /v1/me/player/recently-played on a fixed schedule and records what comes
back as `play` rows, so the library reflects listening without waiting on a
GDPR export.

Explicitly non-authoritative -- history_import._finish deletes every scrobble
an export's range covers (§6). Not a job: it does not touch jobs.py's slot
(§4.7), and its daemon thread is started only from serve.py (§4.1), which is
what makes scrobbling production-only by construction -- the laptop dev loop
never runs it. poll() itself is the whole feature and works everywhere:
serve.py's loop and the manual "Poll now" button call the identical
function."""

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from spotipy.exceptions import SpotifyException

import api_log
import db
import jobs
import scoring
import snapshot
from spotify_client import get_spotify_client

# A module constant with a warning, per H §10's rule: algorithm parameters
# are constants, not environment-tunable, or two deployments could scrobble
# on different, unrecorded schedules. 50 items x a pessimistic 2 min/track is
# 1h40m of unbroken listening, so this cannot overflow the 50-deep window
# (§4.2) -- 14.4 requests/day.
_POLL_INTERVAL_SECONDS = 100 * 60


def start():
    """Spawns the daemon poller thread. Called only from serve.py -- the
    container entrypoint -- never from app.py's app.run() laptop dev loop
    (§4.1)."""
    threading.Thread(target=_loop, daemon=True).start()


def _loop():
    # Set once for the thread's whole lifetime, exactly like jobs.try_start's
    # run() wrapper -- api_context is a contextvar, and a value set on the
    # main thread would not be seen here.
    api_log.api_context.set("scrobble")
    while True:
        conn = db.connect()
        try:
            poll(conn)
        finally:
            conn.close()
        time.sleep(_POLL_INTERVAL_SECONDS)


# -- The poll --------------------------------------------------------------


def poll(conn):
    """One poll: skip if paused or backing off, otherwise fetch, ingest and
    log. Called by the daemon loop above and by the manual "Poll now" button
    (app.py) -- same function, same effects, so the feature is fully
    exercisable on the laptop with no thread running at all (§4.1).

    A plain try/except, not jobs.call -- a single request doesn't justify a
    JobStatus (§4.5). Every path that doesn't reach a real request (paused,
    backing off) returns before writing anything: those aren't polls, they
    never asked Spotify anything, so there's nothing for /dev/scrobble's
    liveness signal to say about them beyond what scrobble_enabled and
    scrobble_backoff_until already show directly.
    """
    if db.get_meta(conn, "scrobble_enabled") == "0":
        return
    backoff_until = db.get_meta(conn, "scrobble_backoff_until")
    if backoff_until and jobs.now_iso() < backoff_until:
        return

    started_at = jobs.now_iso()
    sp = get_spotify_client()
    if sp is None:
        # A missing or scope-invalid token logs and continues rather than
        # killing the thread, so a server that's been redeployed but not yet
        # re-consented (§8) recovers on its own the moment consent lands.
        conn.execute(
            "INSERT INTO scrobble_poll (started_at, error) VALUES (?, ?)",
            (started_at, "not_authenticated"),
        )
        conn.commit()
        return

    try:
        response = sp.current_user_recently_played(limit=50)
    except SpotifyException as e:
        if e.http_status == 429:
            retry_after = int(e.headers.get("Retry-After") or 0) or _POLL_INTERVAL_SECONDS
            db.set_meta(conn, "scrobble_backoff_until", _iso_after(retry_after))
            conn.execute(
                "INSERT INTO scrobble_poll (started_at, retry_after) VALUES (?, ?)",
                (started_at, retry_after),
            )
        else:
            conn.execute(
                "INSERT INTO scrobble_poll (started_at, error) VALUES (?, ?)",
                (started_at, str(e)),
            )
        conn.commit()
        return
    except Exception as e:
        conn.execute(
            "INSERT INTO scrobble_poll (started_at, error) VALUES (?, ?)",
            (started_at, str(e)),
        )
        conn.commit()
        return

    # Not pre-filtered: _derive_ms_played indexes into this list by position
    # to find "the next-older item in the batch" (§4.3), so an unusable item
    # (a local track or an episode -- §1.5) has to stay in place rather than
    # be removed, or the item after it would silently derive its gap against
    # the wrong predecessor. _ingest skips unusable items itself.
    items = response.get("items") or []
    rows_inserted = _ingest(conn, started_at, items)
    conn.commit()

    # Only when something actually landed -- a poll that inserted nothing
    # requests nothing (§4.9). Commit first, then request: the worker reads
    # through its own connection.
    if rows_inserted:
        scoring.request_recompute()


def _iso_after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _truncate_to_seconds(played_at):
    return _parse_ts(played_at).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_hash(played_at, uri):
    # source is inside the hashed dict, not merely the column, so a scrobble
    # digest can never collide with an export digest for the same play
    # (§3.2). played_at is hashed verbatim at millisecond precision, before
    # the ts column's truncation.
    canonical = json.dumps(
        {"source": "scrobble", "played_at": played_at, "uri": uri},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(canonical.encode()).hexdigest()


def _derive_ms_played(conn, items, index, duration_ms):
    """§4.3. An upper bound that can never underestimate: min() is what stops
    an idle break inflating the next track, and its one wrong case (a track
    paused part-way and abandoned, credited its full duration) is narrow and
    the direction the export corrects."""
    this_played_at = items[index]["played_at"]
    if index + 1 < len(items):
        predecessor = items[index + 1]["played_at"]
    else:
        row = conn.execute(
            "SELECT MAX(ts) AS ts FROM play WHERE ts < ?",
            (_truncate_to_seconds(this_played_at),),
        ).fetchone()
        predecessor = row["ts"]

    if predecessor is None:
        return duration_ms

    gap_ms = (_parse_ts(this_played_at) - _parse_ts(predecessor)).total_seconds() * 1000
    if gap_ms <= 0:
        return duration_ms
    return min(round(gap_ms), duration_ms)


def _album_artist_name(album):
    artists = (album or {}).get("artists") or []
    return ", ".join(a["name"] for a in artists) if artists else None


def _ingest(conn, started_at, items):
    """Writes the scrobble_poll row, every usable item's track (§5.1) and
    play row (§3), and the poll's own summary fields, all in the one
    transaction the caller commits (§4.7). Returns rows actually inserted --
    counted from each INSERT OR IGNORE's own rowcount, not conn.total_changes,
    which the interleaved track/artist/album upserts would also move."""
    # Read before this poll writes anything, so a poll that stores nothing
    # (all duplicates) doesn't move its own comparison point.
    prev_newest = conn.execute(
        "SELECT MAX(ts) FROM play WHERE source = 'scrobble'"
    ).fetchone()[0]

    poll_id = conn.execute(
        "INSERT INTO scrobble_poll (started_at) VALUES (?)", (started_at,)
    ).lastrowid

    rows_inserted = 0
    for index, item in enumerate(items):
        track = item.get("track")
        if not snapshot._usable_track(track):
            continue
        snapshot._upsert_track_full(conn, snapshot._parse_track_item(track, None, None))

        played_at = item["played_at"]
        album = track.get("album") or {}
        cursor = conn.execute(
            "INSERT OR IGNORE INTO play (row_hash, source, poll_id, ts, ms_played, "
            "spotify_track_uri, reported_track_name, reported_artist_name, "
            "reported_album_name) VALUES (?, 'scrobble', ?, ?, ?, ?, ?, ?, ?)",
            (
                _row_hash(played_at, track["uri"]),
                poll_id,
                _truncate_to_seconds(played_at),
                _derive_ms_played(conn, items, index, track["duration_ms"]),
                track["uri"],
                track.get("name"),
                _album_artist_name(album),
                album.get("name"),
            ),
        )
        if cursor.rowcount:
            rows_inserted += 1

    oldest_played = items[-1]["played_at"] if items else None
    newest_played = items[0]["played_at"] if items else None
    gap_warning = int(
        bool(items)
        and prev_newest is not None
        and _truncate_to_seconds(oldest_played) > prev_newest
    )

    conn.execute(
        "UPDATE scrobble_poll SET items_read = ?, rows_inserted = ?, oldest_played = ?, "
        "newest_played = ?, gap_warning = ? WHERE id = ?",
        (len(items), rows_inserted, oldest_played, newest_played, gap_warning, poll_id),
    )
    return rows_inserted


# -- /dev/scrobble -----------------------------------------------------------


def index_data(conn):
    """/dev/scrobble's whole read path (P3_refactor.md's rule for a dev
    page), returning exactly the template's kwargs."""
    last_poll = _last_poll_row(conn)
    return {
        "enabled": db.get_meta(conn, "scrobble_enabled") != "0",
        "interval_seconds": _POLL_INTERVAL_SECONDS,
        "last_poll": last_poll,
        "next_poll_estimate": _next_poll_estimate(last_poll),
        "total_scrobbles": conn.execute(
            "SELECT COUNT(*) FROM play WHERE source = 'scrobble'"
        ).fetchone()[0],
        "gap_warning_count": conn.execute(
            "SELECT COUNT(*) FROM scrobble_poll WHERE gap_warning = 1"
        ).fetchone()[0],
        # §7's explicit divider: the cutover is exactly the newest export
        # play, since supersession (§6) leaves no overlap band.
        "export_cutover": conn.execute(
            "SELECT MAX(ts) FROM play WHERE source = 'export'"
        ).fetchone()[0],
        "recent_plays": _recent_plays(conn),
    }


def _last_poll_row(conn):
    row = conn.execute(
        "SELECT id, started_at, items_read, rows_inserted, oldest_played, newest_played, "
        "gap_warning, retry_after, error FROM scrobble_poll ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def _next_poll_estimate(last_poll):
    if last_poll is None:
        return None
    started = _parse_ts(last_poll["started_at"])
    return (started + timedelta(seconds=_POLL_INTERVAL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recent_plays(conn, limit=50):
    rows = conn.execute(
        "SELECT p.ts, p.source, p.spotify_track_uri, p.reported_track_name, "
        "t.track_id, t.name AS track_name "
        "FROM play p "
        "LEFT JOIN played_uri_track x ON x.uri = p.spotify_track_uri "
        "LEFT JOIN track t ON t.track_id = x.track_id "
        "ORDER BY p.ts DESC, p.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_enabled(conn, enabled):
    db.set_meta(conn, "scrobble_enabled", "1" if enabled else "0")
    conn.commit()
