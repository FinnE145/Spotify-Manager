"""Read paths for the entity viewing pages (docs/specs/entity-pages-K.md).

Two kinds of thing live here. The rollups any page can want over an
arbitrary track set -- play_stats and playlists_for_tracks -- plus the two
guarded one-request-per-page-load Spotify detail fetches (album tracklist,
artist image). And, since P3 (docs/codebase-health/P3_refactor.md §4.1),
one detail function per entity page: everything /track, /album, /artist,
/playlist, /search and the four group pages render, assembled here and
returned as the template's kwargs.

**No Flask in this module** (§4.1) -- no abort, no redirect, no request, no
render_template. A missing row returns None and the route raises the 404
with the description it already used, which is what lets every function
below be called against a fixture database with no request context.
Parsing request.args stays in the route too; these take plain arguments.

`canonical.ensure_track_groups(conn)` + `conn.commit()` also stays in the
route, per §2: canonical.py never commits, and splitting that pairing
across two modules is how that invariant gets broken by accident.

The two fetches are the only writes, and they only ever write locally --
read-only w.r.t. the Spotify library, like everything except roundtrip.py."""

import json
from datetime import datetime, timedelta, timezone

import canonical
import generations
import jobs
import scoring
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


# -- One detail function per entity page (P3_refactor.md §4.1) -------------
#
# Each returns exactly the kwargs its template is rendered with, so the
# route is a 404 guard and a render_template(..., **data). They are in page
# order, matching the order of the routes in app.py.


def group_detail(conn, tier, group_id):
    """Everything one of the four group pages renders (/song, /version,
    /recording, /release), keyed by tier.

    Two distinct misses, because the route raises two distinct 404s:
    **None** means there is no group with that id at that tier, and a dict
    whose **track_count is 0** -- holding nothing else -- means the group
    exists but has no members. The second can't be left to the route: every
    line below it indexes track_ids[0]."""
    row = conn.execute("SELECT tier FROM canonical_group WHERE id = ?", (group_id,)).fetchone()
    # The `!= tier` half cannot change the *status*: group ids are one space
    # across all four tiers, so a wrong-tier id yields an empty member list and
    # the caller's "no members" 404 catches it anyway (P2-009). It is kept for
    # the honest message -- "no such group" rather than "group has no members"
    # -- and to skip building a tree; a test asserting only a status code here
    # is one that cannot fail, which is how this was found.
    if row is None or row["tier"] != tier:
        return None

    tree = canonical.group_tree(conn, tier, group_id)
    track_ids = tree["track_ids"]
    if not track_ids:
        return {"track_count": 0}

    rep_id = tree["representative_track_id"]
    rep = canonical.track_display(conn, rep_id) if rep_id else None
    artist_credits = canonical.artist_credits_for_tracks(conn, track_ids)

    breadcrumb = conn.execute(
        "SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id = ?",
        (track_ids[0],),
    ).fetchone()

    track_scores = scoring.scores_for_tier(conn, "track", track_ids)
    member_tracks = sorted(
        (canonical.track_display(conn, tid) for tid in track_ids),
        key=lambda t: (
            -track_scores.get(t["track_id"], {}).get("all_time", 0.0),
            (t["name"] or "").casefold(),
        ),
    )
    tracks_by_id = {t["track_id"]: t for t in member_tracks}

    ordinals = generations.presence_for_tracks(conn, track_ids)
    runs = generations.runs(ordinals) if ordinals else []
    spans = generations.generation_spans(conn)

    return {
        "tier": tier,
        "group_id": group_id,
        "rep": rep,
        "artist_credits": artist_credits,
        "pinned": tree["pinned"],
        "track_count": len(track_ids),
        "breadcrumb": breadcrumb,
        "stats": play_stats(conn, track_ids),
        "playlists": playlists_for_tracks(conn, track_ids),
        "tracks_by_id": tracks_by_id,
        # "song" isn't a materialized tier (§9.1) -- it aggregates at
        # query time from its member versions, same as album/artist/
        # playlist, so it needs song_scores() rather than a direct lookup.
        "score": (
            scoring.song_scores(conn, [group_id]).get(group_id)
            if tier == "song"
            else scoring.get_both(conn, tier, group_id)
        ),
        "spans": spans,
        "ordinals": ordinals,
        "tenure": max((end - start + 1 for start, end in runs), default=0),
        "total_generations": len(ordinals),
        "run_count": len(runs),
        "tree": tree,
        "member_tracks": member_tracks,
    }


def track_detail(conn, track_id):
    """Everything /track/<id> renders, or None when there is no such
    track."""
    if conn.execute("SELECT 1 FROM track WHERE track_id = ?", (track_id,)).fetchone() is None:
        return None

    track = canonical.track_display(conn, track_id)
    track_artists = canonical.artist_credits_for_tracks(conn, [track_id]).get(track_id, [])
    groups = canonical.groups_for_track(conn, track_id)

    memberships = conn.execute(
        """
        SELECT m.playlist_id, s.name AS playlist_name, m.added_at, m.removed_at, m.position
        FROM membership m
        JOIN snapshot s ON s.playlist_id = m.playlist_id
        WHERE m.track_id = ?
        ORDER BY s.name COLLATE NOCASE, m.added_at
        """,
        (track_id,),
    ).fetchall()

    aliases = conn.execute(
        "SELECT requested_uri FROM track_uri_alias WHERE track_id = ? ORDER BY requested_uri",
        (track_id,),
    ).fetchall()

    return {
        "track": track,
        "track_artists": track_artists,
        "groups": groups,
        "memberships": memberships,
        "stats": play_stats(conn, [track_id]),
        "aliases": aliases,
        "score": scoring.get_both(conn, "track", track_id),
    }


def playlist_detail(conn, playlist_id):
    """Everything /playlist/<id> renders, or None when there is no such
    playlist.

    The generation ordinal comes back in the payload rather than the
    carried/new split itself: that split is generations.generation_view()'s
    job, and whether to build it at all depends on ?generation=1, which
    only the route sees."""
    # All 15 columns, named rather than `SELECT *` (P3_refactor.md §4.5) --
    # the template reads this row by name and wants most of it, so the list
    # is the whole table rather than a subset. Kept separate from
    # snapshot.py's identical list on /dev/snapshot: a shared constant
    # between the two modules would just become a cross-module import.
    playlist = conn.execute(
        "SELECT playlist_id, name, image_url, owner, track_count, pulled_at, "
        "snapshot_id, last_changed_at, tracks_pulled_at, unfollowed_at, description, "
        "last_pull_error, excluded, generation_declined, tracks_pulled_snapshot_id "
        "FROM snapshot WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    if playlist is None:
        return None

    rows = conn.execute(
        """
        SELECT m.id, m.track_id, t.name, t.album_id, a.name AS album_name, t.duration_ms,
               m.added_at, m.removed_at, m.position
        FROM membership m
        JOIN track t ON t.track_id = m.track_id
        LEFT JOIN album a ON a.album_id = t.album_id
        WHERE m.playlist_id = ?
        ORDER BY m.position
        """,
        (playlist_id,),
    ).fetchall()

    version_by_track = {}
    for r in rows:
        g = canonical.groups_for_track(conn, r["track_id"])
        version_by_track[r["track_id"]] = g["version"] if g else None
    artist_credits = canonical.artist_credits_for_tracks(conn, [r["track_id"] for r in rows])

    live_track_ids = [r["track_id"] for r in rows if r["removed_at"] is None]
    totals = conn.execute(
        "SELECT SUM(t.duration_ms) AS runtime, MIN(m.added_at) AS first_added, "
        "MAX(m.added_at) AS last_added FROM membership m JOIN track t ON t.track_id = m.track_id "
        "WHERE m.playlist_id = ? AND m.removed_at IS NULL",
        (playlist_id,),
    ).fetchone()

    generation = conn.execute(
        "SELECT ordinal FROM generation WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()

    return {
        "playlist": playlist,
        "rows": rows,
        "version_by_track": version_by_track,
        "artist_credits": artist_credits,
        "totals": totals,
        "stats": play_stats(conn, live_track_ids),
        "generation": generation,
        "score": scoring.playlist_scores(conn, [playlist_id]).get(
            playlist_id, {"all_time": 0.0, "recent": 0.0}
        ),
    }


def album_detail(conn, album_id):
    """Everything /album/<id> renders, or None when there is no such album.

    Spends **at most one Spotify request, on first view only** -- the
    tracklist_pulled_at guard below is that ceiling (entity-pages-K.md
    §5.3), and it is one half of a rule whose other half is the stamp
    fetch_album_tracklist() writes even when the fetch fails (P1-016).
    queue_wanted_uris() runs on every view, deliberately; see below."""

    def _load():
        return conn.execute(
            "SELECT album_id, name, album_type, release_date, total_tracks, image_url, "
            "external_url, tracklist_json, tracklist_pulled_at FROM album WHERE album_id = ?",
            (album_id,),
        ).fetchone()

    album = _load()
    if album is None:
        return None

    if album["tracklist_pulled_at"] is None:
        fetch_album_tracklist(conn, album_id)
        album = _load()
    # Every view, not just the first (spec M §4.4/§0.5): cheap (zero
    # Spotify calls, reads the tracklist already stored) and it's what
    # makes clearing the backfill/album-page queue a real undo -- a
    # cleared uri comes back the moment this page is revisited.
    queue_wanted_uris(conn, album_id, source="album")

    artist_rows = conn.execute(
        # Grouped so a credit under both an alias id and its already-
        # canonical id collapses to one row (P1-016) -- same reasoning as
        # track_artist_credit's GROUP BY. resolved_album_artist carries
        # no position, so this stays an inline query rather than routing
        # through that view.
        "SELECT COALESCE(aa.canonical_artist_id, ab.artist_id) AS artist_id, ar.name "
        "FROM album_artist ab "
        "LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id "
        "JOIN artist ar ON ar.artist_id = COALESCE(aa.canonical_artist_id, ab.artist_id) "
        "WHERE ab.album_id = ? "
        "GROUP BY COALESCE(aa.canonical_artist_id, ab.artist_id), ar.name "
        "ORDER BY MIN(ab.position)",
        (album_id,),
    ).fetchall()

    owned_ids = [
        r["track_id"]
        for r in conn.execute("SELECT track_id FROM track WHERE album_id = ?", (album_id,))
    ]
    # Owned tracks only: an unowned tracklist entry's artist ids come
    # straight from Spotify's simplified object, with no guarantee we've
    # ever ingested an `artist` row for them (fetch_album_tracklist never
    # upserts artists), so linking those would often 404.
    artist_credits = canonical.artist_credits_for_tracks(conn, owned_ids)

    # An owned row built from Symr's own `track` table. Used both when the
    # fetch never succeeded and for the tracks the fetched page didn't
    # reach, so there's one way to render a track Symr actually holds.
    def _owned_rows(track_ids):
        if not track_ids:
            return []
        placeholders = ",".join("?" for _ in track_ids)
        out = []
        for r in conn.execute(
            "SELECT t.track_id, t.name, t.track_number, t.disc_number, t.duration_ms, t.explicit, "
            "       COALESCE(ta.artists, '') AS artists "
            "FROM track t LEFT JOIN track_artists ta ON ta.track_id = t.track_id "
            f"WHERE t.track_id IN ({placeholders})",
            list(track_ids),
        ):
            g = canonical.groups_for_track(conn, r["track_id"])
            out.append(
                {
                    "owned": True,
                    "track_id": r["track_id"],
                    "version_id": g["version"] if g else None,
                    "name": r["name"],
                    "artists": r["artists"],
                    "duration_ms": r["duration_ms"],
                    "explicit": r["explicit"],
                    "track_number": r["track_number"],
                    "disc_number": r["disc_number"],
                }
            )
        return out

    rows = []
    tracklist = json.loads(album["tracklist_json"]) if album["tracklist_json"] else None
    fetched = tracklist is not None
    if fetched:
        owned_set = set(owned_ids)
        for item in tracklist:
            tid = item.get("id")
            is_owned = bool(tid and tid in owned_set)
            version_id = None
            if is_owned:
                g = canonical.groups_for_track(conn, tid)
                version_id = g["version"] if g else None
            rows.append(
                {
                    "owned": is_owned,
                    "track_id": tid,
                    "version_id": version_id,
                    "name": item.get("name"),
                    "artists": ", ".join(a.get("name", "") for a in item.get("artists") or []),
                    "duration_ms": item.get("duration_ms"),
                    "explicit": item.get("explicit"),
                    "track_number": item.get("track_number"),
                    "disc_number": item.get("disc_number"),
                }
            )
    else:
        # Never successfully fetched -- show only what's independently
        # known from Symr's own library rather than nothing at all.
        rows = _owned_rows(owned_ids)
    rows.sort(key=lambda r: (r["disc_number"] or 1, r["track_number"] or 0))

    # Owned tracks the fetched page didn't contain (§5.2). An album past 50
    # tracks can easily hold the one track Symr knows beyond the first page,
    # and without these the tracklist contradicts its own "N of M known"
    # header. Costs no request -- these come from the `track` table.
    shown = {r["track_id"] for r in rows if r["track_id"]}
    appended = _owned_rows([tid for tid in owned_ids if tid not in shown])
    appended.sort(key=lambda r: (r["disc_number"] or 1, r["track_number"] or 0))

    track_names = {r["track_id"]: r["name"] for r in rows + appended if r["owned"]}

    return {
        "album": album,
        "artists": artist_rows,
        "rows": rows,
        "appended": appended,
        "track_artist_credits": artist_credits,
        "track_names": track_names,
        "fetched": fetched,
        # How many the fetch actually stored, vs. total_tracks -- the
        # backfill job pages past the entity page's own 50-track cap
        # (P1-016), so "first 50" is only true when this is still < 50.
        "tracklist_count": len(tracklist) if fetched else None,
        "known_count": len(owned_ids),
        "stats": play_stats(conn, owned_ids),
        "playlists": playlists_for_tracks(conn, owned_ids),
        "score": scoring.album_scores(conn, [album_id]).get(
            album_id, {"all_time": 0.0, "recent": 0.0}
        ),
    }


def artist_detail(conn, artist_id):
    """Everything /artist/<id> renders, or None when there is no such
    artist.

    The alias redirect stays in the route -- it returns a redirect, so it
    is routing, and by the time this is called artist_id is known to be a
    canonical id rather than an alias of one.

    Spends at most one Spotify request, on first view only, by the same
    detail_pulled_at guard shape as album_detail (entity-pages-K.md §7.1)."""

    def _load():
        return conn.execute(
            "SELECT artist_id, name, external_url, image_url, detail_pulled_at "
            "FROM artist WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()

    artist = _load()
    if artist is None:
        return None

    if artist["detail_pulled_at"] is None:
        fetch_artist_image(conn, artist_id)
        artist = _load()

    merged_ids = [
        r["artist_id"]
        for r in conn.execute(
            "SELECT artist_id FROM artist_alias WHERE canonical_artist_id = ? ORDER BY artist_id",
            (artist_id,),
        )
    ]

    credit_rows = conn.execute(
        "SELECT tar.track_id, tar.role, tg.version_id FROM track_artist_role tar "
        "JOIN track_group tg ON tg.track_id = tar.track_id WHERE tar.artist_id = ?",
        (artist_id,),
    ).fetchall()
    all_track_ids = [r["track_id"] for r in credit_rows]

    primary_versions, featured_versions = set(), set()
    for r in credit_rows:
        (primary_versions if r["role"] == "primary" else featured_versions).add(r["version_id"])
    # A version already counted as primary (via some other member track)
    # doesn't also need a featured row -- primary is the more informative
    # badge for this artist.
    featured_versions -= primary_versions

    def _version_rows(version_ids):
        scores = scoring.scores_for_tier(conn, "version", list(version_ids))
        out = []
        for vid in version_ids:
            rid = canonical.representative(conn, vid)
            if rid is None:
                continue
            out.append({"version_id": vid, **canonical.track_display(conn, rid)})
        out.sort(
            key=lambda t: (
                -scores.get(t["version_id"], {}).get("all_time", 0.0),
                (t["name"] or "").casefold(),
            )
        )
        return out

    album_rows = conn.execute(
        "SELECT a.album_id, a.name, a.release_date, a.image_url FROM resolved_album_artist raa "
        "JOIN album a ON a.album_id = raa.album_id "
        "WHERE raa.artist_id = ?",
        (artist_id,),
    ).fetchall()
    album_scores = scoring.album_scores(conn, [a["album_id"] for a in album_rows])
    album_rows = sorted(
        album_rows,
        key=lambda a: (
            -album_scores.get(a["album_id"], {}).get("all_time", 0.0),
            (a["name"] or "").casefold(),
        ),
    )

    playlists_seen = {}
    for row in playlists_for_tracks(conn, all_track_ids):
        playlists_seen.setdefault(row["playlist_id"], row)

    # Counted off the rendered rows, not off credit_rows: the lists below
    # are one row per version group, and _version_rows drops a group whose
    # representative resolves to nothing. Counting anything else lets the
    # header disagree with the list directly under it.
    primary_rows = _version_rows(primary_versions)
    featured_rows = _version_rows(featured_versions)

    return {
        "artist": artist,
        "merged_ids": merged_ids,
        "version_count": len(primary_rows) + len(featured_rows),
        "album_count": len(album_rows),
        "primary_tracks": primary_rows,
        "featured_tracks": featured_rows,
        "albums": album_rows,
        "playlists": list(playlists_seen.values()),
        "ordinals": generations.presence_for_tracks(conn, all_track_ids),
        "spans": generations.generation_spans(conn),
        "stats": play_stats(conn, all_track_ids),
        "score": scoring.artist_group_score(conn, [artist_id]),
    }


def search(conn, q):
    """The four ranked result lists behind /search?q=... -- songs (one row
    per version group), albums, alias-resolved artists, playlists -- as the
    template's kwargs.

    q is already stripped and known non-empty: the route owns that, and
    owns the ensure_track_groups() pairing that only runs when there is
    something to search for."""
    like = f"%{q}%"

    # All four groups here rank by score before capping at 50, not
    # after: a name-ordered cap returns the alphabetically-first 50
    # matches rather than the best 50 (docs/specs/scoring-H.md §11.1).
    # Every match is fetched (no SQL LIMIT) so the ranking sees the
    # whole result set, which searching bounds to a manageable size.
    track_rows = conn.execute(
        "SELECT t.track_id FROM track t WHERE t.name LIKE ? "
        "   OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id) "
        "              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "
        "ORDER BY t.name COLLATE NOCASE",
        (like, like),
    ).fetchall()
    seen_versions = {}
    for row in track_rows:
        groups = canonical.groups_for_track(conn, row["track_id"])
        if not groups or groups["version"] in seen_versions:
            continue
        seen_versions[groups["version"]] = row["track_id"]
    version_scores = scoring.scores_for_tier(conn, "version", list(seen_versions))
    ranked_versions = sorted(
        seen_versions, key=lambda vid: -version_scores.get(vid, {}).get("all_time", 0.0)
    )[:50]
    songs = []
    for vid in ranked_versions:
        rep_id = canonical.representative(conn, vid) or seen_versions[vid]
        songs.append({"version_id": vid, **canonical.track_display(conn, rep_id)})

    album_rows = conn.execute(
        "SELECT album_id, name, image_url FROM album WHERE name LIKE ? "
        "ORDER BY name COLLATE NOCASE",
        (like,),
    ).fetchall()
    album_score_map = scoring.album_scores(conn, [a["album_id"] for a in album_rows])
    albums = sorted(
        album_rows, key=lambda a: -album_score_map.get(a["album_id"], {}).get("all_time", 0.0)
    )[:50]

    artist_rows = conn.execute(
        "SELECT ar.artist_id, ar.name, COALESCE(aa.canonical_artist_id, ar.artist_id) AS resolved_id "
        "FROM artist ar LEFT JOIN artist_alias aa ON aa.artist_id = ar.artist_id "
        "WHERE ar.name LIKE ?",
        (like,),
    ).fetchall()
    seen_artists = {}
    for row in artist_rows:
        if row["resolved_id"] not in seen_artists:
            canonical_artist = conn.execute(
                "SELECT artist_id, name FROM artist WHERE artist_id = ?", (row["resolved_id"],)
            ).fetchone()
            seen_artists[row["resolved_id"]] = dict(canonical_artist)
    artist_score_map = scoring.artist_scores(conn, list(seen_artists))
    artists_result = sorted(
        seen_artists.values(),
        key=lambda a: (
            -artist_score_map.get(a["artist_id"], {}).get("all_time", 0.0),
            (a["name"] or "").casefold(),
        ),
    )[:50]

    playlist_rows = conn.execute(
        "SELECT playlist_id, name, image_url FROM snapshot WHERE name LIKE ? "
        "ORDER BY name COLLATE NOCASE",
        (like,),
    ).fetchall()
    playlist_score_map = scoring.playlist_scores(conn, [p["playlist_id"] for p in playlist_rows])
    playlists_result = sorted(
        playlist_rows,
        key=lambda p: -playlist_score_map.get(p["playlist_id"], {}).get("all_time", 0.0),
    )[:50]

    return {
        "songs": songs,
        "albums": albums,
        "artists": artists_result,
        "playlists": playlists_result,
    }


# -- /dev/generations/tenure (P3_refactor.md §4.1) -------------------------
#
# Not an entity page, and here rather than in generations.py -- which §4.1
# named -- for one reason: this needs scoring, and scoring.py already
# imports generations (scoring.py:32, for generation_spans), so the edge
# generations -> scoring would close exactly the kind of cycle §8's third
# criterion requires the graph not to have. This module already imports
# both, so it is the only existing home costing no new dependency. See
# P3-006.

_TENURE_SORT_KEYS = {
    "tenure": "tenure", "total": "total_generations", "runs": "run_count", "score": "score",
}
_TENURE_PAGE_SIZE = 100


def tenure_page(conn, tier, sort, page):
    """Everything /dev/generations/tenure renders, as the template's kwargs.

    `tier` is "version" or "song", whitelisted by the route (it shares that
    with /dev/generations). `sort` and `page` are the raw ?sort= / ?page=
    values: an unknown sort falls back to "tenure" and the page is clamped
    into range, and both come back in the returned kwargs, since the
    template renders them into its own sort links and pager."""
    spans = generations.generation_spans(conn)

    all_tenures = generations.tenures(conn, tier=tier)
    # Every row's score, computed up front: the sort below runs before
    # pagination, same as tenure/total/runs (docs/specs/scoring-H.md
    # §11.1). "song" aggregates at query time; "version" is a direct
    # materialized lookup.
    if tier == "version":
        score_map = scoring.scores_for_tier(conn, "version", [t["group_id"] for t in all_tenures])
    else:
        score_map = scoring.song_scores(conn, [t["group_id"] for t in all_tenures])
    for t in all_tenures:
        t["score"] = score_map.get(t["group_id"], {}).get("all_time", 0.0)

    if sort not in _TENURE_SORT_KEYS:
        sort = "tenure"
    sort_key = _TENURE_SORT_KEYS[sort]
    # group_id as the tiebreak keeps paging stable across requests.
    all_tenures.sort(key=lambda t: (-t[sort_key], t["group_id"]))

    total = len(all_tenures)
    total_pages = max(1, -(-total // _TENURE_PAGE_SIZE))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * _TENURE_PAGE_SIZE
    page_slice = all_tenures[start : start + _TENURE_PAGE_SIZE]

    rows = []
    for t in page_slice:
        rep_id = canonical.representative(conn, t["group_id"])
        present = {o for start_o, end_o in t["runs"] for o in range(start_o, end_o + 1)}
        rows.append(
            {
                **t,
                "representative": canonical.track_display(conn, rep_id) if rep_id else None,
                "present_ordinals": present,
            }
        )

    return {
        "tier": tier,
        "sort": sort,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "generation_count": len(spans),
        "spans": spans,
        "rows": rows,
    }
