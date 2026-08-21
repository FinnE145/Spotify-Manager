"""Tests of the fixtures in conftest.py.

Separate from test_infrastructure.py, which covers the guards. These cover the
machinery every other test in the suite sits on: a fresh database, a reset
module state, a pinned clock, and a working app.
"""

import os
import threading

import pytest

import conftest
import jobs
import scoring


# -- A fresh database per test (P2_tests.md §4.2) ---------------------------


def test_conn_has_the_full_schema(conn):
    # source: P2_tests.md §4.2 -- db.init_db() builds schema plus views
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }
    assert {"track", "album", "artist", "membership", "snapshot", "score"} <= names
    assert {"played_uri_track", "track_artists", "resolved_track_artist"} <= names


def test_a_first_test_can_write_rows(conn):
    # source: characterization -- paired with the next test; the two together
    # are the isolation assertion, and they only mean anything in file order
    conn.execute("INSERT INTO artist (artist_id, name) VALUES ('artist-leak', 'Leaky')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 1


def test_b_the_next_test_sees_an_empty_database(conn):
    # source: P2_tests.md §4.2 -- the previous test's row must not survive
    assert conn.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 0


# -- Module state reset -----------------------------------------------------


def test_job_slot_starts_unclaimed():
    # source: P2_tests.md §4.5 -- a leaked slot fails an unrelated later test
    assert jobs.active() is None
    assert jobs.stop_requested() is False


def test_a_first_test_can_dirty_a_job_status():
    # source: characterization -- paired with the next test, as above. Dirties
    # a JobStatus rather than the job slot on purpose: leaving the *slot* set
    # is what the teardown guard exists to catch, so a test that did it would
    # (correctly) fail rather than demonstrate the reset.
    import snapshot

    snapshot._status.set(phase="working")
    snapshot._status.log("something happened")
    assert snapshot._status.get()["phase"] == "working"


def test_b_the_next_test_gets_a_clean_job_status():
    # source: P2_tests.md §4.5
    import snapshot

    assert snapshot._status.get()["phase"] is None
    assert snapshot._status.get()["log"] == []


def test_scoring_globals_start_pristine():
    # source: P2_tests.md §4.1/§4.5 -- _checker_conn in particular is an open
    # handle to the database file and would outlive the per-test wipe
    assert scoring._checker_conn is None
    assert scoring._last_data_version is None
    assert scoring._failed_fingerprint is None
    assert scoring._worker_alive is False
    assert scoring.recompute_status()["outcome"] is None


def test_job_status_singletons_are_reset():
    # source: P2_tests.md §4.5 -- four JobStatus objects live at module scope
    import snapshot

    assert snapshot._status.get()["phase"] is None
    assert snapshot._status.get()["log"] == []


# -- The clock (P2_tests.md §4.3) -------------------------------------------


def test_now_is_frozen():
    # source: P2_tests.md §4.3 -- jobs.now_iso() feeds most written timestamps
    assert jobs.now_iso() == "2026-06-15T12:00:00Z"


def test_the_clock_can_be_moved_deliberately(freezer):
    # source: P2_tests.md §4.3 -- the escape hatch for ordering assertions
    before = jobs.now_iso()
    freezer.tick(60)
    assert jobs.now_iso() != before
    assert jobs.now_iso() == "2026-06-15T12:01:00Z"


def test_sqlite_datetime_now_is_not_frozen(conn):
    # source: P2_tests.md §4.3 -- the stated known limit, pinned so that if
    # freezegun ever does reach SQLite, the note saying it does not is caught
    sql_now = conn.execute("SELECT datetime('now')").fetchone()[0]
    assert not sql_now.startswith("2026-06-15 12:00:00")


# -- The recompute recorder -------------------------------------------------


def test_request_recompute_is_recorded_not_spawned(recompute_calls):
    # source: P2_tests.md §4.5 + async-recompute-N §4.1 -- the async sites are
    # assertable as calls rather than as threads
    assert recompute_calls == []
    scoring.request_recompute()
    assert len(recompute_calls) == 1
    assert scoring._worker_alive is False


# -- The settle guard -------------------------------------------------------


def test_settle_guard_returns_true_when_idle():
    # source: P2_tests.md §4.5
    assert conftest._background_threads_settled() is True


def test_settle_guard_returns_false_while_a_job_holds_the_slot(monkeypatch):
    # source: P2_tests.md §4.5 -- the negative case, with the spin shortened so
    # proving it costs 30ms rather than 2s
    monkeypatch.setattr(conftest, "_SETTLE_ATTEMPTS", 3)
    jobs._active = "snapshot"
    try:
        assert conftest._background_threads_settled() is False
    finally:
        jobs._active = None


def test_settle_guard_waits_for_a_real_thread_to_finish():
    # source: P2_tests.md §4.5 -- the case it exists for: a job thread still
    # running when the test body ends
    release = threading.Event()

    def slow_job():
        release.wait(timeout=5)

    assert jobs.try_start("snapshot", slow_job) is True
    assert jobs.active() == "snapshot"
    release.set()
    assert conftest._background_threads_settled() is True
    assert jobs.active() is None


# -- The app (P2_tests.md §4.6) ---------------------------------------------


def test_app_is_built_with_the_login_guard_satisfied(client):
    # source: P2_tests.md §4.6 -- auth bypassed by patching app.get_spotify_client
    response = client.get("/")
    assert response.status_code == 200


def test_app_does_not_set_testing_so_error_handlers_run(app, client):
    # source: P2_tests.md §4.6 / P1-014 -- TESTING=True would propagate the
    # exception past app.py's handlers and the /api/* JSON shape would never
    # be produced, so the tests that cover it could not exist
    assert app.config["TESTING"] is False
    response = client.get("/api/canonical/group/does-not-exist")
    assert response.status_code == 404
    assert response.is_json


def test_unauthenticated_app_redirects_to_login(monkeypatch):
    # source: P1-014 -- the guard itself still works when nothing is patched
    import app as app_module

    monkeypatch.setattr(app_module, "get_spotify_client", lambda: None)
    unauthed = app_module.create_app().test_client()
    assert unauthed.get("/").status_code == 302


def test_the_app_uses_the_test_database(client):
    # source: P2_tests.md §4.1 -- create_app() calls db.init_db(), and this is
    # the assertion that it did so against the temp path and not symr.db
    import config

    assert conftest.TMP_DIR in config.DB_PATH


@pytest.mark.parametrize("path", ["/", "/canvas", "/dev", "/search"])
def test_a_few_pages_render_on_an_empty_database(client, path):
    # source: characterization -- an empty fixture DB must not 500; this is the
    # floor the session-4 route sweep builds on
    assert client.get(path).status_code == 200


# -- Guards against the infrastructure silently going stale ------------------
#
# Two lists in conftest.py are enumerations of the codebase, and an enumeration
# that is not checked drifts the moment someone adds the next one. Both failures
# are silent by nature: a module missing from the first quietly receives the real
# client (which returns None, so the code under test no-ops rather than errors),
# and a job missing from the second leaks its status into the following test.


def _project_modules():
    """Every top-level project module. Not tests/ (its own fakes are not the
    subject) and not scripts/, which conftest never imports."""
    root = os.path.join(os.path.dirname(__file__), os.pardir)
    return sorted(
        name[:-3]
        for name in os.listdir(root)
        if name.endswith(".py") and name != "config.py"
    )


def test_every_module_importing_get_spotify_client_is_patched():
    # source: characterization -- derived from the source rather than restated,
    # so a sixth importer fails here instead of silently getting a real client
    root = os.path.join(os.path.dirname(__file__), os.pardir)
    importers = set()
    for name in _project_modules():
        with open(os.path.join(root, name + ".py")) as handle:
            source = handle.read()
        if "from spotify_client import" in source and "get_spotify_client" in source:
            importers.add(name)

    patched = {module.__name__ for module in conftest._SPOTIFY_CLIENT_IMPORTERS}
    assert importers == patched, (
        "conftest._SPOTIFY_CLIENT_IMPORTERS is out of date with the codebase: "
        f"unpatched={importers - patched}, no longer importing={patched - importers}"
    )


def test_every_job_status_singleton_is_reset_between_tests():
    # source: P2_tests.md §4.5 -- four JobStatus objects live at module scope
    # today; a fifth job would leak its status into the next test
    root = os.path.join(os.path.dirname(__file__), os.pardir)
    declaring = set()
    for name in _project_modules():
        with open(os.path.join(root, name + ".py")) as handle:
            if "jobs.JobStatus(" in handle.read():
                declaring.add(name)

    reset = {status._name for status in conftest._JOB_STATUSES}
    # JobStatus's own name is the job name, which matches its module for all
    # four today -- the mapping this asserts is exactly that correspondence.
    assert declaring == reset, (
        "conftest._JOB_STATUSES is out of date with the codebase: "
        f"declared but not reset={declaring - reset}, reset but not declared={reset - declaring}"
    )
