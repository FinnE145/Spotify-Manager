"""`db._migrate` and `db._ensure_views` -- the two functions with the power to
damage `symr.db`.

`P2_tests.md` §5 puts them in scope for exactly that reason. A temp database
always gets the fresh schema, so **these paths never execute in any other
test** -- a migration only runs when a database predates a column, which means
the only way to cover them is to build an old schema on purpose and migrate it.

Every test here builds its own database file under conftest's temp directory
(the `sqlite3.connect` guard refuses anything else) and drives the same three
steps `init_db()` does: the old shape, then `SCHEMA` -- whose every statement
is `IF NOT EXISTS`, so it fills in missing tables and leaves existing ones at
their old shape -- then `_migrate`.
"""

import os
import sqlite3

import pytest

import conftest
import db

# The `snapshot` table as it stood before partial-pulls-J added the one column
# that means "the stored items are current". Deliberately verbatim-old rather
# than SCHEMA-minus-a-line: that is what a real old database holds.
LEGACY_SNAPSHOT = """
CREATE TABLE snapshot (
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
    excluded INTEGER NOT NULL DEFAULT 0,
    generation_declined INTEGER NOT NULL DEFAULT 0
);
"""

# `roundtrip_failed_uri` briefly stored a free-text `reason` that was also
# matched on as control flow.
LEGACY_FAILED_URI = """
CREATE TABLE roundtrip_failed_uri (
    requested_uri TEXT PRIMARY KEY,
    reason        TEXT,
    failed_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# `wanted_uri` before M added the album_id that makes the settled/missing
# arithmetic plain SQL instead of a per-page scan of every tracklist_json.
LEGACY_WANTED_URI = """
CREATE TABLE wanted_uri (
    uri          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


@pytest.fixture
def legacy():
    """Opens a second database file, for building old schemas in.

    Under conftest's TMP_DIR because the `sqlite3.connect` guard refuses every
    other path -- which is the point of that guard, and the reason this cannot
    quietly become a test against the real file.
    """
    path = os.path.join(conftest.TMP_DIR, "legacy.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def upgrade(conn):
    """`init_db()`'s middle two steps: fill in what's missing, then migrate."""
    conn.executescript(db.SCHEMA)
    db._migrate(conn)
    conn.commit()


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# -- Additive column migrations ---------------------------------------------


def test_the_capture_id_column_is_added_to_an_old_snapshot_table(legacy):
    # source: partial-pulls-J.md §2.9 -- the migration that introduces
    # tracks_pulled_snapshot_id.
    legacy.executescript(LEGACY_SNAPSHOT)
    assert "tracks_pulled_snapshot_id" not in columns(legacy, "snapshot")

    upgrade(legacy)

    assert "tracks_pulled_snapshot_id" in columns(legacy, "snapshot")


def test_a_captured_playlist_is_seeded_as_already_current(legacy):
    """The migration's substantive half, and the one with a visible cost if it
    is wrong: without it the first refresh after J shipped would have found
    every playlist stale and re-read the entire library."""
    # source: partial-pulls-J.md §2.9 -- "Asserts exactly what today's refresh
    # logic already believes -- that a stored snapshot_id matching Spotify's
    # means the stored items are current."
    legacy.executescript(LEGACY_SNAPSHOT)
    legacy.execute(
        "INSERT INTO snapshot (playlist_id, name, snapshot_id, tracks_pulled_at) "
        "VALUES ('p1', 'Captured', 'snap-1', '2026-01-01T00:00:00Z')"
    )
    legacy.commit()

    upgrade(legacy)

    row = legacy.execute(
        "SELECT tracks_pulled_snapshot_id FROM snapshot WHERE playlist_id = 'p1'"
    ).fetchone()
    assert row["tracks_pulled_snapshot_id"] == "snap-1"


def test_a_never_captured_playlist_is_left_null_by_the_seed(legacy):
    """The seed is gated on `tracks_pulled_at IS NOT NULL`. A playlist whose
    items were never read must stay NULL, or the migration would claim a
    capture that never happened and suppress its first pull."""
    # source: partial-pulls-J.md §2.9 -- the UPDATE's own WHERE clause.
    legacy.executescript(LEGACY_SNAPSHOT)
    legacy.execute(
        "INSERT INTO snapshot (playlist_id, name, snapshot_id) VALUES ('p1', 'Fresh', 'snap-1')"
    )
    legacy.commit()

    upgrade(legacy)

    row = legacy.execute(
        "SELECT tracks_pulled_snapshot_id FROM snapshot WHERE playlist_id = 'p1'"
    ).fetchone()
    assert row["tracks_pulled_snapshot_id"] is None


def test_an_album_id_is_added_to_wanted_uri_and_backfilled_from_the_tracklist(legacy):
    """The backfill matches on uri -- the only thing the two sides share."""
    # source: grouping-fixes-backfill-M.md §4.3 -- album_id is what lets the
    # settled/missing arithmetic be plain SQL.
    legacy.executescript(LEGACY_WANTED_URI)
    legacy.executescript(db.SCHEMA)
    legacy.execute(
        "INSERT INTO album (album_id, name, tracklist_json) VALUES ('al1', 'A', ?)",
        ('[{"uri": "spotify:track:t1"}, {"uri": "spotify:track:t2"}]',),
    )
    legacy.execute("INSERT INTO wanted_uri (uri, source) VALUES ('spotify:track:t1', 'album')")
    legacy.execute("INSERT INTO wanted_uri (uri, source) VALUES ('spotify:track:zz', 'album')")
    legacy.commit()

    db._migrate(legacy)
    legacy.commit()

    rows = dict(legacy.execute("SELECT uri, album_id FROM wanted_uri").fetchall())
    assert rows["spotify:track:t1"] == "al1"
    # A uri belonging to no stored tracklist has nothing to match against and
    # is left alone rather than guessed at.
    assert rows["spotify:track:zz"] is None


# -- The `roundtrip_failed_uri` rebuild -------------------------------------


def test_an_empty_legacy_failed_uri_table_is_rebuilt_with_the_state_enum(legacy):
    """Recreated rather than altered, and only ever while empty -- which it is
    in every database that has the old shape."""
    # source: db._migrate -- the free-text `reason` was matched on as control
    # flow, so the replacement is a CHECK-constrained slug enum.
    legacy.executescript(LEGACY_FAILED_URI)

    upgrade(legacy)

    assert columns(legacy, "roundtrip_failed_uri") == {
        "requested_uri", "state", "detail", "failed_at"
    }


def test_a_non_empty_legacy_failed_uri_table_refuses_to_migrate(legacy):
    """Losing rows silently is the failure this raise exists to prevent."""
    # source: db._migrate -- "migrate it by hand rather than losing rows".
    legacy.executescript(LEGACY_FAILED_URI)
    legacy.execute(
        "INSERT INTO roundtrip_failed_uri (requested_uri, reason) "
        "VALUES ('spotify:track:x', 'not returned')"
    )
    legacy.commit()

    with pytest.raises(RuntimeError, match="migrate it by hand"):
        upgrade(legacy)

    # And the rows are still there -- it refused before touching anything.
    assert legacy.execute("SELECT COUNT(*) FROM roundtrip_failed_uri").fetchone()[0] == 1


# -- The naive-UTC timestamp rewrite ----------------------------------------


def test_naive_utc_timestamps_are_rewritten_with_a_z_suffix(legacy):
    """Two columns were briefly written as `2026-07-30 13:34:51`, which the
    front end parsed as local time and rendered hours off."""
    # source: db._migrate -- rewrite them in the ISO-8601-with-Z form
    # everything else uses; format.js parses the Z form (db.py:162).
    legacy.executescript(db.SCHEMA)
    legacy.execute("INSERT INTO track (track_id, name) VALUES ('t1', 'A')")
    legacy.execute("INSERT INTO track (track_id, name) VALUES ('t2', 'B')")
    legacy.execute(
        "INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at) "
        "VALUES ('t1', 't2', '2026-07-30 13:34:51')"
    )
    legacy.commit()

    db._migrate(legacy)
    legacy.commit()

    assert legacy.execute("SELECT decided_at FROM reviewed_pair").fetchone()[0] == (
        "2026-07-30T13:34:51Z"
    )


def test_the_timestamp_rewrite_leaves_rows_that_already_carry_a_z(legacy):
    """Idempotent by its own WHERE clause -- without that guard a second run
    would append a second Z."""
    # source: db._migrate -- "Idempotent: rows already carrying a Z are
    # skipped."
    legacy.executescript(db.SCHEMA)
    legacy.execute("INSERT INTO track (track_id, name) VALUES ('t1', 'A')")
    legacy.execute("INSERT INTO track (track_id, name) VALUES ('t2', 'B')")
    legacy.execute(
        "INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at) "
        "VALUES ('t1', 't2', '2026-07-30T13:34:51Z')"
    )
    legacy.commit()

    db._migrate(legacy)
    db._migrate(legacy)
    legacy.commit()

    assert legacy.execute("SELECT decided_at FROM reviewed_pair").fetchone()[0] == (
        "2026-07-30T13:34:51Z"
    )


# -- Idempotence ------------------------------------------------------------


def test_migrating_an_already_current_database_changes_nothing(legacy):
    """The ordinary case: every startup runs `_migrate` against a database
    that needs none of it."""
    # source: db._migrate's docstring -- "Additive migrations for DBs created
    # before a column existed"; characterization of the no-op path.
    legacy.executescript(LEGACY_SNAPSHOT)
    upgrade(legacy)
    before = {
        table: columns(legacy, table)
        for table in ("snapshot", "track", "album", "artist", "wanted_uri", "canonical_group")
    }

    db._migrate(legacy)
    legacy.commit()

    assert {
        table: columns(legacy, table)
        for table in ("snapshot", "track", "album", "artist", "wanted_uri", "canonical_group")
    } == before


# -- `_ensure_views` --------------------------------------------------------


def recording_executescript(conn):
    """Counts rebuilds. `db.Connection` is a plain subclass that exists purely
    so callers can hang attributes on it -- `sqlite3.Connection` has no
    `__dict__` -- which is what makes this patchable at all."""
    calls = []
    real = conn.executescript

    def wrapped(sql):
        calls.append(sql)
        return real(sql)

    conn.executescript = wrapped
    return calls


def test_an_unchanged_definition_does_not_rebuild_the_views(conn):
    """The rebuild is the one startup step needing a write lock, and it cannot
    take one while a pull holds a write transaction -- so an ordinary restart
    has to stay a pure read."""
    # source: db._ensure_views -- "Hashing VIEWS makes the ordinary restart a
    # pure read, so only a real edit to the definitions needs the lock."
    calls = recording_executescript(conn)

    db._ensure_views(conn)

    assert calls == []


def test_a_dropped_view_is_rebuilt_even_though_the_hash_matches(conn):
    """The presence check. The hash alone would skip the rebuild of a database
    whose views were dropped out from under it."""
    # source: db._ensure_views -- "The presence check covers a DB whose hash is
    # current but whose views were dropped out from under it."
    conn.execute("DROP VIEW played_uri_track")
    calls = recording_executescript(conn)

    db._ensure_views(conn)

    assert calls == [db.VIEWS]
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view' AND name = 'played_uri_track'"
    ).fetchone()[0] == 1


def test_a_changed_definition_rebuilds_and_records_the_new_hash(conn):
    """An edit to VIEWS always takes effect -- that is what the stored hash is
    compared against."""
    # source: db._ensure_views -- the hash is of VIEWS itself, so any edit
    # misses and forces the rebuild.
    db.set_meta(conn, "views_hash", "a-hash-from-an-older-definition")
    conn.commit()
    calls = recording_executescript(conn)

    db._ensure_views(conn)

    assert calls == [db.VIEWS]
    assert db.get_meta(conn, "views_hash") != "a-hash-from-an-older-definition"


def test_every_declared_view_exists_after_init(conn):
    """`_VIEW_NAMES` is parsed out of VIEWS itself, so this asserts the
    definitions and the database agree rather than restating a list."""
    # source: db._ensure_views -- `_VIEW_NAMES <= have` is the presence check
    # it makes on every startup.
    have = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    }
    assert db._VIEW_NAMES <= have
