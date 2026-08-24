"""Error paths assert their **exact** status code, not merely non-5xx.

`test_routes.py`'s permanent sweep asserts every route returns non-5xx, which
is the right shape for a completeness sweep and the wrong shape for an error
path: **a 400 mutated to a 401 is still non-5xx**, so the sweep passes and the
code goes unasserted. The S sweep found 26 such survivors in `app.py`
(`S_sweep.md` §3.3) -- the P2-010 shape the spec predicted for that module.

The sweep cannot be widened to cover them, because these paths are not in
`routes_catalog.py` at all: the catalog issues *valid* requests, and every one
of these aborts needs a deliberately malformed one. So this file supplements
rather than replaces it.

Each case asserts the code **and** the description, following the precedent
`CLAUDE.md` sets for `/callback`: where several distinct refusals share one
status, the code alone cannot tell a working guard from a deleted one.
"""

import pytest

import builders


@pytest.mark.parametrize(
    "method, path, kwargs, status, fragment",
    [
        # source: app.py canonical_group_deep_link -- an id with no
        # canonical_group row is a 404, distinct from the "exists but empty"
        # 404 below it, which is why the description is asserted too.
        ("get", "/dev/canonical/group/98765", {}, 404, "No such canonical group."),
        # source: app.py canonical_review -- an ad-hoc selection needs at least
        # two ids to be a grouping at all.
        ("get", "/dev/canonical/review?tracks=onlyone", {}, 400,
         "tracks= needs at least 2 track ids"),
        # source: app.py dev_generations_confirm -- the decision is a closed
        # yes/no set; anything else is a malformed submission, not a "no".
        ("post", "/dev/generations/confirm", {"data": {"playlist_id": "p1"}}, 400,
         "playlist_id and a yes/no decision are required"),
        ("post", "/dev/generations/confirm",
         {"data": {"playlist_id": "p1", "decision": "maybe"}}, 400,
         "playlist_id and a yes/no decision are required"),
        # source: app.py exclude_snapshot_playlists -- an empty list is
        # rejected rather than silently excluding nothing.
        ("post", "/api/snapshot/exclude", {"json": {"playlist_ids": []}}, 400,
         "playlist_ids required"),
        # source: S_sweep.md §3.4 A -- api_canonical_cross's tracks= param
        # needs at least two ids that are actually known tracks; an unknown id
        # does not count toward the minimum.
        ("get", "/api/canonical/cross?tracks=unknown-1,unknown-2", {}, 400,
         "tracks= needs at least 2 known track ids"),
        # source: S_sweep.md §3.4 A -- api_history_import requires the file
        # field to be present at all; no upload is the same refusal as an
        # empty filename.
        ("post", "/api/history/import", {"data": {}}, 400,
         "A .zip export file is required."),
        # source: S_sweep.md §3.4 A -- and rejects an unwrapped export (the
        # Streaming_History_*.json files themselves, not the zip around them).
        ("post", "/api/history/import",
         {"data": {"file": (__import__("io").BytesIO(b"[]"), "Streaming_History_0.json")}},
         400, "Upload the export .zip itself, not its contents."),
        # source: S_sweep.md §3.4 A -- reimport needs a prior upload folder to
        # re-run against; the very first call has none.
        ("post", "/api/history/reimport", {}, 400,
         "Nothing uploaded yet"),
        # source: S_sweep.md §3.4 A -- aliases must be a non-empty list of
        # {requested_uri, track_id} pairs; an empty list is rejected outright
        # rather than silently aliasing nothing.
        ("post", "/api/roundtrip/alias", {"json": {"aliases": []}}, 400,
         "aliases must be a non-empty list of"),
        # source: S_sweep.md §3.4 A -- wanted-uri source is a closed
        # {"album", "backfill"} set, not an arbitrary client-supplied string.
        ("post", "/api/roundtrip/wanted/clear", {"json": {"source": "bogus"}}, 400,
         "source must be one of"),
        # source: S_sweep.md §3.4 A -- artist alias/unmerge need both ids /
        # the one id respectively.
        ("post", "/api/artists/alias", {"json": {"artist_id_a": "a1"}}, 400,
         "artist_id_a and artist_id_b required"),
        ("post", "/api/artists/unmerge", {"json": {}}, 400, "artist_id required"),
    ],
)
def test_error_paths_return_their_exact_status_and_reason(
    client, method, path, kwargs, status, fragment
):
    # source: S_sweep.md §3.3 -- every one of these abort() codes survived
    # mutation because the only assertion covering the route was "non-5xx".
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == status
    assert fragment in resp.get_data(as_text=True)


def test_canonical_group_deep_link_404s_for_a_group_with_no_members(client, conn):
    # source: S_sweep.md §3.4 A -- distinct from the "no such group" 404
    # above: this id names a real canonical_group row, but nothing in
    # track_group points at it, which is a state group_members() has to
    # detect on its own rather than getting it for free from the row's
    # existence.
    cur = conn.execute(
        "INSERT INTO canonical_group (tier, representative_track_id) VALUES ('song', NULL)"
    )
    conn.commit()
    empty_group_id = cur.lastrowid

    resp = client.get(f"/dev/canonical/group/{empty_group_id}")

    assert resp.status_code == 404
    assert "Group has no members." in resp.get_data(as_text=True)


def test_cross_apply_rejects_a_newcomer_with_no_track_group_row(client, conn):
    # source: S_sweep.md §3.4 A -- api_canonical_cross_apply's per-target
    # groups_for_track() check. ensure_track_groups() gives every real track a
    # track_group row, so this only fires for an id that names no track at
    # all -- which is exactly the malformed-request case this route has to
    # refuse rather than crash on.
    builders.make_track(conn, "t1")
    bogus = "not-a-real-track"

    resp = client.post(
        "/api/canonical/cross/apply",
        json={
            "track_ids": ["t1", bogus],
            "assignments": [{"track_ids": ["t1", bogus]}],
        },
    )

    assert resp.status_code == 400
    assert f"no track_group row for {bogus}" in resp.get_data(as_text=True)


def test_cross_apply_wraps_a_nested_consistency_violation_as_400(client, conn):
    # source: S_sweep.md §3.4 C -- apply_partition's _validate_labels raises
    # ValueError when one recording group's members already span two
    # different version groups, which the route has to catch and report
    # rather than let bubble up as a 500. Built by pinning two tracks to the
    # same recording group while leaving their version/song/release groups
    # separate -- a state only a fixture creates directly, since the engine's
    # own writes never produce it.
    g1 = builders.make_group(conn, ["trackA"])
    builders.make_group(conn, ["trackB"], recording=g1["recording"])

    resp = client.post(
        "/api/canonical/cross/apply",
        json={
            "track_ids": ["trackA", "trackB"],
            "assignments": [{"track_ids": ["trackA", "trackB"]}],
        },
    )

    assert resp.status_code == 400
    assert "not nested-consistent" in resp.get_data(as_text=True)


def test_canonical_apply_wraps_incomplete_labels_as_400(client, conn):
    # source: S_sweep.md §3.4 C -- api_canonical_apply passes the request
    # body's labels straight to apply_partition; a label missing one of the
    # four tiers is _validate_labels' first check, and the route's job is to
    # turn that ValueError into a 400 rather than a 500.
    builders.make_track(conn, "t1")

    resp = client.post(
        "/api/canonical/apply",
        json={
            "track_ids": ["t1"],
            "labels": {"t1": {"song": "s", "version": "v", "recording": "r"}},
        },
    )

    assert resp.status_code == 400
    assert "missing label(s) for tier(s)" in resp.get_data(as_text=True)


def test_pin_representative_wraps_an_unknown_track_as_400(client):
    # source: S_sweep.md §3.4 C -- pin_representative raises ValueError for a
    # track with no track_group row at all; api_canonical_pin has to catch
    # that rather than 500.
    resp = client.post("/api/canonical/pin", json={"track_id": "no-such-track"})

    assert resp.status_code == 400
    assert "no track_group row for track no-such-track" in resp.get_data(as_text=True)


def test_generations_confirm_wraps_an_unknown_playlist_as_400(client):
    # source: S_sweep.md §3.4 C -- confirm_generation raises ValueError when
    # there is no snapshot row for the playlist id, which the route has to
    # turn into a 400 rather than a 500 -- a playlist_id can be typed into the
    # hidden form field just as easily as any other.
    resp = client.post(
        "/dev/generations/confirm",
        data={"playlist_id": "no-such-playlist", "decision": "yes"},
    )

    assert resp.status_code == 400
    assert "no snapshot row for playlist no-such-playlist" in resp.get_data(as_text=True)
