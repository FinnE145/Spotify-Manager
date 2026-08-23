"""`entities.py` -- play stats, the playlist rollup, and the two guarded
one-request-per-page-load Spotify detail fetches (docs/specs/entity-pages-K.md,
Audited 2026-08-17).

The two fetches are the only writes here, and they only ever write locally.
Their whole point is a hard ceiling -- at most one Spotify request per page
load, ever -- so several tests here are about what does *not* happen (no
second request, no paging past the first page) as much as what does.
"""

import builders
import canonical
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


# =======================================================================
# The six entity-page detail functions (P3_refactor.md §4.1)
#
# Extracted out of app.py's views in P3 session 2. The route tests in
# test_routes.py are still the only thing asserting a route *calls* these
# -- losing one re-creates P2-008 exactly -- so these are the other half:
# what each function decides, driven directly against a fixture DB.
#
# None of them run canonical.ensure_track_groups(): that pairing stays in
# the route (§2), so a fixture here builds its groups with make_group.
# =======================================================================


# -- group_detail -------------------------------------------------------


def test_group_detail_returns_none_when_no_group_has_that_id(conn):
    # source: P3_refactor.md §4.1 -- "A missing row returns None and the
    # route calls abort(404, ...) with the description it already uses",
    # here /song/<id>'s "No such group."
    assert entities.group_detail(conn, "song", 999_999) is None


def test_group_detail_returns_none_when_the_id_is_a_group_at_another_tier(conn):
    # source: app.py's group_page guard, moved verbatim -- `row is None or
    # row["tier"] != tier`. The four tiers share one id sequence, so a
    # version id is a perfectly real canonical_group id; an implementation
    # that only checked existence would render /song/<version-id> as a song
    # page. Nothing else in the suite distinguishes those two halves.
    groups = builders.make_group(conn, ["t-tiered"])

    assert entities.group_detail(conn, "song", groups["version"]) is None
    assert entities.group_detail(conn, "version", groups["version"]) is not None


def test_group_detail_returns_track_count_alone_for_a_group_with_no_members(conn):
    # source: P3_refactor.md §4.1 + entities.group_detail's docstring --
    # the second of group_page's two distinct 404s ("Group has no
    # members."), which cannot be left to the route because every line
    # below it indexes track_ids[0]. The payload must be exactly this: a
    # dict that still carried the other keys would mean the emptiness check
    # ran too late to protect them.
    empty_id = conn.execute(
        "INSERT INTO canonical_group (tier, representative_track_id) VALUES ('version', NULL)"
    ).lastrowid
    conn.commit()

    assert entities.group_detail(conn, "version", empty_id) == {"track_count": 0}


def test_group_detail_ranks_member_tracks_by_score_not_by_name_or_id(conn):
    # source: scoring-H.md §11.1 -- ranking is by score, name is only the
    # tiebreak. The fixture disagrees with **every** fallback the sort
    # could have used instead (P2-005): the winner is last by name
    # ("Zebra" > "Apple"), last by track id ("t-zzz" > "t-aaa") and last by
    # insertion order, so only the score puts it first.
    builders.make_track(conn, "t-aaa", name="Apple")
    builders.make_track(conn, "t-zzz", name="Zebra")
    groups = builders.make_group(conn, ["t-aaa", "t-zzz"])
    builders.make_score(conn, "track", "t-aaa", all_time=10.0)
    builders.make_score(conn, "track", "t-zzz", all_time=90.0)

    data = entities.group_detail(conn, "version", groups["version"])

    assert [t["track_id"] for t in data["member_tracks"]] == ["t-zzz", "t-aaa"]


def test_group_detail_scores_a_song_group_by_aggregating_its_versions(conn):
    # source: scoring-H.md §9.1 -- song is not a materialized tier; it
    # aggregates at query time from its member versions, so the song page
    # needs song_scores() and not the scores_for_tier lookup the other
    # three use. The fixture stores a version score and **no song row at
    # all**, so a direct lookup can only produce zero.
    groups = builders.make_group(conn, ["t-song-agg"])
    builders.make_score(conn, "version", groups["version"], all_time=80.0, recent=70.0)

    data = entities.group_detail(conn, "song", groups["song"])

    assert conn.execute("SELECT COUNT(*) FROM score WHERE tier = 'song'").fetchone()[0] == 0
    assert data["score"]["all_time"] > 0


def test_group_detail_reports_tenure_total_and_run_count_separately(conn):
    # source: generations-B.md -- tenure is the *longest* unbroken run,
    # total_generations is how many generations the group was present in,
    # and run_count is how many separate stretches those form. Present in
    # 1-3 and 7-8 out of eight generations, so the three are 3, 5 and 2 --
    # three different numbers, which is what stops one being computed as
    # another and agreeing by coincidence.
    builders.make_track(conn, "t-tenure")
    builders.make_group(conn, ["t-tenure"])
    for ordinal in range(1, 9):
        builders.make_generation(conn, ordinal=ordinal, playlist_id=f"p-ten-{ordinal}")
        if ordinal in (1, 2, 3, 7, 8):
            builders.make_membership(
                conn, playlist_id=f"p-ten-{ordinal}", track_id="t-tenure", position=0
            )
    conn.commit()

    data = entities.group_detail(
        conn, "version", conn.execute("SELECT version_id FROM track_group").fetchone()[0]
    )

    assert (data["tenure"], data["total_generations"], data["run_count"]) == (3, 5, 2)


# -- track_detail -------------------------------------------------------


def test_track_detail_returns_none_for_an_unknown_track(conn):
    # source: P3_refactor.md §4.1 -- the missing-row contract behind
    # /track/<id>'s "Track not found."
    assert entities.track_detail(conn, "t-nonexistent") is None


def test_track_detail_keeps_removed_memberships_and_orders_them_by_playlist_name(conn):
    # source: CLAUDE.md -- `membership` is the append-only per-playlist
    # log, so a removed row is history the track page still shows; the
    # query orders by playlist name, not by insertion. The fixture inserts
    # "Zebra" first so row order and name order disagree, and marks the
    # *Apple* row removed so dropping removed rows would also change the
    # answer.
    builders.make_track(conn, "t-mem")
    builders.make_playlist(conn, "p-zebra", name="Zebra List")
    builders.make_playlist(conn, "p-apple", name="Apple List")
    builders.make_membership(conn, playlist_id="p-zebra", track_id="t-mem")
    builders.make_membership(
        conn, playlist_id="p-apple", track_id="t-mem", removed_at=builders.days_ago(1)
    )

    data = entities.track_detail(conn, "t-mem")

    assert [m["playlist_name"] for m in data["memberships"]] == ["Apple List", "Zebra List"]


def test_track_detail_lists_the_requested_uris_relinked_onto_this_track(conn):
    # source: entity-pages-K.md -- the aliases block names what was asked
    # for and came back as this track. Two aliases, deliberately inserted
    # newest-first, since the query orders by requested_uri.
    builders.make_track(conn, "t-aliased")
    builders.make_uri_alias(conn, "spotify:track:zzz-requested", "t-aliased")
    builders.make_uri_alias(conn, "spotify:track:aaa-requested", "t-aliased")

    data = entities.track_detail(conn, "t-aliased")

    assert [a["requested_uri"] for a in data["aliases"]] == [
        "spotify:track:aaa-requested",
        "spotify:track:zzz-requested",
    ]


# -- album_detail -------------------------------------------------------


def test_album_detail_returns_none_for_an_unknown_album(conn):
    # source: P3_refactor.md §4.1 -- the missing-row contract behind
    # /album/<id>'s "Album not found."
    assert entities.album_detail(conn, "al-nonexistent") is None


def test_album_detail_fetches_the_tracklist_only_when_it_was_never_pulled(conn, fake_spotify):
    # source: entity-pages-K.md §5.3 -- "On first view (tracklist_pulled_at
    # IS NULL only -- never re-fetched automatically)." The guard used to
    # live in app.py and moved here in P3; test_routes.py asserts the route
    # still reaches it, and this asserts the ceiling itself. Two albums
    # rather than two calls on one, so the assertion separates "never
    # fetches again" from "never fetches at all".
    builders.make_album(conn, "al-fresh", name="Fresh")
    builders.make_album(
        conn, "al-already", name="Already", tracklist_pulled_at="2026-01-01T00:00:00Z"
    )
    conn.commit()
    fake_spotify.add_album({"id": "al-fresh", "tracks": {"items": []}})
    fake_spotify.add_album({"id": "al-already", "tracks": {"items": []}})

    entities.album_detail(conn, "al-already")
    assert [c for c in fake_spotify.calls if c[0] == "album"] == []

    entities.album_detail(conn, "al-fresh")
    entities.album_detail(conn, "al-fresh")
    assert len([c for c in fake_spotify.calls if c[0] == "album"]) == 1


def test_album_detail_appends_owned_tracks_the_stored_tracklist_does_not_contain(conn):
    # source: entity-pages-K.md §5.2 -- an album past 50 tracks can hold
    # the one track Symr knows beyond the fetched first page, "and without
    # these the tracklist contradicts its own 'N of M known' header". The
    # owned-but-unfetched track must land in `appended`, not in `rows`:
    # an implementation that dropped the append step reports known_count 2
    # while rendering one row.
    album = builders.make_album(
        conn,
        "al-partial",
        name="Partial",
        total_tracks=60,
        tracklist_json='[{"id":"t-listed","name":"Listed","artists":[],'
        '"track_number":1,"disc_number":1}]',
        tracklist_pulled_at="2026-01-01T00:00:00Z",
    )
    builders.make_track(conn, "t-listed", name="Listed", album_id=album)
    builders.make_track(conn, "t-unlisted", name="Unlisted", album_id=album)
    conn.commit()

    data = entities.album_detail(conn, album)

    assert [r["track_id"] for r in data["rows"]] == ["t-listed"]
    assert [r["track_id"] for r in data["appended"]] == ["t-unlisted"]
    assert data["known_count"] == 2


def test_album_detail_orders_the_tracklist_by_disc_then_track_number(conn):
    # source: characterization -- app.py's album_page sorted on
    # (disc_number or 1, track_number or 0) and the sort moved verbatim.
    # The stored order is deliberately neither the answer nor what sorting
    # on track_number alone would give, so both a no-sort and a
    # disc-ignoring implementation produce a different list.
    album = builders.make_album(
        conn,
        "al-discs",
        name="Discs",
        tracklist_json=(
            '[{"id":"d2t1","name":"D2T1","artists":[],"track_number":1,"disc_number":2},'
            '{"id":"d1t2","name":"D1T2","artists":[],"track_number":2,"disc_number":1},'
            '{"id":"d1t1","name":"D1T1","artists":[],"track_number":1,"disc_number":1}]'
        ),
        tracklist_pulled_at="2026-01-01T00:00:00Z",
    )
    conn.commit()

    data = entities.album_detail(conn, album)

    assert [r["track_id"] for r in data["rows"]] == ["d1t1", "d1t2", "d2t1"]


def test_album_detail_falls_back_to_owned_tracks_when_no_tracklist_is_stored(conn):
    # source: app.py's album_page comment, moved verbatim -- "Never
    # successfully fetched -- show only what's independently known from
    # Symr's own library rather than nothing at all." `fetched` is what the
    # template branches on, so it is asserted alongside the rows.
    album = builders.make_album(
        conn, "al-nofetch", name="No Fetch", tracklist_pulled_at="2026-01-01T00:00:00Z"
    )
    builders.make_track(conn, "t-known", name="Known", album_id=album)
    conn.commit()

    data = entities.album_detail(conn, album)

    assert data["fetched"] is False
    assert data["tracklist_count"] is None
    assert [r["track_id"] for r in data["rows"]] == ["t-known"]


def test_album_detail_requeues_a_cleared_wanted_uri_on_every_call(conn):
    # source: grouping-fixes-backfill-M.md §4.4/§0.5 -- queue_wanted_uris
    # runs on every album-page view, "which is what makes clearing a queue
    # a real undo instead of a trap". Clearing between two calls is the
    # only fixture that separates that from "queued once, on first view".
    album = builders.make_album(
        conn,
        "al-queue",
        name="Queue",
        tracklist_json='[{"id":"t-missing","uri":"spotify:track:t-missing",'
        '"name":"Missing","artists":[],"track_number":1,"disc_number":1}]',
        tracklist_pulled_at="2026-01-01T00:00:00Z",
    )
    conn.commit()

    entities.album_detail(conn, album)
    conn.execute("DELETE FROM wanted_uri")
    conn.commit()
    entities.album_detail(conn, album)

    assert conn.execute(
        "SELECT source FROM wanted_uri WHERE uri = ?", ("spotify:track:t-missing",)
    ).fetchone()["source"] == "album"


# -- artist_detail ------------------------------------------------------


def _artist_credited_twice_on_one_version(conn):
    """One version group holding two tracks that credit `ar-main` in both
    roles: primary on its own album, featured on someone else's."""
    builders.make_album(conn, "al-own", name="Own", artists=["ar-main"])
    builders.make_track(conn, "t-own", name="Own Track", album_id="al-own", artists=["ar-main"])
    builders.make_album(conn, "al-guest", name="Guest", artists=["ar-host"])
    builders.make_track(
        conn, "t-guest", name="Guest Track", album_id="al-guest", artists=["ar-host", "ar-main"]
    )
    builders.make_group(conn, ["t-own", "t-guest"])
    conn.commit()
    return "ar-main"


def test_artist_detail_returns_none_for_an_unknown_artist(conn):
    # source: P3_refactor.md §4.1 -- the missing-row contract behind
    # /artist/<id>'s "Artist not found."
    assert entities.artist_detail(conn, "ar-nonexistent") is None


def test_artist_detail_does_not_repeat_a_primary_version_under_featured(conn):
    # source: app.py's artist_page comment, moved verbatim -- "A version
    # already counted as primary (via some other member track) doesn't also
    # need a featured row -- primary is the more informative badge." The
    # fixture credits the artist in *both* roles on one version group, so
    # dropping the `featured_versions -= primary_versions` line renders the
    # same group twice.
    artist_id = _artist_credited_twice_on_one_version(conn)

    data = entities.artist_detail(conn, artist_id)

    assert len(data["primary_tracks"]) == 1
    assert data["featured_tracks"] == []


def test_artist_detail_counts_versions_off_the_rendered_rows_not_the_credits(conn):
    # source: app.py's artist_page comment, moved verbatim -- "Counted off
    # the rendered rows, not off credit_rows ... Counting anything else
    # lets the header disagree with the list directly under it." Two credit
    # rows collapse to one version group here, so len(credit_rows) is 2 and
    # the count must be 1.
    artist_id = _artist_credited_twice_on_one_version(conn)

    data = entities.artist_detail(conn, artist_id)

    assert conn.execute(
        "SELECT COUNT(*) FROM track_artist_role WHERE artist_id = ?", (artist_id,)
    ).fetchone()[0] == 2
    assert data["version_count"] == 1


def test_artist_detail_ranks_albums_by_score_not_by_name(conn):
    # source: scoring-H.md §11.1 -- every listing ranks by score, with name
    # only as the tiebreak. The higher-scoring album is alphabetically
    # last, so a name-ordered implementation returns the other one first.
    for album_id, name, track_id, score in (
        ("al-zed", "Zebra Album", "t-zed", 90.0),
        ("al-app", "Apple Album", "t-app", 10.0),
    ):
        builders.make_album(conn, album_id, name=name, artists=["ar-ranker"])
        builders.make_track(conn, track_id, album_id=album_id, artists=["ar-ranker"])
        groups = builders.make_group(conn, [track_id])
        builders.make_score(conn, "version", groups["version"], all_time=score)
    conn.commit()

    data = entities.artist_detail(conn, "ar-ranker")

    assert [a["name"] for a in data["albums"]] == ["Zebra Album", "Apple Album"]
    assert data["album_count"] == 2


# -- playlist_detail ----------------------------------------------------


def test_playlist_detail_returns_none_for_an_unknown_playlist(conn):
    # source: P3_refactor.md §4.1 -- the missing-row contract behind
    # /playlist/<id>'s "Playlist not found."
    assert entities.playlist_detail(conn, "p-nonexistent") is None


def test_playlist_detail_selects_every_snapshot_column_and_no_others(conn):
    # source: P3_refactor.md §4.5 -- this query was `SELECT *` until P3
    # named its 15 columns, and the accepted cost of naming them is that
    # "a named list needs updating when a column is added". This is what
    # makes that cost visible instead of silent: compared against
    # PRAGMA table_info, so a migration that adds a column the template
    # might read fails here rather than rendering a Jinja UndefinedError,
    # and a dropped name fails too.
    builders.make_playlist(conn, "p-columns", name="Columns")
    conn.commit()

    data = entities.playlist_detail(conn, "p-columns")

    assert set(data["playlist"].keys()) == {
        "playlist_id", "name", "image_url", "owner", "track_count", "pulled_at",
        "snapshot_id", "last_changed_at", "tracks_pulled_at", "unfollowed_at",
        "description", "last_pull_error", "excluded", "generation_declined",
        "tracks_pulled_snapshot_id",
    }
    assert set(data["playlist"].keys()) == {
        r["name"] for r in conn.execute("PRAGMA table_info(snapshot)")
    }


def test_playlist_detail_keeps_removed_rows_but_totals_only_the_live_ones(conn):
    # source: CLAUDE.md -- `membership` is the append-only per-playlist
    # log, so a removed row stays in the listing, while the totals query
    # and the play stats are both about what the playlist holds *now*.
    # Two tracks of different durations, one removed, so the runtime
    # distinguishes "live only" (200000) from "everything" (500000) and
    # from "the other one" (300000) -- three different numbers.
    #
    # `stats` is asserted for its own reason: totals filters removed rows
    # in its own SQL, so it stays right even if the live_track_ids list
    # feeding play_stats stops filtering. Written without the plays below,
    # this test passed against exactly that mutation -- a code path nothing
    # read (P2-007).
    builders.make_playlist(conn, "p-mixed", name="Mixed")
    builders.make_track(conn, "t-live", duration_ms=200_000)
    builders.make_track(conn, "t-gone", duration_ms=300_000)
    builders.make_membership(conn, playlist_id="p-mixed", track_id="t-live", position=0)
    builders.make_membership(
        conn, playlist_id="p-mixed", track_id="t-gone", position=1,
        removed_at=builders.days_ago(1),
    )
    builders.make_play(conn, track_id="t-live", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-gone", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-gone", ts=builders.days_ago(2))

    data = entities.playlist_detail(conn, "p-mixed")

    assert len(data["rows"]) == 2
    assert data["totals"]["runtime"] == 200_000
    assert data["stats"]["total"] == 1


def test_playlist_detail_returns_the_ordinal_only_for_a_generation_playlist(conn):
    # source: generations-B.md -- `generation` is one row per current-favs
    # playlist, and the ordinal is *stored*. The route decides whether to
    # build the carried/new split off exactly this, so an ordinary playlist
    # must come back None rather than a falsy 0 that would read the same in
    # a truthiness check but differ in `is not None`.
    builders.make_generation(conn, ordinal=4, playlist_id="p-is-gen")
    builders.make_playlist(conn, "p-not-gen", name="Ordinary")
    conn.commit()

    assert entities.playlist_detail(conn, "p-is-gen")["generation"]["ordinal"] == 4
    assert entities.playlist_detail(conn, "p-not-gen")["generation"] is None


# -- search -------------------------------------------------------------


def test_search_returns_one_row_per_version_group(conn):
    # source: entity-pages-K.md -- the songs list is one row per version
    # group, not one per matching track. Two matching tracks in one group
    # must collapse to a single row; a per-track implementation returns 2.
    builders.make_track(conn, "t-dup-a", name="Collapse Me")
    builders.make_track(conn, "t-dup-b", name="Collapse Me Too")
    builders.make_group(conn, ["t-dup-a", "t-dup-b"])
    conn.commit()

    results = entities.search(conn, "Collapse Me")

    assert len(results["songs"]) == 1


def test_search_matches_a_track_by_its_artist_name(conn):
    # source: characterization -- app.py's search_page matched a track on
    # its own name OR an EXISTS over its credited artists, and the query
    # moved verbatim. The track's own name deliberately shares nothing with
    # the query, so only the EXISTS arm can find it.
    builders.make_track(conn, "t-by-artist", name="Nondescript", artists=["ar-findable"])
    conn.execute("UPDATE artist SET name = 'Findable Artist' WHERE artist_id = 'ar-findable'")
    builders.make_group(conn, ["t-by-artist"])
    conn.commit()

    results = entities.search(conn, "Findable")

    assert [s["track_id"] for s in results["songs"]] == ["t-by-artist"]


def test_search_collapses_an_aliased_artist_onto_its_canonical_id(conn):
    # source: CLAUDE.md -- Spotify has multiple ids per artist, and every
    # artist-level listing resolves through `artist_alias`. Both names
    # match the query, so an unresolved implementation returns two rows for
    # what is one artist.
    builders.make_artist(conn, "ar-dupe-one", name="Dupe Artist One")
    builders.make_artist(conn, "ar-dupe-two", name="Dupe Artist Two")
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        ("ar-dupe-two", "ar-dupe-one"),
    )
    conn.commit()

    results = entities.search(conn, "Dupe Artist")

    assert [a["artist_id"] for a in results["artists"]] == ["ar-dupe-one"]


def test_search_ranks_albums_by_score_before_capping_at_fifty(conn):
    # source: scoring-H.md §11.1 -- "All four groups here rank by score
    # before capping at 50, not after: a name-ordered cap returns the
    # alphabetically-first 50 matches rather than the best 50." 51 matches
    # with the only scoring one **last** alphabetically, so a cap-then-rank
    # implementation cuts exactly the album that should come first.
    for i in range(1, 52):
        album = builders.make_album(conn, f"al-cap-{i:02d}", name=f"Cap Album {i:02d}")
        track = builders.make_track(conn, f"t-cap-{i:02d}", album_id=album)
        groups = builders.make_group(conn, [track])
        if i == 51:
            builders.make_score(conn, "version", groups["version"], all_time=95.0)
    conn.commit()

    results = entities.search(conn, "Cap Album")

    assert len(results["albums"]) == 50
    assert results["albums"][0]["name"] == "Cap Album 51"


# -- The rollups each page carries, and why they get their own tests -----
#
# Every assertion below was written after mutating the payload key it
# names: emptying `stats`, `playlists`, `artist_credits`, `version_by_track`
# or `ordinals` passed the entire suite and was caught only by P3's golden
# baseline -- which is deleted at the end of P3 (P3_refactor.md §3.4). A
# key that only a byte-diff observes is the P2-007 shape one layer out: not
# a wrong answer nothing asserts, but a whole return value nothing reads.
# See P3_findings.md for the measurement.


def test_group_detail_rolls_plays_and_playlists_up_over_the_whole_group(conn):
    # source: entity-pages-K.md §8 -- the group page's stats are over its
    # member track set, not one representative. The decoy track outside the
    # group has plays and a membership of its own, so a rollup over
    # everything reports 3 and 2 rather than 2 and 1.
    builders.make_group(conn, ["t-in-a", "t-in-b"])
    builders.make_track(conn, "t-outside")
    builders.make_play(conn, track_id="t-in-a", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-in-b", ts=builders.days_ago(2))
    builders.make_play(conn, track_id="t-outside", ts=builders.days_ago(1))
    builders.make_playlist(conn, "p-in", name="In")
    builders.make_playlist(conn, "p-out", name="Out")
    builders.make_membership(conn, playlist_id="p-in", track_id="t-in-a")
    builders.make_membership(conn, playlist_id="p-out", track_id="t-outside")

    data = entities.group_detail(
        conn, "version", conn.execute(
            "SELECT version_id FROM track_group WHERE track_id = 't-in-a'"
        ).fetchone()[0]
    )

    assert data["stats"]["total"] == 2
    assert [p["playlist_id"] for p in data["playlists"]] == ["p-in"]


def test_track_detail_reports_plays_for_that_track_alone(conn):
    # source: entity-pages-K.md §8 -- play_stats over exactly this track.
    # The second track's two plays are what separate "this track" from
    # "every play in the library".
    builders.make_track(conn, "t-counted")
    builders.make_track(conn, "t-other")
    builders.make_play(conn, track_id="t-counted", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-other", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-other", ts=builders.days_ago(2))

    assert entities.track_detail(conn, "t-counted")["stats"]["total"] == 1


def test_album_detail_rolls_plays_and_playlists_up_over_the_owned_tracks(conn):
    # source: entity-pages-K.md §8 -- the album page's stats are over the
    # tracks Symr owns *on this album*. A play and a membership on a track
    # from a different album are the decoys that make the scope assertable.
    album = builders.make_album(
        conn, "al-rollup", name="Rollup", tracklist_pulled_at="2026-01-01T00:00:00Z"
    )
    builders.make_track(conn, "t-on-album", album_id=album)
    other = builders.make_album(conn, "al-elsewhere", name="Elsewhere")
    builders.make_track(conn, "t-elsewhere", album_id=other)
    builders.make_play(conn, track_id="t-on-album", ts=builders.days_ago(1))
    builders.make_play(conn, track_id="t-elsewhere", ts=builders.days_ago(1))
    builders.make_playlist(conn, "p-has-it", name="Has It")
    builders.make_playlist(conn, "p-has-other", name="Has Other")
    builders.make_membership(conn, playlist_id="p-has-it", track_id="t-on-album")
    builders.make_membership(conn, playlist_id="p-has-other", track_id="t-elsewhere")

    data = entities.album_detail(conn, album)

    assert data["stats"]["total"] == 1
    assert [p["playlist_id"] for p in data["playlists"]] == ["p-has-it"]


def test_album_detail_separates_album_credits_from_per_track_credits(conn):
    # source: entity-pages-K.md -- the album page links its album artists
    # in the header and each track's own artists in the listing, and
    # `artist_credits_for_tracks` exists because track_artists' pre-joined
    # display string carries no id to link to (CLAUDE.md). The featured
    # artist holds a track credit and no album credit, so the two lists
    # must not be the same list.
    album = builders.make_album(
        conn, "al-credits", name="Credits", artists=["ar-album"],
        tracklist_pulled_at="2026-01-01T00:00:00Z",
    )
    builders.make_track(
        conn, "t-credited", album_id=album, artists=["ar-album", "ar-feature"]
    )
    conn.commit()

    data = entities.album_detail(conn, album)

    assert [a["artist_id"] for a in data["artists"]] == ["ar-album"]
    assert [c["artist_id"] for c in data["track_artist_credits"]["t-credited"]] == [
        "ar-album",
        "ar-feature",
    ]


def test_playlist_detail_maps_each_row_to_its_version_group_and_credits(conn):
    # source: entity-pages-K.md -- every track name in the listing links to
    # its version group and every artist name to its artist, which is what
    # these two maps carry. Both were emptiable without failing a single
    # test in the suite.
    builders.make_playlist(conn, "p-linked", name="Linked")
    builders.make_track(conn, "t-linked", artists=["ar-linked"])
    groups = builders.make_group(conn, ["t-linked"])
    builders.make_membership(conn, playlist_id="p-linked", track_id="t-linked")

    data = entities.playlist_detail(conn, "p-linked")

    assert data["version_by_track"] == {"t-linked": groups["version"]}
    assert [c["artist_id"] for c in data["artist_credits"]["t-linked"]] == ["ar-linked"]


def test_artist_detail_reports_the_generations_its_tracks_were_present_in(conn):
    # source: generations-B.md -- the generation strip on every entity page
    # is presence_for_tracks over that page's track set. Two generations
    # exist and the artist is in only the second, so an empty list, a
    # hard-coded [1], and "every generation" are all distinguishable.
    builders.make_album(conn, "al-gen", name="Gen", artists=["ar-gen"])
    builders.make_track(conn, "t-gen", album_id="al-gen", artists=["ar-gen"])
    builders.make_group(conn, ["t-gen"])
    builders.make_generation(conn, ordinal=1, playlist_id="p-gen-1")
    builders.make_generation(conn, ordinal=2, playlist_id="p-gen-2")
    builders.make_membership(conn, playlist_id="p-gen-2", track_id="t-gen")

    assert entities.artist_detail(conn, "ar-gen")["ordinals"] == [2]


def test_artist_detail_keeps_the_first_membership_row_for_each_playlist(conn):
    # source: app.py's artist_page, moved verbatim -- `setdefault`, over
    # rows playlists_for_tracks has already ordered by playlist name then
    # added_at, so one playlist appears once and carries its *earliest*
    # membership. Plain assignment would keep the last, which reads as the
    # artist having entered that playlist later than they did.
    builders.make_album(conn, "al-two", name="Two", artists=["ar-two"])
    builders.make_track(conn, "t-early", album_id="al-two", artists=["ar-two"])
    builders.make_track(conn, "t-late", album_id="al-two", artists=["ar-two"])
    builders.make_group(conn, ["t-early", "t-late"])
    builders.make_playlist(conn, "p-both", name="Both")
    builders.make_membership(
        conn, playlist_id="p-both", track_id="t-early", position=0,
        added_at=builders.days_ago(90),
    )
    builders.make_membership(
        conn, playlist_id="p-both", track_id="t-late", position=1,
        added_at=builders.days_ago(10),
    )

    playlists = entities.artist_detail(conn, "ar-two")["playlists"]

    assert [p["track_id"] for p in playlists] == ["t-early"]


def test_search_returns_matching_playlists(conn):
    # source: entity-pages-K.md -- /search returns four result groups, and
    # the playlists one was droppable without failing a test. The
    # non-matching playlist is what separates "returns matches" from
    # "returns every playlist".
    builders.make_playlist(conn, "p-found", name="Findable Playlist")
    builders.make_playlist(conn, "p-hidden", name="Something Else")
    conn.commit()

    results = entities.search(conn, "Findable")

    assert [p["playlist_id"] for p in results["playlists"]] == ["p-found"]


# =======================================================================
# The keys only the golden baseline was reading (P3-005)
#
# P3-004 measured eleven mutations and closed them. This is the rest of
# that class, found by sweeping **every** key of all seven payloads rather
# than the ones that came to mind: twelve more the permanent suite could
# not see and only the golden compare caught. Golden is deleted at the end
# of P3 (P3_refactor.md §3.4), so each of these was on course to become
# unobserved on the day the refactor finished -- the same trap P3-004
# named, sprung by the keys it did not sample.
#
# Four of them are the `score` on four of the six entity pages: step H's
# entire output, and the number those pages lead with.
# =======================================================================


def _scored_album(conn, suffix, all_time, recent):
    """An artist, album, playlist and track hung off one scored version
    group. Album, artist and playlist scores all aggregate from the version
    tier, so one fixture serves all three of the tests below."""
    builders.make_artist(conn, f"ar-{suffix}", name=f"Artist {suffix}")
    builders.make_album(conn, f"al-{suffix}", name=f"Album {suffix}", artists=[f"ar-{suffix}"])
    builders.make_track(conn, f"t-{suffix}", album_id=f"al-{suffix}", artists=[f"ar-{suffix}"])
    groups = builders.make_group(conn, [f"t-{suffix}"])
    builders.make_score(conn, "version", groups["version"], all_time=all_time, recent=recent)
    builders.make_playlist(conn, f"p-{suffix}", name=f"Playlist {suffix}")
    builders.make_membership(conn, playlist_id=f"p-{suffix}", track_id=f"t-{suffix}", position=0)
    conn.commit()
    return groups


def test_group_detail_reports_the_tier_it_was_asked_for(conn):
    # source: P3_refactor.md §4.1 -- one function serves all four tier
    # routes, and entity_group.html renders `tier` as the page's own label.
    # Asserted at two tiers over one track set, so a hardcoded "version" --
    # the tier every other test in this file happens to use -- fails on the
    # second call rather than sailing through.
    groups = builders.make_group(conn, ["t-tier-echo"])

    assert entities.group_detail(conn, "version", groups["version"])["tier"] == "version"
    assert entities.group_detail(conn, "recording", groups["recording"])["tier"] == "recording"


def test_group_detail_returns_the_scored_representative_not_an_arbitrary_member(conn):
    # source: scoring-H.md §11.3 -- an unpinned representative is the
    # highest-scoring member, then oldest added_at, then lowest track_id.
    # `rep` is what the page is titled after, so getting it wrong renames
    # the page. The fixture disagrees with every fallback the election could
    # have degenerated into (P2-005): the winner is last by track id, last
    # by name and last by insertion order, so only the score picks it.
    builders.make_track(conn, "t-aaa-rep", name="Apple")
    builders.make_track(conn, "t-zzz-rep", name="Zebra")
    groups = builders.make_group(conn, ["t-aaa-rep", "t-zzz-rep"])
    builders.make_score(conn, "track", "t-aaa-rep", all_time=10.0)
    builders.make_score(conn, "track", "t-zzz-rep", all_time=90.0)

    data = entities.group_detail(conn, "version", groups["version"])

    assert data["rep"]["track_id"] == "t-zzz-rep"
    assert data["rep"]["name"] == "Zebra"


def test_group_detail_reports_whether_the_representative_is_pinned(conn):
    # source: canonical.group_tree's "pinned" flag, which
    # entity_group.html:44 renders as a star -- the one visible difference
    # between a representative Finn chose and one the score election
    # picked. Both states are asserted because it is a boolean: a test of
    # one state alone passes against a constant of that value. Song tier,
    # since pin_representative only ever writes there.
    unpinned = builders.make_group(conn, ["t-unpinned"])
    pinned = builders.make_group(conn, ["t-pinned"])
    canonical.pin_representative(conn, "t-pinned")
    conn.commit()

    assert entities.group_detail(conn, "song", unpinned["song"])["pinned"] is False
    assert entities.group_detail(conn, "song", pinned["song"])["pinned"] is True


def test_group_detail_returns_the_ordinals_the_generation_strip_renders(conn):
    # source: generations-B.md -- the strip is one cell per generation ever,
    # filled for the ones this group was present in. `tenure`,
    # `total_generations` and `run_count` are asserted above and are all
    # derived from this same list *inside* the function, so they stay
    # correct even when the payload key itself is emptied: the strip is the
    # only thing that reads `ordinals`. Present in 1 and 3 but not 2, so
    # both "every generation" and "the first N" fail.
    builders.make_track(conn, "t-strip")
    groups = builders.make_group(conn, ["t-strip"])
    for ordinal in (1, 2, 3):
        builders.make_generation(conn, ordinal=ordinal, playlist_id=f"gen-strip-{ordinal}")
    for ordinal in (1, 3):
        builders.make_membership(
            conn, playlist_id=f"gen-strip-{ordinal}", track_id="t-strip", position=0
        )
    conn.commit()

    assert entities.group_detail(conn, "version", groups["version"])["ordinals"] == [1, 3]


def test_group_detail_returns_the_nested_subtree_not_just_the_flat_members(conn):
    # source: canonical.group_tree -- entity_group.html's "Subtree" section
    # is built from `tree`, and nothing else in the payload carries the
    # nesting (`member_tracks` and `tracks_by_id` are both flat). Two tracks
    # sharing a version but split across two recordings, so an
    # implementation that returned one node per track -- or one node full
    # stop -- fails on the count.
    first = builders.make_group(conn, ["t-sub-one"])
    builders.make_group(conn, ["t-sub-two"], song=first["song"], version=first["version"])

    tree = entities.group_detail(conn, "version", first["version"])["tree"]

    assert tree["version_id"] == first["version"]
    assert sorted(tree["track_ids"]) == ["t-sub-one", "t-sub-two"]
    assert len(tree["recordings"]) == 2


def test_track_detail_reports_both_horizons_of_the_tracks_score(conn):
    # source: scoring-H.md -- every score is two numbers on one scale, and
    # entity_track.html leads with them. `recent` is asserted separately
    # and differs from `all_time`, which is the P2-007 shape exactly: the
    # entire `recent` column was once satisfied by writing `all_time` into
    # it, because nothing ever compared the two.
    builders.make_track(conn, "t-scored")
    builders.make_score(conn, "track", "t-scored", all_time=77.0, recent=33.0)

    assert entities.track_detail(conn, "t-scored")["score"] == {
        "all_time": 77.0,
        "recent": 33.0,
    }


def test_album_detail_reports_both_horizons_of_the_albums_score(conn):
    # source: scoring-H.md -- album is never materialized; it aggregates at
    # query time through combine(). Two albums whose horizons are *inverted*
    # relative to each other, so the pair fails against a constant, against
    # None, and against `recent` being a copy of `all_time`. The aggregation
    # itself is scoring.py's own tests' job -- what is asserted here is that
    # the payload carries it.
    _scored_album(conn, "hi", all_time=90.0, recent=10.0)
    _scored_album(conn, "lo", all_time=20.0, recent=80.0)

    assert entities.album_detail(conn, "al-hi")["score"] == {"all_time": 90.0, "recent": 10.0}
    assert entities.album_detail(conn, "al-lo")["score"] == {"all_time": 20.0, "recent": 80.0}


def test_artist_detail_reports_both_horizons_of_the_artists_score(conn):
    # source: scoring-H.md -- as above, for the artist page's headline
    # number, through artist_group_score rather than album_scores.
    _scored_album(conn, "hi", all_time=90.0, recent=10.0)
    _scored_album(conn, "lo", all_time=20.0, recent=80.0)

    assert entities.artist_detail(conn, "ar-hi")["score"] == {"all_time": 90.0, "recent": 10.0}
    assert entities.artist_detail(conn, "ar-lo")["score"] == {"all_time": 20.0, "recent": 80.0}


def test_playlist_detail_reports_both_horizons_of_the_playlists_score(conn):
    # source: scoring-H.md -- as above, for the playlist page, through
    # playlist_scores. The default when a playlist has no score at all is a
    # literal {"all_time": 0.0, "recent": 0.0}, so the third playlist here
    # is what separates "carries the score" from "carries the default".
    _scored_album(conn, "hi", all_time=90.0, recent=10.0)
    builders.make_playlist(conn, "p-unscored", name="Unscored")

    assert entities.playlist_detail(conn, "p-hi")["score"] == {
        "all_time": 90.0,
        "recent": 10.0,
    }
    assert entities.playlist_detail(conn, "p-unscored")["score"] == {
        "all_time": 0.0,
        "recent": 0.0,
    }


def test_artist_detail_lists_the_ids_merged_into_this_artist(conn):
    # source: artist-identity aliasing (CLAUDE.md) -- entity_artist.html
    # renders "Merged ids: ..." so a duplicate Spotify id is visible on the
    # page it was merged into. The fixture holds an alias of a *different*
    # canonical artist, so returning every alias row fails, and the two real
    # ones are inserted in reverse id order, so the ORDER BY is load-bearing
    # rather than incidental.
    for artist_id in ("ar-canon", "ar-other", "ar-alias-b", "ar-alias-a"):
        builders.make_artist(conn, artist_id, name=artist_id)
    for alias, canonical_id in (
        ("ar-alias-b", "ar-canon"),
        ("ar-alias-a", "ar-canon"),
        ("ar-other", "ar-alias-a"),
    ):
        conn.execute(
            "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
            (alias, canonical_id),
        )
    conn.commit()

    assert entities.artist_detail(conn, "ar-canon")["merged_ids"] == [
        "ar-alias-a",
        "ar-alias-b",
    ]
