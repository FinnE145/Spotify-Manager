"""The read-time backstop and the async recompute worker
(docs/specs/async-recompute-N.md §3, §5, §6).

`ensure_fresh` reads through `scoring._checker()`, a connection separate from
the `conn` fixture, so a commit made through `conn` is visible to it as
another connection's commit -- exactly the property the real backstop
depends on.
"""

import threading

import pytest

import builders
import canonical
import conftest
import jobs
import scoring


# ---------------------------------------------------------------- the backstop


def test_the_backstop_enqueues_rather_than_recomputing_inline(conn, recompute_calls):
    # source: async-recompute-N.md §5.1 -- "**It enqueues instead of
    # recomputing.** On a moved fingerprint it calls `request_recompute()`
    # and returns."
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(5))

    assert scoring.ensure_fresh() is True
    assert len(recompute_calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM score").fetchone()[0] == 0  # nothing ran inline


def test_a_commit_that_moves_no_scoring_input_is_marked_seen_without_recomputing(
    conn, recompute_calls
):
    """Primed via a real recompute() first (so _last_data_version/
    _last_fingerprint hold a genuine baseline), then a write to a table
    outside _FINGERPRINT_TABLES bumps SQLite's data_version without moving
    any tracked count."""
    # source: scoring-H.md §9.3 -- "Only when it moves, pay the ... to see
    # whether a *scoring* input changed rather than, say, a canvas card",
    # and ensure_fresh's _mark_seen branch
    builders.make_group(conn, ["t1"])
    scoring.recompute(conn)  # primes _last_data_version/_last_fingerprint

    conn.execute(
        "INSERT INTO api_request (ts, host, method, path) VALUES (?, ?, ?, ?)",
        (builders.days_ago(0), "api.spotify.com", "GET", "/v1/me"),
    )
    conn.commit()

    assert scoring.ensure_fresh() is False
    assert len(recompute_calls) == 0
    assert scoring._last_data_version is not None  # marked seen, not left behind


def test_the_backstop_defers_while_a_job_holds_the_slot_and_catches_up_afterwards(
    conn, recompute_calls
):
    # source: async-recompute-N.md §5.2 / scoring-H.md §9.3 -- "The check is
    # skipped entirely while `jobs.active()` ... Nothing is remembered while
    # deferring, so the first request after the slot is released ...
    # recomputes if the job's own recompute never happened."
    jobs._active = "snapshot"
    try:
        builders.make_play(conn, track_id="t1", ts=builders.days_ago(5))
        assert scoring.ensure_fresh() is False
        assert len(recompute_calls) == 0
        assert scoring._last_fingerprint is None  # nothing remembered while deferring
    finally:
        jobs._active = None

    assert scoring.ensure_fresh() is True  # the first request after the slot frees catches up
    assert len(recompute_calls) == 1


def test_the_backstop_defers_while_the_worker_is_alive(conn, recompute_calls):
    # source: async-recompute-N.md §5.2 -- "every request landing in that
    # ~1.8s window would still pay the ~5ms fingerprint read for a
    # fingerprint it already knows is about to be superseded."
    scoring._worker_alive = True
    try:
        builders.make_play(conn, track_id="t1", ts=builders.days_ago(5))
        assert scoring.ensure_fresh() is False
        assert len(recompute_calls) == 0
    finally:
        scoring._worker_alive = False


def test_an_unmoved_data_version_costs_no_fingerprint_read(conn):
    """recompute() itself writes the `score` table, which bumps SQLite's
    data_version even though `score` isn't a fingerprint table -- so the
    FIRST ensure_fresh() after a recompute always re-reads the fingerprint
    once (finds it unchanged, and catches up _last_data_version). Only the
    SECOND call, with nothing committed in between, hits the true fast
    path this test is about."""
    # source: scoring-H.md §9.3 -- "`PRAGMA data_version` ... Unchanged
    # since last check means no other connection has committed; stop here.
    # This is the normal case on every page load."
    builders.make_group(conn, ["t1"])
    scoring.recompute(conn)
    scoring.ensure_fresh()  # catches up past recompute's own data_version bump

    checker = scoring._checker()
    statements = []
    checker.set_trace_callback(statements.append)
    try:
        result = scoring.ensure_fresh()
    finally:
        checker.set_trace_callback(None)

    assert result is False
    assert len(statements) == 1
    assert "data_version" in statements[0]


# ---------------------------------------------------------------- _failed_fingerprint


def test_a_failed_recompute_suppresses_the_next_backstop_check(conn, recompute_calls, monkeypatch):
    # source: async-recompute-N.md §6.2 -- "`ensure_fresh()` skips when the
    # freshly-read fingerprint equals `_failed_fingerprint`: it neither
    # enqueues nor marks anything seen."
    builders.make_group(conn, ["t1"])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    with pytest.raises(RuntimeError):
        scoring.recompute(conn)

    assert scoring.ensure_fresh() is False
    assert len(recompute_calls) == 0
    assert scoring._last_data_version is None  # nothing marked seen -- §6.2's "accepted cost"


def test_a_transient_failure_is_suppressed_exactly_like_a_repeatable_one(
    conn, recompute_calls, monkeypatch
):
    """This is the record of P1-019's ruling: the pre-P1 spec promised a
    distinct self-heal path for a "transient" failure. There is none -- a
    single caught failure suppresses auto-retry identically, whether or not
    a subsequent attempt would have succeeded."""
    # source: async-recompute-N.md §6.2 as rewritten under P1-019 -- "**There
    # is no separate self-heal path for a transient failure**, by design ...
    # a caught failure is suppressed unconditionally until either a fresh
    # commit moves the fingerprint or the manual button ... is clicked."
    builders.make_group(conn, ["t1"])

    def _boom(*args, **kwargs):
        raise RuntimeError("transient")

    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    with pytest.raises(RuntimeError):
        scoring.recompute(conn)

    assert scoring.ensure_fresh() is False
    assert len(recompute_calls) == 0


def test_a_fresh_commit_moving_the_fingerprint_lifts_the_suppression(
    conn, recompute_calls, monkeypatch
):
    # source: async-recompute-N.md §6.2 -- "until either a fresh commit
    # moves the fingerprint or the manual button"
    builders.make_group(conn, ["t1"])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    with pytest.raises(RuntimeError):
        scoring.recompute(conn)
    assert scoring.ensure_fresh() is False  # suppressed

    builders.make_play(conn, track_id="t1", ts=builders.days_ago(5))  # moves `play`'s count

    assert scoring.ensure_fresh() is True
    assert len(recompute_calls) == 1


def test_a_successful_recompute_re_arms_auto_retry(conn, monkeypatch):
    # source: async-recompute-N.md §6.2 -- "`_mark_seen()` clears
    # `_failed_fingerprint`. Any success -- background or the manual button
    # -- re-arms auto-retry."
    builders.make_group(conn, ["t1"])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    with pytest.raises(RuntimeError):
        scoring.recompute(conn)
    assert scoring._failed_fingerprint is not None

    monkeypatch.undo()  # restore the real _version_horizons
    scoring.recompute(conn)
    assert scoring._failed_fingerprint is None


def test_the_manual_button_retries_a_suppressed_fingerprint(conn, client, monkeypatch):
    # source: async-recompute-N.md §6.2 -- "The manual button always
    # retries, since it calls `recompute()` directly and never consults
    # `_failed_fingerprint`."
    builders.make_group(conn, ["t1"])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    real_version_horizons = scoring._version_horizons
    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    with pytest.raises(RuntimeError):
        scoring.recompute(conn)
    assert scoring._failed_fingerprint is not None
    monkeypatch.setattr(scoring, "_version_horizons", real_version_horizons)

    response = client.post("/api/scoring/recompute")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"]["outcome"] == "ok"
    assert "tier_counts" in body
    assert scoring._failed_fingerprint is None


def test_a_failure_before_the_fingerprint_is_read_arms_nothing(conn, monkeypatch):
    # source: async-recompute-N.md §6.2 -- "`recompute()` initializes
    # `observed = None` before its `try` body (a failure inside
    # `canonical.ensure_track_groups()` happens before `_observe()`), and on
    # the error path records `observed`'s fingerprint ... whenever it is not
    # `None`."
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(canonical, "ensure_track_groups", _boom)

    with pytest.raises(RuntimeError):
        scoring.recompute(conn)

    assert scoring._failed_fingerprint is None
    status = scoring.recompute_status()
    assert status["outcome"] == "error"


# ---------------------------------------------------------------- the worker (N §3)


def test_requests_landing_during_a_pass_are_absorbed_by_one_extra_pass(conn, monkeypatch):
    """Blocks the worker's first recompute() call on an Event, then fires
    three more request_recompute() calls while it's in flight. All three are
    coalesced into exactly one extra pass -- two total, not four."""
    # source: async-recompute-N.md §3.4 -- "**This is the coalescing.**
    # Commits landing during a 1.8s pass all set the same flag, and are
    # absorbed by one extra pass -- so holding Enter through ten queue items
    # costs two or three recomputes, not ten."
    monkeypatch.setattr(scoring, "request_recompute", conftest.REAL_REQUEST_RECOMPUTE)
    builders.make_group(conn, ["t1"])

    real_recompute = scoring.recompute
    entered_first = threading.Event()
    release_first = threading.Event()
    calls = []

    def _tracking_recompute(worker_conn):
        calls.append(1)
        if len(calls) == 1:
            entered_first.set()
            release_first.wait(2)
        return real_recompute(worker_conn)

    monkeypatch.setattr(scoring, "recompute", _tracking_recompute)

    before = set(threading.enumerate())
    scoring.request_recompute()
    assert entered_first.wait(2) is True

    scoring.request_recompute()
    scoring.request_recompute()
    scoring.request_recompute()

    release_first.set()
    for t in set(threading.enumerate()) - before:
        t.join(2)

    assert len(calls) == 2
    assert scoring._worker_alive is False


def test_the_worker_defers_to_a_running_job_and_drops_the_request(conn, monkeypatch):
    # source: async-recompute-N.md §3.6 -- "The worker exits without
    # recomputing while `jobs.active()`, dropping `_worker_pending`.
    # Nothing is lost ... `ensure_fresh()` re-catches whatever the drop
    # lost."
    monkeypatch.setattr(scoring, "request_recompute", conftest.REAL_REQUEST_RECOMPUTE)
    calls = []
    monkeypatch.setattr(scoring, "recompute", lambda c: calls.append(1))

    jobs._active = "snapshot"
    try:
        before = set(threading.enumerate())
        scoring.request_recompute()
        for t in set(threading.enumerate()) - before:
            t.join(2)
    finally:
        jobs._active = None

    assert len(calls) == 0
    assert scoring._worker_pending is False
    assert scoring._worker_alive is False


def test_a_thread_that_never_starts_does_not_strand_the_alive_flag(monkeypatch):
    # source: async-recompute-N.md §3.3 -- "That ordering is also why the
    # `start()` failure has to be caught. The flag is only ever cleared by
    # the worker itself, so a thread that never ran leaves it raised
    # forever."
    monkeypatch.setattr(scoring, "request_recompute", conftest.REAL_REQUEST_RECOMPUTE)

    class _BoomThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(threading, "Thread", _BoomThread)

    with pytest.raises(RuntimeError):
        scoring.request_recompute()

    assert scoring._worker_alive is False


def test_the_alive_flag_comes_down_even_when_the_connection_cannot_be_opened(monkeypatch):
    """The regression async-recompute-N.md §3.4 records as caught in verify:
    clearing `_worker_alive` at each `return` inside the loop leaves
    `db.connect()` raising uncovered, since that happens before the loop's
    own try/except. Only the outer `finally` catches every path out."""
    # source: async-recompute-N.md §3.4 -- "**The outer `finally` is why the
    # flag is cleared in exactly one place.** ... `db.connect()` and
    # `jobs.active()` both sit outside any handler ... That is the one
    # unrecoverable state this module has, and it is entirely silent."
    import db as db_module

    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    monkeypatch.setattr(scoring, "request_recompute", conftest.REAL_REQUEST_RECOMPUTE)

    def _boom_connect():
        raise RuntimeError("cannot open db")

    monkeypatch.setattr(db_module, "connect", _boom_connect)

    before = set(threading.enumerate())
    scoring.request_recompute()
    for t in set(threading.enumerate()) - before:
        t.join(2)

    assert scoring._worker_alive is False
    assert scoring._worker_pending is False


def test_a_failing_recompute_stops_the_worker_rather_than_spinning(monkeypatch):
    # source: async-recompute-N.md §3.4 -- "Stopping here rather than
    # looping is what stops a deterministic failure spinning."
    monkeypatch.setattr(scoring, "request_recompute", conftest.REAL_REQUEST_RECOMPUTE)
    calls = []

    def _always_fails(c):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(scoring, "recompute", _always_fails)

    before = set(threading.enumerate())
    scoring.request_recompute()
    for t in set(threading.enumerate()) - before:
        t.join(2)

    assert len(calls) == 1
    assert scoring._worker_alive is False


# ---------------------------------------------------------------- the one route rule


def test_confirming_a_new_generation_recomputes(conn, client, monkeypatch):
    # source: async-recompute-N.md §4.3's corrected table and app.py's own
    # comment -- "Only a new generation is a scoring input (tenure, §4.1) --
    # declining just mutes the prompt and touches nothing scoring reads."
    # (P1-019)
    playlist_id = builders.make_playlist(conn, name="v40.0.0")
    calls = []
    monkeypatch.setattr(scoring, "recompute", lambda c: calls.append(1))

    response = client.post(
        "/dev/generations/confirm",
        data={"playlist_id": playlist_id, "decision": "yes", "return_to": "dev_generations"},
    )

    assert response.status_code in (302, 303)
    assert len(calls) == 1
    assert (
        conn.execute(
            "SELECT 1 FROM generation WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()
        is not None
    )


def test_declining_a_new_generation_does_not_recompute(conn, client, monkeypatch):
    # source: same as above -- the negative case, without which a
    # "never recomputes" implementation would pass the half that matters
    playlist_id = builders.make_playlist(conn, name="v41.0.0")
    calls = []
    monkeypatch.setattr(scoring, "recompute", lambda c: calls.append(1))

    response = client.post(
        "/dev/generations/confirm",
        data={"playlist_id": playlist_id, "decision": "no", "return_to": "dev_generations"},
    )

    assert response.status_code in (302, 303)
    assert len(calls) == 0
    row = conn.execute(
        "SELECT generation_declined FROM snapshot WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    assert row["generation_declined"] == 1
