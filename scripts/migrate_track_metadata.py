"""One-time migration for roadmap step A (docs/specs/track-metadata-A.md).

Brings an existing symr.db to the track-metadata schema:

  - adds the artist / album / track_artist / album_artist tables
  - adds snapshot.excluded
  - backfills album from the album columns currently living on track
  - rebuilds track: adds uri, track_number, disc_number, is_playable,
    linked_from, linked_from_id, raw_json; drops popularity, preview_url,
    album_name, album_image_url; puts a real foreign key on album_id

Deliberately NOT wired into db._migrate(): nothing destructive should fire
behind an app start. Run it by hand, once:

    venv/bin/python scripts/migrate_track_metadata.py

Safe to re-run — it detects an already-migrated DB and exits. Backs the
database up to symr.db.bak-<timestamp> before touching anything. Reads no
Spotify credentials and makes no API calls.

The track rebuild follows SQLite's documented table-rebuild procedure
("Making Other Kinds Of Table Schema Changes"): foreign keys off *before*
BEGIN, create/copy/drop/rename inside the transaction, foreign_key_check
before COMMIT. membership and track_group both carry REFERENCES
track(track_id), so doing this with foreign keys enforced risks mangling
them.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.environ.get("SYMR_DB_PATH", "symr.db")

# Kept as individual statements, not one executescript() blob: executescript()
# issues an implicit COMMIT before it runs, which would silently end the
# transaction this migration depends on.
NEW_TABLES = (
    """
CREATE TABLE IF NOT EXISTS artist (
    artist_id    TEXT PRIMARY KEY,
    name         TEXT,
    external_url TEXT,
    raw_json     TEXT
)
""",
    """
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
)
""",
    """
CREATE TABLE IF NOT EXISTS track_artist (
    track_id  TEXT NOT NULL REFERENCES track(track_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (track_id, artist_id)
)
""",
    """
CREATE TABLE IF NOT EXISTS album_artist (
    album_id  TEXT NOT NULL REFERENCES album(album_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (album_id, artist_id)
)
""",
    "CREATE INDEX IF NOT EXISTS idx_track_artist_artist ON track_artist(artist_id)",
    "CREATE INDEX IF NOT EXISTS idx_album_artist_artist ON album_artist(artist_id)",
)

TRACK_NEW = """
CREATE TABLE track_new (
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
)
"""


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def main():
    if not os.path.exists(DB_PATH):
        sys.exit(f"No database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None  # explicit BEGIN/COMMIT below

    if "raw_json" in columns(conn, "track"):
        print("Already applied — track.raw_json exists. Nothing to do.")
        conn.close()
        return

    # Fold the WAL into the main file so a plain file copy is a complete backup.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    backup = f"{DB_PATH}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB_PATH, backup)
    print(f"Backed up to {backup}")

    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None
    # Must be set outside a transaction — the pragma is a no-op inside one.
    conn.execute("PRAGMA foreign_keys=OFF")

    try:
        conn.execute("BEGIN")

        for statement in NEW_TABLES:
            conn.execute(statement)

        if "excluded" not in columns(conn, "snapshot"):
            conn.execute(
                "ALTER TABLE snapshot ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0"
            )

        # Must precede the track rebuild: dropping track.album_name while album
        # is empty would blank every album name in the UI and empty
        # canonical_detect's album_norm until the full re-pull lands.
        # GROUP BY (not DISTINCT) so a single album_id can only ever yield one
        # row, whatever drift exists in the denormalized name/image columns.
        conn.execute(
            """
            INSERT INTO album (album_id, name, image_url)
            SELECT album_id, album_name, album_image_url
            FROM track WHERE album_id IS NOT NULL
            GROUP BY album_id
            """
        )

        conn.execute(TRACK_NEW)
        conn.execute(
            """
            INSERT INTO track_new (track_id, name, artists, album_id, duration_ms,
                                   explicit, external_url, uri, isrc)
            SELECT track_id, name, artists, album_id, duration_ms,
                   explicit, external_url, 'spotify:track:' || track_id, isrc
            FROM track
            """
        )
        conn.execute("DROP TABLE track")
        conn.execute("ALTER TABLE track_new RENAME TO track")
        conn.execute("CREATE INDEX idx_track_album ON track(album_id)")
        conn.execute("CREATE INDEX idx_track_linked_from ON track(linked_from_id)")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            conn.execute("ROLLBACK")
            sys.exit(f"Foreign key check failed, rolled back: {violations[:5]}")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("track", "album", "artist", "track_artist", "album_artist", "membership")
    }
    print("Migration applied.")
    for name, n in counts.items():
        print(f"  {name:<14} {n}")
    print(f"\nartist / track_artist / album_artist stay empty until the full re-pull.")
    print(f"Backup: {backup}")
    conn.close()


if __name__ == "__main__":
    main()
