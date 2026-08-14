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
    IS NULL only -- never re-fetched automatically): stores the tracklist and
    queues any track uri with no track row of its own for the round-trip.
    Any failure -- no client, 429, network, 404 -- is swallowed; the page
    always renders from what the DB already has."""
    sp = get_spotify_client()
    if sp is None:
        return
    try:
        album = sp.album(album_id)
    except Exception:
        return

    items = (album.get("tracks") or {}).get("items") or []
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = ? WHERE album_id = ?",
        (json.dumps(items, separators=(",", ":")), jobs.now_iso(), album_id),
    )

    item_ids = [item["id"] for item in items if item.get("id")]
    owned = set()
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        owned = {
            row["track_id"]
            for row in conn.execute(
                f"SELECT track_id FROM track WHERE track_id IN ({placeholders})", item_ids
            )
        }
    for item in items:
        if item.get("id") and item["id"] not in owned and item.get("uri"):
            conn.execute(
                "INSERT OR IGNORE INTO wanted_uri (uri, source) VALUES (?, 'album')",
                (item["uri"],),
            )
    conn.commit()


def fetch_artist_image(conn, artist_id):
    """One request to GET /v1/artists/{id} on first view (detail_pulled_at
    IS NULL only): stores the largest image url and stamps detail_pulled_at
    regardless of whether an image came back, so a future visit doesn't
    re-fetch. genres/followers/popularity are absent from this endpoint for
    this app and aren't worth storing. Any failure is swallowed, same as
    fetch_album_tracklist."""
    sp = get_spotify_client()
    if sp is None:
        return
    try:
        artist = sp.artist(artist_id)
    except Exception:
        return

    images = artist.get("images") or []
    image_url = images[0]["url"] if images else None
    conn.execute(
        "UPDATE artist SET image_url = ?, detail_pulled_at = ? WHERE artist_id = ?",
        (image_url, jobs.now_iso(), artist_id),
    )
    conn.commit()
