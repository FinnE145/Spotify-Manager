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


def test_the_guard_lets_coverage_write_its_own_data_file():
    """`pytest --cov` stores its data in a SQLite file in the repo root, so
    without an exemption the suite passes and then pytest exits INTERNALERROR
    on the report. Found in Verify, 2026-08-21."""
    # source: P2_tests.md §7 -- session 5 is a measured coverage pass, so the
    # guard has to let coverage.py through.
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), os.pardir, ".coverage.probe"))
    conn.close()
    os.remove(os.path.join(os.path.dirname(__file__), os.pardir, ".coverage.probe"))


def test_the_coverage_exemption_does_not_open_a_door_to_the_library():
    """The exemption is by basename, so it can only ever match a file named
    `.coverage*` -- which the library never is. This is the assertion that
    keeps it narrow if anyone ever widens the match."""
    # source: P2_tests.md §4.1 -- the guard's whole purpose survives the
    # exemption added for §7.
    with pytest.raises(RuntimeError, match="Refusing to open a database"):
        sqlite3.connect("symr.db")
    with pytest.raises(RuntimeError, match="Refusing to open a database"):
        sqlite3.connect("/tmp/coverage-but-not-really/symr.db")


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


# -- The suite's own conventions --------------------------------------------


def _test_functions():
    """Every `def test_*` in `tests/`, with the file and line it is on."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent
    for path in sorted(root.glob("test_*.py")):
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                body = "\n".join(lines[node.lineno - 1: node.end_lineno])
                yield path.name, node.lineno, node.name, body


#: What a source line may cite: a spec file, a numbered section, a P1/P2
#: finding, or the literal word `characterization`.
_SOURCE_MARKERS = ("# source:", "characterization")


def test_every_test_declares_where_its_expected_value_came_from():
    """P2's central convention, made mechanical.

    `codebase-health-P.md` §2 requires a one-line source comment on every test
    naming the spec clause it derives from, or `characterization` -- and §2
    says why it is not decoration: it makes review a scan of (assertion, cited
    clause) pairs, and during P3 it is what says at a glance which tests may
    legitimately be regenerated and which must never be.

    A convention that is only checked by eye drifts, and this one had: session
    5's consolidated pass found 32 tests carrying no source line at all. That
    is exactly the kind of thing a test can hold in place for free, so it does.
    """
    # source: codebase-health-P.md §2 -- "Every test carries a one-line source
    # comment"; P2_tests.md §9 item 2 makes it a completion criterion.
    missing = [
        f"{name}:{line} {func}"
        for name, line, func, body in _test_functions()
        if not any(marker in body for marker in _SOURCE_MARKERS)
    ]

    assert missing == [], (
        f"{len(missing)} test(s) with no source comment. Add a one-line "
        "`# source: <spec> §<n> -- <clause>` naming what the expected value "
        "derives from, or the word `characterization` where the expected "
        "value *is* the current behaviour:\n  " + "\n  ".join(missing)
    )


def test_the_source_convention_check_can_actually_fail():
    """The check above is the sort that quietly stops testing anything if its
    own scan breaks -- an `ast.walk` that found no functions, or a glob that
    matched no files, both report zero violations and pass forever.
    """
    # source: P2_tests.md §2 -- "Ship a test that cannot fail" is prohibited;
    # a whole-suite scan asserting an empty list is the shape most at risk of
    # it, so this pins that the scan sees the suite at all.
    found = list(_test_functions())

    assert len(found) > 500, "the scan is not seeing the suite"
    assert any(func == "test_every_test_declares_where_its_expected_value_came_from"
               for _, _, func, _ in found)
    # And a body with no marker is genuinely detected as missing.
    assert not any(marker in "def test_x():\n    assert True" for marker in _SOURCE_MARKERS)
