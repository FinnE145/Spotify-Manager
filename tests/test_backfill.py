"""The album backfill: the derived settled/handled model, the button previews,
and the job.

Authority is **`grouping-fixes-backfill-M.md` §4**, stamped Audited and amended
2026-08-17 under P1-017 -- §4.2 for the arithmetic (including the ruled
NULL-`total_tracks` policy), §4.4/§4.5 for the fetch/queue split and the job,
§4.6 for the previews.

**Nothing here is checkpointed.** Every test therefore asserts a *derived*
answer -- re-deriving after a change rather than checking a stored flag -- which
is what makes "clearing the queue is a free and complete undo" testable at all.

The NULL-`total_tracks` case (§5's last ingest-floor item, deferred from
session 1 because the arithmetic is this module's) is a **specification test,
not an xfail**: P1-017 ruled it "document as current policy, no code change",
and §4.2 now documents it.
"""

import json

import backfill
import builders
import entities
import fakes


def album_with_tracklist(conn, album_id, track_ids, total_tracks=None, pulled=True):
    """An album whose stored `tracklist_json` names `track_ids`.

    `total_tracks` defaults to the tracklist's length, which is the honest
    shape; pass a different number to build an album Symr only partly owns.
    """
    builders.make_album(
        conn,
        album_id=album_id,
        total_tracks=len(track_ids) if total_tracks is None else total_tracks,
        tracklist_json=json.dumps(
            [{"id": t, "uri": f"spotify:track:{t}", "name": f"Track {t}"} for t in track_ids]
        ),
        tracklist_pulled_at=builders.days_ago(1) if pulled else None,
    )
    return album_id


def owned_track(conn, track_id, album_id):
    builders.make_track(conn, track_id, album_id=album_id)


def wanted(conn):
    return {
        row["uri"]: (row["source"], row["album_id"])
        for row in conn.execute("SELECT uri, source, album_id FROM wanted_uri")
    }


def settled(conn, album_id):
    return backfill._settled_map(conn)[album_id]


# -- The settled arithmetic (§4.2) ------------------------------------------


def test_an_album_never_fetched_is_not_settled(conn):
    # source: M §4.2's table -- "album never fetched | queued = 0, so missing
    # > 0 -> not settled -> gets a request".
    builders.make_album(conn, album_id="al-1", total_tracks=10)
    owned_track(conn, "t1", "al-1")

    assert settled(conn, "al-1") is False


def test_an_album_fetched_and_queued_is_settled(conn):
    # source: M §4.2's table -- "album fetched and queued | owned + queued =
    # total -> settled".
    album_with_tracklist(conn, "al-1", ["t1", "t2", "t3"])
    owned_track(conn, "t1", "al-1")
    entities.queue_wanted_uris(conn, "al-1", source="backfill")

    assert settled(conn, "al-1") is True


def test_resolving_a_queued_uri_keeps_the_album_settled(conn):
    # source: M §4.2's table -- "round-trip resolves the uris | owned rises,
    # unresolved queued falls -> still settled". `queued(A)` counts only rows
    # that do not resolve through played_uri_track, so the two move together.
    album_with_tracklist(conn, "al-1", ["t1", "t2"])
    owned_track(conn, "t1", "al-1")
    entities.queue_wanted_uris(conn, "al-1", source="backfill")
    assert settled(conn, "al-1") is True

    owned_track(conn, "t2", "al-1")  # the round-trip resolved it

    assert settled(conn, "al-1") is True


def test_a_permanently_unresolvable_uri_keeps_the_album_settled(conn):
    # source: M §4.2's table -- "a uri that 404s permanently | stays in
    # wanted_uri unresolved -> counted in queued -> settled, never
    # re-offered". This is what stops one dead uri re-offering its whole
    # generation on every run.
    album_with_tracklist(conn, "al-1", ["t1", "t2"])
    owned_track(conn, "t1", "al-1")
    entities.queue_wanted_uris(conn, "al-1", source="backfill")

    assert wanted(conn) == {"spotify:track:t2": ("backfill", "al-1")}
    assert settled(conn, "al-1") is True


def test_a_resolved_queued_uri_is_not_counted_twice(conn):
    """`queued(A)` counts only *unresolved* rows, and that word is the whole
    guard against double-counting.

    Once the round-trip resolves a queued uri, that track is `owned`. A
    `queued` count that still included it would credit the album twice and
    settle it while tracks are genuinely still missing -- here `t4`, which the
    stored tracklist never named.
    """
    # source: M §4.2 -- "`queued(A)` = `wanted_uri` rows for `A` that are
    # still **unresolved** -- i.e. that do not resolve through
    # `played_uri_track`, the same rule the round-trip's work list uses to
    # decide 'done'."
    builders.make_album(conn, album_id="al-1", total_tracks=4)
    owned_track(conn, "t1", "al-1")
    owned_track(conn, "t2", "al-1")  # t2 was queued, then the round-trip got it
    conn.executemany(
        "INSERT INTO wanted_uri (uri, source, album_id) VALUES (?, 'backfill', 'al-1')",
        [("spotify:track:t2",), ("spotify:track:t3",)],
    )
    conn.commit()

    # owned = 2, unresolved queued = 1 (t3 only) -> missing = 1, so t4 is
    # still to come. Counting t2's row again would make missing 0.
    assert settled(conn, "al-1") is False


def test_clearing_the_queue_offers_the_album_again(conn):
    """The Q17 requirement: a clear is a true undo, not a trap."""
    # source: M §4.2's table -- "**Finn clears the backfill queue** | queued
    # drops to 0 -> not settled -> the same generations are offered again, at
    # **zero requests**, because the tracklists are stored."
    album_with_tracklist(conn, "al-1", ["t1", "t2"])
    owned_track(conn, "t1", "al-1")
    entities.queue_wanted_uris(conn, "al-1", source="backfill")
    assert settled(conn, "al-1") is True

    conn.execute("DELETE FROM wanted_uri WHERE source = 'backfill'")
    conn.commit()

    assert settled(conn, "al-1") is False
    # Zero requests to re-offer it: the tracklist is already stored.
    assert backfill._requests_estimate(conn, ["al-1"]) == 0


def test_a_null_total_tracks_album_is_permanently_settled(conn):
    """P1-017's ruled policy, written as the record of the decision.

    Treating NULL as 0 makes `missing <= 0` unconditionally true, so the album
    silently drops out of every future backfill run. Zero albums are in this
    state today; the ruling was to document it rather than guard it.
    """
    # source: M §4.2 -- "**Ruled 2026-08-17 (P1-017):** if `total_tracks` is
    # NULL, this arithmetic treats it as `0`, which makes `missing(A) <= 0`
    # unconditionally -- the album computes as permanently settled and
    # silently drops out of every future backfill run... current, if untested,
    # policy rather than a live bug."
    builders.make_album(conn, album_id="al-null", total_tracks=None)
    owned_track(conn, "t1", "al-null")

    assert settled(conn, "al-null") is True
    # And the control: the same album with a real count is not settled, so the
    # assertion above is about the NULL and not about the fixture.
    builders.make_album(conn, album_id="al-real", total_tracks=10)
    owned_track(conn, "t2", "al-real")
    assert settled(conn, "al-real") is False


def test_a_null_total_tracks_album_with_nothing_owned_is_settled_at_the_boundary(conn):
    # source: S_sweep.md §3.4 D -- the sibling test above always has one
    # owned track, so `missing` lands at -1 (real, NULL treated as 0) or 0
    # (mutant, NULL treated as 1) -- both <= 0, and the two literals never
    # disagree (P2-005's shape). With nothing owned at all, missing is
    # exactly 0 (real, settled) or exactly 1 (mutant, not settled) -- the
    # boundary `missing <= 0` actually turns on.
    builders.make_album(conn, album_id="al-null-empty", total_tracks=None)

    assert settled(conn, "al-null-empty") is True


def test_an_album_with_no_owned_tracks_at_all_is_not_settled(conn):
    # source: S_sweep.md §3.4 D -- `owned.get(row["album_id"], 0)`'s default
    # only fires when the album has zero owned tracks, since `owned` is built
    # by grouping the `track` table and an album with no tracks never becomes
    # a key. total_tracks=1 makes the default's value the entire answer: real
    # (default 0) gives missing=1, not settled; the `0` -> `1` mutant gives
    # missing=0, wrongly settled.
    builders.make_album(conn, album_id="al-unowned", total_tracks=1)

    assert settled(conn, "al-unowned") is False


# -- Handled generations (§4.2) ---------------------------------------------


def generation_with_album(conn, ordinal, album_id, track_ids):
    """A generation whose playlist holds `track_ids`, all on `album_id`.

    `generation_presence` joins `membership` through `track_group`, so the
    tracks need groups as well as memberships.
    """
    playlist = builders.make_playlist(conn, f"pl-{ordinal}", name=f"v{ordinal}.0.0")
    conn.execute(
        "INSERT OR REPLACE INTO generation (ordinal, playlist_id) VALUES (?, ?)",
        (ordinal, playlist),
    )
    conn.commit()
    for track_id in track_ids:
        owned_track(conn, track_id, album_id)
        builders.make_membership(conn, playlist_id=playlist, track_id=track_id)
        builders.make_group(conn, [track_id])
    return playlist


def test_a_generation_is_handled_when_every_album_in_it_is_settled(conn):
    # source: M §4.2 -- "A generation G is **handled** iff every album with at
    # least one track present in G is settled."
    album_with_tracklist(conn, "al-1", ["t1", "t2"])
    generation_with_album(conn, 1, "al-1", ["t1"])
    settled_map = backfill._settled_map(conn)
    assert backfill._unhandled_ordinals_desc(conn, settled_map) == [1]

    entities.queue_wanted_uris(conn, "al-1", source="backfill")

    assert backfill._unhandled_ordinals_desc(conn, backfill._settled_map(conn)) == []


def test_a_generation_with_no_album_bearing_tracks_is_vacuously_handled(conn):
    # source: _unhandled_ordinals_desc' docstring -- "An ordinal with no
    # album-bearing tracks at all is vacuously handled and excluded." The
    # all() of an empty set is True, and that is the intended reading.
    playlist = builders.make_playlist(conn, "pl-1", name="v1.0.0")
    conn.execute(
        "INSERT OR REPLACE INTO generation (ordinal, playlist_id) VALUES (1, 'pl-1')"
    )
    conn.commit()
    builders.make_track(conn, "t1")
    conn.execute("UPDATE track SET album_id = NULL WHERE track_id = 't1'")
    builders.make_membership(conn, playlist_id=playlist, track_id="t1")
    builders.make_group(conn, ["t1"])
    conn.commit()

    assert backfill._unhandled_ordinals_desc(conn, backfill._settled_map(conn)) == []


def test_unhandled_ordinals_come_back_newest_first(conn):
    # source: M §4 -- the buttons take "the most recent N generations", so the
    # ordering is descending and the cap is applied to that.
    for ordinal in (1, 2, 3):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"])
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    assert backfill._unhandled_ordinals_desc(conn, backfill._settled_map(conn)) == [3, 2, 1]


# -- The work list (§4.6) ---------------------------------------------------


def test_the_work_list_caps_at_the_requested_generation_count(conn):
    # source: M §4 -- the two buttons "are the budget control" (N = 7 or 2),
    # so the cap is the only thing separating them.
    for ordinal in (1, 2, 3):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"])
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    assert backfill.work_list(conn, 2)["ordinals"] == [2, 3]
    assert backfill.work_list(conn, 7)["ordinals"] == [1, 2, 3]


def test_the_cap_excludes_the_albums_of_the_generations_it_left_out(conn):
    """The cap is the budget control, so it has to bind on the *albums*, not
    just on the ordinals the status line displays."""
    # source: S_sweep.md §3 -- `sqlAND` at backfill.py:113. The test above
    # asserts the chosen ordinals; nothing asserted that _albums_for_ordinals
    # actually filters by them. The mutant turns "gp.ordinal IN (...) AND
    # t.album_id IS NOT NULL" into OR, so the ordinal list stops binding
    # altogether: every album-bearing track in any generation flows in and the
    # 2-generation button quietly does the 7-generation button's work. al-1 is
    # deliberately left unsettled, or _derive's later unsettled filter would
    # hide the difference.
    for ordinal in (1, 2, 3):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"])
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    wl = backfill.work_list(conn, 2)

    assert wl["ordinals"] == [2, 3]
    assert wl["albums"] == ["al-2", "al-3"]  # al-1's generation was not chosen
    assert backfill._albums_for_ordinals(conn, [2, 3]) == {"al-2", "al-3"}


def test_the_work_list_holds_only_the_unsettled_albums(conn):
    # source: backfill.work_list's docstring -- "the unsettled albums with a
    # track present in any of them". A settled album in a chosen generation
    # is not work.
    album_with_tracklist(conn, "al-done", ["t1"])
    album_with_tracklist(conn, "al-todo", ["t2", "x2"])
    generation_with_album(conn, 1, "al-done", ["t1"])
    for track_id in ("t2",):
        owned_track(conn, track_id, "al-todo")
        builders.make_membership(conn, playlist_id="pl-1", track_id=track_id)
        builders.make_group(conn, [track_id])

    assert backfill.work_list(conn, 7)["albums"] == ["al-todo"]


def test_a_partial_run_resumes_by_re_deriving(conn):
    # source: M §4.2 -- "nothing is checkpointed, so nothing can go stale...
    # re-clicking a button after a partial run simply picks up wherever it
    # left off."
    album_with_tracklist(conn, "al-1", ["t1", "x1"])
    album_with_tracklist(conn, "al-2", ["t2", "x2"])
    generation_with_album(conn, 1, "al-1", ["t1"])
    for track_id in ("t2",):
        owned_track(conn, track_id, "al-2")
        builders.make_membership(conn, playlist_id="pl-1", track_id=track_id)
        builders.make_group(conn, [track_id])
    assert backfill.work_list(conn, 7)["albums"] == ["al-1", "al-2"]

    entities.queue_wanted_uris(conn, "al-1", source="backfill")  # as if the run stopped here

    assert backfill.work_list(conn, 7)["albums"] == ["al-2"]


# -- Request estimates (§4.6) ------------------------------------------------


def test_an_unfetched_album_costs_one_request(conn):
    # source: backfill._requests_estimate's docstring, per M §0.3 -- "1
    # request per album that needs a first fetch (tracklist_pulled_at IS
    # NULL)".
    builders.make_album(conn, album_id="al-1", total_tracks=10)

    assert backfill._requests_estimate(conn, ["al-1"]) == 1


def test_a_long_album_costs_one_request_per_fifty_tracks(conn):
    # source: same -- "plus one more per extra 50 tracks past the first page
    # (§0.3)", i.e. ceil(total_tracks / 50). Both sides of the 50 boundary,
    # since an off-by-one here is invisible on an ordinary album.
    builders.make_album(conn, album_id="al-50", total_tracks=50)
    builders.make_album(conn, album_id="al-51", total_tracks=51)
    builders.make_album(conn, album_id="al-120", total_tracks=120)

    assert backfill._requests_estimate(conn, ["al-50"]) == 1
    assert backfill._requests_estimate(conn, ["al-51"]) == 2
    assert backfill._requests_estimate(conn, ["al-120"]) == 3


def test_an_already_fetched_album_costs_nothing(conn):
    # source: same -- "An unsettled album that was already fetched -- its
    # queue was cleared -- costs nothing, since the tracklist is already
    # stored." This is the arithmetic behind §4.2's "at **zero** requests".
    album_with_tracklist(conn, "al-1", ["t1", "t2"], total_tracks=2)

    assert backfill._requests_estimate(conn, ["al-1"]) == 0


# -- The previews (§4.6) ----------------------------------------------------


def test_the_previews_match_what_the_job_would_do(conn):
    """§4.6's guarantee: the buttons and the job go through one derivation."""
    # source: backfill.previews' docstring, per M §4.6 -- "Display shape of
    # work_list() for the Add buttons... Both buttons share a single
    # _refresh() / _settled_map() / _unhandled_ordinals_desc() pass", and
    # work_list's: "computed identically so the two can never disagree".
    for ordinal in (1, 2, 3):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"], pulled=False)
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    rows = {row["generations"]: row for row in backfill.previews(conn)}

    for n in (7, 2):
        expected = backfill.work_list(conn, n)
        assert rows[n]["album_count"] == len(expected["albums"])
        assert rows[n]["requests_estimate"] == expected["requests_estimate"]


def test_the_preview_labels_the_generation_range(conn):
    # source: M §4.6 -- the panel's per-button album/request estimates are
    # server-rendered on page load, "seeing the numbers before clicking *is*
    # the budget control". _format_ordinal_range collapses runs via
    # generations.runs.
    for ordinal in (1, 2, 3):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"])
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    rows = {row["generations"]: row for row in backfill.previews(conn)}

    assert rows[7]["range_label"] == "1–3"
    assert rows[2]["range_label"] == "2–3"


def test_nothing_to_do_previews_as_zero(conn):
    # characterization -- the empty state the panel renders when every
    # generation is handled; _format_ordinal_range returns "" rather than a
    # stray dash.
    rows = {row["generations"]: row for row in backfill.previews(conn)}

    assert rows[7] == {
        "generations": 7,
        "album_count": 0,
        "requests_estimate": 0,
        "range_label": "",
    }


def test_a_plain_preview_writes_track_groups(conn):
    # source: M §4.6 as amended by P1-017 -- "**A plain page load writes to
    # the database.**... GET /dev/roundtrip's previews() call chain runs
    # canonical.ensure_track_groups() **and commits**, on every ordinary page
    # view. Not a Spotify-request cost, but it is a write the spec never
    # flagged." Every generation_presence reader needs current groups first.
    builders.make_track(conn, "t1")
    assert conn.execute("SELECT COUNT(*) FROM track_group").fetchone()[0] == 0

    backfill.previews(conn)

    assert conn.execute("SELECT COUNT(*) FROM track_group").fetchone()[0] == 1


def test_a_freshly_reset_status_reports_zero_albums(conn):
    # source: S_sweep.md §3.4 E -- `_status`'s JobStatus constructor default
    # `albums_total=0`. `jobs.JobStatus.reset()` rebuilds `_fields` from
    # exactly this default before applying whatever the caller passes (phase,
    # started_at, generations here, mirroring `_run`'s own reset call) --
    # none of which touch albums_total -- so a freshly reset status is a
    # direct read of the constructor's literal.
    import jobs

    backfill._status.reset(phase="working", started_at=jobs.now_iso(), generations=2)

    assert backfill.get_status()["albums_total"] == 0


def test_stopping_is_reported_only_while_the_backfill_is_the_running_job(monkeypatch):
    """`stopping` is a conjunction, and the left half is what makes it mean
    anything: the stop flag is module-global and shared by all four jobs."""
    # source: S_sweep.md §3 -- `and` at backfill.py:43. get_status() sets
    # stopping = running AND jobs.stop_requested(). The `or` mutant reports
    # "stopping" for a backfill that is not running at all -- e.g. while a
    # stop asked of some other job is still set, which try_start only clears
    # when the *next* job claims the slot.
    import jobs

    monkeypatch.setattr(jobs, "stop_requested", lambda: True)
    assert jobs.active() is None  # nothing holds the slot

    assert backfill.get_status()["stopping"] is False

    # And the other half: with the backfill actually running, the same flag
    # does mean stopping, so the assertion above is about `running` and not
    # about stop_requested() being ignored.
    monkeypatch.setattr(jobs, "active", lambda: "backfill")
    assert backfill.get_status()["stopping"] is True


# -- The job (§4.5) ---------------------------------------------------------


def run_job(conn, fake_spotify, n_generations=7):
    """Runs `_run` on this thread. It opens its own connection, so the caller
    re-reads through `conn` afterwards."""
    backfill._run(n_generations)
    return backfill.get_status()


def test_the_job_fetches_a_tracklist_and_queues_its_unowned_uris(conn, fake_spotify):
    # source: M §4.5 -- the job "fetches tracklists for unsettled albums in
    # the most recent N generations and queues their unowned uris into the
    # round-trip's existing work list -- an ingest route, nothing more."
    builders.make_album(conn, album_id="al-1", total_tracks=3)
    generation_with_album(conn, 1, "al-1", ["t1"])
    fake_spotify.add_album(
        fakes.spotify_album(
            "al-1",
            total_tracks=3,
            tracks=fake_spotify.paged(
                [fakes.spotify_track(t) for t in ("t1", "t2", "t3")]
            ),
        )
    )

    status = run_job(conn, fake_spotify)

    assert status["outcome"] == "completed"
    assert status["uris_queued"] == 2  # t1 is already owned
    assert sorted(wanted(conn)) == ["spotify:track:t2", "spotify:track:t3"]
    assert all(source == "backfill" for source, _album in wanted(conn).values())


def test_the_job_pages_past_the_first_fifty_tracks(conn, fake_spotify):
    """The one place the backfill deliberately differs from the entity page's
    capped fetch."""
    # source: M §4.5 / _fetch_full_tracklist's docstring -- "unlike
    # entities.fetch_album_tracklist, pages past Spotify's 50-item first page
    # (§4.5)".
    builders.make_album(conn, album_id="al-1", total_tracks=60)
    generation_with_album(conn, 1, "al-1", ["t1"])
    fake_spotify.add_album(
        fakes.spotify_album(
            "al-1",
            total_tracks=60,
            tracks=fake_spotify.paged(
                [fakes.spotify_track(f"t{i}") for i in range(1, 61)], limit=50
            ),
        )
    )

    status = run_job(conn, fake_spotify)

    assert status["uris_queued"] == 59  # 60 items, t1 already owned
    assert any(call[0] == "next" for call in fake_spotify.calls)
    stored = json.loads(
        conn.execute("SELECT tracklist_json FROM album WHERE album_id = 'al-1'").fetchone()[0]
    )
    assert len(stored) == 60


def test_the_job_does_not_refetch_a_stored_tracklist(conn, fake_spotify):
    # source: M §4.2's table -- an album whose queue was cleared is unsettled
    # again but "costs nothing, since the tracklists are already stored". The
    # job checks tracklist_pulled_at before spending a request.
    album_with_tracklist(conn, "al-1", ["t1", "t2"], total_tracks=2)
    generation_with_album(conn, 1, "al-1", ["t1"])

    status = run_job(conn, fake_spotify)

    assert status["requests"] == 0
    assert [call for call in fake_spotify.calls if call[0] == "album"] == []
    assert status["uris_queued"] == 1


def test_a_failing_album_does_not_stop_the_run(conn, fake_spotify):
    # source: backfill._run -- the per-album try/except logs the failure and
    # carries on, which is what _fetch_full_tracklist's "Failures propagate --
    # _run's per-album try/except is what catches them" refers to.
    for album_id in ("al-1", "al-2"):
        builders.make_album(conn, album_id=album_id, total_tracks=2)
    generation_with_album(conn, 1, "al-1", ["t1"])
    for track_id in ("t2",):
        owned_track(conn, track_id, "al-2")
        builders.make_membership(conn, playlist_id="pl-1", track_id=track_id)
        builders.make_group(conn, [track_id])
    # al-1 is not registered with the fake, so sp.album() 404s for it.
    fake_spotify.add_album(
        fakes.spotify_album(
            "al-2",
            total_tracks=2,
            tracks=fake_spotify.paged([fakes.spotify_track(t) for t in ("t2", "t9")]),
        )
    )

    status = run_job(conn, fake_spotify)

    assert status["outcome"] == "completed"
    assert status["albums_done"] == 2
    assert sorted(wanted(conn)) == ["spotify:track:t9"]
    assert any("al-1" in entry["message"] for entry in status["log"])


def test_a_rate_limit_aborts_the_whole_run_rather_than_failing_one_album(conn, fake_spotify):
    """A quota block is not a per-album fault, and the two `except` arms have
    to stay in that order to say so.

    `_run`'s per-album handler catches `RateLimited` **above** a generic
    `except Exception` that logs the album and carries on. Swap them and a
    quota block degrades into "al-1 — failed: ...", the loop moves to the next
    album, and the job keeps spending requests against a quota that is already
    refusing them -- while the page reports `completed`. So the discriminating
    assertion is not the outcome string but the second album never being
    attempted.
    """
    # source: backfill._run -- the per-album `except RateLimited: rollback;
    # raise` and the outer arm setting outcome="rate_limited"/retry_at; same
    # rule as snapshot's, tested in test_snapshot_ingest.py as
    # test_an_app_quota_block_aborts_the_whole_run.
    for album_id in ("al-1", "al-2"):
        builders.make_album(conn, album_id=album_id, total_tracks=2)
        fake_spotify.add_album(
            fakes.spotify_album(
                album_id,
                total_tracks=2,
                tracks=fake_spotify.paged(
                    [fakes.spotify_track(f"{album_id}-t{n}") for n in (1, 2)]
                ),
            )
        )
    generation_with_album(conn, 1, "al-1", ["al-1-t1"])
    generation_with_album(conn, 2, "al-2", ["al-2-t1"])
    # retry_after > 30s, so jobs.call raises rather than sleeping and retrying.
    fake_spotify.fail("album", fakes.rate_limited(retry_after=3600), times=1)

    status = run_job(conn, fake_spotify)

    assert status["outcome"] == "rate_limited"
    assert status["phase"] == "error"
    assert status["retry_at"] is not None
    # The whole point: the run stopped. One album was attempted, not two, and
    # nothing it would have queued was queued.
    assert len([call for call in fake_spotify.calls if call[0] == "album"]) == 1
    assert wanted(conn) == {}
    assert status["albums_done"] == 0


def test_the_job_stops_cleanly_when_asked(conn, fake_spotify, monkeypatch):
    # source: M §4.5 -- the job "commits per album and polls
    # jobs.stop_requested() between them", i.e. cooperative stopping at a safe
    # point rather than a kill.
    import jobs

    for ordinal, album_id in ((1, "al-1"), (2, "al-2")):
        builders.make_album(conn, album_id=album_id, total_tracks=2)
        generation_with_album(conn, ordinal, album_id, [f"t{ordinal}"])
        fake_spotify.add_album(
            fakes.spotify_album(
                album_id,
                total_tracks=2,
                tracks=fake_spotify.paged(
                    [fakes.spotify_track(f"t{ordinal}"), fakes.spotify_track(f"x{ordinal}")]
                ),
            )
        )
    monkeypatch.setattr(jobs, "stop_requested", lambda: True)

    status = run_job(conn, fake_spotify)

    assert status["outcome"] == "stopped"
    assert status["albums_done"] == 0
    assert wanted(conn) == {}


def test_the_opening_log_line_names_the_generations_the_run_covers(conn, fake_spotify):
    """The event feed's first line is the run's own statement of its scope."""
    # source: S_sweep.md §3 -- `or` at backfill.py:235. The `or 'none'`
    # fallback only fires when _format_ordinal_range returns "", i.e. when
    # there are no unhandled generations at all; the `and 'none'` mutant
    # inverts that, so a real run announces "across generations none" while
    # working twelve albums, and an empty run announces a blank. Both halves
    # are asserted as literals -- reading the range back off
    # _format_ordinal_range would move with the mutant (S brief trap 5).
    for ordinal in (1, 2):
        album_with_tracklist(conn, f"al-{ordinal}", [f"t{ordinal}", f"x{ordinal}"])
        generation_with_album(conn, ordinal, f"al-{ordinal}", [f"t{ordinal}"])

    status = run_job(conn, fake_spotify)

    assert status["outcome"] == "completed"
    opening = status["log"][0]["message"]
    assert "across generations 1–2," in opening

    # And the empty run, which is what the fallback exists for: a word, not a
    # gap where the range should be.
    conn.execute("DELETE FROM wanted_uri")
    conn.execute("DELETE FROM generation")
    conn.commit()

    status = run_job(conn, fake_spotify)

    assert "across generations none," in status["log"][0]["message"]


def test_the_job_reports_not_authenticated_rather_than_running(conn, monkeypatch):
    # characterization -- the guard at the top of _run. With no client there
    # is nothing to fetch, and the status has to say so rather than looking
    # like a completed empty run.
    monkeypatch.setattr(backfill, "get_spotify_client", lambda: None)

    status = run_job(conn, None)

    assert status["outcome"] == "error"
    assert status["error"] == "not_authenticated"


# -- The start route (§4.5) -------------------------------------------------


def test_the_start_route_accepts_exactly_the_two_button_counts(client, run_jobs_inline):
    """The two buttons *are* the budget control, so the route's whitelist is
    what stops an arbitrary N being posted at it."""
    # source: S_sweep.md §3 -- `num` at app.py:940. M §4 -- "no budget
    # parameter, no scope beyond the two fixed buttons (N = 7 or 2), which
    # *are* the budget control". The (2, 7) -> (2, 8) mutant flips both
    # directions at once, so both are asserted: 7 must be accepted and 8 must
    # not. Asserting only that 7 is accepted would still pass for (2, 7, 8).
    for n in (2, 7):
        assert client.post("/api/backfill/start", json={"generations": n}).status_code == 200, n

    for n in (1, 3, 8, 37, 0, -2, None, "7"):
        resp = client.post("/api/backfill/start", json={"generations": n})
        assert resp.status_code == 400, n
        assert resp.get_json()["error"] == "bad_request", n

    # The refusal names the real pair, so the message can't drift off the
    # whitelist it is describing. Written out rather than formatted from the
    # constant, which would move with the mutant (S brief trap 5).
    detail = client.post("/api/backfill/start", json={"generations": 8}).get_json()["detail"]
    assert "(2, 7)" in detail

    # Only the two accepted posts ever reached the job slot.
    assert run_jobs_inline == ["backfill", "backfill"]


def test_a_started_backfill_says_so_in_its_response(client, run_jobs_inline):
    """The JS reads this flag to decide whether to start polling for status."""
    # source: S_sweep.md §3 -- `true` at app.py:952. The `{"started": False}`
    # mutant reports a refusal for a run that really did claim the slot: the
    # two assertions are paired deliberately, since the flag is only worth
    # anything if it agrees with whether a job started. The route's other two
    # exits already say False by other means -- 401 not_authenticated and 409
    # already_running -- so True here is the one thing left to state.
    resp = client.post("/api/backfill/start", json={"generations": 2})

    assert run_jobs_inline == ["backfill"]  # a job really did start
    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}


# -- Scope: an ingest route and nothing else (§4) ---------------------------


def test_the_backfill_does_not_chain_into_anything(conn):
    # source: M §4 -- "an ingest route, nothing more. No chaining into the
    # round-trip or auto-group", plus P1-017's note that "backfill.py doesn't
    # import scoring at all" -- a documented exception to the eleven job call
    # sites, caught by scoring.ensure_fresh()'s backstop instead.
    import sys

    module_globals = vars(backfill)
    assert "roundtrip" not in module_globals
    assert "canonical_autogroup" not in module_globals
    assert "scoring" not in module_globals
    assert sys.modules["backfill"].__name__ == "backfill"


