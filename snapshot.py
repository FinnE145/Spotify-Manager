"""Library snapshot: pulls playlists + their track contents from Spotify into
an append-only membership log (see docs/specs/snapshot.md). Read-only w.r.t.
Spotify; nothing here ever writes to the user's library."""

import json
from collections import defaultdict

import canonical
import db
import jobs
import scoring
from jobs import RateLimited
from spotify_client import get_spotify_client

LIKED_PLAYLIST_ID = "__liked__"

_status = jobs.JobStatus(
    "snapshot",
    phase=None,
    # Progress within the current run's track-pull loop, i.e. how many of
    # the playlists targeted by *this* pull/refresh are done — distinct
    # from summary_counts()'s playlists_total (all playlists ever seen).
    run_total=0,
    run_done=0,
    current_playlist=None,
    started_at=None,
    finished_at=None,
    error=None,
    retry_at=None,
    failed_playlists=[],
    # Spotify requests issued by the current run. Per-run only, reset at the
    # start of each one; nothing is persisted.
    requests=0,
    # Which action started this run ("pull" | "refresh" | "backfill"). Unlike
    # `phase`, it survives into the terminal state, so a page that reloaded
    # mid-run still knows a backfill's failures carry track ids, not playlist
    # ids, and must not be offered the exclude button.
    action=None,
)


def _call(fn, *args, **kwargs):
    return jobs.call(_status, fn, *args, **kwargs)


def _record_failure(conn, playlist_id, name, error):
    _status.append(
        "failed_playlists",
        {"playlist_id": playlist_id, "name": name, "error": str(error)},
    )
    conn.execute(
        "UPDATE snapshot SET last_pull_error = ? WHERE playlist_id = ?", (str(error), playlist_id)
    )
    conn.commit()


def get_status():
    return _status.get()


def summary_counts(conn):
    return {
        "playlists_total": conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0],
        "playlists_pulled": conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE tracks_pulled_at IS NOT NULL AND excluded = 0"
        ).fetchone()[0],
        "playlists_excluded": conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE excluded = 1"
        ).fetchone()[0],
        "playlists_failing": conn.execute(
            "SELECT COUNT(*) FROM snapshot WHERE last_pull_error IS NOT NULL AND excluded = 0"
        ).fetchone()[0],
        "live_memberships": conn.execute(
            "SELECT COUNT(*) FROM membership WHERE removed_at IS NULL"
        ).fetchone()[0],
        "backfill_pending": backfill_pending(conn),
        "last_full_pull_at": db.get_meta(conn, "last_full_pull_at"),
        "last_refresh_at": db.get_meta(conn, "last_refresh_at"),
    }


def backfill_pending(conn):
    """Tracks carrying only pre-metadata-capture fields. A track that leaves
    every playlist between pulls freezes at whatever the last pull got --
    pulls only ever see tracks currently in a playlist -- so it never picks
    up raw_json or its artist credits. Structural, and it recurs."""
    return conn.execute("SELECT COUNT(*) FROM track WHERE raw_json IS NULL").fetchone()[0]


def set_excluded(conn, playlist_ids, excluded):
    placeholders = ",".join("?" for _ in playlist_ids)
    conn.execute(
        f"UPDATE snapshot SET excluded = ? WHERE playlist_id IN ({placeholders})",
        [1 if excluded else 0, *playlist_ids],
    )
    conn.commit()


# -- Triggering -------------------------------------------------------


def start_full_pull():
    return _start(_run_pull, True)


def start_refresh():
    return _start(_run_pull, False)


def start_backfill():
    return _start(_run_backfill)


def _start(target, *args):
    # One job slot for the whole app -- a pull, a history import and a
    # round-trip are mutually exclusive, and jobs.try_start settles that
    # with a single atomic check-and-set.
    return jobs.try_start("snapshot", target, *args)


def _reset_status(phase, action):
    _status.reset(phase=phase, action=action, started_at=jobs.now_iso())


def _run_pull(force_all):
    conn = db.connect()
    sp = get_spotify_client()
    try:
        _reset_status("playlists", "pull" if force_all else "refresh")
        if sp is None:
            raise RuntimeError("not_authenticated")

        targets = _sync_playlists_and_get_targets(conn, sp, force_all)

        _status.set(phase="tracks", run_total=len(targets), run_done=0)
        for i, p in enumerate(targets):
            _status.set(current_playlist=p["name"], run_done=i)
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
        _status.set(run_done=len(targets), current_playlist=None)

        _status.set(phase="liked_songs")
        liked_row = conn.execute(
            "SELECT excluded FROM snapshot WHERE playlist_id = ?", (LIKED_PLAYLIST_ID,)
        ).fetchone()
        if not (liked_row and liked_row["excluded"]):
            try:
                _pull_liked_songs(conn, sp)
                conn.commit()
            except RateLimited:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                _record_failure(conn, LIKED_PLAYLIST_ID, "Liked Songs", e)

        db.set_meta(conn, "last_full_pull_at" if force_all else "last_refresh_at", jobs.now_iso())
        canonical.ensure_track_groups(conn)
        conn.commit()
        scoring.recompute(conn)

        _status.set(phase="done", finished_at=jobs.now_iso())
    except RateLimited as e:
        # Discard anything this run left uncommitted before recomputing --
        # otherwise recompute()'s own commit would inadvertently make durable
        # a partial write this path means to drop on close.
        conn.rollback()
        scoring.recompute(conn)
        _status.set(phase="error", error=str(e), retry_at=e.retry_at, finished_at=jobs.now_iso())
    except Exception as e:
        conn.rollback()
        scoring.recompute(conn)
        _status.set(phase="error", error=str(e), finished_at=jobs.now_iso())
    finally:
        conn.close()


def _run_backfill():
    """Refills tracks stuck without raw_json, one GET /v1/tracks/{id} each --
    the batch ?ids= endpoint 403s for this app, so requests really do equal
    tracks. Touches no membership rows: this is metadata only."""
    conn = db.connect()
    sp = get_spotify_client()
    try:
        _reset_status("backfill", "backfill")
        if sp is None:
            raise RuntimeError("not_authenticated")

        track_ids = [
            row["track_id"]
            for row in conn.execute(
                "SELECT track_id FROM track WHERE raw_json IS NULL ORDER BY track_id"
            )
        ]
        _status.set(run_total=len(track_ids), run_done=0)

        for i, track_id in enumerate(track_ids):
            _status.set(current_playlist=track_id, run_done=i)
            try:
                track = _call(sp.track, track_id)
                if not _usable_track(track):
                    raise RuntimeError("track unavailable")
                _upsert_track_full(conn, _parse_track_item(track, None, None))
                conn.commit()
            except RateLimited:
                # An app-level quota block affects every request, not just
                # this track -- abort rather than burning the rest.
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                _status.append(
                    "failed_playlists",
                    {"playlist_id": track_id, "name": track_id, "error": str(e)},
                )
        _status.set(run_done=len(track_ids), current_playlist=None)

        canonical.ensure_track_groups(conn)
        conn.commit()
        scoring.recompute(conn)
        _status.set(phase="done", finished_at=jobs.now_iso())
    except RateLimited as e:
        conn.rollback()
        scoring.recompute(conn)
        _status.set(phase="error", error=str(e), retry_at=e.retry_at, finished_at=jobs.now_iso())
    except Exception as e:
        conn.rollback()
        scoring.recompute(conn)
        _status.set(phase="error", error=str(e), finished_at=jobs.now_iso())
    finally:
        conn.close()


# -- Playlist list sync -------------------------------------------------


def _sync_playlists_and_get_targets(conn, sp, force_all):
    playlists = _fetch_all_playlists(sp)
    excluded_ids = {
        row["playlist_id"]
        for row in conn.execute("SELECT playlist_id FROM snapshot WHERE excluded = 1")
    }
    seen_ids = set()
    targets = []
    for p in playlists:
        seen_ids.add(p["id"])
        stored = conn.execute(
            "SELECT snapshot_id FROM snapshot WHERE playlist_id = ?", (p["id"],)
        ).fetchone()
        is_new_or_changed = stored is None or stored["snapshot_id"] != p["snapshot_id"]
        # Playlist-level metadata (name, snapshot_id, image, description,
        # track_count) is refreshed for every playlist regardless of
        # exclusion -- only the item-read pass below is skipped.
        _upsert_snapshot_playlist(conn, p)
        _upsert_card(conn, p)
        if p["id"] not in excluded_ids and (force_all or is_new_or_changed):
            targets.append(p)
    conn.commit()

    existing_ids = {
        row["playlist_id"]
        for row in conn.execute(
            "SELECT playlist_id FROM snapshot WHERE playlist_id != ? AND unfollowed_at IS NULL",
            (LIKED_PLAYLIST_ID,),
        )
    }
    now = jobs.now_iso()
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


def _sortable_release_date(release_date, precision):
    """Pads a release_date to a full date so it sorts correctly regardless
    of precision: year -> YYYY-01-01, month -> YYYY-MM-01, day unchanged."""
    if not release_date:
        return None
    if precision == "year":
        return f"{release_date}-01-01"
    if precision == "month":
        return f"{release_date}-01"
    return release_date


def _parse_album(album):
    if not album.get("id"):
        return None
    release_date = album.get("release_date")
    precision = album.get("release_date_precision")
    external_urls = album.get("external_urls") or {}
    return {
        "album_id": album["id"],
        "name": album.get("name"),
        "album_type": album.get("album_type"),
        "release_date": release_date,
        "release_date_precision": precision,
        "release_year": int(release_date[:4]) if release_date else None,
        "release_date_sortable": _sortable_release_date(release_date, precision),
        "total_tracks": album.get("total_tracks"),
        "image_url": _album_image_url(album),
        "external_url": external_urls.get("spotify"),
        "raw_json": json.dumps(album, separators=(",", ":")),
    }


def _parse_track_item(track, added_at, position):
    album = track.get("album") or {}
    track_artists = track.get("artists") or []
    album_artists = album.get("artists") or []
    external_urls = track.get("external_urls") or {}
    external_ids = track.get("external_ids") or {}
    linked_from = track.get("linked_from")
    is_playable = track.get("is_playable")
    return {
        "track_id": track["id"],
        "name": track.get("name"),
        "artists": ", ".join(a["name"] for a in track_artists),
        "album_id": album.get("id"),
        "duration_ms": track.get("duration_ms"),
        "explicit": 1 if track.get("explicit") else 0,
        "external_url": external_urls.get("spotify"),
        "uri": track.get("uri"),
        "isrc": external_ids.get("isrc"),
        "track_number": track.get("track_number"),
        "disc_number": track.get("disc_number"),
        "is_playable": None if is_playable is None else (1 if is_playable else 0),
        "linked_from": json.dumps(linked_from, separators=(",", ":")) if linked_from else None,
        "linked_from_id": (linked_from or {}).get("id"),
        "raw_json": json.dumps(track, separators=(",", ":")),
        "added_at": added_at,
        "position": position,
        "album": _parse_album(album),
        "track_artists": track_artists,
        "album_artists": album_artists,
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


def _upsert_artist(conn, artist):
    if not artist.get("id"):
        return
    external_urls = artist.get("external_urls") or {}
    conn.execute(
        """
        INSERT INTO artist (artist_id, name, external_url, raw_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(artist_id) DO UPDATE SET
            name=excluded.name,
            external_url=excluded.external_url,
            raw_json=excluded.raw_json
        """,
        (
            artist["id"],
            artist.get("name"),
            external_urls.get("spotify"),
            json.dumps(artist, separators=(",", ":")),
        ),
    )


def _upsert_album(conn, album):
    conn.execute(
        """
        INSERT INTO album (album_id, name, album_type, release_date, release_date_precision,
                            release_year, release_date_sortable, total_tracks, image_url,
                            external_url, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(album_id) DO UPDATE SET
            name=excluded.name,
            album_type=excluded.album_type,
            release_date=excluded.release_date,
            release_date_precision=excluded.release_date_precision,
            release_year=excluded.release_year,
            release_date_sortable=excluded.release_date_sortable,
            total_tracks=excluded.total_tracks,
            -- COALESCE, not plain overwrite: Spotify sometimes omits images
            -- on an album object, and a NULL from one pull must not wipe a
            -- value we already have.
            image_url=COALESCE(excluded.image_url, image_url),
            external_url=excluded.external_url,
            raw_json=excluded.raw_json
        """,
        (
            album["album_id"],
            album["name"],
            album["album_type"],
            album["release_date"],
            album["release_date_precision"],
            album["release_year"],
            album["release_date_sortable"],
            album["total_tracks"],
            album["image_url"],
            album["external_url"],
            album["raw_json"],
        ),
    )


def _replace_track_artists(conn, track_id, artists):
    conn.execute("DELETE FROM track_artist WHERE track_id = ?", (track_id,))
    for position, artist in enumerate(artists):
        if not artist.get("id"):
            continue
        conn.execute(
            "INSERT INTO track_artist (track_id, artist_id, position) VALUES (?, ?, ?)",
            (track_id, artist["id"], position),
        )


def _replace_album_artists(conn, album_id, artists):
    conn.execute("DELETE FROM album_artist WHERE album_id = ?", (album_id,))
    for position, artist in enumerate(artists):
        if not artist.get("id"):
            continue
        conn.execute(
            "INSERT INTO album_artist (album_id, artist_id, position) VALUES (?, ?, ?)",
            (album_id, artist["id"], position),
        )


def _upsert_track(conn, it):
    conn.execute(
        """
        INSERT INTO track (track_id, name, artists, album_id, duration_ms, explicit,
                            external_url, uri, isrc, track_number, disc_number, is_playable,
                            linked_from, linked_from_id, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            name=excluded.name,
            artists=excluded.artists,
            album_id=excluded.album_id,
            duration_ms=excluded.duration_ms,
            explicit=excluded.explicit,
            external_url=excluded.external_url,
            uri=excluded.uri,
            -- COALESCE, not plain overwrite: Spotify sometimes omits
            -- external_ids on a track object, and a NULL from one pull must
            -- not wipe a value we already have.
            isrc=COALESCE(excluded.isrc, isrc),
            track_number=excluded.track_number,
            disc_number=excluded.disc_number,
            is_playable=excluded.is_playable,
            linked_from=excluded.linked_from,
            linked_from_id=excluded.linked_from_id,
            raw_json=excluded.raw_json
        """,
        (
            it["track_id"],
            it["name"],
            it["artists"],
            it["album_id"],
            it["duration_ms"],
            it["explicit"],
            it["external_url"],
            it["uri"],
            it["isrc"],
            it["track_number"],
            it["disc_number"],
            it["is_playable"],
            it["linked_from"],
            it["linked_from_id"],
            it["raw_json"],
        ),
    )


def _upsert_track_full(conn, it):
    """Upsert order: artists (from both the track and its album), then the
    album, then the track, then replace the credit tables -- artist/album
    rows must exist before track_artist/album_artist can reference them
    under PRAGMA foreign_keys=ON."""
    for artist in it["track_artists"]:
        _upsert_artist(conn, artist)
    for artist in it["album_artists"]:
        _upsert_artist(conn, artist)
    if it["album"]:
        _upsert_album(conn, it["album"])
    _upsert_track(conn, it)
    _replace_track_artists(conn, it["track_id"], it["track_artists"])
    if it["album"]:
        _replace_album_artists(conn, it["album"]["album_id"], it["album_artists"])


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

    now = jobs.now_iso()

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
            _upsert_track_full(conn, it)
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
        (jobs.now_iso(), live_count, last_changed, playlist_id),
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
