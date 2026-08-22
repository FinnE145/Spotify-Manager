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
    for case in routes_catalog.cases_for(conn):
        resp = routes_catalog.issue(client, case)
        assert resp.status_code < 500, f"{case.slug} ({case.method} {case.path}) -> {resp.status_code}"


# -- Semantic assertions: what P2_tests.md §1 says a bare 200 doesn't prove


def test_track_page_shows_track_and_artist_names(client, corpus):
    resp = client.get(f"/track/{corpus['tracks'][0]}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Track One" in body
    assert "Corpus Artist" in body


def test_album_page_shows_album_and_track_names(client, corpus):
    resp = client.get(f"/album/{corpus['album']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Album" in body
    assert "Corpus Track One" in body


def test_artist_page_shows_artist_name(client, corpus):
    resp = client.get(f"/artist/{corpus['artist']}")
    assert resp.status_code == 200
    assert "Corpus Artist" in resp.get_data(as_text=True)


def test_playlist_page_shows_playlist_name(client, corpus):
    resp = client.get(f"/playlist/{corpus['playlist']}")
    assert resp.status_code == 200
    assert "Corpus Playlist" in resp.get_data(as_text=True)


def test_version_page_shows_a_member_track_name(client, corpus):
    resp = client.get(f"/version/{corpus['groups']['version']}")
    assert resp.status_code == 200
    assert "Corpus Track One" in resp.get_data(as_text=True)


def test_search_finds_a_matching_track_and_not_a_non_matching_one(client, corpus, conn):
    # The negative half is the discriminating one -- a search page that just
    # dumps every track would pass a positive-only assertion too.
    builders.make_track(conn, "t-decoy", name="Totally Unrelated Song")
    conn.commit()

    resp = client.get("/search?q=Corpus")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Corpus Track One" in body
    assert "Totally Unrelated Song" not in body


def test_dev_generations_renders_generation_names(client, corpus):
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
