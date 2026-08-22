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


# -- The sweep --------------------------------------------------------------


def _case_id(case):
    return case.slug


@pytest.fixture
def swept_cases(conn, corpus):
    return routes_catalog.cases_for(conn)


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
    assert "first" not in body.lower() or "first 2 of 2" not in body.lower()


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
