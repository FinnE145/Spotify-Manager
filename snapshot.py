"""Library snapshot: pulls playlists + their track contents from Spotify into
an append-only membership log (see docs/specs/snapshot.md). Read-only w.r.t.
Spotify; nothing here ever writes to the user's library."""

import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from spotipy.exceptions import SpotifyException

import db
from spotify_client import get_spotify_client

LIKED_PLAYLIST_ID = "__liked__"

# A rate-limit wait this short is routine (Spotify's rolling 30s window) and
# safe to sleep through; anything longer means an app-level quota is
# exhausted, and we fail fast instead of blocking the background thread.
_SHORT_WAIT_LIMIT_SECONDS = 30


class RateLimited(Exception):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        self.retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        super().__init__(f"Rate limited by Spotify — retry after {self.retry_at}")


def _call(fn, *args, **kwargs):
    """Calls a Spotipy method, retrying once through a short rate-limit
    wait but raising RateLimited on a long one instead of blocking."""
    for attempt in range(2):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status != 429:
                raise
            retry_after = int(e.headers.get("Retry-After", 1))
            if retry_after > _SHORT_WAIT_LIMIT_SECONDS or attempt == 1:
                raise RateLimited(retry_after) from e
            time.sleep(retry_after)
    raise AssertionError("unreachable")

_status_lock = threading.Lock()
_status = {
    "running": False,
    "phase": None,
    # Progress within the current run's track-pull loop, i.e. how many of
    # the playlists targeted by *this* pull/refresh are done — distinct
    # from summary_counts()'s playlists_total (all playlists ever seen).
    "run_total": 0,
    "run_done": 0,
    "current_playlist": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "retry_at": None,
    "failed_playlists": [],
}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_status(**kwargs):
    with _status_lock:
        _status.update(kwargs)


def _record_failure(conn, playlist_id, name, error):
    with _status_lock:
        _status["failed_playlists"].append({"name": name, "error": str(error)})
    conn.execute(
        "UPDATE snapshot SET last_pull_error = ? WHERE playlist_id = ?", (str(error), playlist_id)
    )
    conn.commit()


def get_status():
    with _status_lock:
        return dict(_status)


def summary_counts(conn):
    return {
        "playlists_total": conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0],
        "playlists_captured": conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE tracks_pulled_at IS NOT NULL"
        ).fetchone()[0],
        "playlists_uncapturable": conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE last_pull_error IS NOT NULL"
        ).fetchone()[0],
        "live_memberships": conn.execute(
            "SELECT COUNT(*) FROM membership WHERE removed_at IS NULL"
        ).fetchone()[0],
        "last_full_pull_at": _get_meta(conn, "last_full_pull_at"),
        "last_refresh_at": _get_meta(conn, "last_refresh_at"),
    }


# -- Triggering -------------------------------------------------------


def start_full_pull():
    return _start(force_all=True)


def start_refresh():
    return _start(force_all=False)


def _start(force_all):
    with _status_lock:
        if _status["running"]:
            return False
        _status["running"] = True
    thread = threading.Thread(target=_run_pull, args=(force_all,), daemon=True)
    thread.start()
    return True


def _run_pull(force_all):
    conn = db.connect()
    sp = get_spotify_client()
    try:
        _set_status(
            running=True,
            phase="playlists",
            run_total=0,
            run_done=0,
            current_playlist=None,
            started_at=_now_iso(),
            finished_at=None,
            error=None,
            retry_at=None,
            failed_playlists=[],
        )
        if sp is None:
            raise RuntimeError("not_authenticated")

        targets = _sync_playlists_and_get_targets(conn, sp, force_all)

        _set_status(phase="tracks", run_total=len(targets), run_done=0)
        for i, p in enumerate(targets):
            _set_status(current_playlist=p["name"], run_done=i)
            try:
                _pull_playlist_tracks(conn, sp, p["id"])
                conn.commit()
            except RateLimited:
                # An app-level quota block affects every request, not just
                # this playlist — abort the run instead of burning through
                # the rest and recording the same failure repeatedly.
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                _record_failure(conn, p["id"], p["name"], e)
        _set_status(run_done=len(targets), current_playlist=None)

        _set_status(phase="liked_songs")
        try:
            _pull_liked_songs(conn, sp)
            conn.commit()
        except RateLimited:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            _record_failure(conn, LIKED_PLAYLIST_ID, "Liked Songs", e)

        _set_meta(conn, "last_full_pull_at" if force_all else "last_refresh_at", _now_iso())
        conn.commit()

        _set_status(phase="done", finished_at=_now_iso())
    except RateLimited as e:
        _set_status(phase="error", error=str(e), retry_at=e.retry_at, finished_at=_now_iso())
    except Exception as e:
        _set_status(phase="error", error=str(e), finished_at=_now_iso())
    finally:
        _set_status(running=False)
        conn.close()


# -- Playlist list sync -------------------------------------------------


def _sync_playlists_and_get_targets(conn, sp, force_all):
    playlists = _fetch_all_playlists(sp)
    seen_ids = set()
    targets = []
    for p in playlists:
        seen_ids.add(p["id"])
        stored = conn.execute(
            "SELECT snapshot_id FROM snapshot WHERE playlist_id = ?", (p["id"],)
        ).fetchone()
        is_new_or_changed = stored is None or stored["snapshot_id"] != p["snapshot_id"]
        _upsert_snapshot_playlist(conn, p)
        _upsert_card(conn, p)
        if force_all or is_new_or_changed:
            targets.append(p)
    conn.commit()

    existing_ids = {
        row["playlist_id"]
        for row in conn.execute(
            "SELECT playlist_id FROM snapshot WHERE playlist_id != ? AND unfollowed_at IS NULL",
            (LIKED_PLAYLIST_ID,),
        )
    }
    now = _now_iso()
    for pid in existing_ids - seen_ids:
        conn.execute("UPDATE snapshot SET unfollowed_at = ? WHERE playlist_id = ?", (now, pid))
    conn.commit()

    return targets


def _fetch_all_playlists(sp):
    results = _call(sp.current_user_playlists, limit=50)
    raw = list(results["items"])
    while results["next"]:
        results = _call(sp.next, results)
        raw.extend(results["items"])

    playlists = []
    for p in raw:
        if p is None:
            # Spotify returns a null entry for playlists that were deleted
            # but are still in the user's followed list.
            continue
        images = p.get("images") or []
        playlists.append(
            {
                "id": p["id"],
                "name": p["name"],
                "image_url": images[0]["url"] if images else None,
                "owner": (p.get("owner") or {}).get("display_name"),
                "track_count": (p.get("tracks") or {}).get("total"),
                "snapshot_id": p.get("snapshot_id"),
                "description": p.get("description"),
            }
        )
    return playlists


def _upsert_snapshot_playlist(conn, p):
    conn.execute(
        """
        INSERT INTO snapshot (playlist_id, name, image_url, owner, track_count, pulled_at,
                               snapshot_id, description)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)
        ON CONFLICT(playlist_id) DO UPDATE SET
            name=excluded.name,
            image_url=excluded.image_url,
            owner=excluded.owner,
            -- Never let a missing/null track count from the playlist-list
            -- endpoint clobber the accurate count _apply_playlist_items()
            -- computes from actually-captured tracks.
            track_count=COALESCE(excluded.track_count, track_count),
            pulled_at=excluded.pulled_at,
            snapshot_id=excluded.snapshot_id,
            description=excluded.description,
            unfollowed_at=NULL
        """,
        (
            p["id"],
            p["name"],
            p["image_url"],
            p["owner"],
            p["track_count"],
            p["snapshot_id"],
            p["description"],
        ),
    )


def _upsert_card(conn, p):
    conn.execute(
        """
        INSERT INTO card (board_id, entity_type, entity_id, display_name, image_url, placement)
        VALUES (?, 'playlist', ?, ?, ?, 'tray')
        ON CONFLICT(board_id, entity_type, entity_id) DO UPDATE SET
            display_name=excluded.display_name,
            image_url=excluded.image_url
        """,
        (db.DEFAULT_BOARD_ID, p["id"], p["name"], p["image_url"]),
    )


# -- Track contents -------------------------------------------------


def _album_image_url(album):
    """The 300px cover (Spotify returns 640/300/64), falling back to the
    middle entry, then the first."""
    images = album.get("images") or []
    if not images:
        return None
    for image in images:
        if image.get("width") == 300:
            return image.get("url")
    return images[len(images) // 2].get("url")


def _parse_track_item(track, added_at, position):
    artists = ", ".join(a["name"] for a in track.get("artists") or [])
    album = track.get("album") or {}
    external_urls = track.get("external_urls") or {}
    external_ids = track.get("external_ids") or {}
    return {
        "track_id": track["id"],
        "name": track.get("name"),
        "artists": artists,
        "album_id": album.get("id"),
        "album_name": album.get("name"),
        "duration_ms": track.get("duration_ms"),
        "explicit": 1 if track.get("explicit") else 0,
        "popularity": track.get("popularity"),
        "preview_url": track.get("preview_url"),
        "external_url": external_urls.get("spotify"),
        "isrc": external_ids.get("isrc"),
        "album_image_url": _album_image_url(album),
        "added_at": added_at,
        "position": position,
    }


def _usable_track(track):
    if track is None:
        return False
    if track.get("is_local"):
        return False
    if track.get("type") == "episode":
        return False
    return bool(track.get("id"))


def _fetch_playlist_items(sp, playlist_id):
    results = _call(sp.playlist_items, playlist_id, limit=100)
    raw = list(results["items"])
    while results["next"]:
        results = _call(sp.next, results)
        raw.extend(results["items"])

    parsed = []
    position = 0
    for entry in raw:
        # The playlist-items endpoint keys the track/episode as "item" (it
        # can hold either type); the saved-tracks endpoint below still uses
        # "track" since it's track-only.
        track = entry.get("item")
        if not _usable_track(track):
            continue
        parsed.append(_parse_track_item(track, entry.get("added_at"), position))
        position += 1
    return parsed


def _fetch_liked_items(sp):
    results = _call(sp.current_user_saved_tracks, limit=50)
    raw = list(results["items"])
    while results["next"]:
        results = _call(sp.next, results)
        raw.extend(results["items"])

    parsed = []
    position = 0
    for entry in raw:
        track = entry.get("track")
        if not _usable_track(track):
            continue
        parsed.append(_parse_track_item(track, entry.get("added_at"), position))
        position += 1
    return parsed


def _upsert_track(conn, it):
    conn.execute(
        """
        INSERT INTO track (track_id, name, artists, album_id, album_name, duration_ms,
                            explicit, popularity, preview_url, external_url,
                            isrc, album_image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            name=excluded.name,
            artists=excluded.artists,
            album_id=excluded.album_id,
            album_name=excluded.album_name,
            duration_ms=excluded.duration_ms,
            explicit=excluded.explicit,
            popularity=excluded.popularity,
            preview_url=excluded.preview_url,
            external_url=excluded.external_url,
            -- COALESCE, not plain overwrite: Spotify sometimes omits
            -- external_ids/images on a track object, and a NULL from one pull
            -- must not wipe a value we already have.
            isrc=COALESCE(excluded.isrc, isrc),
            album_image_url=COALESCE(excluded.album_image_url, album_image_url)
        """,
        (
            it["track_id"],
            it["name"],
            it["artists"],
            it["album_id"],
            it["album_name"],
            it["duration_ms"],
            it["explicit"],
            it["popularity"],
            it["preview_url"],
            it["external_url"],
            it["isrc"],
            it["album_image_url"],
        ),
    )


def _diff_playlist_tracks(conn, playlist_id, current_items):
    """Reconciles stored live membership rows against a fresh pull, by
    comparing how many copies of each track id are present (see the
    "Change detection & diffing" section of docs/specs/snapshot.md)."""
    stored_rows = conn.execute(
        "SELECT id, track_id, position, added_at FROM membership "
        "WHERE playlist_id = ? AND removed_at IS NULL",
        (playlist_id,),
    ).fetchall()

    stored_by_track = defaultdict(list)
    for row in stored_rows:
        stored_by_track[row["track_id"]].append(dict(row))

    current_by_track = defaultdict(list)
    for item in current_items:
        current_by_track[item["track_id"]].append(item)

    now = _now_iso()

    for track_id in set(stored_by_track) | set(current_by_track):
        stored = stored_by_track.get(track_id, [])
        current = current_by_track.get(track_id, [])

        # Unambiguous identity: a copy whose added_at exactly matches a
        # stored copy's added_at is the same copy, wherever it now sits.
        stored_by_added = defaultdict(list)
        for s in stored:
            stored_by_added[s["added_at"]].append(s)

        current_remaining = []
        for c in current:
            bucket = stored_by_added.get(c["added_at"])
            if bucket:
                s = bucket.pop(0)
                conn.execute(
                    "UPDATE membership SET position = ? WHERE id = ?", (c["position"], s["id"])
                )
            else:
                current_remaining.append(c)

        stored_remaining = [s for bucket in stored_by_added.values() for s in bucket]
        current_remaining.sort(key=lambda c: c["position"])
        stored_remaining.sort(key=lambda s: s["position"] if s["position"] is not None else 0)

        if len(current_remaining) >= len(stored_remaining):
            # Pair off leftovers by position order; any extra current copies
            # are new inserts.
            for i, s in enumerate(stored_remaining):
                c = current_remaining[i]
                conn.execute(
                    "UPDATE membership SET position = ?, added_at = ? WHERE id = ?",
                    (c["position"], c["added_at"], s["id"]),
                )
            for c in current_remaining[len(stored_remaining):]:
                conn.execute(
                    "INSERT INTO membership (playlist_id, track_id, position, added_at) "
                    "VALUES (?, ?, ?, ?)",
                    (playlist_id, track_id, c["position"], c["added_at"]),
                )
        else:
            # Fewer copies now than before: some departed. Fallback rule
            # when which-copy is ambiguous: the copy(ies) with the latest
            # added_at are presumed departed (an accidental duplicate is
            # almost always the newer re-add), the rest survive and are
            # paired off by position order.
            stored_remaining.sort(key=lambda s: s["added_at"] or "")
            n_survive = len(current_remaining)
            survivors = sorted(
                stored_remaining[:n_survive],
                key=lambda s: s["position"] if s["position"] is not None else 0,
            )
            departed = stored_remaining[n_survive:]
            for c, s in zip(current_remaining, survivors):
                conn.execute(
                    "UPDATE membership SET position = ?, added_at = ? WHERE id = ?",
                    (c["position"], c["added_at"], s["id"]),
                )
            for s in departed:
                conn.execute("UPDATE membership SET removed_at = ? WHERE id = ?", (now, s["id"]))


def _compute_last_changed(conn, playlist_id):
    row = conn.execute(
        "SELECT MAX(x) AS m FROM ("
        "  SELECT added_at AS x FROM membership WHERE playlist_id = ?"
        "  UNION ALL"
        "  SELECT removed_at AS x FROM membership WHERE playlist_id = ?"
        ")",
        (playlist_id, playlist_id),
    ).fetchone()
    return row["m"]


def _apply_playlist_items(conn, playlist_id, items):
    seen_track_ids = set()
    for it in items:
        if it["track_id"] not in seen_track_ids:
            _upsert_track(conn, it)
            seen_track_ids.add(it["track_id"])

    _diff_playlist_tracks(conn, playlist_id, items)

    live_count = conn.execute(
        "SELECT COUNT(*) FROM membership WHERE playlist_id = ? AND removed_at IS NULL",
        (playlist_id,),
    ).fetchone()[0]
    last_changed = _compute_last_changed(conn, playlist_id)
    conn.execute(
        "UPDATE snapshot SET tracks_pulled_at = ?, track_count = ?, last_changed_at = ?, "
        "last_pull_error = NULL WHERE playlist_id = ?",
        (_now_iso(), live_count, last_changed, playlist_id),
    )


def _pull_playlist_tracks(conn, sp, playlist_id):
    items = _fetch_playlist_items(sp, playlist_id)
    _apply_playlist_items(conn, playlist_id, items)


def _ensure_liked_playlist_row(conn, owner_name):
    conn.execute(
        """
        INSERT INTO snapshot (playlist_id, name, owner, pulled_at)
        VALUES (?, 'Liked Songs', ?, datetime('now'))
        ON CONFLICT(playlist_id) DO UPDATE SET
            owner = excluded.owner,
            pulled_at = datetime('now')
        """,
        (LIKED_PLAYLIST_ID, owner_name),
    )


def _pull_liked_songs(conn, sp):
    me = _call(sp.current_user)
    owner_name = me.get("display_name") or me.get("id")
    _ensure_liked_playlist_row(conn, owner_name)
    items = _fetch_liked_items(sp)
    _apply_playlist_items(conn, LIKED_PLAYLIST_ID, items)


# -- Meta key/value store -------------------------------------------------


def _set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
