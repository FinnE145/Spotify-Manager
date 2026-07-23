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
    pulled_at TEXT NOT NULL DEFAULT (datetime('now'))
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
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
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
