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
import time

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


def test_a_job_runs_on_a_daemon_thread_so_the_interpreter_can_exit():
    """A non-daemon job thread would hold the process open at shutdown.

    `drain()` is cooperative and never kills anything, so a job that ignores
    the stop flag is *meant* to time out and leave Docker's SIGKILL as the
    backstop -- which only works if the thread cannot itself block interpreter
    exit."""
    # source: S_sweep.md §3 -- true at jobs.py:73 (`daemon=True` -> `False`).
    # Read off the live thread rather than off a monkeypatched
    # threading.Thread: the flag is checked on the object try_start actually
    # spawned, so a constructor that dropped the kwarg is caught too. A test
    # inside the interpreter cannot watch the interpreter exit, so the flag on
    # the real thread is the closest observable to the property itself.
    seen = {}
    done = threading.Event()

    def probe():
        seen["daemon"] = threading.current_thread().daemon
        done.set()

    assert jobs.try_start("snapshot", probe) is True
    assert done.wait(2) is True
    assert seen["daemon"] is True


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


def test_drain_returns_immediately_when_nothing_is_running(no_sleep):
    # source: host-on-fe-pro-Q.md §6.4 / Tests clause 2 -- "With no job
    # active, returns True without waiting" -- catches a drain() that just
    # sleeps out its timeout regardless of whether anything is running.
    # Asserted via no_sleep rather than a wall-clock measurement: this
    # suite's autouse freezer fixture freezes time.monotonic, so timing the
    # call would prove nothing.
    assert jobs.drain(timeout=5) is True
    assert no_sleep == []


def test_drain_stops_a_cooperative_job_and_waits_for_the_slot():
    """A job that polls stop_requested() at its own safe points -- exactly
    how all four real jobs behave -- exits on its own once drain() asks."""
    # source: host-on-fe-pro-Q.md §6.4 / Tests clause 2 -- "returns True once
    # the slot clears, and the job observed the flag" -- catches a drain()
    # that waits without ever calling request_stop. A non-default job name
    # ("backfill"): request_stop(name) no-ops when name != _active, so a
    # drain() that hardcoded "snapshot" would pass a test using "snapshot"
    # while being completely broken.
    started, observed_stop = threading.Event(), threading.Event()

    def cooperative():
        started.set()
        while not jobs.stop_requested():
            time.sleep(0.01)
        observed_stop.set()

    jobs.try_start("backfill", cooperative)
    started.wait(2)

    assert jobs.drain(timeout=5) is True
    assert observed_stop.is_set()
    assert jobs.active() is None


def test_drain_times_out_on_a_job_that_ignores_the_stop_flag():
    # source: host-on-fe-pro-Q.md §6.4 / Tests clause 2 -- "With a job that
    # ignores the flag, returns False after the timeout" -- catches a
    # drain() that reports success unconditionally.
    started, release = threading.Event(), threading.Event()

    def stubborn():
        started.set()
        release.wait(2)  # never checks stop_requested()

    jobs.try_start("backfill", stubborn)
    started.wait(2)

    assert jobs.drain(timeout=0.2) is False

    release.set()


def test_drain_polls_for_its_whole_default_timeout(no_sleep):
    """40 seconds of grace at a 0.05s poll is 800 looks, and `serve.py`'s
    SIGTERM handler takes that default -- so the default is the number that
    actually decides how long a shutdown waits for a job to wind up."""
    # source: S_sweep.md §3 -- num at jobs.py:104 (`timeout=40` -> 41) and max
    # at jobs.py:124 (`max` -> `min`). Both change how many times drain looks
    # before giving up, and the two existing drain tests passed explicit
    # timeouts at jobs that either stopped immediately or never stopped, so
    # neither the default nor the poll *count* was read by anything.
    #
    # The count is asserted rather than the elapsed time: `no_sleep` records
    # the waits instead of serving them, so 800 polls cost nothing and the
    # frozen clock (which freezes monotonic, perf_counter and time) never
    # comes into it. 800 = 40s / 0.05s, both written out as literals -- read
    # back off `jobs.drain`'s own default it would move with the mutant.
    started, release = threading.Event(), threading.Event()

    def stubborn():
        started.set()
        release.wait(1)  # never checks stop_requested()

    jobs.try_start("backfill", stubborn)
    started.wait(2)
    try:
        assert jobs.drain() is False
        assert len(no_sleep) == 800
        assert no_sleep[0] == 0.05
        assert no_sleep[-1] == 0.05
    finally:
        release.set()


def test_drain_always_takes_at_least_one_look_however_short_the_timeout(no_sleep):
    """`max(1, ...)` is a floor, not arithmetic: a timeout shorter than one
    poll interval still gets a look, and above the floor the count follows the
    timeout."""
    # source: S_sweep.md §3 -- num at jobs.py:124 (`max(1, ...)` ->
    # `max(2, ...)`). That mutant is invisible at any ordinary timeout, since
    # max(2, 800) is still 800; it only shows below the floor, which is
    # exactly where the `max` earns its place. 0.04s divides to 0 attempts, so
    # the floor is the only thing standing between drain and never looking at
    # all -- and `min(1, 0)` is 0, which is the same line's other mutant.
    started, release = threading.Event(), threading.Event()

    def stubborn():
        started.set()
        release.wait(1)

    jobs.try_start("backfill", stubborn)
    started.wait(2)
    try:
        # 0.04s / 0.05s floors to zero attempts -- the max is what rescues it.
        # 0.05s is exactly one. 0.1s is the first timeout that buys a second.
        for timeout, expected_polls in ((0.04, 1), (0.05, 1), (0.1, 2)):
            del no_sleep[:]
            assert jobs.drain(timeout=timeout) is False
            assert len(no_sleep) == expected_polls
    finally:
        release.set()


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


def test_the_short_wait_boundary_sits_at_exactly_thirty_seconds(no_sleep):
    """Thirty seconds is Spotify's rolling window and is slept through;
    thirty-one is an app-level quota and fails fast. Both sides of that one
    second are pinned here, because the constant and the comparison that
    place the boundary are two separate ways to move it by one."""
    # source: S_sweep.md §3 -- num at jobs.py:32 and cmp> at jobs.py:159.
    # `_SHORT_WAIT_LIMIT_SECONDS = 31` and `retry_after >= LIMIT` each shift
    # the boundary a second in opposite directions, and the existing tests
    # probed only 5s (well inside) and 3600s (well outside), so neither
    # noticed. The two halves below are not redundant: 30 catches the `>=`
    # and 31 catches the constant, and neither catches the other.
    #
    # The retry in the 31s half is what makes that direction observable at
    # all -- a fn that always raises would still end in RateLimited under the
    # mutant (via `or attempt == 1`), just a sleep later, so the fn succeeds
    # on retry and the `no_sleep` assertion backs it up.
    short = jobs.JobStatus("test", requests=0)
    short_attempts = []

    def flaky_at_thirty():
        short_attempts.append(1)
        if len(short_attempts) == 1:
            raise fakes.rate_limited(retry_after=30)
        return "ok"

    assert jobs.call(short, flaky_at_thirty) == "ok"
    assert no_sleep == [30]

    no_sleep.clear()
    long_ = jobs.JobStatus("test", requests=0)
    long_attempts = []

    def flaky_at_thirty_one():
        long_attempts.append(1)
        if len(long_attempts) == 1:
            raise fakes.rate_limited(retry_after=31)
        return "ok"

    with pytest.raises(jobs.RateLimited) as caught:
        jobs.call(long_, flaky_at_thirty_one)

    assert caught.value.retry_after_seconds == 31
    assert no_sleep == []
    # Raised on the first attempt, so the retry never happened.
    assert long_attempts == [1]
    assert long_.get()["requests"] == 1


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


def test_a_counter_bumped_before_it_exists_starts_from_zero():
    """`add()` is the one way a job moves a running total, and its fallback is
    where a counter with no declared default begins."""
    # source: S_sweep.md §3 -- num at jobs.py:222
    # (`self._fields.get(key, 0)` -> `get(key, 1)`). The fallback fires only on
    # the very first bump of a key, and all four real JobStatus instances --
    # and every one built in this file -- declare the keys they later bump, so
    # nothing had ever taken that branch and a first bump silently becoming
    # `delta + 1` went unnoticed. Pinned as an exact value rather than a
    # non-zero one: 5 against 6 is the entire difference.
    status = jobs.JobStatus("test", phase=None)

    status.add(undeclared=5)
    assert status.get()["undeclared"] == 5

    # And the fallback applies once, not on every bump.
    status.add(undeclared=5)
    assert status.get()["undeclared"] == 10


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
    #
    # source: S_sweep.md §3 -- num at jobs.py:27. The cap is written out as a
    # literal 200 on both sides, not read back off `jobs._LOG_LIMIT`: a test
    # that both fills and asserts through the constant moves with it, so
    # `_LOG_LIMIT = 201` retained 201 entries and passed. The retained count
    # and the *identity* of the surviving front entry are both pinned, because
    # the cap's whole job is which entries are dropped.
    status = jobs.JobStatus("test")
    for index in range(250):
        status.log(f"line {index}")

    log = status.get()["log"]
    assert len(log) == 200
    assert log[-1]["message"] == "line 249"
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
