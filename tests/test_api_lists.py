"""Lists, filters, sorting, dates and plays (docs/specs/api-Z1.md §5-§9; its
## Tests clauses 6, 8-15).

Each fixture is one the obvious wrong implementation would fail: the
highest-scoring song is alphabetically last, equal sort keys have to fall back
to id *ascending* even when the sort is descending, and every play near a day
boundary sits on the side the UTC rule gets wrong.
"""

import json
from datetime import datetime

import pytest

import canonical
import entities
import resources
from api_lib import build, drop_strays
from builders import (
    days_ago, make_album, make_artist, make_generation, make_group, make_membership, make_play,
    make_playlist, make_score, make_track,
)


def get(client, path, status=200):
    resp = client.get(path)
    assert resp.status_code == status, resp.get_data(as_text=True)
    return resp.get_json()


def names(listing):
    return [i["name"] for i in listing["items"]]


# -- Clause 8: sort before paging, stable ---------------------------------------------------------------


def test_the_sort_is_applied_to_the_whole_set_before_the_page_is_cut(client, conn):
    # source: api-Z1.md §5.5, §7.4 / Tests 8 -- "a fixture where the highest-
    # scoring entity is alphabetically last and per_page=1: page 1 is that
    # entity." (An implementation sorting the page, or by name, fails.)
    build(conn)

    first = get(client, "/api/v1/songs?per_page=1&sort=-score")
    last = get(client, "/api/v1/songs?per_page=1&page=3&sort=-score")
    by_name = get(client, "/api/v1/songs?per_page=1&sort=name")

    assert names(first) == ["Zebra"] and first["total"] == 3
    assert names(last) == ["Aardvark"]
    assert names(by_name) == ["Aardvark"]


def test_the_default_sort_is_score_descending(client, conn):
    # source: api-Z1.md §7.4 -- "The default is -score."
    build(conn)

    assert names(get(client, "/api/v1/songs")) == ["Zebra", "half•alive", "Aardvark"]


def test_a_second_sort_key_decides_a_tie_in_the_first(client, conn):
    # source: api-Z1.md §7.4 / Tests 8 -- "A multi-key sort's second key decides
    # a first-key tie." Aardvark and half-alive tie on plays; the second key's
    # direction has to flip their order.
    build(conn)
    for track in ("t-aard", "t-aard", "t-half", "t-half", "t-zeb"):
        make_play(conn, track)

    high_score_first = names(get(client, "/api/v1/songs?sort=-plays,-score"))
    low_score_first = names(get(client, "/api/v1/songs?sort=-plays,score"))

    assert high_score_first == ["half•alive", "Aardvark", "Zebra"]
    assert low_score_first == ["Aardvark", "half•alive", "Zebra"]


@pytest.mark.parametrize("sort", ["plays", "-plays"])
def test_equal_keys_page_without_repeats_or_gaps_in_ascending_id_order(client, conn, sort):
    # source: api-Z1.md §5.5 / Tests 8 -- "Every list sort appends the type's
    # unique key (id) as a final ascending tie-breaker." All plays are zero, so
    # only the tie-breaker orders them -- ascending even for a descending sort.
    build(conn)

    pages = [
        get(client, f"/api/v1/tracks?sort={sort}&per_page=1&page={n}")["items"][0]["id"]
        for n in range(1, 5)
    ]

    assert pages == sorted(["t-aard", "t-zeb", "t-zeb-live", "t-half"])


def test_a_track_with_no_materialized_score_has_a_null_score_and_sorts_below_a_zero(client, conn):
    # source: Finn's call in the Z1 implement session -- "if we have no
    # materialized score for anything can we make it null?" (0 is a score;
    # null is none) -- so a materialized 0.0 outranks having none, even though
    # the unscored track's id sorts first.
    build(conn)
    make_track(conn, "t-0-unscored", "Unscored")
    make_track(conn, "t-1-zero", "Zero")
    make_score(conn, "track", "t-1-zero", all_time=0.0, recent=0.0)
    drop_strays(conn)

    items = get(client, "/api/v1/tracks?sort=-score")["items"]

    assert [i["id"] for i in items][-2:] == ["t-1-zero", "t-0-unscored"]
    assert items[-2]["score"] == {"all_time": 0.0, "recent": 0.0}
    assert items[-1]["score"] is None
    assert items[0]["score"] == {"all_time": 90.0, "recent": 85.0}


def test_every_entitys_url_is_the_page_that_shows_it(client, conn):
    # source: api-Z1.md §5.1 -- "url is the site path (/album/<id>, /song/<id>,
    # ...), generated with url_for against the same endpoints entity_link uses".
    # Each must be a real page, not just a plausible string.
    lib = build(conn)
    g = lib["groups"]["zeb"]
    make_playlist(conn, "pl-1", "One")
    expected = {
        "tracks/t-zeb": "/track/t-zeb", "albums/al-z": "/album/al-z", "artists/ar-alpha": "/artist/ar-alpha",
        "playlists/pl-1": "/playlist/pl-1", f"songs/{g['song']}": f"/song/{g['song']}",
        f"versions/{g['version']}": f"/version/{g['version']}",
        f"recordings/{g['recording']}": f"/recording/{g['recording']}",
        f"releases/{g['release']}": f"/release/{g['release']}",
    }

    for path, url in expected.items():
        detail = get(client, f"/api/v1/{path}")
        assert detail["url"] == url
        assert client.get(url).status_code == 200, url


def test_the_default_tz_is_toronto_at_both_layers(conn):
    # source: api-Z1.md §5.3 -- "tz: default America/Toronto (a module constant
    # DEFAULT_TZ in resources.py)". The route declares the default for the index
    # and the builder falls back to it; both have to say the same thing.
    import api

    declared = next(p for p in api.WINDOW if p.name == "tz")

    assert resources.make_window().tz_name == "America/Toronto"
    assert declared.default == resources.DEFAULT_TZ == "America/Toronto"


def test_query_time_scores_are_null_when_no_version_is_scored(client, conn):
    # source: Finn's call, as above -- applied to the query-time types too:
    # an album whose versions have no materialized score has a null score, not
    # a computed zero.
    build(conn)
    make_album(conn, "al-bare", "Bare", artists=["ar-alpha"])
    make_track(conn, "t-bare", "Bare Track", album_id="al-bare", artists=["ar-alpha"])

    bare = get(client, "/api/v1/albums/al-bare")
    scored = get(client, "/api/v1/albums/al-z")

    assert bare["score"] is None
    assert scored["score"]["all_time"] > 0


# -- Clause 9: the three name filters --------------------------------------------------------------------------


def test_the_three_name_filters_differ_in_how_much_of_the_name_must_match(client, conn):
    # source: api-Z1.md §7.2 / Tests 9 -- "q matches a prefix but not an infix;
    # q_contains matches the infix; q_full matches only the whole name."
    build(conn)

    assert names(get(client, "/api/v1/tracks?q=zeb&sort=name")) == ["Zebra", "Zebra (Live)"]
    assert names(get(client, "/api/v1/tracks?q=ebra")) == []
    assert names(get(client, "/api/v1/tracks?q_contains=ebra&sort=name")) == ["Zebra", "Zebra (Live)"]
    assert names(get(client, "/api/v1/tracks?q_full=zebra")) == ["Zebra"]
    assert names(get(client, "/api/v1/tracks?q_full=zeb")) == []


@pytest.mark.parametrize("param", ["q", "q_contains", "q_full"])
def test_a_spaced_query_matches_a_punctuated_name_under_all_three(client, conn, param):
    # source: api-Z1.md §7.2 / Tests 9 -- "half alive matches half-alive under
    # all three (the normalization clause)". normalize.base_string alone maps
    # the name to "halfalive" and the query to "half alive"; both sides lose
    # their spaces here (Finn's call).
    build(conn)

    assert names(get(client, f"/api/v1/tracks?{param}=half%20alive")) == ["half•alive"]
    assert names(get(client, f"/api/v1/artists?{param}=half%20alive")) == ["half•alive"]
    assert names(get(client, f"/api/v1/albums?{param}=half%20album" if param != "q_full"
                     else "/api/v1/albums?q_full=halfalbum")) == ["Half Album"]


def test_a_group_matches_through_a_member_that_is_not_its_representative(client, conn):
    # source: api-Z1.md §7.2 / Tests 9 -- "A group matches through a non-
    # representative member's name." Song "Zebra" is represented by the
    # higher-scoring "Zebra" track; "Zebra (Live)" is the other member.
    build(conn)

    songs = get(client, "/api/v1/songs?q_full=zebra%20live")

    assert names(songs) == ["Zebra"]


def test_an_artist_name_filter_also_matches_an_alias_ids_name(client, conn):
    # source: api-Z1.md §7.2 -- "an artist's name (the canonical one or any
    # alias id's)." "Beta Alt" is only an alias.
    build(conn)

    artists = get(client, "/api/v1/artists?q_full=beta%20alt")

    assert [a["id"] for a in artists["items"]] == ["ar-beta"]


def test_a_query_with_nothing_searchable_matches_nothing(client, conn):
    # source: api-Z1.md §7.2 -- names compare after normalizing; "%" normalizes
    # to nothing, so it must not match every row (search.is_searchable's rule).
    build(conn)

    assert get(client, "/api/v1/tracks?q=%25")["total"] == 0


# -- Clause 10: day boundaries in tz ----------------------------------------------------------------------------


def _window_count(client, path):
    return get(client, path)["plays"]["window"]


def test_a_play_near_midnight_falls_on_the_day_it_has_in_tz_not_in_utc(client, conn):
    # source: api-Z1.md §5.3 / Tests 10 -- "a play at 2025-03-02T03:00:00Z
    # counts toward to=2025-03-01 under the default tz (it is March 1 in
    # Toronto) and not under tz=UTC."
    build(conn)
    make_play(conn, "t-aard", ts="2025-03-02T03:00:00Z")

    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-01") == 1
    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-01&tz=UTC") == 0


def test_a_bare_to_includes_its_whole_day_and_a_bare_from_starts_at_midnight(client, conn):
    # source: api-Z1.md §5.3 / Tests 10 -- "a bare to includes that whole day."
    # The three plays sit on the exact boundaries of March 1 in Toronto (EST,
    # UTC-5): the last second of the day, the first second of the next, and the
    # last second of the day before.
    build(conn)
    make_play(conn, "t-aard", ts="2025-03-02T04:59:59Z")  # 23:59:59 Mar 1
    make_play(conn, "t-aard", ts="2025-03-02T05:00:00Z")  # 00:00:00 Mar 2
    make_play(conn, "t-aard", ts="2025-03-01T04:59:59Z")  # 23:59:59 Feb 28

    assert _window_count(client, "/api/v1/tracks/t-aard?from=2025-03-01&to=2025-03-01") == 1
    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-01") == 2
    assert _window_count(client, "/api/v1/tracks/t-aard?from=2025-03-02") == 1


def test_a_datetime_to_is_exclusive_and_needs_an_offset(client, conn):
    # source: api-Z1.md §5.3 -- "A datetime to is exclusive." An instant equal
    # to it is outside.
    build(conn)
    make_play(conn, "t-aard", ts="2025-03-02T05:00:00Z")

    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-02T05:00:00Z") == 0
    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-02T05:00:01Z") == 1
    assert _window_count(client, "/api/v1/tracks/t-aard?to=2025-03-02T00:00:01-05:00") == 1


def test_weeks_start_on_monday_and_survive_a_dst_change(client, conn):
    # source: api-Z1.md §5.3, §9.2 / Tests 10 -- "week buckets start on Monday;
    # a week spanning the March DST change has the right boundaries." Toronto
    # springs forward on Sunday 2025-03-09: the play at 04:30Z on Mon Mar 10 is
    # 00:30 EDT (week of Mar 10), where a fixed UTC-5 offset would read it as
    # 23:30 on Sunday (week of Mar 3).
    build(conn)
    make_play(conn, "t-aard", ts="2025-03-09T04:30:00Z")  # 23:30 EST Sat Mar 8
    make_play(conn, "t-aard", ts="2025-03-10T04:30:00Z")  # 00:30 EDT Mon Mar 10

    timeline = get(client, "/api/v1/plays/timeline?bucket=week&from=2025-03-03&to=2025-03-16")

    assert [(b["start"], b["plays"]) for b in timeline["buckets"]] == [
        ("2025-03-03", 1), ("2025-03-10", 1),
    ]
    assert all(datetime.fromisoformat(b["start"]).weekday() == 0 for b in timeline["buckets"])


def test_an_unknown_tz_is_a_400(client, conn):
    # source: api-Z1.md §5.3 / Tests 10 -- "an unknown tz -> 400."
    build(conn)

    resp = client.get("/api/v1/plays?tz=Not/AZone")

    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_parameter"


# -- Clause 11: min_played_ms --------------------------------------------------------------------------------------


def test_min_played_ms_changes_everything_the_response_counts(client, conn):
    # source: api-Z1.md §5.3 / Tests 11 -- "min_played_ms changes the plays
    # metrics, the window, the plays listing and the timeline buckets, and
    # without it every row counts."
    build(conn)
    for ms in (1_000, 100_000, 200_000):
        make_play(conn, "t-aard", ts="2025-03-05T15:00:00Z", ms_played=ms)

    plain = get(client, "/api/v1/tracks/t-aard?from=2025-03-01")["plays"]
    cut = get(client, "/api/v1/tracks/t-aard?from=2025-03-01&min_played_ms=50000")["plays"]
    listed = get(client, "/api/v1/plays?min_played_ms=50000")
    metric = get(client, "/api/v1/tracks?q_full=aardvark&min_played_ms=50000")["items"][0]["metrics"]["plays"]
    timeline = get(client, "/api/v1/plays/timeline?bucket=month&min_played_ms=50000")
    timeline_all = get(client, "/api/v1/plays/timeline?bucket=month")

    assert (plain["total"], plain["window"]) == (3, 3)
    assert (cut["total"], cut["window"]) == (2, 2)
    assert listed["total"] == 2 and metric["total"] == 2
    assert sum(b["plays"] for b in timeline["buckets"]) == 2
    assert sum(b["plays"] for b in timeline_all["buckets"]) == 3
    assert sum(b["ms_played"] for b in timeline["buckets"]) == 300_000


# -- Clause 12: rolling windows unchanged -----------------------------------------------------------------------------


def test_rolling_windows_are_null_when_the_data_ends_before_them(client, conn):
    # source: api-Z1.md §5.1 / Tests 12 -- "past_30d / past_7d are null when
    # data_through predates the window, matching entities.play_stats on the
    # same fixture."
    build(conn)
    make_play(conn, "t-aard", ts=days_ago(100))

    plays = get(client, "/api/v1/tracks/t-aard")["plays"]
    old = entities.play_stats(conn, ["t-aard"])

    assert old["month"] is None and old["week"] is None
    assert (plays["past_30d"], plays["past_7d"], plays["total"]) == (None, None, 1)
    assert plays["data_through"] == old["data_through"]
    assert plays["window"] is None  # no from/to given


def test_rolling_windows_count_when_the_data_reaches_them(client, conn):
    # source: api-Z1.md §5.1 / Tests 12 -- the same parity on a fixture whose
    # data does reach the windows: the numbers must equal play_stats'.
    build(conn)
    make_play(conn, "t-aard", ts=days_ago(20))
    make_play(conn, "t-aard", ts=days_ago(3))
    make_play(conn, "t-half", ts=days_ago(1))
    expected = entities.play_stats(conn, ["t-aard"])

    plays = get(client, "/api/v1/tracks/t-aard")["plays"]

    assert (plays["past_30d"], plays["past_7d"]) == (expected["month"], expected["week"]) == (2, 1)
    assert plays["total"] == expected["total"] == 2


# -- Clause 13: the timeline zero-fills ----------------------------------------------------------------------------------


def test_the_timeline_includes_empty_buckets_as_zeros(client, conn):
    # source: api-Z1.md §9.2 / Tests 13 -- "a gap month appears with plays: 0."
    build(conn)
    make_play(conn, "t-aard", ts="2025-01-15T15:00:00Z", ms_played=1000)
    make_play(conn, "t-aard", ts="2025-03-15T15:00:00Z", ms_played=2000)

    timeline = get(client, "/api/v1/plays/timeline?bucket=month&from=2025-01-01&to=2025-03-31")

    assert timeline["buckets"] == [
        {"start": "2025-01-01", "plays": 1, "ms_played": 1000},
        {"start": "2025-02-01", "plays": 0, "ms_played": 0},
        {"start": "2025-03-01", "plays": 1, "ms_played": 2000},
    ]
    assert timeline["tz"] == "America/Toronto" and timeline["bucket"] == "month"


def test_without_a_range_the_timeline_runs_from_the_first_match_to_the_data_end(client, conn):
    # source: api-Z1.md §9.2 -- "When from / to are absent, the range runs from
    # the first matching play to data_through." data_through is the newest play
    # of *any* track, so a track whose own last play is earlier still gets
    # zero-filled buckets up to it.
    build(conn)
    make_play(conn, "t-aard", ts="2025-01-15T15:00:00Z")
    make_play(conn, "t-half", ts="2025-03-15T15:00:00Z")

    timeline = get(client, f"/api/v1/plays/timeline?track=t-aard&bucket=month")

    assert [(b["start"], b["plays"]) for b in timeline["buckets"]] == [
        ("2025-01-01", 1), ("2025-02-01", 0), ("2025-03-01", 0),
    ]


# -- Clause 14: artist aliases ------------------------------------------------------------------------------------------------


def test_an_alias_artist_id_resolves_to_the_canonical_artist(client, conn):
    # source: api-Z1.md §5.1 / Tests 14 -- "detail for an alias id returns the
    # canonical artist with the canonical id, and the artist filter given an
    # alias id matches the canonical artist's tracks."
    build(conn)

    detail = get(client, "/api/v1/artists/ar-beta2")
    via_alias = get(client, "/api/v1/tracks?artist=ar-beta2&sort=name")
    via_canonical = get(client, "/api/v1/tracks?artist=ar-beta&sort=name")

    assert detail["id"] == "ar-beta" and detail["name"] == "Beta"
    assert detail["merged_ids"] == ["ar-beta2"]
    assert names(via_alias) == names(via_canonical) == ["Aardvark", "Zebra", "Zebra (Live)"]


def test_every_artists_list_uses_canonical_ids(client, conn):
    # source: api-Z1.md §5.1 -- "Every artists list in every summary uses
    # canonical ids." t-zeb is credited to the alias id.
    build(conn)

    track = get(client, "/api/v1/tracks/t-zeb")

    assert [a["id"] for a in track["artists"]] == ["ar-alpha", "ar-beta"]
    assert "ar-beta2" not in json.dumps(get(client, "/api/v1/artists"))


def test_artist_role_narrows_the_artist_filter(client, conn):
    # source: api-Z1.md §7.3 -- "artist_role: primary or featured, narrowing
    # artist". On "Zebra" Alpha is the album artist (primary) and Beta,
    # credited through its alias, is featured.
    build(conn)

    primary = names(get(client, "/api/v1/tracks?artist=ar-beta&artist_role=primary&sort=name"))
    featured = names(get(client, "/api/v1/tracks?artist=ar-beta&artist_role=featured"))

    assert primary == ["Aardvark", "Zebra (Live)"]
    assert featured == ["Zebra"]


# -- Clause 15: unfollowed playlists ------------------------------------------------------------------------------------------------


def test_an_unfollowed_playlist_is_never_listed_matched_or_shown(client, conn):
    # source: api-Z1.md §6.2, §7.3 / Tests 15 -- "an unfollowed playlist is
    # absent from /playlists and from a track detail's playlists, and a
    # playlist filter naming it matches nothing." The live membership row is
    # inserted by hand: ending memberships on unfollow is snapshot.py's job and
    # the filter must not depend on it having run.
    build(conn)
    make_playlist(conn, "pl-live", "Live")
    make_playlist(conn, "pl-gone", "Gone", unfollowed_at="2026-01-01T00:00:00Z")
    make_membership(conn, "pl-live", "t-aard")
    make_membership(conn, "pl-gone", "t-aard")

    listing = get(client, "/api/v1/playlists")
    detail = get(client, "/api/v1/tracks/t-aard")

    assert [p["id"] for p in listing["items"]] == ["pl-live"]
    assert [p["id"] for p in detail["playlists"]] == ["pl-live"]
    assert [m["playlist"]["id"] for m in detail["memberships"]] == ["pl-live"]
    assert get(client, "/api/v1/tracks?playlist=pl-gone")["total"] == 0
    assert get(client, "/api/v1/tracks?playlist=pl-live")["total"] == 1
    assert detail["plays"]["total"] == 0
    metrics = get(client, "/api/v1/tracks?q_full=aardvark")["items"][0]["metrics"]
    assert metrics["memberships"] == 1  # live rows in followed playlists only


# -- Representatives, scores, metrics --------------------------------------------------------------------------------------------------


def test_the_set_based_representative_election_matches_the_pages(conn):
    # source: api-Z1.md §6.1 -- "The representative is canonical.representative(),
    # the same election the pages use." The set-based form is a second
    # implementation of it, so this pins them together on groups where each rule
    # (pin, score, oldest membership, lowest id) is the deciding one.
    for tid in "abcdefghij":
        make_track(conn, f"t-{tid}", f"Track {tid}")
    pinned = make_group(conn, ["t-a", "t-b"], representative_track_id="t-b")["version"]
    make_score(conn, "track", "t-a", 99.0)
    by_score = make_group(conn, ["t-c", "t-d"])["version"]
    make_score(conn, "track", "t-c", 20.0)
    make_score(conn, "track", "t-d", 30.0)
    by_age = make_group(conn, ["t-e", "t-f"])["version"]
    make_membership(conn, "pl-1", "t-e", added_at="2025-01-01T00:00:00Z")
    make_membership(conn, "pl-1", "t-f", added_at="2020-01-01T00:00:00Z")
    by_id = make_group(conn, ["t-h", "t-g"])["version"]
    unplaced = make_group(conn, ["t-i", "t-j"])["version"]
    make_membership(conn, "pl-1", "t-j", added_at="2030-01-01T00:00:00Z")

    reps = resources.group_representatives(conn, "version")

    assert reps == {g: canonical.representative(conn, g) for g in reps}
    assert reps[pinned] == "t-b"
    assert reps[by_score] == "t-d"
    assert reps[by_age] == "t-f"
    assert reps[by_id] == "t-g"
    assert reps[unplaced] == "t-j"  # a membership of any age beats having none
    assert resources.group_representatives(conn, "version", [by_age]) == {by_age: "t-f"}


def test_entity_metrics_roll_up_each_types_own_tracks(client, conn):
    # source: api-Z1.md §7.1 -- an artist covers tracks credited in any role
    # (alias-resolved), an album its owned tracks, a group its members, a
    # playlist its live tracks; memberships exclude unfollowed playlists.
    build(conn)
    make_playlist(conn, "pl-1", "One")
    make_membership(conn, "pl-1", "t-zeb")
    make_membership(conn, "pl-1", "t-aard")
    make_membership(conn, "pl-1", "t-half", removed_at="2025-01-01T00:00:00Z")
    for track, n in (("t-aard", 1), ("t-zeb", 2), ("t-zeb-live", 4), ("t-half", 8)):
        for _ in range(n):
            make_play(conn, track)

    beta = get(client, "/api/v1/artists/ar-beta")["plays"]["total"]  # aard + zeb (alias) + live
    alpha_album = get(client, "/api/v1/albums?q_full=zed%20album")["items"][0]["metrics"]
    song = get(client, "/api/v1/songs?q_full=zebra")["items"][0]["metrics"]
    playlist = get(client, "/api/v1/playlists/pl-1")["plays"]["total"]
    list_playlist = get(client, "/api/v1/playlists")["items"][0]["metrics"]

    assert beta == 1 + 2 + 4
    assert alpha_album["plays"]["total"] == 2 and alpha_album["memberships"] == 1
    assert song["plays"]["total"] == 2 + 4 and song["memberships"] == 1
    assert playlist == 1 + 2  # the removed row's track is not covered
    assert set(list_playlist) == {"plays"}  # a playlist has no memberships/generations/tenure


def test_generation_metrics_use_the_same_runs_rule_as_the_tenure_page(client, conn):
    # source: api-Z1.md §6.2, §7.1 -- generations/tenure derive from
    # generation_presence through generations.runs: present in 1, 2 and 4
    # gives total 3, longest run 2, two runs.
    build(conn)
    for ordinal in (1, 2, 3, 4):
        make_generation(conn, ordinal)
    for ordinal in (1, 2, 4):
        playlist = conn.execute("SELECT playlist_id FROM generation WHERE ordinal = ?", (ordinal,)).fetchone()[0]
        make_membership(conn, playlist, "t-aard")

    detail = get(client, "/api/v1/tracks/t-aard")["generations"]
    listed = get(client, "/api/v1/songs?q_full=aardvark")["items"][0]["metrics"]

    assert detail == {"ordinals": [1, 2, 4], "total": 3, "tenure": 2, "run_count": 2,
                      "first_ordinal": 1, "last_ordinal": 4}
    assert (listed["generations"], listed["tenure"]) == (3, 2)
    assert get(client, "/api/v1/tracks?generation=2")["total"] == 1
    assert get(client, "/api/v1/tracks?generation=3")["total"] == 0
    assert names(get(client, "/api/v1/songs?sort=-tenure&per_page=1")) == ["Aardvark"]


def test_min_plays_and_min_memberships_filter_on_the_metric(client, conn):
    # source: api-Z1.md §7.3 -- "min_plays: as 7.1's plays: window if from/to
    # are given, else total"; min_memberships as §7.1.
    build(conn)
    for _ in range(3):
        make_play(conn, "t-aard", ts="2025-03-05T15:00:00Z")
    make_play(conn, "t-half", ts="2024-01-01T15:00:00Z")
    make_membership(conn, "pl-1", "t-half")

    assert names(get(client, "/api/v1/tracks?min_plays=3")) == ["Aardvark"]
    assert names(get(client, "/api/v1/tracks?min_plays=1&sort=name")) == ["Aardvark", "half•alive"]
    assert names(get(client, "/api/v1/tracks?min_plays=1&from=2025-01-01")) == ["Aardvark"]
    assert names(get(client, "/api/v1/tracks?min_memberships=1")) == ["half•alive"]


def test_group_level_filters_use_the_representative_and_the_earliest_member(client, conn):
    # source: api-Z1.md §7.3 -- "a group's [duration] is its representative's"
    # and "[year] is its earliest member's", while other track filters match if
    # any member does. Song "Zebra" is represented by the 300s / 2019 track and
    # also holds a 250s / 2023 member.
    build(conn)

    assert names(get(client, "/api/v1/songs?min_ms=290000")) == ["Zebra"]
    assert names(get(client, "/api/v1/songs?max_ms=260000&sort=name")) == ["Aardvark", "half•alive"]
    # earliest member 2019: in range for year_to=2019 even though another member is 2023
    assert names(get(client, "/api/v1/songs?year_to=2019")) == ["Zebra"]
    assert names(get(client, "/api/v1/songs?year_from=2022")) == ["Aardvark"]
    # the album filter is any-member
    assert names(get(client, "/api/v1/songs?album=al-a&sort=name")) == ["Aardvark", "Zebra"]
    assert names(get(client, "/api/v1/songs?explicit=1")) == ["Aardvark"]


def test_track_filters_year_duration_and_album(client, conn):
    # source: api-Z1.md §7.3 -- the same filters on the track type itself.
    build(conn)

    assert names(get(client, "/api/v1/tracks?year_from=2021&year_to=2021")) == ["half•alive"]
    assert names(get(client, "/api/v1/tracks?min_ms=250000&sort=name")) == ["Zebra", "Zebra (Live)"]
    assert names(get(client, "/api/v1/tracks?album=al-a&sort=name")) == ["Aardvark", "Zebra (Live)"]
    assert names(get(client, "/api/v1/albums?artist=ar-beta2&sort=name")) == ["Alpha Album"]
    assert names(get(client, "/api/v1/albums?year_from=2020&sort=name")) == ["Alpha Album", "Half Album"]


def test_sorts_on_duration_release_date_and_name(client, conn):
    # source: api-Z1.md §7.4 -- duration (a group's is its representative's),
    # release_date (a group's is its earliest member's), name (case-insensitive).
    build(conn)

    assert names(get(client, "/api/v1/tracks?sort=-duration")) == ["Zebra", "Zebra (Live)", "Aardvark", "half•alive"]
    assert names(get(client, "/api/v1/tracks?sort=release_date")) == ["Zebra", "half•alive", "Aardvark", "Zebra (Live)"] or \
        names(get(client, "/api/v1/tracks?sort=release_date"))[0] == "Zebra"
    assert names(get(client, "/api/v1/songs?sort=release_date"))[0] == "Zebra"
    assert names(get(client, "/api/v1/albums?sort=-release_date")) == ["Alpha Album", "Half Album", "Zed Album"]
    # case-insensitive: lowercase "half-alive" sorts between "Aardvark" and "Zebra"
    assert names(get(client, "/api/v1/tracks?sort=name")) == ["Aardvark", "half•alive", "Zebra", "Zebra (Live)"]


# -- Clause 6: one summary shape -------------------------------------------------------------------------------------------------------


def test_a_tracks_summary_is_identical_wherever_it_appears(client, conn):
    # source: api-Z1.md §6.1 / Tests 6 -- "The same track's summary is identical
    # (same keys, same values) in a list, in album detail's tracklist, in a
    # search result and in a playlist's items."
    build(conn)
    make_membership(conn, "pl-1", "t-zeb")
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = '2026-01-01T00:00:00Z' WHERE album_id = 'al-z'",
        (json.dumps([{"id": "t-zeb", "name": "Zebra", "uri": "u", "duration_ms": 300000,
                      "track_number": 1, "disc_number": 1}]),),
    )
    conn.commit()
    group = conn.execute("SELECT version_id FROM track_group WHERE track_id = 't-zeb'").fetchone()[0]

    in_list = next(i for i in get(client, "/api/v1/tracks")["items"] if i["id"] == "t-zeb")
    in_list = {k: v for k, v in in_list.items() if k != "metrics"}
    in_album = get(client, "/api/v1/albums/al-z")["tracklist"][0]["track"]
    in_playlist = get(client, "/api/v1/playlists/pl-1/items")["items"][0]["track"]
    in_member = next(m for m in get(client, f"/api/v1/versions/{group}")["members"] if m["id"] == "t-zeb")
    in_detail = {k: v for k, v in get(client, "/api/v1/tracks/t-zeb").items() if k in in_list}

    assert in_list["name"] == "Zebra" and "metrics" not in in_list
    assert in_album == in_playlist == in_member == in_list == in_detail


def test_a_groups_summary_is_identical_in_a_list_a_search_and_a_generation(client, conn):
    # source: api-Z1.md §6.1 -- one builder per type, whatever the context.
    build(conn)
    gen = make_generation(conn, 1)
    playlist = conn.execute("SELECT playlist_id FROM generation").fetchone()[0]
    make_membership(conn, playlist, "t-zeb")
    version = conn.execute("SELECT version_id FROM track_group WHERE track_id = 't-zeb'").fetchone()[0]

    in_list = next(i for i in get(client, "/api/v1/versions")["items"] if i["id"] == version)
    in_list = {k: v for k, v in in_list.items() if k != "metrics"}
    in_search = next(i for i in get(client, "/api/v1/search?q=zebra")["songs"]["items"] if i["id"] == version)
    in_generation = get(client, f"/api/v1/generations/{gen}")["items"][0]["group"]

    assert in_list["type"] == "version" and in_list["name"] == "Zebra"
    assert in_list == in_search == in_generation


# -- Search (§8) ------------------------------------------------------------------------------------------------------------------------------


def test_search_returns_sections_in_the_matchers_order_with_version_songs(client, conn):
    # source: api-Z1.md §8 -- "Songs rank at version tier, so song results are
    # version summaries"; "Item order is exactly rank()'s".
    import search

    build(conn)

    out = get(client, "/api/v1/search?q=zebra")
    ranked = search.rank(conn, "zebra")

    assert [i["id"] for i in out["songs"]["items"]] == [r["id"] for r in ranked["songs"]]
    assert {i["type"] for i in out["songs"]["items"]} == {"version"}
    assert out["songs"]["total"] == len(ranked["songs"]) == 2
    assert out["most_relevant"] and out["q"] == "zebra"
    assert set(out) == {"q", "most_relevant", "songs", "albums", "artists", "playlists"}


def test_search_with_a_type_pages_the_whole_ranked_list(client, conn):
    # source: api-Z1.md §8 -- "With type=...: one section in the §5.2 list
    # envelope, paging through the full ranked list."
    build(conn)

    page1 = get(client, "/api/v1/search?q=zebra&type=songs&per_page=1")
    page2 = get(client, "/api/v1/search?q=zebra&type=songs&per_page=1&page=2")

    assert page1["total"] == 2 and len(page1["items"]) == len(page2["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]


def test_a_query_below_the_minimum_length_is_empty_not_an_error(client, conn):
    # source: api-Z1.md §8 -- "below search.MIN_QUERY_LEN after normalizing,
    # every list is empty. That is not a 400."
    build(conn)

    out = get(client, "/api/v1/search?q=a")

    assert out["most_relevant"] == []
    assert all(out[t] == {"items": [], "total": 0} for t in ("songs", "albums", "artists", "playlists"))


# -- Plays (§9.1) ------------------------------------------------------------------------------------------------------------------------------


def test_plays_are_newest_first_and_resolve_through_relinked_uris(client, conn):
    # source: api-Z1.md §9.1 -- "newest first (sort=ts reverses)"; "track
    # resolves through played_uri_track, so relinked uris count. An unresolved
    # play has track: null."
    build(conn)
    conn.execute("INSERT INTO track_uri_alias (requested_uri, track_id) VALUES ('spotify:track:old', 't-aard')")
    conn.commit()
    make_play(conn, "t-half", ts="2025-01-01T00:00:00Z")
    make_play(conn, uri="spotify:track:old", ts="2025-02-01T00:00:00Z")
    make_play(conn, uri="spotify:track:nobody", ts="2025-03-01T00:00:00Z", reported_track_name="Mystery")

    newest = get(client, "/api/v1/plays")["items"]
    oldest = get(client, "/api/v1/plays?sort=ts")["items"]

    assert [p["ts"][:7] for p in newest] == ["2025-03", "2025-02", "2025-01"]
    assert [p["ts"][:7] for p in oldest] == ["2025-01", "2025-02", "2025-03"]
    assert newest[0]["track"] is None and newest[0]["reported"]["track_name"] == "Mystery"
    assert newest[1]["track"]["id"] == "t-aard"
    assert set(newest[0]) >= {"id", "ts", "ms_played", "source", "track", "spotify_track_uri", "reported",
                              "reason_start", "reason_end", "shuffle", "skipped", "platform",
                              "conn_country", "offline", "offline_ts", "incognito_mode"}
    assert newest[0]["shuffle"] is False  # SQLite 0/1 become JSON booleans


def test_plays_filter_by_entity_and_source(client, conn):
    # source: api-Z1.md §9.1 -- "at most one entity filter ... (any role,
    # alias-resolved), or playlist (tracks live in it now); source=export|
    # scrobble"; "With an entity filter, unresolved plays can't match."
    build(conn)
    make_playlist(conn, "pl-1", "One")
    make_membership(conn, "pl-1", "t-aard")
    make_play(conn, "t-aard", ts="2025-01-01T00:00:00Z")
    make_play(conn, "t-zeb", ts="2025-01-02T00:00:00Z", source="scrobble")
    make_play(conn, "t-half", ts="2025-01-03T00:00:00Z")
    make_play(conn, uri="spotify:track:nobody", ts="2025-01-04T00:00:00Z")
    song = conn.execute("SELECT song_id FROM track_group WHERE track_id = 't-zeb'").fetchone()[0]

    def total(query):
        return get(client, f"/api/v1/plays?{query}")["total"]

    assert total("") == 4
    assert total("track=t-aard") == 1
    assert total(f"song={song}") == 1
    assert total("album=al-z") == 1
    assert total("artist=ar-beta2") == 2  # t-aard and t-zeb both credit Beta (zeb via the alias)
    assert total("playlist=pl-1") == 1
    assert total("source=scrobble") == 1
    assert total("source=export") == 3
    assert total("track=nope") == 0


# -- Details (§6.2) --------------------------------------------------------------------------------------------------------------------------------


def test_group_details_name_their_parents_children_and_members(client, conn):
    # source: api-Z1.md §6.2 -- "pinned (song only; null at other tiers),
    # parents (summaries of the tiers above), children (the next tier down; null
    # at release tier), members (track summaries ordered by track score
    # descending then name)". `parents` is keyed by tier (Finn's call).
    lib = build(conn)
    zeb, live = lib["groups"]["zeb"], lib["groups"]["live"]

    song = get(client, f"/api/v1/songs/{zeb['song']}")
    recording = get(client, f"/api/v1/recordings/{zeb['recording']}")
    release = get(client, f"/api/v1/releases/{zeb['release']}")

    assert song["pinned"] is False and recording["pinned"] is None
    assert song["parents"] == {}
    assert sorted(recording["parents"]) == ["song", "version"]
    assert recording["parents"]["song"]["id"] == zeb["song"]
    assert recording["parents"]["version"]["id"] == zeb["version"]
    assert [c["id"] for c in song["children"]] == [zeb["version"], live["version"]]  # score order
    assert release["children"] is None
    assert [m["name"] for m in song["members"]] == ["Zebra", "Zebra (Live)"]  # score 90 before 60
    assert song["member_count"] == 2
    assert get(client, f"/api/v1/songs/{zeb['version']}", status=404)["error"] == "not_found"


def test_member_and_child_order_follow_score_not_id(client, conn):
    # source: api-Z1.md §6.2 -- members "ordered by track score descending then
    # name, as the page orders them"; children are ordered by score here too. The
    # default fixture's score order happens to equal its id order, so the scores
    # are inverted: the higher-scoring track and version are the higher ids.
    lib = build(conn)
    zeb, live = lib["groups"]["zeb"], lib["groups"]["live"]
    make_score(conn, "track", "t-zeb", all_time=40.0)
    make_score(conn, "track", "t-zeb-live", all_time=60.0)
    make_score(conn, "version", zeb["version"], all_time=40.0)
    make_score(conn, "version", live["version"], all_time=60.0)

    song = get(client, f"/api/v1/songs/{zeb['song']}")

    assert [m["id"] for m in song["members"]] == ["t-zeb-live", "t-zeb"]
    assert [c["id"] for c in song["children"]] == [live["version"], zeb["version"]]

    make_score(conn, "track", "t-zeb", all_time=60.0)  # a score tie falls back to name
    tied = get(client, f"/api/v1/songs/{zeb['song']}")
    assert [m["id"] for m in tied["members"]] == ["t-zeb", "t-zeb-live"]


def test_a_playlist_holding_several_members_reports_the_earliest_live_added_at(client, conn):
    # source: Finn's call in the Z1 implement session -- the detail's `playlists`
    # section gives "the earliest live added_at among" the entity's tracks in
    # that playlist; a removed row does not count.
    lib = build(conn)
    zeb = lib["groups"]["zeb"]
    make_membership(conn, "pl-1", "t-zeb", added_at="2025-06-01T00:00:00Z")
    make_membership(conn, "pl-1", "t-zeb-live", added_at="2025-03-01T00:00:00Z")
    make_membership(conn, "pl-1", "t-zeb", added_at="2024-01-01T00:00:00Z", removed_at="2024-02-01T00:00:00Z")
    drop_strays(conn)

    song = get(client, f"/api/v1/songs/{zeb['song']}")
    one_track = get(client, "/api/v1/tracks/t-zeb")

    assert [(p["id"], p["added_at"]) for p in song["playlists"]] == [("pl-1", "2025-03-01T00:00:00Z")]
    assert [(p["id"], p["added_at"]) for p in one_track["playlists"]] == [("pl-1", "2025-06-01T00:00:00Z")]


def test_track_detail_has_groups_memberships_and_aliases(client, conn):
    # source: api-Z1.md §6.2 -- track: isrc, uri, track_number, disc_number,
    # groups, memberships (unfollowed excluded, removed rows included),
    # aliases (requested uris relinked onto it).
    lib = build(conn)
    make_playlist(conn, "pl-1", "One")
    make_membership(conn, "pl-1", "t-aard", position=3, added_at="2025-01-01T00:00:00Z",
                    removed_at="2025-02-01T00:00:00Z")
    conn.execute("INSERT INTO track_uri_alias (requested_uri, track_id) VALUES ('spotify:track:old', 't-aard')")
    conn.commit()

    track = get(client, "/api/v1/tracks/t-aard")

    assert track["uri"] == "spotify:track:t-aard" and track["track_number"] == 1
    assert {k: v["id"] for k, v in track["groups"].items()} == {
        k: lib["groups"]["aard"][k] for k in ("song", "version", "recording", "release")}
    assert track["aliases"] == ["spotify:track:old"]
    (membership,) = track["memberships"]
    assert (membership["position"], membership["removed_at"]) == (3, "2025-02-01T00:00:00Z")
    assert track["playlists"] == []  # removed: not "live"


def test_playlist_items_are_paginated_in_position_order_and_hide_removed_rows(client, conn):
    # source: api-Z1.md §6.3 -- "ordered by position. By default the listing
    # holds live rows only; include_removed=1 adds removed rows."
    build(conn)
    make_playlist(conn, "pl-1", "One")
    make_membership(conn, "pl-1", "t-half", position=2)
    make_membership(conn, "pl-1", "t-aard", position=0)
    make_membership(conn, "pl-1", "t-zeb", position=1, removed_at="2025-01-01T00:00:00Z")

    live = get(client, "/api/v1/playlists/pl-1/items")
    everything = get(client, "/api/v1/playlists/pl-1/items?include_removed=1&per_page=2&page=2")

    assert [i["track"]["id"] for i in live["items"]] == ["t-aard", "t-half"] and live["total"] == 2
    assert [i["position"] for i in everything["items"]] == [2] and everything["total"] == 3
    assert get(client, "/api/v1/playlists/nope/items", status=404)["error"] == "not_found"


def test_a_playlist_detail_carries_its_facts_and_generation(client, conn):
    # source: api-Z1.md §6.2 -- playlist: description, last_changed_at,
    # tracks_pulled_at, unfollowed_at, generation {ordinal, started_at,
    # ended_at} or null; is_liked marks Liked Songs (no snapshot_id).
    build(conn)
    make_generation(conn, 1)
    playlist = conn.execute("SELECT playlist_id FROM generation").fetchone()[0]
    make_membership(conn, playlist, "t-aard", added_at="2025-01-01T00:00:00Z")
    make_playlist(conn, "__liked__", "Liked Songs", snapshot_id=None)

    detail = get(client, f"/api/v1/playlists/{playlist}")
    liked = get(client, "/api/v1/playlists/__liked__")

    assert detail["generation"] == {"ordinal": 1, "started_at": "2025-01-01T00:00:00Z", "ended_at": None}
    assert detail["is_liked"] is False and liked["is_liked"] is True
    assert detail["live_count"] == 1 and detail["excluded"] is False
    assert detail["spotify_url"].endswith(playlist) and liked["spotify_url"] is None
    assert "generations" not in detail and "playlists" not in detail  # a playlist carries neither
    assert detail["url"] == f"/playlist/{playlist}"


def test_an_albums_detail_assembles_the_stored_tracklist(client, conn):
    # source: api-Z1.md §6.2 -- album: tracklist [{disc_number, track_number,
    # name, uri, duration_ms, track}], tracklist_count, appended (owned tracks
    # not in the stored tracklist), total_duration_ms (only when every entry
    # has a length, else null).
    build(conn)
    items = [
        {"id": "t-zeb", "name": "Zebra", "uri": "spotify:track:t-zeb", "duration_ms": 300000,
         "track_number": 1, "disc_number": 1},
        {"id": "t-other", "name": "Other", "uri": "spotify:track:t-other", "duration_ms": 1000,
         "track_number": 2, "disc_number": 1},
    ]
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = '2026-01-01T00:00:00Z' WHERE album_id = 'al-z'",
        (json.dumps(items),),
    )
    conn.commit()

    album = get(client, "/api/v1/albums/al-z")

    assert [(t["name"], t["track"] and t["track"]["id"]) for t in album["tracklist"]] == [
        ("Zebra", "t-zeb"), ("Other", None)]
    assert album["tracklist_count"] == 2 and album["appended"] == []
    assert album["total_duration_ms"] == 301000  # 2 of total_tracks=2, both with a length
    assert album["owned_count"] == 1 and album["total_tracks"] == 2

    items[1]["duration_ms"] = None
    conn.execute("UPDATE album SET tracklist_json = ? WHERE album_id = 'al-z'", (json.dumps(items),))
    conn.commit()
    assert get(client, "/api/v1/albums/al-z")["total_duration_ms"] is None  # an entry has no length


def test_an_artists_detail_counts_versions_and_lists_albums(client, conn):
    # source: api-Z1.md §6.2 -- artist: merged_ids, version_count, album_count,
    # albums (album summaries); "its tracks are not inlined".
    build(conn)

    artist = get(client, "/api/v1/artists/ar-beta")

    assert artist["version_count"] == 3 and artist["album_count"] == 1
    assert [a["id"] for a in artist["albums"]] == ["al-a"]
    assert "tracks" not in artist and "members" not in artist
    assert get(client, "/api/v1/versions?artist=ar-beta")["total"] == 3
