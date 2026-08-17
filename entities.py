"""Read paths for the entity viewing pages (docs/specs/entity-pages-K.md)
that don't belong to an existing module owner: play stats, the playlist
rollup over a track set, and the two guarded one-request-per-page-load
Spotify detail fetches (album tracklist, artist image). The fetches are the
only writes here, and they only ever write locally -- read-only w.r.t. the
Spotify library, like everything except roundtrip.py."""

import json
from datetime import datetime, timedelta, timezone

import jobs
from spotify_client import get_spotify_client

_WEEK_DAYS = 7
_MONTH_DAYS = 30


def play_stats(conn, track_ids):
    """{"total", "month", "week", "data_through"} over every play resolving
    to any of track_ids, through played_uri_track so relinked uris count.
    Windows are past 7/30 days relative to now; when data_through predates a
    window's start that window is None (the play_stats macro renders "—"),
    so a stale export reads differently from a genuine zero. total is never
    None -- it isn't windowed, so staleness doesn't apply to it."""
    data_through = conn.execute("SELECT MAX(ts) FROM play").fetchone()[0]
    if not track_ids:
        return {"total": 0, "month": 0, "week": 0, "data_through": data_through}

    placeholders = ",".join("?" for _ in track_ids)
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=_WEEK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    month_start = (now - timedelta(days=_MONTH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN p.ts >= ? THEN 1 ELSE 0 END) AS month,
               SUM(CASE WHEN p.ts >= ? THEN 1 ELSE 0 END) AS week
        FROM play p
        JOIN played_uri_track x ON x.uri = p.spotify_track_uri
        WHERE x.track_id IN ({placeholders})
        """,
        [month_start, week_start, *track_ids],
    ).fetchone()

    month = row["month"] or 0
    week = row["week"] or 0
    if data_through and data_through < month_start:
        month = None
    if data_through and data_through < week_start:
        week = None
    return {"total": row["total"], "month": month, "week": week, "data_through": data_through}


def playlists_for_tracks(conn, track_ids):
    """Every playlist membership (live or removed) for any of track_ids:
    playlist id/name, which track, added_at, removed_at. The rollup the
    group, album and artist pages each need over their own track set."""
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    return conn.execute(
        f"""
        SELECT m.playlist_id, s.name AS playlist_name, m.track_id, m.added_at, m.removed_at
        FROM membership m
        JOIN snapshot s ON s.playlist_id = m.playlist_id
        WHERE m.track_id IN ({placeholders})
        ORDER BY s.name COLLATE NOCASE, m.added_at
        """,
        list(track_ids),
    ).fetchall()


def fetch_album_tracklist(conn, album_id):
    """One request to GET /v1/albums/{id} on first view (tracklist_pulled_at
    IS NULL only -- never re-fetched automatically): stores the tracklist as
    returned, capped at Spotify's own 50-item first page. A failed attempt --
    429, network, 404 -- still stamps tracklist_pulled_at with tracklist_json
    left NULL, so it counts as "tried" and isn't retried on every subsequent
    view (P1-016); the page always renders from what the DB already has
    either way. No client at all (not logged in) doesn't stamp anything --
    that's not a real attempt.

    Queuing wanted_uri rows is queue_wanted_uris()'s job, not this one's --
    a caller runs both (docs/specs/grouping-fixes-backfill-M.md §4.4)."""
    sp = get_spotify_client()
    if sp is None:
        return
    try:
        album = sp.album(album_id)
    except Exception:
        conn.execute(
            "UPDATE album SET tracklist_pulled_at = ? WHERE album_id = ?",
            (jobs.now_iso(), album_id),
        )
        conn.commit()
        return

    items = (album.get("tracks") or {}).get("items") or []
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = ? WHERE album_id = ?",
        (json.dumps(items, separators=(",", ":")), jobs.now_iso(), album_id),
    )
    conn.commit()


def queue_wanted_uris(conn, album_id, source):
    """Queues a wanted_uri row for every item in the *stored* tracklist_json
    with no track row of its own, stamped with album_id and source. Zero
    Spotify requests, so it's cheap enough to run on every album-page view
    rather than only the first -- which is what closes the re-add gap where
    clearing a queued uri and revisiting the page used to queue nothing back
    (§0.5). INSERT OR IGNORE, so a uri already queued (by either source)
    keeps whichever source/album_id it was first queued with.

    Returns how many rows it actually inserted."""
    row = conn.execute(
        "SELECT tracklist_json FROM album WHERE album_id = ?", (album_id,)
    ).fetchone()
    if row is None or not row["tracklist_json"]:
        return 0
    items = json.loads(row["tracklist_json"])

    item_ids = [item["id"] for item in items if item.get("id")]
    owned = set()
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        owned = {
            r["track_id"]
            for r in conn.execute(
                f"SELECT track_id FROM track WHERE track_id IN ({placeholders})", item_ids
            )
        }
    queued = 0
    for item in items:
        if item.get("id") and item["id"] not in owned and item.get("uri"):
            cur = conn.execute(
                "INSERT OR IGNORE INTO wanted_uri (uri, source, album_id) VALUES (?, ?, ?)",
                (item["uri"], source, album_id),
            )
            queued += cur.rowcount
    conn.commit()
    return queued


def fetch_artist_image(conn, artist_id):
    """One request to GET /v1/artists/{id} on first view (detail_pulled_at
    IS NULL only): stores the largest image url and stamps detail_pulled_at
    regardless of whether an image came back, so a future visit doesn't
    re-fetch. genres/followers/popularity are absent from this endpoint for
    this app and aren't worth storing. A failed attempt -- 429, network, 404
    -- also stamps detail_pulled_at (image_url left NULL), so it counts as
    "tried" rather than retrying on every subsequent view (P1-016), same as
    fetch_album_tracklist. No client at all (not logged in) doesn't stamp
    anything -- that's not a real attempt."""
    sp = get_spotify_client()
    if sp is None:
        return
    try:
        artist = sp.artist(artist_id)
    except Exception:
        conn.execute(
            "UPDATE artist SET detail_pulled_at = ? WHERE artist_id = ?",
            (jobs.now_iso(), artist_id),
        )
        conn.commit()
        return

    # Largest by width, not images[0] -- Spotify's ordering is undocumented
    # and not guaranteed to be largest-first (P1-016).
    images = artist.get("images") or []
    image_url = max(images, key=lambda im: im.get("width") or 0)["url"] if images else None
    conn.execute(
        "UPDATE artist SET image_url = ?, detail_pulled_at = ? WHERE artist_id = ?",
        (image_url, jobs.now_iso(), artist_id),
    )
    conn.commit()
