"""Recording/release/track's own-score blend (docs/specs/scoring-H.md §6).

This file carries P1-021's floor item: the subtier's own-score component
must not be shrunk toward a bucket baseline. Written as the negative case,
per P1-021's `Test:` field -- the positive ("no shrinkage applied") is what
is easy to accidentally reintroduce later.
"""

import pytest

import builders
import scoring
from test_scoring_version import bucket_a_library


def test_the_subtier_blend_leaves_the_own_score_unshrunk(conn):
    """a1 and a3 are each alone in their version, so their own track set IS
    the version's track set and score_own == raw exactly. The blend is
    therefore score(track) = 0.95*score(version) + 0.05*raw:

      a1: raw=0.00, version=0.10 -> track = 0.095 -> displayed 30.8221
      a3: raw=0.32, version=0.26 -> track = 0.263 -> displayed 51.2835

    a1's raw sits BELOW its version score and a3's sits ABOVE it, so a bug
    that shrinks the own-score before blending cannot hide behind a sign --
    if it did, score_own would equal score(version) exactly and both tracks
    would collapse onto their version's own displayed number (31.6228 /
    50.9902). a2 is not used here: its raw already equals the bucket
    baseline, so shrinking it is a no-op and it discriminates nothing.
    """
    # source: scoring-H.md §6 -- "**`score_own(x)` is raw only (§4.4) -- it
    # does not run §4's shrinkage.**" (P1-021)
    groups = bucket_a_library(conn)
    scoring.recompute(conn)

    a1_track = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'track' AND group_id = ?", ("a1",)
    ).fetchone()["all_time"]
    a3_track = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'track' AND group_id = ?", ("a3",)
    ).fetchone()["all_time"]
    a1_version = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a1"]["version"]),),
    ).fetchone()["all_time"]
    a3_version = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["a3"]["version"]),),
    ).fetchone()["all_time"]

    assert a1_track == pytest.approx(30.8221, abs=1e-3)
    assert a3_track == pytest.approx(51.2835, abs=1e-3)
    assert a1_track != pytest.approx(a1_version, abs=1e-2)
    assert a3_track != pytest.approx(a3_version, abs=1e-2)


def test_all_three_finer_tiers_blend_the_same_way(conn):
    """§6's formula names recording, release and track together -- a1's four
    groups are all singletons over the same one track, so all three finer
    tiers must land on the identical blended value."""
    # source: scoring-H.md §6
    groups = bucket_a_library(conn)
    scoring.recompute(conn)

    group_ids = {
        "recording": str(groups["a1"]["recording"]),
        "release": str(groups["a1"]["release"]),
        "track": "a1",  # track tier uses the raw track_id verbatim, not a canonical_group id
    }
    values = {
        tier: conn.execute(
            "SELECT all_time FROM score WHERE tier = ? AND group_id = ?", (tier, gid)
        ).fetchone()["all_time"]
        for tier, gid in group_ids.items()
    }
    assert values["recording"] == pytest.approx(30.8221, abs=1e-3)
    assert values["release"] == pytest.approx(30.8221, abs=1e-3)
    assert values["track"] == pytest.approx(30.8221, abs=1e-3)


def test_a_finer_tier_never_moves_far_from_its_version_but_still_differs(conn):
    """One version with two tracks of very different play counts, split
    into two recording groups. Each recording must stay within SUBTIER_W of
    its version's normalized score (the "close enough to be a rounding
    error" half of §6) while the two recordings still differ from each
    other (the "differing enough to break ties" half -- a SUBTIER_W=0
    implementation would pass the first half and fail this one)."""
    # source: scoring-H.md §6 -- "any two track objects under one version
    # score almost identically ... while still differing enough to break
    # ties, which is what makes score usable for choosing a version's
    # representative track"
    version_group = builders.make_group(conn, ["t_quiet"])
    builders.make_group(conn, ["t_loud"], version=version_group["version"])
    builders.make_play(conn, track_id="t_loud", ts=builders.days_ago(5), ms_played=210_000)

    scoring.recompute(conn)

    version_score = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'version' AND group_id = ?",
        (str(version_group["version"]),),
    ).fetchone()["all_time"]
    quiet = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'track' AND group_id = ?", ("t_quiet",)
    ).fetchone()["all_time"]
    loud = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'track' AND group_id = ?", ("t_loud",)
    ).fetchone()["all_time"]

    v_norm = scoring._undisplay(version_score)
    assert abs(scoring._undisplay(quiet) - v_norm) <= scoring.SUBTIER_W
    assert abs(scoring._undisplay(loud) - v_norm) <= scoring.SUBTIER_W
    assert loud != pytest.approx(quiet)


def test_the_own_score_is_computed_over_the_finer_tiers_own_track_set(conn):
    """Same fixture as above: the heavily-played track's own recording must
    outscore the quiet one's. This fails if _fetch_own_inputs grouped by
    version_id instead of the tier's own column -- both recordings would
    then read the version's combined inputs and tie."""
    # source: scoring-H.md §6 -- "computed by the same function over their
    # own narrower track set"
    quiet_group = builders.make_group(conn, ["t_quiet"])
    loud_group = builders.make_group(conn, ["t_loud"], version=quiet_group["version"])
    builders.make_play(conn, track_id="t_loud", ts=builders.days_ago(5), ms_played=210_000)

    scoring.recompute(conn)

    quiet = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'recording' AND group_id = ?",
        (str(quiet_group["recording"]),),
    ).fetchone()["all_time"]
    loud = conn.execute(
        "SELECT all_time FROM score WHERE tier = 'recording' AND group_id = ?",
        (str(loud_group["recording"]),),
    ).fetchone()["all_time"]

    assert loud > quiet


def test_the_own_inputs_carry_no_bucket(conn):
    """The own score never runs the bucket machinery -- ratified by P1-021
    (scoring-H.md §6's own-score-is-raw-only note)."""
    # source: characterization, ratified by P1-021
    groups = bucket_a_library(conn)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    own_inputs = scoring._fetch_own_inputs(conn, "track", False, now, [])
    assert set(own_inputs["a1"]) == {"R", "M", "T"}
