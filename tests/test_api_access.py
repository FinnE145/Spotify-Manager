"""What the v1 API may and may not do (docs/specs/api-Z1.md §3, §4, §5.2, §5.4;
its ## Tests clauses 1-5, 7, 17-19).

The shape of most of these: the clause *replaces* a rule the old code had, so
the fixture is one the old rule would fail -- a lapsed token, an album fetched
before, a play carrying an IP address.
"""

import ast
import json
import os

import pytest

import app as app_module
import entities
import resources
import roundtrip
from api_lib import build, ids_for_sweep
from builders import make_album, make_artist, make_membership, make_play, make_playlist, make_track
from fakes import spotify_album, spotify_artist, spotify_track

import routes_catalog

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def lapsed(monkeypatch):
    """The server's Spotify sign-in has lapsed, and every ask for a client is
    counted -- including each module's own import of the function."""
    asks = []

    def no_client():
        asks.append(1)
        return None

    for module in (app_module, resources, entities):
        monkeypatch.setattr(module, "get_spotify_client", no_client)
    return asks


# -- Clause 1: the guard ----------------------------------------------------------------------


def test_v1_skips_the_login_guard_and_nothing_else_does(client, conn, lapsed):
    # source: api-Z1.md §4.1 / Tests 1 -- replaces "every non-public request
    # requires a valid cached Spotify token". One test, so an exemption keyed on
    # the /api/ prefix rather than the Blueprint fails it.
    build(conn)
    pid = make_playlist(conn, "pl-1")

    assert client.get("/api/v1/tracks").status_code == 200
    # The "no token refresh" half of §4.3: an exemption placed *after* the
    # get_spotify_client() call would fail this.
    assert lapsed == []

    resp = client.patch(f"/api/v1/playlists/{pid}", json={"excluded": True})
    assert resp.status_code == 200
    assert conn.execute("SELECT excluded FROM snapshot WHERE playlist_id = ?", (pid,)).fetchone()[0] == 1
    assert lapsed == []

    # Everything else is unchanged.
    assert client.get("/api/snapshot/status").status_code == 401
    assert client.post("/api/snapshot/stop").status_code == 401
    page = client.get("/")
    assert page.status_code == 302 and page.headers["Location"].endswith("/login")


def test_status_answers_when_the_token_has_lapsed(client, lapsed):
    # source: api-Z1.md §10 -- "spotify_authenticated ... is computed without
    # the login guard, so this endpoint answers even when the token has
    # lapsed, and that answer is the point."
    resp = client.get("/api/v1/status")

    assert resp.status_code == 200
    assert resp.get_json()["spotify_authenticated"] is False


# -- Clause 2: no spend by default ----------------------------------------------------------------


@pytest.fixture
def fetch_spy(monkeypatch):
    calls = []
    real_album, real_artist = entities.fetch_album_tracklist, entities.fetch_artist_image

    def album(conn, album_id):
        calls.append(("album", album_id))
        return real_album(conn, album_id)

    def artist(conn, artist_id):
        calls.append(("artist", artist_id))
        return real_artist(conn, artist_id)

    monkeypatch.setattr(entities, "fetch_album_tracklist", album)
    monkeypatch.setattr(entities, "fetch_artist_image", artist)
    return calls


def test_album_and_artist_detail_do_not_fetch_without_the_param(client, conn, fetch_spy):
    # source: api-Z1.md §4.3/§4.4 / Tests 2 -- "Album and artist detail without
    # the param never call the guarded fetch functions."
    build(conn)

    assert client.get("/api/v1/albums/al-z").status_code == 200
    assert client.get("/api/v1/artists/ar-alpha").status_code == 200

    assert fetch_spy == []


def test_a_never_fetched_tracklist_and_image_are_null_not_empty(client, conn):
    # source: api-Z1.md §4.4 / Tests 2 -- "The never-fetched tracklist is null,
    # not [], and the never-fetched image_url is null." An implementation
    # returning the stored empty value fails this.
    build(conn)

    album = client.get("/api/v1/albums/al-z").get_json()
    artist = client.get("/api/v1/artists/ar-alpha").get_json()

    assert album["tracklist"] is None
    assert album["tracklist_count"] is None
    assert album["total_duration_ms"] is None
    assert album["tracklist_fetched_at"] is None
    assert artist["image_url"] is None
    assert artist["image_fetched_at"] is None


def test_spotify_request_fetches_once_on_an_unattempted_album(client, conn, fake_spotify, fetch_spy):
    # source: api-Z1.md §4.4 / Tests 2 -- "With spotify_request=1 on an
    # unattempted album, the fetch is called exactly once."
    build(conn)
    fake_spotify.add_album(spotify_album("al-z", tracks={"items": [spotify_track("t-zeb")], "next": None}))

    resp = client.get("/api/v1/albums/al-z?spotify_request=1")

    assert resp.status_code == 200
    assert fetch_spy == [("album", "al-z")]
    assert [c[0] for c in fake_spotify.calls] == ["album"]
    assert [t["track"]["id"] for t in resp.get_json()["tracklist"]] == ["t-zeb"]


@pytest.mark.parametrize("state", ["fetched", "attempt_failed"])
def test_spotify_request_refetches_even_when_the_page_would_not(client, conn, fake_spotify, fetch_spy, state):
    # source: api-Z1.md §4.4 / Tests 2 -- replaces the pages' first-view-only
    # rule for this parameter: a fixture with the stamp set is what an
    # implementation that kept that rule would fail.
    build(conn)
    old = json.dumps([{"id": "t-old", "name": "Old", "uri": "spotify:track:t-old"}])
    conn.execute(
        "UPDATE album SET tracklist_pulled_at = '2026-01-01T00:00:00Z', tracklist_json = ? "
        "WHERE album_id = 'al-z'",
        (old if state == "fetched" else None,),
    )
    conn.commit()
    fake_spotify.add_album(spotify_album("al-z", tracks={"items": [spotify_track("t-zeb")], "next": None}))

    resp = client.get("/api/v1/albums/al-z?spotify_request=1")

    assert resp.status_code == 200
    assert fetch_spy == [("album", "al-z")]
    assert [t["name"] for t in resp.get_json()["tracklist"]] != ["Old"]
    row = conn.execute("SELECT tracklist_pulled_at FROM album WHERE album_id = 'al-z'").fetchone()
    assert row[0] == "2026-06-15T12:00:00Z"  # re-stamped


def test_spotify_request_on_an_artist_fetches_once_and_stores_the_image(client, conn, fake_spotify, fetch_spy):
    # source: api-Z1.md §4.4 -- artist detail takes the same parameter.
    build(conn)
    fake_spotify.add_artist(spotify_artist("ar-alpha", images=[{"url": "big", "width": 640, "height": 640}]))

    resp = client.get("/api/v1/artists/ar-alpha?spotify_request=1")

    assert resp.status_code == 200
    assert fetch_spy == [("artist", "ar-alpha")]
    assert resp.get_json()["image_url"] == "big"


# -- Clause 3: never silently --------------------------------------------------------------------


def test_spotify_request_with_no_client_is_a_503_and_stamps_nothing(client, conn, lapsed):
    # source: api-Z1.md §4.4 / Tests 3 -- "No client -> 503
    # spotify_not_authenticated, and nothing stamped"; neither a 200 nor a 401.
    build(conn)

    album = client.get("/api/v1/albums/al-z?spotify_request=1")
    artist = client.get("/api/v1/artists/ar-alpha?spotify_request=1")

    for resp in (album, artist):
        assert resp.status_code == 503
        assert resp.get_json()["error"] == "spotify_not_authenticated"
        assert "/login" in resp.get_json()["detail"]
    assert conn.execute("SELECT tracklist_pulled_at FROM album WHERE album_id = 'al-z'").fetchone()[0] is None
    assert conn.execute("SELECT detail_pulled_at FROM artist WHERE artist_id = 'ar-alpha'").fetchone()[0] is None


def test_spotify_request_that_fails_is_a_502_and_stamps_the_attempt(client, conn, fake_spotify):
    # source: api-Z1.md §4.4 / Tests 3 -- "A fake client whose call raises ->
    # 502 spotify_request_failed, and the attempt stamped (both halves)."
    build(conn)
    fake_spotify.fail("album", RuntimeError("boom"))
    fake_spotify.fail("artist", RuntimeError("bang"))

    album = client.get("/api/v1/albums/al-z?spotify_request=1")
    artist = client.get("/api/v1/artists/ar-alpha?spotify_request=1")

    assert (album.status_code, artist.status_code) == (502, 502)
    assert album.get_json()["error"] == "spotify_request_failed"
    assert "boom" in album.get_json()["detail"]
    assert conn.execute("SELECT tracklist_pulled_at FROM album WHERE album_id = 'al-z'").fetchone()[0] == "2026-06-15T12:00:00Z"
    assert conn.execute("SELECT detail_pulled_at FROM artist WHERE artist_id = 'ar-alpha'").fetchone()[0] == "2026-06-15T12:00:00Z"


def test_a_failed_earlier_attempt_is_visible_without_spending(client, conn):
    # source: api-Z1.md §4.4 -- "whether an earlier attempt failed is visible as
    # tracklist_fetched_at set with tracklist still null."
    build(conn)
    conn.execute("UPDATE album SET tracklist_pulled_at = '2026-01-01T00:00:00Z' WHERE album_id = 'al-z'")
    conn.commit()

    album = client.get("/api/v1/albums/al-z").get_json()

    assert album["tracklist_fetched_at"] == "2026-01-01T00:00:00Z"
    assert album["tracklist"] is None


# -- Clause 4: queueing is opt-in --------------------------------------------------------------------


def _album_with_unowned_tracklist(conn):
    build(conn)
    items = [
        {"id": "t-zeb", "name": "Zebra", "uri": "spotify:track:t-zeb", "duration_ms": 300000,
         "track_number": 1, "disc_number": 1},
        {"id": "t-unowned", "name": "Not Mine", "uri": "spotify:track:t-unowned", "duration_ms": 1000,
         "track_number": 2, "disc_number": 1},
    ]
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = '2026-01-01T00:00:00Z' "
        "WHERE album_id = 'al-z'",
        (json.dumps(items),),
    )
    conn.commit()


def test_queueing_unowned_uris_is_opt_in(client, conn):
    # source: api-Z1.md §4.4 / Tests 4 -- replaces the page's "every album view
    # queues": without queue_unowned a v1 read writes nothing, with it the uris
    # land with source 'album'.
    _album_with_unowned_tracklist(conn)

    client.get("/api/v1/albums/al-z")
    assert conn.execute("SELECT COUNT(*) FROM wanted_uri").fetchone()[0] == 0

    client.get("/api/v1/albums/al-z?queue_unowned=1")
    rows = conn.execute("SELECT uri, source, album_id FROM wanted_uri").fetchall()
    assert [tuple(r) for r in rows] == [("spotify:track:t-unowned", "album", "al-z")]


def test_spotify_request_and_queue_unowned_combine_fetch_first(client, conn, fake_spotify):
    # source: api-Z1.md §4.4 -- queue_unowned "combines with spotify_request=1:
    # the fetch runs first, then the queueing reads the fresh tracklist." The
    # album has no stored tracklist, so only a fetch-then-queue order queues.
    build(conn)
    fake_spotify.add_album(spotify_album(
        "al-z", tracks={"items": [spotify_track("t-zeb"), spotify_track("t-unowned")], "next": None}))

    resp = client.get("/api/v1/albums/al-z?spotify_request=1&queue_unowned=1")

    assert resp.status_code == 200
    assert [r[0] for r in conn.execute("SELECT uri FROM wanted_uri")] == ["spotify:track:t-unowned"]


def test_the_album_page_still_queues_on_a_plain_view(client, conn):
    # source: api-Z1.md §4.4 / Tests 4 -- "the album page itself still queues on
    # a plain view (unchanged)."
    _album_with_unowned_tracklist(conn)

    client.get("/album/al-z")

    assert conn.execute("SELECT COUNT(*) FROM wanted_uri").fetchone()[0] == 1


# -- Clause 5: never exposed -------------------------------------------------------------------------


def test_ip_addr_and_raw_json_never_appear_in_any_v1_response(client, conn):
    # source: api-Z1.md §4.2 / Tests 5 -- "A fixture play with a populated
    # ip_addr: neither the key nor the value appears. No response from any v1
    # endpoint in the route sweep contains any raw_json content."
    make_artist(conn, "ar-1", "Artist", raw_json="RAWJSON-ARTIST")
    make_album(conn, "al-1", artists=["ar-1"], raw_json="RAWJSON-ALBUM")
    make_track(conn, "t-1", "Song", album_id="al-1", artists=["ar-1"], raw_json="RAWJSON-TRACK")
    make_play(conn, "t-1", ip_addr="203.0.113.77")
    make_play(conn, uri="spotify:track:unknown", ip_addr="203.0.113.78")
    make_membership(conn, "pl-1", "t-1")
    ids = ids_for_sweep(conn, build(conn))
    ids.update(track="t-1", album="al-1", artist="ar-1")

    seen = 0
    for case in routes_catalog.CASES:
        if not case.endpoint.startswith("api.") or case.method != "GET":
            continue
        body = client.get(routes_catalog._fill(case.path, ids)).get_data(as_text=True)
        seen += 1
        for forbidden in ("RAWJSON", "203.0.113", "ip_addr"):
            assert forbidden not in body, f"{case.path} leaked {forbidden}"
    plays = client.get("/api/v1/plays").get_json()
    assert plays["total"] == 2 and seen > 20
    assert all("ip_addr" not in p for p in plays["items"])


# -- Clause 7: strict params ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, status, slug",
    [
        ("/api/v1/songs?bogus=1", 400, "unknown_parameter"),
        ("/api/v1/artists?min_ms=3", 400, "unsupported_filter"),
        ("/api/v1/albums?explicit=1", 400, "unsupported_filter"),
        ("/api/v1/playlists?sort=tenure", 400, "unsupported_sort"),
        ("/api/v1/artists?sort=duration", 400, "unsupported_sort"),
        ("/api/v1/tracks?sort=nonsense", 400, "invalid_parameter"),
        ("/api/v1/tracks?artist_role=primary", 400, "invalid_parameter"),
        ("/api/v1/tracks?q=a&q_full=b", 400, "invalid_parameter"),
        ("/api/v1/tracks?q=a&q_contains=b&q_full=c", 400, "invalid_parameter"),
        ("/api/v1/tracks?page=zero", 400, "invalid_parameter"),
        ("/api/v1/tracks?page=0", 400, "invalid_parameter"),
        ("/api/v1/tracks?per_page=201", 400, "invalid_parameter"),
        ("/api/v1/tracks?explicit=maybe", 400, "invalid_parameter"),
        ("/api/v1/tracks?min_played_ms=-1", 400, "invalid_parameter"),
        ("/api/v1/tracks?tz=Mars/Olympus", 400, "invalid_parameter"),
        ("/api/v1/tracks?from=notadate", 400, "invalid_parameter"),
        ("/api/v1/tracks?from=2025-03-02&to=2025-03-01", 400, "invalid_parameter"),
        ("/api/v1/tracks?page=1&page=2", 400, "invalid_parameter"),
        ("/api/v1/search", 400, "invalid_parameter"),
        ("/api/v1/status?x=1", 400, "unknown_parameter"),
        ("/api/v1/plays?track=a&album=b", 400, "invalid_parameter"),
        ("/api/v1/search?q=ab&page=2", 400, "invalid_parameter"),
        ("/api/v1/search?q=ab&type=nope", 400, "invalid_parameter"),
        ("/api/v1/generations/1?tier=nope", 400, "invalid_parameter"),
    ],
)
def test_a_bad_param_is_a_400_with_the_documented_slug(client, conn, path, status, slug):
    # source: api-Z1.md §5.2 / Tests 7 -- replaces "the existing pages ignore
    # query args they don't read": unknown, inapplicable and malformed are all
    # refused, in the {"error", "detail"} shape.
    build(conn)

    resp = client.get(path)

    assert resp.status_code == status
    body = resp.get_json()
    assert body["error"] == slug
    assert set(body) == {"error", "detail"}


def test_an_unfiltered_request_with_a_valid_param_still_succeeds(client, conn):
    # source: api-Z1.md §5.2 -- the counterpart of the refusals above: a valid
    # filter on a type it applies to is accepted, so the table is not simply
    # refusing everything.
    build(conn)

    for path in ("/api/v1/tracks?explicit=1", "/api/v1/artists?q=al", "/api/v1/albums?year_from=2020",
                 "/api/v1/songs?sort=-tenure,name", "/api/v1/tracks?tz=UTC&from=2025-01-01"):
        assert client.get(path).status_code == 200, path


def test_a_miss_is_a_404_and_a_page_past_the_end_is_empty(client, conn):
    # source: api-Z1.md §5.2 -- "a page past the end returns items: [], not a
    # 404. A miss is a 404 (not_found)."
    build(conn)

    miss = client.get("/api/v1/tracks/nope")
    past = client.get("/api/v1/tracks?page=99")

    assert miss.status_code == 404 and miss.get_json()["error"] == "not_found"
    assert past.status_code == 200
    assert past.get_json()["items"] == [] and past.get_json()["total"] == 4


# -- Clause 17: never the round-trip --------------------------------------------------------------------------


def test_v1_never_reaches_the_round_trip(app):
    # source: api-Z1.md §3.2 / Tests 17 -- "api.py and resources.py do not
    # import roundtrip, and no rule in the api Blueprint names it. This is
    # deliberately static, because the clause *is* a statement about the code's
    # reach."
    for name in ("api.py", "resources.py"):
        tree = ast.parse(open(os.path.join(REPO, name)).read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        assert "roundtrip" not in imported, f"{name} imports roundtrip"

    rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("api.")]
    assert len(rules) > 20  # the scan sees the Blueprint at all
    assert not [r for r in rules if "roundtrip" in r.rule or "roundtrip" in r.endpoint]
    assert roundtrip.__name__ == "roundtrip"  # the module exists to be avoided


# -- Clause 18: no CORS -------------------------------------------------------------------------------------------


def test_no_v1_response_carries_a_cors_header(client, conn):
    # source: api-Z1.md §4.2 / Tests 18 -- "no v1 response carries
    # Access-Control-Allow-Origin, including when the request sends an Origin."
    build(conn)
    for path in ("/api/v1/", "/api/v1/tracks", "/api/v1/status", "/api/v1/tracks/nope"):
        resp = client.get(path, headers={"Origin": "http://evil.example"})
        assert not [h for h in resp.headers.keys() if h.lower().startswith("access-control-")], path


# -- Clause 19: the index is complete both ways ----------------------------------------------------------------------


def test_the_index_lists_exactly_the_blueprints_routes(client, app):
    # source: api-Z1.md §5.4 / Tests 19 -- "Every api rule in app.url_map
    # appears in GET /api/v1/ and vice versa. Every entry has a non-empty doc.
    # Every declared param appears in its entry."
    import api

    index = client.get("/api/v1/").get_json()

    registered = {
        (method, rule.rule.replace("<int:", "<"))
        for rule in app.url_map.iter_rules() if rule.endpoint.startswith("api.")
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    listed = {(e["method"], e["path"]) for e in index["endpoints"]}
    assert listed == registered
    assert len(listed) > 20

    for entry in index["endpoints"]:
        assert entry["doc"].strip(), entry["path"]
    by_path = {(e["method"], e["path"]): e for e in index["endpoints"]}
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("api."):
            continue
        name = rule.endpoint.split(".", 1)[1]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            entry = by_path[(method, rule.rule.replace("<int:", "<"))]
            assert [p["name"] for p in entry["params"]] == [p.name for p in api._PARAMS[name]]
    assert set(index["shapes"]) == set(resources.TYPES)
    assert index["defaults"] == {"tz": "America/Toronto", "per_page": 50, "max_per_page": 200}
