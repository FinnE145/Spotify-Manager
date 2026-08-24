"""Test infrastructure for the whole suite (docs/codebase-health/P2_tests.md §4).

**Everything above the first project import is load-bearing and order-dependent.**
`db.py` and `scoring.py` both do `from config import DB_PATH` -- a *from-import*, so
the path is bound once, at import time. Setting SYMR_DB_PATH after importing any
project module is a silent no-op and the suite runs against the real 93 MB
`symr.db`: seven years of streaming history, 461+ hand-reviewed grouping pairs, 37
generations of curation, none of it reconstructible and none of it re-suppliable by
Spotify. Nothing here may be reordered or moved into a fixture.

Three independent layers stand between this suite and that file, deliberately
(P2_tests.md §4.1 -- this is the security-grade part of P, not the KISS part):

  1. the environment is redirected before any project import, below;
  2. the resolved `config.DB_PATH` is hard-checked and the suite refuses to run
     if it is not the path we just set;
  3. `sqlite3.connect` itself rejects any path outside the temp directory, which
     catches a caller that never consults `config.DB_PATH` at all -- every
     script in `scripts/` opens its own connection that way.

Layer 2 is not redundant with layer 1: layer 1 is the mechanism, layer 2 is what
catches the day someone changes the mechanism. Layer 3 is what catches the code
that never used the mechanism.
"""

import os
import shutil
import sqlite3
import sqlite3.dbapi2
import tempfile

# ---------------------------------------------------------------- layer 1: redirect

# One temp directory for the whole run, torn down in pytest_sessionfinish. It has
# to be created here rather than from pytest's tmp_path_factory because
# DB_PATH is bound at import time (see the module docstring), which means the
# database path cannot change between tests -- per-test isolation is a wipe of
# the file at this one path, never a fresh path. See the `db` fixture.
TMP_DIR = tempfile.mkdtemp(prefix="symr-tests-")

DB_PATH = os.path.join(TMP_DIR, "test.db")

os.environ["SYMR_DB_PATH"] = DB_PATH

# config.py:8-10 reads all three of these at import and raises KeyError without
# them (verified 2026-08-19) -- missing the third fails exactly as hard as
# missing the first. Dummies additionally make it structurally impossible for a
# test to build a real authed client, and python-dotenv's load_dotenv() does not
# override an already-set variable, so these shadow the real credentials in
# `.env` rather than being overwritten by them.
os.environ["SPOTIFY_CLIENT_ID"] = "test-client-id"
os.environ["SPOTIFY_CLIENT_SECRET"] = "test-client-secret"
os.environ["SPOTIFY_REDIRECT_URI"] = "http://localhost:45660/callback"

# Not on §4.1's list, added here for the same reason as the three above: the
# repo's real `.spotipy_cache` holds a live token, and get_spotify_client()
# reads it through this path before deciding whether to refresh. Pointed at a
# file that does not exist, it returns None with no network call at all.
os.environ["SYMR_SPOTIPY_CACHE"] = os.path.join(TMP_DIR, "spotipy-cache")

# Required, not optional: config.py has no fallback for this and raises KeyError
# without it (host-on-fe-pro-Q.md §5.1), so missing it fails collection of the
# whole suite exactly as missing a Spotify credential does. Pinned to a fixed
# value rather than a random one so a test can construct two apps and have one
# read the other's session.
os.environ["SYMR_SECRET_KEY"] = "test-secret-key"

# Only app.py's __main__ block reads APP_DEBUG today, so this is inert -- pinned
# anyway so a test run can never inherit the developer's `.env` setting if a
# create_app() branch on it ever appears.
os.environ["SYMR_DEBUG"] = "0"

# ---------------------------------------------------------------- layer 3: sqlite3

_real_sqlite3_connect = sqlite3.connect


def _is_coverage_db(resolved):
    """coverage.py's own data file, which is a SQLite database it writes into
    the repo root -- so `pytest --cov` dies on the guard below without this.

    Narrow on purpose, and safe for the one reason that matters: the exemption
    is by *basename*, and no Symr database is ever named `.coverage`. The real
    file is `symr.db`; coverage's is `.coverage` or, under parallel runs,
    `.coverage.<host>.<pid>.<random>`. Nothing that starts with `.coverage`
    can be the library.

    This is not an optional convenience. `P2_tests.md` §7 makes session 5 a
    measured coverage pass, and without this the guard aborts the report --
    the suite passes, then pytest exits INTERNALERROR.
    """
    return os.path.basename(resolved).startswith(".coverage")


def _guarded_connect(*args, **kwargs):
    """Refuses to open any database outside TMP_DIR, bar coverage's own file.

    The layer that does not depend on a caller having consulted config.DB_PATH:
    every script in `scripts/` hardcodes its own path, and a future test that
    imports one would otherwise open the real file. `:memory:` is allowed
    because it cannot touch the disk.

    Takes *args rather than a named first parameter because sqlite3.connect
    accepts `database` positionally or by keyword, and a guard that raises
    TypeError on the keyword form would read as an unrelated bug.
    """
    database = args[0] if args else kwargs.get("database")
    if database != ":memory:":
        resolved = os.path.realpath(os.fspath(database))
        if not resolved.startswith(os.path.realpath(TMP_DIR) + os.sep) and not _is_coverage_db(
            resolved
        ):
            raise RuntimeError(
                f"Refusing to open a database outside the test temp directory.\n"
                f"  asked for: {resolved}\n"
                f"  allowed under: {os.path.realpath(TMP_DIR)}\n"
                "This guard exists so the suite can never touch the real symr.db."
            )
    return _real_sqlite3_connect(*args, **kwargs)


# Both names, deliberately: `sqlite3/__init__.py` does `from sqlite3.dbapi2
# import *`, so the two start as one object but are two separate bindings --
# patching only `sqlite3.connect` leaves `from sqlite3.dbapi2 import connect`
# as an open door straight to the real file.
sqlite3.connect = _guarded_connect
sqlite3.dbapi2.connect = _guarded_connect

# ---------------------------------------------------------------- no network, ever

# Nothing in this suite may make a real outbound request. spotipy talks to
# Spotify through requests, and every requests verb funnels through one
# HTTPAdapter.send -- so that is the precise place to stop it, and the place
# whose error message names the problem. The socket guard underneath is the
# floor for anything that does not go through requests at all
# (roundtrip._probe builds its own bare Session; a future caller might not use
# requests). A test that needs HTTP-shaped behaviour fakes it above this
# level -- at `sp` (see fakes.py) or at the function under test.
import socket  # noqa: E402  -- after the env block by design; see module docstring

import requests.adapters  # noqa: E402


def _no_network_send(self, request, *args, **kwargs):
    raise RuntimeError(
        f"Blocked outbound HTTP in tests: {request.method} {request.url}\n"
        "Fake at the `sp` object or the function under test instead."
    )


requests.adapters.HTTPAdapter.send = _no_network_send

_real_socket_connect = socket.socket.connect


def _no_socket_connect(self, address):
    raise RuntimeError(f"Blocked outbound socket connection in tests: {address!r}")


socket.socket.connect = _no_socket_connect

# ---------------------------------------------------------------- layer 2: verify

import config  # noqa: E402  -- must follow the env block above, not precede it

# An explicit raise, never `assert`: assert statements are stripped under
# `python -O`, and a guard that silently disappears under an interpreter flag
# is not a guard.
#
# The first check is the live one, and it is exact: DB_PATH is a path inside a
# freshly-created mkdtemp, so anything else at all fails here. That makes the
# second check **unreachable today** -- if config.DB_PATH could be `symr.db`,
# the first would already have raised. It is kept anyway, and not as decoration:
# the first check is the kind that gets loosened (to "anywhere under TMP_DIR",
# say, the day a test wants a second database), and the moment it is, the second
# becomes the live one. Same argument as layer 2 against layer 1, one level down.
if os.path.realpath(config.DB_PATH) != os.path.realpath(DB_PATH):
    raise RuntimeError(
        "REFUSING TO RUN: config.DB_PATH is not the test database.\n"
        f"  resolved to: {os.path.realpath(config.DB_PATH)}\n"
        f"  expected:    {os.path.realpath(DB_PATH)}\n"
        "Something imported a project module before conftest.py set SYMR_DB_PATH."
    )

_REAL_DB = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir, "symr.db"))
if os.path.realpath(config.DB_PATH) == _REAL_DB:
    raise RuntimeError(f"REFUSING TO RUN: config.DB_PATH is the real library at {_REAL_DB}.")

# ---------------------------------------------------------------- the other real path

# `history_import.UPLOAD_ROOT` binds from `config.UPLOAD_ROOT` at import time
# (its own default, unset, resolves to the repo's own `data/streaming_history/`
# -- which holds Finn's real GDPR exports), but this rebinds the module
# attribute directly rather than via SYMR_UPLOAD_ROOT so it doesn't also need
# setting before the env block above. An import test left unredirected would
# both write new folders there and let `latest_upload()` find and reimport
# seven years of real history. Redirected at import time rather than by a
# fixture for the same reason as everything above: a fixture is a thing a
# test can forget to request.
import history_import  # noqa: E402  -- must follow the DB_PATH verification above

history_import.UPLOAD_ROOT = os.path.join(TMP_DIR, "streaming_history")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TMP_DIR, ignore_errors=True)

# ---------------------------------------------------------------- fixtures

import time  # noqa: E402  -- everything below the guards is ordered by design
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from freezegun import freeze_time  # noqa: E402

import api_log  # noqa: E402
import app as app_module  # noqa: E402
import backfill  # noqa: E402
import builders  # noqa: E402
import db  # noqa: E402
import entities  # noqa: E402
import fakes  # noqa: E402
import jobs  # noqa: E402
import roundtrip  # noqa: E402
import scoring  # noqa: E402
import scrobble  # noqa: E402
import snapshot  # noqa: E402

# The instant every test runs at (P2_tests.md §4.3). Mid-month, mid-day, UTC --
# nothing here should ever depend on a month or DST boundary, so the constant
# does not sit near one. Fixture data is built relative to this, so a test that
# says "played 5 days ago" means the same thing on every run.
FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# The real one, captured before any fixture stubs it out, so a test that wants
# the genuine coalescing worker (session 3) can put it back.
REAL_REQUEST_RECOMPUTE = scoring.request_recompute

# scoring's own initial value, captured rather than re-declared here: a copy of
# that dict in this file would drift the first time a field is added to it.
_PRISTINE_RECOMPUTE_STATUS = dict(scoring._recompute_status)

_JOB_STATUSES = (
    snapshot._status,
    history_import._status,
    roundtrip._status,
    backfill._status,
)

_TEMPLATE_DB = os.path.join(TMP_DIR, "template.db")


# -- The database ---------------------------------------------------------


@pytest.fixture(scope="session")
def _db_template():
    """Builds the schema once and keeps a pristine copy to stamp per test.

    init_db() is 13.5ms and a file copy is ~0.5ms; across a suite that
    difference is the whole point. It also means a test can never see a
    half-migrated schema -- every test starts from bytes that init_db()
    produced exactly once.
    """
    db.init_db()
    # WAL is a persistent database-level setting (db.py:644), so the template
    # inherits it. Checkpoint into the main file first, or the template is
    # copied without whatever is still sitting in the -wal.
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    shutil.copyfile(config.DB_PATH, _TEMPLATE_DB)
    return _TEMPLATE_DB


def _reset_module_state():
    """Returns every module-level global to its start-of-process value.

    Wiping the database is not enough on its own: this project keeps real
    state outside it -- the job slot, four JobStatus singletons, seven
    scoring globals (one of which, `_checker_conn`, is an *open handle* to the
    database file) and the scrobble poller's next-wake time. Left alone, that
    handle would survive the wipe and keep reading the unlinked inode, so the
    backstop would compare the new database against the old one's
    data_version and quietly do nothing.
    """
    jobs._active = None
    jobs._stop_requested = False

    # scrobbling-R.md §7: deliberately in-process rather than in the DB, so
    # the wipe above does not touch it. A test that sets it would otherwise
    # leave the next test's /dev/scrobble claiming a poller is scheduled.
    scrobble._next_wake_at = None

    for status in _JOB_STATUSES:
        status.reset()

    # A contextvar set in one test is still set in the next -- pytest runs
    # them all in one context.
    api_log.api_context.set(None)

    if scoring._checker_conn is not None:
        scoring._checker_conn.close()
    scoring._checker_conn = None
    scoring._last_data_version = None
    scoring._last_fingerprint = None
    scoring._failed_fingerprint = None
    scoring._worker_pending = False
    scoring._worker_alive = False
    scoring._recompute_status = dict(_PRISTINE_RECOMPUTE_STATUS)


# Iterations of the settle spin below. A module constant so a test of the
# helper itself can shorten it -- 2s of real waiting to prove a negative is
# 2s every suite run pays for.
_SETTLE_ATTEMPTS = 200


def _background_threads_settled():
    """Spins until no job holds the slot and no recompute worker is alive.

    **Reads no clock.** freezegun freezes time.monotonic, time.perf_counter
    *and* time.time (verified 2026-08-19), so a deadline computed from any of
    them never arrives and this would spin forever inside the frozen context.
    A fixed iteration count is the one form of timeout that survives that.
    time.sleep is not frozen, so the waiting is real.
    """
    for _ in range(_SETTLE_ATTEMPTS):  # ~2s of real time at 10ms each
        if jobs.active() is None and not scoring._worker_alive:
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _clean_slate(_db_template, freezer):
    """A fresh database and fresh module state around every single test.

    Autouse rather than opt-in: the failure this prevents (one test seeing
    another's rows, or its claimed job slot) surfaces somewhere unrelated,
    which is exactly the kind of bug a suite is supposed to not have.
    """
    _reset_module_state()
    builders.reset_ids()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(config.DB_PATH + suffix)
        except FileNotFoundError:
            pass
    shutil.copyfile(_db_template, config.DB_PATH)

    yield

    # Teardown, in this order: a leaked thread is still using the state we are
    # about to reset, so wait for it first and only then reset. Failing here
    # rather than letting it leak is the whole point -- an escaped job thread
    # otherwise fails some later, innocent test (P2_tests.md §4.5).
    settled = _background_threads_settled()
    # Captured before the reset, not after: _reset_module_state() clears both,
    # so a message built afterwards always reads "jobs.active()=None" and names
    # nothing at all.
    leaked = (jobs.active(), scoring._worker_alive)
    _reset_module_state()
    if not settled:
        pytest.fail(
            "A background thread was still running when the test ended: "
            f"jobs.active()={leaked[0]!r}, scoring._worker_alive={leaked[1]!r}. "
            "Join the thread before asserting, or clear what you set by hand."
        )


@pytest.fixture
def conn():
    """A standalone connection, the same one a job or a script would use.

    Not db.get_db(), which needs a Flask application context -- tests of
    module functions should not have to stand up an app to get a cursor.
    """
    connection = db.connect()
    yield connection
    connection.close()


# -- The clock ------------------------------------------------------------


@pytest.fixture(autouse=True)
def freezer():
    """Pins `now` for every test (P2_tests.md §4.3).

    Autouse because the alternative is fixture dates drifting against a real
    clock: entities.play_stats' 30d/7d windows, scoring's 90-day `recent`
    horizon and api_log's rolling counts would each mean something slightly
    different on every run, and golden snapshots would differ for reasons
    that have nothing to do with the code.

    The cost is that jobs.now_iso() returns the *same* string for every write
    inside one test, so anything that asserts on write ordering has to call
    freezer.tick() deliberately. That is the trade, and it is the right way
    round: an explicit tick is visible, a drifting clock is not.

    Known limit (P2_tests.md §4.3): this does not reach SQLite's own
    datetime('now'). All 8 of those uses are write-side only, so a read-only
    page render never picks up an unfrozen timestamp.
    """
    with freeze_time(FROZEN_NOW) as frozen:
        yield frozen


# -- Scoring's async worker -----------------------------------------------


@pytest.fixture(autouse=True)
def recompute_calls(monkeypatch):
    """Records request_recompute() calls instead of spawning worker threads.

    Autouse because scoring.ensure_fresh() runs in a before_request hook on
    every authenticated request, so without this *every* route test spawns a
    real thread that races its assertions.

    Recording rather than no-op'ing turns async-recompute-N §4.1's rule --
    async where you are working a queue, synchronous where you clicked once --
    into something directly assertable: `assert len(recompute_calls) == 1`
    beats watching for a thread. A test that wants the genuine coalescing
    worker restores conftest.REAL_REQUEST_RECOMPUTE.

    The inline scoring.recompute(conn) call sites are untouched: those are
    synchronous by design and fast against a fixture-sized database.
    """
    calls = []
    # Only the *count* is meaningful -- request_recompute() takes no arguments,
    # so there is nothing about a call worth recording but that it happened.
    monkeypatch.setattr(scoring, "request_recompute", lambda: calls.append(True))
    return calls


# -- The app --------------------------------------------------------------


# Every module that does `from spotify_client import get_spotify_client`. A
# from-import binds the name in the importing module, so patching it at its
# source reaches none of them -- each has to be rebound individually, and a
# module missing from this list silently gets the real client (which, with no
# cached token, returns None and makes the code under test quietly no-op).
_SPOTIFY_CLIENT_IMPORTERS = (app_module, backfill, entities, roundtrip, scrobble, snapshot)


@pytest.fixture
def fake_spotify(monkeypatch):
    """The stand-in `sp` (P2_tests.md §4.4), wired into every module that asks
    for a client.

    Returned already patched in, so a test that needs Spotify behaviour just
    requests this fixture and configures the object -- there is no second step
    to forget. See tests/fakes.py for what it does and does not model.
    """
    sp = fakes.FakeSpotify()
    for module in _SPOTIFY_CLIENT_IMPORTERS:
        monkeypatch.setattr(module, "get_spotify_client", lambda: sp)
    return sp


@pytest.fixture
def app(fake_spotify):
    """A real create_app() with the login guard satisfied.

    Deliberately does **not** set TESTING=True. Flask would then propagate
    exceptions past the registered error handlers, and app.py's whole
    centralized error path -- the /api/* JSON shape from P1-014, error.html --
    would never run, so the tests that exist to cover it could not exist.
    """
    return app_module.create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def run_jobs_inline(monkeypatch):
    """Runs a job on the calling thread instead of spawning one (§4.5).

    Returns the list of job names started, so a test can assert the route
    actually claimed the slot. By the time the POST returns, the job has fully
    finished -- so its status is terminal and nothing races the assertions.

    Reproduces try_start's real semantics: the slot is claimed under the same
    lock, refused if already held, the stop flag is cleared, api_context is
    labelled, and the slot is released in a finally.

    api_context is labelled *and put back*. The real try_start runs its target
    in a fresh thread, which gets its own contextvars Context, so the label
    never escapes the job. Inline there is no second context, so the token is
    reset by hand -- otherwise a page rendered later in the same test would log
    its Spotify requests under the job's name.

    **One deliberate difference.** The real try_start runs the target in a
    thread, so an exception the job does not catch itself dies with that thread
    and is invisible. Here it propagates into the caller -- which surfaces as a
    500 from the route that started it. That is the more useful behaviour in a
    test: a job that crashed should fail the test, not pass it quietly.
    """
    started = []

    def _inline_try_start(name, target, *args):
        with jobs._lock:
            if jobs._active is not None:
                return False
            jobs._active = name
            jobs._stop_requested = False
        started.append(name)
        token = api_log.api_context.set(name)
        try:
            target(*args)
        finally:
            api_log.api_context.reset(token)
            with jobs._lock:
                jobs._active = None
        return True

    monkeypatch.setattr(jobs, "try_start", _inline_try_start)
    return started
