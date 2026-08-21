"""`roundtrip.py` -- the only module that writes to the Spotify library.

Its two invariants were both learned the hard way and are the reason the fake
`sp` is shaped the way it is (`P2_tests.md` §4.4):

- **replace, never append.** The fake has no `playlist_add_items` at all, so
  code that tried to append fails with an AttributeError naming the method
  rather than passing a test. That invariant is therefore enforced by the
  fixture's *absence* of a method, and asserted here only where a test can add
  something the absence cannot say -- that the playlist afterwards holds
  exactly the batch.
- **read the page as a bag, never as a sequence.** A returned track identifies
  itself by its own id, and a substituted one carries `linked_from` naming what
  was requested. `test_a_relink_is_read_from_linked_from_not_from_position`
  is the one that would have caught the drifted read window that once rewrote
  1,250 uri->track mappings as bogus relinks.

`foreign-roundtrip-D.md` is the spec. Its §4.3 step 6 and §4.5 were both
rewritten by P1-007, which also fixed two real bugs -- a `not_returned` uri
that a probe later confirmed dead never transitioned, and a stop during
reconciliation recorded the run as completed. Both have tests here.
"""

import pytest

import builders
import db
import fakes
import jobs
import roundtrip


@pytest.fixture
def loader(fake_spotify):
    """Registers the loader playlist, which the fake deliberately does not.

    `roundtrip._guard` verifies name and owner live before anything is
    written, and a fake that satisfied that guard for free would let a test
    pass while the guard was broken -- so every test that needs a working
    round-trip asks for it explicitly, and the guard tests below ask for the
    broken variants instead.
    """
    fake_spotify.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)
    return fake_spotify


def uri(track_id):
    return f"spotify:track:{track_id}"


def failed_state(conn, requested_uri):
    row = conn.execute(
        "SELECT state FROM roundtrip_failed_uri WHERE requested_uri = ?", (requested_uri,)
    ).fetchone()
    return row["state"] if row else None


def failed_uris(conn):
    return {
        row["requested_uri"]
        for row in conn.execute("SELECT requested_uri FROM roundtrip_failed_uri")
    }


# -- The guard (§4.1) -------------------------------------------------------


def test_the_guard_passes_on_the_right_playlist_and_records_it_excluded(conn, loader):
    """The loader is marked `excluded = 1` so a later full pull never reads its
    items and it never contributes membership rows."""
    # source: foreign-roundtrip-D.md §4.1 -- the guard is two reads and zero
    # writes to Spotify, verified live every run and never cached.
    leftovers = roundtrip._guard(conn, loader)

    assert leftovers == 0
    row = conn.execute(
        "SELECT name, excluded FROM snapshot WHERE playlist_id = ?", (roundtrip.LOADER_ID,)
    ).fetchone()
    assert row["name"] == roundtrip.LOADER_NAME
    assert row["excluded"] == 1
    assert loader.replacements == []


def test_the_guard_refuses_a_playlist_with_the_wrong_name(conn, fake_spotify):
    """The whole insurance policy against the one thing that must never
    happen: a bug pointing the clear-playlist call at a real playlist."""
    # source: foreign-roundtrip-D.md §4.1 -- name and owner are both verified
    # live, and zero writes happen if either is wrong.
    fake_spotify.add_playlist(roundtrip.LOADER_ID, name="Finn All")

    with pytest.raises(RuntimeError, match="guard failed"):
        roundtrip._guard(conn, fake_spotify)

    assert fake_spotify.replacements == []


def test_the_guard_refuses_a_playlist_owned_by_someone_else(conn, fake_spotify):
    # source: foreign-roundtrip-D.md §4.1 -- the owner arm of the same guard.
    fake_spotify.add_playlist(
        roundtrip.LOADER_ID, roundtrip.LOADER_NAME, owner_id="someone-else"
    )

    with pytest.raises(RuntimeError, match="guard failed"):
        roundtrip._guard(conn, fake_spotify)

    assert fake_spotify.replacements == []


# -- Load and read back -----------------------------------------------------


def test_the_loader_is_replaced_not_appended(conn, loader):
    """Replace is what makes the read-back always `offset=0`, so no running
    offset can drift."""
    # source: foreign-roundtrip-D.md §4.4 -- "replace, never append"; the
    # playlist holds at most one batch at any time.
    roundtrip._load_and_read(conn, loader, "batch 1/2", [uri("a"), uri("b")])
    roundtrip._load_and_read(conn, loader, "batch 2/2", [uri("c")])

    assert loader.replacements == [
        (roundtrip.LOADER_ID, [uri("a"), uri("b")]),
        (roundtrip.LOADER_ID, [uri("c")]),
    ]
    # The second call replaced the first batch rather than adding to it.
    assert [entry["item"]["id"] for entry in loader.items[roundtrip.LOADER_ID]] == ["c"]


def test_a_returned_track_is_stored_through_the_shared_ingest_path(conn, loader):
    """`roundtrip` writes tracks through `snapshot._upsert_track_full`, so a
    track it stores is filled exactly as a pull fills one -- album, artists
    and credit rows included, not a partial row."""
    # source: CLAUDE.md's snapshot.py entry -- the shared track-ingest path is
    # deliberately one implementation; characterization of what it produces.
    roundtrip._load_and_read(conn, loader, "batch 1/1", [uri("t1")])

    track = conn.execute(
        "SELECT track_id, album_id, uri FROM track WHERE track_id = 't1'"
    ).fetchone()
    assert track["uri"] == uri("t1")
    assert track["album_id"] is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM track_artist WHERE track_id = 't1'"
    ).fetchone()[0] == 1


def test_a_relink_is_read_from_linked_from_not_from_position(conn, loader):
    """The bag-not-a-sequence invariant, in the shape that actually broke it.

    Three uris go in and the **first** comes back from nothing at all, so the
    read-back is shorter than the request and every surviving track sits one
    index earlier than the uri that asked for it. A positional read would
    alias `gone` -> new-a and `old-a` -> new-b -- which is exactly the drifted
    read window that once rewrote 1,250 uri->track mappings as bogus relinks.
    Reading `linked_from` is immune to the shift.
    """
    # source: foreign-roundtrip-D.md §4.4 -- "a track identifies itself by its
    # own id, and a substituted one carries linked_from naming what was
    # requested"; track_uri_alias is written only from linked_from.
    loader.drop(uri("gone"))
    loader.substitute(uri("old-a"), "new-a", linked_from=True)
    loader.substitute(uri("old-b"), "new-b", linked_from=True)

    roundtrip._load_and_read(
        conn, loader, "batch 1/1", [uri("gone"), uri("old-a"), uri("old-b")]
    )

    aliases = dict(
        conn.execute("SELECT requested_uri, track_id FROM track_uri_alias").fetchall()
    )
    assert aliases == {uri("old-a"): "new-a", uri("old-b"): "new-b"}


def test_linked_from_uri_falls_back_to_building_a_uri_from_the_id(conn):
    # source: roundtrip._linked_from_uri -- Spotify's linked_from carries a
    # uri, but the id is the fallback; characterization of both arms.
    assert roundtrip._linked_from_uri({"linked_from": {"uri": uri("x")}}) == uri("x")
    assert roundtrip._linked_from_uri({"linked_from": {"id": "x"}}) == uri("x")
    assert roundtrip._linked_from_uri({}) is None
    assert roundtrip._linked_from_uri({"linked_from": {}}) is None


# -- `_run_batch`: what gets recorded (P1-007) ------------------------------


def test_an_all_missing_batch_with_a_sane_read_records_every_uri(conn, loader):
    """The branch P1-007 found step 6 contradicting.

    Not one requested uri came back, but a full page of *usable* tracks did --
    one per uri sent. That is a structurally sane read, so every uri is
    recorded `not_returned` and handed to the reconciliation pass, which
    selects exactly that state.
    """
    # source: foreign-roundtrip-D.md §4.3 step 6 (rewritten by P1-007) --
    # "record not_returned on a structurally-sane all-missing read".
    requested = [uri("m1"), uri("m2"), uri("m3")]
    for index, requested_uri in enumerate(requested):
        loader.substitute(requested_uri, f"sub{index}", linked_from=False)

    landed = roundtrip._run_batch(conn, loader, 1, 1, requested)

    assert failed_uris(conn) == set(requested)
    assert all(failed_state(conn, u) == roundtrip.STATE_NOT_RETURNED for u in requested)
    # And it still counts as a failed batch toward the circuit breaker: the
    # assertion is about what was *recorded*, not about pass/fail.
    assert landed is False


def test_an_all_missing_batch_with_a_short_read_records_nothing(conn, loader):
    """The read itself looks broken -- fewer usable tracks came back than uris
    went in -- so a systemic fault must not flag every uri in the run."""
    # source: foreign-roundtrip-D.md §4.3 step 6 -- "record nothing on a
    # genuinely broken one ... poisoning 100 good uris is the worse error".
    requested = [uri("m1"), uri("m2"), uri("m3")]
    loader.substitute(uri("m1"), "sub1", linked_from=False)
    loader.substitute(uri("m2"), "sub2", linked_from=False)
    loader.drop(uri("m3"))

    landed = roundtrip._run_batch(conn, loader, 1, 1, requested)

    assert failed_uris(conn) == set()
    assert landed is False


def test_a_partial_missing_batch_records_only_what_did_not_come_back(conn, loader):
    """The commoner path into `not_returned`, and the one that produces most
    of reconciliation's real work."""
    # source: foreign-roundtrip-D.md §4.3 -- what didn't come back is found by
    # set difference, computed after the fact; no positions, no counting.
    loader.drop(uri("gone"))

    landed = roundtrip._run_batch(conn, loader, 1, 1, [uri("here"), uri("gone")])

    assert failed_uris(conn) == {uri("gone")}
    assert failed_state(conn, uri("gone")) == roundtrip.STATE_NOT_RETURNED
    # Success is measured against what was asked for, and one of two resolved.
    assert landed is True


# -- 400 narrowing (§4.3) ---------------------------------------------------
#
# `_probe_dead` builds its own bare requests.Session against open.spotify.com,
# which conftest's network guard blocks outright -- correctly, and its error
# says to fake at the function under test. These monkeypatch the probe itself,
# which is also the only way to state what the probe *found* rather than what
# the public web happened to serve on the day.


def test_a_400_is_narrowed_off_quota_and_the_survivors_retried(conn, loader, monkeypatch):
    """One dead uri poisons the whole write. Bisecting via the API would spend
    the quota the run exists to protect, so the narrowing happens against the
    public web page instead."""
    # source: foreign-roundtrip-D.md §4.3 -- on a 400, narrow once with the
    # off-quota probe and retry with the survivors.
    loader.fail("playlist_replace_items", fakes.bad_request(), times=1)
    monkeypatch.setattr(roundtrip, "_probe_dead", lambda uris: {uri("bad")})

    loaded = roundtrip._load_with_repair(
        conn, loader, "batch 1/1", [uri("good"), uri("bad")]
    )

    assert loaded == [uri("good")]
    assert failed_state(conn, uri("bad")) == roundtrip.STATE_DEAD
    assert loader.replacements[-1] == (roundtrip.LOADER_ID, [uri("good")])


def test_a_retry_that_fails_too_records_the_survivors_as_load_failed(conn, loader, monkeypatch):
    """The probe is best-effort narrowing -- a withdrawn track may still serve
    a page -- so the retry failing again is the real backstop."""
    # source: foreign-roundtrip-D.md §4.3 -- survivors of a failed retry are
    # recorded `load_failed` and the batch is dead.
    loader.fail("playlist_replace_items", fakes.bad_request(), times=2)
    monkeypatch.setattr(roundtrip, "_probe_dead", lambda uris: set())

    landed = roundtrip._run_batch(conn, loader, 1, 1, [uri("x")])

    assert landed is False
    assert failed_state(conn, uri("x")) == roundtrip.STATE_LOAD_FAILED


# -- `_fail_uris` is an upsert, not insert-or-ignore (P1-007 (A)) -----------


def test_a_probe_confirmed_dead_uri_overwrites_a_not_returned_row(conn):
    """P1-007's bug (A). Under `INSERT OR IGNORE` the `dead` verdict was
    silently dropped, the row stayed `not_returned`, and `_reconcile_list`
    re-selected that uri into *every* future run to be re-probed forever."""
    # source: foreign-roundtrip-D.md §4.5 -- "a probe-confirmed dead never is
    # [worth spending requests on]"; the transition is what makes that true.
    roundtrip._fail_uris(conn, [uri("x")], roundtrip.STATE_NOT_RETURNED)
    assert failed_state(conn, uri("x")) == roundtrip.STATE_NOT_RETURNED

    roundtrip._fail_uris(conn, [uri("x")], roundtrip.STATE_DEAD, "404")

    assert failed_state(conn, uri("x")) == roundtrip.STATE_DEAD
    assert roundtrip._reconcile_list(conn) == []


def test_a_dead_uri_is_excluded_from_the_work_list_and_from_reconciliation(conn):
    # source: foreign-roundtrip-D.md §4.5 -- probe-confirmed 404s are excluded;
    # re-requesting them spends quota for nothing.
    builders.make_play(conn, uri=uri("dead-one"))
    roundtrip._fail_uris(conn, [uri("dead-one")], roundtrip.STATE_DEAD)

    assert roundtrip._work_list(conn) == []
    assert roundtrip._reconcile_list(conn) == []


def test_a_resolved_uri_drops_out_of_the_reconcile_list(conn):
    """"Done" stays derived: a uri is finished when it resolves through
    `played_uri_track`, never because something was checkpointed."""
    # source: foreign-roundtrip-D.md §4.5 -- the list is "recorded as
    # not-returned, *and still unresolved*".
    roundtrip._fail_uris(conn, [uri("later")], roundtrip.STATE_NOT_RETURNED)
    assert roundtrip._reconcile_list(conn) == [uri("later")]

    builders.make_track(conn, track_id="later")

    assert roundtrip._reconcile_list(conn) == []


# -- Reconciliation and stopping (P1-007 (B)) -------------------------------


def test_reconcile_reports_a_stop_that_cut_it_short(conn, loader, monkeypatch):
    """P1-007's bug (B): `_reconcile` used to return on a stop without
    signalling it, so `_run`'s outcome stayed "completed" -- the run then spent
    the clear-playlist request and recorded a completed run, where §6.1 says a
    stop skips the clear and ends stopped-early."""
    # source: foreign-roundtrip-D.md §6.1 -- a stop should "skip the clear
    # (§4.4), and end in the stopped-early state".
    roundtrip._fail_uris(conn, [uri("pending")], roundtrip.STATE_NOT_RETURNED)
    monkeypatch.setattr(jobs, "stop_requested", lambda: True)

    assert roundtrip._reconcile(conn, loader) is True
    # Stopped before it asked Spotify for anything.
    assert loader.replacements == []


def test_reconcile_with_nothing_to_do_is_not_a_stop(conn, loader):
    """The two falsey outcomes have to stay distinguishable: nothing to
    reconcile is a completed run, not a stopped one."""
    # source: foreign-roundtrip-D.md §4.5 -- the pass runs only over uris
    # worth another look.
    assert roundtrip._reconcile(conn, loader) is False


def test_a_stop_during_reconciliation_skips_the_clear_and_records_stopped(conn, loader, monkeypatch):
    """The whole of P1-007 (B), through the real `_run`."""
    # source: foreign-roundtrip-D.md §6.1 -- outcome "stopped", and the clear
    # (§4.4) is skipped because a quota stop has no requests to spare.
    roundtrip._fail_uris(conn, [uri("pending")], roundtrip.STATE_NOT_RETURNED)
    monkeypatch.setattr(jobs, "stop_requested", lambda: True)

    roundtrip._run(reconcile_only=True)

    outcome = conn.execute(
        "SELECT outcome FROM roundtrip_run ORDER BY id DESC LIMIT 1"
    ).fetchone()["outcome"]
    assert outcome == "stopped"
    # The clear is `playlist_replace_items(LOADER_ID, [])`; it must not have run.
    assert loader.replacements == []


def test_an_uninterrupted_reconcile_only_run_completes_and_clears(conn, loader):
    """The positive control for the test above -- without it, a `_run` that
    was broken in some other way would make that assertion pass for free."""
    # source: foreign-roundtrip-D.md §4.4 -- the run ends by clearing the
    # loader; tidiness only, and one request whatever is in there.
    roundtrip._run(reconcile_only=True)

    outcome = conn.execute(
        "SELECT outcome FROM roundtrip_run ORDER BY id DESC LIMIT 1"
    ).fetchone()["outcome"]
    assert outcome == "completed"
    assert loader.replacements == [(roundtrip.LOADER_ID, [])]


# -- The work list and the queue partition (P1-017) -------------------------


def test_the_work_list_covers_played_and_wanted_uris_with_plays_first(conn):
    """One list, two arms: a played-but-unknown uri and an album-tracklist uri
    Symr has no track row for. A wanted uri has no plays by definition, so
    `plays = 0` sorts it after every played one with no special case."""
    # source: grouping-fixes-backfill-M.md §4.3 -- wanted uris are merged into
    # the round-trip's existing work list rather than being a second queue.
    builders.make_play(conn, uri=uri("played"))
    conn.execute(
        "INSERT INTO wanted_uri (uri, source) VALUES (?, 'album')", (uri("wanted"),)
    )
    conn.commit()

    assert roundtrip._work_list(conn) == [uri("played"), uri("wanted")]


def test_a_wanted_uri_that_was_also_played_appears_once(conn):
    # source: grouping-fixes-backfill-M.md §4.3 -- "the NOT IN against `play`
    # keeps a uri that's both wanted and played from appearing twice."
    builders.make_play(conn, uri=uri("both"))
    conn.execute(
        "INSERT INTO wanted_uri (uri, source) VALUES (?, 'album')", (uri("both"),)
    )
    conn.commit()

    assert roundtrip._work_list(conn) == [uri("both")]


def set_muted(conn, muted):
    """The listening arm's [Clear] -- a meta flag, not a delete."""
    db.set_meta(conn, "roundtrip_listening_muted", "1" if muted else "0")
    conn.commit()


def queue_fixture(conn):
    """One uri in each of the three arms of the partition."""
    builders.make_play(conn, uri=uri("listened"))
    conn.execute("INSERT INTO wanted_uri (uri, source) VALUES (?, 'album')", (uri("page"),))
    conn.execute(
        "INSERT INTO wanted_uri (uri, source) VALUES (?, 'backfill')", (uri("filled"),)
    )
    conn.commit()


def test_the_three_queue_counts_partition_the_work_list(conn):
    # source: grouping-fixes-backfill-M.md §4.6 -- listening, album-page and
    # album-backfill "sum to remaining_uris with no double-counting".
    queue_fixture(conn)

    counts = roundtrip.counts(conn)

    assert (counts["listening_uris"], counts["album_page_uris"], counts["album_backfill_uris"]) == (1, 1, 1)
    assert (
        counts["listening_uris"] + counts["album_page_uris"] + counts["album_backfill_uris"]
        == counts["remaining_uris"]
    )
    assert counts["listening_muted"] is False


def test_muting_the_listening_arm_drops_it_from_the_work_list_only(conn):
    """The documented exception to the partition.

    The mute filter lives inside `_WORK_LIST_SQL`, so `remaining_uris` drops
    the listening arm -- but `_LISTENING_REMAINING_SQL` deliberately omits it,
    so the row still shows what muting excludes rather than a meaningless zero.
    The three counts therefore do *not* sum to `remaining_uris` while muted,
    and that is correct.
    """
    # source: grouping-fixes-backfill-M.md §4.6 (amended by P1-017) -- the
    # unmuted case is the invariant; the muted case is the stated exception.
    queue_fixture(conn)
    set_muted(conn, True)

    counts = roundtrip.counts(conn)

    assert counts["listening_muted"] is True
    assert counts["listening_uris"] == 1
    assert counts["remaining_uris"] == 2
    assert roundtrip._work_list(conn) == [uri("filled"), uri("page")]


def test_unmuting_restores_the_listening_arm(conn):
    """Clearing the listening queue is a mute, not a delete -- there are no
    rows to delete -- so it is completely reversible."""
    # source: grouping-fixes-backfill-M.md §4.6 -- "[Clear] on it sets the
    # roundtrip_listening_muted meta flag instead".
    queue_fixture(conn)
    set_muted(conn, True)
    set_muted(conn, False)

    counts = roundtrip.counts(conn)
    assert counts["listening_muted"] is False
    assert counts["remaining_uris"] == 3


def test_the_request_estimate_is_two_per_batch_plus_three(conn):
    # source: foreign-roundtrip-D.md §4.1/§4.4 -- 2 guard reads + 2 per batch
    # (replace, read back) + 1 clear.
    for index in range(roundtrip.BATCH_SIZE + 1):
        builders.make_play(conn, uri=uri(f"u{index}"))

    counts = roundtrip.counts(conn)

    assert counts["remaining_uris"] == roundtrip.BATCH_SIZE + 1
    assert counts["batches"] == 2
    assert counts["requests_estimate"] == 2 * 2 + 3
