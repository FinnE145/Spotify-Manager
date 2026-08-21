"""`canonical.py`'s write side: the engine and the review-pair writer.

The authority for everything here is `docs/canonical-tracks/grouping-engine.md`
(stamped **Audited 2026-08-17**, P1-018) -- its "Invariants", "Reconciliation
algorithm" and "Marking review" sections describe all four tiers, both
closures, and the id-reuse rule. Assertions cite it by step number.

**Tier order runs finest to coarsest: release, recording, version, song.**
`canonical.TIER_ORDER` is written that way and the algorithm processes it in
that order, so "the finer tier" of `song` is `version`, not `recording`. Every
fixture below depends on getting that round the right way.

Two conventions the fixtures lean on:

- `builders.make_group(conn, ids)` puts its tracks in **one group at each of
  the four tiers**, so two tracks that should share only their *song* group are
  built as two calls, the second passing `song=first["song"]`;
- `canonical_group.id` is `AUTOINCREMENT`, so **creation order fixes the id
  order**, which step 4's `min(candidates)` rule turns into observable
  behaviour. Where that matters the fixture says so.
"""

import pytest

import builders
import canonical


def groups(conn, track_id):
    return canonical.groups_for_track(conn, track_id)


def group_rows(conn, tier):
    return [
        row["id"]
        for row in conn.execute("SELECT id FROM canonical_group WHERE tier = ?", (tier,))
    ]


def reviewed_rows(conn):
    return [
        (row["track_id_a"], row["track_id_b"])
        for row in conn.execute(
            "SELECT track_id_a, track_id_b FROM reviewed_pair ORDER BY track_id_a, track_id_b"
        )
    ]


def labels_for(track_ids, **tiers):
    """One label per tier for each of `track_ids`, defaulting to a shared
    label at every tier -- i.e. "merge these into one group everywhere".

    Pass a tier by keyword to override it with a per-track mapping, which is
    how a test says "same song, different versions" without writing out all
    four tiers for every track.
    """
    out = {}
    for track_id in track_ids:
        out[track_id] = {
            tier: tiers[tier][track_id] if tier in tiers else f"shared-{tier}"
            for tier in canonical.TIER_ORDER
        }
    return out


def own_labels(track_ids):
    """A label mapping giving each track its own distinct label -- the
    "everything apart at this tier" shape."""
    return {track_id: f"own-{track_id}" for track_id in track_ids}


# -- Bootstrapping ----------------------------------------------------------


def test_ensure_track_groups_gives_each_track_four_private_groups(conn):
    # source: grouping-engine.md "Bootstrapping" -- "allocate four fresh
    # singleton groups (one per tier)", plus invariant 2 (totality: exactly
    # one track_group row with four non-NULL ids).
    a = builders.make_track(conn)
    b = builders.make_track(conn)

    canonical.ensure_track_groups(conn)

    a_groups, b_groups = groups(conn, a), groups(conn, b)
    assert set(a_groups) == {"song", "version", "recording", "release"}
    assert all(value is not None for value in a_groups.values())
    # Singleton means singleton: two fresh tracks share nothing. A bootstrap
    # that allocated one group per tier for the whole batch would satisfy
    # "four non-NULL ids" and fail here.
    assert set(a_groups.values()).isdisjoint(b_groups.values())


def test_ensure_track_groups_is_idempotent(conn):
    # source: grouping-engine.md "Bootstrapping" -- "Idempotent and cheap."
    builders.make_track(conn)
    canonical.ensure_track_groups(conn)
    before = {tier: group_rows(conn, tier) for tier in canonical.TIER_ORDER}

    canonical.ensure_track_groups(conn)

    assert {tier: group_rows(conn, tier) for tier in canonical.TIER_ORDER} == before


def test_ensure_track_groups_leaves_existing_rows_alone(conn):
    # source: grouping-engine.md "Bootstrapping" -- it acts "for every track
    # row lacking a track_group row", so an already-grouped track keeps the
    # grouping it has rather than being reset to singletons.
    existing = builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")

    canonical.ensure_track_groups(conn)

    assert groups(conn, "ta") == groups(conn, "tb")
    assert groups(conn, "ta")["song"] == existing["song"]


# -- Validation -------------------------------------------------------------


def test_labels_missing_a_tier_are_rejected(conn):
    # source: grouping-engine.md "Validation" -- labels carry one label per
    # tier; a partial mapping is a client bug, not a partial update.
    track = builders.make_track(conn)
    with pytest.raises(ValueError, match="missing label"):
        canonical.apply_partition(conn, {track: {"song": "s", "version": "v"}})


def test_unknown_track_ids_are_rejected(conn):
    # source: grouping-engine.md "Validation" -- "Every track id must exist
    # in `track`."
    with pytest.raises(ValueError, match="unknown track ids"):
        canonical.apply_partition(conn, labels_for(["no-such-track"]))


def test_labels_that_are_not_nested_consistent_are_rejected(conn):
    # source: grouping-engine.md "Validation" -- "two tracks sharing a release
    # label must share their recording, version, and song labels; and so on
    # up." Here both tracks share a release label but disagree on recording.
    a, b = builders.make_track(conn, "ta"), builders.make_track(conn, "tb")
    bad = labels_for([a, b], recording={a: "r1", b: "r2"})

    with pytest.raises(ValueError, match="nested-consistent"):
        canonical.apply_partition(conn, bad)


def test_validation_rejects_before_writing_anything(conn):
    # source: grouping-engine.md "Validation" -- rejection is a 400, i.e. the
    # call does nothing at all. _validate_labels runs before the
    # ensure_track_groups() call that would otherwise allocate rows for the
    # valid track in this item.
    builders.make_track(conn, "ta")
    before = group_rows(conn, "song")

    with pytest.raises(ValueError):
        canonical.apply_partition(conn, labels_for(["ta", "missing-track"]))

    assert group_rows(conn, "song") == before


def test_empty_labels_are_a_no_op(conn):
    # source: grouping-engine.md "Reconciliation algorithm" -- with no item
    # tracks there are no parts, so there is nothing to reconcile.
    builders.make_track(conn)
    before = group_rows(conn, "song")

    assert canonical.apply_partition(conn, {}) == {"tracks": {}, "dragged_in": []}
    assert group_rows(conn, "song") == before


# -- Step 1: parts ----------------------------------------------------------


def test_merging_two_singletons_shares_every_tier(conn):
    # source: grouping-engine.md "What the primitives look like" -- Merge:
    # "give every track in S the same tier-t label (and unify their labels at
    # all coarser tiers, since nesting requires it)."
    a, b = builders.make_track(conn, "ta"), builders.make_track(conn, "tb")

    result = canonical.apply_partition(conn, labels_for([a, b]))

    assert groups(conn, a) == groups(conn, b)
    assert result["dragged_in"] == []


def test_different_labels_at_one_tier_keep_the_coarser_tier_shared(conn):
    # source: grouping-engine.md "Reconciliation algorithm" step 1 -- the
    # label partition is "the only *hard* partition: two item tracks with
    # different labels can never share a part". Nesting forces the finer
    # tiers apart with version; song stays shared.
    a, b = builders.make_track(conn, "ta"), builders.make_track(conn, "tb")
    split = labels_for(
        [a, b],
        version=own_labels([a, b]),
        recording=own_labels([a, b]),
        release=own_labels([a, b]),
    )

    canonical.apply_partition(conn, split)

    assert groups(conn, a)["song"] == groups(conn, b)["song"]
    assert groups(conn, a)["version"] != groups(conn, b)["version"]


def test_an_item_can_split_a_group_it_shows_both_halves_of(conn):
    # source: grouping-engine.md "Reconciliation algorithm" step 3 -- upward
    # closure skips "any track already claimed by another part", so a group
    # whose members are all present *and* labelled apart genuinely splits.
    # This is the case that must NOT be re-merged by preservation.
    builders.make_group(conn, ["ta", "tb"])

    canonical.apply_partition(
        conn,
        labels_for(
            ["ta", "tb"],
            song=own_labels(["ta", "tb"]),
            version=own_labels(["ta", "tb"]),
            recording=own_labels(["ta", "tb"]),
            release=own_labels(["ta", "tb"]),
        ),
    )

    assert groups(conn, "ta")["song"] != groups(conn, "tb")["song"]


# -- Step 2: downward closure (nesting-mandatory) ---------------------------


def test_downward_closure_drags_a_finer_group_mate_along(conn):
    """A track outside the item, sharing a member's finer group, must follow.

    `tc` shares `ta`'s *release* group and nothing coarser is being asked
    about it -- but nesting means a release group can never straddle two
    recording groups, so when the item moves `ta`'s recording, `tc` has to
    move with it.
    """
    # source: grouping-engine.md step 2 -- "Expand each part with every track
    # -- in the item or not -- that shares a member's *just-assigned*
    # finer-tier group... This is what nesting forces: a release group can
    # never straddle two recording groups."
    a = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tc"], release=a["release"])
    builders.make_track(conn, "tb")

    canonical.apply_partition(conn, labels_for(["ta", "tb"]))

    # The discriminating assertion: tc is not in the item and shares no
    # recording group with tb by any other route. Without step 2 it keeps its
    # old recording id while still sharing ta's release -- a nesting
    # violation the runner would never notice on its own.
    assert groups(conn, "tc")["recording"] == groups(conn, "tb")["recording"]
    assert groups(conn, "tc")["song"] == groups(conn, "tb")["song"]


# -- Step 3: upward closure (preservation) ----------------------------------


def test_upward_closure_preserves_a_group_the_item_only_partly_covers(conn):
    """The rule that makes a no-op commit a genuine no-op.

    The item holds two of a three-track song group and says "these two are one
    song". The third must come along unchanged rather than being cut loose.
    """
    # source: grouping-engine.md step 3 -- "An item can therefore only split a
    # group it *actively disagrees* about; tracks it never mentions come along
    # unchanged", and "Without it, an item holding only part of a group...
    # reads as 'everyone else is excluded' and silently cuts the rest loose."
    a = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"], song=a["song"])
    builders.make_group(conn, ["tc"], song=a["song"])
    before = groups(conn, "tc")

    result = canonical.apply_partition(conn, labels_for(["ta", "tb"]))

    assert groups(conn, "tc")["song"] == groups(conn, "ta")["song"]
    # Preserved, not moved -- step 4 reuses the song id because the part now
    # covers the whole group, so tc's own ids never changed and the return
    # value stays quiet about it ("Return value": tracks "merely *preserved*
    # by step 3 keep their ids and are not reported").
    assert groups(conn, "tc") == before
    assert result["dragged_in"] == []


def test_an_absent_group_mate_follows_when_the_item_merges_two_groups(conn):
    """The one way an outside track genuinely moves -- and the only case
    `dragged_in` is non-empty.

    `ta~tb` already share a song group; the item says `ta~tc`, so `tb` joins
    by transitivity. **`tc`'s groups are created first on purpose**: step 4
    reuses `min(candidates)`, so the lower id wins and it is `tb` that has to
    move rather than `tc`.
    """
    # source: grouping-engine.md "Return value" -- "The one way an outside
    # track genuinely moves is when the item merges two existing groups: A~B
    # already, and the item says A~C, so B joins C's group by transitivity.
    # That is reported in dragged_in."
    c = builders.make_group(conn, ["tc"])
    a = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"], song=a["song"])
    assert c["song"] < a["song"]  # the fixture's whole point; see docstring

    result = canonical.apply_partition(
        conn,
        labels_for(
            ["ta", "tc"],
            version=own_labels(["ta", "tc"]),
            recording=own_labels(["ta", "tc"]),
            release=own_labels(["ta", "tc"]),
        ),
    )

    assert groups(conn, "tb")["song"] == groups(conn, "ta")["song"] == c["song"]
    assert result["dragged_in"] == ["tb"]


# -- Step 4: choosing the group id ------------------------------------------


def test_a_group_that_only_gains_members_keeps_its_id(conn):
    # source: grouping-engine.md step 4 -- "So a group that only *gains*
    # members keeps its id".
    existing = builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")

    canonical.apply_partition(conn, labels_for(["ta", "tb", "tc"]))

    assert groups(conn, "tc")["song"] == existing["song"]


def test_a_genuine_split_yields_new_ids_for_both_halves(conn):
    """Neither half may keep the old id, and the old row is deleted.

    A part can only reuse an id whose *full* membership it covers, and after a
    2-into-1+1 split neither half covers the original pair -- so both get
    fresh rows and the original is orphaned.
    """
    # source: grouping-engine.md step 4 -- "a group the item genuinely
    # **splits** yields new ids for both halves and the old row is deleted",
    # plus invariant 4 ("No orphans") and invariant 3 (ids are never reused).
    original = builders.make_group(conn, ["ta", "tb"])

    canonical.apply_partition(
        conn,
        labels_for(
            ["ta", "tb"],
            song=own_labels(["ta", "tb"]),
            version=own_labels(["ta", "tb"]),
            recording=own_labels(["ta", "tb"]),
            release=own_labels(["ta", "tb"]),
        ),
    )

    assert groups(conn, "ta")["song"] != original["song"]
    assert groups(conn, "tb")["song"] != original["song"]
    assert original["song"] not in group_rows(conn, "song")


# -- Step 6: cleanup --------------------------------------------------------


def test_a_pin_survives_a_change_that_keeps_the_pinned_track_in_the_group(conn):
    # source: grouping-engine.md step 6 -- the pin is cleared only "if the
    # pinned track is no longer a member". Written as the negative case
    # because an over-eager cleanup that always cleared it would pass the
    # positive one below.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")
    canonical.pin_representative(conn, "ta")

    canonical.apply_partition(conn, labels_for(["ta", "tb", "tc"]))

    assert canonical.representative(conn, groups(conn, "ta")["song"]) == "ta"


def test_cleanup_clears_a_pin_whose_track_is_no_longer_a_member(conn):
    """Step 6's stale-pin sweep, driven directly.

    Not through `apply_partition`: a song group only keeps its id when a part
    covers its *whole* membership, which includes the pinned track -- so
    within one call the pinned track cannot leave a group that survives (see
    the P2 findings note on `_cleanup_tier`). The state below is what a
    wholesale `track_group` restore leaves behind, which is the sweep's real
    caller.
    """
    # source: grouping-engine.md step 6 -- "For a surviving group whose
    # membership changed, clear representative_track_id to NULL if the pinned
    # track is no longer a member."
    builders.make_group(conn, ["ta"])
    other = builders.make_group(conn, ["tb"])
    canonical.pin_representative(conn, "ta")
    # ta pinned on a group it does not belong to -- tb's.
    conn.execute(
        "UPDATE canonical_group SET representative_track_id = ? WHERE id = ?", ("ta", other["song"])
    )

    canonical.cleanup_all_tiers(conn)

    row = conn.execute(
        "SELECT representative_track_id FROM canonical_group WHERE id = ?", (other["song"],)
    ).fetchone()
    assert row["representative_track_id"] is None
    # ta's own group keeps its valid pin -- the sweep clears stale pins, not
    # every pin it passes.
    assert canonical.representative(conn, groups(conn, "ta")["song"]) == "ta"


def test_cleanup_false_leaves_the_orphan_for_the_caller_to_sweep(conn):
    # source: grouping-engine.md "The one write operation" -- "cleanup=False
    # skips the four _cleanup_tier passes... Only a caller that runs
    # cleanup_all_tiers() itself once its own batch is done may pass it."
    original = builders.make_group(conn, ["ta", "tb"])
    split = labels_for(
        ["ta", "tb"],
        song=own_labels(["ta", "tb"]),
        version=own_labels(["ta", "tb"]),
        recording=own_labels(["ta", "tb"]),
        release=own_labels(["ta", "tb"]),
    )

    canonical.apply_partition(conn, split, cleanup=False)
    assert original["song"] in group_rows(conn, "song")

    canonical.cleanup_all_tiers(conn)
    assert original["song"] not in group_rows(conn, "song")


def test_cleanup_false_reaches_the_same_grouping_as_cleanup_true(conn):
    # source: grouping-engine.md "The one write operation" -- cleanup is a
    # cost optimisation over the *same* reconciliation, so the track_group
    # rows it produces must be identical either way; only the orphaned
    # canonical_group rows differ.
    builders.make_group(conn, ["ta", "tb"])
    split = labels_for(
        ["ta", "tb"],
        song=own_labels(["ta", "tb"]),
        version=own_labels(["ta", "tb"]),
        recording=own_labels(["ta", "tb"]),
        release=own_labels(["ta", "tb"]),
    )

    canonical.apply_partition(conn, split, cleanup=False)
    without = {track: groups(conn, track) for track in ("ta", "tb")}
    canonical.cleanup_all_tiers(conn)

    assert without == {track: groups(conn, track) for track in ("ta", "tb")}


# -- Marking review ---------------------------------------------------------


def test_mark_reviewed_pairs_normalizes_an_unsorted_pair(conn):
    # source: grouping-engine.md "Marking review" -- pairs are stored "always
    # `a < b` lexicographically". P1-018 moved that normalization inside
    # mark_reviewed_pairs so a caller's own ordering is never load-bearing.
    builders.make_track(conn, "tb")
    builders.make_track(conn, "ta")

    canonical.mark_reviewed_pairs(conn, [("tb", "ta")])

    assert reviewed_rows(conn) == [("ta", "tb")]


def test_marking_a_pair_both_ways_round_writes_one_row(conn):
    # source: grouping-engine.md "Marking review" -- "refreshing decided_at on
    # conflict". The conflict only fires if both orderings normalize to the
    # same stored pair, so a second row here would mean the invariant above is
    # being enforced in name only.
    builders.make_track(conn, "ta")
    builders.make_track(conn, "tb")

    canonical.mark_reviewed_pairs(conn, [("ta", "tb")])
    canonical.mark_reviewed_pairs(conn, [("tb", "ta")])

    assert reviewed_rows(conn) == [("ta", "tb")]


def test_mark_reviewed_writes_every_unordered_pair(conn):
    # source: grouping-engine.md "Marking review" -- "Inserts every unordered
    # pair from track_ids into reviewed_pair", i.e. C(3,2) = 3 rows, not the
    # 2 that chaining consecutive ids would give.
    for track_id in ("tc", "ta", "tb"):
        builders.make_track(conn, track_id)

    canonical.mark_reviewed(conn, ["tc", "ta", "tb"])

    assert reviewed_rows(conn) == [("ta", "tb"), ("ta", "tc"), ("tb", "tc")]


def test_mark_reviewed_of_a_single_track_writes_nothing(conn):
    # source: grouping-engine.md "Marking review" -- there is no unordered
    # pair to draw from one track, so there is nothing to record.
    builders.make_track(conn, "ta")

    canonical.mark_reviewed(conn, ["ta"])

    assert reviewed_rows(conn) == []


# -- Module invariants (codebase-health-P.md §6) ----------------------------
#
# P3 moves code between modules, which is exactly what would violate one of
# these by accident. Both are asserted dynamically, against SQL the module
# actually issues, rather than by reading its source.


WRITE_VERBS = ("insert into", "update", "delete from", "replace into")


def executed_sql(conn, run):
    """Every SQL statement `run()` puts through `conn`.

    sqlite3's trace callback fires per statement, so this sees writes made by
    any code path -- including one a future refactor introduces that this
    file never calls directly.
    """
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        run()
    finally:
        conn.set_trace_callback(None)
    return seen


def writes_to(statements, table):
    for statement in statements:
        collapsed = " ".join(statement.lower().split())
        for verb in WRITE_VERBS:
            # `<verb> <table> ` with the trailing space, so `track_group`
            # never reads as a write to `track`.
            if f"{verb} {table} " in f"{collapsed} ":
                return True
    return False


def test_canonical_never_writes_track_or_membership(conn):
    # source: codebase-health-P.md §6 -- "canonical.py never touches
    # track/membership" is one of the stated module invariants P2 is asked to
    # assert. It reads both (representative() counts membership rows,
    # track_display joins track); the invariant is about writing.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_membership(conn, track_id="ta")
    builders.make_track(conn, "tc")

    statements = executed_sql(
        conn,
        lambda: (
            canonical.ensure_track_groups(conn),
            canonical.apply_partition(conn, labels_for(["ta", "tb", "tc"])),
            canonical.mark_reviewed(conn, ["ta", "tb", "tc"]),
            canonical.pin_representative(conn, "ta"),
            canonical.cleanup_all_tiers(conn),
        ),
    )

    assert statements  # or the two assertions below are vacuously true
    assert not writes_to(statements, "track")
    assert not writes_to(statements, "membership")
    # The positive control: it does write the tables it owns, so the matcher
    # above is capable of seeing a write at all.
    assert writes_to(statements, "track_group")
    assert writes_to(statements, "reviewed_pair")


def test_no_canonical_function_commits(conn):
    # source: canonical.py's own module docstring -- "None of these functions
    # commit -- callers own the transaction" -- listed in codebase-health-P.md
    # §6 as an invariant that must survive P.
    #
    # sqlite3 clears in_transaction on commit, so an open transaction after
    # each call is the direct observation. Every call below genuinely writes,
    # or the assertion would pass for the wrong reason.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")
    assert not conn.in_transaction  # builders committed; start from closed

    for call in (
        lambda: canonical.ensure_track_groups(conn),
        lambda: canonical.apply_partition(conn, labels_for(["ta", "tb", "tc"])),
        lambda: canonical.mark_reviewed(conn, ["ta", "tb", "tc"]),
        lambda: canonical.pin_representative(conn, "ta"),
    ):
        conn.rollback()
        call()
        assert conn.in_transaction
