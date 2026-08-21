"""`canonical.py`'s read paths: the representative election and the viewer's
listing/tree helpers.

The representative rule's authority is **`scoring-H.md` §11.3**, restated in
full in `canonical-tracks.md`'s "Representative track" section (rewritten
2026-08-17 by P1-008, which is what makes it citable): a pin, else **highest
`score.all_time` at the *track* tier** -> **oldest `added_at` over all
membership rows, live or not** -> **lowest `track_id`**.

Two details of that rule are what the fixtures below are built to separate,
because they are the two H changed and the two a plausible wrong
implementation still gets right on easy input:

- the primary key is the score, **not** the live-membership count the pre-H
  rule used -- so every score fixture here deliberately puts the two in
  opposition;
- the `added_at` tail no longer filters to `removed_at IS NULL` -- so the
  degraded-fallback fixture gives its winner a *removed* membership only.
"""

import pytest

import builders
import canonical

OLD = "2024-01-01T00:00:00Z"
NEW = "2024-12-01T00:00:00Z"


def rep(conn, group_id):
    return canonical.representative(conn, group_id)


# -- The representative election -------------------------------------------


def test_a_higher_score_beats_more_live_memberships(conn):
    """P1-008's headline case: the two rules disagree, and score wins.

    `tb` holds three live memberships to `ta`'s one, so the pre-H rule elects
    `tb`. `ta` scores higher, so the current rule elects `ta`.
    """
    # source: scoring-H.md §11.3 -- "canonical.representative() -- currently
    # most live memberships -> oldest added_at -> lowest track id. Becomes
    # highest score."
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_membership(conn, track_id="ta")
    for _ in range(3):
        builders.make_membership(conn, playlist_id=None, track_id="tb")
    builders.make_score(conn, "track", "ta", all_time=90.0)
    builders.make_score(conn, "track", "tb", all_time=20.0)

    assert rep(conn, group["song"]) == "ta"


def test_the_election_reads_the_track_tier_not_the_groups_own_tier(conn):
    """A version group is elected by its members' *track*-tier scores.

    Scores and `added_at` are put in opposition, so an implementation that
    joined `score` on the group's own tier -- finding nothing, and collapsing
    every candidate to 0.0 -- would elect the older track instead.
    """
    # source: canonical-tracks.md "Representative track" -- "highest
    # score.all_time (the track tier's own score -- not the group's tier; a
    # version group's representative is elected by its member tracks'
    # *track*-tier scores...)".
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_membership(conn, track_id="ta", added_at=OLD)
    builders.make_membership(conn, track_id="tb", added_at=NEW)
    builders.make_score(conn, "track", "ta", all_time=20.0)
    builders.make_score(conn, "track", "tb", all_time=90.0)
    # A version-tier row for this very group, which the rule must ignore.
    builders.make_score(conn, "version", group["version"], all_time=99.0)

    assert rep(conn, group["version"]) == "tb"


def test_a_pin_wins_over_the_score(conn):
    # source: scoring-H.md §11.3 -- "a manual pin
    # (canonical_group.representative_track_id) still wins over the score."
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "track", "ta", all_time=90.0)
    builders.make_score(conn, "track", "tb", all_time=10.0)

    canonical.pin_representative(conn, "tb")

    assert rep(conn, group["song"]) == "tb"


def test_with_no_scores_the_election_falls_back_to_the_oldest_added_at(conn):
    """The degraded case, and the one that pins the dropped live filter.

    `ta`'s only membership is **removed**; `tb`'s is live and newer. The
    pre-H rule filtered to live rows, so it would elect `tb`. The current
    rule takes `MIN(added_at)` over all rows, so it elects `ta`.
    """
    # source: canonical-tracks.md "Representative track" -- "If the score
    # table is ever empty... the election silently collapses to the tail of
    # this rule (oldest added_at -> lowest track_id, without the old
    # live-membership filter)", the tail itself being "oldest added_at (over
    # *all* membership rows for the track, live or not)".
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_membership(conn, track_id="ta", added_at=OLD, removed_at=NEW)
    builders.make_membership(conn, track_id="tb", added_at=NEW)

    assert conn.execute("SELECT COUNT(*) FROM score").fetchone()[0] == 0
    assert rep(conn, group["song"]) == "ta"


def test_a_track_with_no_memberships_at_all_sorts_last(conn):
    # source: canonical-tracks.md "Representative track" -- "a track with no
    # membership rows at all sorts last, not first". (canonical.py coalesces
    # a NULL oldest_added to "9999", which is what puts it at the end rather
    # than the start.)
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_membership(conn, track_id="tb", added_at=NEW)

    assert rep(conn, group["song"]) == "tb"


def test_the_final_tiebreak_is_the_lowest_track_id(conn):
    # source: canonical-tracks.md "Representative track" -- "-> lowest
    # track_id", the last key. Equal scores and equal added_at leave nothing
    # else to decide it.
    group = builders.make_group(conn, ["tb", "ta"])
    builders.make_membership(conn, track_id="ta", added_at=OLD)
    builders.make_membership(conn, track_id="tb", added_at=OLD)
    builders.make_score(conn, "track", "ta", all_time=50.0)
    builders.make_score(conn, "track", "tb", all_time=50.0)

    assert rep(conn, group["song"]) == "ta"


def test_an_unscored_track_defaults_to_zero_rather_than_being_dropped(conn):
    # source: canonical-tracks.md "Representative track" -- the track tier's
    # score, "defaulting to 0.0 for an unscored track". It still stands for
    # election, so a group where nothing is scored but one track still
    # returns that track rather than None.
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "track", "tb", all_time=5.0)

    # tb's 5.0 beats ta's implicit 0.0 even though 5.0 is a low score.
    assert rep(conn, group["song"]) == "tb"


def test_representative_of_an_unknown_group_raises(conn):
    # characterization -- the guard exists so a stale group id surfaces as an
    # error rather than as a silent None the caller renders as a blank row.
    with pytest.raises(ValueError, match="no canonical_group"):
        canonical.representative(conn, 9999)


def test_representative_of_an_empty_group_is_none(conn):
    # characterization -- cleanup deletes empty groups, so this is only
    # reachable with cleanup deferred; it returns None rather than raising.
    group_id = conn.execute(
        "INSERT INTO canonical_group (tier) VALUES ('song')"
    ).lastrowid
    conn.commit()

    assert canonical.representative(conn, group_id) is None


# -- Pinning ---------------------------------------------------------------


def test_pinning_writes_the_song_group_only(conn):
    # source: canonical-tracks.md "Representative track" -- "pinning stays
    # song-tier only; a version group always uses the computed election,
    # never a pin."
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "track", "tb", all_time=90.0)

    canonical.pin_representative(conn, "ta")

    assert rep(conn, group["song"]) == "ta"
    # The version group is untouched, so it still elects by score.
    assert rep(conn, group["version"]) == "tb"


def test_pinning_a_track_with_no_track_group_row_raises(conn):
    # characterization -- ensure_track_groups() runs ahead of every
    # /dev/canonical* request, so this is a programming error rather than a
    # state the UI can reach.
    builders.make_track(conn, "ta")

    with pytest.raises(ValueError, match="no track_group row"):
        canonical.pin_representative(conn, "ta")


# -- Group membership helpers ----------------------------------------------


def test_group_members_lists_the_tier_the_group_belongs_to(conn):
    # characterization -- group_members reads the tier off canonical_group
    # and picks the matching track_group column, so the same helper works at
    # any tier.
    group = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"], song=group["song"])

    assert sorted(canonical.group_members(conn, group["song"])) == ["ta", "tb"]
    assert canonical.group_members(conn, group["version"]) == ["ta"]


def test_group_members_of_an_unknown_group_raises(conn):
    # characterization -- same guard as representative()'s.
    with pytest.raises(ValueError, match="no canonical_group"):
        canonical.group_members(conn, 9999)


def test_groups_for_an_ungrouped_track_is_none(conn):
    # characterization -- the None that callers branch on (see
    # canonical_detect.ad_hoc_group's fallback and app.py's cross-apply 400).
    builders.make_track(conn, "ta")

    assert canonical.groups_for_track(conn, "ta") is None


# -- The song listing ------------------------------------------------------


def song_ids(rows):
    return sorted(row["song_id"] for row in rows)


def test_the_listing_hides_singleton_groups_by_default(conn):
    # source: canonical-tracks.md / viewer-page.md -- the viewer lists
    # *groups*, and a one-track song group is the ungrouped default state
    # rather than a grouping decision. include_singletons is the toggle.
    pair = builders.make_group(conn, ["ta", "tb"])
    lone = builders.make_group(conn, ["tc"])

    assert song_ids(canonical.song_group_rows(conn)) == [pair["song"]]
    assert song_ids(canonical.song_group_rows(conn, include_singletons=True)) == sorted(
        [pair["song"], lone["song"]]
    )


def test_the_listing_reports_each_groups_track_count(conn):
    # characterization -- track_count is what the listing renders beside the
    # group name, and what the caller ranks alongside score.
    builders.make_group(conn, ["ta", "tb", "tc"])

    assert canonical.song_group_rows(conn)[0]["track_count"] == 3


def test_the_query_matches_a_track_name(conn):
    # characterization -- the filter is a LIKE over track name OR artist
    # name, matching the Groups box on /dev/canonical.
    builders.make_track(conn, "ta", name="Cornelia Street")
    builders.make_track(conn, "tb", name="Cornelia Street")
    match = builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc", name="Cruel Summer")
    builders.make_track(conn, "td", name="Cruel Summer")
    builders.make_group(conn, ["tc", "td"])

    assert song_ids(canonical.song_group_rows(conn, query="cornelia")) == [match["song"]]


def test_the_query_matches_an_artist_name(conn):
    # characterization -- the EXISTS arm over track_artist/artist, which is
    # what makes typing an artist into the Groups box work at all.
    builders.make_artist(conn, "ar-1", name="Phoebe Bridgers")
    builders.make_track(conn, "ta", artists=["ar-1"])
    builders.make_track(conn, "tb", artists=["ar-1"])
    match = builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")
    builders.make_track(conn, "td")
    builders.make_group(conn, ["tc", "td"])

    assert song_ids(canonical.song_group_rows(conn, query="bridgers")) == [match["song"]]


def test_the_listing_is_unhydrated(conn):
    # source: canonical.song_group_rows' docstring and CLAUDE.md's
    # /dev/canonical page-budget note -- "work proportional to what's
    # rendered": the rows carry id and size only, and the caller hydrates the
    # capped slice. A row arriving pre-hydrated would mean the split that
    # bought 307ms had been undone.
    builders.make_group(conn, ["ta", "tb"])

    assert set(canonical.song_group_rows(conn)[0]) == {"song_id", "track_count"}


def test_hydration_adds_the_representative_and_the_pin_flag(conn):
    # characterization -- hydrate_song_groups is the second half of that
    # split, and this is the shape canonical.html renders.
    builders.make_track(conn, "ta", name="August")
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "track", "ta", all_time=90.0)

    hydrated = canonical.hydrate_song_groups(conn, canonical.song_group_rows(conn))

    assert hydrated[0]["song_id"] == group["song"]
    assert hydrated[0]["track_count"] == 2
    assert hydrated[0]["representative_track_id"] == "ta"
    assert hydrated[0]["representative"]["name"] == "August"
    assert hydrated[0]["pinned"] is False


def test_hydration_reports_a_pinned_group_as_pinned(conn):
    # characterization -- the flag drives the star in the listing, and is
    # separate from *which* track won, since an election can agree with a pin.
    builders.make_group(conn, ["ta", "tb"])
    canonical.pin_representative(conn, "ta")

    hydrated = canonical.hydrate_song_groups(conn, canonical.song_group_rows(conn))

    assert hydrated[0]["pinned"] is True


# -- Track display ---------------------------------------------------------


def test_track_display_counts_only_live_memberships(conn):
    # characterization -- "live" means removed_at IS NULL everywhere in this
    # codebase, and live_count is what the leaf-meta line renders.
    builders.make_track(conn, "ta")
    builders.make_membership(conn, track_id="ta")
    builders.make_membership(conn, playlist_id=None, track_id="ta", removed_at=NEW)

    assert canonical.track_display(conn, "ta")["live_count"] == 1


def test_track_display_renders_artists_through_the_view(conn):
    # source: canonical.py / CLAUDE.md -- "track.artists is write-only, never
    # read"; the display string comes from the track_artists view. Written
    # with track.artists deliberately set to a wrong value, so a read of the
    # column would be visible here.
    builders.make_artist(conn, "ar-1", name="Lorde")
    builders.make_track(conn, "ta", artists=["ar-1"])
    conn.execute("UPDATE track SET artists = 'WRONG' WHERE track_id = 'ta'")
    conn.commit()

    assert canonical.track_display(conn, "ta")["artists"] == "Lorde"


# -- Artist credits --------------------------------------------------------


def test_artist_credits_come_back_in_credit_order(conn):
    # source: canonical.artist_credits_for_tracks' docstring -- "{track_id:
    # [{"artist_id", "name"}, ...]} in credit order", which is what lets a
    # page link each name separately rather than rendering the pre-joined
    # display string.
    builders.make_artist(conn, "ar-b", name="Bon Iver")
    builders.make_artist(conn, "ar-a", name="Taylor Swift")
    builders.make_track(conn, "ta", artists=["ar-a", "ar-b"])

    credits = canonical.artist_credits_for_tracks(conn, ["ta"])

    # Credit order, not alphabetical and not id order -- "ar-a" is second
    # alphabetically only by accident; position is what decides.
    assert [c["artist_id"] for c in credits["ta"]] == ["ar-a", "ar-b"]
    assert [c["name"] for c in credits["ta"]] == ["Taylor Swift", "Bon Iver"]


def test_artist_credits_batches_several_tracks(conn):
    # characterization -- one batched query over exactly the tracks a page
    # needs; a track with no credits is simply absent from the mapping.
    builders.make_artist(conn, "ar-1", name="SZA")
    builders.make_track(conn, "ta", artists=["ar-1"])
    builders.make_track(conn, "tb", artists=["ar-1"])

    credits = canonical.artist_credits_for_tracks(conn, ["ta", "tb"])

    assert sorted(credits) == ["ta", "tb"]


def test_artist_credits_of_nothing_is_empty(conn):
    # characterization -- the early return that keeps the IN () placeholder
    # list from being built empty, which SQLite would reject.
    assert canonical.artist_credits_for_tracks(conn, []) == {}


# -- Trees -----------------------------------------------------------------


def test_the_song_tree_nests_versions_recordings_releases_and_tracks(conn):
    # source: canonical.py's song_tree docstring and grouping-engine.md's
    # nesting invariant -- the tree is the four tiers in order, bottoming out
    # at track display dicts.
    group = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"], song=group["song"])

    tree = canonical.song_tree(conn, group["song"])

    assert tree["song_id"] == group["song"]
    assert sorted(tree["track_ids"]) == ["ta", "tb"]
    # Two versions under the song, because tb shares only the song group.
    assert len(tree["versions"]) == 2
    version = next(v for v in tree["versions"] if v["version_id"] == group["version"])
    release = version["recordings"][0]["releases"][0]
    assert [t["track_id"] for t in release["tracks"]] == ["ta"]


def test_two_tracks_in_one_release_group_share_every_level_of_the_tree(conn):
    # source: grouping-engine.md invariant 1 (nesting) -- sharing a release
    # means sharing recording, version and song, so the tree has exactly one
    # node at each level.
    group = builders.make_group(conn, ["ta", "tb"])

    tree = canonical.song_tree(conn, group["song"])

    assert len(tree["versions"]) == 1
    assert len(tree["versions"][0]["recordings"]) == 1
    assert len(tree["versions"][0]["recordings"][0]["releases"]) == 1
    assert sorted(
        t["track_id"] for t in tree["versions"][0]["recordings"][0]["releases"][0]["tracks"]
    ) == ["ta", "tb"]


def test_group_tree_works_from_a_version_group_down(conn):
    # source: canonical.group_tree's docstring -- "song_tree(conn, song_id) is
    # group_tree(conn, "song", song_id)", i.e. the helper is tier-agnostic,
    # which is what the album/artist/search pages use at version tier.
    group = builders.make_group(conn, ["ta", "tb"])

    tree = canonical.group_tree(conn, "version", group["version"])

    assert tree["version_id"] == group["version"]
    assert sorted(tree["track_ids"]) == ["ta", "tb"]
    assert "recordings" in tree


def test_subtree_of_a_release_group_is_plain_track_ids(conn):
    # source: canonical.subtree's docstring -- "a release has no finer tier,
    # so its subtree is simply its member track ids."
    group = builders.make_group(conn, ["ta", "tb"])

    assert sorted(canonical.subtree(conn, "release", group["release"])) == ["ta", "tb"]


# -- Counts and badges -----------------------------------------------------


def test_tier_counts_separate_total_from_non_singleton(conn):
    # characterization -- the Stats panel on /dev/canonical: how many groups
    # exist at each tier, and how many hold more than one track.
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_group(conn, ["tc"], song=group["song"])

    counts = canonical.tier_counts(conn)

    # Three tracks in one song group -- so song has a single, non-singleton
    # group -- but tc has its own release group, so release has two of which
    # one is non-singleton. That difference between the tiers is the whole
    # point of reporting them separately.
    assert counts["song"] == {"total": 1, "non_singleton": 1}
    assert counts["release"] == {"total": 2, "non_singleton": 1}


def test_auto_grouped_ids_are_the_tagged_groups_only(conn):
    # source: canonical.auto_grouped_ids' docstring -- the viewer's badge,
    # driven by canonical_group.auto_run_id, which a later manual edit
    # legitimately drops.
    tagged = builders.make_group(conn, ["ta"])
    builders.make_group(conn, ["tb"])
    run_id = conn.execute("INSERT INTO auto_group_run DEFAULT VALUES").lastrowid
    conn.execute(
        "UPDATE canonical_group SET auto_run_id = ? WHERE id = ?", (run_id, tagged["song"])
    )
    conn.commit()

    assert canonical.auto_grouped_ids(conn) == {tagged["song"]}


# -- The per-connection artist-display cache -------------------------------


def test_the_artist_display_cache_does_not_see_a_later_write(conn):
    """Pinned deliberately: this cache never invalidates.

    `_artist_display` loads the whole `track_artists` view once per
    connection. The docstring's safety argument is that Flask hands out a
    fresh connection per request -- so the trap is a single request that
    writes artist data and then re-renders a display on the same connection.
    """
    # characterization -- canonical._artist_display's docstring ("loaded once
    # per connection... the cache can never outlive the data it was built
    # from"). Asserting the stale read is what makes the constraint visible
    # to P3, which moves code between modules and could easily create the
    # write-then-render-in-one-request case this rules out.
    builders.make_artist(conn, "ar-1", name="Before")
    builders.make_track(conn, "ta", artists=["ar-1"])
    assert canonical.track_display(conn, "ta")["artists"] == "Before"

    conn.execute("UPDATE artist SET name = 'After' WHERE artist_id = 'ar-1'")
    conn.commit()

    assert canonical.track_display(conn, "ta")["artists"] == "Before"
