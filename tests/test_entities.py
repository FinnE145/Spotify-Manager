"""`entities.py` -- play stats, the playlist rollup, and the two guarded
one-request-per-page-load Spotify detail fetches (docs/specs/entity-pages-K.md,
Audited 2026-08-17).

The two fetches are the only writes here, and they only ever write locally.
Their whole point is a hard ceiling -- at most one Spotify request per page
load, ever -- so several tests here are about what does *not* happen (no
second request, no paging past the first page) as much as what does.
"""

import builders
import entities


# -- play_stats ---------------------------------------------------------


def test_play_stats_on_no_tracks_still_reads_data_through(conn):
    # source: entities.play_stats's docstring -- "total is never None -- it
    # isn't windowed, so staleness doesn't apply to it" and the empty-list
    # early return still has to report data_through honestly.
    builders.make_play(conn, ts="2026-08-01T00:00:00Z")

    stats = entities.play_stats(conn, [])

    assert stats == {
        "total": 0,
        "month": 0,
        "week": 0,
        "data_through": "2026-08-01T00:00:00Z",
    }


def test_play_stats_resolves_relinked_uris_through_played_uri_track(conn):
    # source: entity-pages-K.md §8 -- "Resolves through the
    # played_uri_track view, never track.uri directly, so relinked uris
    # count." A play against the *requested* (aliased) uri, not the track's
    # real one, must still count -- a naive `JOIN track ON track.uri` would
    # not see it.
    builders.make_uri_alias(conn, "spotify:track:requested-elsewhere", "t1")
    builders.make_play(conn, uri="spotify:track:requested-elsewhere", ts=builders.days_ago(1))

    stats = entities.play_stats(conn, ["t1"])

    assert stats["total"] == 1


def test_play_stats_windows_are_not_swapped(conn):
    # source: entity-pages-K.md §8 -- "Windows are past 7 and past 30 days
    # relative to now." A play at 3d counts in both windows, one at 8d counts
    # only in month, one at 31d counts in neither -- three different numbers,
    # so a week/month swap is caught rather than agreeing by coincidence.
    t1 = builders.make_track(conn, "t1")
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(3))
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(8))
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(31))

    stats = entities.play_stats(conn, [t1])

    assert stats["total"] == 3
    assert stats["month"] == 2
    assert stats["week"] == 1


def test_play_stats_week_boundary_is_inclusive(conn):
    # source: entity-pages-K.md §8's "past 7 ... days" -- the code computes
    # `now - 7 days` and compares with `>=`, so a play exactly at that instant
    # counts.
    t1 = builders.make_track(conn, "t1")
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(7))

    stats = entities.play_stats(conn, [t1])

    assert stats["week"] == 1


def test_play_stats_stale_data_renders_none_not_zero(conn):
    # source: entity-pages-K.md §8 -- "When data_through is older than a
    # window's start, that window renders '-', not '0', so a stale export is
    # visibly different from a genuine zero." Assert `is None`, not falsy --
    # 0 is falsy and is exactly the wrong answer here.
    t1 = builders.make_track(conn, "t1")
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(10))

    stats = entities.play_stats(conn, [t1])

    assert stats["week"] is None
    assert isinstance(stats["month"], int)
    assert stats["total"] == 1


def test_play_stats_very_stale_data_nulls_both_windows_but_not_total(conn):
    # source: entity-pages-K.md §8 -- same clause, taken past both window
    # starts. "total is never None -- it isn't windowed" is the other half of
    # the same docstring sentence.
    t1 = builders.make_track(conn, "t1")
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(40))

    stats = entities.play_stats(conn, [t1])

    assert stats["month"] is None
    assert stats["week"] is None
    assert stats["total"] == 1


def test_play_stats_data_through_is_whole_table_not_scoped_to_track_ids(conn):
    # source: entity-pages-K.md §8 -- "data_through is MAX(play.ts) across
    # the whole play table." A play against a track *not* in track_ids must
    # still move data_through -- an implementation that scoped MAX(ts) to the
    # queried tracks would give an earlier, wrong answer here.
    t1 = builders.make_track(conn, "t1")
    t2 = builders.make_track(conn, "t2")
    builders.make_play(conn, track_id=t1, ts="2026-08-01T00:00:00Z")
    builders.make_play(conn, track_id=t2, ts="2026-08-15T00:00:00Z")

    stats = entities.play_stats(conn, [t1])

    assert stats["data_through"] == "2026-08-15T00:00:00Z"


def test_play_stats_aggregates_across_multiple_track_ids(conn):
    # characterization -- the IN-clause aggregation over more than one id.
    t1 = builders.make_track(conn, "t1")
    t2 = builders.make_track(conn, "t2")
    builders.make_play(conn, track_id=t1, ts=builders.days_ago(1))
    builders.make_play(conn, track_id=t2, ts=builders.days_ago(1))

    stats = entities.play_stats(conn, [t1, t2])

    assert stats["total"] == 2
    assert stats["week"] == 2


# -- playlists_for_tracks -------------------------------------------------


def test_playlists_for_tracks_empty_input(conn):
    # characterization -- the empty-list early return, before any SQL is built.
    assert entities.playlists_for_tracks(conn, []) == []


def test_playlists_for_tracks_includes_removed_memberships(conn):
    # source: playlists_for_tracks's docstring -- "Every playlist membership
    # (live or removed)". A playlist whose only membership row is removed
    # must still appear -- an implementation that filtered removed_at IS
    # NULL (the live-only rule used elsewhere) would drop it.
    t1 = builders.make_track(conn, "t1")
    p1 = builders.make_playlist(conn, "p1", name="Old List")
    builders.make_membership(conn, playlist_id=p1, track_id=t1, removed_at=builders.days_ago(1))

    rows = entities.playlists_for_tracks(conn, [t1])

    assert len(rows) == 1
    assert rows[0]["playlist_id"] == p1
    assert rows[0]["removed_at"] is not None


def test_playlists_for_tracks_orders_by_name_case_insensitively(conn):
    # source: playlists_for_tracks's SQL -- "ORDER BY s.name COLLATE NOCASE,
    # m.added_at". Names chosen so a binary sort (Banana < apple < cherry,
    # since uppercase sorts before lowercase in ASCII) disagrees with the
    # NOCASE order (apple, Banana, cherry) -- a missing COLLATE would fail
    # this and pass a same-case fixture.
    t1 = builders.make_track(conn, "t1")
    p_banana = builders.make_playlist(conn, "p-banana", name="Banana")
    p_apple = builders.make_playlist(conn, "p-apple", name="apple")
    p_cherry = builders.make_playlist(conn, "p-cherry", name="cherry")
    for p in (p_banana, p_apple, p_cherry):
        builders.make_membership(conn, playlist_id=p, track_id=t1)

    rows = entities.playlists_for_tracks(conn, [t1])

    assert [r["playlist_id"] for r in rows] == [p_apple, p_banana, p_cherry]


# -- fetch_album_tracklist -------------------------------------------------


def _album_payload(album_id, item_ids, next_token=None):
    items = [
        {
            "id": tid,
            "uri": f"spotify:track:{tid}",
            "name": f"Track {tid}",
            "artists": [{"name": "An Artist"}],
            "duration_ms": 200000,
            "explicit": False,
            "track_number": i + 1,
            "disc_number": 1,
        }
        for i, tid in enumerate(item_ids)
    ]
    tracks = {"items": items}
    if next_token is not None:
        tracks["next"] = next_token
    return {"id": album_id, "tracks": tracks}


def test_fetch_album_tracklist_no_client_stamps_nothing(conn, monkeypatch):
    # source: entity-pages-K.md §5.3 -- "(No client at all -- not logged in --
    # still doesn't stamp anything; that's not a real attempt.)"
    monkeypatch.setattr(entities, "get_spotify_client", lambda: None)
    builders.make_album(conn, "al-1")

    entities.fetch_album_tracklist(conn, "al-1")

    row = conn.execute(
        "SELECT tracklist_json, tracklist_pulled_at FROM album WHERE album_id = ?", ("al-1",)
    ).fetchone()
    assert row["tracklist_json"] is None
    assert row["tracklist_pulled_at"] is None


def test_fetch_album_tracklist_success_stores_items_and_stamps(conn, fake_spotify):
    # source: entity-pages-K.md §5.3 -- "On first view ... spend one request
    # on GET /v1/albums/{id}, store the response's tracks.items into
    # album.tracklist_json and stamp album.tracklist_pulled_at."
    builders.make_album(conn, "al-1")
    fake_spotify.add_album(_album_payload("al-1", ["t1", "t2"]))

    entities.fetch_album_tracklist(conn, "al-1")

    row = conn.execute(
        "SELECT tracklist_json, tracklist_pulled_at FROM album WHERE album_id = ?", ("al-1",)
    ).fetchone()
    assert row["tracklist_pulled_at"] is not None
    import json

    stored = json.loads(row["tracklist_json"])
    assert [item["id"] for item in stored] == ["t1", "t2"]


def test_fetch_album_tracklist_failure_still_stamps_but_leaves_json_null(conn, fake_spotify):
    # source: entity-pages-K.md §5.3 -- "Fixed during P1 (P1-016): a failed
    # attempt now also stamps tracklist_pulled_at ... tracklist_json stays
    # NULL on a failed attempt ... it simply stops re-spending a request on
    # every later visit." Both halves of this must hold together, or a
    # transient failure either retries forever or is indistinguishable from
    # a real fetch.
    builders.make_album(conn, "al-1")
    fake_spotify.fail("album", Exception("429"))

    entities.fetch_album_tracklist(conn, "al-1")

    row = conn.execute(
        "SELECT tracklist_json, tracklist_pulled_at FROM album WHERE album_id = ?", ("al-1",)
    ).fetchone()
    assert row["tracklist_pulled_at"] is not None
    assert row["tracklist_json"] is None


def test_fetch_album_tracklist_never_pages_past_the_first_page(conn, fake_spotify):
    # source: entity-pages-K.md §1.3 -- "An album with more than 50 tracks
    # renders the 50 that came inline plus a note; it does not page for the
    # rest. ... At most one Spotify request per page load." A tracks object
    # carrying a `next` token must not trigger a second request.
    builders.make_album(conn, "al-1")
    fake_spotify.add_album(_album_payload("al-1", ["t1"], next_token="https://api/next-page"))

    entities.fetch_album_tracklist(conn, "al-1")

    assert [c for c in fake_spotify.calls if c[0] == "next"] == []
    assert len([c for c in fake_spotify.calls if c[0] == "album"]) == 1


# -- queue_wanted_uris -------------------------------------------------


def test_queue_wanted_uris_no_tracklist_queues_nothing(conn):
    # source: grouping-fixes-backfill-M.md §4.4 -- queuing runs off the *stored*
    # tracklist, so an album with none queues nothing and spends nothing.
    builders.make_album(conn, "al-1")

    assert entities.queue_wanted_uris(conn, "al-1", source="album") == 0
    assert conn.execute("SELECT COUNT(*) FROM wanted_uri").fetchone()[0] == 0


def test_queue_wanted_uris_skips_owned_tracks(conn, fake_spotify):
    # source: entity-pages-K.md §6 -- the queue exists for unowned tracklist
    # entries only. An owned id must not be queued.
    builders.make_album(conn, "al-1")
    builders.make_track(conn, "t-owned", album_id="al-1")
    fake_spotify.add_album(_album_payload("al-1", ["t-owned", "t-unowned"]))
    entities.fetch_album_tracklist(conn, "al-1")

    queued = entities.queue_wanted_uris(conn, "al-1", source="album")

    assert queued == 1
    rows = conn.execute("SELECT uri, source, album_id FROM wanted_uri").fetchall()
    assert len(rows) == 1
    assert rows[0]["uri"] == "spotify:track:t-unowned"
    assert rows[0]["source"] == "album"
    assert rows[0]["album_id"] == "al-1"


def test_queue_wanted_uris_first_source_wins_on_reinsert(conn, fake_spotify):
    # source: entity-pages-K.md §6 -- "Whichever route queues a uri first
    # owns its source (INSERT OR IGNORE)." A second queue attempt with a
    # different source must not overwrite the first.
    builders.make_album(conn, "al-1")
    fake_spotify.add_album(_album_payload("al-1", ["t-unowned"]))
    entities.fetch_album_tracklist(conn, "al-1")
    entities.queue_wanted_uris(conn, "al-1", source="album")

    second = entities.queue_wanted_uris(conn, "al-1", source="backfill")

    assert second == 0
    row = conn.execute("SELECT source FROM wanted_uri WHERE uri = ?", ("spotify:track:t-unowned",)).fetchone()
    assert row["source"] == "album"


def test_queue_wanted_uris_spends_no_spotify_requests(conn, fake_spotify):
    # source: entity-pages-K.md §5.3 -- "queue_wanted_uris ... is a separate,
    # zero-Spotify-cost function that reads the *stored* tracklist_json."
    # This is the observation nothing else here makes: the call count, not
    # just the return value.
    builders.make_album(conn, "al-1")
    fake_spotify.add_album(_album_payload("al-1", ["t-unowned"]))
    entities.fetch_album_tracklist(conn, "al-1")
    fake_spotify.calls.clear()

    entities.queue_wanted_uris(conn, "al-1", source="album")

    assert fake_spotify.calls == []


def test_queue_wanted_uris_skips_items_missing_id_or_uri(conn):
    # characterization -- the per-item id/uri guard in the loop.
    builders.make_album(conn, "al-1")
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = ? WHERE album_id = ?",
        (
            '[{"name": "No id at all"}, {"id": "t-real", "uri": "spotify:track:t-real"}]',
            builders.days_ago(0),
            "al-1",
        ),
    )
    conn.commit()

    queued = entities.queue_wanted_uris(conn, "al-1", source="album")

    assert queued == 1


# -- fetch_artist_image -------------------------------------------------


def test_fetch_artist_image_picks_largest_width_not_first(conn, fake_spotify):
    # source: entity-pages-K.md §7.1 -- "'The largest' wasn't actually
    # computed -- the code took images[0] ... Now picks by max(width)
    # explicitly." images[0] is deliberately not the largest here, so an
    # images[0] implementation fails.
    builders.make_artist(conn, "ar-1")
    fake_spotify.add_artist(
        {
            "id": "ar-1",
            "images": [
                {"url": "https://img/160", "width": 160},
                {"url": "https://img/640", "width": 640},
                {"url": "https://img/320", "width": 320},
            ],
        }
    )

    entities.fetch_artist_image(conn, "ar-1")

    row = conn.execute("SELECT image_url FROM artist WHERE artist_id = ?", ("ar-1",)).fetchone()
    assert row["image_url"] == "https://img/640"


def test_fetch_artist_image_missing_width_treated_as_zero(conn, fake_spotify):
    # characterization -- `im.get("width") or 0` in the max() key.
    builders.make_artist(conn, "ar-1")
    fake_spotify.add_artist(
        {
            "id": "ar-1",
            "images": [{"url": "https://img/no-width"}, {"url": "https://img/50", "width": 50}],
        }
    )

    entities.fetch_artist_image(conn, "ar-1")

    row = conn.execute("SELECT image_url FROM artist WHERE artist_id = ?", ("ar-1",)).fetchone()
    assert row["image_url"] == "https://img/50"


def test_fetch_artist_image_no_images_leaves_url_null_but_stamps(conn, fake_spotify):
    # source: entity-pages-K.md, via P1-016 -- a fetch that returns no usable
    # image still stamps `detail_pulled_at`, or the page retries forever.
    builders.make_artist(conn, "ar-1")
    fake_spotify.add_artist({"id": "ar-1", "images": []})

    entities.fetch_artist_image(conn, "ar-1")

    row = conn.execute(
        "SELECT image_url, detail_pulled_at FROM artist WHERE artist_id = ?", ("ar-1",)
    ).fetchone()
    assert row["image_url"] is None
    assert row["detail_pulled_at"] is not None


def test_fetch_artist_image_failure_stamps_but_leaves_url_null(conn, fake_spotify):
    # source: entity-pages-K.md §7.1 -- "a failed attempt didn't stamp
    # detail_pulled_at (only success did), so a transient failure retried on
    # every subsequent page view forever instead of the intended once --
    # same fix and same reasoning as §5.3's tracklist-fetch bug."
    builders.make_artist(conn, "ar-1")
    fake_spotify.fail("artist", Exception("429"))

    entities.fetch_artist_image(conn, "ar-1")

    row = conn.execute(
        "SELECT image_url, detail_pulled_at FROM artist WHERE artist_id = ?", ("ar-1",)
    ).fetchone()
    assert row["image_url"] is None
    assert row["detail_pulled_at"] is not None


def test_fetch_artist_image_no_client_stamps_nothing(conn, monkeypatch):
    # source: entity-pages-K.md §7.1 -- same "not a real attempt" rule as the
    # album fetch.
    monkeypatch.setattr(entities, "get_spotify_client", lambda: None)
    builders.make_artist(conn, "ar-1")

    entities.fetch_artist_image(conn, "ar-1")

    row = conn.execute(
        "SELECT image_url, detail_pulled_at FROM artist WHERE artist_id = ?", ("ar-1",)
    ).fetchone()
    assert row["detail_pulled_at"] is None
