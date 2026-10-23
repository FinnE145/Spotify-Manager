"""Every field the v1 representations produce is read by a test
(docs/specs/api-Z1.md §5.1, §6.1, §6.2, §9, §10).

Coverage cannot see an unread field -- a stored or returned value nothing
asserts executes exactly as if it were right -- so each summary and a few
details are pinned whole, with expected values written from the fixture and the
spec's tables, not read back out of the response.
"""

import json

import jobs
import scoring
from api_lib import build, drop_strays
from builders import (
    make_generation, make_membership, make_play, make_playlist, make_track,
)


def get(client, path, status=200):
    resp = client.get(path)
    assert resp.status_code == status, resp.get_data(as_text=True)
    return resp.get_json()


def _bools(*values):
    """`1 == True` in Python, so a dict comparison passes for a SQLite 0/1 that
    was never converted; the spec says JSON booleans (§5.1)."""
    assert all(type(v) is bool for v in values), values


def test_a_track_summary_is_exactly_the_documented_fields(client, conn):
    # source: api-Z1.md §6.1 table -- track: spotify_url, artists [{id, name}]
    # in credit order, album {id, name, url}, duration_ms, explicit, cover_url,
    # score; plus type, id, name, url (§5.1).
    build(conn)

    track = get(client, "/api/v1/tracks?q_full=aardvark")["items"][0]
    del track["metrics"]

    _bools(track["explicit"])
    assert track == {
        "type": "track", "id": "t-aard", "name": "Aardvark", "url": "/track/t-aard",
        "spotify_url": "https://open.spotify.com/track/t-aard",
        "artists": [{"id": "ar-beta", "name": "Beta"}],
        "album": {"id": "al-a", "name": "Alpha Album", "url": "/album/al-a"},
        "duration_ms": 200000, "explicit": True,
        "cover_url": "https://i.scdn.co/image/al-a",
        "score": {"all_time": 10.0, "recent": 5.0},
    }


def test_credits_keep_their_order_and_a_missing_album_is_null(client, conn):
    # source: api-Z1.md §6.1 -- artists "in credit order"; "Keys are always
    # present. Unknown means null". "Zebra" credits Alpha then the Beta alias.
    build(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("UPDATE track SET album_id = NULL WHERE track_id = 't-half'")
    conn.commit()

    zebra = get(client, "/api/v1/tracks/t-zeb")
    half = get(client, "/api/v1/tracks/t-half")

    assert [a["name"] for a in zebra["artists"]] == ["Alpha", "Beta"]
    assert half["album"] is None and half["cover_url"] is None


def test_a_group_summary_is_exactly_the_documented_fields(client, conn):
    # source: api-Z1.md §6.1 table -- song / version / recording / release: the
    # representative track's artists, album, duration_ms, cover_url;
    # representative_track_id, member_count, score. A group has no spotify_url.
    lib = build(conn)
    zeb = lib["groups"]["zeb"]

    for tier in ("song", "version", "recording", "release"):
        group = get(client, f"/api/v1/{tier}s/{zeb[tier]}")
        summary = {k: group[k] for k in (
            "type", "id", "name", "url", "artists", "album", "duration_ms", "cover_url",
            "representative_track_id", "member_count", "score")}
        assert group["type"] == tier and group["url"] == f"/{tier}/{zeb[tier]}"
        assert "spotify_url" not in group
        assert summary["artists"] == [{"id": "ar-alpha", "name": "Alpha"}, {"id": "ar-beta", "name": "Beta"}]
        assert summary["album"] == {"id": "al-z", "name": "Zed Album", "url": "/album/al-z"}
        assert (summary["name"], summary["duration_ms"], summary["representative_track_id"]) == ("Zebra", 300000, "t-zeb")
        assert summary["cover_url"] == "https://i.scdn.co/image/al-z"
    song = get(client, f"/api/v1/songs/{zeb['song']}")
    version = get(client, f"/api/v1/versions/{zeb['version']}")
    assert (song["member_count"], version["member_count"]) == (2, 1)
    assert version["score"] == {"all_time": 90.0, "recent": 85.0}


def test_an_album_summary_is_exactly_the_documented_fields(client, conn):
    # source: api-Z1.md §6.1 table -- album: spotify_url, artists, album_type,
    # release_date, release_date_precision, total_tracks, owned_count,
    # cover_url, score.
    build(conn)

    album = get(client, "/api/v1/albums?q_full=alpha%20album")["items"][0]
    del album["metrics"]

    assert {k: v for k, v in album.items() if k != "score"} == {
        "type": "album", "id": "al-a", "name": "Alpha Album", "url": "/album/al-a",
        "spotify_url": "https://open.spotify.com/album/al-a",
        "artists": [{"id": "ar-beta", "name": "Beta"}],
        "album_type": "album", "release_date": "2023-02-01", "release_date_precision": "day",
        "total_tracks": 3, "owned_count": 2,
        "cover_url": "https://i.scdn.co/image/al-a",
    }
    assert album["score"] is not None and album["score"]["all_time"] > 0


def test_an_artist_summary_is_exactly_the_documented_fields(client, conn):
    # source: api-Z1.md §6.1 table -- artist: spotify_url, image_url (null
    # until fetched), score.
    build(conn)
    conn.execute("UPDATE artist SET image_url = 'https://img/alpha' WHERE artist_id = 'ar-alpha'")
    conn.commit()

    alpha = get(client, "/api/v1/artists?q_full=alpha")["items"][0]
    beta = get(client, "/api/v1/artists?q_full=beta")["items"][0]

    assert {k: alpha[k] for k in alpha if k not in ("metrics", "score")} == {
        "type": "artist", "id": "ar-alpha", "name": "Alpha", "url": "/artist/ar-alpha",
        "spotify_url": "https://open.spotify.com/artist/ar-alpha", "image_url": "https://img/alpha",
    }
    assert beta["image_url"] is None
    assert set(alpha) == {"type", "id", "name", "url", "spotify_url", "image_url", "score", "metrics"}


def test_a_playlist_summary_is_exactly_the_documented_fields(client, conn):
    # source: api-Z1.md §6.1 table -- playlist: spotify_url, owner, cover_url,
    # live_count, excluded, is_liked, generation (ordinal or null), score.
    build(conn)
    make_generation(conn, 4)
    gen_playlist = conn.execute("SELECT playlist_id FROM generation").fetchone()[0]
    make_playlist(conn, "pl-1", "One", owner="finn", image_url="https://img/pl", excluded=1)
    make_membership(conn, "pl-1", "t-aard")
    make_membership(conn, "pl-1", "t-half")
    make_membership(conn, "pl-1", "t-zeb", removed_at="2025-01-01T00:00:00Z")
    drop_strays(conn)

    one = get(client, "/api/v1/playlists/pl-1")
    gen = get(client, f"/api/v1/playlists/{gen_playlist}")

    assert {k: one[k] for k in ("type", "id", "name", "url", "spotify_url", "owner", "cover_url",
                                "live_count", "excluded", "is_liked", "generation")} == {
        "type": "playlist", "id": "pl-1", "name": "One", "url": "/playlist/pl-1",
        "spotify_url": "https://open.spotify.com/playlist/pl-1", "owner": "finn",
        "cover_url": "https://img/pl", "live_count": 2, "excluded": True, "is_liked": False,
        "generation": None,
    }
    _bools(one["excluded"], one["is_liked"])
    assert gen["generation"]["ordinal"] == 4
    assert one["score"] is not None  # its live tracks' versions are scored


def test_a_playlist_details_own_facts(client, conn):
    # source: api-Z1.md §6.2 -- playlist: description, last_changed_at,
    # tracks_pulled_at, unfollowed_at.
    make_playlist(conn, "pl-1", "One", description="Mine", last_changed_at="2025-02-02T00:00:00Z",
                  tracks_pulled_at="2025-03-03T00:00:00Z", unfollowed_at="2025-04-04T00:00:00Z")

    detail = get(client, "/api/v1/playlists/pl-1")

    assert (detail["description"], detail["last_changed_at"], detail["tracks_pulled_at"],
            detail["unfollowed_at"]) == ("Mine", "2025-02-02T00:00:00Z", "2025-03-03T00:00:00Z",
                                         "2025-04-04T00:00:00Z")


def test_an_albums_appended_holds_owned_tracks_missing_from_the_tracklist(client, conn):
    # source: api-Z1.md §6.2 -- "appended (summaries of owned tracks on this
    # album that aren't in the stored tracklist)"; and with no tracklist at all
    # every owned track is "appended" (they are the only ones known).
    build(conn)

    unfetched = get(client, "/api/v1/albums/al-a")
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = '2026-01-01T00:00:00Z' WHERE album_id = 'al-a'",
        (json.dumps([{"id": "t-aard", "name": "Aardvark", "uri": "u", "duration_ms": 200000,
                      "track_number": 1, "disc_number": 1}]),),
    )
    conn.commit()
    fetched = get(client, "/api/v1/albums/al-a")

    assert sorted(t["id"] for t in unfetched["appended"]) == ["t-aard", "t-zeb-live"]
    assert [t["id"] for t in fetched["appended"]] == ["t-zeb-live"]
    assert [t["track"]["id"] for t in fetched["tracklist"]] == ["t-aard"]
    assert fetched["total_duration_ms"] is None  # 2 known entries of total_tracks = 3


def test_a_play_carries_every_documented_field(client, conn):
    # source: api-Z1.md §9.1 -- the full play object, including what the export
    # reported (the *album* artist) and the connection fields.
    build(conn)
    make_play(
        conn, "t-aard", ts="2025-05-05T05:05:05Z", ms_played=12345, source="export",
        reported_track_name="Aardvark!", reported_artist_name="Beta", reported_album_name="Alpha Album",
        reason_start="clickrow", reason_end="fwdbtn", shuffle=1, skipped=1, platform="ios",
        conn_country="CA", offline=1, offline_ts="2025-05-05T05:00:00Z", incognito_mode=1,
    )

    (play,) = get(client, "/api/v1/plays")["items"]

    assert play["track"]["id"] == "t-aard"
    _bools(play["shuffle"], play["skipped"], play["offline"], play["incognito_mode"])
    del play["track"], play["id"]
    assert play == {
        "ts": "2025-05-05T05:05:05Z", "ms_played": 12345, "source": "export",
        "spotify_track_uri": "spotify:track:t-aard",
        "reported": {"track_name": "Aardvark!", "artist_name": "Beta", "album_name": "Alpha Album"},
        "reason_start": "clickrow", "reason_end": "fwdbtn", "shuffle": True, "skipped": True,
        "platform": "ios", "conn_country": "CA", "offline": True, "offline_ts": "2025-05-05T05:00:00Z",
        "incognito_mode": True,
    }


def test_the_timeline_echoes_its_resolved_bounds(client, conn):
    # source: api-Z1.md §9.2 -- {"bucket", "tz", "from", "to", "buckets"}; the
    # bounds are the resolved UTC instants (a bare `to` is the next local
    # midnight), null when not given.
    build(conn)

    bounded = get(client, "/api/v1/plays/timeline?bucket=day&from=2025-03-01&to=2025-03-01")
    open_ended = get(client, "/api/v1/plays/timeline")

    assert bounded["bucket"] == "day" and bounded["tz"] == "America/Toronto"
    assert bounded["from"] == "2025-03-01T05:00:00Z" and bounded["to"] == "2025-03-02T05:00:00Z"
    assert [b["start"] for b in bounded["buckets"]] == ["2025-03-01"]
    assert (open_ended["from"], open_ended["to"], open_ended["bucket"]) == (None, None, "month")
    assert open_ended["buckets"] == []  # no plays at all


def test_status_reports_a_running_job_a_recompute_and_a_pending_generation(client, conn, monkeypatch):
    # source: api-Z1.md §10 -- active_job: jobs.active(); scoring:
    # recompute_status() without its error text; pending_generation: the §9.3
    # shape.
    make_generation(conn, 37)
    make_playlist(conn, "pl-new", "v38.0.0")
    monkeypatch.setattr(jobs, "_active", "snapshot")
    monkeypatch.setattr(scoring, "_recompute_status", {
        "started_at": "2026-06-15T11:59:00Z", "finished_at": "2026-06-15T11:59:02Z", "duration_seconds": 2.0,
        "outcome": "error", "error": "Traceback: secret detail", "counts": None,
    })

    status = get(client, "/api/v1/status")

    assert status["active_job"] == "snapshot"
    assert status["scoring"] == {"last_recompute_at": "2026-06-15T11:59:02Z", "outcome": "error"}
    assert "secret detail" not in json.dumps(status)
    assert status["pending_generation"]["name"] == "v38.0.0"
    assert status["library"]["generations"] == 1
    assert status["plays"] == {"data_through": None, "export_through": None, "last_import_at": None}
    assert status["last_pull_at"] is not None


def test_the_index_documents_defaults_shapes_and_every_params_fields(client):
    # source: api-Z1.md §5.4 -- {"version", "defaults", "shapes", "endpoints":
    # [{method, path, doc, params: [{name, type, default, doc}]}]}.
    index = get(client, "/api/v1/")

    assert index["version"] == "v1"
    assert "representative" in index["shapes"]["song"] and "owned_count" in index["shapes"]["album"]
    search = next(e for e in index["endpoints"] if e["path"] == "/api/v1/search")
    q = next(p for p in search["params"] if p["name"] == "q")
    per_page = next(p for p in search["params"] if p["name"] == "per_page")
    assert set(q) >= {"name", "type", "default", "doc", "in"} and q["required"] is True
    assert (per_page["type"], per_page["default"], per_page["in"]) == ("int", 50, "query")
    pin = next(e for e in index["endpoints"] if e["method"] == "PUT")
    assert pin["params"][0]["in"] == "body" and pin["path"] == "/api/v1/songs/<song_id>/representative"
    detail = next(e for e in index["endpoints"] if e["path"] == "/api/v1/albums/<album_id>")
    spend = next(p for p in detail["params"] if p["name"] == "spotify_request")
    assert spend["type"] == "bool" and spend["default"] is False
    assert "null" in detail["doc"] and "spotify_request" in detail["doc"]  # what null means, and the cost
