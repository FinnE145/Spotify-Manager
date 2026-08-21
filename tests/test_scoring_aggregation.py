"""Query-time aggregation (docs/specs/scoring-H.md §5) and the
`track_artist_role` view it depends on.

The view is tested here rather than beside `db.py`'s other views because
§5.3's `FEATURED_WEIGHT` is the only thing that reads it -- P1-021's second
floor item for this session.
"""

import sqlite3

import pytest

import builders
import scoring


# ---------------------------------------------------------------- track_artist_role


def test_every_credit_is_primary_when_none_of_them_is_an_album_artist(conn):
    """Various Artists compilation: album credited to "va", track credited to
    two artists neither of whom is an album artist. The fallback promotes
    both to primary."""
    # source: scoring-H.md §5.3 -- "the fallback the view adds specifically
    # so Various Artists compilations don't misclassify the real artist as
    # featured -- when *none* of the track's credited artists match any
    # album artist ... the fallback makes it a per-track decision that can
    # promote every credit on a track to `primary` at once."
    album_id = builders.make_album(conn, artists=["va"])
    builders.make_track(conn, track_id="t1", album_id=album_id, artists=["real1", "real2"])

    roles = {
        row["artist_id"]: row["role"]
        for row in conn.execute(
            "SELECT artist_id, role FROM track_artist_role WHERE track_id = ?", ("t1",)
        )
    }
    assert roles["real1"] == "primary"
    assert roles["real2"] == "primary"


def test_a_credit_that_is_not_an_album_artist_is_featured_when_another_credit_is(conn):
    """The negative case that stops an "always primary" implementation from
    passing the test above: when one credit DOES match an album artist, a
    second non-matching credit is genuinely featured."""
    # source: scoring-H.md §5.3 -- "a credit is `primary` when its artist is
    # also an album artist"
    album_id = builders.make_album(conn, artists=["main"])
    builders.make_track(conn, track_id="t1", album_id=album_id, artists=["main", "guest"])

    roles = {
        row["artist_id"]: row["role"]
        for row in conn.execute(
            "SELECT artist_id, role FROM track_artist_role WHERE track_id = ?", ("t1",)
        )
    }
    assert roles["main"] == "primary"
    assert roles["guest"] == "featured"


def test_two_credits_aliased_onto_one_artist_collapse_to_one_row(conn):
    """Two track_artist rows that both alias-resolve to the same canonical
    artist must produce one role row, not two -- db.py's own comment on
    track_artist_credit: "Grouped so that two credits aliased onto one
    artist collapse to one row rather than rendering the canonical name
    twice.\""""
    # source: characterization, db.py's track_artist_credit view comment
    album_id = builders.make_album(conn, artists=["main"])
    builders.make_track(conn, track_id="t1", album_id=album_id, artists=["dupe_a", "dupe_b"])
    builders.make_artist(conn, artist_id="canonical")
    for dupe in ("dupe_a", "dupe_b"):
        conn.execute(
            "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
            (dupe, "canonical"),
        )
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM track_artist_role WHERE track_id = ?", ("t1",)
    ).fetchone()["n"]
    assert count == 1


# ---------------------------------------------------------------- the aggregators


def test_a_song_combines_its_version_scores(conn):
    """Same fixture and same wrong-answer trap as test_group_score_combines_
    in_normalized_space_not_display_space in test_scoring_math.py: 76.7244
    if combined in display space, 72.8869 as a plain mean."""
    # source: scoring-H.md §5 -- "Song groups aggregate their constituent
    # version scores, using the same combiner as albums, artists and
    # playlists ... A song is not special."
    g1 = builders.make_group(conn, ["t1"])
    g2 = builders.make_group(conn, ["t2"], song=g1["song"])
    builders.make_score(conn, "version", g1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g2["version"], all_time=25.0, recent=25.0)

    result = scoring.song_scores(conn, [g1["song"]])
    assert result[g1["song"]]["all_time"] == pytest.approx(87.0721, abs=1e-3)


def test_a_song_is_never_materialized(conn):
    # source: scoring-H.md §9.1 -- "Song, album, artist, playlist and
    # arbitrary collections are **aggregated at query time**"; the `score`
    # table's own CHECK constraint enumerates the four stored tiers.
    g1 = builders.make_group(conn, ["t1"])
    builders.make_group(conn, ["t2"], song=g1["song"])
    scoring.recompute(conn)

    count = conn.execute("SELECT COUNT(*) FROM score WHERE tier = 'song'").fetchone()[0]
    assert count == 0

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO score (tier, group_id, all_time, recent) VALUES ('song', 'x', 1.0, 1.0)"
        )


def test_an_album_is_padded_with_its_untouched_tracks(conn):
    """Album with total_tracks=4 and two known tracks, both scored display
    100.0 (norm 1.0). Padded with 2 zero-scoring members."""
    # source: scoring-H.md §5.4 -- "An album's member list is padded with
    # `total_tracks − known` zero-scoring members before combining."
    album_id = builders.make_album(conn, total_tracks=4)
    builders.make_track(conn, track_id="t1", album_id=album_id)
    builders.make_track(conn, track_id="t2", album_id=album_id)
    g1 = builders.make_group(conn, ["t1"])
    g2 = builders.make_group(conn, ["t2"])
    builders.make_score(conn, "version", g1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g2["version"], all_time=100.0, recent=100.0)

    result = scoring.album_scores(conn, [album_id])
    assert result[album_id]["all_time"] == pytest.approx(87.0551, abs=1e-3)
    # the unpadded wrong answer -- "owning one great song rates the whole album"
    assert result[album_id]["all_time"] != pytest.approx(100.0, abs=1e-1)


def test_an_album_holding_more_tracks_than_it_claims_is_not_padded_negatively(conn):
    # source: scoring-H.md §5.4's "Known interaction -- duplicate album
    # rows"; the `max(..., 0)` guard in album_scores
    album_id = builders.make_album(conn, total_tracks=1)
    builders.make_track(conn, track_id="t1", album_id=album_id)
    builders.make_track(conn, track_id="t2", album_id=album_id)
    g1 = builders.make_group(conn, ["t1"])
    g2 = builders.make_group(conn, ["t2"])
    builders.make_score(conn, "version", g1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g2["version"], all_time=100.0, recent=100.0)

    result = scoring.album_scores(conn, [album_id])
    assert result[album_id]["all_time"] == pytest.approx(100.0, abs=1e-3)  # no negative padding


def test_an_album_with_no_known_tracks_scores_zero(conn):
    # source: characterization -- combine()'s all-zero guard, reached
    # through padding when `have == 0`
    album_id = builders.make_album(conn, total_tracks=4)
    result = scoring.album_scores(conn, [album_id])
    assert result[album_id]["all_time"] == 0.0


def test_a_playlist_counts_only_its_live_members(conn):
    """One live member (score 100.0) and one removed member (score 50.0).
    The removed member must not count -- 87.5925 would be the wrong answer
    if it did."""
    # source: scoring-H.md §5 / playlist_scores' docstring -- "combine() over
    # each playlist's **live** member version scores"; "live" is
    # `removed_at IS NULL` per §4.1.
    playlist_id = builders.make_playlist(conn)
    g1 = builders.make_group(conn, ["t1"])
    builders.make_membership(conn, playlist_id=playlist_id, track_id="t1")  # live
    g2 = builders.make_group(conn, ["t2"])
    builders.make_membership(
        conn, playlist_id=playlist_id, track_id="t2", removed_at=builders.days_ago(0)
    )
    builders.make_score(conn, "version", g1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g2["version"], all_time=50.0, recent=50.0)

    result = scoring.playlist_scores(conn, [playlist_id])
    assert result[playlist_id]["all_time"] == pytest.approx(100.0, abs=1e-3)
    assert result[playlist_id]["all_time"] != pytest.approx(87.5925, abs=1e-2)


def test_a_featured_only_credit_is_discounted_in_an_artist_score(conn):
    """Artist credited on two versions: primary on one (album 1, matches the
    album artist), featured-only on the other (album 2, credited alongside
    the real album artist "other"). If FEATURED_WEIGHT were ignored, both
    versions would carry weight 1.0 and the score would be 87.5925."""
    # source: scoring-H.md §5.3 + §10.1's FEATURED_WEIGHT = 0.6
    album1 = builders.make_album(conn, artists=["art1"])
    builders.make_track(conn, track_id="t1", album_id=album1, artists=["art1"])
    g1 = builders.make_group(conn, ["t1"])

    album2 = builders.make_album(conn, artists=["other"])
    builders.make_track(conn, track_id="t2", album_id=album2, artists=["other", "art1"])
    g2 = builders.make_group(conn, ["t2"])

    builders.make_score(conn, "version", g1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g2["version"], all_time=50.0, recent=50.0)

    result = scoring.artist_scores(conn, ["art1"])
    assert result["art1"]["all_time"] == pytest.approx(91.367, abs=1e-3)
    assert result["art1"]["all_time"] != pytest.approx(87.5925, abs=1e-2)


def test_an_artist_group_score_unions_the_pairs_versions(conn):
    """Two artists sharing one version and each holding a distinct one --
    the group's score is the combine over the union, not a comparison of
    two separately-computed scores."""
    # source: artist_group_score's docstring -- "the score of everything
    # either credits, not a comparison of two separately-computed numbers.
    # The combiner does not know what it is combining (§5)."
    album1 = builders.make_album(conn, artists=["artA"])
    builders.make_track(conn, track_id="t_shared", album_id=album1, artists=["artA", "artB"])
    g_shared = builders.make_group(conn, ["t_shared"])

    album2 = builders.make_album(conn, artists=["artA"])
    builders.make_track(conn, track_id="t_a_only", album_id=album2, artists=["artA"])
    g_a = builders.make_group(conn, ["t_a_only"])

    album3 = builders.make_album(conn, artists=["artB"])
    builders.make_track(conn, track_id="t_b_only", album_id=album3, artists=["artB"])
    g_b = builders.make_group(conn, ["t_b_only"])

    builders.make_score(conn, "version", g_shared["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g_a["version"], all_time=50.0, recent=50.0)
    builders.make_score(conn, "version", g_b["version"], all_time=25.0, recent=25.0)

    group_score = scoring.artist_group_score(conn, ["artA", "artB"])
    a_alone = scoring.artist_scores(conn, ["artA"])["artA"]
    b_alone = scoring.artist_scores(conn, ["artB"])["artB"]

    assert group_score["all_time"] != pytest.approx(a_alone["all_time"], abs=1e-2)
    assert group_score["all_time"] != pytest.approx(b_alone["all_time"], abs=1e-2)


def test_a_version_one_artist_leads_is_not_featured_only_for_the_group(conn):
    """v1: "lead" is primary, "feat" is featured -- the group must NOT
    discount v1, since not every credited artist reaches it only through a
    featured credit. v2: both "lead" and "feat" are featured (alongside a
    third album artist) -- the group DOES discount v2.

    The "or"-merge bug this discriminates against would discount v1 too
    (since "feat" alone is featured there), which uniformly scales both
    versions' weights and produces the SAME number as no discount at all
    (87.5925) -- the uniform scaling cancels in combine()'s ratio. Only the
    correct AND-merge (discount v2 alone) gives 91.367.
    """
    # source: scoring-H.md §5.3 -- featured_only means "**every** credit
    # tying this version to this artist is a featured one", generalized
    # across the group by artist_group_score's
    # `merged[vid] = merged.get(vid, True) and featured_only`
    album_v1 = builders.make_album(conn, artists=["lead"])
    builders.make_track(conn, track_id="t_v1", album_id=album_v1, artists=["lead", "feat"])
    g_v1 = builders.make_group(conn, ["t_v1"])

    album_v2 = builders.make_album(conn, artists=["third"])
    builders.make_track(
        conn, track_id="t_v2", album_id=album_v2, artists=["third", "lead", "feat"]
    )
    g_v2 = builders.make_group(conn, ["t_v2"])

    builders.make_score(conn, "version", g_v1["version"], all_time=100.0, recent=100.0)
    builders.make_score(conn, "version", g_v2["version"], all_time=50.0, recent=50.0)

    result = scoring.artist_group_score(conn, ["lead", "feat"])
    assert result["all_time"] == pytest.approx(91.367, abs=1e-3)
    assert result["all_time"] != pytest.approx(87.5925, abs=1e-2)


def test_scores_for_tier_returns_the_key_types_it_was_given(conn):
    """int in, int out for version/recording/release; the track_id string
    in, string out for track; an unknown id is omitted rather than
    defaulted."""
    # source: scores_for_tier's docstring -- "Keyed by whatever type the
    # entries of group_ids already are (int for version/recording/release,
    # the track_id string for track)."
    g1 = builders.make_group(conn, ["t1"])
    builders.make_score(conn, "version", g1["version"], all_time=42.0, recent=10.0)
    builders.make_score(conn, "track", "t1", all_time=99.0, recent=5.0)

    version_result = scoring.scores_for_tier(conn, "version", [g1["version"], 999999])
    track_result = scoring.scores_for_tier(conn, "track", ["t1", "no-such-track"])

    assert set(version_result) == {g1["version"]}
    assert isinstance(list(version_result)[0], int)
    assert set(track_result) == {"t1"}
    assert isinstance(list(track_result)[0], str)


def test_get_both_returns_none_before_the_first_recompute(conn):
    # source: get_both's docstring -- "or None if it doesn't exist yet (e.g.
    # before the first recompute)."
    g1 = builders.make_group(conn, ["t1"])
    assert scoring.get_both(conn, "version", g1["version"]) is None


def test_every_aggregator_returns_empty_for_an_empty_id_list(conn):
    # source: characterization -- the `if not ...: return {}` guards
    assert scoring.song_scores(conn, []) == {}
    assert scoring.album_scores(conn, []) == {}
    assert scoring.artist_scores(conn, []) == {}
    assert scoring.playlist_scores(conn, []) == {}
    assert scoring.scores_for_tier(conn, "version", []) == {}
    assert scoring.artist_group_score(conn, []) == {"all_time": 0.0, "recent": 0.0}
