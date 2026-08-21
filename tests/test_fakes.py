"""Tests of the fake Spotify client and the inline-job fixture.

As with the builders, the assertions that matter are not "the fake returned a
dict" -- they are that the real parsers, the real jobs.call wrapper and the real
job loops accept what it returns. A fake whose shapes are subtly wrong makes
every Spotify-bound test downstream meaningless.
"""

import pytest

import entities
import jobs
import roundtrip
import snapshot
from builders import make_album, make_artist
from fakes import (
    FakeSpotify,
    bad_request,
    playlist_item,
    rate_limited,
    spotify_album,
    spotify_artist,
    spotify_track,
)


# -- The shapes are the real shapes -----------------------------------------


def test_a_fake_track_survives_the_real_parser_and_upsert(conn):
    # source: snapshot._parse_track_item / _upsert_track_full -- the shared
    # ingest path. If the fake's objects do not go through this cleanly, no
    # Spotify-bound test written on it proves anything.
    track = spotify_track("t1", name="Glue", artists=[spotify_artist("a1", name="Bicep")])
    parsed = snapshot._parse_track_item(track, "2024-03-01T12:00:00Z", 0)
    snapshot._upsert_track_full(conn, parsed)
    conn.commit()

    row = conn.execute(
        "SELECT name, artists, album_id, uri, isrc, duration_ms FROM track WHERE track_id = 't1'"
    ).fetchone()
    assert row["name"] == "Glue"
    assert row["artists"] == "Bicep"
    assert row["uri"] == "spotify:track:t1"
    assert row["isrc"] == "ISRCt1"
    assert conn.execute("SELECT COUNT(*) FROM artist WHERE artist_id = 'a1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM album WHERE album_id = ?", (row["album_id"],)).fetchone()[0] == 1


def test_album_image_selection_finds_the_300px_cover(conn):
    # source: snapshot._album_image_url -- it looks for width 300 specifically
    # and falls back to the middle entry, so a fake with one image would test
    # only the fallback. The three-image shape is the point.
    parsed = snapshot._parse_album(spotify_album("al1"))
    assert parsed["image_url"].endswith("-300")


def test_usable_track_accepts_the_fake_and_rejects_what_it_should(conn):
    # source: snapshot._usable_track -- local files and episodes are skipped
    assert snapshot._usable_track(spotify_track("t1")) is True
    assert snapshot._usable_track(spotify_track("t2", is_local=True)) is False
    assert snapshot._usable_track(spotify_track("t3", type="episode")) is False


# -- Paging ------------------------------------------------------------------


def test_pages_walk_with_sp_next():
    # source: characterization -- every job loop is `while results["next"]:
    # results = _call(sp.next, results)`, so a fake that cannot page cannot
    # exercise any of them
    sp = FakeSpotify()
    for index in range(120):
        sp.add_playlist(f"p{index}")

    page = sp.current_user_playlists(limit=50)
    collected = list(page["items"])
    while page["next"]:
        page = sp.next(page)
        collected.extend(page["items"])

    assert len(collected) == 120
    assert page["next"] is None


def test_an_empty_collection_is_one_empty_page():
    # source: characterization -- the loop above must terminate on an empty
    # library rather than raising on a missing "items"
    sp = FakeSpotify()
    page = sp.current_user_playlists(limit=50)
    assert page["items"] == []
    assert page["next"] is None


def test_playlist_items_honours_offset():
    # source: roundtrip.py:745 reads the loader back with offset=0, limit=100
    sp = FakeSpotify()
    sp.add_playlist("p1", tracks=[spotify_track(f"t{i}") for i in range(10)])
    assert len(sp.playlist_items("p1", offset=4)["items"]) == 6


def test_a_pages_total_is_the_collection_not_the_remainder():
    # source: Spotify's paging object -- `total` is the size of the collection
    # and does not move with `offset`. A fake that shrank it would make a
    # progress figure read low and plausible rather than wrong.
    sp = FakeSpotify()
    sp.add_playlist("p1", tracks=[spotify_track(f"t{i}") for i in range(10)])
    assert sp.playlist_items("p1", offset=4)["total"] == 10


def test_reading_an_unregistered_playlist_404s_rather_than_reading_empty():
    # source: the real endpoint -- and an empty page here would be
    # indistinguishable from "every requested uri came back missing", which is
    # a genuine round-trip outcome the suite has to be able to tell apart.
    sp = FakeSpotify()
    with pytest.raises(Exception) as raised:
        sp.playlist_items("never-registered")
    assert raised.value.http_status == 404


# -- Failure modes (P2_tests.md §4.4) ---------------------------------------


def test_a_short_rate_limit_is_slept_through_and_retried(monkeypatch):
    # source: jobs.call -- a wait of <= 30s is routine (Spotify's rolling 30s
    # window) and safe to sleep through. time.sleep is patched rather than
    # really slept: freezegun does not patch it, so this would cost real
    # seconds on every suite run.
    sleeps = []
    monkeypatch.setattr(jobs.time, "sleep", sleeps.append)

    sp = FakeSpotify()
    sp.add_playlist("p1")
    sp.fail("playlist", rate_limited(5))

    status = jobs.JobStatus("test", requests=0)
    result = jobs.call(status, sp.playlist, "p1")

    assert result["id"] == "p1"
    assert sleeps == [5]
    # Both attempts count -- a 429 retry really did hit the API.
    assert status.get()["requests"] == 2


def test_a_long_rate_limit_fails_fast_instead_of_blocking(monkeypatch):
    # source: jobs.call -- anything past 30s means an app-level quota is
    # exhausted, and blocking a background thread on it is the failure mode
    # spotify_client.py's respect_retry_after_header=False exists to avoid
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: pytest.fail("must not sleep"))

    sp = FakeSpotify()
    sp.add_playlist("p1")
    sp.fail("playlist", rate_limited(3600))

    with pytest.raises(jobs.RateLimited) as raised:
        jobs.call(jobs.JobStatus("test", requests=0), sp.playlist, "p1")
    assert raised.value.retry_after_seconds == 3600


def test_a_400_propagates_rather_than_being_retried():
    # source: jobs.call -- only 429 is special; a 400 on a batch is what
    # roundtrip narrows down with its off-quota probe
    sp = FakeSpotify()
    sp.add_playlist("p1")
    sp.fail("playlist_replace_items", bad_request())
    with pytest.raises(Exception) as raised:
        jobs.call(jobs.JobStatus("test", requests=0), sp.playlist_replace_items, "p1", [])
    assert raised.value.http_status == 400


def test_queued_failures_apply_once_each():
    # source: characterization -- a breaker test needs N consecutive failures
    # followed by a success, so the queue must drain rather than latch
    sp = FakeSpotify()
    sp.add_playlist("p1")
    sp.fail("playlist", bad_request(), times=2)
    for _ in range(2):
        with pytest.raises(Exception):
            sp.playlist("p1")
    assert sp.playlist("p1")["id"] == "p1"


# -- Substitution: the read-as-a-bag cases ----------------------------------


def test_a_substitute_carrying_linked_from_names_what_was_requested():
    # source: roundtrip._linked_from_uri -- "the only trustworthy pairing
    # between a requested uri and a returned track"
    sp = FakeSpotify()
    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)
    sp.substitute("spotify:track:old", "new", linked_from=True)

    sp.playlist_replace_items(roundtrip.LOADER_ID, ["spotify:track:old"])
    served = sp.playlist_items(roundtrip.LOADER_ID)["items"]

    assert len(served) == 1
    track = served[0]["item"]
    assert track["id"] == "new"
    assert roundtrip._linked_from_uri(track) == "spotify:track:old"


def test_a_silent_substitute_carries_nothing_at_all():
    # source: roundtrip.py's reconciliation pass -- "Spotify substitutes some
    # ids without setting linked_from", which is the entire reason that pass
    # exists. A fake that could not express this could not test it.
    sp = FakeSpotify()
    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)
    sp.substitute("spotify:track:old", "new", linked_from=False)

    sp.playlist_replace_items(roundtrip.LOADER_ID, ["spotify:track:old"])
    track = sp.playlist_items(roundtrip.LOADER_ID)["items"][0]["item"]

    assert track["id"] == "new"
    assert roundtrip._linked_from_uri(track) is None


def test_a_dropped_uri_simply_does_not_come_back():
    # source: roundtrip.py -- "uris that don't come back are found by set
    # difference", so the fake has to be able to just omit one
    sp = FakeSpotify()
    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)
    sp.drop("spotify:track:gone")

    sp.playlist_replace_items(roundtrip.LOADER_ID, ["spotify:track:here", "spotify:track:gone"])
    served = [entry["item"]["id"] for entry in sp.playlist_items(roundtrip.LOADER_ID)["items"]]

    assert served == ["here"]


# -- Replace, never append ---------------------------------------------------


def test_replace_leaves_the_playlist_holding_exactly_the_new_uris():
    # source: roundtrip.py's first load-bearing invariant. If replace ever
    # appended, the read-back at offset=0 would be reading an older batch.
    sp = FakeSpotify()
    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)

    sp.playlist_replace_items(roundtrip.LOADER_ID, ["spotify:track:a", "spotify:track:b"])
    sp.playlist_replace_items(roundtrip.LOADER_ID, ["spotify:track:c"])
    served = [entry["item"]["id"] for entry in sp.playlist_items(roundtrip.LOADER_ID)["items"]]

    assert served == ["c"]
    assert sp.replacements == [
        (roundtrip.LOADER_ID, ["spotify:track:a", "spotify:track:b"]),
        (roundtrip.LOADER_ID, ["spotify:track:c"]),
    ]


def test_the_fake_has_no_way_to_append_to_a_playlist():
    # source: P2_tests.md §4.4 -- the invariant enforced by absence. Code that
    # tried to append fails here by name rather than by silently working.
    sp = FakeSpotify()
    assert not hasattr(sp, "playlist_add_items")
    assert not hasattr(sp, "user_playlist_add_tracks")


def test_the_loader_playlist_is_not_registered_by_default():
    # source: roundtrip._guard -- it verifies name and owner live before any
    # write. A fake that satisfied that for free would let a test pass while
    # the guard was broken, which is the one thing the guard is for.
    sp = FakeSpotify()
    with pytest.raises(Exception) as raised:
        sp.playlist(roundtrip.LOADER_ID)
    assert raised.value.http_status == 404


def test_the_guard_rejects_a_playlist_owned_by_someone_else():
    # source: roundtrip._guard -- the fake must be able to express the state
    # the guard exists to refuse
    sp = FakeSpotify()
    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME, owner_id="someone-else")
    playlist = sp.playlist(roundtrip.LOADER_ID)
    assert playlist["owner"]["id"] != sp.current_user()["id"]


def test_a_foreign_owners_display_name_is_not_the_current_users():
    # source: snapshot._fetch_all_playlists stores owner["display_name"] as
    # snapshot.owner while roundtrip's guard compares owner["id"]. A fixed
    # display name would file a foreign playlist under Finn's name and no
    # ownership test written on it would mean anything.
    sp = FakeSpotify(user_id="finn", user_name="Finn")
    sp.add_playlist("mine")
    sp.add_playlist("theirs", owner_id="someone-else")
    assert sp.playlist("mine")["owner"]["display_name"] == "Finn"
    assert sp.playlist("theirs")["owner"]["display_name"] == "someone-else"


# -- Wiring ------------------------------------------------------------------


def test_the_fake_is_wired_into_every_module_that_asks_for_a_client(fake_spotify):
    # source: characterization -- five modules from-import get_spotify_client,
    # and one missed would silently receive None and quietly no-op
    import app as app_module
    import backfill

    for module in (app_module, backfill, entities, roundtrip, snapshot):
        assert module.get_spotify_client() is fake_spotify


def test_entities_detail_fetch_spends_exactly_one_request(conn, fake_spotify):
    # source: entities.fetch_album_tracklist -- "at most one Spotify request,
    # on that entity's own page, on first view only". The load-bearing proof
    # that the fake's album shape is one the real fetch can store.
    album_id = make_album(conn, "al1")
    fake_spotify.add_album(
        spotify_album("al1", tracks={"items": [spotify_track("t1"), spotify_track("t2")], "next": None})
    )

    entities.fetch_album_tracklist(conn, album_id)

    row = conn.execute(
        "SELECT tracklist_json, tracklist_pulled_at FROM album WHERE album_id = 'al1'"
    ).fetchone()
    assert '"t1"' in row["tracklist_json"]
    assert row["tracklist_pulled_at"] == "2026-06-15T12:00:00Z"
    assert [call[0] for call in fake_spotify.calls] == ["album"]


def test_entities_artist_fetch_picks_the_largest_image(conn, fake_spotify):
    # source: entities.fetch_artist_image -- "largest by width, not images[0]"
    # (P1-016). The fake must be able to serve them out of size order.
    artist_id = make_artist(conn, "a1")
    fake_spotify.add_artist(
        spotify_artist(
            "a1",
            images=[
                {"url": "small", "width": 160, "height": 160},
                {"url": "large", "width": 640, "height": 640},
                {"url": "medium", "width": 320, "height": 320},
            ],
        )
    )

    entities.fetch_artist_image(conn, artist_id)

    assert conn.execute(
        "SELECT image_url FROM artist WHERE artist_id = 'a1'"
    ).fetchone()["image_url"] == "large"


# -- The inline-job fixture (P2_tests.md §4.5) ------------------------------


def test_run_jobs_inline_runs_the_target_and_releases_the_slot(run_jobs_inline):
    # source: P2_tests.md §4.5 -- by the time try_start returns, the job has
    # finished, so nothing races the assertions
    ran = []

    def job():
        ran.append(jobs.active())

    assert jobs.try_start("snapshot", job) is True
    assert ran == ["snapshot"]
    assert jobs.active() is None
    assert run_jobs_inline == ["snapshot"]


def test_run_jobs_inline_still_refuses_a_second_job(run_jobs_inline):
    # source: jobs.py -- exactly one job may hold the slot, and the inline
    # version must not quietly relax that
    def outer():
        assert jobs.try_start("roundtrip", lambda: pytest.fail("must not run")) is False

    assert jobs.try_start("snapshot", outer) is True
    assert run_jobs_inline == ["snapshot"]


def test_run_jobs_inline_passes_arguments_through(run_jobs_inline):
    # source: jobs.try_start(name, target, *args)
    received = []
    jobs.try_start("backfill", lambda *args: received.append(args), 7, "extra")
    assert received == [(7, "extra")]


def test_run_jobs_inline_puts_the_api_context_back(run_jobs_inline):
    # source: api_log.api_context -- the real try_start labels a *fresh thread's*
    # context, so the label never escapes the job. Inline there is no second
    # context, and a leaked label would file a later page's requests under the
    # job's name.
    import api_log

    inside = []
    jobs.try_start("snapshot", lambda: inside.append(api_log.api_context.get()))
    assert inside == ["snapshot"]
    assert api_log.api_context.get() is None


def test_run_jobs_inline_releases_the_slot_when_the_job_raises(run_jobs_inline):
    # source: jobs.try_start -- "the slot is released in a finally, so a job
    # that crashes can never wedge the app". Inline, the exception surfaces
    # instead of dying with a thread, which is the documented difference.
    def exploding_job():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        jobs.try_start("snapshot", exploding_job)
    assert jobs.active() is None


# -- Multi-page and Liked Songs (added after scanning the fake) --------------


def test_an_album_tracklist_can_span_pages_for_the_backfill(conn, fake_spotify):
    # source: backfill._fetch_full_tracklist -- "unlike
    # entities.fetch_album_tracklist's deliberate one-request cap, its own
    # fetch pages past the first 50 items". A single-page fake album could not
    # test the one place the two deliberately differ.
    import backfill

    make_album(conn, "al1")
    tracks = [spotify_track(f"t{index}") for index in range(60)]
    fake_spotify.add_album(spotify_album("al1", total_tracks=60, tracks=fake_spotify.paged(tracks, limit=50)))

    backfill._fetch_full_tracklist(conn, fake_spotify, "al1")
    conn.commit()

    stored = conn.execute("SELECT tracklist_json FROM album WHERE album_id = 'al1'").fetchone()[0]
    import json

    assert len(json.loads(stored)) == 60
    assert [call[0] for call in fake_spotify.calls] == ["album", "next"]


def test_entities_fetch_does_not_page_even_when_more_exist(conn, fake_spotify):
    # source: entities.fetch_album_tracklist -- "never page past the first 50
    # tracklist items". The same fixture as above, the opposite expectation:
    # this is the pairing that makes each one mean something.
    make_album(conn, "al1")
    tracks = [spotify_track(f"t{index}") for index in range(60)]
    fake_spotify.add_album(spotify_album("al1", total_tracks=60, tracks=fake_spotify.paged(tracks, limit=50)))

    entities.fetch_album_tracklist(conn, "al1")

    stored = conn.execute("SELECT tracklist_json FROM album WHERE album_id = 'al1'").fetchone()[0]
    import json

    assert len(json.loads(stored)) == 50
    assert [call[0] for call in fake_spotify.calls] == ["album"]


def test_saved_tracks_come_back_keyed_as_track_not_item():
    # source: snapshot._fetch_liked_items -- the saved-tracks endpoint is
    # track-only and still keys it "track", where playlist items key it "item".
    # Getting this backwards would make Liked Songs silently pull nothing.
    sp = FakeSpotify()
    sp.add_saved_tracks([spotify_track("t1"), spotify_track("t2")])
    page = sp.current_user_saved_tracks(limit=50)
    assert [entry["track"]["id"] for entry in page["items"]] == ["t1", "t2"]


def test_next_returns_none_past_the_end_like_spotipy():
    # source: characterization -- a loop calling next() unconditionally should
    # fail loudly here rather than read an empty page forever
    sp = FakeSpotify()
    sp.add_playlist("p1")
    page = sp.current_user_playlists(limit=50)
    assert sp.next(page) is None


def test_add_playlist_registers_its_tracks_for_lookup():
    # source: characterization -- a track handed to add_playlist must be the
    # same object sp.track() returns, not a freshly built stand-in
    sp = FakeSpotify()
    sp.add_playlist("p1", tracks=[spotify_track("t1", name="Glue")])
    assert sp.track("t1")["name"] == "Glue"
