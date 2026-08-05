import hashlib
import re
import sqlite3

from flask import g

from config import DB_PATH

DEFAULT_BOARD_ID = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS board (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshot (
    playlist_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    image_url TEXT,
    owner TEXT,
    track_count INTEGER,
    pulled_at TEXT NOT NULL DEFAULT (datetime('now')),
    snapshot_id TEXT,
    last_changed_at TEXT,
    tracks_pulled_at TEXT,
    unfollowed_at TEXT,
    description TEXT,
    last_pull_error TEXT,
    excluded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artist (
    artist_id    TEXT PRIMARY KEY,
    name         TEXT,
    external_url TEXT,
    raw_json     TEXT
);

CREATE TABLE IF NOT EXISTS album (
    album_id               TEXT PRIMARY KEY,
    name                   TEXT,
    album_type             TEXT,
    release_date           TEXT,
    release_date_precision TEXT,
    release_year           INTEGER,
    release_date_sortable  TEXT,
    total_tracks           INTEGER,
    image_url              TEXT,
    external_url           TEXT,
    raw_json               TEXT
);

CREATE TABLE IF NOT EXISTS track (
    track_id       TEXT PRIMARY KEY,
    name           TEXT,
    artists        TEXT,
    album_id       TEXT REFERENCES album(album_id),
    duration_ms    INTEGER,
    explicit       INTEGER,
    external_url   TEXT,
    uri            TEXT,
    isrc           TEXT,
    track_number   INTEGER,
    disc_number    INTEGER,
    is_playable    INTEGER,
    linked_from    TEXT,
    linked_from_id TEXT,
    raw_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_track_album ON track(album_id);
CREATE INDEX IF NOT EXISTS idx_track_linked_from ON track(linked_from_id);

CREATE TABLE IF NOT EXISTS track_artist (
    track_id  TEXT NOT NULL REFERENCES track(track_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (track_id, artist_id)
);

CREATE TABLE IF NOT EXISTS album_artist (
    album_id  TEXT NOT NULL REFERENCES album(album_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (album_id, artist_id)
);

CREATE INDEX IF NOT EXISTS idx_track_artist_artist ON track_artist(artist_id);
CREATE INDEX IF NOT EXISTS idx_album_artist_artist ON album_artist(artist_id);

CREATE TABLE IF NOT EXISTS membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL REFERENCES snapshot(playlist_id),
    track_id TEXT NOT NULL REFERENCES track(track_id),
    position INTEGER,
    added_at TEXT,
    removed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_membership_playlist ON membership(playlist_id);
CREATE INDEX IF NOT EXISTS idx_membership_track ON membership(track_id);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS card (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id INTEGER NOT NULL REFERENCES board(id),
    entity_type TEXT NOT NULL DEFAULT 'playlist',
    entity_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    image_url TEXT,
    note TEXT NOT NULL DEFAULT '',
    placement TEXT NOT NULL DEFAULT 'tray' CHECK (placement IN ('tray', 'placed')),
    x REAL,
    y REAL,
    UNIQUE (board_id, entity_type, entity_id)
);

CREATE TABLE IF NOT EXISTS label (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id INTEGER NOT NULL REFERENCES board(id),
    text TEXT NOT NULL DEFAULT '',
    x REAL NOT NULL,
    y REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL CHECK (tier IN ('song', 'version', 'recording', 'release')),
    representative_track_id TEXT REFERENCES track(track_id),
    -- ISO-8601 with an explicit Z, matching snapshot._now_iso() and what
    -- static/js/format.js parses. Plain datetime('now') is naive UTC and
    -- renders as a local time, i.e. hours off.
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS track_group (
    track_id TEXT PRIMARY KEY REFERENCES track(track_id),
    song_id INTEGER NOT NULL REFERENCES canonical_group(id),
    version_id INTEGER NOT NULL REFERENCES canonical_group(id),
    recording_id INTEGER NOT NULL REFERENCES canonical_group(id),
    release_id INTEGER NOT NULL REFERENCES canonical_group(id)
);

CREATE INDEX IF NOT EXISTS idx_track_group_song ON track_group(song_id);
CREATE INDEX IF NOT EXISTS idx_track_group_version ON track_group(version_id);
CREATE INDEX IF NOT EXISTS idx_track_group_recording ON track_group(recording_id);
CREATE INDEX IF NOT EXISTS idx_track_group_release ON track_group(release_id);

CREATE TABLE IF NOT EXISTS reviewed_pair (
    track_id_a TEXT NOT NULL REFERENCES track(track_id),
    track_id_b TEXT NOT NULL REFERENCES track(track_id),
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (track_id_a, track_id_b)
);

-- Spotify issues more than one artist id for the same artist. Sparse: only
-- merged artists get a row, pointing at the canonical id. Resolution is
-- always COALESCE(artist_alias.canonical_artist_id, <raw id>).
CREATE TABLE IF NOT EXISTS artist_alias (
    artist_id           TEXT PRIMARY KEY REFERENCES artist(artist_id),
    canonical_artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    decided_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_artist_alias_canonical ON artist_alias(canonical_artist_id);

-- Mirrors reviewed_pair: records that a pair was judged, whatever the
-- verdict, so a decided pair stops resurfacing in the /dev/artists queue.
CREATE TABLE IF NOT EXISTS reviewed_artist_pair (
    artist_id_a TEXT NOT NULL REFERENCES artist(artist_id),
    artist_id_b TEXT NOT NULL REFERENCES artist(artist_id),
    decided_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (artist_id_a, artist_id_b)
);

-- One row per import run of the GDPR extended-streaming-history export
-- (see docs/specs/play-history-C.md). Kept even when the run failed, so
-- the failure stays visible on /dev/import.
CREATE TABLE IF NOT EXISTS play_import (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN ('upload', 'reimport')),
    source        TEXT NOT NULL DEFAULT 'export',
    -- ISO-8601 with an explicit Z, for the same reason as
    -- canonical_group.created_at above: this one is rendered.
    uploaded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    original_name TEXT,
    folder        TEXT NOT NULL,
    files_parsed  INTEGER,
    rows_read     INTEGER,
    rows_inserted INTEGER,
    range_start   TEXT,
    range_end     TEXT,
    error         TEXT
);

-- One row per play. No FK to track and no denormalized track_id: two thirds
-- of the played uris have no track row (foreign to the library), so
-- resolution is a query-time join on play.spotify_track_uri = track.uri,
-- which self-heals as those tracks get imported.
CREATE TABLE IF NOT EXISTS play (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash          TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL DEFAULT 'export',
    import_id         INTEGER REFERENCES play_import(id),
    source_file       TEXT,
    ts                TEXT NOT NULL,
    ms_played         INTEGER NOT NULL,
    spotify_track_uri TEXT NOT NULL,
    -- What the export claimed at play time, not resolved entities: never
    -- join these against track/artist/album names. reported_artist_name is
    -- the *album* artist, so it misses featured credits entirely.
    reported_track_name  TEXT,
    reported_artist_name TEXT,
    reported_album_name  TEXT,
    reason_start      TEXT,
    reason_end        TEXT,
    shuffle           INTEGER,
    skipped           INTEGER,
    platform          TEXT,
    conn_country      TEXT,
    ip_addr           TEXT,
    offline           INTEGER,
    offline_ts        TEXT,
    incognito_mode    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_play_uri ON play(spotify_track_uri);
CREATE INDEX IF NOT EXISTS idx_play_ts ON play(ts);
CREATE INDEX IF NOT EXISTS idx_play_import ON play(import_id);
"""

# Rebuilt whenever the definition here changes (see _ensure_views) rather than
# CREATE ... IF NOT EXISTS, so an edit always takes effect instead of silently
# keeping the old definition.
#
# Wrapped in an explicit transaction: executescript() commits between
# statements, so an unwrapped DROP ... CREATE leaves a window where a
# concurrent reader sees no views at all and fails with "no such table".
# Under SYMR_DEBUG=1 the reloader re-runs init_db on every file save, so
# that window lands on live page loads and on the background pull thread.
VIEWS = """
BEGIN;

DROP VIEW IF EXISTS track_artists;
DROP VIEW IF EXISTS track_artist_role;
DROP VIEW IF EXISTS track_artist_credit;
DROP VIEW IF EXISTS resolved_album_artist;
DROP VIEW IF EXISTS resolved_track_artist;

-- Alias resolution, applied once per join table. Kept as its own view so
-- every downstream join is a plain equi-join: expressing the resolution
-- inline as a correlated subquery instead made a full scan take 17s.
CREATE VIEW resolved_track_artist AS
SELECT ta.track_id, ta.position,
       COALESCE(aa.canonical_artist_id, ta.artist_id) AS artist_id
FROM track_artist ta
LEFT JOIN artist_alias aa ON aa.artist_id = ta.artist_id;

CREATE VIEW resolved_album_artist AS
SELECT ab.album_id,
       COALESCE(aa.canonical_artist_id, ab.artist_id) AS artist_id
FROM album_artist ab
LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id;

-- One row per track credit, resolved, with whether that artist also holds
-- an album credit on the track's own album.
CREATE VIEW track_artist_credit AS
SELECT rta.track_id,
       MIN(rta.position) AS position,
       rta.artist_id,
       ar.name AS name,
       MAX(CASE WHEN raa.artist_id IS NOT NULL THEN 1 ELSE 0 END) AS is_album_artist
FROM resolved_track_artist rta
JOIN track t ON t.track_id = rta.track_id
LEFT JOIN artist ar ON ar.artist_id = rta.artist_id
LEFT JOIN resolved_album_artist raa
       ON raa.album_id = t.album_id AND raa.artist_id = rta.artist_id
-- Grouped so that two credits aliased onto one artist collapse to one row
-- rather than rendering the canonical name twice.
GROUP BY rta.track_id, rta.artist_id;

-- primary = also an album artist, falling back to *every* credit when no
-- credit is one. Without that fallback the 63 tracks on Various Artists
-- compilations would classify their real artist as a feature.
-- The fallback is a join against a per-track aggregate, not a correlated
-- NOT EXISTS: as a subquery it re-materialised the whole credit view once
-- per credit row, turning a 0.1s scan into 19s.
CREATE VIEW track_artist_role AS
SELECT c.track_id, c.position, c.artist_id, c.name,
       CASE WHEN c.is_album_artist = 1 OR h.any_album_artist = 0
            THEN 'primary' ELSE 'featured' END AS role
FROM track_artist_credit c
JOIN (
    SELECT track_id, MAX(is_album_artist) AS any_album_artist
    FROM track_artist_credit
    GROUP BY track_id
) h ON h.track_id = c.track_id;

-- The rendered display string: "Primary A, Primary B (feat. Featured C)".
-- The single read path for artist names -- track.artists is write-only.
--
-- Deliberately built on track_artist_credit rather than track_artist_role,
-- with the primary-artist fallback folded into this one GROUP BY as CASE
-- expressions. Going through the role view instead costs a *second*,
-- ungrouped aggregate over the whole credit table, which a WHERE track_id=?
-- can't filter into -- that made every single-row lookup 44ms and took
-- /dev/canonical (hundreds of such lookups) past 20s to render.
CREATE VIEW track_artists AS
SELECT track_id,
       COALESCE(primary_names, '')
       || CASE WHEN featured_names IS NOT NULL
               THEN ' (feat. ' || featured_names || ')' ELSE '' END AS artists,
       primary_names,
       featured_names
FROM (
    SELECT track_id,
           CASE WHEN MAX(is_album_artist) = 1
                THEN group_concat(CASE WHEN is_album_artist = 1 THEN name END, ', ' ORDER BY position)
                ELSE group_concat(name, ', ' ORDER BY position)
           END AS primary_names,
           CASE WHEN MAX(is_album_artist) = 1
                THEN group_concat(CASE WHEN is_album_artist = 0 THEN name END, ', ' ORDER BY position)
                ELSE NULL
           END AS featured_names
    FROM track_artist_credit
    GROUP BY track_id
);

COMMIT;
"""


_VIEW_NAMES = frozenset(re.findall(r"CREATE VIEW (\w+)", VIEWS))


def _ensure_views(conn):
    """Rebuilds the views only when their definition actually changed.

    The rebuild is the one thing at startup that needs a write lock, and it
    can't take one while the pull thread holds a write transaction: it waits
    out sqlite3's 5s default and then raises "database is locked", which
    under SYMR_DEBUG=1 means a .py save mid-pull fails the reload. Hashing
    VIEWS makes the ordinary restart a pure read, so only a real edit to the
    definitions needs the lock.

    The presence check covers a DB whose hash is current but whose views were
    dropped out from under it -- the hash alone would skip the rebuild.
    """
    digest = hashlib.sha256(VIEWS.encode()).hexdigest()
    row = conn.execute("SELECT value FROM meta WHERE key = 'views_hash'").fetchone()
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
    if row and row[0] == digest and _VIEW_NAMES <= have:
        return
    conn.executescript(VIEWS)
    # Only after a successful rebuild -- a failed one must not record its hash.
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('views_hash', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (digest,),
    )


class Connection(sqlite3.Connection):
    """Plain subclass purely so callers can hang per-connection caches on it
    -- sqlite3.Connection has no __dict__ and rejects attribute assignment.
    See canonical._artist_display."""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, factory=Connection)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def connect():
    """A standalone connection for use outside a Flask request context
    (e.g. the snapshot pull's background thread)."""
    conn = sqlite3.connect(DB_PATH, factory=Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def close_db(e=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # WAL lets the snapshot pull's background-thread writes and page-load
    # reads run concurrently instead of blocking each other (a long pull
    # would otherwise risk "database is locked" on a page load). This is a
    # persistent, database-level setting — applied once here is enough.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _ensure_views(conn)
    conn.execute(
        "INSERT INTO board (id, name) SELECT 1, 'Default' "
        "WHERE NOT EXISTS (SELECT 1 FROM board WHERE id = 1)"
    )
    conn.commit()
    conn.close()


def _migrate(conn):
    """Additive migrations for DBs created before a column existed."""
    card_columns = {row[1] for row in conn.execute("PRAGMA table_info(card)")}
    if "note" not in card_columns:
        conn.execute("ALTER TABLE card ADD COLUMN note TEXT NOT NULL DEFAULT ''")

    snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshot)")}
    for column, ddl in (
        ("snapshot_id", "ALTER TABLE snapshot ADD COLUMN snapshot_id TEXT"),
        ("last_changed_at", "ALTER TABLE snapshot ADD COLUMN last_changed_at TEXT"),
        ("tracks_pulled_at", "ALTER TABLE snapshot ADD COLUMN tracks_pulled_at TEXT"),
        ("unfollowed_at", "ALTER TABLE snapshot ADD COLUMN unfollowed_at TEXT"),
        ("description", "ALTER TABLE snapshot ADD COLUMN description TEXT"),
        ("last_pull_error", "ALTER TABLE snapshot ADD COLUMN last_pull_error TEXT"),
    ):
        if column not in snapshot_columns:
            conn.execute(ddl)

    track_columns = {row[1] for row in conn.execute("PRAGMA table_info(track)")}
    for column, ddl in (
        ("isrc", "ALTER TABLE track ADD COLUMN isrc TEXT"),
    ):
        if column not in track_columns:
            conn.execute(ddl)

    # These two were briefly written as naive UTC ("2026-07-30 13:34:51"),
    # which the front-end parsed as local time and rendered hours off. Rewrite
    # them in the ISO-8601-with-Z form everything else uses. Idempotent: rows
    # already carrying a Z are skipped.
    for table, column in (("reviewed_pair", "decided_at"), ("canonical_group", "created_at")):
        conn.execute(
            f"UPDATE {table} SET {column} = replace({column}, ' ', 'T') || 'Z' "
            f"WHERE {column} IS NOT NULL AND {column} NOT LIKE '%Z'"
        )
