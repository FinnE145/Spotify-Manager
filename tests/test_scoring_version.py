"""The version tier's inputs, shrinkage and horizons (docs/specs/scoring-H.md
§4, §7). Every expected literal is hand-derived from the spec's formulas; see
docs/codebase-health/P2_tests.md §2 -- none came from running scoring.py.
"""

from datetime import datetime, timedelta, timezone

import pytest

import builders
import scoring


def bucket_a_library(conn):
    """Three bucket-A versions (no memberships anywhere) with R = {0, 0.5, 2.0}.

    Bucket A can only vary in R -- M is 0 by definition of the bucket and T
    needs a membership -- so the median R is 0.5 and the baseline is
    W_RATE * sat(0.5, 0.5) = 0.2. Reused by test_scoring_subtier.py.
    """
    groups = {name: builders.make_group(conn, [name]) for name in ("a1", "a2", "a3")}
    # a1: nothing at all. W=0, no first opportunity -> E = MIN_EXPOSURE_DAYS, R = 0.
    # a2: one half-listened play 30d ago -> W = 105000/210000 = 0.5, E = 30, R = 30*0.5/30 = 0.5
    builders.make_play(conn, track_id="a2", ts=builders.days_ago(30), ms_played=105_000)
    # a3: two full plays 30d ago -> W = 2.0, E = 30, R = 30*2.0/30 = 2.0
    builders.make_play(conn, track_id="a3", ts=builders.days_ago(30))
    builders.make_play(conn, track_id="a3", ts=builders.days_ago(30))
    return groups


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- §4.2 play weight


def test_a_play_contributes_its_listen_fraction(conn):
    # source: scoring-H.md §4.2 -- "contribution = min(ms_played / duration_ms, 1.0)"
    group = builders.make_group(conn, ["t1"])  # duration defaults to 210_000
    builders.make_play(conn, track_id="t1", ms_played=52_500)  # 0.25 of duration

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[group["version"]]["W"] == pytest.approx(0.25)


def test_a_play_longer_than_the_track_is_capped_at_one(conn):
    # source: scoring-H.md §4.2 -- the "min(..., 1.0)"
    group = builders.make_group(conn, ["t1"])
    builders.make_play(conn, track_id="t1", ms_played=999_999)  # >> 210_000

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[group["version"]]["W"] == pytest.approx(1.0)


def test_a_track_with_no_duration_contributes_a_full_play(conn):
    """The explicit CASE, not MIN(x/NULLIF(duration,0),1.0): SQLite's scalar
    MIN returns NULL if any argument is NULL and SUM drops NULL rows, which
    would silently read as "contributes nothing" -- the opposite of §4.2.
    Note: docs/scoring/tuning_prototype.py itself uses the NULLIF form and
    gets this wrong; see the P2 finding recorded for session 3."""
    # source: scoring-H.md §4.2 -- "Tracks with duration_ms of 0 or NULL
    # contribute their raw play at weight 1.0 rather than dividing by zero."
    builders.make_track(conn, track_id="tnull", duration_ms=None)  # before make_group: it fills parents but never overwrites an existing row
    g_null = builders.make_group(conn, ["tnull"])
    builders.make_play(conn, track_id="tnull", ms_played=1000)

    builders.make_track(conn, track_id="tzero", duration_ms=0)
    g_zero = builders.make_group(conn, ["tzero"])
    builders.make_play(conn, track_id="tzero", ms_played=1000)

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[g_null["version"]]["W"] == pytest.approx(1.0)
    assert inputs[g_zero["version"]]["W"] == pytest.approx(1.0)


# ---------------------------------------------------------------- §4.3 exposure and rate


def test_exposure_floors_at_the_minimum_window():
    # source: scoring-H.md §4.3 -- "A version with *neither* a play nor an
    # `added_at` has no first opportunity at all; its `E` is
    # `MIN_EXPOSURE_DAYS`" and the floor generally
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert scoring._first_opportunity_days(now, False, win, "9999") == 14


def test_exposure_is_the_days_since_first_opportunity():
    # source: scoring-H.md §4.3
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fo = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert scoring._first_opportunity_days(now, False, win, fo) == 60


def test_exposure_never_drops_below_the_floor_even_for_a_recent_first_opportunity():
    # source: scoring-H.md §4.3 -- "The floor prevents a version first seen
    # yesterday from posting an unbounded rate off one play."
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fo = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert scoring._first_opportunity_days(now, False, win, fo) == 14  # not 5


def test_exposure_is_clamped_to_the_window_on_the_recent_horizon():
    # source: scoring-H.md §7.1 -- "Exposure `E`: since first opportunity |
    # clamped to the window" (recent column)
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fo = (now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert scoring._first_opportunity_days(now, True, win, fo) == 90  # clamped, not 200


def test_exposure_inside_the_window_is_not_clamped():
    # source: scoring-H.md §7.1 -- the negative half of the clamp above:
    # only a first opportunity *predating* the window is pulled forward to it
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fo = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert scoring._first_opportunity_days(now, True, win, fo) == 30


def test_the_rate_is_plays_per_thirty_days(conn):
    """The ×30 is what §4.3 calls load-bearing and easy to drop: without it
    every rate is 30x too small."""
    # source: scoring-H.md §4.3 -- "**`R` is plays per 30 days, not per day.**
    # ... Implementing `R = W / E` instead makes every rate 30× too small"
    group = builders.make_group(conn, ["t1"])
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(60))  # full play, W=1.0

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[group["version"]]["R"] == pytest.approx(0.5)  # 30*1.0/60


def test_first_opportunity_is_the_earlier_of_the_first_play_and_the_earliest_add_play_first(conn):
    """Membership added 100d ago, played 50d ago -- the earlier (further
    back) of the two wins, so E = 100, not 50."""
    # source: scoring-H.md §4.3 -- "first opportunity is the earlier of its
    # first play and its earliest `added_at`."
    group = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, track_id="t1", added_at=builders.days_ago(100))
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(50))  # full play, W=1.0

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[group["version"]]["R"] == pytest.approx(0.3)  # 30*1.0/100


def test_first_opportunity_is_the_earlier_of_the_first_play_and_the_earliest_add_add_first(conn):
    """Same claim, the other way round: played 100d ago, added 50d ago."""
    # source: scoring-H.md §4.3
    group = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, track_id="t1", added_at=builders.days_ago(50))
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(100))  # full play, W=1.0

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[group["version"]]["R"] == pytest.approx(0.3)  # 30*1.0/100


# ---------------------------------------------------------------- §4.5 shrinkage and buckets


def test_the_three_buckets_are_assigned_from_memberships_and_tenure(conn):
    # source: scoring-H.md §4.5's table -- A: M=0; B: M>0 and T=0; C: T>0
    bucket_a = builders.make_group(conn, ["ta"])  # nothing at all -> A

    bucket_b = builders.make_group(conn, ["tb"])
    builders.make_membership(conn, track_id="tb")  # in a playlist, never a generation -> B

    bucket_c = builders.make_group(conn, ["tc"])
    gen_playlist = builders.make_playlist(conn)
    builders.make_generation(conn, playlist_id=gen_playlist)
    builders.make_membership(conn, playlist_id=gen_playlist, track_id="tc")  # in a generation -> C

    inputs = scoring._fetch_version_inputs(conn, False, _now(), [])
    assert inputs[bucket_a["version"]]["bucket"] == "A"
    assert inputs[bucket_b["version"]]["bucket"] == "B"
    assert inputs[bucket_c["version"]]["bucket"] == "C"


def test_the_bucket_comes_from_all_time_inputs_on_the_recent_horizon(conn):
    """A membership added 200 days ago (outside the 90-day window) still puts
    the version in bucket B on the recent horizon -- proven by checking BOTH
    that the windowed M is 0 (proof the window is actually applied) and that
    the bucket is still B (proof buckets ignore that window)."""
    # source: scoring-H.md §4.5 -- "**Buckets are assigned from all-time `M`
    # and `T` on both horizons**, never from the windowed values ...
    # Assigning from windowed inputs would put almost the whole library in
    # bucket A on `recent`."
    group = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, track_id="t1", added_at=builders.days_ago(200))

    inputs = scoring._fetch_version_inputs(conn, True, _now(), [])
    row = inputs[group["version"]]
    assert row["M"] == 0  # windowed membership count: outside the window
    assert row["bucket"] == "B"  # but the bucket still sees it


def test_the_bucket_baseline_is_built_from_median_inputs_not_from_any_score():
    """Pure function test -- no DB. Three bucket-C rows whose marginal
    medians give one number (0.083333), while the wrong alternatives this
    fixture is built to exclude give two others: 0.316667 if the baseline
    were the median of the members' own *scores* rather than their inputs,
    and 0.511429 if the marginal aggregate were the mean rather than the
    median. All three differ, so this fixture catches either mistake."""
    # source: scoring-H.md §4.5 -- "Split every version into one of three
    # buckets. For each bucket, take the **median** of each input
    # independently (`R`, `M`, `T`). Assemble one synthetic version from
    # those marginal medians. Run §4.4 on it **once**. No output score is
    # ever read, so there is no fixed point to solve."
    rows = {
        "c1": {"R": 2.0, "M": 0, "T": 1, "W": 2.0, "bucket": "C"},
        "c2": {"R": 0.0, "M": 8, "T": 1, "W": 0.0, "bucket": "C"},
        "c3": {"R": 0.0, "M": 0, "T": 6, "W": 9.0, "bucket": "C"},
    }
    _, baselines = scoring._score_all(rows)
    assert baselines["C"] == pytest.approx(0.083333, abs=1e-6)


def test_shrinkage_pulls_toward_the_baseline_and_is_capped_at_half():
    """Same three rows. c2 carries zero evidence (W=0), so an uncapped pull
    would land it exactly on the baseline (0.083333); SHRINK_MAX=0.5 instead
    puts it halfway between its own raw score and the baseline."""
    # source: scoring-H.md §4.5 -- "pull = min( K_SHRINK / (W + K_SHRINK),
    # SHRINK_MAX ) ... At SHRINK_MAX = 0.5 a zero-evidence version moves **at
    # most halfway** toward its baseline and can never reach it"
    rows = {
        "c1": {"R": 2.0, "M": 0, "T": 1, "W": 2.0, "bucket": "C"},
        "c2": {"R": 0.0, "M": 8, "T": 1, "W": 0.0, "bucket": "C"},
        "c3": {"R": 0.0, "M": 0, "T": 6, "W": 9.0, "bucket": "C"},
    }
    scores, _ = scoring._score_all(rows)
    assert scores["c1"] == pytest.approx(0.243333, abs=1e-6)
    assert scores["c2"] == pytest.approx(0.200000, abs=1e-6)
    assert scores["c3"] == pytest.approx(0.161458, abs=1e-6)


def test_a_well_evidenced_version_keeps_more_of_its_own_score():
    """c3 carries W=9, well past K_SHRINK=3, so its pull is the uncapped
    3/12=0.25 rather than the 0.5 cap -- it keeps 75% of its own raw score.
    A constant-SHRINK_MAX implementation (pull always 0.5) would instead
    give c3 a score of 0.135417."""
    # source: scoring-H.md §4.5 -- "Shrinkage pulls under-evidenced estimates
    # toward a baseline, and releases them as evidence accumulates"
    rows = {
        "c1": {"R": 2.0, "M": 0, "T": 1, "W": 2.0, "bucket": "C"},
        "c2": {"R": 0.0, "M": 8, "T": 1, "W": 0.0, "bucket": "C"},
        "c3": {"R": 0.0, "M": 0, "T": 6, "W": 9.0, "bucket": "C"},
    }
    scores, _ = scoring._score_all(rows)
    assert scores["c3"] == pytest.approx(0.161458, abs=1e-6)
    assert scores["c3"] != pytest.approx(0.135417, abs=1e-3)  # constant-cap wrong answer


def test_an_empty_bucket_gets_a_zero_baseline():
    # source: characterization -- _score_all's `if not members: baselines[bucket] = 0.0`
    rows = {"c1": {"R": 1.0, "M": 1, "T": 1, "W": 1.0, "bucket": "C"}}
    _, baselines = scoring._score_all(rows)
    assert baselines["A"] == 0.0
    assert baselines["B"] == 0.0


def test_the_whole_bucket_a_library_scores_as_the_spec_derives_it(conn):
    """End-to-end through recompute(). a1/a2/a3 have R = {0, 0.5, 2.0}, all
    bucket A; the baseline is raw(median R=0.5) = 0.2 and every pull is
    capped at 0.5 (all three have W <= 3).

    Wrong answers this discriminates against: if SHRINK_MAX were dropped
    (pull=1.0 whenever W is small), a1 would land exactly on the baseline
    and display as 44.7214 instead of 31.6228. If the baseline used the mean
    input (0.8333) instead of the median (0.5), a1 would display as 35.3553.
    a2's own raw already equals the baseline, so its score is
    shrink-invariant -- it is the control here, not a discriminator.
    """
    # source: scoring-H.md §4.4, §4.5, §8
    groups = bucket_a_library(conn)
    scoring.recompute(conn)

    a1 = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a1"]["version"]),),
    ).fetchone()["all_time"]
    a2 = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a2"]["version"]),),
    ).fetchone()["all_time"]
    a3 = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a3"]["version"]),),
    ).fetchone()["all_time"]

    assert a1 == pytest.approx(31.6228, abs=1e-3)
    assert a2 == pytest.approx(44.7214, abs=1e-3)
    assert a3 == pytest.approx(50.9902, abs=1e-3)


def test_a_version_with_nothing_at_all_still_gets_a_score(conn):
    # source: scoring-H.md §3.1 -- "**Every track in the library is scored.**
    # No hard gate on membership or plays"; §14 -- "not 0 and not NULL"
    groups = bucket_a_library(conn)
    scoring.recompute(conn)

    row = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a1"]["version"]),),
    ).fetchone()
    assert row is not None
    assert row["all_time"] > 0


def test_memberships_without_plays_score_between_zero_and_the_same_with_plays(conn):
    """Two versions, each with two live memberships; one is additionally
    played. Absence of plays must not read as negative evidence."""
    # source: scoring-H.md §4.6 -- "a few memberships and no plays should
    # land meaningfully above zero and below the same memberships with
    # plays"; §14 restates it.
    unplayed = builders.make_group(conn, ["tu"])
    builders.make_membership(conn, track_id="tu")
    builders.make_membership(conn, playlist_id=None, track_id="tu")

    played = builders.make_group(conn, ["tp"])
    builders.make_membership(conn, track_id="tp")
    builders.make_membership(conn, playlist_id=None, track_id="tp")
    builders.make_play(conn, track_id="tp", ts=builders.days_ago(1))

    scoring.recompute(conn)
    unplayed_score = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(unplayed["version"]),),
    ).fetchone()["all_time"]
    played_score = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(played["version"]),),
    ).fetchone()["all_time"]

    assert 0 < unplayed_score < played_score


# ---------------------------------------------------------------- §7 the two horizons


def test_only_plays_inside_the_window_count_on_the_recent_horizon(conn):
    # source: scoring-H.md §7.1's table -- "Plays counted: all | within the window"
    group = builders.make_group(conn, ["t1"])
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(200))  # outside window
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(30))  # inside

    all_time = scoring._fetch_version_inputs(conn, False, _now(), [])
    recent = scoring._fetch_version_inputs(conn, True, _now(), [])
    assert all_time[group["version"]]["W"] == pytest.approx(2.0)
    assert recent[group["version"]]["W"] == pytest.approx(1.0)


def test_memberships_count_by_when_they_were_added_not_by_being_live(conn):
    """A live membership added 200 days ago must not count on the recent
    horizon, even though it is still live -- "live during the window" would
    make nearly every membership ever count."""
    # source: scoring-H.md §7.1 -- "Memberships count by `added_at` inside
    # the window because membership is cumulative -- a 3-year-old add
    # sitting in a stale playlist is not a recent signal, and 'live during
    # the window' would be nearly every membership ever."
    group = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, track_id="t1", added_at=builders.days_ago(200))

    all_time = scoring._fetch_version_inputs(conn, False, _now(), [])
    recent = scoring._fetch_version_inputs(conn, True, _now(), [])
    assert all_time[group["version"]]["M"] == 1
    assert recent[group["version"]]["M"] == 0


def test_tenure_counts_generations_that_began_in_the_window_not_ones_that_overlap_it(conn):
    """Generation 1's SPAN reaches into the 90-day window (it runs from
    200d ago until generation 2 starts, 30d ago) even though it did not
    BEGIN there. An "overlaps" implementation would count it; "began within"
    must not."""
    # source: scoring-H.md §7.1 -- "**Tenure counts generations that began
    # within the window, not generations that overlap it** ... measured at
    # the 90-day window on 2026-08-14, *began within* gives generations
    # {35, 36, 37} while *overlaps* gives {34, 35, 36, 37}."
    playlist_1 = builders.make_playlist(conn)
    builders.make_generation(conn, ordinal=1, playlist_id=playlist_1)
    group = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, playlist_id=playlist_1, track_id="t1", added_at=builders.days_ago(200))

    playlist_2 = builders.make_playlist(conn)
    builders.make_generation(conn, ordinal=2, playlist_id=playlist_2)
    builders.make_membership(conn, playlist_id=playlist_2, track_id="anchor2", added_at=builders.days_ago(30))

    now = _now()
    recent_ordinals = scoring._recent_ordinals(conn, now)
    assert recent_ordinals == [2]  # generation 1 began outside the window, generation 2 within it

    recent = scoring._fetch_version_inputs(conn, True, now, recent_ordinals)
    all_time = scoring._fetch_version_inputs(conn, False, now, recent_ordinals)
    assert all_time[group["version"]]["T"] == 1
    assert recent[group["version"]]["T"] == 0


def test_the_recent_horizon_is_blended_toward_all_time(conn):
    """Two versions, both entirely inactive in the 90-day window (one play
    each, 200 days ago, with different weights so their all-time scores
    differ). recent_windowed is 0 for both, since nothing they did falls
    inside the window -- so if the blend were dropped, both recents would
    tie at exactly 0, the "one enormous tie" §7.1a exists to prevent."""
    # source: scoring-H.md §7.1a -- "recent = (1 − RECENT_ALLTIME_BLEND)
    # ·recent_windowed + RECENT_ALLTIME_BLEND·all_time"
    g1 = builders.make_group(conn, ["t1"])
    builders.make_play(conn, track_id="t1", ts=builders.days_ago(200), ms_played=210_000)

    g2 = builders.make_group(conn, ["t2"])
    builders.make_play(conn, track_id="t2", ts=builders.days_ago(200), ms_played=105_000)

    now = _now()
    all_time, recent = scoring._version_horizons(conn, now, [])
    v1, v2 = g1["version"], g2["version"]

    assert recent[v1] > 0
    assert recent[v2] > 0
    assert recent[v1] != pytest.approx(recent[v2])
    assert recent[v1] == pytest.approx(scoring.RECENT_ALLTIME_BLEND * all_time[v1])
    assert recent[v2] == pytest.approx(scoring.RECENT_ALLTIME_BLEND * all_time[v2])


def test_both_horizons_use_the_same_shrinkage(conn):
    """Two versions, both with a membership added 5 days ago (so both count
    on the recent horizon) and different play weights, so their recent raw
    scores differ from each other and from the shared baseline -- meaning
    shrinkage actually moves both. Hand-derived independently of scoring.py,
    using the identical K_SHRINK=3.0/SHRINK_MAX=0.5 the all-time horizon
    uses: vX -> 0.376162, vY -> 0.306308. A "no shrinkage on recent"
    implementation would instead leave each at its raw, unshrunk value:
    0.394324 and 0.254615."""
    # source: scoring-H.md §7.1b -- "Both horizons use the same shrinkage ...
    # `recent` uses the identical `K_SHRINK = 3.0` / `SHRINK_MAX = 0.5` as
    # `all_time`. There is no recent-specific shrink parameter."
    gx = builders.make_group(conn, ["tx"])
    builders.make_membership(conn, track_id="tx", added_at=builders.days_ago(5))
    builders.make_play(conn, track_id="tx", ts=builders.days_ago(5), ms_played=210_000)  # full play

    gy = builders.make_group(conn, ["ty"])
    builders.make_membership(conn, track_id="ty", added_at=builders.days_ago(5))
    builders.make_play(conn, track_id="ty", ts=builders.days_ago(5), ms_played=42_000)  # 0.2 weight

    now = _now()
    recent_rows = scoring._fetch_version_inputs(conn, True, now, [])
    recent_windowed, _ = scoring._score_all(recent_rows)

    assert recent_windowed[gx["version"]] == pytest.approx(0.376162, abs=1e-5)
    assert recent_windowed[gy["version"]] == pytest.approx(0.306308, abs=1e-5)
    # neither collapsed to its unshrunk raw value
    assert recent_windowed[gx["version"]] != pytest.approx(0.394324, abs=1e-4)
    assert recent_windowed[gy["version"]] != pytest.approx(0.254615, abs=1e-4)


# ---------------------------------------------------- post-P mutation sweep
#
# Every test below was written from a surviving mutation, not from a reading
# of the code: the bounded sweep over scoring.py / canonical.py / snapshot.py /
# roundtrip.py killed 364 of 372 mutants, and these close the ones that
# mattered. See docs/codebase-health/post_P_sweep.md.


def test_the_recent_blend_weights_the_windowed_half_too(conn):
    """The version above it asserts the same §7.1a formula and **cannot fail**
    against a wrong weight on `recent_windowed`, because both its fixtures are
    inactive in the window: the term is `(1 - BLEND) * 0`, which is 0 for any
    coefficient at all. This one gives the version real in-window activity, so
    the coefficient is load-bearing.

    Asserted as a convex combination rather than a literal, because that is
    what §7.1a's formula *is* — two weights summing to 1 — and it is the
    property a wrong coefficient breaks: with `recent_windowed` above
    `all_time`, doubling its weight pushes the result past the larger of the
    two, which no weighted average of them can reach.
    """
    # source: scoring-H.md §7.1a -- "recent = (1 - RECENT_ALLTIME_BLEND)
    # ·recent_windowed + RECENT_ALLTIME_BLEND·all_time". Found by the post-P
    # mutation sweep: `(1 - BLEND)` -> `(2 - BLEND)` survived the whole suite.
    group = builders.make_group(conn, ["t1"])
    # Dense activity, all of it inside the 90-day window: the recent horizon
    # sees the same plays over a window-clamped exposure, so recent_windowed
    # comes out above all_time rather than below it.
    for days in (5, 10, 15, 20, 25):
        builders.make_play(conn, track_id="t1", ts=builders.days_ago(days))

    now = _now()
    all_time, recent = scoring._version_horizons(conn, now, [])
    windowed, _ = scoring._score_all(scoring._fetch_version_inputs(conn, True, now, []))
    vid = group["version"]

    # The premise the older test lacks -- without this the assertion below
    # holds for any coefficient, and the test is decoration.
    assert windowed[vid] > 0

    # And the degeneracy the older fixture fell into, named explicitly: with
    # recent_windowed at 0 the whole formula collapses to BLEND * all_time,
    # which is what made a wrong coefficient invisible there.
    assert recent[vid] != pytest.approx(scoring.RECENT_ALLTIME_BLEND * all_time[vid])
    assert recent[vid] == pytest.approx(
        (1 - scoring.RECENT_ALLTIME_BLEND) * windowed[vid]
        + scoring.RECENT_ALLTIME_BLEND * all_time[vid]
    )


def test_a_generation_that_began_exactly_at_the_window_edge_counts_as_recent(conn):
    # source: scoring-H.md §7.1 -- tenure counts generations that "began
    # within the window". The test above proves 200d out / 30d in; this is
    # the boundary itself, which `>=` includes and `>` does not. Found by the
    # post-P sweep: `started_at >= win` -> `> win` survived.
    playlist = builders.make_playlist(conn)
    builders.make_generation(conn, ordinal=1, playlist_id=playlist)
    builders.make_group(conn, ["t1"])
    now = _now()
    edge = (now - timedelta(days=scoring.RECENT_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    builders.make_membership(conn, playlist_id=playlist, track_id="t1", added_at=edge)

    assert scoring._recent_ordinals(conn, now) == [1]


def test_a_first_opportunity_exactly_at_the_window_start_gives_the_full_window(conn):
    """`fo < win` and `fo <= win` are **the same function** at this point, and
    that is the thing worth writing down: clamping a first opportunity that
    already equals the window start assigns it the value it already had. The
    post-P sweep flagged `<` -> `<=` as a surviving mutant here; it is an
    equivalent mutant, not a gap, so no test can kill it and none should try.

    This one exists to pin the boundary's *answer* -- 90, the full window --
    so that a future change to either side of the comparison has to keep it.
    """
    # source: scoring-H.md §7.1 -- "Exposure E: ... clamped to the window".
    now = _now()
    win = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert scoring._first_opportunity_days(now, True, win, win) == 90


def test_the_recent_window_is_ninety_days_at_its_boundary(conn):
    # source: scoring-H.md §7 -- the recent horizon is a 90-day window.
    # Asserted at the boundary rather than by reading the constant: a play at
    # 89 days is inside it and one at 91 days is not, which is the smallest
    # fixture that pins the number itself. Found by the post-P sweep:
    # RECENT_WINDOW_DAYS 90 -> 91 survived.
    inside = builders.make_group(conn, ["t-inside"])
    outside = builders.make_group(conn, ["t-outside"])
    builders.make_play(conn, track_id="t-inside", ts=builders.days_ago(89))
    builders.make_play(conn, track_id="t-outside", ts=builders.days_ago(91))

    rows = scoring._fetch_version_inputs(conn, True, _now(), [])

    assert rows[inside["version"]]["R"] > 0
    assert rows[outside["version"]]["R"] == 0


def test_the_subtier_own_score_counts_the_generations_its_tracks_were_in(conn):
    # source: scoring-H.md §6 -- a recording/release/track's own score is
    # computed "over their own narrower track set" from §4's inputs, tenure
    # included; only the shrinkage is dropped (§4.4). That tenure term is a
    # second query from the version tier's -- _fetch_own_inputs, not
    # _fetch_version_inputs -- and only the version one was asserted, so
    # emptying the own-tier tenure CTE survived the post-P sweep.
    playlist = builders.make_playlist(conn)
    builders.make_generation(conn, ordinal=1, playlist_id=playlist)
    present = builders.make_group(conn, ["t-present"])
    absent = builders.make_group(conn, ["t-absent"])
    builders.make_membership(
        conn, playlist_id=playlist, track_id="t-present", added_at=builders.days_ago(200)
    )

    rows = scoring._fetch_own_inputs(conn, "recording", False, _now(), [])

    # Paired, so an implementation returning a constant fails one or the other.
    assert rows[present["recording"]]["T"] == 1
    assert rows[absent["recording"]]["T"] == 0
