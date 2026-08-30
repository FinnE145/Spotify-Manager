"""search.py's matcher, cache and ranking (docs/specs/better-search-L.md,
docs/specs/better-search-L2.md).

Route-level rendering and the write-nothing guarantee live in test_routes.py
alongside the rest of the permanent sweep; this file is the matcher itself,
tested against `search.rank()`'s raw output rather than rendered HTML, so a
fixture can name exactly the id it expects without parsing a page.
"""

import pytest

import builders
import scoring
import search


def _song_ids(ranked):
    return {r["id"] for r in ranked["songs"]}


def _artist_ids(ranked):
    return {r["id"] for r in ranked["artists"]}


# -- Replaced-rule assertions (§2, §12) --------------------------------------


def test_a_song_matches_on_its_own_title_only_not_its_artists_name(conn):
    # source: better-search-L.md §2 -- replaces K §10's "track.name, or any
    # credited artist's name". The negative is the whole test: K's old
    # implementation returns "Wait a Minute!" here too, and would pass an
    # assertion that only checked "Willow" (the song) is present.
    willow_artist = builders.make_artist(conn, name="Willow")
    other_artist = builders.make_artist(conn, name="Someone Else")
    wait_track = builders.make_track(conn, name="Wait a Minute!", artists=[willow_artist])
    willow_song = builders.make_track(conn, name="Willow", artists=[other_artist])
    wait_group = builders.make_group(conn, [wait_track])
    willow_group = builders.make_group(conn, [willow_song])
    conn.commit()

    ranked = search.rank(conn, "willow")

    assert willow_group["version"] in _song_ids(ranked)
    assert wait_group["version"] not in _song_ids(ranked)
    assert willow_artist in _artist_ids(ranked)


def test_percent_finds_nothing_rather_than_everything(conn):
    # source: better-search-L.md §2 -- replaces K §10's unescaped-LIKE-
    # wildcard note. base_string deletes "%" as punctuation, so the
    # normalized query is empty and falls under MIN_QUERY_LEN.
    builders.make_track(conn, name="Anything At All")
    conn.commit()

    ranked = search.rank(conn, "%")

    assert ranked == {"songs": [], "albums": [], "artists": [], "playlists": [], "combined": []}


def test_a_query_shorter_than_min_query_len_returns_nothing(conn):
    # source: better-search-L.md §4.1 / §4.6 (MIN_QUERY_LEN=2).
    builders.make_track(conn, name="Anything At All")
    conn.commit()

    ranked = search.rank(conn, "a")

    assert ranked["combined"] == []


# -- The matcher (§4.3, §4.4, §12) -------------------------------------------


def test_typo_tolerance_finds_the_track(conn):
    # source: better-search-L.md §10.1 -- "bohemian rapsody" -> 0.970.
    track = builders.make_track(conn, name="Bohemian Rhapsody")
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "bohemian rapsody")

    assert group["version"] in _song_ids(ranked)


def test_word_order_does_not_matter(conn):
    # source: better-search-L.md §4.4 -- the token-coverage term is what
    # makes "rhapsody bohemian" find "Bohemian Rhapsody".
    track = builders.make_track(conn, name="Bohemian Rhapsody")
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "rhapsody bohemian")

    assert group["version"] in _song_ids(ranked)


def test_accent_folding_finds_the_artist(conn):
    # source: better-search-L.md §4.1 -- normalize.base_string is what makes
    # "beyonce" find "Beyoncé" for free.
    artist = builders.make_artist(conn, name="Beyoncé")
    conn.commit()

    ranked = search.rank(conn, "beyonce")

    assert artist in _artist_ids(ranked)


def test_the_assoc_bump_ranks_the_credited_track_higher(conn):
    # source: better-search-L2.md §4.2/§7.1 -- two tracks sharing the
    # **identical** title, one credited to the artist named in the query.
    # Identical titles isolate the associated-name term: with different
    # titles, `own` alone could explain the ordering. (L's `BUMP` is
    # retired; this is the ASSOC_WEIGHT-weighted coverage term now.)
    radiohead = builders.make_artist(conn, name="Radiohead")
    other = builders.make_artist(conn, name="Someone Else")
    match_track = builders.make_track(conn, name="Creep", artists=[radiohead])
    other_track = builders.make_track(conn, name="Creep", artists=[other])
    match_group = builders.make_group(conn, [match_track])
    other_group = builders.make_group(conn, [other_track])
    conn.commit()

    ranked = search.rank(conn, "radiohead creep")

    songs_by_id = {r["id"]: r for r in ranked["songs"]}
    assert match_group["version"] in songs_by_id
    assert other_group["version"] in songs_by_id
    assert songs_by_id[match_group["version"]]["relevance"] > songs_by_id[other_group["version"]]["relevance"]


def test_relevance_floor_drops_an_artist_only_match(conn):
    # source: better-search-L2.md §4.3, the exact worked case named there --
    # "Wait a Minute!" by Willow, on q=willow, is absent. Its own title
    # contributes nothing to the query at all (own_scores all 0), so §4.3
    # drops it *by rule* before relevance is computed -- not because a
    # RELEVANCE_FLOOR comparison happens to come out below 0.5. At
    # ASSOC_WEIGHT=0.5 the artist-only evidence alone would compute to
    # exactly 0.500, which is RELEVANCE_FLOOR and would survive a plain
    # floor check: this fixture is what distinguishes "dropped by the
    # explicit rule" from "dropped by a threshold coincidence" (L2 §4.3).
    willow = builders.make_artist(conn, name="Willow")
    track = builders.make_track(conn, name="Wait a Minute!", artists=[willow])
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "willow")

    assert group["version"] not in _song_ids(ranked)


def test_score_floor_lets_an_unscored_entity_still_rank(conn):
    # source: better-search-L.md §4.5 -- "scoring's lookups return nothing
    # for an entity with no materialized score, and a plain score * ...
    # would make that entity rank zero on an exact name match -- invisible,
    # silently". No scoring.recompute() call here, so the `score` table has
    # no row at all for this version -- exactly the hole SCORE_FLOOR closes.
    track = builders.make_track(conn, name="Brand New Unscored Track")
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "brand new unscored track")

    songs_by_id = {r["id"]: r for r in ranked["songs"]}
    assert group["version"] in songs_by_id
    assert songs_by_id[group["version"]]["rank_key"] > 0


# -- L2: the fuzzy gate (§4.1, §8) --------------------------------------------


def test_the_gate_zeroes_a_fallback_ratio_below_the_floor():
    # source: better-search-L2.md §4.1/§8 -- "assert the values, not
    # presence: a mutant that gates at 0.95 also returns 0.0 for the first
    # two." "test"/"greatest" and "test"/"best" are noise (0.667, 0.750);
    # "rapsody"/"rhapsody" is a real typo at 0.933 -- asserting its exact
    # value is what catches a gate raised to e.g. 0.95, which would zero it
    # too.
    assert search._tsim("test", "greatest") == 0.0
    assert search._tsim("test", "best") == 0.0
    assert search._tsim("rapsody", "rhapsody") == pytest.approx(0.9333333333333333)


def test_the_gate_does_not_touch_the_prefix_branch():
    # source: better-search-L2.md §4.1/§8, handoff §5's 1c -- "the prefix
    # branch is what makes an as-you-type query work ... it is not a fuzzy
    # match", and coverage confirmed this line was never executed by L's
    # suite at all.
    assert search._tsim("boh", "bohemian") == pytest.approx(0.90625)


def test_the_whole_string_reading_is_gated_too(conn):
    # source: better-search-L2.md §4.1/§8 -- "beyonce against beyond scores
    # 0.769 both per-token and whole-string, so a one-sided gate changes
    # nothing for it." An implementation that gates `_tsim` but forgets to
    # gate the whole-string reading inside `_name_token_scores` would still
    # let "Beyond" through here, at relevance 0.769 (above RELEVANCE_FLOOR).
    builders.make_artist(conn, name="Beyond")
    conn.commit()

    ranked = search.rank(conn, "beyonce")

    assert ranked["artists"] == []


def test_whole_still_carries_a_cross_boundary_match():
    # source: better-search-L2.md §4.2/§8, subsumes handoff §5's 1e --
    # "bohemianrhapsody" (no space) is one query token whose per-token
    # reading against "bohemian rhapsody" is gated to 0 (0.667 raw, below
    # FUZZY_FLOOR), while `whole` = 0.970 carries it across the token
    # boundary the query doesn't share. Both readings clear RELEVANCE_FLOOR
    # in L's version, so presence alone doesn't discriminate -- assert the
    # value.
    scores = search._name_token_scores(
        "bohemianrhapsody", ["bohemianrhapsody"], "bohemian rhapsody"
    )

    assert scores == (pytest.approx(0.9696969696969697),)


def test_a_single_token_typo_still_clears_stage_1(conn):
    # source: better-search-L2.md §8, handoff §5's 1d -- the existing typo
    # test above ("bohemian rapsody") is two-token, and "bohemian" alone
    # already admits the candidate at stage 1 regardless of TRIGRAM_FLOOR,
    # so the typo'd token itself never faces the trigram prefilter. A
    # single-token typo query does: "rapsody" covers 5 of its own 8
    # trigrams (0.625) against "bohemian rhapsody", which clears
    # TRIGRAM_FLOOR=0.5 but would not clear a mutant raised to e.g. 0.95.
    track = builders.make_track(conn, name="Bohemian Rhapsody")
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "rapsody")

    assert group["version"] in _song_ids(ranked)


def test_trigrams_pad_the_string_so_word_boundaries_participate():
    # source: better-search-L.md §4.3/L2 §8, handoff §5's 1f -- "two leading
    # spaces, one trailing, so word boundaries participate in the coverage
    # test." Direct assertion on the literal output; brittle by the
    # handoff's own admission, but there is no behavioural isolation for
    # this specific padding choice.
    assert search._trigrams("hi") == {"  h", " hi", "hi "}


# -- L2: per-token coverage and the own-must-contribute rule (§4.2-4.3, §8) --


def test_coverage_spreads_evidence_across_own_and_associated_names(conn):
    # source: better-search-L2.md §4.2/§8 -- "taylor swift cardigan covers
    # two tokens from the artist and one from the title, for
    # (0.5 + 0.5 + 1.0) / 3 = 0.667." This is the test that fails against
    # L's formula, where the mean-over-query-tokens `own` term is dragged
    # under RELEVANCE_FLOOR by the two unmatched tokens and the track is
    # dropped entirely (handoff §3) -- the discriminating case for the
    # whole step.
    artist = builders.make_artist(conn, name="Taylor Swift")
    track = builders.make_track(conn, name="cardigan", artists=[artist])
    group = builders.make_group(conn, [track])
    conn.commit()

    ranked = search.rank(conn, "taylor swift cardigan")

    songs_by_id = {r["id"]: r for r in ranked["songs"]}
    assert group["version"] in songs_by_id
    assert songs_by_id[group["version"]]["relevance"] == pytest.approx(2 / 3)


def test_a_self_titled_album_is_not_counted_twice(conn):
    # source: better-search-L2.md §3/§8, handoff §5's 1g -- under per-token
    # coverage, a name appearing as both `own` and an associated name
    # contributes max(own_i, ASSOC_WEIGHT * own_i) = own_i: one string can
    # no longer be read twice. L's old formula (own * (1 + BUMP * assoc))
    # double-counted the one string and, at real-library scale, split two
    # identically-named/identically-scored rows 22 rank-key points apart
    # (L2 §1). The fixture must assert *equality of the two rank keys*, not
    # merely that both appear.
    artist = builders.make_artist(conn, name="Zzz Artist")
    album = builders.make_album(conn, name="Zzzselftitled", artists=[artist])
    track = builders.make_track(conn, name="Zzzselftitled", album_id=album, artists=[artist])
    group = builders.make_group(conn, [track])
    builders.make_score(conn, "version", group["version"], all_time=50.0)
    conn.commit()

    ranked = search.rank(conn, "zzzselftitled")

    songs_by_id = {r["id"]: r for r in ranked["songs"]}
    albums_by_id = {r["id"]: r for r in ranked["albums"]}
    assert group["version"] in songs_by_id
    assert album in albums_by_id
    assert songs_by_id[group["version"]]["relevance"] == pytest.approx(1.0)
    assert albums_by_id[album]["relevance"] == pytest.approx(1.0)
    assert songs_by_id[group["version"]]["rank_key"] == pytest.approx(
        albums_by_id[album]["rank_key"]
    )


def test_combined_cross_type_ranking_by_rank_key_not_type_or_insertion_order(conn, monkeypatch):
    # source: better-search-L2.md §8, handoff §5's 1a -- "combined left
    # unsorted; sorted worst-first; ALPHA = 0.0" all survived L's suite
    # ("the most valuable gap. Cross-type ranking is L's headline and
    # nothing asserts it").
    #
    # `TYPES` (and so `by_type`'s own iteration order) is
    # songs/albums/artists/playlists, so a song and an artist naturally
    # land song-first if `combined` is merely concatenated by type,
    # unsorted -- that would accidentally look "correct" here whichever
    # type the fixture used unless the *lower*-TYPES-order entity is
    # deliberately given the higher rank_key. The artist below has
    # relevance 0.5/score 1000 (rank_key 125.0); the song has relevance
    # 1.0/score 15 (rank_key 15.0) -- so a correct sort must move the
    # artist ahead of the song, which an unsorted or ascending ("worst-
    # first") `combined` cannot do. The exact rank_key values separately
    # catch ALPHA=0.0 (artist_row would read 1000.0, not 125.0).
    song_artist = builders.make_artist(conn, name="Someone")
    song_track = builders.make_track(conn, name="Zzzalpha Zzzbeta", artists=[song_artist])
    song_group = builders.make_group(conn, [song_track])
    builders.make_score(conn, "version", song_group["version"], all_time=15.0)

    scored_artist = builders.make_artist(conn, name="Zzzalpha")
    carrier_track = builders.make_track(conn, artists=[scored_artist])
    carrier_group = builders.make_group(conn, [carrier_track])
    builders.make_score(conn, "version", carrier_group["version"], all_time=1000.0)
    conn.commit()

    ranked = search.rank(conn, "zzzalpha zzzbeta")

    combined_by_key = {(r["type"], r["id"]): r for r in ranked["combined"]}
    song_row = combined_by_key[("songs", song_group["version"])]
    artist_row = combined_by_key[("artists", scored_artist)]
    assert song_row["relevance"] == pytest.approx(1.0)
    assert artist_row["relevance"] == pytest.approx(0.5)
    assert song_row["rank_key"] == pytest.approx(15.0)
    assert artist_row["rank_key"] == pytest.approx(125.0)
    assert ranked["combined"].index(artist_row) < ranked["combined"].index(song_row)

    monkeypatch.setattr(search, "COMBINED_LIMIT", 1)
    page_kwargs = search.search_page(conn, "zzzalpha zzzbeta")
    assert [r["id"] for r in page_kwargs["most_relevant"]] == [scored_artist]


def test_combined_rows_carry_their_own_type_label_and_image(conn):
    # source: better-search-L2.md §8, handoff §5's 1h -- Most Relevant and
    # the dropdown render both `type_label` and `image_url`, and nothing in
    # search.py itself reads either back, so a hardcoded `"Song"` / `None`
    # survived L's suite untested. An album and a playlist, both matching,
    # isolate the two fields from each other and from the "songs" default.
    builders.make_album(conn, name="Zzzcombinedlabel Album", image_url="https://img/album")
    builders.make_playlist(
        conn, name="Zzzcombinedlabel Playlist", image_url="https://img/playlist"
    )
    conn.commit()

    rows = search.search_dropdown(conn, "zzzcombinedlabel")

    by_type = {r["type"]: r for r in rows}
    assert by_type["albums"]["type_label"] == "Album"
    assert by_type["albums"]["image_url"] == "https://img/album"
    assert by_type["playlists"]["type_label"] == "Playlist"
    assert by_type["playlists"]["image_url"] == "https://img/playlist"


# -- Caching (§4.2, §12) ------------------------------------------------------


def test_a_name_inserted_after_the_first_search_is_findable_by_the_second(conn):
    # source: better-search-L.md §4.2 -- staleness-checked on PRAGMA
    # data_version. A cache with no staleness check passes every test built
    # on a fixture stamped once before the run; this is the one test that
    # fails for it.
    before = search.rank(conn, "brand new track")
    assert before["songs"] == []

    track = builders.make_track(conn, name="Brand New Track")
    group = builders.make_group(conn, [track])
    conn.commit()

    after = search.rank(conn, "brand new track")
    assert group["version"] in _song_ids(after)


# -- Result assembly (§4.7, §6, §12) -----------------------------------------


def test_songs_dedupe_to_one_row_per_version_group_keeping_the_highest_relevance_member(conn):
    # source: better-search-L.md §4.7 -- "Songs dedupe to one row per version
    # group, keeping the member with the highest relevance." Same title on
    # both members isolates the dedupe rule to `assoc` (one credited to the
    # queried artist, one not), same shape as the assoc-bump test above.
    radiohead = builders.make_artist(conn, name="Radiohead")
    other = builders.make_artist(conn, name="Someone Else")
    better_track = builders.make_track(conn, name="Creep", artists=[radiohead])
    worse_track = builders.make_track(conn, name="Creep", artists=[other])
    group = builders.make_group(conn, [better_track])
    builders.make_group(conn, [worse_track], **group)  # same version group as better_track
    conn.commit()

    ranked = search.rank(conn, "radiohead creep")

    matches = [r for r in ranked["songs"] if r["id"] == group["version"]]
    assert len(matches) == 1
    assert matches[0]["track_id"] == better_track


def test_rank_leaves_combined_unclipped_for_the_caller_to_slice(conn):
    # source: better-search-L.md §4.7 -- "Ranking must see every candidate
    # before the cap ... capping before ranking returns an arbitrary N, not
    # the best N." rank() itself applies no COMBINED_LIMIT/DROPDOWN_LIMIT;
    # that's search_page()/search_dropdown()'s job, tested below.
    builders.make_artist(conn, name="Zzzsearch One")
    builders.make_artist(conn, name="Zzzsearch Two")
    t1 = builders.make_track(conn, name="Zzzsearch Three")
    builders.make_group(conn, [t1])
    conn.commit()

    ranked = search.rank(conn, "zzzsearch")

    assert len(ranked["combined"]) == 3
    combined_types = {r["type"] for r in ranked["combined"]}
    assert "artists" in combined_types and "songs" in combined_types


def test_most_relevant_and_the_dropdown_cap_at_their_own_limits(conn, monkeypatch):
    # source: better-search-L.md §6.1 (COMBINED_LIMIT) and §7 (DROPDOWN_LIMIT,
    # "same shape as Most Relevant"). Both cap combined[] independently at
    # their own call site -- search_page() and search_dropdown().
    monkeypatch.setattr(search, "COMBINED_LIMIT", 2)
    monkeypatch.setattr(search, "DROPDOWN_LIMIT", 1)
    for i in range(4):
        builders.make_artist(conn, name=f"Zzzcap Artist {i}")
    conn.commit()

    page_kwargs = search.search_page(conn, "zzzcap")
    assert len(page_kwargs["most_relevant"]) == 2

    dropdown_rows = search.search_dropdown(conn, "zzzcap")
    assert len(dropdown_rows) == 1


def test_search_more_returns_at_most_section_max(conn, monkeypatch):
    # source: better-search-L.md §5/§6.2 -- "/api/search/more?q=&type= ...
    # rendered HTML fragment ... up to SECTION_MAX."
    monkeypatch.setattr(search, "SECTION_MAX", 2)
    for i in range(4):
        builders.make_artist(conn, name=f"Zzzmany Artist {i}")
    conn.commit()

    rows = search.search_more(conn, "zzzmany", "artists")

    assert len(rows) == 2


def test_search_page_section_renders_at_most_section_limit(conn, monkeypatch):
    # source: better-search-L.md §6.2 -- "Each section renders SECTION_LIMIT
    # rows on load. Where more matched, it carries a See more control."
    monkeypatch.setattr(search, "SECTION_LIMIT", 2)
    for i in range(4):
        builders.make_artist(conn, name=f"Zzzfew Artist {i}")
    conn.commit()

    kwargs = search.search_page(conn, "zzzfew")

    assert len(kwargs["artists"]) == 2
    assert kwargs["artists_total"] == 4


def test_a_song_ranks_before_its_slice_is_cut_not_after(conn, monkeypatch):
    # source: better-search-L.md §4.7 -- "Ranking must see every candidate
    # before the cap ... capping before ranking returns an arbitrary N, not
    # the best N." Carried over from entity-pages-K.md's equivalent test for
    # the old capping-at-50 rule (P3-008 found the version half of this
    # missing a sort once already). 21 equally-relevant matches, insertion
    # order deliberately alphabetical-last-scores-best, so a slice-then-rank
    # bug returns anything but the one that should be first.
    monkeypatch.setattr(search, "SECTION_LIMIT", 1)
    for i in range(1, 21):
        track = builders.make_track(conn, name=f"Zzzcap Song {i:02d}")
        groups = builders.make_group(conn, [track])
        if i == 20:
            builders.make_score(conn, "version", groups["version"], all_time=95.0)
    conn.commit()

    kwargs = search.search_page(conn, "zzzcap song")

    assert kwargs["songs_total"] == 20
    assert kwargs["songs"][0]["name"] == "Zzzcap Song 20"


def test_an_album_ranks_before_its_slice_is_cut_not_after(conn, monkeypatch):
    # source: better-search-L.md §4.7 -- the same invariant, on the simpler
    # per-type ranker shape albums/artists/playlists share (distinct from
    # songs' version-group batching and dedupe).
    #
    # Named "Record", not "Album": make_group()'s own internal make_track()
    # call re-creates the track it's given (a no-op for the track row) but,
    # since it passes no album_id of its own, always mints a fresh unused
    # album too, named by builders.py's own default "Album {id}" -- which
    # would otherwise fuzzy-match a query containing the literal word
    # "album" and inflate this test's candidate count.
    monkeypatch.setattr(search, "SECTION_LIMIT", 1)
    for i in range(1, 21):
        album = builders.make_album(conn, name=f"Zzzcap Record {i:02d}")
        track = builders.make_track(conn, album_id=album)
        groups = builders.make_group(conn, [track])
        if i == 20:
            builders.make_score(conn, "version", groups["version"], all_time=95.0)
    conn.commit()

    kwargs = search.search_page(conn, "zzzcap record")

    assert kwargs["albums_total"] == 20
    assert kwargs["albums"][0]["name"] == "Zzzcap Record 20"


def test_an_artist_ranks_before_its_slice_is_cut_not_after(conn, monkeypatch):
    # source: better-search-L2.md §8, handoff §5's 1b -- the artist twin of
    # the album test above; L's suite only ever asserted "rank before cut"
    # for songs and albums, so a dropped `.sort()` in `_rank_artists`
    # survived.
    #
    # Named "Person", not "Artist": make_group()'s own internal
    # make_track() call also mints a fresh, unrequested artist for its
    # phantom album (builders.py's own default "Artist {id}") -- same trap
    # as "Album" above, just one entity type over.
    monkeypatch.setattr(search, "SECTION_LIMIT", 1)
    for i in range(1, 21):
        artist = builders.make_artist(conn, name=f"Zzzcap Person {i:02d}")
        if i == 20:
            track = builders.make_track(conn, artists=[artist])
            groups = builders.make_group(conn, [track])
            builders.make_score(conn, "version", groups["version"], all_time=95.0)
    conn.commit()

    kwargs = search.search_page(conn, "zzzcap person")

    assert kwargs["artists_total"] == 20
    assert kwargs["artists"][0]["name"] == "Zzzcap Person 20"


def test_a_playlist_ranks_before_its_slice_is_cut_not_after(conn, monkeypatch):
    # source: better-search-L2.md §8, handoff §5's 1b -- the playlist twin;
    # a dropped `.sort()` in `_rank_playlists` also survived L's suite.
    monkeypatch.setattr(search, "SECTION_LIMIT", 1)
    for i in range(1, 21):
        playlist = builders.make_playlist(conn, name=f"Zzzcap Playlist {i:02d}")
        if i == 20:
            track = builders.make_track(conn)
            builders.make_membership(conn, playlist_id=playlist, track_id=track)
            groups = builders.make_group(conn, [track])
            builders.make_score(conn, "version", groups["version"], all_time=95.0)
    conn.commit()

    kwargs = search.search_page(conn, "zzzcap playlist")

    assert kwargs["playlists_total"] == 20
    assert kwargs["playlists"][0]["name"] == "Zzzcap Playlist 20"


# -- L2: album dedupe (§5, §8) -------------------------------------------------


def test_album_dedupe_keeps_the_highest_ranked_of_a_colliding_pair(conn):
    # source: better-search-L2.md §5/§8 -- all three conditions hold (equal
    # normalized name, equal normalized artist list, overlapping release
    # groups), so the pair collapses to one row, "always ... the
    # highest-ranked of its group": deliberately different scores, and the
    # lower-scored id must not survive.
    #
    # Named "Record", not "Album", for the same reason as the rank-before-
    # cut test above: make_group()'s internal make_track() call mints a
    # fresh phantom album named "Album {id}", which would fuzzy-match a
    # query containing the literal word "album".
    artist = builders.make_artist(conn, name="Zzzdupe Artist")
    album_hi = builders.make_album(conn, name="Zzzdupe Record", artists=[artist])
    album_lo = builders.make_album(conn, name="Zzzdupe Record", artists=[artist])
    track_hi = builders.make_track(conn, album_id=album_hi, artists=[artist])
    track_lo = builders.make_track(conn, album_id=album_lo, artists=[artist])
    group_hi = builders.make_group(conn, [track_hi])
    group_lo = builders.make_group(conn, [track_lo], release=group_hi["release"])
    builders.make_score(conn, "version", group_hi["version"], all_time=80.0)
    builders.make_score(conn, "version", group_lo["version"], all_time=20.0)
    conn.commit()

    ranked = search.rank(conn, "zzzdupe record")

    assert {r["id"] for r in ranked["albums"]} == {album_hi}


def test_album_dedupe_does_not_collapse_disjoint_release_groups(conn):
    # source: better-search-L2.md §5/§8 -- name+artist equality alone is not
    # enough: "294 name+artist groups cover 624 album rows, and 75 of those
    # groups are genuinely different releases" (a deluxe edition against
    # the album it reissued). Same name, same artist, no shared release
    # group -- both ids must survive.
    artist = builders.make_artist(conn, name="Zzzdisjoint Artist")
    album_a = builders.make_album(conn, name="Zzzdisjoint Record", artists=[artist])
    album_b = builders.make_album(conn, name="Zzzdisjoint Record", artists=[artist])
    track_a = builders.make_track(conn, album_id=album_a, artists=[artist])
    track_b = builders.make_track(conn, album_id=album_b, artists=[artist])
    builders.make_group(conn, [track_a])
    builders.make_group(conn, [track_b])  # its own, disjoint release group
    conn.commit()

    ranked = search.rank(conn, "zzzdisjoint record")

    assert {r["id"] for r in ranked["albums"]} == {album_a, album_b}


def test_album_dedupe_does_not_collapse_different_artists(conn):
    # source: better-search-L2.md §5/§8 -- condition 2: name equality and an
    # overlapping release group are not enough either without matching
    # credited artists. Same name, same (shared) release group, different
    # artists -- both ids must survive.
    artist_a = builders.make_artist(conn, name="Zzzartistone")
    artist_b = builders.make_artist(conn, name="Zzzartisttwo")
    album_a = builders.make_album(conn, name="Zzzsamename Record", artists=[artist_a])
    album_b = builders.make_album(conn, name="Zzzsamename Record", artists=[artist_b])
    track_a = builders.make_track(conn, album_id=album_a, artists=[artist_a])
    track_b = builders.make_track(conn, album_id=album_b, artists=[artist_b])
    group_a = builders.make_group(conn, [track_a])
    builders.make_group(conn, [track_b], release=group_a["release"])
    conn.commit()

    ranked = search.rank(conn, "zzzsamename record")

    assert {r["id"] for r in ranked["albums"]} == {album_a, album_b}


# -- Ported from the old entities.search suite (still valid under L: §2's
# table names three replaced rules, and none of these are among them) ------


def test_songs_return_one_row_per_version_group(conn):
    # source: entity-pages-K.md, carried by better-search-L.md §2 ("songs
    # deduped by version group" stands). Two matching tracks in one group
    # must collapse to a single row.
    t1 = builders.make_track(conn, name="Collapse Me")
    t2 = builders.make_track(conn, name="Collapse Me Too")
    builders.make_group(conn, [t1, t2])
    conn.commit()

    ranked = search.rank(conn, "collapse me")

    assert len(ranked["songs"]) == 1


def test_artists_collapse_an_aliased_artist_onto_its_canonical_id(conn):
    # source: CLAUDE.md -- Spotify has multiple ids per artist, and every
    # artist-level listing resolves through artist_alias. Both raw names
    # match the query, so an unresolved implementation returns two rows for
    # what is one artist.
    one = builders.make_artist(conn, name="Dupe Artist One")
    two = builders.make_artist(conn, name="Dupe Artist Two")
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)", (two, one)
    )
    conn.commit()

    ranked = search.rank(conn, "dupe artist")

    assert _artist_ids(ranked) == {one}


def test_playlists_return_matches_only(conn):
    # source: entity-pages-K.md -- the non-matching playlist is what
    # separates "returns matches" from "returns every playlist".
    found = builders.make_playlist(conn, name="Findable Playlist")
    builders.make_playlist(conn, name="Something Else")
    conn.commit()

    ranked = search.rank(conn, "findable playlist")

    assert {r["id"] for r in ranked["playlists"]} == {found}


def test_a_track_with_no_canonical_group_is_skipped_without_crashing(conn):
    # source: S_sweep.md §3, carried forward -- ensure_track_groups() is the
    # route's job, not rank()'s, so a matching track with no track_group row
    # at all must be skipped rather than crash when the version lookup
    # comes back empty for it.
    builders.make_track(conn, name="Ungrouped Match")
    conn.commit()

    ranked = search.rank(conn, "ungrouped match")

    assert ranked["songs"] == []


def test_an_artist_with_no_alias_row_at_all_still_resolves(conn):
    # source: S_sweep.md §3, carried forward -- an artist never aliased in
    # either direction has no artist_alias row at all; resolution must still
    # surface it rather than requiring one.
    plain = builders.make_artist(conn, name="Plain Artist Solo")
    conn.commit()

    ranked = search.rank(conn, "plain artist solo")

    assert plain in _artist_ids(ranked)


def test_a_null_named_canonical_artist_still_renders(conn):
    # source: S_sweep.md §3, carried forward -- the canonical artist reached
    # through an alias can itself have a NULL name; hydration must still
    # render it rather than crash.
    alias_source = builders.make_artist(conn, name="Findable Name")
    canonical_artist = builders.make_artist(conn)
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        (alias_source, canonical_artist),
    )
    conn.execute("UPDATE artist SET name = NULL WHERE artist_id = ?", (canonical_artist,))
    conn.commit()

    rows = search.search_more(conn, "findable name", "artists")

    assert [r["artist_id"] for r in rows] == [canonical_artist]
    assert rows[0]["name"] is None
