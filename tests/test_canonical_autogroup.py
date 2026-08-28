"""The deterministic auto-group run: preview, run, undo.

Authority is **`grouping-catch-up-E.md`** §3.2 (what a run writes), §3.3 (the
preview/confirm split) and §3.4 (the run log and the whole-table snapshot),
stamped Audited and amended 2026-08-17 under P1-013.

`canonical_detect.auto_group_candidates` decides *which* groups qualify and is
tested in `test_canonical_detect_queues.py`; this file is about the writing,
the tagging and the restore. The rule itself is only used here to build a
qualifying group and a non-qualifying one.

**Unlike `canonical.py`, these functions commit** (the module says so), so the
tests read back on the same connection without one.
"""

import pytest

import builders
import canonical
import canonical_autogroup as autogroup
import canonical_detect as detect
from test_canonical_detect_rules import make


def qualifying_pair(conn, base="Willow", ids=("ta", "tb"), albums=("Album One", "Album Two")):
    """Two tracks the auto-group rule matches on: same ISRC, same normalized
    title and suffix, same duration, same explicit flag, different albums."""
    isrc = f"ISRC-{base}"
    for track_id, album in zip(ids, albums):
        make(conn, track_id, base, isrc=isrc, duration_ms=200_000, album=album)
    return list(ids)


def reviewed_pairs(conn):
    return {
        (row["track_id_a"], row["track_id_b"])
        for row in conn.execute("SELECT track_id_a, track_id_b FROM reviewed_pair")
    }


def snapshot_counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for _live, table, _columns in autogroup._SNAPSHOT_TABLES
    }


# -- Preview ----------------------------------------------------------------


def test_preview_reports_the_counts_without_writing(conn):
    # source: E §3.3 -- "Preview -- GET /api/canonical/autogroup/preview runs
    # the rule without writing and returns the counts. Render as 'This will
    # resolve 568 of 810 queue items, leaving 242.'"
    qualifying_pair(conn)
    # A second queue item the rule does not close: same title, different
    # durations, so one pair fails.
    make(conn, "tc", "Cardigan", isrc="ISRC-C", duration_ms=200_000)
    make(conn, "td", "Cardigan", isrc="ISRC-C", duration_ms=240_000, album="Album Four")

    result = autogroup.preview(conn)

    assert result == {
        "groups_closed": 1,
        "tracks_affected": 2,
        "queue_total": 2,
        "remaining": 1,
    }
    assert conn.execute("SELECT COUNT(*) FROM auto_group_run").fetchone()[0] == 0
    assert reviewed_pairs(conn) == set()


# -- A run ------------------------------------------------------------------


def test_a_run_writes_one_shared_song_version_and_recording(conn):
    # source: E §3.2 -- "one shared **song** label, one shared **version**
    # label, one shared **recording** label".
    qualifying_pair(conn)

    autogroup.run(conn)

    a = canonical.groups_for_track(conn, "ta")
    b = canonical.groups_for_track(conn, "tb")
    assert a["song"] == b["song"]
    assert a["version"] == b["version"]
    assert a["recording"] == b["recording"]


def test_a_run_keys_the_release_on_the_album(conn):
    """Two albums means two releases; one album means one."""
    # source: E §3.2 -- "**release** label keyed on the normalized album name,
    # so tracks on the same album share a release and tracks on different
    # albums don't".
    qualifying_pair(conn)  # ta / tb, different albums
    qualifying_pair(conn, base="Cardigan", ids=("tc", "td"), albums=("Same LP", "Same LP"))

    autogroup.run(conn)

    assert (
        canonical.groups_for_track(conn, "ta")["release"]
        != canonical.groups_for_track(conn, "tb")["release"]
    )
    assert (
        canonical.groups_for_track(conn, "tc")["release"]
        == canonical.groups_for_track(conn, "td")["release"]
    )


def test_a_run_marks_the_closed_group_reviewed(conn):
    # source: E §3.2 -- "then `mark_reviewed` over the group's tracks", which
    # is what takes the item out of the queue. M §1.3 confirms this caller is
    # not an over-reach: the rule matched on every pair, so every pair
    # genuinely was decided.
    qualifying_pair(conn)

    autogroup.run(conn)

    assert reviewed_pairs(conn) == {("ta", "tb")}
    assert detect.candidate_groups(conn) == []


def test_a_run_leaves_a_non_qualifying_group_alone(conn):
    # source: E §3.2 -- a run closes the qualifying groups and nothing else,
    # so the rest of the queue survives it untouched.
    make(conn, "tc", "Cardigan", isrc="ISRC-C", duration_ms=200_000)
    make(conn, "td", "Cardigan", isrc="ISRC-C", duration_ms=240_000, album="Album Four")
    # As /dev/canonical* does on every request -- a run that closes nothing
    # returns before its own ensure_track_groups() call, so without this the
    # tracks would have no track_group rows to compare at all.
    canonical.ensure_track_groups(conn)
    conn.commit()

    autogroup.run(conn)

    assert (
        canonical.groups_for_track(conn, "tc")["song"]
        != canonical.groups_for_track(conn, "td")["song"]
    )
    assert reviewed_pairs(conn) == set()
    assert len(detect.candidate_groups(conn)) == 1


def test_a_run_tags_the_groups_it_decided(conn):
    # source: E §3.5 / canonical.auto_grouped_ids -- the viewer's badge is
    # driven by canonical_group.auto_run_id, and canonical_autogroup takes the
    # ids "from what apply_partition actually wrote, which includes anything
    # its closure pulled in" rather than by diffing against the snapshot.
    qualifying_pair(conn)

    result = autogroup.run(conn)

    assert result["run_id"] is not None
    assert canonical.groups_for_track(conn, "ta")["song"] in canonical.auto_grouped_ids(conn)


def test_a_run_tags_every_group_it_decided_not_all_but_the_first(conn):
    """The tagging UPDATE is chunked, and the neighbouring test only asks
    about the *song* group -- which is the highest of the five ids a pair
    decides, so it survives a chunk loop that starts one id late. This one
    asserts the whole set.
    """
    # source: E §3.5 -- canonical_group.auto_run_id marks every group the run
    # decided; S_sweep.md §3 -- num at canonical_autogroup.py:122 (col 23).
    # The mutant makes the loop `range(1, len(ids), 500)`, so `ids[0]` -- the
    # lowest decided group id, here one of the two release groups -- is left
    # out of every chunk and never tagged.
    qualifying_pair(conn)  # ta / tb, different albums

    autogroup.run(conn)

    a = canonical.groups_for_track(conn, "ta")
    b = canonical.groups_for_track(conn, "tb")
    decided = set(a.values()) | set(b.values())
    # song + version + recording shared, one release each: five groups.
    assert len(decided) == 5
    assert canonical.auto_grouped_ids(conn) == decided


def test_a_run_records_its_totals_in_the_log(conn):
    # source: E §3.4 -- "auto_group_run(id, started_at, finished_at,
    # groups_closed, tracks_affected)", plus the `undone_at` column P1-013
    # added to that list.
    qualifying_pair(conn)

    result = autogroup.run(conn)

    row = conn.execute("SELECT * FROM auto_group_run WHERE id = ?", (result["run_id"],)).fetchone()
    assert (row["groups_closed"], row["tracks_affected"]) == (1, 2)
    assert row["finished_at"] is not None
    assert row["undone_at"] is None


def test_a_run_with_nothing_to_do_writes_no_log_row(conn):
    # source: canonical_autogroup.run's docstring -- "Returns the same shape
    # as preview(), plus the run id (None when there was nothing to do)". A
    # logged no-op run would also claim the undo slot and displace a real
    # run's snapshot.
    make(conn, "tc", "Cardigan", isrc="ISRC-C", duration_ms=200_000)
    make(conn, "td", "Cardigan", isrc="ISRC-C", duration_ms=240_000, album="Album Four")

    result = autogroup.run(conn)

    assert result["run_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM auto_group_run").fetchone()[0] == 0
    assert snapshot_counts(conn) == dict.fromkeys(snapshot_counts(conn), 0)


def test_a_run_pays_the_tier_cleanup_once_for_the_whole_batch(conn, monkeypatch):
    """`_cleanup_tier` is a full `canonical_group` x `track_group` LEFT JOIN
    per tier, and it is what made the 568-group run take 11.75s instead of
    1.15s. So `run()` passes `cleanup=False` to every `apply_partition` and
    settles the debt with one `cleanup_all_tiers` afterwards.

    Nothing about the *rows* records that: the final table state is
    byte-identical either way (checked by dumping `canonical_group`,
    `track_group` and `reviewed_pair` under both), because a stale pin is
    only ever stale on a group `apply_partition` has just emptied, which both
    orderings delete. The number of passes is the property, so the number of
    passes is what this counts -- three closable groups and four passes, not
    sixteen.
    """
    # source: canonical.apply_partition's docstring -- "Only a caller that
    # runs cleanup_all_tiers() itself once its batch is done may pass it";
    # S_sweep.md §3 -- false at canonical_autogroup.py:105. The mutant makes
    # the per-group call `cleanup=True`, which cleans inside every one of the
    # three applies as well as once at the end: 16 passes rather than 4.
    tiers = []
    real_cleanup_tier = canonical._cleanup_tier

    def counting(c, tier, column):
        tiers.append(tier)
        return real_cleanup_tier(c, tier, column)

    monkeypatch.setattr(canonical, "_cleanup_tier", counting)

    qualifying_pair(conn)
    qualifying_pair(conn, base="Cardigan", ids=("tc", "td"), albums=("Same LP", "Same LP"))
    qualifying_pair(conn, base="Betty", ids=("te", "tf"), albums=("LP3", "LP4"))

    result = autogroup.run(conn)

    assert result["groups_closed"] == 3, "three groups, so a per-group cleanup would show"
    assert tiers == ["release", "recording", "version", "song"]
    # And the batch really was cleaned: the run still leaves no orphaned
    # group behind, which is the obligation cleanup=False takes on.
    assert (
        conn.execute(
            """
            SELECT COUNT(*) FROM canonical_group cg
            LEFT JOIN track_group tg ON tg.song_id = cg.id
            WHERE cg.tier = 'song' AND tg.track_id IS NULL
            """
        ).fetchone()[0]
        == 0
    )


def test_a_run_commits(conn):
    # source: canonical_autogroup's module docstring -- "Unlike canonical.py,
    # these functions *do* commit -- a run is one transaction per public
    # call." The counterpart to test_canonical_engine's no-commit assertion.
    qualifying_pair(conn)

    autogroup.run(conn)

    assert not conn.in_transaction


# -- The run log ------------------------------------------------------------


def test_last_run_is_undoable_only_while_its_snapshot_stands(conn):
    # source: E §3.4 as amended by P1-013 -- "`undone_at`... is what actually
    # gates whether the Undo button is available (a run with `undone_at`
    # already set can't be undone again)"; only a finished, not-yet-undone run
    # still has its snapshot.
    assert autogroup.last_run(conn) is None

    qualifying_pair(conn)
    autogroup.run(conn)
    assert autogroup.last_run(conn)["undoable"] is True

    autogroup.undo(conn)
    assert autogroup.last_run(conn)["undoable"] is False


def test_a_second_run_replaces_the_snapshot(conn):
    # source: E §3.4 -- "**Only the most recent run's snapshot is kept.**
    # Running auto-group again replaces it. Undo is one level deep."
    qualifying_pair(conn)
    first = autogroup.run(conn)
    qualifying_pair(conn, base="Cardigan", ids=("tc", "td"), albums=("Album Three", "Album Four"))
    second = autogroup.run(conn)

    run_ids = {
        row["run_id"]
        for row in conn.execute("SELECT DISTINCT run_id FROM auto_group_snapshot_track_group")
    }
    assert run_ids == {second["run_id"]}
    assert first["run_id"] not in run_ids


# -- Undo -------------------------------------------------------------------


def test_undo_restores_the_grouping(conn):
    # source: E §3.4 -- "Undo deletes the three live tables' contents and
    # re-inserts from the snapshot."
    qualifying_pair(conn)
    canonical.ensure_track_groups(conn)
    conn.commit()
    before = {t: canonical.groups_for_track(conn, t) for t in ("ta", "tb")}
    assert before["ta"] != before["tb"]  # two singletons, nothing shared

    autogroup.run(conn)
    # apply_partition reuses min(candidates), so ta keeps its ids and tb
    # joins them -- asserting on ta alone would see no change at all. The
    # release tier stays split, because these two are on different albums
    # (§3.2), so the merge shows at song tier.
    assert canonical.groups_for_track(conn, "tb")["song"] == before["ta"]["song"]
    assert canonical.groups_for_track(conn, "tb")["song"] != before["tb"]["song"]

    autogroup.undo(conn)

    assert {t: canonical.groups_for_track(conn, t) for t in ("ta", "tb")} == before


def test_undo_returns_the_group_to_the_queue(conn):
    # source: E §3.4 -- the snapshot covers `reviewed_pair` too, so undoing a
    # run un-decides what it decided and the item comes back.
    qualifying_pair(conn)
    autogroup.run(conn)
    assert detect.candidate_groups(conn) == []

    autogroup.undo(conn)

    assert [g["track_ids"] for g in detect.candidate_groups(conn)] == [["ta", "tb"]]


def test_undo_also_rolls_back_a_review_decided_since_the_run(conn):
    """The documented cost of one blunt snapshot -- pinned, not worked around.

    A hand review recorded *after* the run is not in the snapshot, so a
    wholesale restore discards it. The page says so; this is the assertion
    that keeps it true.
    """
    # source: canonical_autogroup.undo's docstring, per E §3.4's "restored
    # wholesale" -- "Wholesale means wholesale: any review decided *since*
    # that run is rolled back too. That's the documented cost of one blunt
    # snapshot, and the page says so."
    qualifying_pair(conn)
    autogroup.run(conn)
    make(conn, "tc", "Cardigan", isrc="ISRC-C")
    make(conn, "td", "Cardigan", isrc="ISRC-D", album="Album Four")
    canonical.mark_reviewed(conn, ["tc", "td"])
    conn.commit()
    assert ("tc", "td") in reviewed_pairs(conn)

    autogroup.undo(conn)

    assert ("tc", "td") not in reviewed_pairs(conn)


def test_undo_clears_the_snapshot_tables(conn):
    # source: E §3.4 -- undo consumes the snapshot; leaving it behind would
    # let a second undo restore state that has already been restored once.
    qualifying_pair(conn)
    autogroup.run(conn)
    assert any(snapshot_counts(conn).values())

    autogroup.undo(conn)

    assert not any(snapshot_counts(conn).values())


def test_undoing_twice_raises(conn):
    # source: E §3.4 -- "Undo is one level deep". last_run()'s `undoable` is
    # what the page gates on; the function refuses regardless, so a stale page
    # cannot double-restore.
    qualifying_pair(conn)
    autogroup.run(conn)
    autogroup.undo(conn)

    with pytest.raises(ValueError, match="no auto-group run to undo"):
        autogroup.undo(conn)


def test_undo_with_no_run_raises(conn):
    # source: E §3.4 -- there is nothing to restore from.
    with pytest.raises(ValueError, match="no auto-group run to undo"):
        autogroup.undo(conn)


def test_undo_stamps_the_run_as_undone(conn):
    # source: E §3.4 as amended by P1-013 -- `undone_at` is the gate, so it
    # has to be written for the gate to close.
    qualifying_pair(conn)
    result = autogroup.run(conn)

    autogroup.undo(conn)

    row = conn.execute(
        "SELECT undone_at FROM auto_group_run WHERE id = ?", (result["run_id"],)
    ).fetchone()
    assert row["undone_at"] is not None


# -- The chunked tag-back, and the last-run lookup ---------------------------


def test_a_run_tags_every_group_it_decided_past_the_chunk_boundary(conn):
    """The `auto_run_id` tag-back is chunked under SQLITE_MAX_VARIABLE_NUMBER,
    and a group that misses its chunk is silently never tagged -- which means
    undo cannot find it.

    130 qualifying pairs decide 650 groups (four tiers apiece, less the
    reuse), so the loop runs twice and the boundary at index 500 is really
    crossed. Every fixture before this one decided far fewer than 500 groups,
    so the loop only ever ran once and the stride was never read.
    """
    # source: S_sweep.md §3 -- num at canonical_autogroup.py:122 col36
    # (`range(0, len(ids), 500)` -> `501`). The stride moves but the slice
    # still takes 500, so ids[500] falls in the gap between the first chunk's
    # end and the second chunk's start and never gets its auto_run_id.
    # Asserted as "nothing is left untagged" rather than an exact count: the
    # property is that the tag-back covers the whole decided set, and a count
    # would also have to be rederived whenever the fixture size changed.
    for n in range(130):
        qualifying_pair(conn, base=f"Song{n}", ids=(f"t{n}a", f"t{n}b"),
                        albums=(f"Alb{n}A", f"Alb{n}B"))

    autogroup.run(conn)

    total, untagged = conn.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE auto_run_id IS NULL) "
        "FROM canonical_group"
    ).fetchone()
    # The boundary is only exercised if more than one chunk's worth was decided.
    assert total > 500
    assert untagged == 0


def test_last_run_is_the_newest_run_not_the_oldest(conn):
    """`last_run` drives the page's undo control, and undo is one level deep --
    so pointing it at the *first* run ever would offer to restore a snapshot
    that no longer exists."""
    # source: S_sweep.md §3 -- sqlDESC at canonical_autogroup.py:146
    # (`ORDER BY id DESC` -> `ASC`). Every existing test here creates exactly
    # one run, and with a single row DESC and ASC return the same one, so the
    # ordering was never read. Two runs are the whole fixture.
    qualifying_pair(conn, base="First", ids=("f1", "f2"))
    first = autogroup.run(conn)["run_id"]

    qualifying_pair(conn, base="Second", ids=("s1", "s2"))
    second = autogroup.run(conn)["run_id"]

    assert first is not None and second > first
    assert autogroup.last_run(conn)["id"] == second
