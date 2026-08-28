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


# -- Reconciliation's matching rule (§4.5) ----------------------------------
#
# The highest-corruption-risk decision in the module: it writes
# `track_uri_alias` rows off *inferred* pairings. §4.5 permits that only on
# evidence -- normalized full title AND album artist, 1:1 in both directions --
# because guessing an unstated pairing is what silently corrupted 1,250 rows.
# Every test below is therefore built to fail against one specific weaker rule.


def played_as(conn, requested_uri, name, artist):
    """A uri the export labelled but Spotify won't serve -- §4.5's only
    independent evidence about it."""
    builders.make_play(
        conn, uri=requested_uri, reported_track_name=name, reported_artist_name=artist
    )


def substitute_track(track_id, name, album_artist):
    """A returned track nothing else accounts for. `_album_artist_keys` reads
    the *album's* artists, not the track's, because that is what the export's
    reported_artist_name is."""
    return fakes.spotify_track(
        track_id,
        name=name,
        album=fakes.spotify_album(
            f"{track_id}-album", artists=[fakes.spotify_artist(f"{track_id}-ar", album_artist)]
        ),
    )


def test_a_substitute_is_matched_on_evidence_and_never_on_position(conn):
    """The positive case, built so a positional read gets it exactly wrong.

    The candidates are returned in the opposite order to the uris that asked
    for them, so `zip(unresolved, candidates)` -- the tempting implementation
    §4.5 explicitly forbids -- produces the inverted mapping rather than this
    one. Without the crossover this test passes against position.
    """
    # source: foreign-roundtrip-D.md §4.5 -- "Auto-alias only when the
    # normalized full title *and* the album artist both match", and "Position
    # is deliberately not used, even here."
    played_as(conn, uri("x"), "Alpha", "Artist One")
    played_as(conn, uri("y"), "Beta", "Artist Two")
    candidates = [
        substitute_track("tB", "Beta", "Artist Two"),
        substitute_track("tA", "Alpha", "Artist One"),
    ]

    matched = roundtrip._match_substitutes(conn, [uri("x"), uri("y")], candidates)

    assert matched == {uri("x"): "tA", uri("y"): "tB"}


def test_a_title_match_with_a_different_album_artist_is_not_evidence(conn):
    """Half the rule is not the rule. A title-only implementation matches here."""
    # source: foreign-roundtrip-D.md §4.5 -- title *and* album artist must
    # both match.
    played_as(conn, uri("x"), "Alpha", "Artist One")

    matched = roundtrip._match_substitutes(
        conn, [uri("x")], [substitute_track("tA", "Alpha", "Someone Else")]
    )

    assert matched == {}


def test_an_album_artist_match_with_a_different_title_is_not_evidence(conn):
    """The other half, for the same reason."""
    # source: foreign-roundtrip-D.md §4.5 -- title *and* album artist.
    played_as(conn, uri("x"), "Alpha", "Artist One")

    matched = roundtrip._match_substitutes(
        conn, [uri("x")], [substitute_track("tA", "Something Else", "Artist One")]
    )

    assert matched == {}


def test_two_remixes_sharing_a_base_title_do_not_match_each_other(conn):
    """§4.5's own named counter-example, and the reason `_title_key` keeps the
    suffix. The precondition assertion is the point of the test: without it
    this passes for free the moment the two names stop sharing a base, and it
    would no longer say anything about suffix handling at all.
    """
    # source: foreign-roundtrip-D.md §4.5 -- "The title key must keep its
    # suffix -- on `normalize_title`'s base alone, `Opalite`, `Opalite - BUNT.
    # Remix` and `Opalite - Chris Lake Remix` collapse into one key and all
    # three go ambiguous."
    played_as(conn, uri("x"), "Opalite - BUNT. Remix", "Yungblud")
    candidate = substitute_track("tA", "Opalite - Chris Lake Remix", "Yungblud")

    # Same base, different suffix -- so a base-only key matches and the real
    # full key does not.
    assert (
        roundtrip._title_key("Opalite - BUNT. Remix")[0]
        == roundtrip._title_key("Opalite - Chris Lake Remix")[0]
    )

    assert roundtrip._match_substitutes(conn, [uri("x")], [candidate]) == {}


def test_one_candidate_claimed_by_two_uris_is_written_for_neither(conn):
    """1:1 in *both* directions. Two uris the export labelled identically both
    evidence the same returned track, so neither pairing is safe."""
    # source: foreign-roundtrip-D.md §4.5 -- "the pairing is 1:1 in both
    # directions", and the code's own note that a candidate claimed by two
    # uris is ambiguous in the other direction.
    played_as(conn, uri("x"), "Alpha", "Artist One")
    played_as(conn, uri("y"), "Alpha", "Artist One")

    matched = roundtrip._match_substitutes(
        conn, [uri("x"), uri("y")], [substitute_track("tA", "Alpha", "Artist One")]
    )

    assert matched == {}


def test_one_uri_matching_two_candidates_is_written_for_neither(conn):
    """The same ambiguity from the other side: the evidence does not single
    out a track, so nothing is written."""
    # source: foreign-roundtrip-D.md §4.5 -- "Everything else is flagged
    # `needs a manual alias`, never guessed."
    played_as(conn, uri("x"), "Alpha", "Artist One")

    matched = roundtrip._match_substitutes(
        conn,
        [uri("x")],
        [
            substitute_track("tA1", "Alpha", "Artist One"),
            substitute_track("tA2", "Alpha", "Artist One"),
        ],
    )

    assert matched == {}


def test_a_uri_the_export_never_labelled_cannot_be_matched(conn):
    """No evidence, no pairing -- and the candidate here is *equally*
    evidence-free, which is the only fixture that tests the guard.

    Against a candidate with a real name, dropping the guard changes nothing:
    the unlabelled uri's empty key matches no real title, so the test passes
    either way and says nothing (the P2-005 shape). Give the returned track an
    empty name and an unnamed album artist and both sides normalize to the
    same empty key -- so without the guard, absence of evidence on both sides
    reads as agreement and writes a `track_uri_alias` row on nothing at all.
    """
    # source: foreign-roundtrip-D.md §4.5 -- the export's reported names are
    # "the only independent evidence available about a uri Spotify won't
    # serve", and everything unevidenced "is flagged `needs a manual alias`,
    # never guessed".
    builders.make_play(conn, uri=uri("x"))  # reported_* default to NULL

    matched = roundtrip._match_substitutes(
        conn, [uri("x")], [substitute_track("tA", "", "")]
    )

    assert matched == {}


# -- The manual alias step (§4.6) -------------------------------------------


def needs_review(conn, requested_uri, name, artist, plays=1):
    """A uri §4.5 declined to decide, sitting in the review table with the
    export's labels behind it."""
    for _ in range(plays):
        played_as(conn, requested_uri, name, artist)
    roundtrip._fail_uris(conn, [requested_uri], roundtrip.STATE_NEEDS_REVIEW)
    conn.commit()


def alias_target(conn, requested_uri):
    row = conn.execute(
        "SELECT track_id FROM track_uri_alias WHERE requested_uri = ?", (requested_uri,)
    ).fetchone()
    return row["track_id"] if row else None


def test_manual_candidates_are_offered_on_the_title_base_alone(conn):
    """Deliberately *looser* than §4.5's automatic rule, and the inverse of
    `test_two_remixes_sharing_a_base_title_do_not_match_each_other`: the exact
    pairing the automatic rule must refuse is the one a human is here to
    judge. An implementation that reused §4.5's full key offers nothing.
    """
    # source: foreign-roundtrip-D.md §4.6 -- "Candidates are matched on the
    # normalized title **base only** -- looser than §4.5's automatic rule,
    # which also requires the suffix to match. That looseness is the entire
    # point of the manual step."
    needs_review(conn, uri("x"), "Opalite", "Yungblud")
    builders.make_track(conn, "remix", name="Opalite - BUNT. Remix")

    rows = roundtrip.manual_alias_rows(conn)

    assert [r["requested_uri"] for r in rows] == [uri("x")]
    assert [c["track_id"] for c in rows[0]["candidates"]] == ["remix"]


def test_a_track_sharing_no_base_is_not_offered_as_a_candidate(conn):
    """The negative control: base matching is still matching, not "offer
    everything"."""
    # source: foreign-roundtrip-D.md §4.6 -- candidates are matched, and the
    # real four offered "exactly one candidate each".
    needs_review(conn, uri("x"), "Opalite", "Yungblud")
    builders.make_track(conn, "unrelated", name="Something Else Entirely")

    rows = roundtrip.manual_alias_rows(conn)

    assert rows[0]["candidates"] == []


def test_saving_a_manual_alias_records_it_and_closes_the_review_row(conn):
    """The whole point of the step: the uri resolves from here on, and stops
    being listed as unresolved."""
    # source: foreign-roundtrip-D.md §4.6 -- the endpoint "writes the aliases
    # and drops those uris from `roundtrip_failed_uri`".
    needs_review(conn, uri("x"), "Opalite", "Yungblud")
    builders.make_track(conn, "remix", name="Opalite - BUNT. Remix")

    assert roundtrip.set_manual_aliases(conn, [(uri("x"), "remix")]) == 1

    assert alias_target(conn, uri("x")) == "remix"
    assert failed_state(conn, uri("x")) is None
    # And it now resolves the way any other alias does.
    assert roundtrip._unresolved(conn, [uri("x")]) == []


def test_one_bad_pair_leaves_every_other_pair_unwritten(conn):
    """§4.6's all-or-nothing clause, and the ordering is the test: the *good*
    pair comes first, so an implementation that validated and wrote in one
    pass would already have written it before reaching the bad one. Put the
    bad pair first and this passes against that implementation.
    """
    # source: foreign-roundtrip-D.md §4.6 -- the endpoint "validates every
    # pair before writing any of them (one stale row can't leave the rest
    # half-applied)".
    needs_review(conn, uri("good"), "Alpha", "Artist One")
    builders.make_track(conn, "tA", name="Alpha")

    with pytest.raises(ValueError):
        roundtrip.set_manual_aliases(
            conn, [(uri("good"), "tA"), (uri("never-reviewed"), "tA")]
        )

    assert alias_target(conn, uri("good")) is None
    assert failed_state(conn, uri("good")) == roundtrip.STATE_NEEDS_REVIEW


def test_a_uri_not_awaiting_review_cannot_be_aliased(conn):
    """Not a general "rewrite any mapping" lever -- it only ever resolves a
    uri the round-trip already flagged for a human."""
    # source: foreign-roundtrip-D.md §4.6 -- the table is "one row per uri
    # awaiting review"; roundtrip.set_manual_aliases' docstring makes the
    # restriction explicit.
    roundtrip._fail_uris(conn, [uri("dead")], roundtrip.STATE_DEAD)
    conn.commit()
    builders.make_track(conn, "tA")

    with pytest.raises(ValueError):
        roundtrip.set_manual_aliases(conn, [(uri("dead"), "tA")])

    assert alias_target(conn, uri("dead")) is None


def test_an_unknown_track_id_is_refused(conn):
    """The other validation arm. An alias to a track that does not exist would
    make the uri resolve through `played_uri_track` to nothing."""
    # source: foreign-roundtrip-D.md §4.6 -- every pair is validated before
    # any is written.
    needs_review(conn, uri("x"), "Alpha", "Artist One")

    with pytest.raises(ValueError):
        roundtrip.set_manual_aliases(conn, [(uri("x"), "no-such-track")])

    assert alias_target(conn, uri("x")) is None


# -- `_reconcile_batch`: what one reconciliation batch decides (§4.5) -------


def test_a_uri_spotify_serves_this_time_stops_being_a_failure(conn, loader):
    """§4.5's last paragraph. The row has to go, not just stop being selected:
    a resolved uri never returns to this pass, so a row left behind would sit
    there claiming it wasn't returned for good.
    """
    # source: foreign-roundtrip-D.md §4.5 -- "Leaving the row behind would
    # strand it -- a resolved uri never returns to this pass, so it would sit
    # in the failures table claiming it wasn't returned, permanently."
    roundtrip._fail_uris(conn, [uri("back")], roundtrip.STATE_NOT_RETURNED)
    conn.commit()

    roundtrip._reconcile_batch(conn, loader, 1, 1, [uri("back")])

    assert failed_state(conn, uri("back")) is None
    assert roundtrip._unresolved(conn, [uri("back")]) == []


def test_a_batch_aliases_the_evidenced_substitute_and_flags_the_rest(conn, loader):
    """Both outcomes in one batch, so neither can pass by the other's route.

    `sub` comes back as an unlabelled substitute the export's own labels
    identify; `mystery` comes back as one they don't. §4.5 writes an alias for
    the first and refuses to guess the second.
    """
    # source: foreign-roundtrip-D.md §4.5 -- auto-alias only on matching title
    # and album artist; "Everything else is flagged `needs a manual alias`,
    # never guessed."
    played_as(conn, uri("sub"), "Alpha", "Artist One")
    played_as(conn, uri("mystery"), "Beta", "Artist Two")
    roundtrip._fail_uris(
        conn, [uri("sub"), uri("mystery")], roundtrip.STATE_NOT_RETURNED
    )
    conn.commit()

    loader.add_track(substitute_track("tA", "Alpha", "Artist One"))
    loader.add_track(substitute_track("tZ", "Nothing Like It", "Nobody"))
    loader.substitute(uri("sub"), "tA", linked_from=False)
    loader.substitute(uri("mystery"), "tZ", linked_from=False)

    roundtrip._reconcile_batch(conn, loader, 1, 1, [uri("sub"), uri("mystery")])

    assert alias_target(conn, uri("sub")) == "tA"
    assert failed_state(conn, uri("sub")) is None

    assert alias_target(conn, uri("mystery")) is None
    assert failed_state(conn, uri("mystery")) == roundtrip.STATE_NEEDS_REVIEW


# -- The circuit breaker (§5) ----------------------------------------------


def scripted_batches(monkeypatch, results):
    """Drives `_run` over one batch per uri with a fixed pass/fail script.

    `_run`'s breaker is its own logic -- count consecutive failures, reset on
    success, stop at three -- so the batch body is stubbed rather than
    contrived into failing, which is the only way to state the F,F,T,F,F
    sequence the consecutive-vs-total distinction needs.
    """
    attempted = []

    def fake_run_batch(conn, sp, index, total, batch):
        attempted.append(batch)
        return results[index - 1]

    monkeypatch.setattr(roundtrip, "BATCH_SIZE", 1)
    monkeypatch.setattr(roundtrip, "_run_batch", fake_run_batch)
    return attempted


def last_outcome(conn):
    return conn.execute(
        "SELECT outcome FROM roundtrip_run ORDER BY id DESC LIMIT 1"
    ).fetchone()["outcome"]


def test_three_consecutive_failed_batches_stop_the_run(conn, loader, monkeypatch):
    """Without the breaker a systemic fault -- a bad token, a revoked scope --
    fails every batch and fires a public probe per uri for nothing."""
    # source: foreign-roundtrip-D.md §5 -- "Three consecutive failed batches
    # stop the run... The run ends in the normal stopped-early state with the
    # reason logged."
    for n in range(5):
        builders.make_play(conn, uri=uri(f"u{n}"))
    attempted = scripted_batches(monkeypatch, [False] * 5)

    roundtrip._run()

    assert last_outcome(conn) == "breaker"
    # It stopped at the third, rather than running the remaining two.
    assert len(attempted) == 3


def test_scattered_failures_never_trip_the_breaker(conn, loader, monkeypatch):
    """The discriminating case for "consecutive". Four of these five batches
    fail -- more than the limit -- but never three in a row, so an
    implementation counting *total* failures stops the run and this fails.
    """
    # source: foreign-roundtrip-D.md §5 -- "The count resets on any successful
    # batch, so scattered dead uris never trip it."
    for n in range(5):
        builders.make_play(conn, uri=uri(f"u{n}"))
    attempted = scripted_batches(monkeypatch, [False, False, True, False, False])

    roundtrip._run()

    assert last_outcome(conn) == "completed"
    assert len(attempted) == 5


def test_a_track_that_already_named_its_uri_is_not_reused_as_a_candidate(conn, loader):
    """"Nothing else accounts for it" is a real filter, not a formality.

    `labelled` comes back as an honestly-labelled substitute, so the batch
    already writes it a real alias off `linked_from`. That same track also
    matches what the export recorded for `other`, which came back from
    nothing. Without the filter it is offered as evidence a second time and
    `other` is aliased to it too -- one track claimed by two requested uris,
    on a pairing that was already spoken for.
    """
    # source: foreign-roundtrip-D.md §4.5 -- reconciliation considers only
    # "each returned track that nothing else accounts for (not one of the
    # requested uris, no `linked_from`)".
    played_as(conn, uri("other"), "Alpha", "Artist One")
    roundtrip._fail_uris(
        conn, [uri("labelled"), uri("other")], roundtrip.STATE_NOT_RETURNED
    )
    conn.commit()

    loader.add_track(substitute_track("tA", "Alpha", "Artist One"))
    loader.substitute(uri("labelled"), "tA", linked_from=True)
    loader.drop(uri("other"))

    roundtrip._reconcile_batch(conn, loader, 1, 1, [uri("labelled"), uri("other")])

    # tA belongs to `labelled`, stated by Spotify itself.
    assert alias_target(conn, uri("labelled")) == "tA"
    # ...so it is not also evidence for `other`, which stays for a human.
    assert alias_target(conn, uri("other")) is None
    assert failed_state(conn, uri("other")) == roundtrip.STATE_NEEDS_REVIEW


# -- `_run`'s terminal states ----------------------------------------------


def test_a_rate_limit_ends_the_run_as_rate_limited_and_skips_the_clear(conn, loader, monkeypatch):
    """A quota block is the one terminal state with a spend consequence: the
    clear (§4.4) is another request against a quota already refusing them.
    """
    # source: foreign-roundtrip-D.md §5 -- "**Anything else** -- the usual
    # `except` -> `phase="error"`, message in the status, committed work
    # kept", and §6.1's `retry_at` on the terminal state.
    builders.make_play(conn, uri=uri("u0"))

    def blocked(*args):
        raise jobs.RateLimited(3600)

    monkeypatch.setattr(roundtrip, "BATCH_SIZE", 1)
    monkeypatch.setattr(roundtrip, "_run_batch", blocked)

    roundtrip._run()

    status = roundtrip._status.get()
    assert status["phase"] == "error"
    assert status["outcome"] == "rate_limited"
    assert status["retry_at"] is not None
    assert last_outcome(conn) == "rate_limited"
    # No clear -- that is a request, and the quota is what just refused one.
    assert loader.replacements == []


def test_any_other_failure_ends_the_run_as_an_error_with_the_work_kept(conn, loader, monkeypatch):
    """The generic arm. It must stay distinguishable from the rate-limited one
    -- `retry_at` is what the page offers, and there is nothing to offer here.
    """
    # source: foreign-roundtrip-D.md §5 -- "**Anything else** ... committed
    # work kept".
    builders.make_play(conn, uri=uri("u0"))

    def broken(*args):
        raise RuntimeError("something went wrong")

    monkeypatch.setattr(roundtrip, "BATCH_SIZE", 1)
    monkeypatch.setattr(roundtrip, "_run_batch", broken)

    roundtrip._run()

    status = roundtrip._status.get()
    assert status["phase"] == "error"
    assert status["outcome"] == "error"
    assert status["retry_at"] is None
    assert last_outcome(conn) == "error"


def test_a_stop_between_batches_finishes_the_current_one_and_skips_the_clear(
    conn, loader, monkeypatch
):
    """Cooperative stopping: the flag is polled at a batch boundary, so the
    batch already in flight completes and commits rather than being killed.

    The stop is armed *after* the first poll, so batch 1 runs and batch 2 does
    not -- a fixture that stopped from the start would exercise the same code
    path as `reconcile_only` and never show that work in progress survives.
    """
    # source: foreign-roundtrip-D.md §6.1 -- "the run finishes its current
    # batch, commits, skips the clear (§4.4), and ends in the stopped-early
    # state".
    for n in range(3):
        builders.make_play(conn, uri=uri(f"u{n}"))
    attempted = scripted_batches(monkeypatch, [True, True, True])

    polls = []

    def stop_after_the_first_batch():
        polls.append(1)
        return len(polls) > 1

    monkeypatch.setattr(jobs, "stop_requested", stop_after_the_first_batch)

    roundtrip._run()

    assert last_outcome(conn) == "stopped"
    assert len(attempted) == 1
    assert loader.replacements == []


def test_a_run_with_no_client_is_an_error_not_a_crash(conn, monkeypatch):
    """The guard runs before anything is written, so an unauthenticated run
    must cost nothing and record itself honestly."""
    # source: roundtrip._run -- `if sp is None: raise
    # RuntimeError("not_authenticated")`, before _guard and before any write.
    monkeypatch.setattr(roundtrip, "get_spotify_client", lambda: None)

    roundtrip._run()

    assert roundtrip._status.get()["phase"] == "error"
    assert last_outcome(conn) == "error"
    # The *message* is the discriminating part. Without the explicit check
    # `_guard(conn, None)` is reached and dies on an attribute of None, which
    # is still an "error" run -- so asserting the outcome alone is a test that
    # cannot fail. What the page shows has to say which of the two happened.
    assert "not_authenticated" in roundtrip._status.get()["error"]


# -- The routes (app.py) ----------------------------------------------------
#
# Everything below tests `app.py`'s round-trip and round-trip-queue endpoints
# rather than `roundtrip.py`. They live here because the partition that put
# them somewhere is by feature domain, not by module: the wiring from a button
# to the module function beneath it is only visible to a route test, and the
# module tests above cannot see it at all.


def test_the_start_route_runs_the_main_pass_over_the_work_list(
    client, conn, loader, run_jobs_inline
):
    """`/api/roundtrip/start` -- the `reconcile_only=False` decorator.

    The discriminating assertion is that the queued uri reached the loader
    playlist, not that the POST returned 200: a route that claimed the slot
    and did nothing, or one that passed `reconcile_only=True`, answers 200
    just the same.
    """
    # source: S_sweep.md §3 -- `true` at app.py:861. The mutant flips the
    # body to {"started": False} while the run still happens, so the flag is
    # asserted beside the behaviour rather than on its own.
    builders.make_play(conn, uri=uri("queued"))
    conn.commit()

    resp = client.post("/api/roundtrip/start")

    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}
    assert run_jobs_inline == ["roundtrip"]
    assert (roundtrip.LOADER_ID, [uri("queued")]) in loader.replacements
    assert conn.execute(
        "SELECT 1 FROM track WHERE track_id = 'queued'"
    ).fetchone() is not None


def test_the_reconcile_route_skips_the_main_pass(client, conn, loader, run_jobs_inline):
    """`/api/roundtrip/reconcile` -- the second decorator on the same view,
    and the only thing that separates the two is the `reconcile_only` default.

    Both routes answer an identical 200 body, so the status code proves
    nothing: what says the default reached `roundtrip.start` is that the
    queued uri was never loaded and never resolved.
    """
    # source: entity/route convention P2-010 -- an alternate render path needs
    # a semantic assertion beside its catalog case; and S_sweep.md §3, `true`
    # at app.py:861.
    builders.make_play(conn, uri=uri("queued"))
    conn.commit()

    resp = client.post("/api/roundtrip/reconcile")

    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}
    assert run_jobs_inline == ["roundtrip"]
    # Only the tidying clear at the end of the run -- the batch pass never ran.
    assert loader.replacements == [(roundtrip.LOADER_ID, [])]
    assert conn.execute(
        "SELECT 1 FROM track WHERE track_id = 'queued'"
    ).fetchone() is None


def test_the_alias_route_saves_a_well_formed_batch(client, conn):
    """The wiring from the review table's Save button to
    `roundtrip.set_manual_aliases`, which only a route test can see -- the
    module tests above call it directly and would pass against a route that
    never reached it.
    """
    # source: S_sweep.md §3 -- `or` at app.py:882 and `true` at app.py:891.
    # The `or` mutant makes `(body.get("aliases") and [])` collapse a supplied
    # list to empty, so the route 400s on a batch it should have saved; the
    # `true` mutant flips the reported flag while the write still happens.
    needs_review(conn, uri("x"), "Opalite", "Yungblud")
    builders.make_track(conn, "remix", name="Opalite - BUNT. Remix")
    conn.commit()

    resp = client.post(
        "/api/roundtrip/alias",
        json={"aliases": [{"requested_uri": uri("x"), "track_id": "remix"}]},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "saved": 1}
    assert alias_target(conn, uri("x")) == "remix"
    assert failed_state(conn, uri("x")) is None


def test_the_alias_route_refuses_a_body_with_no_aliases_key(client, conn):
    """What the `or []` fallback is for. A body that names no aliases at all
    is a client mistake and must come back as the same 400 an empty list does
    -- not as a 500 from iterating `None`, which is what the fallback's
    absence produces.
    """
    # source: S_sweep.md §3 -- `or` at app.py:882. The mutant leaves
    # `body.get("aliases")` (None) as the loop's iterable and the request dies
    # in a TypeError, so the discriminating assertion is the 400 and its
    # description, not that the request failed.
    needs_review(conn, uri("x"), "Opalite", "Yungblud")

    resp = client.post("/api/roundtrip/alias", json={"not_aliases": []})

    assert resp.status_code == 400
    assert "non-empty list" in resp.get_json()["detail"]
    assert alias_target(conn, uri("x")) is None


def test_the_alias_route_refuses_a_half_formed_entry_before_the_module_sees_it(
    client, conn, monkeypatch
):
    """The route validates the *shape* of every entry itself, so a pair with
    no `track_id` never reaches `roundtrip.set_manual_aliases` at all.

    Both layers refuse, so the status code cannot tell them apart -- weaken
    the route's check and the module still raises `ValueError` and still 400s.
    The discriminating assertions are that the writer was never called and
    that the description is the route's own.
    """
    # source: S_sweep.md §3 -- `and` at app.py:884. The mutant relaxes
    # `uri and track_id` to `uri or track_id`, so an entry carrying only one
    # of the two passes the route's guard and is handed to the writer to
    # reject instead.
    needs_review(conn, uri("x"), "Opalite", "Yungblud")
    needs_review(conn, uri("y"), "Alpha", "Artist One")
    builders.make_track(conn, "remix", name="Opalite - BUNT. Remix")
    conn.commit()

    calls = []
    real = roundtrip.set_manual_aliases
    monkeypatch.setattr(
        roundtrip,
        "set_manual_aliases",
        lambda c, pairs: (calls.append(list(pairs)), real(c, pairs))[1],
    )

    resp = client.post(
        "/api/roundtrip/alias",
        json={
            "aliases": [
                {"requested_uri": uri("x"), "track_id": "remix"},
                {"requested_uri": uri("y")},
            ]
        },
    )

    assert resp.status_code == 400
    assert calls == []
    assert "non-empty list" in resp.get_json()["detail"]
    # And the well-formed half of the batch was not written either.
    assert alias_target(conn, uri("x")) is None


def test_the_clear_failures_route_reopens_every_failed_uri(client, conn):
    """[Clear] beside the failed-uri table. The point is not that rows
    disappear but that the uris come *back into the work list* -- clearing
    only ever re-opens work, which is why it needs no confirm step.
    """
    # source: S_sweep.md §3 -- `true` at app.py:897, asserted beside the
    # behaviour it reports (the mutant flips the flag while the delete still
    # happens). The behavioural half is roundtrip.clear_failures' docstring:
    # "Empties roundtrip_failed_uri so a later run retries those uris."
    for name in ("dead", "review", "untouched"):
        builders.make_play(conn, uri=uri(name))
    roundtrip._fail_uris(conn, [uri("dead")], roundtrip.STATE_DEAD)
    roundtrip._fail_uris(conn, [uri("review")], roundtrip.STATE_NEEDS_REVIEW)
    conn.commit()
    assert set(roundtrip._work_list(conn)) == {uri("untouched")}

    resp = client.post("/api/roundtrip/clear-failures")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert failed_uris(conn) == set()
    assert set(roundtrip._work_list(conn)) == {
        uri("dead"),
        uri("review"),
        uri("untouched"),
    }


def test_clearing_one_wanted_source_leaves_the_other_source_intact(client, conn):
    """The queue box's two [Clear] buttons, and the reason this test asserts
    on *which* rows survive rather than how many.

    `source = ?` inverted to `source <> ?` deletes exactly the rows the user
    did not ask about and keeps the ones they did -- a silent data loss that
    every count-only assertion passes. The two sources are given different row
    counts so even a count assertion could not coincide.
    """
    # source: S_sweep.md §3 -- `sql=` at app.py:914 (the DELETE's comparison)
    # and `true` at app.py:916. grouping-fixes-backfill-M.md §4.6: each row's
    # Clear removes that row's own queue and nothing else.
    conn.executemany(
        "INSERT INTO wanted_uri (uri, source) VALUES (?, ?)",
        [
            (uri("page-a"), "album"),
            (uri("page-b"), "album"),
            (uri("filled"), "backfill"),
        ],
    )
    conn.commit()

    resp = client.post("/api/roundtrip/wanted/clear", json={"source": "album"})

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert {
        (row["uri"], row["source"])
        for row in conn.execute("SELECT uri, source FROM wanted_uri")
    } == {(uri("filled"), "backfill")}
    counts = roundtrip.counts(conn)
    assert (counts["album_page_uris"], counts["album_backfill_uris"]) == (0, 1)


def test_the_mute_route_drops_the_listening_arm_and_unmutes_it_again(client, conn):
    """The listening row's [Clear] and [Re-add]. One endpoint serves both, and
    what it stores is derived from the request body -- so a route that ignored
    `muted` and always stored "1" would answer both calls identically.

    The behavioural half is what the flag is *for*: the listening arm leaves
    the work list while muted and comes back when it is cleared.
    """
    # source: S_sweep.md §3 -- `true` at app.py:927, asserted beside the
    # behaviour (the mutant flips the flag while the meta write still lands).
    # grouping-fixes-backfill-M.md §4.6: "[Clear] on it sets the
    # roundtrip_listening_muted meta flag instead", and it is fully reversible.
    queue_fixture(conn)

    muted = client.post("/api/roundtrip/listening/mute", json={"muted": True})

    assert muted.status_code == 200
    assert muted.get_json() == {"ok": True}
    assert db.get_meta(conn, "roundtrip_listening_muted") == "1"
    assert set(roundtrip._work_list(conn)) == {uri("page"), uri("filled")}
    assert roundtrip.counts(conn)["listening_muted"] is True

    unmuted = client.post("/api/roundtrip/listening/mute", json={"muted": False})

    assert unmuted.get_json() == {"ok": True}
    assert db.get_meta(conn, "roundtrip_listening_muted") == "0"
    assert set(roundtrip._work_list(conn)) == {
        uri("listened"),
        uri("page"),
        uri("filled"),
    }
    assert roundtrip.counts(conn)["listening_muted"] is False


def test_the_isrc_clear_route_closes_its_own_queue_row_and_no_other(client, conn):
    """The fourth queue row's [Clear]. It settles rather than deletes, so the
    observable outcome is that *that row's* count goes to zero -- the other
    arms of the partition are untouched, and a track that has an ISRC was
    never in the set and must not be settled either.
    """
    # source: S_sweep.md §3 -- `true` at app.py:936, asserted beside the
    # behaviour (the mutant flips the flag while the settle still lands).
    # scrobbling-R.md §5.3: track_isrc_absent is arm 3's own stop condition.
    for track_id in ("noisrc-a", "noisrc-b"):
        builders.make_track(conn, track_id, isrc=None)
        builders.make_play(conn, uri=uri(track_id))
    builders.make_track(conn, "has-isrc", isrc="GBAAA0000001")
    builders.make_play(conn, uri=uri("has-isrc"))
    conn.execute("INSERT INTO wanted_uri (uri, source) VALUES (?, 'album')", (uri("page"),))
    conn.commit()
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 2

    resp = client.post("/api/roundtrip/incomplete-isrc/clear")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    counts = roundtrip.counts(conn)
    assert counts["incomplete_isrc_uris"] == 0
    assert counts["album_page_uris"] == 1
    assert {
        row["track_id"] for row in conn.execute("SELECT track_id FROM track_isrc_absent")
    } == {"noisrc-a", "noisrc-b"}
