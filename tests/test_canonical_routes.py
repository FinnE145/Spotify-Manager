"""The grouping endpoints whose *rule* lives in the route body rather than in a
module.

Deliberately not a route sweep -- `P2_tests.md` §4.6's permanent
"every route returns non-5xx" layer belongs to session 4. These are the two
places a grouping decision is implemented in `app.py` itself:

- **`/api/canonical/cross/listing`** (P1-009): the split that took detection
  off `/dev/canonical`'s synchronous page load. The claim worth asserting is
  the *negative* one -- that the page no longer pays that cost.
- **`/api/canonical/cross/apply`** (M §1): the cross-component-only marking.
  `canonical_detect.cross_component_pairs` is unit-tested in
  `test_canonical_detect_queues.py`; what is only observable here is that the
  route actually calls it rather than `mark_reviewed` over the whole bucket.

Plus `async-recompute-N.md` §4.2's async call sites, which are route bodies
too. The autouse `recompute_calls` fixture records those without spawning a
worker thread.
"""

import pytest

import builders
import canonical
import canonical_detect as detect
import scoring
from test_canonical_detect_rules import OTHER_ARTIST, make


@pytest.fixture
def route_recompute_calls(monkeypatch, recompute_calls):
    """`recompute_calls` with `scoring.ensure_fresh()` silenced.

    The read backstop runs in a `before_request` hook on every request and
    **enqueues rather than recomputing inline** (`async-recompute-N.md` §5.2),
    so it lands in `recompute_calls` alongside the route's own request and a
    bare count cannot tell the two apart. The backstop is session 3's subject;
    what is being asserted here is which *route bodies* ask for a recompute,
    per §4.2's table.
    """
    monkeypatch.setattr(scoring, "ensure_fresh", lambda: None)
    return recompute_calls


def bucket(conn):
    """Two same-artist tracks and one by a different artist, sharing a base
    title -- one cross-artist bucket over two components."""
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", album="Album Two")
    make(conn, "tc", "Willow", artists=[OTHER_ARTIST], album="Album Three")
    canonical.ensure_track_groups(conn)
    conn.commit()
    return ["ta", "tb", "tc"]


def reviewed_pairs(conn):
    return {
        (row["track_id_a"], row["track_id_b"])
        for row in conn.execute("SELECT track_id_a, track_id_b FROM reviewed_pair")
    }


def pending_rows(conn):
    return {row["track_id"] for row in conn.execute("SELECT track_id FROM pending_tier_review")}


# -- The cross-listing split (P1-009) ---------------------------------------


def test_the_page_load_does_not_run_detection(conn, client, monkeypatch):
    """P1-009's real claim, asserted as the absence of a call.

    `_fetch_tracks` is the ~350ms whole-library path detection starts from.
    `/dev/canonical` still calls `pending_song_ids`, which is cheap SQL over
    `pending_tier_review` and deliberately does *not* go through it.
    """
    # source: canonical-fixes.md §2 as amended by P1-009 -- the fix "is now
    # invoked from /api/canonical/cross/listing, an async endpoint the page
    # fetches after paint, rather than from the page route synchronously";
    # and canonical_index's own comment: "Detection is deliberately absent
    # here: it cost ~350ms of this page's ~500ms".
    bucket(conn)
    calls = []
    real = detect._fetch_tracks
    monkeypatch.setattr(detect, "_fetch_tracks", lambda c: calls.append(True) or real(c))

    assert client.get("/dev/canonical").status_code == 200
    assert calls == []

    # The control: the async endpoint that took the work over does run it, so
    # the probe above is capable of seeing a call at all.
    assert client.get("/api/canonical/cross/listing").status_code == 200
    assert calls == [True]


def test_the_cross_listing_returns_what_the_pane_needs(conn, client):
    # source: api_canonical_cross_listing's docstring -- "The /dev/canonical
    # cross-artist pane, plus the two unreviewed counts that sit in its Stats
    # panel", returning "rendered HTML rather than JSON rows so the entity
    # links in it stay the same entity_link macro every other page uses".
    bucket(conn)

    body = client.get("/api/canonical/cross/listing").get_json()

    assert set(body) == {"html", "total", "unreviewed_main", "unreviewed_cross"}
    assert body["total"] == 1
    assert body["unreviewed_main"] == 1
    assert body["unreviewed_cross"] == 1
    assert "willow" in body["html"].lower()


def test_the_cross_listing_honours_the_filter(conn, client):
    # source: filter_groups' docstring -- the pane's filter is the same
    # LIKE-style match as the Groups box, and `?cross=` is what
    # canonical_viewer.js syncs via replaceState.
    bucket(conn)
    make(conn, "td", "Cardigan")
    make(conn, "te", "Cardigan", artists=[OTHER_ARTIST], album="Album Five")

    assert client.get("/api/canonical/cross/listing").get_json()["total"] == 2
    assert client.get("/api/canonical/cross/listing?cross=willow").get_json()["total"] == 1


# -- The cross-apply write (M §1) -------------------------------------------


def apply_cross(client, track_ids, assignments):
    return client.post(
        "/api/canonical/cross/apply",
        json={"track_ids": track_ids, "assignments": assignments},
    )


def test_the_route_marks_cross_component_pairs_only(conn, client):
    """M1's fix, at the site it was made.

    Answering the bucket must settle it without touching `ta`/`tb`, which the
    cross queue never asked about.
    """
    # source: M §1.2 -- "`app.py:983` then becomes
    # `canonical.mark_reviewed_pairs(conn, canonical_detect.cross_component_pairs(conn,
    # track_ids))`", and §1.4's two consequences.
    ids = bucket(conn)

    response = apply_cross(client, ids, [])

    assert response.status_code == 200
    # source: S_survivors.md app.py:563 -- {"ok": True}; grepped
    # static/js/canonical_cross.js's commit() and found no consumer of this
    # field (it branches on `result.error` only), so the fix pairs the flag
    # with the real write already asserted below rather than asserting a
    # dead field alone.
    assert response.get_json()["ok"] is True

    assert reviewed_pairs(conn) == {("ta", "tc"), ("tb", "tc")}
    assert detect.cross_buckets(conn) == []
    assert [g["track_ids"] for g in detect.candidate_groups(conn)] == [["ta", "tb"]]


def test_the_one_keypress_none_of_these_answer_still_settles(conn, client):
    # source: M §1.1 -- the cross queue is shaped so "the overwhelmingly
    # common answer, 'no, none of these are related', costs one keypress",
    # which posts no assignments at all. It must still settle the bucket.
    ids = bucket(conn)

    apply_cross(client, ids, [])

    assert canonical.groups_for_track(conn, "tc")["song"] != canonical.groups_for_track(
        conn, "ta"
    )["song"]
    assert detect.cross_buckets(conn) == []


def test_an_assignment_shares_the_song_but_keeps_the_finer_tiers(conn, client):
    """E §4.4's rule: a song-tier decision must not silently detach a newcomer
    from a finer-tier group it is already in."""
    # source: app.py's cross-apply comment, per spec E §4.4 -- "One shared song
    # label; every track keeps its existing version, recording and release
    # group. Passing the newcomer's current ids rather than fresh singletons
    # is deliberate."
    ids = bucket(conn)
    song_id = canonical.groups_for_track(conn, "ta")["song"]
    before = canonical.groups_for_track(conn, "tc")

    apply_cross(client, ids, [{"song_id": song_id, "track_ids": ["tc"]}])

    after = canonical.groups_for_track(conn, "tc")
    assert after["song"] == canonical.groups_for_track(conn, "ta")["song"]
    assert after["version"] == before["version"]
    assert after["recording"] == before["recording"]
    assert after["release"] == before["release"]


def test_an_assigned_newcomer_owes_a_tier_pass(conn, client):
    # source: E §4.5 -- `pending_tier_review` is written for the newcomers an
    # assignment moved, because the cross queue decides song tier only and the
    # finer tiers still need a pass.
    ids = bucket(conn)
    song_id = canonical.groups_for_track(conn, "ta")["song"]

    apply_cross(client, ids, [{"song_id": song_id, "track_ids": ["tc"]}])

    assert pending_rows(conn) == {"tc"}


def test_a_track_outside_the_bucket_is_rejected(conn, client):
    # source: app.py's cross-apply guard -- "The bucket is the only thing this
    # page is allowed to touch." A stale page must not be able to regroup
    # something it never displayed.
    ids = bucket(conn)
    make(conn, "tz", "Unrelated")
    song_id = canonical.groups_for_track(conn, "ta")["song"]

    response = apply_cross(client, ids, [{"song_id": song_id, "track_ids": ["tz"]}])

    assert response.status_code == 400
    assert reviewed_pairs(conn) == set()


def test_a_bucket_of_fewer_than_two_tracks_is_rejected(conn, client):
    # source: app.py's cross-apply guard -- "track_ids needs at least 2 track
    # ids"; there is no cross-artist question to answer below that.
    bucket(conn)

    assert apply_cross(client, ["ta"], []).status_code == 400


def test_an_api_error_carries_the_shared_json_shape(conn, client):
    # source: error-pages.md via P1-014 -- every /api/* error response has
    # exactly `error` and `detail`. Asserted here only for the endpoint this
    # session owns; session 4 owns the sweep across all of them.
    bucket(conn)

    body = apply_cross(client, ["ta"], []).get_json()

    assert set(body) == {"error", "detail"}


# -- Async recompute call sites (async-recompute-N.md §4.2) -----------------


def test_cross_apply_requests_an_async_recompute(conn, client, route_recompute_calls):
    # source: async-recompute-N.md §4.2's table -- /api/canonical/cross/apply
    # is async, per §4.1's rule: "Async where you are working a queue."
    ids = bucket(conn)

    apply_cross(client, ids, [])

    assert len(route_recompute_calls) == 1


def test_the_main_apply_requests_an_async_recompute(conn, client, route_recompute_calls):
    # source: async-recompute-N.md §4.2's table -- /api/canonical/apply is the
    # first async site, and the one §4.1 is written about: "worthless on a
    # keypress you make hundreds of times an hour".
    bucket(conn)
    labels = {
        track_id: {tier: "one" for tier in canonical.TIER_ORDER} for track_id in ("ta", "tb")
    }

    response = client.post(
        "/api/canonical/apply", json={"track_ids": ["ta", "tb"], "labels": labels}
    )

    assert response.status_code == 200
    assert canonical.groups_for_track(conn, "ta") == canonical.groups_for_track(conn, "tb")
    assert len(route_recompute_calls) == 1


def test_the_main_apply_clears_a_pending_tier_row(conn, client, route_recompute_calls):
    # source: app.py's comment -- "A pending tier-review item is exactly the
    # song group these tracks are in, so committing it is what the pending row
    # was waiting for."
    bucket(conn)
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('ta')")
    conn.commit()
    labels = {
        track_id: {tier: "one" for tier in canonical.TIER_ORDER} for track_id in ("ta", "tb")
    }

    client.post("/api/canonical/apply", json={"track_ids": ["ta", "tb"], "labels": labels})

    assert pending_rows(conn) == set()


def test_the_main_apply_leaves_an_unapplied_pending_row_alone(conn, client, route_recompute_calls):
    """A single pending row is not enough here: with only `ta` pending and
    two applied track_ids (`ta`, `tb`), both `DELETE ... WHERE track_id = ?`
    and its `<>` inversion end up clearing the table in two loop iterations,
    so the test above cannot distinguish them. A pending row belonging to a
    *third*, unapplied track is what the inverted query wrongly wipes out.
    """
    # source: S_survivors.md app.py:615 -- `DELETE FROM pending_tier_review
    # WHERE track_id = ?`; needs two rows, one applied and one not, to
    # distinguish `=` from `<>` (mutation-sweep-S.md's domain note, same
    # shape as round 1's canvas WHERE mutants).
    bucket(conn)
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('ta')")
    conn.execute("INSERT INTO pending_tier_review (track_id) VALUES ('tc')")
    conn.commit()
    labels = {
        track_id: {tier: "one" for tier in canonical.TIER_ORDER} for track_id in ("ta", "tb")
    }

    client.post("/api/canonical/apply", json={"track_ids": ["ta", "tb"], "labels": labels})

    assert pending_rows(conn) == {"tc"}


def test_pinning_requests_an_async_recompute(conn, client, route_recompute_calls):
    """Pinning changes no scoring input, and still recomputes.

    P1-008 noted the call is wasted -- `scoring.py` never reads
    `representative()`, so the dependency runs one way only. It is harmless
    (the worker coalesces) and §4.2 lists the site, so the behaviour is
    pinned as specified rather than as it might ideally be.
    """
    # source: async-recompute-N.md §4.2's table -- /api/canonical/pin is the
    # third async site.
    bucket(conn)

    response = client.post("/api/canonical/pin", json={"track_id": "ta"})

    assert response.status_code == 200
    # source: S_survivors.md app.py:655 -- {"ok": True}; grepped
    # static/js/canonical_viewer.js's pin-star handler and found no consumer
    # of this field (it branches on `data.error` only), so the fix pairs the
    # flag with the real write it accompanies rather than asserting a dead
    # field alone.
    assert response.get_json()["ok"] is True
    song_id = canonical.groups_for_track(conn, "ta")["song"]
    row = conn.execute(
        "SELECT representative_track_id FROM canonical_group WHERE id = ?", (song_id,)
    ).fetchone()
    assert row["representative_track_id"] == "ta"
    assert len(route_recompute_calls) == 1


def test_pinning_without_a_track_id_is_rejected(conn, client):
    # source: app.py's guard -- "track_id required".
    assert client.post("/api/canonical/pin", json={}).status_code == 400


def test_the_autogroup_endpoints_recompute_synchronously(conn, client, route_recompute_calls):
    # source: async-recompute-N.md §4.3 / app.py's comment on the autogroup
    # route -- it "deliberately keeps inline" the closing recompute, because
    # it is "one deliberate click, the button reports its own progress, and
    # the page reloads onto score-ordered content when it returns". So the
    # async worker is never asked.
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=200_000, album="Album Two")

    assert client.get("/api/canonical/autogroup/preview").get_json()["groups_closed"] == 1
    assert client.post("/api/canonical/autogroup").status_code == 200

    assert route_recompute_calls == []
    assert canonical.groups_for_track(conn, "ta")["song"] == canonical.groups_for_track(
        conn, "tb"
    )["song"]


def test_undoing_with_no_run_is_a_400_not_a_500(conn, client):
    # source: app.py's autogroup-undo handler -- canonical_autogroup.undo's
    # ValueError is translated, so a stale page's Undo click is a clean
    # rejection rather than an error page.
    assert client.post("/api/canonical/autogroup/undo").status_code == 400


# -- The unfiltered listing cap (app.py:31) ---------------------------------


def test_the_unfiltered_page_caps_at_fifty_groups(conn, client):
    # source: S_survivors.md app.py:31 -- `_LISTING_CAP = 50`; the unfiltered
    # /dev/canonical load must render no more than that many groups, however
    # many candidate groups actually exist.
    for i in range(51):
        builders.make_group(conn, [f"ta{i}", f"tb{i}"])

    html = client.get("/dev/canonical").get_data(as_text=True)

    assert html.count('class="song-group"') == 50


# -- The group deep link (app.py:419) ---------------------------------------


def test_the_deep_link_redirects_to_the_shared_song_regardless_of_member_order(conn, client):
    """`members[0]` picks *a* member of the group to resolve up to its song --
    equivalent, not arbitrary. `canonical._validate_labels` rejects any apply
    that would let two members of one group disagree about their song
    (grouping-engine.md's "nested-consistent" rule -- confirmed empirically:
    it raises ValueError on a recording/song mismatch), so every member of a
    canonical_group necessarily resolves to the same song and members[0] vs
    members[1] can never differ under real data.
    """
    # source: S_survivors.md app.py:419 -- ruled equivalent; recorded here so
    # the next sweep does not re-derive it.
    group = builders.make_group(conn, ["tb", "ta"])
    recording_id = group["recording"]

    resp = client.get(f"/dev/canonical/group/{recording_id}", follow_redirects=False)

    assert f"expand={group['song']}" in resp.headers["Location"]


# -- The review page's track-count guard (app.py:426) -----------------------


def test_the_review_page_accepts_exactly_two_tracks(client):
    # source: S_survivors.md app.py:426 -- `len(...) < 2` is the guard
    # ("tracks= needs at least 2 track ids"); exactly two must not be
    # rejected, which the < 2/<= 2/< 3 mutants all do.
    assert client.get("/dev/canonical/review?tracks=ta,tb").status_code == 200
