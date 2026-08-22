"""Hand-built fixture rows (P2_tests.md §4.2).

Nothing real is committed to this repo, so these produce the tiny purposeful
rows unit tests want and the ~20-track shape route tests want. Every builder
takes keyword overrides and defaults everything else, so **a test states only
what it is about** -- a test whose setup is twenty lines of irrelevant
scaffolding is a test nobody will read.

Three conventions worth knowing before using them:

**They commit.** Test setup, not production code -- unlike canonical.py, whose
functions deliberately never commit. Route tests build data on the `conn`
fixture and then hit `client`, which uses a different connection, so uncommitted
rows would simply be invisible.

**They fill in parents.** The schema declares real foreign keys and db.connect()
sets `PRAGMA foreign_keys = ON`, so a track needs its album and artists to exist
first (the order snapshot._upsert_track_full uses: artists, then album, then
track, then the credit tables). Passing an id that does not exist yet creates it
rather than failing.

**Ids are generated and deterministic.** `make_track(conn)` twice gives
"track-1" and "track-2"; the counters reset per test, so the same test always
sees the same ids and an assertion can name one literally.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import db

_counters = defaultdict(int)

# For the one parameter whose None is a meaningful value rather than "give me
# the default" -- see make_membership's added_at. Everywhere else None-means-
# default is fine, because NULL there is either impossible or uninteresting.
UNSET = object()


def reset_ids():
    """Called per test by conftest's _clean_slate, so ids restart at 1."""
    _counters.clear()


def _next(kind):
    _counters[kind] += 1
    return f"{kind}-{_counters[kind]}"


def days_ago(days, hours=0):
    """An ISO-Z timestamp relative to *now* -- which the autouse `freezer`
    fixture has pinned to conftest.FROZEN_NOW.

    Reads the clock rather than importing that constant on purpose: it goes
    through exactly the same frozen `datetime.now` the code under test does, so
    "played 5 days ago" lands inside a 7-day window here for the same reason it
    would in production.
    """
    when = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _exists(conn, table, column, value):
    return (
        conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", (value,)).fetchone()
        is not None
    )


# -- Artists ----------------------------------------------------------------


def make_artist(conn, artist_id=None, name=None, **overrides):
    """One `artist` row. Returns its id."""
    artist_id = artist_id or _next("artist")
    if not _exists(conn, "artist", "artist_id", artist_id):
        row = {
            "artist_id": artist_id,
            "name": name if name is not None else f"Artist {artist_id}",
            "external_url": f"https://open.spotify.com/artist/{artist_id}",
            "raw_json": None,
            "image_url": None,
            "detail_pulled_at": None,
        }
        row.update(overrides)
        _insert(conn, "artist", row)
    conn.commit()
    return artist_id


def _ensure_artists(conn, artist_ids):
    """Creates any artist that does not exist yet, and returns their names in
    the order given -- which is the order the display string needs."""
    names = []
    for artist_id in artist_ids:
        make_artist(conn, artist_id)
        names.append(conn.execute(
            "SELECT name FROM artist WHERE artist_id = ?", (artist_id,)
        ).fetchone()["name"])
    return names


# -- Albums -----------------------------------------------------------------


def make_album(conn, album_id=None, name=None, artists=None, **overrides):
    """One `album` row plus its `album_artist` credits. Returns its id.

    `artists` is a list of artist ids, created if they do not exist. Their
    `position` is their index in that list, which is what carries "first credit
    is the album artist".
    """
    album_id = album_id or _next("album")
    artists = artists if artists is not None else [_next("artist")]
    _ensure_artists(conn, artists)

    if not _exists(conn, "album", "album_id", album_id):
        row = {
            "album_id": album_id,
            "name": name if name is not None else f"Album {album_id}",
            "album_type": "album",
            "release_date": "2024-01-15",
            "release_date_precision": "day",
            "release_year": 2024,
            "release_date_sortable": "2024-01-15",
            "total_tracks": 1,
            "image_url": f"https://i.scdn.co/image/{album_id}",
            "external_url": f"https://open.spotify.com/album/{album_id}",
            "raw_json": None,
            "tracklist_json": None,
            "tracklist_pulled_at": None,
        }
        row.update(overrides)
        _insert(conn, "album", row)
        for position, artist_id in enumerate(artists):
            _insert(
                conn,
                "album_artist",
                {"album_id": album_id, "artist_id": artist_id, "position": position},
            )
    conn.commit()
    return album_id


# -- Tracks -----------------------------------------------------------------


def make_track(conn, track_id=None, name=None, album_id=None, artists=None, **overrides):
    """One `track` row plus its `track_artist` credits. Returns its id.

    `track.artists` is filled in with ", ".join of the credited names, matching
    snapshot.py:536 -- it is a **write-only** display column that nothing reads
    (the read path is the `track_artists` view), but a fixture that left it
    empty would not look like a real row.
    """
    track_id = track_id or _next("track")
    artists = artists if artists is not None else [_next("artist")]
    names = _ensure_artists(conn, artists)
    if album_id is None:
        album_id = make_album(conn, artists=artists)
    elif not _exists(conn, "album", "album_id", album_id):
        make_album(conn, album_id=album_id, artists=artists)

    if not _exists(conn, "track", "track_id", track_id):
        row = {
            "track_id": track_id,
            "name": name if name is not None else f"Track {track_id}",
            "artists": ", ".join(names),
            "album_id": album_id,
            "duration_ms": 210_000,
            "explicit": 0,
            "external_url": f"https://open.spotify.com/track/{track_id}",
            "uri": f"spotify:track:{track_id}",
            "isrc": None,
            "track_number": 1,
            "disc_number": 1,
            "is_playable": 1,
            "linked_from": None,
            "linked_from_id": None,
            "raw_json": None,
        }
        row.update(overrides)
        _insert(conn, "track", row)
        for position, artist_id in enumerate(artists):
            _insert(
                conn,
                "track_artist",
                {"track_id": track_id, "artist_id": artist_id, "position": position},
            )
    conn.commit()
    return track_id


# -- Playlists and membership -----------------------------------------------


def make_playlist(conn, playlist_id=None, name=None, **overrides):
    """One `snapshot` row. Returns its playlist id.

    The table is called `snapshot` because it holds per-playlist *state*; the
    builder is named for the thing, not the table.

    **The defaults describe a playlist that has just been pulled successfully**
    -- `tracks_pulled_at` is now and `tracks_pulled_snapshot_id` equals
    `snapshot_id`, so snapshot._is_stale says it needs no refresh. Partial-pull
    tests (partial-pulls-J.md) mostly want the opposite and should override:
    `tracks_pulled_snapshot_id=None` for never-captured, a differing value for
    changed-since-capture, `last_pull_error="..."` for failing.
    """
    playlist_id = playlist_id or _next("playlist")
    if not _exists(conn, "snapshot", "playlist_id", playlist_id):
        row = {
            "playlist_id": playlist_id,
            "name": name if name is not None else f"Playlist {playlist_id}",
            "image_url": None,
            "owner": "finn",
            "track_count": 0,
            "pulled_at": days_ago(0),
            "snapshot_id": f"snap-{playlist_id}",
            "last_changed_at": None,
            "tracks_pulled_at": days_ago(0),
            "tracks_pulled_snapshot_id": f"snap-{playlist_id}",
            "unfollowed_at": None,
            "description": "",
            "last_pull_error": None,
            "excluded": 0,
            "generation_declined": 0,
        }
        row.update(overrides)
        _insert(conn, "snapshot", row)
    conn.commit()
    return playlist_id


def make_membership(
    conn, playlist_id=None, track_id=None, position=0, added_at=UNSET, removed_at=None, **overrides
):
    """One `membership` row. Returns its rowid.

    `membership` is an append-only log: a track that left a playlist keeps its
    row and gains a `removed_at`. "Live" everywhere in this codebase means
    `removed_at IS NULL`, which is this builder's default.

    **`added_at` defaults through UNSET, not through None**, unlike every other
    builder here. A NULL `added_at` is a real state with its own documented
    behaviour in `_diff_playlist_tracks` -- two NULL rows exact-match each
    other in the identity pass, and a NULL sorts as oldest and so never departs
    by the fallback (`snapshot.md`, "Change detection & diffing") -- so a test
    has to be able to ask for one. Under the usual None-means-default rule it
    could not, and a test that asked for NULL would silently get a timestamp
    and pass by exercising a different pass than it named.
    """
    playlist_id = make_playlist(conn, playlist_id)
    track_id = make_track(conn, track_id)
    row = {
        "playlist_id": playlist_id,
        "track_id": track_id,
        "position": position,
        "added_at": days_ago(30) if added_at is UNSET else added_at,
        "removed_at": removed_at,
    }
    row.update(overrides)
    cursor = _insert(conn, "membership", row)
    conn.commit()
    return cursor.lastrowid


# -- Plays ------------------------------------------------------------------


def make_play(conn, track_id=None, uri=None, ts=None, ms_played=None, **overrides):
    """One `play` row. Returns its rowid.

    A play does not reference `track` -- it carries a uri, and resolves to a
    track through the `played_uri_track` view. So `track_id` here is a
    convenience that looks up that track's uri; passing `uri` directly is how a
    test builds a play for something Symr does not know about yet, which is the
    round-trip's entire subject.
    """
    if uri is None:
        track_id = make_track(conn, track_id)
        uri = conn.execute(
            "SELECT uri FROM track WHERE track_id = ?", (track_id,)
        ).fetchone()["uri"]

    row = {
        # A real row_hash is a SHA-1 over 16 named source keys
        # (history_import.py); tests that exercise dedup should build it the
        # same way. This default only has to be unique and non-null.
        "row_hash": _next("play-hash"),
        "source": "export",
        "import_id": None,
        "source_file": "Streaming_History_Audio_2024.json",
        "ts": ts if ts is not None else days_ago(1),
        "ms_played": ms_played if ms_played is not None else 210_000,
        "spotify_track_uri": uri,
        "reported_track_name": None,
        "reported_artist_name": None,
        "reported_album_name": None,
        "reason_start": "trackdone",
        "reason_end": "trackdone",
        "shuffle": 0,
        "skipped": 0,
        "platform": "test",
        "conn_country": "GB",
        "ip_addr": None,
        "offline": 0,
        "offline_ts": None,
        "incognito_mode": 0,
    }
    row.update(overrides)
    cursor = _insert(conn, "play", row)
    conn.commit()
    return cursor.lastrowid


# -- Grouping and generations ------------------------------------------------


TIERS = ("song", "version", "recording", "release")


def make_group(conn, track_ids, representative_track_id=None, **tier_ids):
    """Puts `track_ids` into one group at each of the four tiers.

    Returns `{"song": id, "version": id, "recording": id, "release": id}`.
    Pass an existing id for a tier to join that tier's group instead of making
    a new one -- which is how a test builds two versions of one song:

        a = make_group(conn, ["track-1"])
        b = make_group(conn, ["track-2"], song=a["song"])

    **`canonical.apply_partition` is the engine and the source of truth.** Use
    this for read-path fixtures, where the point is a group that exists; use
    the engine when the point is how grouping decides.

    **`representative_track_id` defaults to NULL, and must.** This builder
    used to default it to `track_ids[0]`, which no production path does:
    `canonical._INSERT_GROUP_SQL` never writes the column, and
    `pin_representative` only ever writes it at the **song** tier. A pinned
    fixture short-circuits `canonical.representative()` before the election
    runs at all, so every test of the score tiebreak (scoring-H.md §11.3,
    P1-008) silently asserted the pin instead -- and passed, because the
    pinned track was usually the one the election would have picked anyway.
    Found while writing session 2; see P2 findings. A test that wants a pin
    calls `canonical.pin_representative` or passes this argument.
    """
    # Tracks first: canonical_group.representative_track_id is a real foreign
    # key into track, so a group cannot be inserted before its members exist.
    for track_id in track_ids:
        make_track(conn, track_id)

    groups = {}
    for tier in TIERS:
        if tier in tier_ids and tier_ids[tier] is not None:
            groups[tier] = tier_ids[tier]
            continue
        cursor = conn.execute(
            "INSERT INTO canonical_group (tier, representative_track_id) VALUES (?, ?)",
            (tier, representative_track_id),
        )
        groups[tier] = cursor.lastrowid

    for track_id in track_ids:
        conn.execute(
            "INSERT OR REPLACE INTO track_group "
            "(track_id, song_id, version_id, recording_id, release_id) VALUES (?, ?, ?, ?, ?)",
            (track_id, groups["song"], groups["version"], groups["recording"], groups["release"]),
        )
    conn.commit()
    return groups


def make_generation(conn, ordinal=None, playlist_id=None):
    """One `generation` row -- a current-favs playlist and its position.

    `ordinal` is **stored, not derived** from sort order (generations-B.md), so
    a test can build a gap in the sequence deliberately, which is exactly what
    P1-015's mid-sequence-empty-generation case needs.
    """
    if ordinal is None:
        _counters["generation"] += 1
        ordinal = _counters["generation"]
    playlist_id = make_playlist(conn, playlist_id, name=f"v{ordinal}.0.0")
    conn.execute(
        "INSERT OR REPLACE INTO generation (ordinal, playlist_id) VALUES (?, ?)",
        (ordinal, playlist_id),
    )
    conn.commit()
    return ordinal


def make_score(conn, tier, group_id, all_time=50.0, recent=50.0):
    """One `score` row, in **display space** (the 10-99-ish number).

    The real table is wholesale-replaced by scoring.recompute(), never
    upserted, so a test that cares how scores are *computed* should call that.
    This is for tests that need a particular score to exist -- the
    representative-track tiebreak (P1-008) being the one P1 named.
    """
    conn.execute(
        "INSERT OR REPLACE INTO score (tier, group_id, all_time, recent) VALUES (?, ?, ?, ?)",
        (tier, str(group_id), all_time, recent),
    )
    conn.commit()


# -- Plumbing ---------------------------------------------------------------


def _insert(conn, table, row):
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    return conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(row.values())
    )


# -- The org canvas ----------------------------------------------------------


def make_board(conn, name=None):
    """An *extra* `board` row, beyond the default one. Returns its id.

    Rarely what you want. `db.init_db()` already seeds board
    `db.DEFAULT_BOARD_ID` (1), and every canvas route hard-codes that id in its
    WHERE clause -- so a card built on a board from here is invisible to all of
    them, and the route still answers 200 with an empty canvas. That is a
    passing test that observes nothing, so `make_card`/`make_label` default to
    the seeded board and this exists only for a test that genuinely wants a
    second one (e.g. asserting the routes ignore it).
    """
    cursor = conn.execute("INSERT INTO board (name) VALUES (?)", (name or _next("board"),))
    conn.commit()
    return cursor.lastrowid


def make_card(conn, board_id=None, x=None, y=None, display_name=None, **overrides):
    """One `card` row. Returns its id.

    **`placement` defaults to `'placed'` here, where the schema defaults to
    `'tray'`.** Deliberate, and the opposite of the usual builder rule of
    mirroring the schema: `grouping.group_cards` filters to placed cards on its
    first line, so a builder defaulting to the schema value would hand every
    grouping test an empty canvas -- and the test would pass, having grouped
    nothing. A test about the tray says so explicitly.

    x/y default to 0, which is a real coordinate: the grouping tests are about
    distances, so every one of them passes both anyway.
    """
    if board_id is None:
        board_id = db.DEFAULT_BOARD_ID
    row = {
        "board_id": board_id,
        "entity_type": "playlist",
        "entity_id": _next("card-entity"),
        "display_name": display_name or _next("card"),
        "image_url": None,
        "note": "",
        "placement": "placed",
        "x": 0.0 if x is None else x,
        "y": 0.0 if y is None else y,
    }
    row.update(overrides)
    cursor = _insert(conn, "card", row)
    conn.commit()
    return cursor.lastrowid


def make_label(conn, board_id=None, x=0.0, y=0.0, text=None):
    """One `label` row on the default board. Returns its id."""
    if board_id is None:
        board_id = db.DEFAULT_BOARD_ID
    cursor = conn.execute(
        "INSERT INTO label (board_id, text, x, y) VALUES (?, ?, ?, ?)",
        (board_id, text if text is not None else _next("label"), x, y),
    )
    conn.commit()
    return cursor.lastrowid


# -- Relinked uris -----------------------------------------------------------


def make_uri_alias(conn, requested_uri, track_id):
    """One `track_uri_alias` row: Spotify served `track_id` for a request for
    `requested_uri`.

    This is what makes `played_uri_track` more than `track.uri`, so any test
    asserting that a read path resolves relinked plays needs a uri here that is
    **not** the track's own -- otherwise a naive `JOIN track ON track.uri`
    would satisfy it too, and the assertion could not fail.
    """
    make_track(conn, track_id)
    conn.execute(
        "INSERT OR REPLACE INTO track_uri_alias (requested_uri, track_id) VALUES (?, ?)",
        (requested_uri, track_id),
    )
    conn.commit()
