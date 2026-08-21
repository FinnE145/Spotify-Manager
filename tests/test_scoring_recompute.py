"""Storage and recompute (docs/specs/scoring-H.md §9): materialization,
wholesale replace, idempotence, and the backstop's capture-before-read
ordering.
"""

import pytest

import builders
import canonical
import scoring


def test_recompute_materializes_exactly_the_four_stored_tiers(conn):
    # source: scoring-H.md §9.1 -- "One table keyed by `(tier, group_id)`
    # holding both horizons, covering **version, recording, release and
    # track** only."
    builders.make_group(conn, ["t1"])
    scoring.recompute(conn)

    tiers = {row["tier"] for row in conn.execute("SELECT DISTINCT tier FROM score")}
    assert tiers == {"version", "recording", "release", "track"}


def test_recompute_replaces_the_table_wholesale(conn):
    """A stale row for a group id that no longer exists (as happens when a
    merge destroys a group) must be gone after recompute, not upserted."""
    # source: scoring-H.md §9.2 -- "Grouping changes destroy group ids ...
    # an upsert would leave those rows behind forever as scores for entities
    # that no longer exist."
    builders.make_score(conn, "version", "999999", all_time=42.0, recent=42.0)
    builders.make_group(conn, ["t1"])
    scoring.recompute(conn)

    row = conn.execute(
        "SELECT 1 FROM score WHERE tier = 'version' AND group_id = '999999'"
    ).fetchone()
    assert row is None


def test_a_second_recompute_with_no_writes_changes_nothing(conn):
    # source: scoring-H.md §9.2 -- "Clearing is also what makes a recompute
    # idempotent, which §14 tests"; §14 -- "a second run with no intervening
    # writes changes nothing."
    builders.make_group(conn, ["t1"])
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(5))
    scoring.recompute(conn)
    before = {
        (row["tier"], row["group_id"]): (row["all_time"], row["recent"])
        for row in conn.execute("SELECT tier, group_id, all_time, recent FROM score")
    }

    scoring.recompute(conn)
    after = {
        (row["tier"], row["group_id"]): (row["all_time"], row["recent"])
        for row in conn.execute("SELECT tier, group_id, all_time, recent FROM score")
    }
    assert before == after


def test_recompute_groups_any_track_that_has_no_group_yet(conn):
    # source: recompute's first statement (canonical.ensure_track_groups),
    # and §9.3's note that it "inserts track_group rows on an ordinary page
    # load"
    builders.make_track(conn, track_id="t1")  # no make_group call
    assert conn.execute(
        "SELECT 1 FROM track_group WHERE track_id = 't1'"
    ).fetchone() is None

    scoring.recompute(conn)

    assert conn.execute(
        "SELECT 1 FROM track_group WHERE track_id = 't1'"
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM score WHERE tier = 'track' AND group_id = 't1'"
    ).fetchone() is not None


def test_recompute_records_a_successful_run(conn):
    # source: scoring-H.md §9.3's manual-button description -- "it shows the
    # per-tier materialized counts and the last run's outcome/duration"
    builders.make_group(conn, ["t1"])
    scoring.recompute(conn)

    status = scoring.recompute_status()
    assert status["outcome"] == "ok"
    assert status["error"] is None
    assert status["counts"] == scoring.tier_counts(conn)
    assert status["started_at"] is not None
    assert status["finished_at"] is not None
    assert isinstance(status["duration_seconds"], float)


def test_recompute_records_a_failure_and_re_raises_it(conn, monkeypatch):
    # source: recompute's docstring -- "a failure is re-raised afterward, so
    # the status is an extra receipt, never the only place the error
    # surfaces."
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(scoring, "_version_horizons", _boom)
    builders.make_group(conn, ["t1"])

    with pytest.raises(RuntimeError, match="boom"):
        scoring.recompute(conn)

    status = scoring.recompute_status()
    assert status["outcome"] == "error"
    assert "boom" in status["error"]
    assert status["counts"] is None


def test_the_backstop_pair_is_captured_before_the_recompute_reads_its_inputs(
    conn, recompute_calls, monkeypatch
):
    """A second connection commits a play row partway through the recompute
    (during _version_horizons, before the fingerprint would otherwise be
    read). Because _observe() is called BEFORE that point in the real
    recompute() -- not after -- the mid-run write is still visible to the
    next ensure_fresh() check."""
    # source: scoring-H.md §9.3 -- "The pair is captured **before** the
    # recompute reads its inputs and published only on success ... Capturing
    # it afterwards would record that change as already-handled and lose
    # it."
    import db

    builders.make_group(conn, ["t1"])

    real_version_horizons = scoring._version_horizons
    called = []

    def _mid_run_write(*args, **kwargs):
        if not called:
            called.append(True)
            side_conn = db.connect()
            builders.make_play(side_conn, track_id="t1", ts=builders.days_ago(5))
            side_conn.close()
        return real_version_horizons(*args, **kwargs)

    monkeypatch.setattr(scoring, "_version_horizons", _mid_run_write)
    scoring.recompute(conn)

    assert scoring.ensure_fresh() is True
    assert len(recompute_calls) == 1
