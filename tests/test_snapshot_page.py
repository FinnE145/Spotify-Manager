"""`snapshot.index_data` -- `/dev/snapshot`'s read path
(docs/codebase-health/P3_refactor.md §4.1).

Extracted out of `app.py`'s `snapshot_index` in P3 session 3. Session 3's
mutation sweep over its six payload keys found `playlists` and `changes` held
up by the golden baseline alone -- the suite §3.4 deletes at the end of P3 --
so those two are what the assertions here exist for. `summary`, `query` and
`track_matches` already die against the permanent suite and are not re-tested
from this side.

The page's pending-generation prompt is deliberately not part of this payload
(it stays in the route, so `snapshot.py` gains no dependency on
`generations.py`), so there is nothing to assert about it here.
"""

import builders
import jobs
import snapshot


def test_index_data_selects_every_snapshot_column_and_no_others(conn):
    # source: P3_refactor.md §4.5 -- this was the second of the two
    # `SELECT * FROM snapshot` sites P3 named, and the accepted cost of a
    # named list is that "a named list needs updating when a column is
    # added". Compared against PRAGMA table_info for the same reason its
    # sibling on /playlist/<id> is: a migration adding a column the template
    # might read fails here rather than as a Jinja UndefinedError.
    builders.make_playlist(conn, "p-columns", name="Columns")

    data = snapshot.index_data(conn, "")

    assert set(data["playlists"][0].keys()) == {
        "playlist_id", "name", "image_url", "owner", "track_count", "pulled_at",
        "snapshot_id", "last_changed_at", "tracks_pulled_at", "unfollowed_at",
        "description", "last_pull_error", "excluded", "generation_declined",
        "tracks_pulled_snapshot_id",
    }
    assert set(data["playlists"][0].keys()) == {
        r["name"] for r in conn.execute("PRAGMA table_info(snapshot)")
    }


def test_playlists_rank_by_score_and_fall_back_to_name(conn):
    # source: docs/specs/scoring-H.md §11.1 -- "/dev/snapshot playlist list"
    # moves from name to score. Both rules are exercised at once and they
    # disagree: the scored playlist is last alphabetically, so a name-only
    # implementation puts it third, while the two unscored ones are inserted
    # in reverse alphabetical order, so an insertion-order implementation
    # gets those two backwards.
    scored = builders.make_playlist(conn, "p-zebra", name="Zebra")
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "version", group["version"], all_time=90.0)
    builders.make_membership(conn, playlist_id=scored, track_id="ta")
    builders.make_playlist(conn, "p-beta", name="Beta")
    builders.make_playlist(conn, "p-alpha", name="Alpha")

    data = snapshot.index_data(conn, "")

    assert [p["name"] for p in data["playlists"]] == ["Zebra", "Alpha", "Beta"]


def test_every_playlist_is_listed_including_excluded_and_unfollowed_ones(conn):
    # source: characterization of snapshot.html, which renders the excluded
    # ones with their toggle in place rather than hiding them -- the page is
    # where you go to un-exclude. A WHERE excluded = 0 would make the row
    # unreachable from the only UI that can change it.
    builders.make_playlist(conn, "p-live", name="Live")
    builders.make_playlist(conn, "p-excluded", name="Excluded", excluded=1)
    builders.make_playlist(conn, "p-gone", name="Gone", unfollowed_at=builders.days_ago(2))

    data = snapshot.index_data(conn, "")

    assert sorted(p["name"] for p in data["playlists"]) == ["Excluded", "Gone", "Live"]


def test_changes_are_the_newest_membership_events_first_and_carry_their_kind(conn):
    # source: characterization of snapshot.html's "Recent changes" table.
    # event_at is COALESCE(removed_at, added_at), so the removal -- added
    # long ago, removed yesterday -- has to sort *first*. Ordering on
    # added_at instead puts it second, which is the mutation this catches.
    builders.make_membership(
        conn, playlist_id="p-1", track_id="ta", added_at=builders.days_ago(30)
    )
    builders.make_membership(
        conn,
        playlist_id="p-2",
        track_id="tb",
        added_at=builders.days_ago(20),
        removed_at=builders.days_ago(1),
    )
    builders.make_membership(
        conn, playlist_id="p-3", track_id="tc", added_at=builders.days_ago(5)
    )

    changes = snapshot.index_data(conn, "")["changes"]

    assert [(c["track_id"], c["kind"]) for c in changes] == [
        ("tb", "removed"),
        ("tc", "added"),
        ("ta", "added"),
    ]


# -- The snapshot control routes -------------------------------------------
#
# `/api/snapshot/{pull,refresh,backfill}` are three entry points into one
# module, and what separates them is *which* target and argument they hand to
# the job slot -- pull and refresh are the same `_run_pull` with `force_all`
# flipped. Nothing asserted that mapping, nor the `{"started": true}` body the
# routes answer with, so a route wired to the wrong sibling was invisible.


def _slot_recorder(monkeypatch):
    """Captures what a route hands to `jobs.try_start` without running it.

    Not `run_jobs_inline`: that runs the pull for real against the fake
    Spotify client, which exercises the job rather than the dispatch, and
    discards the target/args this is here to look at.
    """
    calls = []

    def fake_try_start(name, target, *args):
        calls.append((name, target, args))
        return True

    monkeypatch.setattr(jobs, "try_start", fake_try_start)
    return calls


def test_the_pull_route_starts_a_forced_full_pull_and_says_it_started(
    client, monkeypatch
):
    # source: S_sweep.md §3 -- `true` at app.py:772. The mutant answers
    # {"started": false} from a route that did start the job, so the page
    # reports a failure that did not happen. The dispatch half is the level
    # below: snapshot.start_full_pull is _run_pull with force_all=True, and
    # only the argument tells it apart from /refresh.
    calls = _slot_recorder(monkeypatch)

    resp = client.post("/api/snapshot/pull")

    assert calls == [("snapshot", snapshot._run_pull, (True,))]
    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}


def test_the_refresh_route_starts_an_unforced_pull_and_says_it_started(
    client, monkeypatch
):
    # source: S_sweep.md §3 -- `true` at app.py:780, same mutant as /pull's.
    # The discriminating half is force_all=False: /refresh and /pull share a
    # target and differ only here, so a copy-pasted route would silently
    # re-read all 154 playlists.
    calls = _slot_recorder(monkeypatch)

    resp = client.post("/api/snapshot/refresh")

    assert calls == [("snapshot", snapshot._run_pull, (False,))]
    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}


def test_the_backfill_route_starts_the_backfill_and_says_it_started(
    client, monkeypatch
):
    # source: S_sweep.md §3 -- `true` at app.py:788, same mutant again. The
    # third entry point is the one with a different target entirely
    # (_run_backfill, no argument), so it is asserted by name rather than
    # assumed to follow from its two siblings.
    calls = _slot_recorder(monkeypatch)

    resp = client.post("/api/snapshot/backfill")

    assert calls == [("snapshot", snapshot._run_backfill, ())]
    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}


def _excluded_flags(conn):
    return dict(
        conn.execute("SELECT playlist_id, excluded FROM snapshot ORDER BY playlist_id")
    )


def test_the_exclude_route_toggles_exactly_the_playlists_it_was_given(client, conn):
    """Both directions, and a bystander playlist that must not move.

    The route is the only way the exclude checkbox reaches `set_excluded`, and
    it is the only place the posted `excluded` flag is coerced -- so what is
    asserted is the *stored* flag, not the response alone.
    """
    # source: S_sweep.md §3 -- `or` at app.py:807. `body.get("playlist_ids")
    # and []` collapses a supplied list to [], so a real exclusion 400s
    # instead of happening; the empty-body arm still 400s and hides it.
    builders.make_playlist(conn, "p-target", name="Target")
    builders.make_playlist(conn, "p-bystander", name="Bystander")

    resp = client.post(
        "/api/snapshot/exclude",
        json={"playlist_ids": ["p-target"], "excluded": True},
    )

    assert resp.status_code == 200
    assert _excluded_flags(conn) == {"p-bystander": 0, "p-target": 1}

    resp = client.post(
        "/api/snapshot/exclude",
        json={"playlist_ids": ["p-target"], "excluded": False},
    )

    assert resp.status_code == 200
    assert _excluded_flags(conn) == {"p-bystander": 0, "p-target": 0}


def test_the_exclude_route_confirms_the_write_with_ok_true(client, conn):
    """The body the checkbox handler gets back on success.

    `snapshot.js` reverts the checkbox from `.catch()` only, so a mutated
    `{"ok": false}` leaves the box ticked over a write that may not have
    happened -- nothing on the page would contradict it.
    """
    # source: S_sweep.md §3 -- `true` at app.py:812, which answers
    # {"ok": false} from a route that did perform the exclusion.
    builders.make_playlist(conn, "p-ok", name="Ok")

    resp = client.post(
        "/api/snapshot/exclude", json={"playlist_ids": ["p-ok"], "excluded": True}
    )

    assert resp.get_json() == {"ok": True}
    assert _excluded_flags(conn) == {"p-ok": 1}
