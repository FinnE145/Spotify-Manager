"""`jobs.py` -- the one background-job slot, and the wrapper every Spotify
request in every job goes through.

The slot is one module lock guarding one `_active` name, so claiming it is a
single atomic check-and-set: no lock ordering, no deadlock, and no window in
which two starters both see "nothing running". The modules previously each
held their own lock and checked the other's status without one, which raced --
so the concurrency assertions below are the point of the file, not decoration.

`jobs.call`'s rate-limit rule is the other half: a short wait is Spotify's
routine rolling window and is slept through, but a long one means an app-level
quota is exhausted and must **fail fast** rather than block a background thread
for hours. `spotify_client.py` sets `respect_retry_after_header=False`
specifically so that decision lands here rather than inside `requests`.
"""

import threading

import pytest
from spotipy.exceptions import SpotifyException

import fakes
import jobs
import snapshot


@pytest.fixture
def no_sleep(monkeypatch):
    """Records rate-limit waits instead of serving them.

    Returns the list of durations slept, which is what turns "it waited the
    right amount" into an assertion rather than a stopwatch. Patched on the
    `time` module `jobs` imported, so only calls made during this test see it
    and monkeypatch restores it afterwards.
    """
    slept = []
    monkeypatch.setattr(jobs.time, "sleep", slept.append)
    return slept


# -- The job slot -----------------------------------------------------------


def test_a_job_claims_the_slot_and_releases_it():
    # source: jobs.py -- "The slot is released in a finally, so a job that
    # crashes can never wedge the app."
    ran = threading.Event()

    assert jobs.try_start("snapshot", ran.set) is True
    assert ran.wait(2) is True


def test_a_second_job_cannot_claim_a_held_slot():
    """Exactly one job may run at a time: they all write the same SQLite file
    and two of them spend the same Spotify request budget."""
    # source: jobs.py -- one atomic check-and-set, so there is no window in
    # which two starters both see "nothing running".
    started, release = threading.Event(), threading.Event()

    def hold():
        started.set()
        release.wait(2)

    assert jobs.try_start("snapshot", hold) is True
    assert started.wait(2) is True
    assert jobs.active() == "snapshot"
    assert jobs.try_start("roundtrip", lambda: None) is False

    release.set()


def test_the_slot_is_released_even_when_the_job_raises(monkeypatch):
    """A crashing job must not wedge the app -- the release is in a finally.

    The exception dies with the thread, which is the real behaviour: an
    uncaught error in a job is invisible to the app. (`run_jobs_inline`
    deliberately diverges from this, so a crashed job fails its test rather
    than passing quietly.)

    Both pieces of plumbing here exist because that death is asynchronous.
    pytest reports an unhandled thread exception as a warning against
    *whichever test happens to be running when it surfaces*, so it is silenced
    at `threading.excepthook` rather than with a filterwarnings mark, which
    would be attached to the wrong test to have any effect; and the thread is
    joined rather than polled, so the excepthook has certainly run before the
    monkeypatch is undone.
    """
    # source: jobs.try_start -- the finally around target(*args).
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    crashed = threading.Event()
    before = set(threading.enumerate())

    def boom():
        crashed.set()
        raise RuntimeError("job blew up")

    assert jobs.try_start("snapshot", boom) is True
    assert crashed.wait(2) is True
    for thread in set(threading.enumerate()) - before:
        thread.join(2)

    assert jobs.active() is None


def test_stopping_refuses_a_job_that_is_not_the_one_running():
    """The name is taken for the same reason `try_start` takes it: the caller
    must not be able to stop a job it did not mean to, and checking
    `active() == name` outside the lock leaves a gap between check and set."""
    # source: jobs.request_stop -- it returns False if `name` is not active.
    started, release = threading.Event(), threading.Event()

    def hold():
        started.set()
        release.wait(2)

    jobs.try_start("snapshot", hold)
    started.wait(2)

    assert jobs.request_stop("roundtrip") is False
    assert jobs.stop_requested() is False
    assert jobs.request_stop("snapshot") is True
    assert jobs.stop_requested() is True

    release.set()


def test_a_new_job_clears_a_stop_left_over_from_the_previous_run():
    # source: jobs.try_start -- "A stop asked for during the previous run must
    # not kill this one before it starts."
    jobs._stop_requested = True
    ran = threading.Event()

    def check():
        # Read inside the job, which is the only place it matters.
        if not jobs.stop_requested():
            ran.set()

    jobs.try_start("snapshot", check)
    assert ran.wait(2) is True


def test_now_iso_carries_an_explicit_z():
    """The database holds two timestamp formats -- SQL-side `datetime('now')`
    writes naive UTC, this writes the Z form that `format.js` parses."""
    # source: db.py:162's comment, and P2_tests.md §4.3's note on the two
    # formats; the Z is what makes a stored timestamp render correctly.
    assert jobs.now_iso().endswith("Z")
    assert len(jobs.now_iso()) == len("2026-06-15T12:00:00Z")


# -- `jobs.call` and the rate-limit rule ------------------------------------


def test_a_call_counts_one_request(no_sleep):
    # source: jobs.call -- every Spotify request a job makes is counted into
    # that job's own status, which is what makes the run counter one increment.
    status = jobs.JobStatus("test", requests=0)

    jobs.call(status, lambda: "ok")

    assert status.get()["requests"] == 1


def test_a_short_rate_limit_wait_is_slept_through_and_retried(no_sleep):
    """Spotify's rolling 30s window is routine; sleeping through it is
    cheaper than failing a whole run over it."""
    # source: jobs.py -- "_SHORT_WAIT_LIMIT_SECONDS = 30 ... safe to sleep
    # through".
    status = jobs.JobStatus("test", requests=0)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise fakes.rate_limited(retry_after=5)
        return "ok"

    assert jobs.call(status, flaky) == "ok"
    assert no_sleep == [5]
    # The 429 really did hit the API, so it counts too.
    assert status.get()["requests"] == 2


def test_a_long_rate_limit_wait_fails_fast_without_sleeping(no_sleep):
    """An app-level quota is exhausted. Blocking a background thread for hours
    is the behaviour `spotify_client.py` turns off at the `requests` layer so
    that this decision can be made here instead."""
    # source: jobs.py -- "anything longer means an app-level quota is
    # exhausted, and we fail fast instead of blocking the background thread."
    status = jobs.JobStatus("test", requests=0)

    with pytest.raises(jobs.RateLimited) as caught:
        jobs.call(status, lambda: (_ for _ in ()).throw(fakes.rate_limited(retry_after=3600)))

    assert caught.value.retry_after_seconds == 3600
    assert caught.value.retry_at.endswith("Z")
    assert no_sleep == []


def test_a_second_consecutive_rate_limit_fails_rather_than_sleeping_again(no_sleep):
    """One retry, not a loop -- otherwise a sustained 429 becomes an unbounded
    wait dressed up as progress."""
    # source: jobs.call -- `or attempt == 1` is what caps it at one retry.
    status = jobs.JobStatus("test", requests=0)

    def always_limited():
        raise fakes.rate_limited(retry_after=2)

    with pytest.raises(jobs.RateLimited):
        jobs.call(status, always_limited)

    assert no_sleep == [2]
    assert status.get()["requests"] == 2


def test_a_non_rate_limit_error_is_raised_straight_through(no_sleep):
    # source: jobs.call -- only a 429 is handled here; everything else is the
    # caller's problem.
    status = jobs.JobStatus("test", requests=0)

    with pytest.raises(SpotifyException) as caught:
        jobs.call(status, lambda: (_ for _ in ()).throw(fakes.not_found()))

    assert caught.value.http_status == 404
    assert no_sleep == []


def test_a_missing_retry_after_header_defaults_to_one_second(no_sleep):
    # source: jobs.call -- `int(e.headers.get("Retry-After", 1))`.
    status = jobs.JobStatus("test", requests=0)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise SpotifyException(429, -1, "slow down", headers={})
        return "ok"

    assert jobs.call(status, flaky) == "ok"
    assert no_sleep == [1]


# -- `JobStatus` ------------------------------------------------------------


def test_running_is_derived_from_the_slot_never_stored():
    """One source of truth for whether a job is live. A stored flag is what
    leaves a crashed job showing as running forever."""
    # source: jobs.JobStatus.get -- "Derived rather than stored, so there is
    # exactly one source of truth for whether the job is live."
    status = jobs.JobStatus("snapshot", phase=None)
    assert status.get()["running"] is False

    # Set by hand rather than by starting a thread: the assertion is about
    # `get()` reading the slot, not about try_start. Restored in the finally
    # because conftest fails any test that ends with the slot still claimed.
    jobs._active = "snapshot"
    try:
        assert status.get()["running"] is True
        assert jobs.JobStatus("roundtrip", phase=None).get()["running"] is False
    finally:
        jobs._active = None


def test_terminal_fields_survive_so_a_reload_still_shows_the_outcome():
    # source: jobs.JobStatus.get -- "The terminal fields do stay in the dict,
    # so a page that reloads after a run still renders its outcome."
    status = jobs.JobStatus("snapshot", phase=None, error=None)
    status.set(phase="done", error=None)

    got = status.get()
    assert got["running"] is False
    assert got["phase"] == "done"


def test_reset_returns_every_field_to_its_declared_default():
    # source: jobs.JobStatus -- progress fields "are declared as the defaults
    # a reset() returns to".
    status = jobs.JobStatus("test", phase=None, count=0)
    status.set(phase="working")
    status.add(count=7)

    status.reset()

    assert status.get()["phase"] is None
    assert status.get()["count"] == 0


def test_reset_hands_back_a_fresh_list_not_the_one_the_last_run_appended_to():
    """Without the copy, a reset would return the very list the previous run
    filled -- and every "new" run would start holding the old one's entries."""
    # source: jobs.JobStatus._fresh -- "Lists are copied, not shared".
    status = jobs.JobStatus("test", failures=[])
    status.append("failures", {"uri": "spotify:track:x"})

    status.reset()

    assert status.get()["failures"] == []


def test_the_event_log_is_capped_so_a_long_run_stays_small():
    # source: jobs.py -- "_LOG_LIMIT = 200 ... Old entries drop off the front,
    # so the status payload stays small however long a run goes."
    status = jobs.JobStatus("test")
    for index in range(jobs._LOG_LIMIT + 50):
        status.log(f"line {index}")

    log = status.get()["log"]
    assert len(log) == jobs._LOG_LIMIT
    assert log[-1]["message"] == f"line {jobs._LOG_LIMIT + 49}"
    # The front is what drops.
    assert log[0]["message"] == "line 50"


def test_append_honours_its_own_limit():
    # source: jobs.JobStatus.append -- the per-field cap the failure feed uses.
    status = jobs.JobStatus("test", failures=[])
    for index in range(5):
        status.append("failures", index, limit=3)

    assert status.get()["failures"] == [2, 3, 4]


def test_get_returns_a_copy_so_a_caller_cannot_mutate_the_status():
    """`roundtrip._run` reads `_status.get()['left_in_playlist']` mid-run and
    `_record_run` reads the whole dict; neither may be able to write it back."""
    # source: jobs.JobStatus.get -- it builds `dict(self._fields)` and
    # `list(self._log)`; characterization of that boundary.
    status = jobs.JobStatus("test", count=0)
    status.log("one")

    got = status.get()
    got["count"] = 99
    got["log"].append({"ts": "x", "message": "injected"})

    assert status.get()["count"] == 0
    assert len(status.get()["log"]) == 1


def test_every_job_status_is_reset_between_tests():
    """The four `JobStatus` singletons are module-level state that a wiped
    database does not touch, so conftest resets them by hand."""
    # source: P2_tests.md §4.5 / conftest._reset_module_state -- a leaked
    # status is exactly the kind of bug that surfaces in an unrelated test.
    assert snapshot._status.get()["phase"] is None
    assert snapshot._status.get()["log"] == []
