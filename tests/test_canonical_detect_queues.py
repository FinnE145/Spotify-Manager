"""Detection's queues: candidate buckets, the cross-artist queue, ordering,
the pending-tier queue and the ad-hoc item.

Two specs share authority here:

- **`grouping-fixes-backfill-M.md` §1** for `cross_component_pairs` -- the M1
  fix, and the one whose failure mode is silent and permanent: marking a
  within-component pair reviewed suppresses it from the main queue *forever*.
- **`grouping-catch-up-E.md`** §3.1 (auto-group closing) and §4 (the
  cross-artist queue's shape).

Ordering comes from **`scoring-H.md` §11.1**, which retired `impact` (summed
live memberships) in favour of score. Both ordering tests below put the two in
opposition on purpose, because they agree on almost every real input.
"""

import builders
import canonical
import canonical_detect as detect
from test_canonical_detect_rules import ARTIST, OTHER_ARTIST, make


def bases(groups):
    return [g["base"] for g in groups]


def keys(groups):
    return {tuple(g["track_ids"]) for g in groups}


# -- Bucketing --------------------------------------------------------------


def test_a_bucket_splits_into_artist_overlap_components(conn):
    # source: E §4 / _bucket_components' docstring -- "Every normalized-base
    # bucket, each split into artist-overlap connected components."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")

    tracks = detect._fetch_tracks(conn)
    buckets = dict(detect._bucket_components(tracks))

    assert sorted(sorted(comp) for comp in buckets["willow"]) == [["ta", "tb"], ["tc"]]


def test_components_are_transitive(conn):
    # source: _group_by_rule is a union-find, so overlap chains: ta~tb through
    # ARTIST and tb~tc through a third artist puts all three in one component
    # even though ta and tc share nobody.
    make(conn, "ta", "Willow", artists=[ARTIST])
    make(conn, "tb", "Willow", artists=[ARTIST, "ar-third"], album="Album Two")
    make(conn, "tc", "Willow", artists=["ar-third"], album="Album Three")

    tracks = detect._fetch_tracks(conn)
    buckets = dict(detect._bucket_components(tracks))

    assert not (tracks["ta"]["artist_ids"] & tracks["tc"]["artist_ids"])
    assert [sorted(comp) for comp in buckets["willow"]] == [["ta", "tb", "tc"]]


def test_the_main_queue_holds_multi_track_same_artist_components(conn):
    # source: E §4 / _build_all_groups -- a main-queue candidate is a
    # component of two or more tracks; a lone track is nothing to review.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Cardigan")

    assert keys(detect.candidate_groups(conn)) == {("ta", "tb")}


def test_a_reviewed_component_leaves_the_main_queue(conn):
    # source: grouping-engine.md "Marking review" -- "A candidate group counts
    # as unreviewed if any pair among its tracks is missing from
    # reviewed_pair."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    assert keys(detect.candidate_groups(conn)) == {("ta", "tb")}

    canonical.mark_reviewed(conn, ["ta", "tb"])
    conn.commit()

    assert detect.candidate_groups(conn) == []


def test_one_missing_pair_keeps_a_component_in_the_queue(conn):
    # source: grouping-engine.md "Marking review" -- "*any* pair... missing".
    # Two of the three pairs reviewed is still unreviewed.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", album="Album Three")
    canonical.mark_reviewed_pairs(conn, [("ta", "tb"), ("ta", "tc")])
    conn.commit()

    assert keys(detect.candidate_groups(conn)) == {("ta", "tb", "tc")}


def test_a_cross_artist_bucket_needs_two_components(conn):
    # source: E §4 / _build_all_groups -- a cross-artist candidate exists only
    # where one normalized base is held by two or more artist components.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")

    _main, cross = detect._build_all_groups(conn)
    assert cross == []

    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")
    _main, cross = detect._build_all_groups(conn)
    assert keys(cross) == {("ta", "tb", "tc")}


# -- cross_component_pairs: the M1 fix --------------------------------------


def test_cross_component_pairs_excludes_same_artist_pairs(conn):
    """M1's whole point: the cross-artist queue never asked about `ta`/`tb`.

    `ta` and `tb` share an artist, so they are one component and their pair
    belongs to the main queue. Only the pairs that cross the component
    boundary were actually put to the reviewer.
    """
    # source: M §1.2 -- "At the cross-apply site **only**, mark
    # **cross-component pairs and nothing else**... It must use the same rule
    # as _bucket_components".
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")

    pairs = detect.cross_component_pairs(conn, ["ta", "tb", "tc"])

    assert sorted(pairs) == [("ta", "tc"), ("tb", "tc")]


def test_cross_component_pairs_agrees_with_the_bucket_components(conn):
    # source: M §1.2 -- "**Share `_group_by_rule` rather than re-implementing
    # the union-find** -- if this function's components ever disagree with
    # `_bucket_components`, a bucket can fail to settle and resurface
    # forever." Asserted as agreement on the same rows rather than by reading
    # the source, since that is the property that actually matters.
    make(conn, "ta", "Willow", artists=[ARTIST])
    make(conn, "tb", "Willow", artists=[ARTIST, "ar-third"], album="Album Two")
    make(conn, "tc", "Willow", artists=["ar-third"], album="Album Three")
    make(conn, "td", "Willow", artists=[OTHER_ARTIST], album="Album Four")

    ids = ["ta", "tb", "tc", "td"]
    tracks = detect._fetch_tracks(conn)
    components = dict(detect._bucket_components(tracks))["willow"]
    component_of = {tid: i for i, comp in enumerate(components) for tid in comp}
    expected = sorted(
        (a, b)
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if component_of[a] != component_of[b]
    )

    assert sorted(detect.cross_component_pairs(conn, ids)) == expected


def test_cross_component_pairs_resolves_artists_through_aliases(conn):
    # source: M §1.2 -- the components use "alias-resolved `artist_ids` from
    # `artists.artist_sets(conn)`". Two Spotify ids for one artist must form
    # ONE component once merged, so their pair stops being cross-component.
    make(conn, "ta", "Willow", artists=["ar-1"])
    make(conn, "tb", "Willow", artists=["ar-2"], album="Album Two")
    assert detect.cross_component_pairs(conn, ["ta", "tb"]) == [("ta", "tb")]

    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        ("ar-2", "ar-1"),
    )
    conn.commit()

    assert detect.cross_component_pairs(conn, ["ta", "tb"]) == []


def test_answering_a_bucket_settles_it_but_leaves_the_main_queue_pair(conn):
    """M §1.4's two consequences, together -- the regression M1 fixed.

    Marking only the cross-component pairs must settle the bucket *and* leave
    `ta`/`tb` for the main queue. Marking every pair (the old behaviour)
    settles the bucket too, so the second assertion is the one carrying the
    fix.
    """
    # source: M §1.4 -- "Answering a cross-artist bucket still settles it...
    # the bucket does not resurface until a new track joins it" and
    # "Within-component pairs in that bucket remain unreviewed, so they appear
    # in the main queue."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")

    canonical.mark_reviewed_pairs(conn, detect.cross_component_pairs(conn, ["ta", "tb", "tc"]))
    conn.commit()

    assert detect.cross_buckets(conn) == []
    assert keys(detect.candidate_groups(conn)) == {("ta", "tb")}


def test_marking_every_pair_is_what_suppressed_the_main_queue(conn):
    # The control for the test above, and the reason M1 exists: the old
    # whole-bucket mark_reviewed settles the cross bucket the same way, but
    # takes the main-queue candidate with it. Without this pair the assertion
    # above could not show which behaviour it is pinning.
    #
    # source: M §1.1 -- "answering a bucket... marks same-artist pairs inside
    # it as decided and permanently suppresses them from the main queue".
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")

    canonical.mark_reviewed(conn, ["ta", "tb", "tc"])
    conn.commit()

    assert detect.cross_buckets(conn) == []
    assert detect.candidate_groups(conn) == []


def test_a_newcomer_reopens_a_settled_bucket(conn):
    # source: M §1.4 -- the bucket "does not resurface until a new track joins
    # it", which is what makes cross-component marking a settle rather than a
    # permanent dismissal.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", artists=[OTHER_ARTIST], album="Album Two")
    canonical.mark_reviewed_pairs(conn, detect.cross_component_pairs(conn, ["ta", "tb"]))
    conn.commit()
    assert detect.cross_buckets(conn) == []

    make(conn, "tc", "Willow", artists=["ar-third"], album="Album Three")

    assert keys(detect.cross_buckets(conn)) == {("ta", "tb", "tc")}


# -- The cross-artist item's shape (E §4.1) ---------------------------------


def test_a_newcomer_sharing_a_primary_artist_is_nested_not_offered(conn):
    """E §4.1's asymmetry: the cross queue must not do the main queue's job.

    `tb` and `tc` are established -- each already reviewed against the other
    -- and `tb` holds a song group. Two newcomers then arrive: `ta` shares
    `tb`'s primary artist, so it is nested and unassignable; `td` shares
    nobody, so it is offered as a new track.

    **`td` is the control.** Without it, "nothing in `new_tracks`" would also
    be what a build that offered no newcomers at all produced.
    """
    # source: _make_cross_item's comment, per E §4.1 -- "A newcomer sharing a
    # primary artist with this group already forms an unreviewed *main*-queue
    # candidate with it, by construction. Asking about it here would be doing
    # the main queue's job twice, so it is rendered nested and unassignable."
    make(conn, "tb", "Willow")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Two")
    builders.make_group(conn, ["tb"])
    canonical.mark_reviewed_pairs(conn, [("tb", "tc")])
    conn.commit()
    make(conn, "ta", "Willow", album="Album Three")
    make(conn, "td", "Willow", artists=["ar-third"], album="Album Four")

    item = detect.cross_bucket_for(conn, ["ta", "tb", "tc", "td"])

    nested = [row["track_id"] for group in item["groups"] for row in group["nested"]]
    assert nested == ["ta"]
    assert [row["track_id"] for row in item["new_tracks"]] == ["td"]


def test_an_established_track_stays_established_after_a_newcomer_arrives(conn):
    # source: _make_cross_item's comment, per E §4.1 -- "when a newcomer
    # arrives in a settled bucket only the newcomer is new, and the tracks it
    # creates fresh unreviewed pairs against stay established and stay
    # collapsed. A track that was reviewed and left ungrouped is still
    # established."
    make(conn, "tb", "Willow")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Two")
    builders.make_group(conn, ["tb"])
    canonical.mark_reviewed_pairs(conn, [("tb", "tc")])
    conn.commit()
    make(conn, "td", "Willow", artists=["ar-third"], album="Album Four")

    item = detect.cross_bucket_for(conn, ["tb", "tc", "td"])

    # td is unreviewed against both, but that does not make them new again.
    assert [row["track_id"] for row in item["new_tracks"]] == ["td"]


def test_cross_bucket_for_needs_two_tracks(conn):
    # characterization -- Backspace re-renders a bucket by track id, and a
    # one-track bucket is not a bucket; the caller branches on None.
    make(conn, "ta", "Willow")

    assert detect.cross_bucket_for(conn, ["ta"]) is None
    assert detect.cross_bucket_for(conn, ["ta", "unknown-track"]) is None


# -- Ordering (scoring-H.md §11.1) ------------------------------------------


def test_candidate_groups_rank_by_score_not_by_live_memberships(conn):
    """`impact` is retired, so the busier group must lose to the better one.

    `quiet` holds one membership and a high score; `busy` holds four and a
    low one. Under the old `impact` ordering `busy` came first.
    """
    # source: scoring-H.md §11.1 -- "**`impact` is retired** once these land.
    # It was the summed live-membership count, used as a stand-in for exactly
    # this score."
    for track_id, name in (("q1", "Quiet"), ("q2", "Quiet"), ("b1", "Busy"), ("b2", "Busy")):
        make(conn, track_id, name, album=f"Album {track_id}")
    builders.make_membership(conn, track_id="q1")
    for track_id in ("b1", "b1", "b2", "b2"):
        builders.make_membership(conn, playlist_id=None, track_id=track_id)
    builders.make_score(conn, "track", "q1", all_time=90.0)
    builders.make_score(conn, "track", "q2", all_time=90.0)
    builders.make_score(conn, "track", "b1", all_time=10.0)
    builders.make_score(conn, "track", "b2", all_time=10.0)

    ordered = detect.candidate_groups(conn)

    tracks = detect._fetch_tracks(conn)
    assert tracks["b1"]["live_count"] > tracks["q1"]["live_count"]  # the old key disagrees
    assert bases(ordered) == ["quiet", "busy"]


def test_equal_scores_fall_back_to_size_then_base(conn):
    # source: canonical_detect._order -- the one ordering key, "(-score,
    # -len(track_ids), base)". With no scores at all every group ties at 0.0,
    # so the tail keys decide: the larger group first, then alphabetically.
    for track_id, name in (
        ("a1", "Alpha"), ("a2", "Alpha"),
        ("b1", "Beta"), ("b2", "Beta"), ("b3", "Beta"),
        ("c1", "Gamma"), ("c2", "Gamma"),
    ):
        make(conn, track_id, name, album=f"Album {track_id}")

    assert bases(detect.candidate_groups(conn)) == ["beta", "alpha", "gamma"]


# -- The /dev/canonical listing feed ----------------------------------------


def test_canonical_page_groups_returns_the_three_the_pane_needs(conn):
    # source: canonical_page_groups' docstring, per canonical-fixes.md §2.3 --
    # "the unreviewed main queue, the unreviewed cross-artist bucket count,
    # and every candidate for the listing -- derived from a single
    # _build_all_groups()".
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")

    unreviewed_main, unreviewed_cross, all_groups = detect.canonical_page_groups(conn)

    assert keys(unreviewed_main) == {("ta", "tb")}
    assert keys(unreviewed_cross) == {("ta", "tb", "tc")}
    assert keys(all_groups) == {("ta", "tb"), ("ta", "tb", "tc")}


def test_canonical_page_groups_agrees_with_candidate_groups(conn):
    # source: canonical-fixes.md §2.3 -- the combined function exists purely
    # to avoid three separate rebuilds, so its main-queue result must be the
    # same one candidate_groups() produces on its own. If the two ever
    # disagreed, the count on the page and the queue behind it would too.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Cardigan")
    make(conn, "td", "Cardigan", album="Album Four")

    unreviewed_main, _cross, _all = detect.canonical_page_groups(conn)

    assert keys(unreviewed_main) == keys(detect.candidate_groups(conn))


# -- The listing filter -----------------------------------------------------


def matched_bases(conn, query):
    _main, _cross, all_groups = detect.canonical_page_groups(conn)
    return sorted(bases(detect.filter_groups(all_groups, query)))


def test_the_filter_matches_a_base_a_title_or_an_artist(conn):
    # source: filter_groups' docstring -- "Candidate groups whose base title,
    # track title or artists match `query`."
    builders.make_artist(conn, ARTIST, name="Taylor Swift")
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    builders.make_artist(conn, OTHER_ARTIST, name="Phoebe Bridgers")
    make(conn, "tc", "Cardigan", artists=[OTHER_ARTIST])
    make(conn, "td", "Cardigan", artists=[OTHER_ARTIST], album="Album Four")

    assert matched_bases(conn, "willow") == ["willow"]
    assert matched_bases(conn, "Bridgers") == ["cardigan"]  # case-insensitive


def test_the_filter_honours_the_sql_percent_wildcard(conn):
    """`%` is what gets you past the listing's unfiltered cap."""
    # source: filter_groups' docstring -- "Matched with SQL LIKE's `%`
    # wildcard rather than a plain substring so this filter behaves like the
    # LIKE-backed Groups filter beside it on `/dev/canonical` -- in
    # particular, searching `%` lists everything".
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Cardigan")
    make(conn, "td", "Cardigan", album="Album Four")

    assert matched_bases(conn, "%") == ["cardigan", "willow"]
    # A wildcard in the middle spans arbitrary text, which a plain substring
    # match would not.
    assert matched_bases(conn, "car%gan") == ["cardigan"]


def test_an_empty_filter_returns_everything_unchanged(conn):
    # characterization -- the early return; the page's unfiltered load relies
    # on it, and on getting the same list object semantics as no filter.
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    _main, _cross, all_groups = detect.canonical_page_groups(conn)

    assert detect.filter_groups(all_groups, "") is all_groups


# -- Auto-group candidates (E §3.1) -----------------------------------------


def test_a_group_closes_only_when_every_pair_matches(conn):
    """E §3.1's "partial matches close nothing", with the 3-track case it
    names.

    `ta` and `tb` are a true duplicate; `tc` shares their ISRC and title but
    is 30s longer, so one of the three pairs fails and the whole group stays
    in the queue.
    """
    # source: E §3.1 -- "A candidate group **auto-closes** when the rule
    # matches on **every** pair in the group. Partial matches close nothing --
    # a 3-track group where the rule fires on two pairs of three stays in the
    # queue whole."
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=200_000, album="Album Two")
    make(conn, "tc", "Willow", isrc="ISRC-SAME", duration_ms=230_000, album="Album Three")

    closable, queue_total = detect.auto_group_candidates(conn)

    assert queue_total == 1
    assert closable == []


def test_a_fully_matching_group_is_closable(conn):
    # source: E §3.1 -- the positive case, and E §3.2: "Release is keyed on
    # the album name, so tracks on the same album share a release and tracks
    # on different albums don't" (album_norms is what carries that).
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=200_000, album="Album Two")

    closable, queue_total = detect.auto_group_candidates(conn)

    assert queue_total == 1
    assert [g["track_ids"] for g in closable] == [["ta", "tb"]]
    assert closable[0]["album_norms"] == {"ta": "album one", "tb": "album two"}


def test_a_reviewed_group_is_not_a_candidate(conn):
    # source: auto_group_candidates' docstring -- it closes "unreviewed
    # main-queue candidate groups", so an already-decided group is neither
    # closable nor counted in the queue total.
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=200_000, album="Album Two")
    canonical.mark_reviewed(conn, ["ta", "tb"])
    conn.commit()

    assert detect.auto_group_candidates(conn) == ([], 0)


def test_cross_artist_candidates_are_never_closable(conn):
    # source: auto_group_candidates' docstring -- "Cross-artist candidates are
    # never considered; none of them close under this rule anyway." Built so
    # the rule *would* match if they were considered, which is what makes the
    # exclusion visible.
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(
        conn,
        "tb",
        "Willow",
        isrc="ISRC-SAME",
        duration_ms=200_000,
        artists=[OTHER_ARTIST],
        album="Album Two",
    )

    tracks = detect._fetch_tracks(conn)
    assert detect._auto_group_pair(tracks, "ta", "tb")  # the rule agrees...
    assert detect.auto_group_candidates(conn) == ([], 0)  # ...and it is still not offered


# -- Pending tier review (E §4.5/§4.6) --------------------------------------


def test_two_newcomers_in_one_group_are_one_pending_item(conn):
    # source: pending_song_ids' docstring -- "Deduped at read time: two
    # newcomers landing in the same group are two rows resolving to one item."
    group = builders.make_group(conn, ["ta", "tb", "tc"])
    conn.executemany(
        "INSERT INTO pending_tier_review (track_id) VALUES (?)", [("tb",), ("tc",)]
    )
    conn.commit()

    assert detect.pending_song_ids(conn) == [group["song"]]


def test_a_group_that_fell_back_to_one_member_is_skipped(conn):
    # source: pending_song_ids' docstring -- "A group that has fallen back to
    # a single member is skipped, because there is nothing left to review
    # across -- an auto-group undo restores track_group wholesale and can
    # detach an assigned newcomer".
    builders.make_group(conn, ["ta"])
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('ta')")
    conn.commit()

    assert detect.pending_song_ids(conn) == []


def test_the_pending_row_survives_so_a_later_merge_brings_it_back(conn):
    # source: pending_song_ids' docstring -- "The pending row itself is
    # deliberately left in place rather than deleted. It still records that
    # the track owes a tier pass, so if a later merge puts it back into a
    # multi-member group the item correctly comes back."
    group = builders.make_group(conn, ["ta"])
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('ta')")
    conn.commit()
    assert detect.pending_song_ids(conn) == []

    builders.make_group(conn, ["tb"], song=group["song"])

    assert detect.pending_song_ids(conn) == [group["song"]]


def test_the_pending_count_and_the_pending_queue_cannot_disagree(conn):
    # source: pending_song_ids' docstring -- "This filter is the *only* one:
    # pending_tier_items() serves exactly what this returns, so the count on
    # /dev/canonical and the queue behind it can never disagree."
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_group(conn, ["tc"])
    conn.executemany(
        "INSERT INTO pending_tier_review (track_id) VALUES (?)", [("tb",), ("tc",)]
    )
    conn.commit()

    assert detect.pending_song_ids(conn) == [group["song"]]
    assert len(detect.pending_tier_items(conn)) == 1


def test_a_pending_item_carries_a_prefill_below_the_song_tier(conn):
    # source: pending_tier_items' docstring -- "A full candidate group, **not**
    # ad_hoc_group: the whole point of the pending queue is to assign the
    # finer tiers, so the prefill has to run and fill them in", while "The
    # song tier comes out shared regardless, because _prefill_labels'
    # same_song consults _same_real first".
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (Live)", isrc="ISRC-B", album="Album Two")
    group = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"], song=group["song"])
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('tb')")
    conn.commit()

    item = detect.pending_tier_items(conn)[0]

    assert item["labels"]["ta"]["song"] == item["labels"]["tb"]["song"]
    # The prefill proposed below the song tier: a (Live) cut stands alone.
    assert item["labels"]["ta"]["version"] != item["labels"]["tb"]["version"]


# -- The ad-hoc item --------------------------------------------------------


def test_the_ad_hoc_item_renders_the_saved_grouping_not_a_prefill(conn):
    # source: ad_hoc_group's docstring -- "Skips detection: pre-fills nothing,
    # renders the tracks' current saved grouping instead." Built from two
    # tracks the prefill *would* merge at version, so a prefill leaking in
    # here would be visible.
    make(conn, "ta", "Willow", isrc="ISRC-SAME")
    make(conn, "tb", "Willow", isrc="ISRC-SAME", album="Album Two")
    a = builders.make_group(conn, ["ta"])
    b = builders.make_group(conn, ["tb"])

    item = detect.ad_hoc_group(conn, ["ta", "tb"])

    assert item["labels"]["ta"]["version"] == str(a["version"])
    assert item["labels"]["tb"]["version"] == str(b["version"])
    assert item["reviewed"] is None


def test_an_ungrouped_track_gets_labels_unique_to_itself(conn):
    # source: ad_hoc_group's fallback comment -- "fall back to labels unique
    # to this track rather than one shared sentinel, which would silently
    # merge every ungrouped track in the item."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")

    item = detect.ad_hoc_group(conn, ["ta", "tb"])

    assert item["labels"]["ta"]["song"] != item["labels"]["tb"]["song"]


# -- Stale decisions --------------------------------------------------------


def test_a_saved_recording_group_the_rules_would_refuse_is_reported(conn):
    # source: stale_recording_groups' docstring -- "Saved recording groups
    # holding at least one pair the current rules would no longer merge --
    # decisions made under an older rule set... Pure: reports, changes
    # nothing."
    make(conn, "ta", "Willow", isrc="ISRC-A", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-B", duration_ms=230_000, album="Album Two")
    group = builders.make_group(conn, ["ta", "tb"])

    assert detect.stale_recording_groups(conn) == [
        {"recording_id": group["recording"], "track_ids": ["ta", "tb"]}
    ]


def test_a_recording_group_the_rules_still_agree_with_is_not_stale(conn):
    # The control: same shape, but the pair still satisfies recording
    # identity, so nothing is reported.
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=200_000, album="Album Two")
    builders.make_group(conn, ["ta", "tb"])

    assert detect.stale_recording_groups(conn) == []
