"""The derived work list: which playlists a pull reads, and in what order.

`docs/specs/partial-pulls-J.md` §2 is the spec, and its central claim is that
**nothing is checkpointed** -- the work list is re-derived from `snapshot`'s
own columns on every run, which is what makes Refresh and Full pull their own
resume. The rules here are the whole of that derivation:

- `_is_stale` (§2.2) -- the refresh rule;
- `_is_full_pull_target` (§2.3) -- the refresh rule *or* not yet done for this
  epoch, since a forced pull exists precisely to re-read playlists whose
  `snapshot_id` has not changed;
- `_resolve_force_epoch` (§2.4) -- resume this epoch or mint a new one;
- `_order_targets` (§2.5) -- never-captured first, then by score descending.

Both rules are compared **in Python, not SQL**, because `NULL != NULL` is
`NULL` in SQL and both columns are nullable -- so the None arms below are the
ones that would silently invert if this ever moved into a query.
"""

import builders
import db
import jobs
import snapshot
from conftest import FROZEN_NOW

# An epoch well before anything the builders stamp, so "captured during this
# epoch" and "captured before it" are unambiguous rather than clock-adjacent.
OLD_EPOCH = "2020-01-01T00:00:00Z"


def stored_row(conn, playlist_id):
    """The stored row exactly as `_sync_playlists_and_get_targets` selects it.

    Same column list, deliberately: a test that fetched `SELECT *` would pass
    while the real caller handed these functions a row missing a column they
    read.
    """
    return conn.execute(
        "SELECT snapshot_id, tracks_pulled_snapshot_id, tracks_pulled_at, last_pull_error "
        "FROM snapshot WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()


def candidate(conn, playlist_id, fresh_snapshot_id=None):
    """One `(playlist, stored, stale)` triple, as `_resolve_force_epoch` takes.

    `fresh_snapshot_id` is what Spotify reports this run; defaulting it to the
    stored value means "unchanged since last pull", which is the uninteresting
    case every test that is about the epoch rather than staleness wants.
    """
    stored = stored_row(conn, playlist_id)
    if fresh_snapshot_id is None:
        fresh_snapshot_id = stored["snapshot_id"] if stored else None
    return ({"id": playlist_id}, stored, snapshot._is_stale(stored, fresh_snapshot_id))


# -- The refresh rule (§2.2) ------------------------------------------------


def test_a_playlist_never_seen_before_is_stale(conn):
    # source: partial-pulls-J.md §2.2 -- `stored is None` is the first arm of
    # the refresh rule.
    assert snapshot._is_stale(None, "snap-anything") is True


def test_a_playlist_never_item_read_is_stale(conn):
    # source: partial-pulls-J.md §2.1 -- tracks_pulled_snapshot_id is the only
    # column meaning "the stored items are current", and NULL means never.
    playlist = builders.make_playlist(conn, tracks_pulled_snapshot_id=None)
    stored = stored_row(conn, playlist)
    assert snapshot._is_stale(stored, stored["snapshot_id"]) is True


def test_a_changed_snapshot_id_is_stale(conn):
    # source: partial-pulls-J.md §2.2 -- stored capture id != the one Spotify
    # reports now.
    playlist = builders.make_playlist(conn)
    assert snapshot._is_stale(stored_row(conn, playlist), "snap-changed-since") is True


def test_an_unchanged_playlist_is_not_stale(conn):
    # source: partial-pulls-J.md §2.2 -- the whole point of the gate: an
    # untouched playlist is never re-read on a refresh.
    playlist = builders.make_playlist(conn)
    stored = stored_row(conn, playlist)
    assert snapshot._is_stale(stored, stored["snapshot_id"]) is False


def test_snapshot_id_alone_does_not_mean_the_items_are_current(conn):
    """`snapshot_id` is refreshed for every playlist on every run whether or
    not its items were read, so it cannot mean "captured". Only
    `tracks_pulled_snapshot_id` can, and this is the case that distinguishes
    them."""
    # source: partial-pulls-J.md §2.1 -- "snapshot_id itself is refreshed for
    # every playlist on every run ... this column is the only one that does."
    playlist = builders.make_playlist(
        conn, snapshot_id="snap-current", tracks_pulled_snapshot_id="snap-older"
    )
    assert snapshot._is_stale(stored_row(conn, playlist), "snap-current") is True


# -- The full-pull rule (§2.3) ----------------------------------------------


def test_a_stale_playlist_is_a_full_pull_target_whatever_the_epoch(conn):
    # source: partial-pulls-J.md §2.3 -- the full-pull rule is the refresh
    # rule OR not-done-for-this-epoch.
    playlist = builders.make_playlist(conn, tracks_pulled_at=builders.days_ago(0))
    stored = stored_row(conn, playlist)
    assert snapshot._is_full_pull_target(stored, True, OLD_EPOCH) is True


def test_a_playlist_captured_before_this_epoch_is_a_full_pull_target(conn):
    """The whole reason the epoch exists: this playlist is *not* stale, so the
    refresh rule says done -- and a forced pull must read it anyway."""
    # source: partial-pulls-J.md §2.3 -- "A forced pull's entire point is to
    # re-read playlists whose snapshot_id has not changed."
    playlist = builders.make_playlist(conn, tracks_pulled_at="2019-01-01T00:00:00Z")
    stored = stored_row(conn, playlist)
    assert snapshot._is_stale(stored, stored["snapshot_id"]) is False
    assert snapshot._is_full_pull_target(stored, False, OLD_EPOCH) is True


def test_a_playlist_captured_during_this_epoch_is_done_for_it(conn):
    # source: partial-pulls-J.md §2.3 -- "A playlist is done for it when
    # tracks_pulled_at >= pull_force_epoch."
    playlist = builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    assert snapshot._is_full_pull_target(stored_row(conn, playlist), False, OLD_EPOCH) is False


def test_a_playlist_captured_exactly_at_the_epoch_is_done_for_it(conn):
    """The boundary the clause turns on, and the one the frozen clock makes
    ordinary rather than exotic: a playlist read during a forced pull is
    stamped from the same `jobs.now_iso()` that minted the epoch, so equality
    is what a completed target actually looks like. `<=` here would re-target
    every playlist the run just finished.
    """
    # source: partial-pulls-J.md §2.3 -- "done for it when tracks_pulled_at
    # >= pull_force_epoch", so equality is done, not outstanding.
    playlist = builders.make_playlist(conn, tracks_pulled_at=OLD_EPOCH)
    assert snapshot._is_full_pull_target(stored_row(conn, playlist), False, OLD_EPOCH) is False


def test_a_never_captured_playlist_is_a_full_pull_target(conn):
    # source: partial-pulls-J.md §2.3 -- the NULL arm, which is why this is
    # compared in Python: `NULL < epoch` is NULL in SQL, not true.
    playlist = builders.make_playlist(conn, tracks_pulled_at=None)
    assert snapshot._is_full_pull_target(stored_row(conn, playlist), False, OLD_EPOCH) is True


def test_a_playlist_with_no_stored_row_at_all_is_a_full_pull_target(conn):
    # source: partial-pulls-J.md §2.3 -- `stored is None`, the brand-new arm.
    assert snapshot._is_full_pull_target(None, False, OLD_EPOCH) is True


# -- Epoch resolution (§2.4) ------------------------------------------------


def test_a_first_forced_pull_mints_and_persists_an_epoch(conn):
    # source: partial-pulls-J.md §2.4 -- a new epoch is written "only when the
    # previous one is complete or absent"; absent is this case.
    playlist = builders.make_playlist(conn)
    epoch = snapshot._resolve_force_epoch(conn, [candidate(conn, playlist)])

    assert epoch == jobs.now_iso()
    assert db.get_meta(conn, "pull_force_epoch") == epoch


def test_an_epoch_with_unfinished_targets_is_resumed_not_replaced(conn):
    """Resume is the same button: while a forced pull is incomplete, clicking
    Full pull again continues that epoch rather than starting over."""
    # source: partial-pulls-J.md §2.4 -- "Full pull resumes the current epoch
    # if it still has unfinished targets."
    db.set_meta(conn, "pull_force_epoch", OLD_EPOCH)
    done = builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    unfinished = builders.make_playlist(conn, tracks_pulled_at="2019-01-01T00:00:00Z")

    epoch = snapshot._resolve_force_epoch(
        conn, [candidate(conn, done), candidate(conn, unfinished)]
    )

    assert epoch == OLD_EPOCH
    assert db.get_meta(conn, "pull_force_epoch") == OLD_EPOCH


def test_a_completed_epoch_is_replaced_with_a_fresh_one(conn):
    # source: partial-pulls-J.md §2.4 -- a new epoch "only when the previous
    # one is complete".
    db.set_meta(conn, "pull_force_epoch", OLD_EPOCH)
    builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    playlist = builders.make_playlist(conn, tracks_pulled_at="2020-07-01T00:00:00Z")

    epoch = snapshot._resolve_force_epoch(conn, [candidate(conn, playlist)])

    assert epoch == jobs.now_iso() != OLD_EPOCH
    assert db.get_meta(conn, "pull_force_epoch") == epoch


def test_a_playlist_never_seen_before_keeps_the_epoch_alive(conn):
    """The arm P1-004's second-model review added: `stored is None` counts as
    unfinished, not only a playlist carrying a recorded error."""
    # source: partial-pulls-J.md §2.4 via P1-004 -- `_is_full_pull_target`'s
    # `stored is None` arm reaches the epoch check too.
    db.set_meta(conn, "pull_force_epoch", OLD_EPOCH)
    done = builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    brand_new = ({"id": "playlist-never-seen"}, None, True)

    epoch = snapshot._resolve_force_epoch(conn, [candidate(conn, done), brand_new])

    assert epoch == OLD_EPOCH


def test_a_failing_playlist_keeps_the_epoch_alive(conn):
    """P1-004's fix, and the reason it was a live quota-spend bug.

    An earlier version discounted a `last_pull_error`-carrying playlist from
    this check. Once the only unfinished target left was one permanently
    broken playlist, the epoch resolved as complete, a fresh one was minted --
    and a fresh epoch makes every already-captured playlist a target again, so
    the next Full pull silently re-read the entire library.
    """
    # source: partial-pulls-J.md §2.4 -- "A playlist whose item read is
    # currently failing counts as unfinished for this purpose ... it keeps the
    # epoch alive exactly like any other incomplete target."
    db.set_meta(conn, "pull_force_epoch", OLD_EPOCH)
    captured = builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    failing = builders.make_playlist(
        conn, tracks_pulled_at="2019-01-01T00:00:00Z", last_pull_error="403 Forbidden"
    )

    candidates = [candidate(conn, captured), candidate(conn, failing)]
    epoch = snapshot._resolve_force_epoch(conn, candidates)

    assert epoch == OLD_EPOCH
    # And the consequence that makes it matter: the work list stays the
    # targeted retry of the broken playlist, not a re-read of everything.
    chosen = [
        p["id"] for p, stored, stale in candidates
        if snapshot._is_full_pull_target(stored, stale, epoch)
    ]
    assert chosen == [failing]


def test_excluding_the_failing_playlist_lets_the_epoch_complete(conn):
    """The sanctioned way to unstick a permanently broken playlist.

    An excluded playlist is filtered out of `candidates` upstream of this
    check (`_sync_playlists_and_get_targets`), so what reaches here is the
    remaining, finished set -- and the epoch resolves as complete.
    """
    # source: partial-pulls-J.md §2.4 -- "The correct way to unstick a
    # permanently broken playlist and let the epoch complete is to exclude it
    # -- an excluded playlist is filtered out of the candidate set entirely,
    # upstream of this check."
    db.set_meta(conn, "pull_force_epoch", OLD_EPOCH)
    captured = builders.make_playlist(conn, tracks_pulled_at="2020-06-01T00:00:00Z")
    builders.make_playlist(
        conn, tracks_pulled_at="2019-01-01T00:00:00Z", last_pull_error="403 Forbidden",
        excluded=1,
    )

    epoch = snapshot._resolve_force_epoch(conn, [candidate(conn, captured)])

    assert epoch == jobs.now_iso() != OLD_EPOCH


# -- `last_pull_error` clearing (P1-004) ------------------------------------


def test_un_excluding_clears_a_stale_pull_error(conn):
    # source: partial-pulls-J.md §2.4 -- "Un-excluding it (or re-following a
    # previously-unfollowed playlist) clears its last_pull_error so it doesn't
    # carry a stale failure forward."
    playlist = builders.make_playlist(conn, excluded=1, last_pull_error="403 Forbidden")

    snapshot.set_excluded(conn, [playlist], False)

    assert stored_row(conn, playlist)["last_pull_error"] is None


def test_excluding_keeps_the_pull_error(conn):
    """Only *un*-excluding is the fresh start. Excluding a failing playlist is
    how you park it, and the error is the record of why."""
    # source: snapshot.set_excluded -- the clear is on the un-exclude branch
    # only; characterization of the other branch.
    playlist = builders.make_playlist(conn, last_pull_error="403 Forbidden")

    snapshot.set_excluded(conn, [playlist], True)

    assert stored_row(conn, playlist)["last_pull_error"] == "403 Forbidden"


def test_re_following_an_unfollowed_playlist_clears_its_pull_error(conn):
    # source: partial-pulls-J.md §2.4 -- the re-follow arm of the same rule.
    playlist = builders.make_playlist(
        conn, last_pull_error="403 Forbidden", unfollowed_at="2025-01-01T00:00:00Z"
    )

    snapshot._upsert_snapshot_playlist(
        conn,
        {
            "id": playlist, "name": "Back Again", "image_url": None, "owner": "finn",
            "track_count": 3, "snapshot_id": "snap-new", "description": "",
        },
    )

    row = stored_row(conn, playlist)
    assert row["last_pull_error"] is None
    assert conn.execute(
        "SELECT unfollowed_at FROM snapshot WHERE playlist_id = ?", (playlist,)
    ).fetchone()["unfollowed_at"] is None


def test_an_ordinary_repull_keeps_a_still_failing_playlists_error(conn):
    """The guard that makes the re-follow clear safe: an ordinary list-read
    pass over a still-followed, still-failing playlist must not wipe the error
    that keeps it visible."""
    # source: partial-pulls-J.md §2.4 -- the clear is guarded on
    # `unfollowed_at IS NOT NULL`, so a plain re-pull leaves the error alone.
    playlist = builders.make_playlist(conn, last_pull_error="403 Forbidden")

    snapshot._upsert_snapshot_playlist(
        conn,
        {
            "id": playlist, "name": "Still Here", "image_url": None, "owner": "finn",
            "track_count": 3, "snapshot_id": "snap-new", "description": "",
        },
    )

    assert stored_row(conn, playlist)["last_pull_error"] == "403 Forbidden"


# -- Ordering (§2.5) --------------------------------------------------------


def scored_playlist(conn, all_time, tracks_pulled_at=FROZEN_NOW.strftime("%Y-%m-%dT%H:%M:%SZ")):
    """A captured playlist holding one scored track.

    `playlist_scores` combines *version*-tier scores over a playlist's live
    memberships, so a playlist needs a track, a group and a score row before it
    has any score at all -- which is also why the no-scored-versions case below
    is a real state and not a contrivance.
    """
    playlist = builders.make_playlist(conn, tracks_pulled_at=tracks_pulled_at)
    track = builders.make_track(conn)
    groups = builders.make_group(conn, [track])
    builders.make_score(conn, "version", groups["version"], all_time=all_time)
    builders.make_membership(conn, playlist, track)
    return playlist


def test_never_captured_playlists_come_first(conn):
    # source: partial-pulls-J.md §2.5 rule 1 -- "Never-captured first
    # (tracks_pulled_at IS NULL)."
    high_scoring = scored_playlist(conn, all_time=90.0)
    never = builders.make_playlist(conn, tracks_pulled_at=None)

    targets = [
        ({"id": high_scoring}, stored_row(conn, high_scoring)),
        ({"id": never}, stored_row(conn, never)),
    ]
    assert [p["id"] for p in snapshot._order_targets(conn, targets)] == [never, high_scoring]


def test_captured_playlists_sort_by_all_time_score_descending(conn):
    # source: partial-pulls-J.md §2.5 rule 2 -- "Then by
    # playlist_scores(...)['all_time'], descending."
    low = scored_playlist(conn, all_time=20.0)
    high = scored_playlist(conn, all_time=80.0)
    middle = scored_playlist(conn, all_time=50.0)

    targets = [
        ({"id": pid}, stored_row(conn, pid)) for pid in (low, high, middle)
    ]
    assert [p["id"] for p in snapshot._order_targets(conn, targets)] == [high, middle, low]


def test_a_captured_playlist_with_no_scored_versions_sorts_last(conn):
    # source: partial-pulls-J.md §2.5 -- "A captured playlist with no scored
    # versions falls out as 0.0 ... so it sorts last among captured with no
    # special handling."
    scored = scored_playlist(conn, all_time=15.0)
    unscored = builders.make_playlist(
        conn, tracks_pulled_at=FROZEN_NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    targets = [
        ({"id": unscored}, stored_row(conn, unscored)),
        ({"id": scored}, stored_row(conn, scored)),
    ]
    assert [p["id"] for p in snapshot._order_targets(conn, targets)] == [scored, unscored]


def test_never_captured_beats_score_entirely(conn):
    """The two rules are a tuple, not a blend: no score puts a captured
    playlist ahead of an uncaptured one."""
    # source: partial-pulls-J.md §2.5 -- rule 1 is applied before rule 2.
    top = scored_playlist(conn, all_time=99.0)
    never_low = builders.make_playlist(conn, tracks_pulled_at=None)

    targets = [
        ({"id": top}, stored_row(conn, top)),
        ({"id": never_low}, stored_row(conn, never_low)),
    ]
    assert [p["id"] for p in snapshot._order_targets(conn, targets)][0] == never_low
