"""`canonical_detect.index_data` -- `/dev/canonical`'s page assembly
(docs/codebase-health/P3_refactor.md §4.1.1).

Extracted out of `app.py`'s `canonical_index` in P3 session 3. It is tested
here for the reason P3-004 and P3-005 name between them: of the eleven payload
keys the *entity*-page extraction moved, nine were observable only by the
golden baseline -- a suite §3.4 deletes at the end of P3. Session 3 ran the
same measurement over this function's ten keys before deleting anything, and
found five held up by golden alone plus one (`pending_tier_count`) that
nothing observed at all. These are the assertions that make the compare
redundant rather than merely clean.

The listing pipeline (rank -> cap -> expand-deep-link -> hydrate) is tested
through the returned payload rather than through the page, because the whole
point of the extraction is that it can be: no request context, no template.
"""

import builders
import canonical_detect

_NO_SEARCH = dict(show_singletons=False, search_q="", expand_song_id=None)


def _index(conn, q="", cap=None, **overrides):
    """index_data with the route's defaults, so each test names only what it
    is about. `cap=None` is what the route passes for a *searched* page; the
    unfiltered load passes _LISTING_CAP."""
    kwargs = {**_NO_SEARCH, **overrides}
    return canonical_detect.index_data(conn, q, cap=cap, **kwargs)


def test_total_tracks_counts_the_library_not_the_listing(conn):
    # source: characterization of canonical.html's header row, which reads
    # "N tracks" for the whole library. The fixture makes the two numbers
    # disagree three ways -- 3 tracks, 1 listed group, 2 listed tracks -- so
    # an implementation counting rendered rows, rendered tracks or groups
    # produces a different number rather than coinciding.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_track(conn, "tc")

    data = _index(conn)

    assert data["total_tracks"] == 3
    assert len(data["groups"]) == 1


def test_reviewed_reports_the_pair_count_and_the_latest_decision(conn):
    # source: characterization of canonical.html's "N pairs reviewed, last
    # <date>" line, which reads reviewed_pair. Two rows with different
    # decided_at, deliberately inserted oldest-last, so MAX is distinguishable
    # from MIN and from "whatever came back first".
    builders.make_track(conn, "ta")
    builders.make_track(conn, "tb")
    builders.make_track(conn, "tc")
    conn.execute(
        "INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at) VALUES (?, ?, ?)",
        ("ta", "tb", "2026-05-05T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO reviewed_pair (track_id_a, track_id_b, decided_at) VALUES (?, ?, ?)",
        ("ta", "tc", "2026-01-01T00:00:00Z"),
    )
    conn.commit()

    data = _index(conn)

    assert data["reviewed_count"] == 2
    assert data["reviewed_latest"] == "2026-05-05T00:00:00Z"


def test_group_total_is_every_match_even_when_the_listing_is_capped(conn):
    # source: _macros.html's listing_cap_note ("Showing the top N of M"),
    # which needs the uncapped total -- so group_total must be taken before
    # the cap, not from the slice. len(groups) == 3 would be the bug.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_group(conn, ["tc", "td"])
    builders.make_group(conn, ["te", "tf"])

    data = _index(conn, cap=1)

    assert len(data["groups"]) == 1
    assert data["group_total"] == 3


def test_the_listing_ranks_by_score_before_the_cap_applies(conn):
    # source: docs/specs/scoring-H.md §11.1 -- "both are capped, so
    # name-ordering means they currently return the alphabetically-first N
    # rather than the best N". The fixture disagrees with the fallback rule
    # as well as the spec's: song_id ascending is the tiebreak, so the
    # *second* group made -- the higher id -- is given the better score. A
    # score-blind implementation returns the other one (P2-005).
    low = builders.make_group(conn, ["ta", "tb"])
    high = builders.make_group(conn, ["tc", "td"])
    builders.make_score(conn, "version", low["version"], all_time=20.0)
    builders.make_score(conn, "version", high["version"], all_time=90.0)

    data = _index(conn, cap=1)

    assert [g["song_id"] for g in data["groups"]] == [high["song"]]


def test_an_expanded_group_survives_a_cap_that_would_have_cut_it(conn):
    # source: the comment moved with this code -- "a deep link to a group
    # past the cap would otherwise land on a page that doesn't contain it".
    # The expanded group is the low-scoring one, so the cap genuinely cuts it
    # and only the deep-link branch can put it back.
    high = builders.make_group(conn, ["ta", "tb"])
    low = builders.make_group(conn, ["tc", "td"])
    builders.make_score(conn, "version", high["version"], all_time=90.0)
    builders.make_score(conn, "version", low["version"], all_time=20.0)

    cut = _index(conn, cap=1)
    assert [g["song_id"] for g in cut["groups"]] == [high["song"]]

    data = _index(conn, cap=1, expand_song_id=low["song"])

    assert sorted(g["song_id"] for g in data["groups"]) == sorted([high["song"], low["song"]])


def test_artist_credits_cover_every_track_the_trees_render(conn):
    # source: canonical.artist_credits_for_tracks' docstring and the comment
    # moved with this code -- the ids are gathered "up front so
    # artist_credits_for_tracks is one batched query rather than one per
    # track". Both members carry a credit, and the group has no pinned
    # representative, so an implementation gathering only representatives
    # covers one of the two rather than both.
    builders.make_artist(conn, "ar-1", name="Phoebe Bridgers")
    builders.make_track(conn, "ta", artists=["ar-1"])
    builders.make_track(conn, "tb", artists=["ar-1"])
    builders.make_group(conn, ["ta", "tb"])

    credits = _index(conn)["artist_credits"]

    assert sorted(credits) == ["ta", "tb"]
    assert [c["name"] for c in credits["ta"]] == ["Phoebe Bridgers"]


def test_pending_tier_count_honours_the_two_member_filter(conn):
    # source: CLAUDE.md's canonical_detect entry -- "pending_song_ids owns
    # the 'group still has >=2 members' filter, so the count on
    # /dev/canonical and the items ?queue=pending serves can't disagree."
    # Two pending rows, one of them in a singleton song group: a count of
    # pending_tier_review rows says 2, the correct count says 1.
    builders.make_group(conn, ["ta", "tb"])
    builders.make_group(conn, ["tc"])
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('ta')")
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('tc')")
    conn.commit()

    assert _index(conn)["pending_tier_count"] == 1


def test_pending_tier_count_is_zero_with_nothing_queued(conn):
    # source: same clause -- the paired zero case, so the test above cannot
    # pass by an implementation that returns 1 for any reason at all.
    builders.make_group(conn, ["ta", "tb"])

    assert _index(conn)["pending_tier_count"] == 0


def test_the_track_search_ranks_by_score_before_its_hundred_row_cap(conn):
    # source: docs/specs/scoring-H.md §11.1 -- "/dev/canonical search
    # results" is one of the two search sites that table moves off name
    # ordering, and "both are capped, so name-ordering means they currently
    # return the alphabetically-first N rather than the best N".
    #
    # Found by P3's Verify pass (P3-008): the cap itself is 100 and a literal,
    # so this asserts the *order* rather than the membership -- which is the
    # only thing observable below 100 matches, and is what a
    # `sorted(...)`-to-`list(...)` mutation changes. The fixture disagrees
    # with both fallbacks at once: "A" is first by the query's own
    # `ORDER BY t.name COLLATE NOCASE` and first by insertion, and is the one
    # given the worse score.
    builders.make_track(conn, "t-a", name="A Ranked Track")
    builders.make_track(conn, "t-b", name="B Ranked Track")
    builders.make_score(conn, "track", "t-a", all_time=20.0)
    builders.make_score(conn, "track", "t-b", all_time=90.0)

    results = _index(conn, search_q="Ranked Track")["search_results"]

    assert [r["name"] for r in results] == ["B Ranked Track", "A Ranked Track"]


def test_the_track_search_caps_at_a_hundred_results(conn):
    # source: S_survivors.md canonical_detect.py:694 -- `ranked_rows[:100]`
    # is the cap; a 101st match must not come back.
    for i in range(101):
        builders.make_track(conn, f"t-{i:03d}", name=f"Zebra {i:03d}")

    results = _index(conn, search_q="Zebra")["search_results"]

    assert len(results) == 100


def test_the_track_search_matches_by_the_tracks_own_artist_only(conn):
    # source: S_survivors.md canonical_detect.py:687 -- the EXISTS subquery
    # must join back through *this* track's own artist credit
    # (`x.track_id = t.track_id AND ar.name LIKE ?`), not any unrelated
    # track that merely exists elsewhere or shares the album's artist row.
    builders.make_artist(conn, "ar-1", name="Special Artist")
    builders.make_track(conn, "ta", name="Some Song", artists=["ar-1"])
    builders.make_track(conn, "tb", name="Other Song", artists=["ar-2"])

    results = _index(conn, search_q="Special Artist")["search_results"]

    assert [r["track_id"] for r in results] == ["ta"]


def test_the_deep_link_does_not_duplicate_a_group_already_shown(conn):
    # source: S_survivors.md canonical_detect.py:673 -- `expand_song_id and
    # not any(...)` guards the deep-link prepend; a group already inside the
    # cap must not be inserted a second time.
    high = builders.make_group(conn, ["ta", "tb"])
    low = builders.make_group(conn, ["tc", "td"])
    builders.make_score(conn, "version", high["version"], all_time=90.0)
    builders.make_score(conn, "version", low["version"], all_time=20.0)

    data = _index(conn, cap=2, expand_song_id=high["song"])

    assert [g["song_id"] for g in data["groups"]] == [high["song"], low["song"]]
