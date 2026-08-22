"""The permanent route sweep (P2_tests.md §4.6): every one of the 69 routes
returns non-5xx, plus semantic assertions on the pages where "returned 200"
would otherwise be the whole test.

`P2_tests.md` §1's warning for this session, verbatim: "the cheapest
possible non-observation is 'the page returned 200'. A route test that
never asserts what is on the page is the same defect as the unread `recent`
column, wearing a different hat." So most pages here assert their own
entity's name (or a related one) actually renders, not just that the
response came back.

Job-starting POSTs stub `jobs.try_start` to record the call and return True
without spawning a thread -- this sweep is about routes, not job bodies
(sessions 1 and 2's subject), and running a job body inline here would make
an unrelated job crash present as a route failure.
"""

import pytest

import builders
import canonical
import jobs
import routes_catalog
import scoring


@pytest.fixture
def corpus(conn):
    """The ~20-track / few-playlist shape P2_tests.md §4.2 describes --
    enough for every route in the catalog to resolve a real id and render
    something with real content, not an empty state.
    """
    artist = builders.make_artist(conn, "ar-corpus", name="Corpus Artist")
    album = builders.make_album(conn, "al-corpus", name="Corpus Album", artists=["ar-corpus"])
    t1 = builders.make_track(conn, "t-corpus-1", name="Corpus Track One", album_id=album, artists=["ar-corpus"])
    t2 = builders.make_track(conn, "t-corpus-2", name="Corpus Track Two", album_id=album, artists=["ar-corpus"])

    groups = builders.make_group(conn, [t1, t2])

    playlist = builders.make_playlist(conn, "p-corpus", name="Corpus Playlist")
    builders.make_membership(conn, playlist_id=playlist, track_id=t1, added_at=builders.days_ago(10))
    builders.make_membership(conn, playlist_id=playlist, track_id=t2, added_at=builders.days_ago(5))

    gen_playlist = builders.make_playlist(conn, "p-corpus-gen", name="v1.0.0")
    builders.make_generation(conn, ordinal=1, playlist_id=gen_playlist)
    builders.make_membership(conn, playlist_id=gen_playlist, track_id=t1, added_at=builders.days_ago(10))

    builders.make_play(conn, track_id=t1, ts=builders.days_ago(1))
    builders.make_play(conn, track_id=t2, ts=builders.days_ago(2))

    card = builders.make_card(conn, x=10, y=10, display_name="Corpus Card")
    label = builders.make_label(conn, x=0, y=0, text="Corpus Label")

    canonical.ensure_track_groups(conn)
    scoring.recompute(conn)
    conn.commit()

    return {
        "artist": artist,
        "album": album,
        "tracks": [t1, t2],
        "groups": groups,
        "playlist": playlist,
        "gen_playlist": gen_playlist,
        "card": card,
        "label": label,
    }


@pytest.fixture
def stub_jobs(monkeypatch):
    """jobs.try_start records the call and always succeeds -- no thread, no
    real job body. The sweep asserts routes respond; sessions 1-2 assert
    what the jobs actually do.
    """
    calls = []

    def _fake_try_start(name, target, *args):
        calls.append(name)
        return True

    monkeypatch.setattr(jobs, "try_start", _fake_try_start)
    return calls


# -- Catalog completeness --------------------------------------------------


def test_catalog_covers_every_registered_route(app):
    # source: P2_tests.md §4.6 -- "every one of the 69 routes returns
    # non-5xx." The catalog has to actually cover all of them, in both
    # directions, or a future route silently escapes the sweep.
    registered = routes_catalog.app_rules(app)
    catalog = routes_catalog.catalog_rules()

    assert registered - catalog == set(), "routes registered but not swept"
    assert catalog - registered == set(), "catalog cases naming a dead route"


def test_every_case_slug_is_unique():
    # source: Case.slug's own contract -- "unique across the catalog". The
    # slug is a snapshot filename, so a collision means one capture
    # overwrites another and compare() silently reports no diff for a route
    # it never compared. Two rules already share an endpoint+method
    # (roundtrip start/reconcile), which is what `variant` is for (P2-008).
    slugs = [case.slug for case in routes_catalog.CASES]

    assert len(slugs) == len(set(slugs)), "duplicate case slug(s): " + str(
        sorted({s for s in slugs if slugs.count(s) > 1})
    )


# -- The sweep --------------------------------------------------------------


def test_every_route_returns_non_5xx(client, corpus, conn, stub_jobs, fake_spotify):
    # source: P2_tests.md §4.6 -- the permanent layer: "every one of the 69
    # routes returns non-5xx".
    for case in routes_catalog.cases_for(conn):
        resp = routes_catalog.issue(client, case)
        assert resp.status_code < 500, f"{case.slug} ({case.method} {case.path}) -> {resp.status_code}"


# -- Semantic assertions: what P2_tests.md §1 says a bare 200 doesn't prove


def test_track_page_shows_track_and_artist_names(client, corpus):
    # source: P2_tests.md §1 -- a route test that never asserts what is *on*
    # the page is the same defect as an unread column.
    resp = client.get(f"/track/{corpus['tracks'][0]}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Track One" in body
    assert "Corpus Artist" in body


def test_album_page_shows_album_and_track_names(client, corpus):
    # source: P2_tests.md §1 -- a route test that never asserts what is *on*
    # the page is the same defect as an unread column.
    resp = client.get(f"/album/{corpus['album']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Album" in body
    assert "Corpus Track One" in body


def test_artist_page_shows_artist_name(client, corpus):
    # source: P2_tests.md §1 -- as above; "returned 200" is not an observation.
    resp = client.get(f"/artist/{corpus['artist']}")
    assert resp.status_code == 200
    assert "Corpus Artist" in resp.get_data(as_text=True)


def test_playlist_page_shows_playlist_name(client, corpus):
    # source: P2_tests.md §1 -- as above; "returned 200" is not an observation.
    resp = client.get(f"/playlist/{corpus['playlist']}")
    assert resp.status_code == 200
    assert "Corpus Playlist" in resp.get_data(as_text=True)


def test_version_page_shows_a_member_track_name(client, corpus):
    # source: P2_tests.md §1 -- as above, and entity-pages-K.md: a group page
    # renders its members.
    resp = client.get(f"/version/{corpus['groups']['version']}")
    assert resp.status_code == 200
    assert "Corpus Track One" in resp.get_data(as_text=True)


def test_search_finds_a_matching_track_and_not_a_non_matching_one(client, corpus, conn):
    # The negative half is the discriminating one -- a search page that just
    # dumps every track would pass a positive-only assertion too.
    # source: P2_tests.md §4.6 -- "plus a handful of semantic assertions".
    builders.make_track(conn, "t-decoy", name="Totally Unrelated Song")
    conn.commit()

    resp = client.get("/search?q=Corpus")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Track One" in body
    assert "Totally Unrelated Song" not in body


def test_dev_generations_renders_generation_names(client, corpus):
    # source: generations-B.md '/dev/generations -- the generation list'.
    resp = client.get("/dev/generations")
    assert resp.status_code == 200
    assert "v1.0.0" in resp.get_data(as_text=True)


def test_large_counts_render_with_thousands_separators(client, conn):
    # source: CLAUDE.md's frontend rule -- "Any count that can exceed 999
    # renders with thousands separators, both halves of the path." Build
    # more than 999 plays so /dev/import's coverage count crosses the
    # threshold; assert the comma form appears and the bare digit form does
    # not (a bug that formats one but not the other is the failure mode
    # named in CLAUDE.md).
    for i in range(1200):
        builders.make_play(conn, track_id="t-shared", ts=builders.days_ago(1), row_hash=f"hash-{i}")
    conn.commit()

    resp = client.get("/dev/import")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1,200" in body
    assert "1200" not in body.replace("1,200", "")


# -- P1-016's remaining two: route/template behaviour ------------------


def test_fully_backfilled_album_renders_no_first_n_note(client, conn, fake_spotify):
    # source: entity-pages-K.md §5.2 -- "Now: render whatever tracklist_json
    # holds, and only when that count is less than total_tracks note
    # 'first N of total'." A fully-backfilled album (stored count ==
    # total_tracks) must render no such note.
    import entities

    album = builders.make_album(conn, "al-full", name="Full Album", total_tracks=2)
    t1 = builders.make_track(conn, "tf1", album_id=album)
    t2 = builders.make_track(conn, "tf2", album_id=album)
    conn.commit()
    fake_spotify.add_album(
        {
            "id": album,
            "tracks": {
                "items": [
                    {"id": t1, "uri": "spotify:track:tf1", "name": "One", "artists": [], "track_number": 1, "disc_number": 1},
                    {"id": t2, "uri": "spotify:track:tf2", "name": "Two", "artists": [], "track_number": 2, "disc_number": 1},
                ]
            },
        }
    )
    entities.fetch_album_tracklist(conn, album)
    conn.commit()

    resp = client.get(f"/album/{album}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The note's exact wording, from entity_album.html: "First {n} of
    # {total};". `A or B` where A implies B collapses to B, so state B.
    assert "first 2 of 2" not in body.lower()


def test_partially_fetched_album_renders_the_first_n_note(client, conn, fake_spotify):
    # Positive half of the same clause -- an album whose fetch only got the
    # first page of a larger total must show the note.
    # source: entity-pages-K.md, via P1-016 -- the "first N of total" note, and
    # entities.fetch_album_tracklist never pages past the first 50 items.
    import entities

    album = builders.make_album(conn, "al-partial", name="Partial Album", total_tracks=60)
    t1 = builders.make_track(conn, "tp1", album_id=album)
    conn.commit()
    fake_spotify.add_album(
        {
            "id": album,
            "tracks": {
                "items": [
                    {"id": t1, "uri": "spotify:track:tp1", "name": "One", "artists": [], "track_number": 1, "disc_number": 1},
                ]
            },
        }
    )
    entities.fetch_album_tracklist(conn, album)
    conn.commit()

    resp = client.get(f"/album/{album}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "1 of 60" in body


def test_album_credit_under_alias_and_canonical_id_renders_once(client, conn, fake_spotify):
    # source: entity-pages-K.md -- "a credit under both an alias id and its
    # already-canonical id would render that artist's name twice" (P1-016,
    # fixed with a GROUP BY). Assert the count, not just presence.
    canonical_artist = builders.make_artist(conn, "ar-canon", name="Duplicated Artist")
    alias_artist = builders.make_artist(conn, "ar-alias", name="Duplicated Artist (alias)")
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        (alias_artist, canonical_artist),
    )
    album = builders.make_album(conn, "al-dup", name="Dup Album", artists=[])
    conn.execute(
        "INSERT INTO album_artist (album_id, artist_id, position) VALUES (?, ?, 0)",
        (album, canonical_artist),
    )
    conn.execute(
        "INSERT INTO album_artist (album_id, artist_id, position) VALUES (?, ?, 1)",
        (album, alias_artist),
    )
    conn.commit()

    resp = client.get(f"/album/{album}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert body.count("Duplicated Artist</a>") == 1


def test_edit_link_resolves_through_the_deep_link_to_the_viewer(client, corpus):
    # source: entity-pages-K.md -- the Edit link on a group page resolves to
    # /dev/canonical/group/<id>, which redirects into the viewer
    # (dev_canonical) deep-linked with ?expand= and a #group- fragment.
    version_id = corpus["groups"]["version"]

    resp = client.get(f"/dev/canonical/group/{version_id}", follow_redirects=False)

    assert resp.status_code == 302
    assert "expand=" in resp.headers["Location"]
    assert f"#group-{version_id}" in resp.headers["Location"]

    followed = client.get(resp.headers["Location"])
    assert followed.status_code == 200


# -- The one-request-per-page-load ceiling, at the route level ----------
#
# entity-pages-K.md's hardest constraint is not that the fetch stores what
# it fetched -- test_entities.py has that -- but that the page spends AT
# MOST one Spotify request, on first view, ever. That ceiling lives in
# app.py's `if album["tracklist_pulled_at"] is None:` guard, and nothing
# read it until session 4's Verify: deleting the guard, so every single
# page view spends a request, passed all 708 tests (P2-008). The stamp and
# the guard that reads it are two halves of one rule and neither is worth
# anything alone, so these drive the real route twice and count the calls.


def test_album_page_fetches_the_tracklist_only_on_the_first_view(client, conn, fake_spotify):
    # source: entity-pages-K.md §5.3 -- "On first view (tracklist_pulled_at
    # IS NULL only -- never re-fetched automatically)."
    album = builders.make_album(conn, "al-guarded", name="Guarded Album", total_tracks=1)
    conn.commit()
    fake_spotify.add_album(
        {
            "id": album,
            "tracks": {
                "items": [
                    {"id": "tg1", "uri": "spotify:track:tg1", "name": "One", "artists": [], "track_number": 1, "disc_number": 1},
                ]
            },
        }
    )

    client.get(f"/album/{album}")
    client.get(f"/album/{album}")

    assert len([c for c in fake_spotify.calls if c[0] == "album"]) == 1


def test_a_failed_album_fetch_does_not_retry_on_the_next_page_view(client, conn, fake_spotify):
    # source: entity-pages-K.md §5.3 (P1-016) -- "a failed attempt now also
    # stamps tracklist_pulled_at ... it simply stops re-spending a request
    # on every later visit." The end-to-end half of P2_tests.md §5's floor
    # item; test_entities.py asserts the stamp, this asserts the ceiling it
    # exists to enforce.
    album = builders.make_album(conn, "al-failing", name="Failing Album")
    conn.commit()
    fake_spotify.fail("album", Exception("429"))

    client.get(f"/album/{album}")
    client.get(f"/album/{album}")

    assert len([c for c in fake_spotify.calls if c[0] == "album"]) == 1


def test_artist_page_fetches_the_image_only_on_the_first_view(client, conn, fake_spotify):
    # source: entity-pages-K.md §7.1 -- "on first view (detail_pulled_at IS
    # NULL only)", the same rule and the same guard shape as the album page.
    artist = builders.make_artist(conn, "ar-guarded", name="Guarded Artist")
    conn.commit()
    fake_spotify.add_artist({"id": artist, "images": [{"url": "https://img/640", "width": 640}]})

    client.get(f"/artist/{artist}")
    client.get(f"/artist/{artist}")

    assert len([c for c in fake_spotify.calls if c[0] == "artist"]) == 1


def test_album_page_requeues_a_cleared_wanted_uri_on_every_view(client, conn, fake_spotify):
    # source: grouping-fixes-backfill-M.md §4.4/§0.5 -- queue_wanted_uris
    # runs on EVERY album-page view, not just the first, "which is what
    # makes clearing a queue a real undo instead of a trap, since a cleared
    # uri comes back the moment the page is revisited." The route-level
    # wiring, which nothing read: moving the call under the first-view
    # guard -- or deleting it outright -- passed the whole suite (P2-008).
    album = builders.make_album(conn, "al-requeue", name="Requeue Album")
    conn.commit()
    fake_spotify.add_album(
        {
            "id": album,
            "tracks": {
                "items": [
                    {"id": "t-unowned", "uri": "spotify:track:t-unowned", "name": "U", "artists": [], "track_number": 1, "disc_number": 1},
                ]
            },
        }
    )

    client.get(f"/album/{album}")
    assert conn.execute("SELECT COUNT(*) FROM wanted_uri").fetchone()[0] == 1

    conn.execute("DELETE FROM wanted_uri")
    conn.commit()

    client.get(f"/album/{album}")

    assert conn.execute("SELECT COUNT(*) FROM wanted_uri").fetchone()[0] == 1


def test_playlist_generation_view_renders_the_generation_split(client, corpus):
    # source: CLAUDE.md's route map -- "/playlist/<id> (?generation=1
    # renders the generation view, ?tier= toggles it)". A whole alternate
    # render path on an already-swept route: the catalog issues the bare
    # path, so nothing exercised this branch at all (P2-008). Asserts the
    # view's own content, not just a 200 -- P2_tests.md §1's warning.
    resp = client.get(f"/playlist/{corpus['gen_playlist']}?generation=1")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Track One" in body

    tiered = client.get(f"/playlist/{corpus['gen_playlist']}?generation=1&tier=song")
    assert tiered.status_code == 200


# -- Semantic assertions for the query-string variants ----------------------
#
# The catalog's variant cases (routes_catalog.py) prove those branches respond;
# these prove they respond with the right thing. Same split as the sweep above,
# and the same reason: a filter that ignored its own argument would return 200
# on every one of the catalog cases.


def test_the_canonical_groups_filter_actually_filters(client, corpus, conn):
    # source: P2_tests.md §4.6 -- the permanent layer is non-5xx "plus a
    # handful of semantic assertions". The negative half is the discriminating
    # one: `?q=` is a LIKE over the listing, and a page that ignored the
    # parameter would still contain the matching track.
    decoy = builders.make_track(conn, "t-filter-decoy", name="Totally Unrelated Song")
    builders.make_group(conn, [decoy, builders.make_track(conn, "t-filter-decoy-2",
                                                          name="Totally Unrelated Song (Live)")])
    canonical.ensure_track_groups(conn)
    conn.commit()

    body = client.get("/dev/canonical?q=Corpus").get_data(as_text=True)

    assert "Corpus Track One" in body
    assert "Totally Unrelated Song" not in body


def test_the_ad_hoc_queue_serves_exactly_the_requested_tracks(client, corpus):
    # source: app.py's `?tracks=` arm -- "tracks= needs at least 2 track ids",
    # then one ad-hoc group over exactly those. Asserting the *ids* rather
    # than the item count is what separates it from the main queue, which
    # would also return one item here.
    t1, t2 = corpus["tracks"]

    resp = client.get(f"/api/canonical/queue?tracks={t1},{t2}")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["queue"] == "ad-hoc"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["track_ids"] == sorted([t1, t2])


def test_the_ad_hoc_queue_refuses_a_single_track(client, corpus):
    # source: app.py -- a one-track ad-hoc group is a 400, not an empty queue.
    # Without this, the arm above could pass while `len(track_ids) < 2` was
    # never enforced.
    resp = client.get(f"/api/canonical/queue?tracks={corpus['tracks'][0]}")

    assert resp.status_code == 400
    assert set(resp.get_json()) == {"error", "detail"}


def test_the_ad_hoc_queue_refuses_an_unknown_track_id(client, corpus):
    # source: app.py -- "unknown track ids: ...". A queue built over an id with
    # no `track` row would render a group with a blank member.
    t1 = corpus["tracks"][0]

    resp = client.get(f"/api/canonical/queue?tracks={t1},no-such-track")

    assert resp.status_code == 400


def test_the_pending_queue_is_a_different_queue_from_the_main_one(client, corpus):
    # source: canonical_detect.pending_tier_items -- `?queue=pending` serves
    # the cross-artist assignments owing a tier pass. Naming the queue back is
    # what distinguishes it; the corpus has none pending, so the item list is
    # empty and only the label discriminates.
    resp = client.get("/api/canonical/queue?queue=pending")

    assert resp.status_code == 200
    assert resp.get_json()["queue"] == "pending"


def test_the_tenure_page_rejects_an_unwhitelisted_sort_instead_of_using_it(client, corpus):
    # source: app.py -- `sort` is looked up in _TENURE_SORT_KEYS and falls back
    # to "tenure", never interpolated into the ORDER BY. The assertion is that
    # a SQL fragment as `sort` renders the page rather than reaching SQLite.
    resp = client.get("/dev/generations/tenure?sort=;drop table generation")

    assert resp.status_code == 200
    # The table is still there afterwards, which a successful injection would
    # have changed.
    assert client.get("/dev/generations").status_code == 200


# -- The entity pages' 404 branches -----------------------------------------
#
# Each is three lines and individually dull; together they are the difference
# between "unknown id" and "500". The wrong-tier case is the one with real
# content: the four tier routes are four decorators on one view function, so
# nothing but this check stops /song/<id> rendering a release group.


def test_an_unknown_id_is_a_404_on_every_entity_page(client, corpus):
    # source: app.py's four `abort(404, ...)` guards. A missing row must not
    # reach the render and raise.
    for path in ("/track/nope", "/album/nope", "/artist/nope", "/playlist/nope"):
        assert client.get(path).status_code == 404, path


def test_a_group_id_of_the_wrong_tier_is_a_404_not_someone_elses_page(client, corpus):
    """The four tier routes are four decorators on one view function, so this
    is what keeps /song/<id> from rendering a release group.

    **The status code alone cannot discriminate here, and the message is why
    this test asserts one** (P2-009). `canonical_group.id` is globally unique
    across tiers, so a release-tier id can never appear in
    `track_group.song_id` -- deleting the `row["tier"] != tier` clause still
    404s, one guard later, on "Group has no members". The description is the
    only observable difference, so asserting the status code would be a test
    that cannot fail.
    """
    # source: app.py -- `if row is None or row["tier"] != tier: abort(404,
    # description="No such group.")`, and the four-decorators-one-view
    # structure in CLAUDE.md's codebase map.
    release_id = corpus["groups"]["release"]

    assert client.get(f"/release/{release_id}").status_code == 200

    resp = client.get(f"/song/{release_id}")
    assert resp.status_code == 404
    assert "No such group." in resp.get_data(as_text=True)
    assert "Group has no members." not in resp.get_data(as_text=True)


def test_an_unknown_group_id_is_a_404(client, corpus):
    # source: app.py -- the `row is None` half of the same guard.
    assert client.get("/song/999999").status_code == 404


def test_an_aliased_artist_id_redirects_to_the_canonical_artist(client, corpus, conn):
    """Artist identity is many-ids-to-one (`artist_alias`), so a link built
    from a track credit can name an id that has no page of its own."""
    # source: app.py's artist_page alias branch, and the artist-identity rule
    # that every artist-level page resolves through `artist_alias`.
    builders.make_artist(conn, "ar-dupe", name="Corpus Artist (dupe)")
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES (?, ?)",
        ("ar-dupe", corpus["artist"]),
    )
    conn.commit()

    resp = client.get("/artist/ar-dupe")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/artist/{corpus['artist']}")


# -- Job-start routes reject a second start ---------------------------------


def test_every_job_start_route_reports_the_slot_is_taken(client, corpus, conn, monkeypatch):
    """One job slot, four jobs, seven start routes. Each must refuse cleanly
    with a 409 rather than starting a second job or 500ing.

    `jobs.try_start` is stubbed to *fail*, which is what a claimed slot looks
    like to a route -- the slot mechanics themselves are session 1's.
    """
    # source: async-recompute-N.md's premise and jobs.py -- a single module
    # lock guards a single `_active` job name, so a second start cannot
    # succeed; app.py answers `api_error("already_running", 409)`.
    monkeypatch.setattr(jobs, "try_start", lambda *a, **k: False)
    # /api/history/reimport checks it has something to re-read *before* the
    # slot, so without a usable upload row it 400s and never reaches the 409.
    conn.execute(
        "INSERT INTO play_import (kind, folder, original_name, files_parsed) "
        "VALUES ('upload', '/tmp/nonexistent-export', 'export.zip', 1)"
    )
    conn.commit()

    starts = [
        ("/api/snapshot/pull", None),
        ("/api/snapshot/refresh", None),
        ("/api/snapshot/backfill", None),
        ("/api/roundtrip/start", None),
        ("/api/roundtrip/reconcile", None),
        ("/api/backfill/start", {"generations": 2}),
        ("/api/history/reimport", None),
    ]
    for path, body in starts:
        resp = client.post(path, json=body) if body else client.post(path)
        assert resp.status_code == 409, f"{path} -> {resp.status_code}"
        assert set(resp.get_json()) == {"error", "detail"}, path
        assert resp.get_json()["error"] == "already_running", path
