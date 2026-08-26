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
import db
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
# MOST one Spotify request, on first view, ever. That ceiling lives in the
# `if album["tracklist_pulled_at"] is None:` guard, and nothing read it
# until session 4's Verify: deleting the guard, so every single page view
# spends a request, passed all 708 tests (P2-008). The stamp and the guard
# that reads it are two halves of one rule and neither is worth anything
# alone, so these drive the real route twice and count the calls.
#
# The guard moved from app.py's album_page into entities.album_detail in
# P3 session 2, and these tests did not change: they go through the route,
# which is the only thing that can show the route still reaches it. That is
# the half P2-008 was about, and it is why P3 was forbidden from touching
# them (P3_refactor.md §6).


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
    # uri comes back the moment the page is revisited." The wiring, which
    # nothing read: moving the call under the first-view guard -- or
    # deleting it outright -- passed the whole suite (P2-008). It sits in
    # entities.album_detail since P3 session 2; this still drives the route.
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


def test_playlist_generation_view_renders_the_generation_split(client, corpus, conn):
    """The generation view is a whole alternate render path on an
    already-swept route (P2-008).

    **The assertions are the carried/new headings and their counts, because
    nothing else here discriminates.** This test used to assert a member
    track's name, which the *ordinary* playlist render also contains -- so
    `if False:` on the `?generation=1` branch passed it. The headings exist
    only in the generation view, and the counts are what `?tier=` changes:
    the two tracks below are two versions of one song, so version tier sees
    two groups and song tier sees one.
    """
    # source: CLAUDE.md's route map -- "/playlist/<id> (?generation=1 renders
    # the generation view, ?tier= toggles it)"; generations-B.md's
    # carried/new split and its rollup tier.
    tb = builders.make_track(conn, "t-gen-second", name="Second Version")
    builders.make_group(conn, [tb], song=corpus["groups"]["song"])
    builders.make_membership(
        conn,
        playlist_id=corpus["gen_playlist"],
        track_id=tb,
        added_at=builders.days_ago(9),
    )
    canonical.ensure_track_groups(conn)
    conn.commit()

    plain = client.get(f"/playlist/{corpus['gen_playlist']}").get_data(as_text=True)
    assert "New in this generation" not in plain

    resp = client.get(f"/playlist/{corpus['gen_playlist']}?generation=1")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Generation 1 is the first, so nothing can have been carried into it.
    assert "Carried forward (0)" in body
    assert "New in this generation (2)" in body

    tiered = client.get(f"/playlist/{corpus['gen_playlist']}?generation=1&tier=song")

    assert tiered.status_code == 200
    assert "New in this generation (1)" in tiered.get_data(as_text=True)


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


def test_the_tenure_page_clamps_page_zero_and_a_missing_page_to_one(client, conn, monkeypatch):
    # source: S_sweep.md §3.4 D -- app.py's
    # `request.args.get("page", 1, type=int) or 1`, which carries two separate
    # literals and needs both halves exercised. The trailing `or 1` only has
    # an effect when `?page=` resolves to the falsy int 0; the `1` inside
    # `get(...)` only has an effect when `page` is absent from the query
    # string entirely. P2-010's rule in practice: a query-string variant that
    # merely *responds* proves nothing, so this asserts the page the route
    # actually computed.
    import entities

    # 1 row per page, so two groups are enough to need two pages -- rather
    # than the 101 the real _TENURE_PAGE_SIZE would demand.
    monkeypatch.setattr(entities, "_TENURE_PAGE_SIZE", 1)
    builders.make_generation(conn, ordinal=1, playlist_id="gen-1")
    g1 = builders.make_group(conn, ["t-a"])
    g2 = builders.make_group(conn, ["t-b"])
    builders.make_membership(conn, playlist_id="gen-1", track_id="t-a")
    builders.make_membership(conn, playlist_id="gen-1", track_id="t-b")
    # sanity: both groups tie on tenure length (one generation each), so the
    # group_id tiebreak decides page order -- and g1 must sort first for the
    # assertions below to mean what they say.
    assert g1["version"] < g2["version"]

    assert "Page 1 of 2" in client.get("/dev/generations/tenure?page=0").get_data(as_text=True)
    assert "Page 1 of 2" in client.get("/dev/generations/tenure").get_data(as_text=True)


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


# -- The route half of the seams P3 session 2 created (P3-005) -------------
#
# Extraction moved these views' work into entities.py, and the unit tests
# beside it pin what the extracted function *returns*. These three pin what
# the route does with it -- the half that cannot enforce the rule alone.
# All three were verified by deleting the line each names: every one passed
# the full suite and the golden compare both.


def test_a_group_with_no_members_is_a_404_and_not_a_500(client, corpus, conn):
    """The second of group_page's two 404s, and the half that stayed in the
    route when P3 session 2 split this guard across a module boundary.

    `entities.group_detail` signals it by returning `{"track_count": 0}` and
    nothing else; test_entities.py pins that shape. Deleting the route's
    `if not data["track_count"]` renders the template with a payload missing
    every other key -- a 500, not a 404 -- and passed everything.
    """
    # source: app.py's group_page -- `abort(404, description="Group has no
    # members.")`, plus P3_refactor.md's Tests section: "where a rule is
    # split across a function and its call site, the test has to cross the
    # seam." The negative assertion separates this guard from the tier
    # guard above it, which 404s with a different description (P2-009).
    empty_id = conn.execute(
        "INSERT INTO canonical_group (tier, representative_track_id) VALUES ('version', NULL)"
    ).lastrowid
    conn.commit()

    resp = client.get(f"/version/{empty_id}")

    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "Group has no members." in body
    assert "No such group." not in body


def test_the_album_page_allocates_groups_so_its_tracks_link_to_their_versions(client, conn):
    """`canonical.ensure_track_groups(conn); conn.commit()` stays in the
    route (P3_refactor.md §2 -- canonical.py never commits), which makes it
    route wiring that only a route test can see.

    Deleting it passes the whole suite *and* the golden compare, because a
    library whose tracks all have groups renders identically either way.
    So the fixture is the un-golden-able case: a track with no track_group
    row at all, whose version link exists only if the page allocated one.
    """
    # source: app.py's album_page, and CLAUDE.md's note that
    # ensure_track_groups "writes on a plain GET". tracklist_pulled_at is
    # pre-stamped so this spends no Spotify request -- the fetch ceiling is
    # a different rule, tested above.
    builders.make_album(
        conn, "al-ungrouped", name="Ungrouped Album", tracklist_pulled_at=builders.days_ago(1)
    )
    builders.make_track(conn, "t-ungrouped", name="Ungrouped Track", album_id="al-ungrouped")
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM track_group WHERE track_id = 't-ungrouped'"
    ).fetchone()[0] == 0

    resp = client.get("/album/al-ungrouped")

    assert resp.status_code == 200
    row = conn.execute(
        "SELECT version_id FROM track_group WHERE track_id = ?", ("t-ungrouped",)
    ).fetchone()
    assert row is not None, "the page did not allocate a group for its own track"
    assert f"/version/{row['version_id']}" in resp.get_data(as_text=True)


def test_an_empty_search_allocates_nothing_but_a_real_one_does(client, corpus, monkeypatch):
    """A plain GET that writes is exactly what P3's golden harness had to
    neutralise, and `search_page`'s `if q:` is what keeps the bare page out
    of that set.

    Nothing read it. Deleting the guard passes the full suite and the
    golden compare, because `routes_catalog` carries `/search?q=a` but not
    the bare path, and the url_map completeness check keys on
    `(endpoint, method)` -- a query string is neither (P2 session 5). The
    positive half is what makes this discriminating: a spy that simply
    never fires would pass against a route that had stopped calling it.
    """
    # source: app.py's search_page -- "ensure_track_groups only when there
    # is something to search for, exactly as before: an empty /search
    # writes nothing."
    calls = []
    monkeypatch.setattr(canonical, "ensure_track_groups", lambda conn: calls.append(1))

    assert client.get("/search").status_code == 200
    assert calls == []

    assert client.get("/search?q=Corpus").status_code == 200
    assert calls == [1]


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


def _zip_upload():
    """The multipart body `/api/history/import` needs to get past its two
    format guards. Never opened -- the slot is refused first."""
    import io

    return {
        "data": {"file": (io.BytesIO(b"not really a zip"), "export.zip")},
        "content_type": "multipart/form-data",
    }


def test_every_job_start_route_reports_the_slot_is_taken(client, corpus, conn, monkeypatch):
    """One job slot, four jobs, **eight** start routes. Each must refuse
    cleanly with a 409 rather than starting a second job or 500ing.

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
        ("/api/snapshot/pull", {}),
        ("/api/snapshot/refresh", {}),
        ("/api/snapshot/backfill", {}),
        ("/api/roundtrip/start", {}),
        ("/api/roundtrip/reconcile", {}),
        ("/api/backfill/start", {"json": {"generations": 2}}),
        ("/api/history/reimport", {}),
        # The eighth, and the one an endpoint-keyed list keeps losing: it
        # needs a body to reach its slot check at all.
        ("/api/history/import", _zip_upload()),
    ]
    for path, kwargs in starts:
        resp = client.post(path, **kwargs)
        assert resp.status_code == 409, f"{path} -> {resp.status_code}"
        assert set(resp.get_json()) == {"error", "detail"}, path
        assert resp.get_json()["error"] == "already_running", path


def test_the_upload_route_refuses_a_taken_slot_before_it_saves_the_body(
    client, conn, monkeypatch
):
    """`/api/history/import`'s *first* 409 arm, and the reason it is first.

    `history_import.busy()` is checked before `save_upload`, so a rejected
    import never copies a ~66 MB export to disk. Move the check below the save
    and the response is still an identical 409 -- **the discriminating
    assertion is that no upload folder appeared**, which is the only thing the
    ordering changes.
    """
    # source: app.py's own comment on that guard -- "Checked before the file
    # is copied anywhere, so a rejected import doesn't leave a ~66 MB orphan
    # folder behind", and history_import.busy()'s docstring.
    #
    # **Asserted on the call, not on the filesystem**, which was the first
    # attempt and could not fail: `save_upload` names its folder from the
    # clock, `UPLOAD_ROOT` is redirected once for the whole session, and the
    # autouse freezegun clock never moves -- so every upload in the run lands
    # on one constant path and `os.makedirs(exist_ok=True)` reuses it. A
    # before/after listing is therefore identical whether or not this request
    # saved anything.
    import history_import

    monkeypatch.setattr(jobs, "active", lambda: "snapshot")
    saves = []
    monkeypatch.setattr(
        history_import, "save_upload", lambda upload: saves.append(upload.filename)
    )

    resp = client.post("/api/history/import", **_zip_upload())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "already_running"
    assert saves == [], "the body was saved before the slot was checked"


# -- The OAuth callback ------------------------------------------------------
#
# `/callback` is the one route where P2's "would this notice a wrong answer?"
# question has a security answer, and where the status code cannot supply it.
# Every refusal here is a 400 and so is the guard *after* it, so deleting the
# state check outright still returns 400 -- on "Missing authorization code",
# one guard later. The catalog's two variant cases assert non-5xx and
# therefore cannot fail. What discriminates is the description, and whether
# the token exchange was reached at all.


@pytest.fixture
def auth_spy(monkeypatch):
    """Records every token exchange, and performs none.

    Also load-bearing as a *negative* signal: the real exchange is a network
    call that conftest blocks outright, so without this a guard that wrongly
    let a request through would surface as a connection error -- which reads
    like a refusal and would let a broken guard pass.
    """
    exchanges = []

    class _AuthManager:
        def get_access_token(self, code, as_dict=False):
            exchanges.append(code)
            return "an-access-token"

    import app as app_module

    monkeypatch.setattr(app_module, "get_auth_manager", lambda: _AuthManager())
    return exchanges


def test_a_matching_state_completes_the_exchange(client, auth_spy):
    # source: app.py's callback -- the state set by /login is compared, then
    # `auth_manager.get_access_token(code)` and a redirect home. The positive
    # control: without it every assertion below is satisfied by a route that
    # refuses everything.
    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    resp = client.get("/callback?state=the-real-state&code=an-auth-code")

    assert resp.status_code == 302
    assert auth_spy == ["an-auth-code"]


def test_a_forged_state_is_refused_before_the_token_exchange(client, auth_spy):
    """CSRF protection on the OAuth flow: a callback the app did not initiate
    must not be exchanged for a token.

    The `code` is supplied deliberately, so that removing the state check
    doesn't merely change which 400 is returned -- it completes the flow. That
    is what `auth_spy` is asserting on.
    """
    # source: app.py -- `if not expected or request.args.get("state") !=
    # expected: abort(400, description="Invalid OAuth state.")`, and
    # CLAUDE.md's rule that auth and session handling are done fully.
    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    resp = client.get("/callback?state=forged&code=an-auth-code")

    assert resp.status_code == 400
    assert "Invalid OAuth state." in resp.get_data(as_text=True)
    assert auth_spy == []


def test_a_callback_carrying_no_state_at_all_is_refused(client, auth_spy):
    """The `not expected` half of the same guard, and the only fixture that
    reaches it.

    With no `oauth_state` in the session and no `state` argument, the
    comparison is `None != None` -- which is False. So the second half of the
    condition *passes a forged callback*, and only `not expected` refuses it.
    A mismatched-state test cannot show this: there the comparison already
    fails on its own.
    """
    # source: app.py -- the `not expected` disjunct; an unsolicited callback
    # is one that names no state at all.
    resp = client.get("/callback?code=an-auth-code")

    assert resp.status_code == 400
    assert "Invalid OAuth state." in resp.get_data(as_text=True)
    assert auth_spy == []


def test_the_state_is_single_use_so_a_replayed_callback_is_refused(client, auth_spy):
    # source: app.py -- `session.pop("oauth_state", None)`. A pop, not a get:
    # a captured callback url must not be replayable against a live session.
    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    assert client.get("/callback?state=the-real-state&code=first").status_code == 302
    replay = client.get("/callback?state=the-real-state&code=second")

    assert replay.status_code == 400
    assert "Invalid OAuth state." in replay.get_data(as_text=True)
    assert auth_spy == ["first"]


def test_an_authorization_error_is_reported_and_stops_the_flow(client, auth_spy):
    """Spotify's own refusal, checked before anything else.

    The session state matches and a `code` is present, so without this arm the
    request would run to a completed exchange rather than to a different 400.
    """
    # source: app.py -- `abort(400, description=f"Spotify authorization
    # failed: {error}")`, the first guard in the route.
    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    resp = client.get("/callback?error=access_denied&state=the-real-state&code=an-auth-code")

    assert resp.status_code == 400
    assert "access_denied" in resp.get_data(as_text=True)
    assert auth_spy == []


def test_a_missing_code_is_refused_after_the_state_check_passes(client, auth_spy):
    # source: app.py -- `if not code: abort(400, description="Missing
    # authorization code.")`. The description is what separates this from the
    # state refusal, which is the whole reason those tests assert on it.
    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    resp = client.get("/callback?state=the-real-state")

    assert resp.status_code == 400
    assert "Missing authorization code." in resp.get_data(as_text=True)
    assert auth_spy == []


# -- The remaining query-string variants, made observable --------------------
#
# The catalog's variant cases prove these branches respond. Eight of them
# could ignore their own argument entirely and no test would notice, which is
# `P2_tests.md` §1's cheapest non-observation wearing the newest hat. Two are
# P2-008's seam exactly: `include_singletons` and `render_export_text`'s
# `cutoff` are both well tested as functions, and only the route's *wiring*
# of the parameter was unobserved.


def test_singleton_groups_are_hidden_until_asked_for(client, corpus, conn):
    # source: canonical.song_group_rows' `include_singletons` -- a group of one
    # is a track the engine has not grouped with anything, not a grouping
    # decision, so the listing omits it unless `?singletons=1` asks.
    solo = builders.make_track(conn, "t-solo", name="Solo Singleton Track")
    builders.make_group(conn, [solo])
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert "Solo Singleton Track" not in client.get("/dev/canonical").get_data(as_text=True)
    assert "Solo Singleton Track" in client.get(
        "/dev/canonical?singletons=1"
    ).get_data(as_text=True)


def test_the_canonical_search_box_finds_a_track_the_listing_omits(client, corpus, conn):
    """`?search=` is a track search beside the group listing, not a filter on
    it. The target is a singleton *on purpose*: the listing can never render
    it, so only the search block can, and the two cannot be confused.
    """
    # source: app.py's dev_canonical -- `search_q` builds `search_results`,
    # a LIKE over track and artist names, separately from the group listing.
    solo = builders.make_track(conn, "t-searchable", name="Solo Searchable Track")
    builders.make_group(conn, [solo])
    canonical.ensure_track_groups(conn)
    conn.commit()

    assert "Solo Searchable Track" not in client.get("/dev/canonical").get_data(as_text=True)
    assert "Solo Searchable Track" in client.get(
        "/dev/canonical?search=Searchable"
    ).get_data(as_text=True)


def test_a_deep_linked_group_is_shown_even_when_the_cap_excludes_it(client, corpus, monkeypatch):
    """`?expand=` must survive the listing cap, or a deep link to a group past
    it lands on a page that doesn't contain it.

    The cap is set to zero rather than to a number just below the fixture's
    size: what is under test is that `expand` re-adds a group the cap dropped,
    and zero makes "the cap dropped it" unconditional instead of dependent on
    where the fixture happens to rank.
    """
    # source: app.py -- "A deep link to a group past the cap would otherwise
    # land on a page that doesn't contain it", and CLAUDE.md's `_LISTING_CAP`
    # note that an unfiltered load renders 50 rows.
    import app as app_module

    monkeypatch.setattr(app_module, "_LISTING_CAP", 0)
    song_id = corpus["groups"]["song"]

    assert "Corpus Track One" not in client.get("/dev/canonical").get_data(as_text=True)
    assert "Corpus Track One" in client.get(
        f"/dev/canonical?expand={song_id}"
    ).get_data(as_text=True)


def test_the_snapshot_page_track_search_finds_a_library_track(client, corpus):
    """`?q=` drives the "Find a track" panel.

    **A track's name is not a usable assertion on this page** -- the Recent
    changes panel renders member names too, so a search that ignored `q`
    would still show one. The search block's own per-row suffix is what only
    the search can produce, and the miss case pins that `q` reached the LIKE
    rather than merely opening the panel.
    """
    # source: app.py's dev_snapshot -- `?q=` builds `track_matches`, a LIKE
    # over tracks with a live membership, rendered with their playlist count.
    hit = client.get("/dev/snapshot?q=Corpus Track One").get_data(as_text=True)
    miss = client.get("/dev/snapshot?q=zzz-nothing-matches-this").get_data(as_text=True)

    assert "— Corpus Artist (2 playlists)" in hit
    assert "No tracks match" in miss
    assert "(2 playlists)" not in miss


def _one_generation_with_two_versions_of_one_song(conn):
    """Two version groups, one song group, both present in generation 1 --
    the smallest fixture on which the two tiers disagree."""
    ta = builders.make_track(conn, "t-tier-a", name="Tier Version A")
    tb = builders.make_track(conn, "t-tier-b", name="Tier Version B")
    groups = builders.make_group(conn, [ta])
    builders.make_group(conn, [tb], song=groups["song"])
    playlist = builders.make_playlist(conn, "p-tier-gen", name="v1.0.0")
    builders.make_generation(conn, ordinal=1, playlist_id=playlist)
    for track_id in (ta, tb):
        builders.make_membership(conn, playlist_id=playlist, track_id=track_id)
    canonical.ensure_track_groups(conn)
    conn.commit()


def test_the_tenure_tier_toggle_rolls_two_versions_into_one_song(client, conn):
    # source: generations-B.md 'Rollup tier' -- tenure is reported at version
    # or song tier, and two versions of one song collapse to a single song
    # row. The rendered total is what the toggle changes.
    _one_generation_with_two_versions_of_one_song(conn)

    version = client.get("/dev/generations/tenure?tier=version").get_data(as_text=True)
    song = client.get("/dev/generations/tenure?tier=song").get_data(as_text=True)

    assert "2 groups ever present in a generation" in version
    assert "1 group ever present in a generation" in song


def test_the_generations_list_tier_toggle_counts_songs_not_versions(client, conn):
    # source: generations-B.md 'Rollup tier' -- the same parameter, on the
    # other page that reads `_generations_tier_arg`. Both routes share one
    # helper, so this is what stops the list page's half going unobserved.
    _one_generation_with_two_versions_of_one_song(conn)

    version = client.get("/dev/generations?tier=version").get_data(as_text=True)
    song = client.get("/dev/generations?tier=song").get_data(as_text=True)

    assert version != song


def test_the_tenure_sort_actually_reorders_the_table(client, conn):
    """`?sort=` is a whitelist lookup, and the existing test only proves an
    unrecognised value is refused. This is the other half: a recognised one
    changes the order.

    The fixture is built so tenure and score *disagree* -- the long-tenured
    group has no plays and the newcomer has many -- so an implementation
    ignoring `sort` puts the same row first both times.
    """
    # source: app.py -- `sort_key = _TENURE_SORT_KEYS[sort]` then
    # `all_tenures.sort(...)`, which scoring-H.md §11.1 requires to run before
    # pagination.
    veteran = builders.make_track(conn, "t-veteran", name="Veteran Track")
    newcomer = builders.make_track(conn, "t-newcomer", name="Newcomer Track")
    builders.make_group(conn, [veteran])
    builders.make_group(conn, [newcomer])
    for ordinal in (1, 2, 3):
        playlist = builders.make_playlist(conn, f"p-sort-{ordinal}", name=f"v{ordinal}.0.0")
        builders.make_generation(conn, ordinal=ordinal, playlist_id=playlist)
        builders.make_membership(conn, playlist_id=playlist, track_id=veteran)
        if ordinal == 3:
            builders.make_membership(conn, playlist_id=playlist, track_id=newcomer)
    for _ in range(40):
        builders.make_play(conn, track_id=newcomer, ts=builders.days_ago(1))
    canonical.ensure_track_groups(conn)
    scoring.recompute(conn)
    conn.commit()

    by_tenure = client.get("/dev/generations/tenure?sort=tenure").get_data(as_text=True)
    by_score = client.get("/dev/generations/tenure?sort=score").get_data(as_text=True)

    assert by_tenure.index("Veteran Track") < by_tenure.index("Newcomer Track")
    assert by_score.index("Newcomer Track") < by_score.index("Veteran Track")


def test_the_tenure_page_paginates_at_a_hundred_rows(client, conn):
    """`?page=` is only observable past `_TENURE_PAGE_SIZE`, which is a
    closure local and so cannot be lowered from a test -- the fixture has to
    be genuinely bigger than a page.

    Every group scores 0, so the sort falls through to its `group_id`
    tiebreak, which is what makes "the last one" a fixed, nameable row rather
    than whichever way the scores happened to land.
    """
    # source: app.py -- `_TENURE_PAGE_SIZE = 100`, `page_slice = all_tenures[
    # start : start + _TENURE_PAGE_SIZE]`, and the "group_id as the tiebreak
    # keeps paging stable across requests" comment above the sort.
    playlist = builders.make_playlist(conn, "p-paged", name="v1.0.0")
    builders.make_generation(conn, ordinal=1, playlist_id=playlist)
    for n in range(101):
        track_id = builders.make_track(conn, f"t-paged-{n:03d}", name=f"Paged Track {n:03d}")
        builders.make_group(conn, [track_id])
        builders.make_membership(conn, playlist_id=playlist, track_id=track_id)
    canonical.ensure_track_groups(conn)
    conn.commit()

    page1 = client.get("/dev/generations/tenure?page=1").get_data(as_text=True)
    page2 = client.get("/dev/generations/tenure?page=2").get_data(as_text=True)

    assert "Page 1 of 2" in page1
    assert "Paged Track 000" in page1
    assert "Paged Track 100" not in page1
    assert "Paged Track 100" in page2


def _export_section_of(text, card_name):
    """Which `## heading` a card is listed under in the export text."""
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:]
        elif card_name in line:
            return section
    return None


def test_the_export_cutoff_decides_what_groups_under_a_label(client, conn):
    # source: app.py -- `cutoff = float(request.args.get("cutoff", 300))`,
    # passed straight into grouping.render_export_text. The function is
    # covered; this is the route's wiring of the argument (P2-008's seam).
    builders.make_label(conn, x=0.0, y=0.0, text="Cutoff Label")
    builders.make_card(conn, x=200.0, y=0.0, display_name="Distant Card")
    conn.commit()

    wide = client.get("/api/export?cutoff=300").get_json()["text"]
    narrow = client.get("/api/export?cutoff=100").get_json()["text"]

    assert _export_section_of(wide, "Distant Card").startswith("Cutoff Label")
    assert _export_section_of(narrow, "Distant Card") == "Ungrouped"


# -- P3 session 3's seams: the three dev pages ------------------------------
#
# Same shape as the section above, one extraction later. Session 3's mutation
# sweep (P3-007) ran every one of these as a one-line change and found each
# observable only by the golden baseline, or by nothing at all. They are route
# tests rather than unit tests because each is P2-008's seam exactly: the
# extracted function does the work, and app.py decides what to hand it or what
# to render beside it -- neither half can be tested from the other side.


def test_a_search_lifts_the_listing_cap(client, corpus, monkeypatch):
    """`?q=` is taken as asking for *all* of its matches, so the cap does not
    apply to a filtered listing.

    The cap is monkeypatched to zero for the same reason the `?expand=` test
    above does it: it makes "the cap would have dropped this" unconditional.
    It is also why nothing was catching this -- every `?q=` case in the
    catalog matches far fewer rows than the real cap of 50, so capping a
    search and not capping it render identically.
    """
    # source: app.py's _cap_listing comment -- "a search is taken as asking
    # for all of its matches, so `%` (a LIKE wildcard in both filters) still
    # gets you everything". Since P3 the groups half of that rule is the
    # route's `cap=None if q else _LISTING_CAP`, handed to
    # canonical_detect.index_data, so only the route can decide it.
    import app as app_module

    monkeypatch.setattr(app_module, "_LISTING_CAP", 0)

    assert "Corpus Track One" not in client.get("/dev/canonical").get_data(as_text=True)
    assert "Corpus Track One" in client.get("/dev/canonical?q=Corpus").get_data(as_text=True)


def test_the_canonical_filter_boxes_keep_what_was_searched_for(client, corpus):
    # source: canonical.html:101 and :181 -- the Groups and cross-artist
    # filter inputs render `value="{{ q }}"` / `value="{{ cross_q }}"` from
    # the route's echo. Losing an echo empties the box on every result page
    # while the rows stay correctly filtered, so nothing about the listing
    # looks wrong -- which is why only a byte-exact baseline saw it.
    body = client.get("/dev/canonical?q=Corpus&cross=Zzz").get_data(as_text=True)

    assert 'name="q" value="Corpus"' in body
    assert 'name="cross" id="cross-input" value="Zzz"' in body


def test_the_singletons_checkbox_stays_ticked_after_it_is_used(client, corpus):
    # source: canonical.html:104 -- `{{ "checked" if show_singletons }}`.
    # The listing test above proves `?singletons=1` changes the rows; this is
    # the other half, that the control reporting the state agrees with it. A
    # page whose rows include singletons but whose box is unticked invites
    # exactly one wrong click.
    assert "checked" not in client.get("/dev/canonical").get_data(as_text=True)
    assert "checked" in client.get("/dev/canonical?singletons=1").get_data(as_text=True)


def test_a_deep_linked_group_is_rendered_already_open(client, corpus):
    # source: canonical.html:117 -- `{{ "open" if g.song_id == expand_song_id }}`
    # on the <details>. Distinct from the cap test above, which proves the
    # group is *present*: a deep link that lands on a collapsed group has
    # arrived at the right page and shown nothing.
    song_id = corpus["groups"]["song"]

    body = client.get(f"/dev/canonical?expand={song_id}").get_data(as_text=True)

    assert f'id="song-{song_id}" open' in body
    assert f'id="song-{song_id}" open' not in client.get("/dev/canonical").get_data(as_text=True)


def test_the_auto_group_status_line_and_badge_come_from_the_run_record(client, corpus, conn):
    # source: canonical.html:78-83 and its auto_badge macro. Both values stay
    # in the route rather than moving into index_data, because
    # canonical_autogroup imports canonical_detect and reaching for them there
    # would be a new cycle (P3_refactor.md §4.1.1) -- so this is the only
    # place the wiring is visible at all.
    conn.execute(
        "INSERT INTO auto_group_run (id, started_at, finished_at, groups_closed, tracks_affected) "
        "VALUES (7, '2026-08-01T00:00:00Z', '2026-08-01T00:01:00Z', 3, 9)"
    )
    conn.execute(
        "UPDATE canonical_group SET auto_run_id = 7 WHERE id = ?", (corpus["groups"]["song"],)
    )
    conn.commit()

    body = client.get("/dev/canonical").get_data(as_text=True)

    # Asserted as two fragments because the template breaks the line between
    # them; both numbers come from last_auto_run, so a missing echo takes
    # both with it.
    assert "3 groups," in body
    assert "9 tracks" in body
    assert 'title="Created by an auto-group run"' in body


def test_the_snapshot_page_offers_an_undeclared_generation(client, corpus, conn):
    # source: snapshot.html:10 -- generation_confirm_banner(pending_generation).
    # That value is generations.py's and is fetched in the route, deliberately
    # not inside snapshot.index_data (snapshot.py has no dependency on
    # generations.py and gains none). The route is therefore the only side
    # that can be observed, and nothing was observing it.
    builders.make_playlist(conn, "p-v2", name="v2.0.0")
    conn.commit()

    body = client.get("/dev/snapshot").get_data(as_text=True)

    assert 'id="generation-confirm-form"' in body
    assert "v2.0.0" in body


# -- Scrobbling (docs/specs/scrobbling-R.md) ---------------------------------


def test_the_scrobble_page_shows_a_stored_scrobble(client, corpus, conn):
    # source: scrobbling-R.md §7 -- "The last 50 plays ... each linked
    # through the entity_link macro." The catalog's dev_scrobble case only
    # proves the page responds (P2-010); this proves a real play renders on
    # it rather than an empty state.
    builders.make_play(
        conn, track_id=corpus["tracks"][0], source="scrobble", ts=builders.days_ago(0)
    )

    body = client.get("/dev/scrobble").get_data(as_text=True)

    assert "Corpus Track One" in body


def test_the_scrobble_toggle_flips_the_meta_key_in_both_directions(client, corpus, conn):
    # source: scrobbling-R.md Tests clause 14 -- "/api/scrobble/toggle flips
    # the meta key in both directions."
    resp = client.post("/api/scrobble/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert db.get_meta(conn, "scrobble_enabled") == "0"

    resp = client.post("/api/scrobble/toggle", json={"enabled": True})
    assert resp.status_code == 200
    assert db.get_meta(conn, "scrobble_enabled") == "1"


# -- The two app-wide hooks, and the OAuth pair (S sweep, app.py 80/95/741/761)


def test_the_scoring_backstop_runs_on_authenticated_requests_but_not_public_ones(
    client, monkeypatch
):
    """app.py's second `before_request` guards on the same
    `_PUBLIC_ENDPOINTS` set as the login guard above it, and the two are
    easy to confuse: inverting *this* one is not an auth hole, it silently
    stops the scoring backstop ever running on a real request.
    """
    # source: S_sweep.md §3 -- in at app.py:80
    calls = []
    monkeypatch.setattr(scoring, "ensure_fresh", lambda: calls.append(True))

    client.get("/login")
    assert calls == [], "the backstop must not run on a public endpoint"

    client.get("/")
    assert calls == [True], "the backstop must run on an authenticated request"


def test_the_scoring_banner_shows_only_when_the_last_recompute_failed(
    client, monkeypatch
):
    """async-recompute-N.md §7.1: a background recompute failure has no
    request to 500, so this banner is its only visible signal. Both
    directions are asserted -- a banner that is always on is as useless as
    one that never appears, and nothing else in the suite reads
    `scoring_failed` at all.
    """
    # source: S_sweep.md §3 -- eq at app.py:95
    monkeypatch.setattr(
        scoring,
        "recompute_status",
        lambda: {"outcome": "error", "error": "a broken recompute"},
    )

    body = client.get("/").get_data(as_text=True)

    assert "Background score recompute is failing" in body
    assert "a broken recompute" in body

    monkeypatch.setattr(
        scoring,
        "recompute_status",
        lambda: {"outcome": "ok", "error": None},
    )

    body = client.get("/").get_data(as_text=True)

    assert "Background score recompute is failing" not in body


def test_the_oauth_state_carries_at_least_the_entropy_login_asks_for(client):
    """The `num` mutant at app.py:741 -- `token_urlsafe(32)` to `(33)` -- is
    **equivalent**: a longer state is strictly more entropy and nothing
    observes its length. This test does not kill that mutant and is not
    meant to. It pins the floor instead, which is the half worth having
    (mutation-sweep-S.md §5's carve-out: record the equivalent, and test the
    boundary's answer where that answer is itself worth pinning). Recorded
    here so the next sweep does not re-derive the same verdict.
    """
    # source: S_sweep.md §3 -- num at app.py:741
    client.get("/login")

    with client.session_transaction() as sess:
        state = sess["oauth_state"]

    # token_urlsafe(n) base64url-encodes n random bytes, so 32 -> 43 chars.
    assert len(state) >= 43


def test_the_token_exchange_uses_spotipys_non_deprecated_form(client, monkeypatch):
    """`get_access_token`'s return value is discarded at the call site, so
    `as_dict` changes nothing Symr observes but spotipy's own
    DeprecationWarning -- which a spy cannot raise. The argument is the only
    observable left, so it is what this pins. The spy defaults it to `True`,
    matching spotipy's real signature, so dropping the argument entirely
    fails here too rather than silently passing.
    """
    # source: S_sweep.md §3 -- false at app.py:761
    seen = {}

    class _AuthManager:
        def get_access_token(self, code, as_dict=True, check_cache=True):
            seen["as_dict"] = as_dict
            return "an-access-token"

    import app as app_module

    monkeypatch.setattr(app_module, "get_auth_manager", lambda: _AuthManager())

    with client.session_transaction() as sess:
        sess["oauth_state"] = "the-real-state"

    resp = client.get("/callback?state=the-real-state&code=an-auth-code")

    assert resp.status_code == 302
    assert seen["as_dict"] is False
