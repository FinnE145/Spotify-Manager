"""`snapshot._diff_playlist_tracks` -- P2's single largest target (P1-002).

The algorithm is three passes, and the spec section it is tested against
(`docs/specs/snapshot.md`, "Change detection & diffing", rewritten 2026-08-17
by P1-002) is the authoritative description of all three. Nothing else in the
codebase documents these mechanics, so assertions here cite that section.

**Two rules the spec itself sets for tests of this function**, both load-bearing
in how the cases below are written:

- the query fetching stored rows has **no `ORDER BY`**, so which of two rows
  sharing an exact `added_at` pass 1 consumes first is unspecified -- every
  assertion is about the *set* of surviving rows, never about row order;
- `position` is Symr's own dense per-pull index (locals and episodes are
  skipped without incrementing it), not Spotify's raw item index.

`_diff_playlist_tracks` does not commit -- its caller `_apply_playlist_items`
owns the transaction -- so these read back on the same connection.
"""

import pytest

import builders
import snapshot

# Fixed timestamps rather than builders.days_ago(), so the ordering the
# algorithm turns on is visible in the test rather than computed from it.
# OLD < MID < NEW lexicographically, which is the only property pass 3's
# `sorted(key=added_at)` actually uses.
OLD = "2024-01-01T00:00:00Z"
MID = "2024-06-01T00:00:00Z"
NEW = "2024-12-01T00:00:00Z"
OTHER = "2025-03-01T00:00:00Z"


def item(track_id, position, added_at):
    """One entry of `current_items`.

    `_diff_playlist_tracks` reads exactly these three keys off a parsed item;
    the real `_parse_track_item` dict carries twenty more that this function
    never touches, and including them would only obscure what it uses.
    """
    return {"track_id": track_id, "position": position, "added_at": added_at}


def live_rows(conn, playlist_id):
    return conn.execute(
        "SELECT id, track_id, position, added_at FROM membership "
        "WHERE playlist_id = ? AND removed_at IS NULL ORDER BY position",
        (playlist_id,),
    ).fetchall()


def removed_ids(conn, playlist_id):
    return {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM membership WHERE playlist_id = ? AND removed_at IS NOT NULL",
            (playlist_id,),
        )
    }


@pytest.fixture
def playlist(conn):
    return builders.make_playlist(conn)


# -- Baseline ---------------------------------------------------------------


def test_identical_repull_changes_nothing(conn, playlist):
    # source: snapshot.md "Change detection & diffing" pass 1 -- an exact
    # added_at match is the same copy, so a re-pull of an unchanged playlist
    # removes nothing and inserts nothing.
    track = builders.make_track(conn)
    row_id = builders.make_membership(conn, playlist, track, position=0, added_at=MID)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 0, MID)])

    live = live_rows(conn, playlist)
    assert [row["id"] for row in live] == [row_id]
    assert live[0]["added_at"] == MID
    assert removed_ids(conn, playlist) == set()


# -- Pass 1: the identity pass ----------------------------------------------


def test_exact_added_at_match_survives_a_position_change(conn, playlist):
    # source: snapshot.md pass 1 -- "treated as *the same copy*, wherever it
    # now sits -- its position is updated, nothing else changes."
    track = builders.make_track(conn)
    row_id = builders.make_membership(conn, playlist, track, position=0, added_at=MID)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 7, MID)])

    live = live_rows(conn, playlist)
    assert [row["id"] for row in live] == [row_id]
    assert live[0]["position"] == 7
    assert live[0]["added_at"] == MID
    assert removed_ids(conn, playlist) == set()


def test_identity_pass_inverts_the_newest_departs_fallback(conn, playlist):
    """The single most important interaction in the whole algorithm.

    Stored holds an old copy and a new one; the playlist now holds just the
    *new* one. Pass 1 matches it directly, so pass 3 never sees it -- and the
    row that departs is the **oldest**, the exact opposite of pass 3's
    "newest presumed departed" rule.
    """
    # source: snapshot.md pass 1 -- "it can match the *newest* stored copy
    # while leaving an *older* one unmatched, which inverts the 'newest
    # presumed departed' intent of pass 3 for whichever copies it resolves."
    track = builders.make_track(conn)
    old_row = builders.make_membership(conn, playlist, track, position=0, added_at=OLD)
    new_row = builders.make_membership(conn, playlist, track, position=1, added_at=NEW)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 0, NEW)])

    assert [row["id"] for row in live_rows(conn, playlist)] == [new_row]
    assert removed_ids(conn, playlist) == {old_row}


def test_null_added_at_rows_match_each_other_in_the_identity_pass(conn, playlist):
    """`None` is a valid dict key, so NULL matches NULL in pass 1.

    Built so that pass 1 and pass 2 give **different** answers: the NULL
    current copy sits at position 1 and the dated one at position 0, so
    position-order pairing would hand the NULL stored row the *dated* copy.
    Only the identity pass produces the outcome asserted below.
    """
    # source: snapshot.md pass 1 + its NULL caveat -- an exact added_at match
    # is the same copy "wherever it now sits", and NULL is an exact match.
    track = builders.make_track(conn)
    null_row = builders.make_membership(conn, playlist, track, position=0, added_at=None)
    dated_row = builders.make_membership(conn, playlist, track, position=1, added_at=MID)

    snapshot._diff_playlist_tracks(
        conn, playlist, [item(track, 0, NEW), item(track, 1, None)]
    )

    live = {row["id"]: row for row in live_rows(conn, playlist)}
    assert set(live) == {null_row, dated_row}
    # The NULL stored row followed its NULL copy to position 1 and kept NULL;
    # pass 2 would have given it the position-0 copy and stamped it NEW.
    assert (live[null_row]["position"], live[null_row]["added_at"]) == (1, None)
    assert (live[dated_row]["position"], live[dated_row]["added_at"]) == (0, NEW)
    assert removed_ids(conn, playlist) == set()


# -- Pass 2: position-order pairing on a net increase / no change -----------


def test_a_brand_new_track_is_inserted(conn, playlist):
    # source: snapshot.md pass 2 -- "any current items left over after pairing
    # (a net increase) become new INSERTs."
    existing = builders.make_track(conn)
    fresh = builders.make_track(conn)
    builders.make_membership(conn, playlist, existing, position=0, added_at=MID)

    snapshot._diff_playlist_tracks(
        conn, playlist, [item(existing, 0, MID), item(fresh, 1, NEW)]
    )

    live = live_rows(conn, playlist)
    assert [row["track_id"] for row in live] == [existing, fresh]
    assert live[1]["added_at"] == NEW
    assert removed_ids(conn, playlist) == set()


def test_a_second_copy_of_a_known_track_is_inserted_not_repaired(conn, playlist):
    # source: snapshot.md pass 2 -- the identity pass resolves the copy that
    # matches, and the extra current copy is a new INSERT rather than a
    # rewrite of the row already there.
    track = builders.make_track(conn)
    row_id = builders.make_membership(conn, playlist, track, position=0, added_at=MID)

    snapshot._diff_playlist_tracks(
        conn, playlist, [item(track, 0, MID), item(track, 1, NEW)]
    )

    live = live_rows(conn, playlist)
    assert len(live) == 2
    assert live[0]["id"] == row_id and live[0]["added_at"] == MID
    assert live[1]["id"] != row_id and live[1]["added_at"] == NEW
    assert removed_ids(conn, playlist) == set()


def test_unmatched_leftovers_are_paired_by_position_order(conn, playlist):
    """Pass 2 is a deliberate use of position as copy identity.

    Neither current copy matches a stored `added_at`, so both stored rows fall
    through to pass 2 and are paired index-by-index against the current copies
    *sorted by position* -- not in the order the current list happens to
    arrive in.
    """
    # source: snapshot.md pass 2 -- "sorted by position ... and paired off
    # index-by-index. This is a deliberate use of position as copy identity."
    track = builders.make_track(conn)
    first = builders.make_membership(conn, playlist, track, position=0, added_at=OLD)
    second = builders.make_membership(conn, playlist, track, position=1, added_at=NEW)

    # Deliberately handed in highest-position-first, so a pairing that used
    # list order rather than position order would pair them the other way.
    snapshot._diff_playlist_tracks(
        conn, playlist, [item(track, 1, OTHER), item(track, 0, MID)]
    )

    live = {row["id"]: row for row in live_rows(conn, playlist)}
    assert set(live) == {first, second}
    assert (live[first]["position"], live[first]["added_at"]) == (0, MID)
    assert (live[second]["position"], live[second]["added_at"]) == (1, OTHER)
    assert removed_ids(conn, playlist) == set()


def test_a_stored_row_with_a_null_position_sorts_as_zero(conn, playlist):
    # source: snapshot.md pass 2 -- "stored rows with a NULL position sort as 0".
    track = builders.make_track(conn)
    null_position = builders.make_membership(
        conn, playlist, track, position=None, added_at=OLD
    )
    positioned = builders.make_membership(conn, playlist, track, position=1, added_at=NEW)

    snapshot._diff_playlist_tracks(
        conn, playlist, [item(track, 0, MID), item(track, 1, OTHER)]
    )

    live = {row["id"]: row for row in live_rows(conn, playlist)}
    # Sorting as 0 puts the NULL-position row first, so it takes the
    # position-0 current copy.
    assert live[null_position]["added_at"] == MID
    assert live[positioned]["added_at"] == OTHER


# -- Pass 3: the oldest-survives fallback on a net decrease -----------------


def test_the_oldest_unmatched_copy_survives_and_the_newest_departs(conn, playlist):
    # source: snapshot.md pass 3 -- "the stored leftovers are sorted by
    # added_at ascending ... the oldest n_survive survive ... the rest are
    # marked removed_at = now."
    track = builders.make_track(conn)
    oldest = builders.make_membership(conn, playlist, track, position=0, added_at=OLD)
    newest = builders.make_membership(conn, playlist, track, position=1, added_at=NEW)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 0, OTHER)])

    assert [row["id"] for row in live_rows(conn, playlist)] == [oldest]
    assert removed_ids(conn, playlist) == {newest}


def test_a_surviving_copys_added_at_is_overwritten_not_just_its_position(conn, playlist):
    """The history mutation P1-002's second pass surfaced.

    A survivor of pass 3 is restamped with its paired current copy's
    `added_at` -- so the row that survives no longer carries the timestamp it
    survived *by*.
    """
    # source: snapshot.md pass 3 -- "the survivor's added_at is overwritten
    # with its paired current copy's added_at, not just its position."
    track = builders.make_track(conn)
    oldest = builders.make_membership(conn, playlist, track, position=0, added_at=OLD)
    builders.make_membership(conn, playlist, track, position=1, added_at=NEW)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 4, OTHER)])

    live = live_rows(conn, playlist)
    assert [row["id"] for row in live] == [oldest]
    assert live[0]["added_at"] == OTHER
    assert live[0]["position"] == 4


def test_ties_on_equal_added_at_keep_the_lowest_position_copy(conn, playlist):
    """Two stored copies share an `added_at`, so the ascending sort cannot
    separate them -- and being stable over an already position-sorted list, it
    leaves the lowest-position one first, which is the one that survives.

    **The higher position is inserted first, deliberately**, so that rowid
    order and position order disagree. The stored-row query has no `ORDER BY`,
    so inserting them the other way round hands the algorithm a list that is
    already position-sorted for free -- and the pre-sort this test exists to
    pin becomes unobservable. Written that way it passed with the sort
    deleted, which is §1's "green but cannot fail" exactly.
    """
    # source: snapshot.md pass 3 -- "ties on equal added_at keep their existing
    # position order, so the lowest-position one among a tie survives."
    track = builders.make_track(conn)
    higher = builders.make_membership(conn, playlist, track, position=5, added_at=MID)
    lower = builders.make_membership(conn, playlist, track, position=2, added_at=MID)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 0, OTHER)])

    assert [row["id"] for row in live_rows(conn, playlist)] == [lower]
    assert removed_ids(conn, playlist) == {higher}


def test_a_null_added_at_row_never_departs_by_the_fallback(conn, playlist):
    # source: snapshot.md pass 3 -- "a NULL added_at sorts first, i.e. treated
    # as oldest, and therefore never departs by this rule."
    track = builders.make_track(conn)
    null_row = builders.make_membership(conn, playlist, track, position=0, added_at=None)
    dated = builders.make_membership(conn, playlist, track, position=1, added_at=OLD)

    snapshot._diff_playlist_tracks(conn, playlist, [item(track, 0, OTHER)])

    assert [row["id"] for row in live_rows(conn, playlist)] == [null_row]
    assert removed_ids(conn, playlist) == {dated}


def test_a_track_gone_from_the_playlist_departs_entirely(conn, playlist):
    # source: snapshot.md pass 3 -- with no current copies at all, every
    # stored copy is a leftover and none survive.
    gone = builders.make_track(conn)
    stays = builders.make_track(conn)
    gone_row = builders.make_membership(conn, playlist, gone, position=0, added_at=OLD)
    stays_row = builders.make_membership(conn, playlist, stays, position=1, added_at=MID)

    snapshot._diff_playlist_tracks(conn, playlist, [item(stays, 0, MID)])

    assert [row["id"] for row in live_rows(conn, playlist)] == [stays_row]
    assert removed_ids(conn, playlist) == {gone_row}


def test_departure_stamps_removed_at_rather_than_deleting(conn, playlist):
    """`membership` is append-only: a departed copy keeps its row."""
    # source: snapshot.md "Why copies get their own rows" -- the log is
    # append-only, so "live" means removed_at IS NULL and nothing is deleted.
    track = builders.make_track(conn)
    row_id = builders.make_membership(conn, playlist, track, position=0, added_at=OLD)

    snapshot._diff_playlist_tracks(conn, playlist, [])

    row = conn.execute(
        "SELECT removed_at FROM membership WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["removed_at"] is not None
    assert live_rows(conn, playlist) == []


# -- Cross-track independence ----------------------------------------------


def test_each_track_id_is_diffed_independently(conn, playlist):
    """The whole algorithm runs per track id, so a track losing a copy cannot
    affect another track's rows even at identical positions."""
    # source: snapshot.md -- "Diff runs per track id, in three passes".
    losing = builders.make_track(conn)
    steady = builders.make_track(conn)
    kept = builders.make_membership(conn, playlist, losing, position=0, added_at=OLD)
    dropped = builders.make_membership(conn, playlist, losing, position=1, added_at=NEW)
    other = builders.make_membership(conn, playlist, steady, position=2, added_at=MID)

    snapshot._diff_playlist_tracks(
        conn, playlist, [item(losing, 0, OTHER), item(steady, 1, MID)]
    )

    assert {row["id"] for row in live_rows(conn, playlist)} == {kept, other}
    assert removed_ids(conn, playlist) == {dropped}
    # The untouched track's row kept its own added_at -- it matched in pass 1.
    live = {row["id"]: row for row in live_rows(conn, playlist)}
    assert live[other]["added_at"] == MID
