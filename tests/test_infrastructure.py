"""Tests of the test infrastructure itself.

Everything downstream is built on conftest.py, and a subtle bug there would be
invisible in every test that uses it (P2_tests.md §3). These are the assertions
that make it visible.
"""

import os
import socket
import sqlite3

import pytest

import config
import conftest


# -- The symr.db guard, all three layers (P2_tests.md §4.1) ------------------


def test_db_path_is_the_temp_database():
    # source: P2_tests.md §4.1 step 3 -- the resolved path is hard-checked
    assert os.path.realpath(config.DB_PATH) == os.path.realpath(conftest.DB_PATH)
    assert os.path.realpath(config.DB_PATH).startswith(os.path.realpath(conftest.TMP_DIR))


def test_db_path_is_not_the_real_library():
    # source: P2_tests.md §4.1 -- the whole reason the guard exists. Implied by
    # the test above rather than independent of it, and kept for the same reason
    # conftest keeps the check it mirrors: it names the actual disaster, and it
    # is what stays true if the exact-path check above is ever loosened.
    real = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir, "symr.db"))
    assert os.path.realpath(config.DB_PATH) != real


def test_sqlite_connect_refuses_a_path_outside_the_temp_dir():
    # source: P2_tests.md §4.1 -- layer 3, the guard that does not rely on config
    with pytest.raises(RuntimeError, match="Refusing to open a database"):
        sqlite3.connect("symr.db")


def test_sqlite_connect_allows_the_temp_dir():
    # source: P2_tests.md §4.1 -- the guard must not block the suite's own DB
    conn = sqlite3.connect(os.path.join(conftest.TMP_DIR, "guard-probe.db"))
    conn.close()


def test_credentials_are_dummies_not_the_real_ones():
    # source: P2_tests.md §4.1 step 2 -- load_dotenv() must not have overridden these
    assert config.SPOTIFY_CLIENT_ID == "test-client-id"
    assert config.SPOTIFY_CLIENT_SECRET == "test-client-secret"


# -- No network, ever -------------------------------------------------------


def test_outbound_http_is_blocked():
    # source: P2_tests.md §4.1, extended -- nothing here may reach Spotify
    import requests

    with pytest.raises(RuntimeError, match="Blocked outbound HTTP"):
        requests.get("https://api.spotify.com/v1/me")


def test_outbound_sockets_are_blocked():
    # source: P2_tests.md §4.1, extended -- the floor under the requests guard
    with pytest.raises(RuntimeError, match="Blocked outbound socket"):
        socket.create_connection(("api.spotify.com", 443), timeout=1)


def test_spotify_client_is_unauthenticated_and_makes_no_request():
    # source: P2_tests.md §4.1 -- dummy credentials plus an empty token cache
    # make a real authed client structurally impossible. No network guard is
    # doing the work here: with no cached token there is nothing to refresh.
    from spotify_client import get_spotify_client

    assert get_spotify_client() is None


def test_sqlite_dbapi2_connect_is_guarded_too():
    # source: P2_tests.md §4.1 -- sqlite3.connect and sqlite3.dbapi2.connect are
    # two bindings to one object, so patching one leaves the other open
    import sqlite3.dbapi2

    with pytest.raises(RuntimeError, match="Refusing to open a database"):
        sqlite3.dbapi2.connect("symr.db")


def test_sqlite_connect_is_guarded_via_keyword_too():
    # source: P2_tests.md §4.1 -- connect() takes `database` either way
    with pytest.raises(RuntimeError, match="Refusing to open a database"):
        sqlite3.connect(database="symr.db")


def test_upload_root_is_redirected_away_from_the_real_exports():
    # source: P2_tests.md §4.1, extended -- data/streaming_history/ holds real
    # GDPR exports, and latest_upload() would otherwise find them
    import history_import

    assert os.path.realpath(history_import.UPLOAD_ROOT).startswith(
        os.path.realpath(conftest.TMP_DIR)
    )
