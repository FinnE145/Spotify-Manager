"""`generations.py` -- generations and tenure (docs/specs/generations-B.md,
Audited 2026-08-17).

Every function here reads through `generation_presence`, which joins
`track_group` -- so every test calls `canonical.ensure_track_groups(conn)`
(or builds groups directly via `builders.make_group`) before asserting,
exactly as the real read paths must.
"""

import pytest

import builders
import canonical
import generations


def _present(conn, playlist_id, track_id, **kwargs):
    """A live membership -- the thing generation_presence keys on."""
    builders.make_membership(conn, playlist_id=playlist_id, track_id=track_id, **kwargs)


def _gen(conn, ordinal, playlist_id=None):
    """Creates a generation and returns its **playlist id**.

    `builders.make_generation` itself returns the *ordinal* (the caller-
    supplied identity `generations-B.md` keys generations on), not the
    playlist id -- every test here wants the playlist id, to attach
    memberships to it.
    """
    playlist_id = playlist_id or f"gen-playlist-{ordinal}"
    builders.make_generation(conn, ordinal=ordinal, playlist_id=playlist_id)
    return playlist_id


# -- generation_spans -----------------------------------------------------


def test_generation_spans_started_at_ignores_removed_memberships(conn):
    # source: generations-B.md §Spans -- "starts at the earliest added_at of
    # its live memberships." A removed row with an EARLIER added_at must not
    # win -- an implementation missing removed_at IS NULL would pick it.
    p1 = _gen(conn, 1)
    t1 = builders.make_track(conn, "t1")
    t2 = builders.make_track(conn, "t2")
    _present(conn, p1, t1, added_at="2026-01-01T00:00:00Z", removed_at="2026-01-02T00:00:00Z")
    _present(conn, p1, t2, added_at="2026-03-01T00:00:00Z")

    spans = generations.generation_spans(conn)

    assert spans[0]["started_at"] == "2026-03-01T00:00:00Z"


def test_generation_spans_ends_when_next_generation_starts(conn):
    # source: generations-B.md §Spans -- "ends when the next generation
    # starts. ... The active generation's span is open -- it ends today."
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    _present(conn, p1, builders.make_track(conn, "t1"), added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, builders.make_track(conn, "t2"), added_at="2026-06-01T00:00:00Z")

    spans = generations.generation_spans(conn)

    assert spans[0]["ended_at"] == "2026-06-01T00:00:00Z"
    assert spans[1]["ended_at"] is None


def test_generation_spans_skips_a_mid_sequence_empty_generation(conn):
    # source: P1-015's fix, generations.py's own comment -- "a mid-sequence
    # empty generation would desync this span's ended_at ... even though a
    # later, real generation already superseded it." gen 2 has zero live
    # memberships; gen 1's ended_at must reach past it to gen 3's
    # started_at, not fall back to None/"still open".
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    p3 = _gen(conn, 3)
    _present(conn, p1, builders.make_track(conn, "t1"), added_at="2026-01-01T00:00:00Z")
    _present(conn, p3, builders.make_track(conn, "t3"), added_at="2026-06-01T00:00:00Z")

    spans = generations.generation_spans(conn)
    by_ordinal = {s["ordinal"]: s for s in spans}

    assert by_ordinal[2]["started_at"] is None
    assert by_ordinal[1]["ended_at"] == "2026-06-01T00:00:00Z"
    assert by_ordinal[2]["ended_at"] == "2026-06-01T00:00:00Z"


def test_generation_spans_ordered_by_ordinal_not_insertion_order(conn):
    # source: generations.py's generation_spans docstring -- "Ordered
    # per-generation rows." Insert ordinal 3 before ordinal 1 to prove the
    # ordering comes from the ORDER BY, not from insertion order.
    p3 = _gen(conn, 3)
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    for p, o in ((p1, 1), (p2, 2), (p3, 3)):
        _present(conn, p, builders.make_track(conn, f"t{o}"), added_at=f"2026-0{o}-01T00:00:00Z")

    spans = generations.generation_spans(conn)

    assert [s["ordinal"] for s in spans] == [1, 2, 3]


def test_generation_spans_name_comes_from_snapshot(conn):
    p1 = builders.make_generation(conn, ordinal=1, playlist_id="p-named")
    conn.execute("UPDATE snapshot SET name = 'v1.0.0' WHERE playlist_id = ?", (p1,))
    conn.commit()
    _present(conn, p1, builders.make_track(conn, "t1"))

    spans = generations.generation_spans(conn)

    assert spans[0]["name"] == "v1.0.0"


# -- generations() ----------------------------------------------------------


def test_generations_carried_in_counts_a_different_track_id_of_same_version(conn):
    # source: generations-B.md §What counts as present -- "a group is
    # 'carried' if it was present in generation N-1, even if a *different
    # track id* of the same version was the one carried forward." This is
    # the spec's own worked example -- a track-id-based carried_in
    # implementation fails it.
    ta = builders.make_track(conn, "ta")
    tb = builders.make_track(conn, "tb")
    builders.make_group(conn, [ta, tb])  # one version group, two track ids
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, tb, added_at="2026-02-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    rows = generations.generations(conn, tier="version")

    assert rows[1]["carried_in"] == 1
    assert rows[1]["new_in"] == 0


def test_generations_new_in_is_group_count_minus_carried_in(conn):
    ta = builders.make_track(conn, "ta")
    tb = builders.make_track(conn, "tb")
    builders.make_group(conn, [ta])
    builders.make_group(conn, [tb])
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, tb, added_at="2026-02-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    rows = generations.generations(conn, tier="version")

    assert rows[1]["group_count"] == 2
    assert rows[1]["carried_in"] == 1
    assert rows[1]["new_in"] == 1


def test_generations_survived_out_is_none_for_the_last_generation(conn):
    # source: generations.py's generations() docstring -- "survived_out (how
    # many of its groups appear in the next generation)". The last row has
    # no next generation, so this must be None, not 0 -- a consumer that
    # can't distinguish "zero survived" from "no next generation to check"
    # would render both identically.
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    p1 = _gen(conn, 1)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    rows = generations.generations(conn, tier="version")

    assert rows[-1]["survived_out"] is None


def test_generations_tier_song_aggregates_versions(conn):
    # source: generations-B.md §Rollup tier -- "Acoustic and studio cuts are
    # separate tenures; ... The tier is a parameter to the backend function."
    ta = builders.make_track(conn, "ta")
    tb = builders.make_track(conn, "tb")
    song_groups = builders.make_group(conn, [ta])
    builders.make_group(conn, [tb], song=song_groups["song"])  # 2nd version, same song
    p1 = _gen(conn, 1)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p1, tb, added_at="2026-01-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_rows = generations.generations(conn, tier="version")
    song_rows = generations.generations(conn, tier="song")

    assert version_rows[0]["group_count"] == 2
    assert song_rows[0]["group_count"] == 1


def test_generations_rejects_an_invalid_tier(conn):
    # source: generations-B.md §generations.py -- "Reject anything else
    # rather than interpolating it into SQL." A SQL-injection-shaped value
    # must raise, not be interpolated.
    with pytest.raises(ValueError):
        generations.generations(conn, tier="version_id; DROP TABLE track--")


def test_generations_removed_membership_does_not_count_as_present(conn):
    # source: generations-B.md §Removals never happened -- "A track with
    # removed_at set is treated as never having been in that generation at
    # all."
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    p1 = _gen(conn, 1)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z", removed_at="2026-01-05T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    rows = generations.generations(conn, tier="version")

    assert rows[0]["group_count"] == 0


# -- runs() -----------------------------------------------------------------


def test_runs_matches_the_specs_own_example(conn):
    # source: generations-B.md §Runs -- "A group present in 5, 6, 7, 10 has
    # two runs: 5-7 and 10." The spec's own worked example, verbatim.
    assert generations.runs({5, 6, 7, 10}) == [(5, 7), (10, 10)]


def test_runs_single_ordinal():
    assert generations.runs({5}) == [(5, 5)]


def test_runs_unsorted_input_still_collapses_correctly():
    assert generations.runs({10, 5, 7, 6}) == [(5, 7), (10, 10)]


def test_runs_all_consecutive_is_one_run():
    assert generations.runs({1, 2, 3, 4}) == [(1, 4)]


# -- tenures() ----------------------------------------------------------


def _gen_chain(conn, count):
    """count generations, ordinals 1..count, each a day apart."""
    playlists = []
    for i in range(1, count + 1):
        p = _gen(conn, i)
        # Give every generation a real span even with no presence of its own,
        # so ended_at/started_at are never None for a generation this test
        # doesn't otherwise touch.
        filler = builders.make_track(conn, f"filler-{i}")
        _present(conn, p, filler, added_at=f"2026-{i:02d}-01T00:00:00Z")
        playlists.append(p)
    return playlists


def test_tenures_reports_three_distinct_numbers(conn):
    # source: generations-B.md §Runs, and the three numbers table -- tenure
    # (longest run), total_generations (sum of run lengths), run_count
    # (number of runs). Built so all three differ, so confusing any pair of
    # them is caught.
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    playlists = _gen_chain(conn, 6)
    # present in 1,2,3 (run of 3) and 5,6 (run of 2) -- skips 4
    for o in (1, 2, 3, 5, 6):
        _present(conn, playlists[o - 1], ta, added_at=f"2026-{o:02d}-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    tenures = {t["group_id"]: t for t in generations.tenures(conn, tier="version")}
    version_id = canonical.groups_for_track(conn, ta)["version"]

    t = tenures[version_id]
    assert t["tenure"] == 3
    assert t["total_generations"] == 5
    assert t["run_count"] == 2


def test_tenures_first_and_last_ordinal_span_every_presence_not_just_winning_run(conn):
    # source: generations-B.md §Ties for longest run -- "(first_ordinal/
    # last_ordinal are unaffected -- those are the min/max over *every*
    # ordinal the group was ever present in, not just the winning run.)"
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    playlists = _gen_chain(conn, 6)
    # present in 1 (isolated) and 4,5,6 (the winning 3-run)
    for o in (1, 4, 5, 6):
        _present(conn, playlists[o - 1], ta, added_at=f"2026-{o:02d}-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_id = canonical.groups_for_track(conn, ta)["version"]
    t = {t["group_id"]: t for t in generations.tenures(conn, tier="version")}[version_id]

    assert t["tenure"] == 3
    assert t["first_ordinal"] == 1
    assert t["last_ordinal"] == 6


def test_tenures_ties_favour_the_earliest_run(conn):
    # source: generations-B.md §Ties for longest run -- "Ties for longest run
    # favour the earliest one ... The reported days comes from whichever
    # tied run appears first in run order -- i.e. the oldest." Two length-1
    # runs at ordinals 1 and 5, whose generations have DIFFERENT span
    # lengths -- so picking the later run gives a different (wrong) `days`.
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)  # gen 1 spans 1 day
    p5 = _gen(conn, 5)
    p6 = _gen(conn, 6)  # gen 5 spans 30 days
    filler = builders.make_track(conn, "filler")
    _present(conn, p1, filler, added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, filler, added_at="2026-01-02T00:00:00Z")
    _present(conn, p5, filler, added_at="2026-02-01T00:00:00Z")
    _present(conn, p6, filler, added_at="2026-03-03T00:00:00Z")
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p5, ta, added_at="2026-02-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_id = canonical.groups_for_track(conn, ta)["version"]
    t = {t["group_id"]: t for t in generations.tenures(conn, tier="version")}[version_id]

    # gen 1's span is 2026-01-01 -> 2026-01-02 = 1 day.
    assert t["days"] == 1


def test_tenures_days_computed_from_the_actual_span_dates(conn):
    # source: generations-B.md §Tenure in days -- "from the start of the
    # run's first generation to the end of the run's last generation."
    # Computed here by hand from the fixture's own dates, not read off a run.
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    p1 = _gen(conn, 1)
    p2 = _gen(conn, 2)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p2, builders.make_track(conn, "filler"), added_at="2026-01-11T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_id = canonical.groups_for_track(conn, ta)["version"]
    t = {t["group_id"]: t for t in generations.tenures(conn, tier="version")}[version_id]

    assert t["days"] == 10


def test_tenures_active_generation_ended_at_falls_back_to_now(conn):
    # source: generations-B.md §Spans -- "If the run's last generation is the
    # active one, the span ends today, and tenure is still accruing."
    # FROZEN_NOW is 2026-06-15 (conftest.py); started_at must predate it.
    ta = builders.make_track(conn, "ta")
    builders.make_group(conn, [ta])
    p1 = _gen(conn, 1)
    _present(conn, p1, ta, added_at="2026-06-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_id = canonical.groups_for_track(conn, ta)["version"]
    t = {t["group_id"]: t for t in generations.tenures(conn, tier="version")}[version_id]

    # 2026-06-01 -> 2026-06-15 (FROZEN_NOW) = 14 days.
    assert t["days"] == 14


def test_tenures_tier_parameter_selects_song_or_version(conn):
    ta = builders.make_track(conn, "ta")
    tb = builders.make_track(conn, "tb")
    groups = builders.make_group(conn, [ta])
    builders.make_group(conn, [tb], song=groups["song"])
    p1 = _gen(conn, 1)
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    _present(conn, p1, tb, added_at="2026-01-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_tenures = generations.tenures(conn, tier="version")
    song_tenures = generations.tenures(conn, tier="song")

    assert len(version_tenures) == 2
    assert len(song_tenures) == 1


# -- presence_for_tracks ------------------------------------------------


def test_presence_for_tracks_empty_input(conn):
    assert generations.presence_for_tracks(conn, []) == []


def test_presence_for_tracks_returns_sorted_distinct_ordinals(conn):
    ta = builders.make_track(conn, "ta")
    p3 = _gen(conn, 3)
    p1 = _gen(conn, 1)
    _present(conn, p3, ta, added_at="2026-03-01T00:00:00Z")
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert generations.presence_for_tracks(conn, [ta]) == [1, 3]


def test_presence_for_tracks_a_track_with_no_membership_is_absent(conn):
    ta = builders.make_track(conn, "ta")
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert generations.presence_for_tracks(conn, [ta]) == []


# -- pending_new_generation -----------------------------------------------


def test_pending_new_generation_surfaces_lowest_major_first(conn):
    # source: generations-B.md §Detecting a new generation -- "collects
    # every candidate but surfaces only the lowest-major one at a time."
    # Insert the HIGHER major first, to prove it's sorted, not first-found.
    builders.make_playlist(conn, "p-high", name="v40.0.0")
    builders.make_playlist(conn, "p-low", name="v38.0.0")

    pending = generations.pending_new_generation(conn)

    assert pending["ordinal"] == 38
    assert pending["playlist_id"] == "p-low"


def test_pending_new_generation_excludes_existing_ordinal(conn):
    builders.make_generation(conn, ordinal=37, playlist_id="p-existing")
    conn.execute("UPDATE snapshot SET name = 'v37.0.0' WHERE playlist_id = 'p-existing'")
    conn.commit()

    assert generations.pending_new_generation(conn) is None


def test_pending_new_generation_excludes_declined(conn):
    builders.make_playlist(conn, "p-declined", name="v37.0.0")
    generations.decline_generation(conn, "p-declined")
    conn.commit()

    assert generations.pending_new_generation(conn) is None


@pytest.mark.parametrize(
    "name", ["v37.0", "Finn All", "v37.0.0 extra", "xv37.0.0", "v37.0.0.0"]
)
def test_pending_new_generation_regex_is_anchored(conn, name):
    # source: generations-B.md §Detecting a new generation -- the regex is
    # "^v(\\d+)\\.\\d+\\.\\d+$", fully anchored. Near-misses must not match.
    builders.make_playlist(conn, "p-1", name=name)

    assert generations.pending_new_generation(conn) is None


def test_pending_new_generation_none_when_no_candidates(conn):
    assert generations.pending_new_generation(conn) is None


# -- confirm_generation / decline_generation -------------------------------


def test_confirm_generation_derives_ordinal_from_playlist_name(conn):
    # source: generations-B.md §Detecting a new generation -- "The ordinal
    # comes from the major number in the name, so it never needs typing."
    builders.make_playlist(conn, "p-37", name="v37.0.0")

    generations.confirm_generation(conn, "p-37")
    conn.commit()

    row = conn.execute(
        "SELECT ordinal FROM generation WHERE playlist_id = ?", ("p-37",)
    ).fetchone()
    assert row["ordinal"] == 37


def test_confirm_generation_raises_for_missing_snapshot_row(conn):
    with pytest.raises(ValueError):
        generations.confirm_generation(conn, "no-such-playlist")


def test_confirm_generation_raises_for_non_matching_name(conn):
    builders.make_playlist(conn, "p-bad", name="Not A Generation")

    with pytest.raises(ValueError):
        generations.confirm_generation(conn, "p-bad")


def test_confirm_generation_insert_or_ignore_swallows_a_conflicting_ordinal(conn):
    # source: generations-B.md §Detecting a new generation -- "Known
    # limitation, documented as current behavior (P1-016): the insert is
    # INSERT OR IGNORE -- a conflicting ordinal or playlist id is silently
    # swallowed, and Finn is redirected as if it succeeded."
    builders.make_generation(conn, ordinal=37, playlist_id="p-original")
    builders.make_playlist(conn, "p-conflict", name="v37.0.0")

    generations.confirm_generation(conn, "p-conflict")
    conn.commit()

    row = conn.execute(
        "SELECT playlist_id FROM generation WHERE ordinal = 37"
    ).fetchone()
    assert row["playlist_id"] == "p-original"


def test_confirm_generation_does_not_commit(conn):
    # source: generations-B.md §generations.py -- "Both functions read only;
    # neither commits." (confirm/decline: "Caller commits.") A second
    # connection must not see the row until the caller commits.
    import db

    builders.make_playlist(conn, "p-37", name="v37.0.0")
    generations.confirm_generation(conn, "p-37")

    other = db.connect()
    try:
        row = other.execute(
            "SELECT 1 FROM generation WHERE playlist_id = ?", ("p-37",)
        ).fetchone()
        assert row is None
    finally:
        other.close()


def test_decline_generation_stops_it_being_surfaced_again(conn):
    builders.make_playlist(conn, "p-37", name="v37.0.0")
    assert generations.pending_new_generation(conn) is not None

    generations.decline_generation(conn, "p-37")
    conn.commit()

    assert generations.pending_new_generation(conn) is None


def test_decline_generation_does_not_commit(conn):
    import db

    builders.make_playlist(conn, "p-37", name="v37.0.0")
    generations.decline_generation(conn, "p-37")

    other = db.connect()
    try:
        row = other.execute(
            "SELECT generation_declined FROM snapshot WHERE playlist_id = ?", ("p-37",)
        ).fetchone()
        assert row["generation_declined"] == 0
    finally:
        other.close()
