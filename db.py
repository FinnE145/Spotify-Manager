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
    last_pull_error TEXT
);

CREATE TABLE IF NOT EXISTS track (
    track_id TEXT PRIMARY KEY,
    name TEXT,
    artists TEXT,
    album_id TEXT,
    album_name TEXT,
    duration_ms INTEGER,
    explicit INTEGER,
    popularity INTEGER,
    preview_url TEXT,
    external_url TEXT,
    isrc TEXT,
    album_image_url TEXT
);

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
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def connect():
    """A standalone connection for use outside a Flask request context
    (e.g. the snapshot pull's background thread)."""
    conn = sqlite3.connect(DB_PATH)
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
        ("album_image_url", "ALTER TABLE track ADD COLUMN album_image_url TEXT"),
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
