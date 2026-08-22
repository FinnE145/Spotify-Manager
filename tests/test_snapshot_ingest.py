"""The shared track-ingest path, and a whole pull run end to end.

`_usable_track` / `_parse_track_item` / `_upsert_track_full` are the **one**
way a track row gets written -- `roundtrip.py` calls the same three rather than
growing a second one, so changing their signatures breaks it. That makes them
worth pinning independently of either caller.

The pull itself runs against the fake `sp` (`P2_tests.md` §4.4). `snapshot.py`
is **read-only with respect to Spotify** -- nothing in it ever writes to the
library -- and the fake has no write method but `playlist_replace_items`, so
`sp.replacements` staying empty across a full pull is a real assertion of that
invariant rather than a restatement of it.
"""

import pytest

import builders
import db
import fakes
import jobs
import snapshot


def playlist_item_calls(sp):
    return [call for call in sp.calls if call[0] == "playlist_items"]


# -- Parsing ----------------------------------------------------------------


def test_the_cover_is_the_300px_image(conn):
    # source: snapshot._album_image_url -- Spotify returns 640/300/64 and the
    # 300 is the one the UI wants.
    album = fakes.spotify_album("al1")
    assert snapshot._album_image_url(album) == "https://i.scdn.co/image/al1-300"


def test_the_cover_falls_back_to_the_middle_image(conn):
    """A fake with a single image would only ever exercise this fallback,
    which is why `fakes.spotify_album` ships all three sizes."""
    # source: snapshot._album_image_url -- "falling back to the middle entry,
    # then the first."
    album = fakes.spotify_album("al1", images=[
        {"url": "a", "width": 640}, {"url": "b", "width": 500}, {"url": "c", "width": 64},
    ])
    assert snapshot._album_image_url(album) == "b"


def test_an_album_with_no_images_has_no_cover(conn):
    # source: snapshot._album_image_url -- the empty-list arm.
    assert snapshot._album_image_url(fakes.spotify_album("al1", images=[])) is None


@pytest.mark.parametrize(
    "release_date, precision, expected",
    [
        ("2024", "year", "2024-01-01"),
        ("2024-06", "month", "2024-06-01"),
        ("2024-06-15", "day", "2024-06-15"),
        (None, "day", None),
    ],
)
def test_release_dates_are_padded_so_they_sort_across_precisions(
    release_date, precision, expected
):
    """A year-precision release must sort against a day-precision one, and
    string comparison on `2024` vs `2024-06-15` would not."""
    # source: snapshot._sortable_release_date -- "Pads a release_date to a
    # full date so it sorts correctly regardless of precision."
    assert snapshot._sortable_release_date(release_date, precision) == expected


@pytest.mark.parametrize(
    "track, usable",
    [
        (None, False),
        ({"id": "t1", "is_local": True}, False),
        ({"id": "t1", "type": "episode"}, False),
        ({"id": None}, False),
        ({}, False),
        ({"id": "t1"}, True),
    ],
)
def test_what_counts_as_a_usable_track(track, usable):
    """The playlist-items endpoint can hold episodes and local files, neither
    of which has anything Symr can store."""
    # source: snapshot._usable_track -- characterization of all four rejections.
    assert snapshot._usable_track(track) is usable


def test_a_parsed_track_carries_the_fields_the_row_needs(conn):
    # source: snapshot._parse_track_item -- characterization of the mapping
    # from Spotify's object to Symr's columns.
    track = fakes.spotify_track("t1", name="A Song")
    parsed = snapshot._parse_track_item(track, "2024-03-01T00:00:00Z", 4)

    assert parsed["track_id"] == "t1"
    assert parsed["name"] == "A Song"
    assert parsed["uri"] == "spotify:track:t1"
    assert parsed["isrc"] == "ISRCt1"
    assert parsed["added_at"] == "2024-03-01T00:00:00Z"
    assert parsed["position"] == 4
    assert parsed["explicit"] == 0


def test_is_playable_keeps_its_three_way_distinction(conn):
    """Spotify omits `is_playable` unless market relinking is in play, and
    "absent" is not the same fact as "not playable"."""
    # source: snapshot._parse_track_item -- `None if is_playable is None else
    # (1 if is_playable else 0)`.
    absent = fakes.spotify_track("t1")
    del absent["is_playable"]

    assert snapshot._parse_track_item(absent, None, 0)["is_playable"] is None
    assert snapshot._parse_track_item(
        fakes.spotify_track("t2", is_playable=False), None, 0
    )["is_playable"] == 0


def test_a_relinked_track_records_what_was_requested(conn):
    # source: snapshot._parse_track_item -- linked_from is stored whole as
    # JSON plus its id in its own column.
    track = fakes.spotify_track("new", linked_from={"id": "old", "uri": "spotify:track:old"})
    parsed = snapshot._parse_track_item(track, None, 0)

    assert parsed["linked_from_id"] == "old"
    assert "spotify:track:old" in parsed["linked_from"]


def test_an_album_with_no_id_does_not_parse(conn):
    """Spotify serves an album stub with no id on some relinked tracks, and a
    row keyed on NULL is not a row."""
    # source: snapshot._parse_album -- the `if not album.get("id")` guard.
    assert snapshot._parse_album({"name": "Orphan"}) is None


# -- The shared upsert path -------------------------------------------------


def test_upserting_a_track_fills_the_album_and_artist_tables_too(conn):
    """Artists first, then the album, then the track, then the credit tables
    -- artist and album rows must exist before `track_artist`/`album_artist`
    can reference them under `PRAGMA foreign_keys = ON`."""
    # source: snapshot._upsert_track_full's docstring -- the upsert order is
    # load-bearing, not incidental.
    album = fakes.spotify_album("al1", artists=[fakes.spotify_artist("ar1")])
    track = fakes.spotify_track(
        "t1", album=album,
        artists=[fakes.spotify_artist("ar1"), fakes.spotify_artist("ar2")],
    )

    snapshot._upsert_track_full(conn, snapshot._parse_track_item(track, None, 0))
    conn.commit()

    assert conn.execute("SELECT album_id FROM track WHERE track_id = 't1'").fetchone()[0] == "al1"
    assert conn.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 2
    credits = conn.execute(
        "SELECT artist_id, position FROM track_artist WHERE track_id = 't1' ORDER BY position"
    ).fetchall()
    assert [(row["artist_id"], row["position"]) for row in credits] == [("ar1", 0), ("ar2", 1)]


def test_credits_are_replaced_not_merged_on_a_re_pull(conn):
    """`_replace_track_artists` deletes first, so a credit Spotify has dropped
    actually goes -- a merge would leave it forever."""
    # source: snapshot._replace_track_artists -- DELETE then re-INSERT.
    two = fakes.spotify_track(
        "t1", artists=[fakes.spotify_artist("ar1"), fakes.spotify_artist("ar2")]
    )
    snapshot._upsert_track_full(conn, snapshot._parse_track_item(two, None, 0))
    one = fakes.spotify_track("t1", artists=[fakes.spotify_artist("ar1")])
    snapshot._upsert_track_full(conn, snapshot._parse_track_item(one, None, 0))
    conn.commit()

    assert [
        row["artist_id"]
        for row in conn.execute("SELECT artist_id FROM track_artist WHERE track_id = 't1'")
    ] == ["ar1"]


def test_a_missing_cover_does_not_wipe_one_already_stored(conn):
    """Spotify sometimes omits images on an album object, and a NULL from one
    pull must not clobber a value already held."""
    # source: snapshot._upsert_album -- "COALESCE, not plain overwrite".
    with_image = fakes.spotify_track("t1", album=fakes.spotify_album("al1"))
    snapshot._upsert_track_full(conn, snapshot._parse_track_item(with_image, None, 0))

    without = fakes.spotify_track("t1", album=fakes.spotify_album("al1", images=[]))
    snapshot._upsert_track_full(conn, snapshot._parse_track_item(without, None, 0))
    conn.commit()

    assert conn.execute(
        "SELECT image_url FROM album WHERE album_id = 'al1'"
    ).fetchone()[0] == "https://i.scdn.co/image/al1-300"


def test_the_display_artist_string_is_written_but_never_read(conn):
    """`track.artists` is a pre-joined display column; the read path is the
    `track_artists` view. It is filled anyway, because a row that left it
    empty would not look like a real one."""
    # source: CLAUDE.md's db.py entry -- "track.artists is write-only, never
    # read".
    track = fakes.spotify_track(
        "t1",
        artists=[fakes.spotify_artist("ar1", name="One"), fakes.spotify_artist("ar2", name="Two")],
    )

    snapshot._upsert_track_full(conn, snapshot._parse_track_item(track, None, 0))
    conn.commit()

    assert conn.execute("SELECT artists FROM track WHERE track_id = 't1'").fetchone()[0] == (
        "One, Two"
    )


# -- Fetching a playlist's items --------------------------------------------


def test_positions_are_a_dense_index_that_skips_unusable_entries(conn, fake_spotify):
    """Position is Symr's own per-pull index, **not** Spotify's raw item
    index: a local file or an episode is skipped *without* incrementing it.
    A fixture built against the raw index would be off by one for every track
    after the first skip.
    """
    # source: snapshot.md "Change detection & diffing" -- "position here is
    # Symr's own dense per-pull index (locals/episodes are skipped without
    # incrementing it), not Spotify's raw item index."
    fake_spotify.add_playlist("p1", "Mixed", tracks=[
        fakes.spotify_track("t1"),
        fakes.spotify_track("loc", is_local=True),
        fakes.spotify_track("ep1", type="episode"),
        fakes.spotify_track("t2"),
    ])

    items = snapshot._fetch_playlist_items(fake_spotify, "p1")

    assert [(it["track_id"], it["position"]) for it in items] == [("t1", 0), ("t2", 1)]


def test_a_multi_page_playlist_is_read_to_the_end(conn, fake_spotify):
    # source: snapshot._fetch_playlist_items -- the `while results["next"]`
    # paging loop.
    tracks = [fakes.spotify_track(f"t{index}") for index in range(150)]
    fake_spotify.add_playlist("p1", "Long", tracks=tracks)

    items = snapshot._fetch_playlist_items(fake_spotify, "p1")

    assert len(items) == 150
    assert [it["position"] for it in items] == list(range(150))


# -- Applying them ----------------------------------------------------------


def test_applying_items_stamps_the_capture_id_and_clears_the_error(conn):
    """The self-referential write J §2.1 turns on: `tracks_pulled_snapshot_id`
    is set from the `snapshot_id` this run's list pass already stored, which is
    the value to compare against next run."""
    # source: partial-pulls-J.md §2.1 -- this column is the only one that
    # means "the stored items are current".
    playlist = builders.make_playlist(
        conn, snapshot_id="snap-new", tracks_pulled_snapshot_id="snap-old",
        last_pull_error="403 Forbidden",
    )
    # A real parsed item, not a hand-built dict: `_apply_playlist_items` hands
    # it to the shared upsert path, which reads every column of a track row.
    items = [snapshot._parse_track_item(
        fakes.spotify_track("t1"), "2024-03-01T00:00:00Z", 0
    )]

    snapshot._apply_playlist_items(conn, playlist, items)
    conn.commit()

    row = conn.execute(
        "SELECT tracks_pulled_snapshot_id, track_count, last_changed_at, last_pull_error "
        "FROM snapshot WHERE playlist_id = ?", (playlist,)
    ).fetchone()
    assert row["tracks_pulled_snapshot_id"] == "snap-new"
    assert row["track_count"] == 1
    assert row["last_changed_at"] == "2024-03-01T00:00:00Z"
    assert row["last_pull_error"] is None


def test_last_changed_advances_on_our_own_removal_stamp(conn):
    """`last_changed_at` is computed, never a status you set -- and it spans
    `removed_at` as well as `added_at`, so a departure moves it too."""
    # source: snapshot.md "Derived recency" -- "it's max(added_at, removed_at)
    # across its memberships (so our own removal stamps advance it too)."
    playlist = builders.make_playlist(conn)
    track = builders.make_track(conn)
    builders.make_membership(conn, playlist, track, position=0, added_at="2024-01-01T00:00:00Z")

    snapshot._apply_playlist_items(conn, playlist, [])
    conn.commit()

    row = conn.execute(
        "SELECT track_count, last_changed_at FROM snapshot WHERE playlist_id = ?", (playlist,)
    ).fetchone()
    assert row["track_count"] == 0
    assert row["last_changed_at"] == jobs.now_iso()


# -- A whole pull -----------------------------------------------------------


@pytest.fixture
def library(fake_spotify):
    """A small library: two playlists and a couple of saved tracks."""
    fake_spotify.add_playlist("p1", "Finn All", tracks=[
        fakes.spotify_track("t1"), fakes.spotify_track("t2"),
    ])
    fake_spotify.add_playlist("p2", "v37.0.0", tracks=[fakes.spotify_track("t1")])
    fake_spotify.add_saved_tracks([fakes.spotify_track("t3")])
    return fake_spotify


def test_a_refresh_pulls_every_playlist_it_has_never_seen(conn, library):
    # source: partial-pulls-J.md §2.2 -- a playlist with no stored row is
    # stale, so a first refresh captures the whole library.
    snapshot._run_pull(force_all=False)

    assert snapshot._status.get()["phase"] == "done"
    assert {
        row["playlist_id"] for row in conn.execute("SELECT playlist_id FROM snapshot")
    } == {"p1", "p2", snapshot.LIKED_PLAYLIST_ID}
    assert conn.execute(
        "SELECT COUNT(*) FROM membership WHERE removed_at IS NULL"
    ).fetchone()[0] == 4
    assert db.get_meta(conn, "last_refresh_at") == jobs.now_iso()


def test_a_pull_never_writes_to_the_spotify_library(conn, library):
    """`snapshot.py` is read-only with respect to Spotify. The fake's only
    write method is `playlist_replace_items`, so an empty write log is the
    whole of that invariant."""
    # source: CLAUDE.md's snapshot.py entry -- "Read-only w.r.t. Spotify --
    # nothing here ever writes to the library."
    snapshot._run_pull(force_all=True)

    assert library.replacements == []


def test_liked_songs_is_pulled_through_its_own_endpoint(conn, library):
    """Half an exception to every playlist rule: a `snapshot` row with no
    `snapshot_id`, pulled outside the main loop."""
    # source: partial-pulls-J.md §2.7 -- Liked Songs stays the tail step.
    snapshot._run_pull(force_all=False)

    row = conn.execute(
        "SELECT name, snapshot_id, track_count FROM snapshot WHERE playlist_id = ?",
        (snapshot.LIKED_PLAYLIST_ID,),
    ).fetchone()
    assert row["name"] == "Liked Songs"
    assert row["snapshot_id"] is None
    assert row["track_count"] == 1


def test_a_second_refresh_reads_no_items_for_an_unchanged_playlist(conn, library):
    """The cheap gate: the list pass returns every playlist's current
    `snapshot_id` in a handful of calls, and only changed ones cost an item
    read."""
    # source: snapshot.md -- "snapshot_id is the cheap gate ... an untouched
    # one is never re-pulled."
    snapshot._run_pull(force_all=False)
    before = len(playlist_item_calls(library))
    snapshot._run_pull(force_all=False)

    assert before == 2
    assert len(playlist_item_calls(library)) == before


def test_a_full_pull_re_reads_a_playlist_the_refresh_rule_calls_done(conn, library, freezer):
    """The epoch is minted at `now` and compared against `tracks_pulled_at`,
    so the clock has to move between the two runs -- with it frozen the epoch
    equals the capture time it is supposed to beat, and the full pull finds
    nothing to do. An explicit tick is visible; a drifting clock is not."""
    # source: partial-pulls-J.md §2.3 -- the epoch is what expresses "done for
    # *this* pull".
    snapshot._run_pull(force_all=False)
    before = len(playlist_item_calls(library))
    freezer.tick(60)

    snapshot._run_pull(force_all=True)

    assert len(playlist_item_calls(library)) == before + 2
    assert db.get_meta(conn, "pull_force_epoch") is not None


def test_an_excluded_playlist_keeps_its_metadata_fresh_but_is_never_read(conn, library, freezer):
    """Playlist-level metadata is refreshed for every playlist regardless of
    exclusion -- only the item read is skipped."""
    # source: snapshot._sync_playlists_and_get_targets -- its comment, and
    # track-metadata-A.md's exclude-flag section.
    snapshot._run_pull(force_all=False)
    snapshot.set_excluded(conn, ["p2"], True)
    library.playlists[1]["name"] = "v37.0.1"
    library.playlists[1]["snapshot_id"] = "snap-changed"
    before = len(playlist_item_calls(library))
    # See the full-pull test above: the epoch has to postdate the capture.
    freezer.tick(60)

    snapshot._run_pull(force_all=True)

    assert conn.execute(
        "SELECT name FROM snapshot WHERE playlist_id = 'p2'"
    ).fetchone()[0] == "v37.0.1"
    assert [call[1][0] for call in playlist_item_calls(library)[before:]] == ["p1"]


def test_a_playlist_that_disappeared_is_marked_unfollowed(conn, library):
    # source: snapshot._sync_playlists_and_get_targets -- existing ids minus
    # seen ids get an unfollowed_at stamp.
    snapshot._run_pull(force_all=False)
    library.playlists = [p for p in library.playlists if p["id"] != "p2"]

    snapshot._run_pull(force_all=False)

    assert conn.execute(
        "SELECT unfollowed_at FROM snapshot WHERE playlist_id = 'p2'"
    ).fetchone()[0] == jobs.now_iso()


def test_one_failing_playlist_records_its_error_and_the_run_continues(conn, library):
    """A failure is per-playlist. The run keeps going and the error is stored,
    which is what puts that playlist back in every subsequent work list."""
    # source: partial-pulls-J.md §2.6 -- "a playlist whose item read failed
    # keeps its old tracks_pulled_snapshot_id, so every subsequent run retries
    # it."
    library.fail("playlist_items", fakes.not_found("playlist gone"), times=1)

    snapshot._run_pull(force_all=False)

    assert snapshot._status.get()["phase"] == "done"
    errors = dict(conn.execute(
        "SELECT playlist_id, last_pull_error FROM snapshot WHERE last_pull_error IS NOT NULL"
    ).fetchall())
    assert len(errors) == 1
    failed = next(iter(errors))
    # It stayed uncaptured, so the refresh rule still calls it stale.
    row = conn.execute(
        "SELECT snapshot_id, tracks_pulled_snapshot_id FROM snapshot WHERE playlist_id = ?",
        (failed,),
    ).fetchone()
    assert snapshot._is_stale(row, row["snapshot_id"]) is True


def test_an_app_quota_block_aborts_the_whole_run(conn, library):
    """A quota block affects every request, not just this playlist -- so the
    run aborts instead of burning through the rest recording the same failure
    over and over."""
    # source: snapshot._run_pull -- the RateLimited branch, and
    # partial-pulls-J.md §3.1.
    library.fail("playlist_items", fakes.rate_limited(retry_after=3600), times=1)

    snapshot._run_pull(force_all=False)

    status = snapshot._status.get()
    assert status["phase"] == "error"
    assert status["retry_at"] is not None
    # Nothing was captured, and no later playlist was even attempted.
    assert len(playlist_item_calls(library)) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM snapshot WHERE tracks_pulled_at IS NOT NULL"
    ).fetchone()[0] == 0


def test_a_stop_ends_the_run_cleanly_and_does_not_claim_a_pull_date(conn, library, monkeypatch):
    """A deliberate stop is not a fault and must not render as one -- and a
    stopped run leaves the page showing the older, honest date rather than
    claiming one."""
    # source: partial-pulls-J.md §2.8 / §3.2 -- both meta keys mean "a pull
    # ran to completion".
    monkeypatch.setattr(jobs, "stop_requested", lambda: True)

    snapshot._run_pull(force_all=False)

    assert snapshot._status.get()["phase"] == "stopped"
    assert db.get_meta(conn, "last_refresh_at") is None
    # The safe point is after the first playlist's commit, so exactly one
    # playlist was captured before it wound up.
    assert conn.execute(
        "SELECT COUNT(*) FROM snapshot WHERE tracks_pulled_at IS NOT NULL"
    ).fetchone()[0] == 1


# -- `_run_backfill`: the track-metadata refill job -------------------------
#
# Distinct from `backfill.py`, which is the *album* backfill. This one spends
# one `GET /v1/tracks/{id}` per track with a NULL `raw_json`, which makes it
# the most request-hungry loop in the tree and the one where the `except`
# ordering below matters most.


def raw_json_ids(conn):
    return {
        row["track_id"]
        for row in conn.execute("SELECT track_id FROM track WHERE raw_json IS NOT NULL")
    }


def track_calls(sp):
    return [call for call in sp.calls if call[0] == "track"]


def test_the_backfill_fills_raw_json_for_every_track_missing_it(conn, fake_spotify):
    # source: CLAUDE.md's codebase map -- the backfill is a "raw_json mop-up",
    # one GET /v1/tracks/{id} per track; snapshot._run_backfill selects
    # exactly `WHERE raw_json IS NULL`.
    builders.make_track(conn, "tb1")
    builders.make_track(conn, "tb2")
    builders.make_track(conn, "tb-done", raw_json='{"id": "tb-done"}')
    for track_id in ("tb1", "tb2"):
        fake_spotify.add_track(fakes.spotify_track(track_id))
    conn.commit()

    snapshot._run_backfill()

    assert snapshot._status.get()["phase"] == "done"
    assert raw_json_ids(conn) == {"tb1", "tb2", "tb-done"}
    # The already-filled track was not re-fetched: the selection is the budget.
    assert sorted(call[1][0] for call in track_calls(fake_spotify)) == ["tb1", "tb2"]


def test_a_quota_block_aborts_the_backfill_rather_than_failing_one_track(conn, fake_spotify):
    """The `except` ordering in the per-track loop is load-bearing.

    `except RateLimited: rollback; raise` sits *above* the generic
    `except Exception` that records the track and carries on. Swap them and a
    quota block degrades into a per-track failure while the job keeps
    spending -- one request per track, against a quota already refusing them,
    and the page still reports `done`.

    **The discriminating assertion is that the second track is never
    attempted**, not the phase: the outer handler sets `phase="error"` either
    way once the loop finally ends, so a swapped ordering still looks like an
    error at the end while having burned the whole queue getting there. Same
    shape as `backfill.py`'s
    `test_a_rate_limit_aborts_the_whole_run_rather_than_failing_one_album`.
    """
    # source: snapshot._run_backfill's own comment -- "An app-level quota
    # block affects every request, not just this track -- abort rather than
    # burning the rest", and partial-pulls-J.md §3.1.
    builders.make_track(conn, "tb1")
    builders.make_track(conn, "tb2")
    for track_id in ("tb1", "tb2"):
        fake_spotify.add_track(fakes.spotify_track(track_id))
    conn.commit()
    # retry_after > 30s, so jobs.call raises rather than sleeping and retrying.
    fake_spotify.fail("track", fakes.rate_limited(retry_after=3600), times=1)

    snapshot._run_backfill()

    status = snapshot._status.get()
    assert status["phase"] == "error"
    assert status["retry_at"] is not None
    assert len(track_calls(fake_spotify)) == 1
    assert raw_json_ids(conn) == set()


def test_one_unavailable_track_is_recorded_and_the_run_carries_on(conn, fake_spotify):
    """The other half of the same ordering: a fault that really is per-track
    must not stop the run. Without this the test above passes against an
    implementation that aborts on everything.
    """
    # source: snapshot._run_backfill -- the generic `except Exception` arm
    # records the track and continues; only RateLimited re-raises.
    builders.make_track(conn, "tb1")
    builders.make_track(conn, "tb2")
    # tb1 is never registered with the fake, so sp.track() 404s on it.
    fake_spotify.add_track(fakes.spotify_track("tb2"))
    conn.commit()

    snapshot._run_backfill()

    status = snapshot._status.get()
    assert status["phase"] == "done"
    assert len(track_calls(fake_spotify)) == 2
    assert raw_json_ids(conn) == {"tb2"}
    assert [f["playlist_id"] for f in status["failed_playlists"]] == ["tb1"]


def test_a_stop_ends_the_backfill_as_stopped_not_as_an_error(conn, fake_spotify, monkeypatch):
    # source: snapshot._run_backfill's own comment -- "A deliberate stop is
    # not a fault and must not render as one", the same rule _run_pull has.
    builders.make_track(conn, "tb1")
    fake_spotify.add_track(fakes.spotify_track("tb1"))
    conn.commit()
    monkeypatch.setattr(jobs, "stop_requested", lambda: True)

    snapshot._run_backfill()

    assert snapshot._status.get()["phase"] == "stopped"
    assert snapshot._status.get()["error"] is None
