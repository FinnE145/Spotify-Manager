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
import entities
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


def test_generation_spans_started_at_is_the_earliest_live_membership(conn):
    # source: generations-B.md §Spans -- "starts at the EARLIEST added_at of
    # its LIVE memberships." Both halves of that need their own fixture row,
    # and the second was missing until session 4's Verify (P2-008): with one
    # live membership, MIN and MAX are the same row, so `MAX(m.added_at)` --
    # the plain misreading of "starts at" -- passed the whole suite. So:
    # a removed row EARLIER than every live one (which a missing
    # `removed_at IS NULL` would wrongly pick), and two live rows at
    # different dates (so only MIN gets the answer right).
    p1 = _gen(conn, 1)
    t1 = builders.make_track(conn, "t1")
    t2 = builders.make_track(conn, "t2")
    t3 = builders.make_track(conn, "t3")
    _present(conn, p1, t1, added_at="2026-01-01T00:00:00Z", removed_at="2026-01-02T00:00:00Z")
    _present(conn, p1, t2, added_at="2026-03-01T00:00:00Z")
    _present(conn, p1, t3, added_at="2026-05-01T00:00:00Z")

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


def test_generation_spans_ordered_by_ordinal_not_by_name_or_playlist_id(conn):
    # source: generations.py's generation_spans docstring -- "Ordered
    # per-generation rows", ordered by ordinal.
    #
    # Not "not insertion order", which this cannot test and no fixture can
    # (P2-008): `generation.ordinal` is INTEGER PRIMARY KEY, so it *is* the
    # rowid, and a bare table scan comes back in ordinal order however the
    # rows went in -- dropping the ORDER BY entirely passes. What can go
    # wrong is ordering by one of the other two columns in the query, so the
    # names and playlist ids here sort in the exact reverse of the ordinals
    # and the assertion names both.
    for ordinal, playlist_id in ((1, "zzz-playlist"), (2, "mmm-playlist"), (3, "aaa-playlist")):
        _gen(conn, ordinal, playlist_id=playlist_id)
        conn.execute(
            "UPDATE snapshot SET name = ? WHERE playlist_id = ?",
            (f"v{4 - ordinal}.0.0", playlist_id),
        )
        _present(
            conn,
            playlist_id,
            builders.make_track(conn, f"t{ordinal}"),
            added_at=f"2026-0{ordinal}-01T00:00:00Z",
        )
    conn.commit()

    spans = generations.generation_spans(conn)

    assert [s["ordinal"] for s in spans] == [1, 2, 3]
    assert [s["playlist_id"] for s in spans] == ["zzz-playlist", "mmm-playlist", "aaa-playlist"]
    assert [s["name"] for s in spans] == ["v3.0.0", "v2.0.0", "v1.0.0"]


def test_generation_spans_name_comes_from_snapshot(conn):
    # source: generations-B.md 'Display resolution' -- a generation's name is
    # the playlist's, resolved from `snapshot`, not stored on `generation`.
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
    # source: generations-B.md 'Runs, and the three numbers' / the
    # /dev/generations list -- carried/new is a split of the same total, so
    # new_in is whatever carried_in is not.
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
    # source: generations-B.md 'Runs, and the three numbers' -- a single
    # ordinal is a run of length 1, which is what "two length-1 runs" needs.
    assert generations.runs({5}) == [(5, 5)]


def test_runs_unsorted_input_still_collapses_correctly():
    # source: generations-B.md 'Runs, and the three numbers' -- runs are over a
    # *set* of ordinals, so input order cannot matter.
    assert generations.runs({10, 5, 7, 6}) == [(5, 7), (10, 10)]


def test_runs_all_consecutive_is_one_run():
    # source: generations-B.md 'Runs, and the three numbers' -- "a group present
    # in 5, 6, 7, 10 has two runs"; fully consecutive is the one-run case.
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
    # source: generations-B.md 'Rollup tier' -- tenure is reported at version or
    # song tier, and two versions of one song collapse to a single song row.
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
    # characterization -- the empty-list early return that keeps the IN ()
    # placeholder list from being built at all.
    assert generations.presence_for_tracks(conn, []) == []


def test_presence_for_tracks_returns_sorted_distinct_ordinals(conn):
    # source: generations-B.md 'What counts as present' -- presence is a set of
    # ordinals; the entity pages render it as a strip, so it must be ordered.
    ta = builders.make_track(conn, "ta")
    p3 = _gen(conn, 3)
    p1 = _gen(conn, 1)
    _present(conn, p3, ta, added_at="2026-03-01T00:00:00Z")
    _present(conn, p1, ta, added_at="2026-01-01T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert generations.presence_for_tracks(conn, [ta]) == [1, 3]


def test_presence_for_tracks_a_track_with_no_membership_is_absent(conn):
    # source: generations-B.md 'What counts as present' -- presence comes from
    # `membership`, so a track in no generation playlist is present nowhere.
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
    # source: generations-B.md 'Detecting a new generation' -- a candidate's
    # major must be "not already a `generation.ordinal`".
    builders.make_generation(conn, ordinal=37, playlist_id="p-existing")
    conn.execute("UPDATE snapshot SET name = 'v37.0.0' WHERE playlist_id = 'p-existing'")
    conn.commit()

    assert generations.pending_new_generation(conn) is None


def test_pending_new_generation_excludes_declined(conn):
    # source: generations-B.md 'Detecting a new generation' -- "whose
    # `generation_declined` is 0"; No means "stop asking".
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
    # source: generations-B.md 'Detecting a new generation' -- detection is a
    # query over `snapshot`, so with no matching name there is no candidate.
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
    # source: generations-B.md 'Detecting a new generation' -- the ordinal comes
    # from the playlist's name, so a playlist with no snapshot row has none.
    with pytest.raises(ValueError):
        generations.confirm_generation(conn, "no-such-playlist")


def test_confirm_generation_raises_for_non_matching_name(conn):
    # source: generations-B.md 'Detecting a new generation' -- the name must
    # match `^v(\d+)\.\d+\.\d+$`; the ordinal is read out of it.
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
    # source: generations-B.md 'Detecting a new generation' -- "**No** -> set
    # `snapshot.generation_declined = 1`, and stop asking".
    builders.make_playlist(conn, "p-37", name="v37.0.0")
    assert generations.pending_new_generation(conn) is not None

    generations.decline_generation(conn, "p-37")
    conn.commit()

    assert generations.pending_new_generation(conn) is None


def test_decline_generation_does_not_commit(conn):
    # source: generations.py's module contract (CLAUDE.md codebase map) --
    # "callers commit those"; a second connection must not see the write.
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


# -- generation_view ------------------------------------------------------
#
# Extracted out of app.py's playlist_page in P3 session 2
# (P3_refactor.md §4.1). The route still owns ?generation=1 and ?tier=;
# this owns the split itself.


def test_generation_view_splits_carried_from_new_against_the_previous_generation(conn):
    # source: generations-B.md -- a generation's groups divide into those
    # carried over from the one before it and those new to it. The fixture
    # gives generation 2 one of each *and* leaves a group behind in
    # generation 1, so "carried" cannot be satisfied by returning either
    # whole set: this generation holds {carried, new}, the previous holds
    # {carried, dropped}.
    for track_id in ("t-carried", "t-new", "t-dropped"):
        builders.make_track(conn, track_id)
    canonical.ensure_track_groups(conn)
    conn.commit()

    first = _gen(conn, 1)
    second = _gen(conn, 2)
    _present(conn, first, "t-carried", position=0)
    _present(conn, first, "t-dropped", position=1)
    _present(conn, second, "t-carried", position=0)
    _present(conn, second, "t-new", position=1)

    view = generations.generation_view(conn, 2, "version")

    assert [g["track_id"] for g in view["carried"]] == ["t-carried"]
    assert [g["track_id"] for g in view["new"]] == ["t-new"]


def test_generation_view_treats_the_first_generation_as_entirely_new(conn):
    # source: generations-B.md -- generation 1 has no predecessor, so
    # everything in it is new rather than carried. The guard is `if idx > 0
    # else set()`; without it, spans[-1] silently compares generation 1
    # against the *newest* generation, which here shares its only group and
    # would report it carried.
    builders.make_track(conn, "t-first")
    canonical.ensure_track_groups(conn)
    conn.commit()

    first = _gen(conn, 1)
    last = _gen(conn, 2)
    _present(conn, first, "t-first", position=0)
    _present(conn, last, "t-first", position=0)

    view = generations.generation_view(conn, 1, "version")

    assert view["carried"] == []
    assert [g["track_id"] for g in view["new"]] == ["t-first"]


def test_generation_view_reports_survived_out_as_none_only_for_the_newest(conn):
    # source: generations.generation_view's docstring -- survived_out is
    # None for the newest generation, "which has no next to survive into,
    # and which the template renders differently from a genuine zero". The
    # fixture makes both cases available at once: generation 1 survives
    # into 2 with zero overlap, and 2 is the newest. A count-only
    # implementation reports 0 for both, which reads as "everything was
    # dropped" on a page that simply has nothing to look forward to.
    for track_id in ("t-early", "t-late"):
        builders.make_track(conn, track_id)
    canonical.ensure_track_groups(conn)
    conn.commit()

    first = _gen(conn, 1)
    second = _gen(conn, 2)
    _present(conn, first, "t-early", position=0)
    _present(conn, second, "t-late", position=0)

    assert generations.generation_view(conn, 1, "version")["survived_out"] == 0
    assert generations.generation_view(conn, 2, "version")["survived_out"] is None


def test_generation_view_at_song_tier_collapses_two_versions_of_one_song(conn):
    # source: generations-B.md -- tier is a whitelisted column lookup, and
    # song vs version is a real difference rather than a label: two version
    # groups under one song count as two rows at version tier and one at
    # song tier. Same fixture, both tiers, so a hard-coded version_id
    # cannot pass.
    first_group = builders.make_group(conn, ["t-v1"])
    builders.make_group(conn, ["t-v2"], song=first_group["song"])
    conn.commit()

    playlist = _gen(conn, 1)
    _present(conn, playlist, "t-v1", position=0)
    _present(conn, playlist, "t-v2", position=1)

    assert len(generations.generation_view(conn, 1, "version")["new"]) == 2
    assert len(generations.generation_view(conn, 1, "song")["new"]) == 1


def test_generation_view_rejects_a_tier_that_is_not_a_column(conn):
    # source: CLAUDE.md -- tier is "a whitelisted column lookup, never
    # interpolated", and the column goes into an f-string here. The route
    # normalizes ?tier= before calling, so this pins the module's own
    # guard rather than the route's.
    _gen(conn, 1)

    with pytest.raises(ValueError):
        generations.generation_view(conn, 1, "release_id; DROP TABLE generation")


def test_generation_view_reports_the_ordinal_it_was_asked_for(conn):
    # source: P3_refactor.md §4.1 -- the route parses ?generation=1 and
    # hands the ordinal in; entity_playlist.html renders it back as the
    # section heading ("Generation N"). Asserted at two ordinals, so a
    # constant -- or the newest generation, the plausible wrong default --
    # fails on one of them. This is the only key nothing else in the payload
    # would expose: carried/new/survived_out are all *derived* from the
    # ordinal inside the function, so they stay right when it is dropped.
    _gen_chain(conn, 3)

    assert generations.generation_view(conn, 1, "version")["ordinal"] == 1
    assert generations.generation_view(conn, 3, "version")["ordinal"] == 3


def test_generation_view_returns_the_span_of_that_generation_not_another(conn):
    # source: generations-B.md §Spans -- `span` is the dated range the page
    # prints under the heading, picked as spans[idx] where idx is *this*
    # generation's position. The middle of three is requested deliberately:
    # spans[0], spans[-1] and both off-by-ones name a different generation
    # than the correct answer, which no other assertion in this file would
    # notice, since the carried/new split is computed from the ordinal
    # rather than from the span.
    _gen_chain(conn, 3)

    span = generations.generation_view(conn, 2, "version")["span"]

    assert span["ordinal"] == 2
    assert span["started_at"] == "2026-02-01T00:00:00Z"
    assert span["ended_at"] == "2026-03-01T00:00:00Z"


# -- entities.tenure_page -- /dev/generations/tenure's read path ------------
#
# The function lives in `entities.py` rather than here, and that is a
# deliberate exception recorded as P3-006: it ranks every row by score before
# paginating, `scoring.py` imports this module, and `generations -> scoring`
# would close a cycle. Its tests live here, with the tenure fixtures they are
# built from and where anyone looking for tenure will look.
#
# Session 3's mutation sweep found `sort` observable only by the golden
# baseline, which is deleted at the end of P3.


def test_tenure_page_ranks_by_score_before_it_paginates(conn, monkeypatch):
    # source: docs/specs/scoring-H.md §11.1 -- score is "add score as a sort
    # column" for this page, and the sort runs before pagination. The
    # highest-scoring group is built LAST, so it is last by group_id, which
    # is both the insertion order and the documented tiebreak: an
    # implementation that slices before sorting leaves it off page 1
    # entirely, and one that ignores score puts it third.
    #
    # The page size is monkeypatched rather than built around: the rule under
    # test is "sort, then slice", and proving it at a boundary of 2 needs
    # three groups where proving it at 100 needs 101.
    monkeypatch.setattr(entities, "_TENURE_PAGE_SIZE", 2)
    playlists = _gen_chain(conn, 1)
    groups = []
    for i, track in enumerate(("ta", "tb", "tc")):
        builders.make_track(conn, track)
        groups.append(builders.make_group(conn, [track, f"{track}-2"]))
        _present(conn, playlists[0], track, added_at="2026-01-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()
    for group, score in zip(groups, (10.0, 20.0, 90.0)):
        builders.make_score(conn, "version", group["version"], all_time=score)

    data = entities.tenure_page(conn, "version", "score", 1)

    assert data["total_pages"] == 2
    assert data["rows"][0]["group_id"] == groups[2]["version"]


def test_an_unrecognised_sort_falls_back_to_tenure_and_reports_the_fallback(conn):
    # source: the route variant in tests/routes_catalog.py -- "an
    # unrecognised sort falls back to 'tenure' rather than reaching SQL".
    # The returned `sort` is what the template writes into its own sort
    # links, so a fallback that ordered correctly but echoed the junk back
    # would build every link on the page around `?sort=;drop`.
    playlists = _gen_chain(conn, 1)
    builders.make_group(conn, ["ta", "tb"])
    _present(conn, playlists[0], "ta", added_at="2026-01-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert entities.tenure_page(conn, "version", ";drop", 1)["sort"] == "tenure"
    assert entities.tenure_page(conn, "version", "runs", 1)["sort"] == "runs"


def test_the_page_number_is_clamped_into_range_and_returned_normalized(conn):
    # source: characterization of the pager -- `page` comes back in the
    # kwargs because generations_tenure.html renders it, so a page past the
    # end has to become a real page rather than an empty slice labelled 9.
    playlists = _gen_chain(conn, 1)
    builders.make_group(conn, ["ta", "tb"])
    _present(conn, playlists[0], "ta", added_at="2026-01-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert entities.tenure_page(conn, "version", "tenure", 9)["page"] == 1
    assert entities.tenure_page(conn, "version", "tenure", 0)["page"] == 1


def test_rows_carry_a_representative_and_every_ordinal_present_in(conn):
    # source: generations-B.md's tenure table -- each row renders a
    # representative track and a strip of one cell per generation. The group
    # is present in 1 and 3 but not 2, so present_ordinals is the expanded
    # run set rather than the endpoints: {1, 3}, not {1, 2, 3}.
    playlists = _gen_chain(conn, 3)
    # Both members carry the same name, so the assertion holds whichever one
    # the representative election picks -- this is testing that a
    # representative is attached and rendered, not which track wins it.
    builders.make_track(conn, "ta", name="Cornelia Street")
    builders.make_track(conn, "tb", name="Cornelia Street")
    builders.make_group(conn, ["ta", "tb"])
    for ordinal in (1, 3):
        _present(conn, playlists[ordinal - 1], "ta", added_at=f"2026-0{ordinal}-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    version_id = canonical.groups_for_track(conn, "ta")["version"]
    rows = entities.tenure_page(conn, "version", "tenure", 1)["rows"]
    row = {r["group_id"]: r for r in rows}[version_id]

    assert row["present_ordinals"] == {1, 3}
    assert row["representative"]["name"] == "Cornelia Street"


def test_the_generation_count_is_every_generation_not_every_row(conn):
    # source: generations_tenure.html renders one strip cell per generation,
    # so this counts the spans rather than the tenure rows. The fixture makes
    # the two numbers differ (3 generations, 1 group with tenure).
    playlists = _gen_chain(conn, 3)
    builders.make_group(conn, ["ta", "tb"])
    _present(conn, playlists[0], "ta", added_at="2026-01-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()

    data = entities.tenure_page(conn, "version", "tenure", 1)

    assert data["generation_count"] == 3
    assert len(data["spans"]) == 3


def test_tenure_page_reports_the_tier_it_was_asked_for_alongside_that_tier_s_ids(conn):
    # source: generations_tenure.html:52 -- `entity_link(tier, r.group_id,
    # ...)`. The echoed tier and the rows' group ids are one pair, not two
    # independent values: group ids are per-tier, so a tier that disagrees
    # with the ids it is rendered beside links every row to a *different*
    # group at the wrong tier, and takes the tier toggle (:21/:23) and every
    # sort and pager link with it.
    #
    # Found by P3's Verify pass (P3-008): hardcoding `"tier": "version"`
    # survived the whole suite, because every other tenure_page test asks
    # for "version" and so cannot tell an echo from a constant. This one
    # asks for "song" and asserts the pair, which is why it needs both
    # halves rather than just `data["tier"] == "song"`.
    playlists = _gen_chain(conn, 1)
    builders.make_group(conn, ["ta", "tb"])
    _present(conn, playlists[0], "ta", added_at="2026-01-15T00:00:00Z")
    canonical.ensure_track_groups(conn)
    conn.commit()
    groups = canonical.groups_for_track(conn, "ta")

    data = entities.tenure_page(conn, "song", "tenure", 1)
    listed = [r["group_id"] for r in data["rows"]]

    assert data["tier"] == "song"
    assert groups["song"] in listed
    assert groups["version"] not in listed
