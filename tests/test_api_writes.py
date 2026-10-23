"""The three writes, generations and status (docs/specs/api-Z1.md §9.3, §10,
§11; its ## Tests clause 16).
"""

import pytest

import db
import generations
import scoring
from api_lib import build, drop_strays
from builders import make_generation, make_membership, make_play, make_playlist, make_track


def get(client, path, status=200):
    resp = client.get(path)
    assert resp.status_code == status, resp.get_data(as_text=True)
    return resp.get_json()


def fresh_conn():
    """A second connection, to see only what the first has committed."""
    return db.connect()


# -- Clause 16: the writes -------------------------------------------------------------------------------


def test_exclude_flips_the_flag_and_returns_the_summary(client, conn):
    # source: api-Z1.md §11 / Tests 16 -- "Exclude flips snapshot.excluded."
    make_playlist(conn, "pl-1", "One")

    on = client.patch("/api/v1/playlists/pl-1", json={"excluded": True})
    mid = conn.execute("SELECT excluded FROM snapshot WHERE playlist_id = 'pl-1'").fetchone()[0]
    off = client.patch("/api/v1/playlists/pl-1", json={"excluded": False})

    assert on.status_code == 200 and on.get_json()["excluded"] is True and on.get_json()["id"] == "pl-1"
    assert mid == 1
    assert off.get_json()["excluded"] is False
    assert conn.execute("SELECT excluded FROM snapshot WHERE playlist_id = 'pl-1'").fetchone()[0] == 0


@pytest.mark.parametrize(
    "body, slug",
    [
        ({"excluded": "yes"}, "invalid_parameter"),
        ({"excluded": 1}, "invalid_parameter"),
        ({}, "invalid_parameter"),
        ({"excluded": True, "name": "x"}, "unknown_parameter"),
        ([True], "invalid_parameter"),
    ],
)
def test_exclude_accepts_only_a_boolean_excluded_field(client, conn, body, slug):
    # source: api-Z1.md §11 -- `{"excluded": bool}` "(the only accepted field)";
    # a bad body is a 400 in the §5.2 shape and changes nothing.
    make_playlist(conn, "pl-1", "One")

    resp = client.patch("/api/v1/playlists/pl-1", json=body)

    assert resp.status_code == 400 and resp.get_json()["error"] == slug
    assert conn.execute("SELECT excluded FROM snapshot WHERE playlist_id = 'pl-1'").fetchone()[0] == 0


def test_a_write_without_a_json_body_or_for_a_missing_playlist_is_refused(client, conn):
    # source: api-Z1.md §5.2 -- errors keep the {"error", "detail"} shape; a
    # miss is a 404.
    make_playlist(conn, "pl-1", "One")

    no_body = client.patch("/api/v1/playlists/pl-1", data="excluded=1")
    missing = client.patch("/api/v1/playlists/nope", json={"excluded": True})

    assert no_body.status_code == 400
    assert missing.status_code == 404 and missing.get_json()["error"] == "not_found"


def test_pinning_a_track_from_another_song_is_a_400_and_changes_nothing(client, conn):
    # source: api-Z1.md §11 / Tests 16 -- "Pin with a track from another song ->
    # 400, and no row changed." pin_representative derives the song from the
    # track, so without the route's own check this would pin the *other* song.
    lib = build(conn)
    aard, zeb = lib["groups"]["aard"]["song"], lib["groups"]["zeb"]["song"]
    before = conn.execute("SELECT id, representative_track_id FROM canonical_group ORDER BY id").fetchall()

    resp = client.put(f"/api/v1/songs/{zeb}/representative", json={"track_id": "t-aard"})

    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_parameter"
    after = conn.execute("SELECT id, representative_track_id FROM canonical_group ORDER BY id").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert conn.execute("SELECT representative_track_id FROM canonical_group WHERE id = ?", (aard,)).fetchone()[0] is None


def test_pinning_a_member_sets_the_representative_and_returns_the_song(client, conn, recompute_calls):
    # source: api-Z1.md §11 -- "PUT /songs/<id>/representative {track_id} ...
    # returns the song's summary".
    lib = build(conn)
    zeb = lib["groups"]["zeb"]["song"]
    assert client.get(f"/api/v1/songs/{zeb}").get_json()["representative_track_id"] == "t-zeb"

    resp = client.put(f"/api/v1/songs/{zeb}/representative", json={"track_id": "t-zeb-live"})

    assert resp.status_code == 200
    assert resp.get_json()["representative_track_id"] == "t-zeb-live"
    assert resp.get_json()["name"] == "Zebra (Live)"
    assert client.get(f"/api/v1/songs/{zeb}").get_json()["pinned"] is True
    assert client.put("/api/v1/songs/999999/representative", json={"track_id": "t-zeb"}).status_code == 404


def test_pin_commits_before_it_asks_for_the_recompute(client, conn, monkeypatch):
    # source: api-Z1.md §11 / Tests 16 -- "Pin commits before request_recompute
    # is called" (async-recompute-N.md §4.1: the worker reads through its own
    # connection, so an uncommitted pin would be invisible to it).
    lib = build(conn)
    zeb = lib["groups"]["zeb"]["song"]
    seen = []

    def spy():
        other = fresh_conn()
        try:
            seen.append(other.execute(
                "SELECT representative_track_id FROM canonical_group WHERE id = ?", (zeb,)
            ).fetchone()[0])
        finally:
            other.close()

    monkeypatch.setattr(scoring, "request_recompute", spy)
    # The read backstop enqueues on any request while the (stubbed) recompute
    # has not run; take it out so only the pin's own request is seen.
    monkeypatch.setattr(scoring, "ensure_fresh", lambda: None)

    client.put(f"/api/v1/songs/{zeb}/representative", json={"track_id": "t-zeb-live"})

    assert seen == ["t-zeb-live"]


# -- Confirm / decline -----------------------------------------------------------------------------------------


@pytest.fixture
def pending(conn):
    """Generation 37 exists; a playlist named v38.0.0 is the undeclared next."""
    make_generation(conn, 37)
    make_playlist(conn, "pl-new", "v38.0.0")
    return "pl-new"


def test_pending_reports_the_prompt_or_null(client, conn, pending):
    # source: api-Z1.md §9.3 -- "GET /generations/pending:
    # generations.pending_new_generation() as {playlist: <summary>, name}, or
    # null."
    got = get(client, "/api/v1/generations/pending")

    assert got["name"] == "v38.0.0" and got["playlist"]["id"] == pending
    assert got["playlist"]["type"] == "playlist"

    conn.execute("UPDATE snapshot SET generation_declined = 1 WHERE playlist_id = 'pl-new'")
    conn.commit()
    assert get(client, "/api/v1/generations/pending") is None


def test_confirm_recomputes_inline_after_committing(client, conn, pending, monkeypatch):
    # source: api-Z1.md §11 / Tests 16 -- "Confirm runs scoring.recompute
    # inline"; N §4.1 -- commit first, then recompute.
    seen = []

    def spy(c):
        other = fresh_conn()
        try:
            seen.append(other.execute("SELECT ordinal FROM generation WHERE playlist_id = 'pl-new'").fetchone())
        finally:
            other.close()

    monkeypatch.setattr(scoring, "recompute", spy)

    resp = client.post("/api/v1/generations/pending/confirm", json={"playlist_id": pending})

    assert resp.status_code == 200
    assert [tuple(r) for r in seen] == [(38,)]
    assert [g["ordinal"] for g in resp.get_json()["items"]] == [37, 38]
    assert get(client, "/api/v1/generations/pending") is None


def test_decline_does_not_recompute_and_mutes_the_prompt(client, conn, pending, monkeypatch, recompute_calls):
    # source: api-Z1.md §11 / Tests 16 -- "decline doesn't (spy)": declining
    # touches nothing scoring reads.
    inline = []
    monkeypatch.setattr(scoring, "recompute", lambda c: inline.append(1))
    monkeypatch.setattr(scoring, "ensure_fresh", lambda: None)  # the backstop would enqueue too

    resp = client.post("/api/v1/generations/pending/decline", json={"playlist_id": pending})

    assert resp.status_code == 200
    assert inline == [] and recompute_calls == []
    assert conn.execute("SELECT generation_declined FROM snapshot WHERE playlist_id = 'pl-new'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM generation").fetchone()[0] == 1
    assert get(client, "/api/v1/generations/pending") is None


@pytest.mark.parametrize("answer", ["confirm", "decline"])
def test_answering_the_wrong_playlist_or_nothing_pending_is_a_400(client, conn, answer):
    # source: api-Z1.md §11 -- "a playlist_id that isn't the pending one is a
    # 400, and so is a call with nothing pending."
    make_generation(conn, 37)
    make_playlist(conn, "pl-other", "Not A Generation")
    url = f"/api/v1/generations/pending/{answer}"

    nothing = client.post(url, json={"playlist_id": "pl-other"})
    assert nothing.status_code == 400

    make_playlist(conn, "pl-new", "v38.0.0")
    wrong = client.post(url, json={"playlist_id": "pl-other"})
    assert wrong.status_code == 400 and wrong.get_json()["error"] == "invalid_parameter"
    assert conn.execute("SELECT COUNT(*) FROM generation").fetchone()[0] == 1
    assert conn.execute("SELECT generation_declined FROM snapshot WHERE playlist_id = 'pl-new'").fetchone()[0] == 0


# -- Generations (§9.3) ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def two_generations(conn):
    """Generation 1 holds Aardvark and Zebra; generation 2 holds Zebra and half-alive."""
    build(conn)
    make_generation(conn, 1)
    make_generation(conn, 2)
    p1, p2 = (conn.execute("SELECT playlist_id FROM generation WHERE ordinal = ?", (o,)).fetchone()[0] for o in (1, 2))
    make_membership(conn, p1, "t-aard", added_at="2025-01-01T00:00:00Z")
    make_membership(conn, p1, "t-zeb", added_at="2025-01-02T00:00:00Z")
    make_membership(conn, p2, "t-zeb", added_at="2025-06-01T00:00:00Z")
    make_membership(conn, p2, "t-half", added_at="2025-06-02T00:00:00Z")
    return p1, p2


def test_the_generation_list_carries_the_counts_generations_py_derives(client, conn, two_generations):
    # source: api-Z1.md §9.3 -- "{ordinal, name, playlist, started_at, ended_at,
    # version_count, song_count, carried, new}. ended_at is null for the active
    # generation. The counts are the ones generations.generations() derives."
    p1, p2 = two_generations
    expected = generations.generations(conn, tier="version")

    listing = get(client, "/api/v1/generations")

    assert listing["total"] == 2
    first, second = listing["items"]
    assert (first["ordinal"], first["playlist"]["id"]) == (1, p1)
    assert first["started_at"] == "2025-01-01T00:00:00Z" and first["ended_at"] == "2025-06-01T00:00:00Z"
    assert second["ended_at"] is None
    assert (first["version_count"], first["song_count"]) == (2, 2)
    assert (first["carried"], first["new"]) == (expected[0]["carried_in"], expected[0]["new_in"]) == (0, 2)
    assert (second["carried"], second["new"]) == (1, 1)  # Zebra carried, half-alive new


def test_the_generation_tier_param_changes_carried_and_new_only(client, conn):
    # source: Finn's call in the Z1 implement session -- "add the tier param, but
    # make it default to version": it counts carried/new at that tier while
    # version_count / song_count stay fixed. Generation 1 holds one version of
    # "Zebra" and generation 2 holds the *other* version of the same song, so
    # the two tiers disagree about whether it was carried.
    build(conn)
    make_generation(conn, 1)
    make_generation(conn, 2)
    p1, p2 = (conn.execute("SELECT playlist_id FROM generation WHERE ordinal = ?", (o,)).fetchone()[0] for o in (1, 2))
    make_membership(conn, p1, "t-zeb", added_at="2025-01-01T00:00:00Z")
    make_membership(conn, p2, "t-zeb-live", added_at="2025-06-01T00:00:00Z")

    by_version = get(client, "/api/v1/generations")["items"][1]
    by_song = get(client, "/api/v1/generations?tier=song")["items"][1]

    assert (by_version["carried"], by_version["new"]) == (0, 1)
    assert (by_song["carried"], by_song["new"]) == (1, 0)
    assert (by_version["version_count"], by_version["song_count"]) == (1, 1)
    assert (by_song["version_count"], by_song["song_count"]) == (1, 1)
    assert get(client, "/api/v1/generations?tier=nope", status=400)["error"] == "invalid_parameter"


def test_a_generation_detail_labels_each_group_carried_or_new(client, conn, two_generations):
    # source: api-Z1.md §9.3 -- "one entry per group present, each {group,
    # label}, where label is carried or new, ordered by score descending. Also
    # survived_out, null for the newest generation. Same carried/new rule as
    # generations.generation_view."
    two = get(client, "/api/v1/generations/2")
    one = get(client, "/api/v1/generations/1")

    assert [(i["group"]["name"], i["label"]) for i in two["items"]] == [("Zebra", "carried"), ("half•alive", "new")]
    assert [(i["group"]["name"], i["label"]) for i in one["items"]] == [("Zebra", "new"), ("Aardvark", "new")]
    assert two["survived_out"] is None  # the newest generation has no next
    assert one["survived_out"] == 1  # only Zebra survives into generation 2
    assert {i["group"]["type"] for i in one["items"]} == {"version"}
    assert get(client, "/api/v1/generations/2?tier=song")["items"][0]["group"]["type"] == "song"
    assert get(client, "/api/v1/generations/99", status=404)["error"] == "not_found"

    # the same rule as the playlist page's overlay, on the same tracks
    view = generations.generation_view(conn, 2, "version", ["t-zeb", "t-half"])
    assert view["label_by_track"] == {"t-zeb": "carried", "t-half": "new"}


# -- Status (§10) -------------------------------------------------------------------------------------------------------------------


def test_status_reports_freshness_and_library_counts(client, conn, fake_spotify):
    # source: api-Z1.md §10 -- the documented fields, each from its documented
    # source; "holds no tokens ... and no error tracebacks".
    build(conn)
    make_playlist(conn, "pl-1", "One", tracks_pulled_at="2026-06-01T00:00:00Z")
    make_playlist(conn, "pl-2", "Gone", unfollowed_at="2026-01-01T00:00:00Z")
    make_play(conn, "t-aard", ts="2025-03-01T00:00:00Z")
    make_play(conn, "t-half", ts="2025-04-01T00:00:00Z", source="scrobble")
    conn.execute("INSERT INTO play_import (kind, folder, uploaded_at) VALUES ('upload', 'f', '2026-05-01T00:00:00Z')")
    conn.execute("INSERT INTO play_import (kind, folder, uploaded_at, error) VALUES ('upload', 'f', '2026-06-01T00:00:00Z', 'bad')")
    conn.execute("INSERT INTO api_request (ts, host, method, path) VALUES ('2026-06-15T11:00:00Z', 'api.spotify.com', 'GET', '/v1/x')")
    conn.execute("INSERT INTO api_request (ts, host, method, path) VALUES ('2026-06-15T11:00:00Z', 'accounts.spotify.com', 'POST', '/api/token')")
    conn.execute("INSERT INTO scrobble_poll (started_at) VALUES ('2026-06-15T10:00:00Z')")
    conn.commit()
    drop_strays(conn)

    status = get(client, "/api/v1/status")

    assert status["spotify_authenticated"] is True
    assert status["active_job"] is None
    assert status["last_pull_at"] == "2026-06-15T12:00:00Z"  # the newest tracks_pulled_at: pl-2's default (now)
    assert status["plays"] == {"data_through": "2025-04-01T00:00:00Z", "export_through": "2025-03-01T00:00:00Z",
                               "last_import_at": "2026-05-01T00:00:00Z"}  # the failed import is not "last"
    assert status["scrobble"] == {"enabled": True, "last_poll_at": "2026-06-15T10:00:00Z", "next_poll_at": None}
    assert status["spotify_requests"] == {"past_24h": 1, "past_7d": 1}  # the token refresh is not counted
    assert status["library"] == {
        "tracks": 4, "albums": 3, "artists": 3, "playlists": 1, "songs": 3, "versions": 4,
        "recordings": 4, "releases": 4, "plays": 2, "generations": 0,
    }
    assert set(status["scoring"]) == {"last_recompute_at", "outcome"}
    assert status["pending_generation"] is None


def test_status_reports_the_pollers_next_wake_when_one_runs(client, monkeypatch):
    # source: api-Z1.md §10 -- "scrobble.next_poll_at: scrobble._next_wake_at
    # through a small public accessor. null means no poller in this process."
    import scrobble

    monkeypatch.setattr(scrobble, "_next_wake_at", "2026-06-15T13:40:00Z")

    assert get(client, "/api/v1/status")["scrobble"]["next_poll_at"] == "2026-06-15T13:40:00Z"
    assert scrobble.next_wake_at() == "2026-06-15T13:40:00Z"


def test_status_scrobble_flags_follow_the_meta_switch(client, conn):
    # source: api-Z1.md §10 -- scrobble.enabled mirrors scrobble_enabled = '0'.
    db.set_meta(conn, "scrobble_enabled", "0")
    conn.commit()

    assert get(client, "/api/v1/status")["scrobble"]["enabled"] is False


# -- The hooks -----------------------------------------------------------------------------------------------------------------------


def test_a_cross_origin_v1_write_is_still_refused(client, conn):
    # source: api-Z1.md §4.2 -- "the reject_cross_origin_writes hook already
    # covers every non-safe method on every route, v1 included."
    make_playlist(conn, "pl-1", "One")

    resp = client.patch("/api/v1/playlists/pl-1", json={"excluded": True},
                        headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 403
    assert conn.execute("SELECT excluded FROM snapshot WHERE playlist_id = 'pl-1'").fetchone()[0] == 0


def test_every_v1_request_does_the_grouping_catch_up(client, conn):
    # source: api-Z1.md §2.3 -- "The Blueprint does the same in one
    # api.before_request hook, for every v1 request, so no endpoint can forget
    # it." A track with no track_group row is grouped by an unrelated endpoint.
    make_track(conn, "t-new", "New")
    assert conn.execute("SELECT COUNT(*) FROM track_group").fetchone()[0] == 0

    client.get("/api/v1/status")

    assert conn.execute("SELECT COUNT(*) FROM track_group WHERE track_id = 't-new'").fetchone()[0] == 1
