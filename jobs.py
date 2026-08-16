"""One background job at a time: the single lock, the single active-job
name, and the status/event-log plumbing all three jobs share.

Symr runs three long jobs -- the snapshot pull, the play-history import and
the foreign-track round-trip -- and exactly one may run at a time: they all
write the same SQLite file, and two of them spend the same Spotify request
budget. Claiming the slot is one atomic check-and-set under one lock, so
there is no lock ordering, no deadlock, and no window in which two starters
both see "nothing running". (The modules previously each held their own
lock and checked the other's status without one, which raced.)"""

import threading
import time
from datetime import datetime, timedelta, timezone

from spotipy.exceptions import SpotifyException

import api_log

# The one lock and the one job slot.
_lock = threading.Lock()
_active = None  # None | "snapshot" | "history_import" | "roundtrip" | "backfill"
_stop_requested = False

# Entries kept in a JobStatus event log. Old entries drop off the front, so
# the status payload stays small however long a run goes.
_LOG_LIMIT = 200

# A rate-limit wait this short is routine (Spotify's rolling 30s window) and
# safe to sleep through; anything longer means an app-level quota is
# exhausted, and we fail fast instead of blocking the background thread.
_SHORT_WAIT_LIMIT_SECONDS = 30


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- The job slot -------------------------------------------------


def active():
    """The name of the running job, or None."""
    with _lock:
        return _active


def try_start(name, target, *args):
    """Claims the job slot and spawns the daemon thread, or returns False if
    another job holds it. The slot is released in a finally, so a job that
    crashes can never wedge the app."""
    global _active, _stop_requested
    with _lock:
        if _active is not None:
            return False
        _active = name
        # A stop asked for during the previous run must not kill this one
        # before it starts.
        _stop_requested = False

    def run():
        global _active
        # A fresh thread gets its own contextvars Context by construction, so
        # this can't race a concurrent page-load request's own label (see
        # api_log.api_context).
        api_log.api_context.set(name)
        try:
            target(*args)
        finally:
            with _lock:
                _active = None

    threading.Thread(target=run, daemon=True).start()
    return True


def request_stop(name):
    """Asks the named job to wind up, and returns False if it isn't the one
    running. Takes the name for the same reason try_start does: the caller
    must not be able to stop a job it didn't mean to, and checking
    `active() == name` outside this lock leaves a gap between the check and
    the set.

    Cooperative, never a kill: the job checks stop_requested() at its own safe
    points and exits through its normal stopped-early path with everything
    committed. Interrupting a job mid-write is exactly how a half-written
    batch would be misattributed."""
    global _stop_requested
    with _lock:
        if _active != name:
            return False
        _stop_requested = True
        return True


def stop_requested():
    with _lock:
        return _stop_requested


# -- Spotify calls -------------------------------------------------


class RateLimited(Exception):
    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        self.retry_at = (
            datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        super().__init__(f"Rate limited by Spotify — retry after {self.retry_at}")


def call(status, fn, *args, **kwargs):
    """Calls a Spotipy method, retrying once through a short rate-limit wait
    but raising RateLimited on a long one instead of blocking.

    Every Spotify request a job makes goes through here, counted into that
    job's own status, which is what makes the run's request counter a single
    increment. A 429 retry counts too -- it really did hit the API."""
    for attempt in range(2):
        status.count_request()
        try:
            return fn(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status != 429:
                raise
            retry_after = int(e.headers.get("Retry-After", 1))
            if retry_after > _SHORT_WAIT_LIMIT_SECONDS or attempt == 1:
                raise RateLimited(retry_after) from e
            time.sleep(retry_after)
    raise AssertionError("unreachable")


# -- Per-job status -------------------------------------------------


class JobStatus:
    """A job's progress fields plus its event log, behind one lock.

    Progress fields stay per-job (playlists vs files vs batches genuinely
    differ) and are declared as the defaults a reset() returns to."""

    def __init__(self, name, **default_fields):
        self._name = name
        self._lock = threading.Lock()
        self._defaults = default_fields
        self._fields = self._fresh()
        self._log = []

    def _fresh(self):
        # Lists are copied, not shared: without this a reset() would hand
        # back the very list the previous run appended to.
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in self._defaults.items()
        }

    def set(self, **kwargs):
        with self._lock:
            self._fields.update(kwargs)

    def reset(self, **kwargs):
        with self._lock:
            self._fields = self._fresh()
            self._fields.update(kwargs)
            self._log = []

    def get(self):
        with self._lock:
            status = dict(self._fields)
            status["log"] = list(self._log)
        # Derived rather than stored, so there is exactly one source of truth
        # for whether the job is live. The terminal fields (phase,
        # finished_at, error, action) do stay in the dict, so a page that
        # reloads after a run still renders its outcome.
        status["running"] = active() == self._name
        return status

    def log(self, message):
        """Appends one short line to the live feed. A monitoring feed, not a
        debug log -- keep entries to one line each."""
        with self._lock:
            self._log.append({"ts": now_iso(), "message": message})
            del self._log[:-_LOG_LIMIT]

    def add(self, **deltas):
        """Increments running totals in place. Lets a job keep its counters in
        one place instead of tallying locally and mirroring them in."""
        with self._lock:
            for key, delta in deltas.items():
                self._fields[key] = self._fields.get(key, 0) + delta

    def count_request(self):
        self.add(requests=1)

    def append(self, field, value, limit=None):
        with self._lock:
            items = self._fields[field]
            items.append(value)
            if limit is not None:
                del items[:-limit]
