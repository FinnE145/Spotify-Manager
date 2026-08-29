"""search.py's matcher, cache and ranking (docs/specs/better-search-L.md).

Route-level rendering and the write-nothing guarantee live in test_routes.py
alongside the rest of the permanent sweep; this file is the matcher itself,
tested against `search.rank()`'s raw output rather than rendered HTML, so a
fixture can name exactly the id it expects without parsing a page.
"""

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
    # source: better-search-L.md §4.4/§10.1 -- two tracks sharing the
    # **identical** title, one credited to the artist named in the query.
    # Identical titles isolate `assoc`: with different titles, `own` alone
    # could explain the ordering.
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
    # source: better-search-L.md §4.4/§10.1 -- "Wait a Minute!" by Willow
    # scores own=0.400 on "willow", below RELEVANCE_FLOOR=0.5, and nothing
    # about score or assoc rescues it.
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
