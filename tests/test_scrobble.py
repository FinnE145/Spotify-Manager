"""`scrobble.py` -- polling GET /v1/me/player/recently-played into `play`
rows (docs/specs/scrobbling-R.md).

Two rules this feature changes from the export-only world, named because a
fixture built against the old one would agree with a broken implementation
and pass: `roundtrip._WORK_LIST_SQL`'s "done" now also requires an ISRC (or a
`track_isrc_absent` row), and `play.source` is no longer always `'export'`.

Items are added to the fake newest-first (`fake_spotify.add_recently_played`),
matching the real endpoint and the order §4.3's predecessor derivation
depends on.
"""

from spotipy.exceptions import SpotifyException

import builders
import db
import fakes
import history_import
import jobs
import roundtrip
import scrobble
import snapshot


def uri(track_id):
    return f"spotify:track:{track_id}"


def last_poll(conn):
    return conn.execute(
        "SELECT * FROM scrobble_poll ORDER BY id DESC LIMIT 1"
    ).fetchone()


# -- ms_played derivation (§4.3) ---------------------------------------------


def test_back_to_back_items_derive_ms_played_from_the_gap(fake_spotify, conn):
    # source: scrobbling-R.md §4.3 -- "predecessor = played_at of the
    # next-older item in the batch ... ms_played = min(gap_ms, duration_ms)".
    newer = fakes.spotify_track("t-new", duration_ms=180_000)
    older = fakes.spotify_track("t-old", duration_ms=180_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:05:00.000Z")
    fake_spotify.add_recently_played(older, "2026-06-15T10:03:30.000Z")  # 90s before

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newer["uri"],)
    ).fetchone()[0]
    assert ms_played == 90_000


def test_an_idle_gap_longer_than_the_track_clamps_to_duration(fake_spotify, conn):
    # source: scrobbling-R.md §4.3 -- "a 10-minute break followed by a
    # 3-minute song gives min(13min, 3min) = 3min." A fixture of only
    # back-to-back items can't tell gap from min(gap, duration).
    newer = fakes.spotify_track("t-new", duration_ms=180_000)
    older = fakes.spotify_track("t-old", duration_ms=180_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:15:00.000Z")
    fake_spotify.add_recently_played(older, "2026-06-15T10:00:00.000Z")  # 15 min gap

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newer["uri"],)
    ).fetchone()[0]
    assert ms_played == 180_000


def test_a_skipped_track_derives_ms_played_from_the_short_gap(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 1 -- "Skipped track -> the gap."
    newer = fakes.spotify_track("t-new", duration_ms=200_000)
    older = fakes.spotify_track("t-old", duration_ms=200_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:00:20.000Z")
    fake_spotify.add_recently_played(older, "2026-06-15T10:00:00.000Z")  # skipped after 20s

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newer["uri"],)
    ).fetchone()[0]
    assert ms_played == 20_000


def test_the_oldest_items_predecessor_is_found_in_the_database(fake_spotify, conn):
    # source: scrobbling-R.md §4.3 -- "predecessor ... else MAX(ts) FROM play
    # WHERE ts < this item's ts." Only the last item in a batch needs this --
    # every earlier one finds its predecessor in the batch itself.
    builders.make_play(conn, uri=uri("earlier"), ts="2026-06-15T09:00:00Z")
    oldest = fakes.spotify_track("t-oldest", duration_ms=200_000)
    fake_spotify.add_recently_played(oldest, "2026-06-15T09:01:00.000Z")  # 60s after the DB row

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (oldest["uri"],)
    ).fetchone()[0]
    assert ms_played == 60_000


def test_no_predecessor_at_all_uses_the_full_duration(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 1 -- "No predecessor at all ->
    # duration_ms."
    only = fakes.spotify_track("t-only", duration_ms=200_000)
    fake_spotify.add_recently_played(only, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (only["uri"],)
    ).fetchone()[0]
    assert ms_played == 200_000


def test_a_non_positive_gap_uses_the_full_duration(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 1 -- "Non-positive gap ->
    # duration_ms." A clock/ordering anomaly must not store a negative or
    # zero ms_played.
    newer = fakes.spotify_track("t-new", duration_ms=180_000)
    older = fakes.spotify_track("t-old", duration_ms=180_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:00:00.000Z")
    fake_spotify.add_recently_played(older, "2026-06-15T10:00:00.000Z")  # zero gap

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newer["uri"],)
    ).fetchone()[0]
    assert ms_played == 180_000


# -- Row identity (§3.2) ------------------------------------------------------


def test_polling_the_same_response_twice_inserts_once(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 2 -- "Feeding the same response
    # twice inserts once."
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)
    scrobble.poll(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM play WHERE spotify_track_uri = ?", (track["uri"],)
    ).fetchone()[0]
    assert count == 1


def test_two_plays_of_the_same_uri_at_different_times_both_insert(fake_spotify, conn):
    # source: scrobbling-R.md §1.5 -- "41 unique uris in 50 items ... so
    # played_at is load-bearing in the row identity."
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")
    fake_spotify.add_recently_played(track, "2026-06-15T09:00:00.000Z")

    scrobble.poll(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM play WHERE spotify_track_uri = ?", (track["uri"],)
    ).fetchone()[0]
    assert count == 2


def test_a_scrobble_digest_differs_from_the_export_digest_for_the_same_play():
    # source: scrobbling-R.md Tests clause 2 -- "A scrobble digest differs
    # from history_import._row_hash for a row describing the same play."
    played_at = "2026-06-15T10:00:00.000Z"
    scrobble_hash = scrobble._row_hash(played_at, uri("t1"))
    export_hash = history_import._row_hash({"ts": played_at, "spotify_track_uri": uri("t1")})
    assert scrobble_hash != export_hash


def test_the_row_hash_covers_source_played_at_and_uri_exactly():
    # source: scrobbling-R.md §3.2 -- "source is inside the hashed dict, not
    # merely the column, so a scrobble digest can never collide with an
    # export digest for the same play." The comparison test above can't
    # actually tell this apart from a hash that omits `source` entirely --
    # scrobble's and the export's hashed key sets differ regardless (§3.2's
    # own {"source", "played_at", "uri"} vs history_import's 16 export-shaped
    # keys), so a wrong implementation missing `source` would pass it too.
    # This recomputes the exact digest the spec's algorithm produces
    # independently, rather than by running the code under test.
    import hashlib
    import json

    played_at = "2026-06-15T10:00:00.000Z"
    expected = hashlib.sha1(
        json.dumps(
            {"source": "scrobble", "played_at": played_at, "uri": uri("t1")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert scrobble._row_hash(played_at, uri("t1")) == expected


def test_ts_is_truncated_to_seconds_but_the_hash_uses_the_verbatim_ms_string(fake_spotify, conn):
    # source: scrobbling-R.md §3.2/§3.3 -- ts is truncated for storage, but
    # the hash covers played_at "verbatim at millisecond precision". Storing
    # the truncated value in the hash would still dedupe and still pass a
    # test that only checks the column, so both are asserted here.
    played_at = "2026-06-15T10:00:00.813Z"
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, played_at)

    scrobble.poll(conn)

    row = conn.execute(
        "SELECT ts, row_hash FROM play WHERE spotify_track_uri = ?", (track["uri"],)
    ).fetchone()
    assert row["ts"] == "2026-06-15T10:00:00Z"
    assert row["row_hash"] == scrobble._row_hash(played_at, track["uri"])


# -- Supersession by the export (§6) -----------------------------------------


def _finish_import(conn, range_end, error=None):
    import_id = conn.execute(
        "INSERT INTO play_import (kind, folder) VALUES ('upload', 'x')"
    ).lastrowid
    conn.commit()
    counts = {
        "files_parsed": 1, "rows_read": 0, "rows_inserted": 0,
        "range_start": None, "range_end": range_end,
    }
    history_import._finish(conn, import_id, counts, error)


def test_a_successful_import_deletes_scrobbles_at_or_before_range_end(conn):
    # source: scrobbling-R.md §6 -- "DELETE FROM play WHERE source =
    # 'scrobble' AND ts <= :range_end" -- and leaves later ones intact.
    builders.make_play(conn, uri=uri("early"), source="scrobble", ts="2026-06-14T00:00:00Z")
    builders.make_play(conn, uri=uri("onboundary"), source="scrobble", ts="2026-06-15T00:00:00Z")
    builders.make_play(conn, uri=uri("later"), source="scrobble", ts="2026-06-16T00:00:00Z")

    _finish_import(conn, range_end="2026-06-15T00:00:00Z")

    remaining = {
        row["spotify_track_uri"] for row in conn.execute("SELECT spotify_track_uri FROM play")
    }
    assert remaining == {uri("later")}


def test_supersession_does_not_run_when_the_import_recorded_an_error(conn):
    # source: scrobbling-R.md Tests clause 4 -- "does not run when the import
    # recorded an error."
    builders.make_play(conn, uri=uri("early"), source="scrobble", ts="2026-06-14T00:00:00Z")

    _finish_import(conn, range_end="2026-06-15T00:00:00Z", error="boom")

    assert conn.execute("SELECT COUNT(*) FROM play").fetchone()[0] == 1


def test_supersession_does_not_run_when_range_end_is_none(conn):
    # source: scrobbling-R.md Tests clause 4 -- "does not run when range_end
    # is NULL."
    builders.make_play(conn, uri=uri("early"), source="scrobble", ts="2026-06-14T00:00:00Z")

    _finish_import(conn, range_end=None)

    assert conn.execute("SELECT COUNT(*) FROM play").fetchone()[0] == 1


def test_exported_rows_are_never_deleted_by_supersession(conn):
    # source: scrobbling-R.md §6 -- the DELETE is filtered to source =
    # 'scrobble'; an export row at or before range_end must survive.
    builders.make_play(conn, uri=uri("export"), source="export", ts="2026-06-01T00:00:00Z")

    _finish_import(conn, range_end="2026-06-15T00:00:00Z")

    assert conn.execute("SELECT COUNT(*) FROM play").fetchone()[0] == 1


# -- Round-trip arm 3 (§5.2) --------------------------------------------------


def test_a_null_isrc_track_with_a_play_appears_in_the_work_list(conn):
    # source: scrobbling-R.md §5.2 -- the third UNION ALL arm.
    track_id = builders.make_track(conn, "t1", isrc=None)
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)

    assert track_uri in roundtrip._work_list(conn)
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 1


def test_a_settled_track_does_not_appear_in_the_work_list(conn):
    # source: scrobbling-R.md §5.2 -- "the same track once in
    # track_isrc_absent does not."
    track_id = builders.make_track(conn, "t1", isrc=None)
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)
    conn.execute("INSERT INTO track_isrc_absent (track_id) VALUES (?)", (track_id,))
    conn.commit()

    assert track_uri not in roundtrip._work_list(conn)
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 0


def test_a_track_with_an_isrc_never_appears_in_arm_three(conn):
    # source: scrobbling-R.md §5.2 -- "a track with an ISRC never does."
    track_id = builders.make_track(conn, "t1", isrc="ISRCT1")
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)

    assert track_uri not in roundtrip._work_list(conn)
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 0


def test_an_unresolved_uri_still_appears_via_the_listening_arm(conn):
    # source: scrobbling-R.md Tests clause 5 -- arm 3 must not crowd out the
    # listening arm.
    builders.make_play(conn, uri=uri("unknown"))

    assert uri("unknown") in roundtrip._work_list(conn)


def test_the_four_partitions_sum_to_remaining_uris(conn):
    # source: grouping-fixes-backfill-M.md §4.6, extended by scrobbling-R.md
    # §5.3 to four partitions.
    builders.make_play(conn, uri=uri("unknown"))  # arm 1: listening
    album_id = builders.make_album(conn, "al1")
    conn.execute(
        "INSERT INTO wanted_uri (uri, source, album_id) VALUES (?, 'album', ?)",
        (uri("wanted"), album_id),
    )
    track_id = builders.make_track(conn, "t1", isrc=None)
    isrc_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=isrc_uri)  # arm 3
    conn.commit()

    counts = roundtrip.counts(conn)
    assert counts["remaining_uris"] == (
        counts["listening_uris"]
        + counts["album_page_uris"]
        + counts["album_backfill_uris"]
        + counts["incomplete_isrc_uris"]
    )


def test_muting_the_listening_arm_leaves_arm_three_present(conn):
    # source: scrobbling-R.md §5.2 -- "Muting the listening arm leaves arm 3
    # present -- the whole reason it is a separate arm."
    builders.make_play(conn, uri=uri("unknown"))
    track_id = builders.make_track(conn, "t1", isrc=None)
    isrc_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=isrc_uri)
    db.set_meta(conn, "roundtrip_listening_muted", "1")
    conn.commit()

    work_list = roundtrip._work_list(conn)
    assert uri("unknown") not in work_list
    assert isrc_uri in work_list


# -- The round-trip's stop condition (§5.2) -----------------------------------


def test_a_returned_track_still_missing_an_isrc_is_settled(conn, fake_spotify):
    # source: scrobbling-R.md §5.2 -- "After the round-trip upserts a
    # returned track, if its isrc is still NULL, INSERT OR IGNORE the track
    # into track_isrc_absent."
    fake_spotify.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)
    fake_spotify.add_track(fakes.spotify_track("t1", external_ids={}))

    roundtrip._load_and_read(conn, fake_spotify, "batch 1/1", [uri("t1")])

    assert conn.execute(
        "SELECT 1 FROM track_isrc_absent WHERE track_id = 't1'"
    ).fetchone() is not None


def test_a_returned_track_that_fills_the_isrc_is_not_settled(conn, fake_spotify):
    # source: scrobbling-R.md Tests clause 6 -- "one that fills the ISRC in
    # does not."
    fake_spotify.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)

    roundtrip._load_and_read(conn, fake_spotify, "batch 1/1", [uri("t1")])

    assert conn.execute(
        "SELECT 1 FROM track_isrc_absent WHERE track_id = 't1'"
    ).fetchone() is None


# -- Ingest (§5.1) -------------------------------------------------------------


def test_a_scrobbled_item_upserts_a_track_with_no_isrc_but_full_metadata(fake_spotify, conn):
    # source: scrobbling-R.md §5.1 -- "the resulting row has isrc,
    # is_playable and linked_from NULL. It is otherwise complete."
    track = fakes.spotify_track("t1", name="A Song", duration_ms=222_000, external_ids={})
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    row = conn.execute(
        "SELECT name, duration_ms, isrc, album_id FROM track WHERE track_id = 't1'"
    ).fetchone()
    assert row["name"] == "A Song"
    assert row["duration_ms"] == 222_000
    assert row["isrc"] is None
    assert row["album_id"] is not None


def test_a_later_full_object_upsert_fills_the_isrc_without_wiping_other_columns(fake_spotify, conn):
    # source: scrobbling-R.md §5.1 -- the COALESCE rule; "a test using only a
    # NULL-to-NULL upsert would not exercise" it.
    scrobbled = fakes.spotify_track("t1", name="A Song", external_ids={})
    fake_spotify.add_recently_played(scrobbled, "2026-06-15T10:00:00.000Z")
    scrobble.poll(conn)

    full = fakes.spotify_track("t1", name="A Song")  # carries the real ISRC this time
    snapshot._upsert_track_full(conn, snapshot._parse_track_item(full, None, None))

    row = conn.execute("SELECT name, isrc FROM track WHERE track_id = 't1'").fetchone()
    assert row["name"] == "A Song"
    assert row["isrc"] == "ISRCt1"


# -- Skipping (§4.4/§4.5) ------------------------------------------------------


def test_a_paused_poller_never_calls_sp(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 8 -- assert the fake sp was never
    # called; "no rows inserted" can't distinguish skipping from polling and
    # receiving nothing.
    db.set_meta(conn, "scrobble_enabled", "0")
    conn.commit()

    scrobble.poll(conn)

    assert fake_spotify.calls == []


def test_a_poller_in_backoff_never_calls_sp(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 8 -- the backoff arm of the same
    # rule.
    db.set_meta(conn, "scrobble_backoff_until", "2026-06-15T13:00:00Z")  # after FROZEN_NOW
    conn.commit()

    scrobble.poll(conn)

    assert fake_spotify.calls == []


# -- 429 back-off (§4.5) -------------------------------------------------------


def test_a_429_sets_backoff_from_retry_after(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 9 -- "429 sets
    # scrobble_backoff_until from Retry-After."
    fake_spotify.fail(
        "current_user_recently_played",
        SpotifyException(429, -1, "rate limited", headers={"Retry-After": "120"}),
    )

    scrobble.poll(conn)

    assert db.get_meta(conn, "scrobble_backoff_until") == "2026-06-15T12:02:00Z"
    assert last_poll(conn)["retry_after"] == 120


def test_a_429_with_no_retry_after_falls_back_to_one_interval(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 9 -- "falls back to one interval
    # when the header is absent."
    fake_spotify.fail(
        "current_user_recently_played", SpotifyException(429, -1, "rate limited", headers={})
    )

    scrobble.poll(conn)

    # FROZEN_NOW (12:00:00) + 6000s (100 min).
    assert db.get_meta(conn, "scrobble_backoff_until") == "2026-06-15T13:40:00Z"


def test_a_non_429_exception_records_an_error_and_does_not_set_backoff(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 9 -- "A non-429 exception records
    # scrobble_poll.error and does not set backoff."
    fake_spotify.fail("current_user_recently_played", fakes.not_found())

    scrobble.poll(conn)

    assert db.get_meta(conn, "scrobble_backoff_until") is None
    assert last_poll(conn)["error"] is not None


# -- gap_warning (§4.6) ---------------------------------------------------


def test_gap_warning_fires_when_the_response_starts_after_the_last_stored_scrobble(fake_spotify, conn):
    # source: scrobbling-R.md §4.6 -- "if oldest_played in the response is
    # newer than the newest scrobble already stored, the plays between them
    # were lost."
    builders.make_play(conn, uri=uri("old"), source="scrobble", ts="2026-06-15T09:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 1


def test_gap_warning_does_not_fire_when_the_windows_overlap(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 10 -- "not set when the windows
    # overlap."
    builders.make_play(conn, uri=uri("old"), source="scrobble", ts="2026-06-15T09:59:00Z")
    newer = fakes.spotify_track("t-new", duration_ms=120_000)
    older = fakes.spotify_track("t-old2", duration_ms=120_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:00:00.000Z")
    fake_spotify.add_recently_played(older, "2026-06-15T09:58:00.000Z")  # overlaps the stored row

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 0


def test_gap_warning_does_not_fire_on_the_first_poll_ever(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 10 -- "not set on the first poll
    # ever."
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 0


# -- Poll liveness and recompute (§3.1/§4.9) -----------------------------


def test_a_poll_row_is_written_even_when_nothing_is_inserted(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 11 -- "A poll row is written even
    # when it inserts nothing" -- the liveness signal, easy to optimise away.
    scrobble.poll(conn)

    row = last_poll(conn)
    assert row["items_read"] == 0
    assert row["rows_inserted"] == 0


def test_request_recompute_is_called_only_when_rows_were_inserted(fake_spotify, conn, recompute_calls):
    # source: scrobbling-R.md §4.9 -- "A poll that inserted nothing requests
    # nothing."
    scrobble.poll(conn)
    assert recompute_calls == []

    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:05:00.000Z")
    scrobble.poll(conn)
    assert len(recompute_calls) == 1


# -- Not a job (§4.7) ----------------------------------------------------


def test_polling_does_not_claim_the_job_slot(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 13 -- "does not set jobs._active."
    scrobble.poll(conn)
    assert jobs.active() is None


def test_polling_succeeds_while_a_job_holds_the_slot(fake_spotify, conn):
    # source: scrobbling-R.md Tests clause 13 -- "succeeds while a job holds
    # the slot."
    jobs._active = "snapshot"
    try:
        track = fakes.spotify_track("t1", duration_ms=200_000)
        fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")
        scrobble.poll(conn)
    finally:
        jobs._active = None

    assert conn.execute("SELECT COUNT(*) FROM play").fetchone()[0] == 1


# -- The export guard (§6.1) --------------------------------------------------


def test_the_next_poll_does_not_restore_superseded_scrobbles(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- the regression for the bug this whole
    # clause was written for. Deleting a scrobble destroys the row_hash
    # INSERT OR IGNORE dedupes against, so before the guard the window simply
    # served the same items back and every superseded row returned.
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")
    scrobble.poll(conn)
    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 1

    # An export lands covering that play: §6 deletes it, and seeds the export
    # row that the guard now reads its cutover from.
    builders.make_play(conn, uri=track["uri"], source="export", ts="2026-06-15T10:00:00Z")
    _finish_import(conn, range_end="2026-06-15T10:00:00Z")
    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 0

    scrobble.poll(conn)  # same 50-deep window, same item

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 0


def test_an_item_the_export_already_covers_is_not_inserted(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- "skips the play insert for any item
    # whose truncated ts is <= MAX(ts) FROM play WHERE source = 'export'".
    builders.make_play(conn, uri=uri("exported"), source="export", ts="2026-06-15T11:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")  # before the cutover

    scrobble.poll(conn)

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 0


def test_an_item_after_the_export_cutover_is_still_inserted(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- the partner to the test above. One
    # asserting only the skip cannot tell the guard from "insert nothing".
    builders.make_play(conn, uri=uri("exported"), source="export", ts="2026-06-15T09:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")  # after the cutover

    scrobble.poll(conn)

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 1


def test_the_export_cutover_boundary_is_inclusive(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- the predicate is `<=`, matching §6's own
    # DELETE. An item at exactly the cutover second is the export's to own, so
    # a `<` here would re-insert precisely the row §6 just deleted.
    builders.make_play(conn, uri=uri("exported"), source="export", ts="2026-06-15T10:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.400Z")  # same second

    scrobble.poll(conn)

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 0


def test_the_track_is_still_upserted_for_an_item_the_export_covers(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- "The track upsert still runs -- the
    # export supersedes the *play*, not what Symr knows about the track."
    builders.make_play(conn, uri=uri("exported"), source="export", ts="2026-06-15T11:00:00Z")
    track = fakes.spotify_track("t1", name="A Song", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    row = conn.execute("SELECT name FROM track WHERE track_id = 't1'").fetchone()
    assert row is not None and row["name"] == "A Song"


def test_an_existing_scrobble_does_not_suppress_an_older_item(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- the cutover is the newest **export**
    # play. Only the export is authoritative; a scrobble already stored says
    # nothing about whether some older play belongs. Dropping the source
    # filter here would quietly turn the guard into "never insert anything
    # older than what we already have", and every test without an export row
    # would still pass.
    builders.make_play(conn, uri=uri("s"), source="scrobble", ts="2026-06-15T11:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")  # older, unexported

    scrobble.poll(conn)

    assert conn.execute(
        "SELECT COUNT(*) FROM play WHERE spotify_track_uri = ?", (track["uri"],)
    ).fetchone()[0] == 1


def test_every_item_inserts_when_no_export_row_exists(fake_spotify, conn):
    # source: scrobbling-R.md §6.1 -- the guard is derived from the export's
    # newest play, so with no export at all it must not suppress anything. A
    # NULL MAX(ts) compared with <= would silently drop every row.
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 1


# -- The loop survives a failure after the request (§4.5) ---------------------


class _StopLoop(Exception):
    """Raised from the patched sleep to end _loop's infinite while."""


def test_the_loop_survives_an_exception_raised_after_the_request(monkeypatch, conn):
    # source: scrobbling-R.md §4.5 -- "That guarantee covers the whole loop
    # body, not just the request." Nothing restarts this thread, so an escape
    # here ends scrobbling until the container restarts. Reaching the patched
    # sleep is what proves the exception did not escape the loop.
    def boom(_conn):
        raise KeyError("duration_ms")

    def stop(_seconds):
        raise _StopLoop

    monkeypatch.setattr(scrobble, "poll", boom)
    monkeypatch.setattr(scrobble.time, "sleep", stop)

    try:
        scrobble._loop()
    except _StopLoop:
        pass
    else:
        raise AssertionError("_loop returned without reaching sleep")

    row = last_poll(conn)
    assert row is not None and "duration_ms" in row["error"]


def test_a_loop_error_is_recorded_without_the_failing_connection(conn):
    # source: scrobbling-R.md §4.5 -- recorded "on a fresh connection (the one
    # that raised may be the problem)". Asserting the row lands proves the
    # recorder does not reuse a caller-supplied handle.
    scrobble._record_loop_error(ValueError("database is locked"))

    row = last_poll(conn)
    assert row is not None
    assert "database is locked" in row["error"]
    assert row["items_read"] is None  # never reached the request


# -- /dev/scrobble's read path (§7) -------------------------------------------


def test_export_cutover_is_the_newest_export_play(conn):
    # source: scrobbling-R.md §7 -- "the cutover is exactly MAX(ts) WHERE
    # source = 'export'", which is what the page draws its divider from.
    builders.make_play(conn, uri=uri("a"), source="export", ts="2026-06-14T00:00:00Z")
    builders.make_play(conn, uri=uri("b"), source="export", ts="2026-06-15T00:00:00Z")
    builders.make_play(conn, uri=uri("c"), source="scrobble", ts="2026-06-16T00:00:00Z")

    assert scrobble.index_data(conn)["export_cutover"] == "2026-06-15T00:00:00Z"


def test_total_scrobbles_counts_only_scrobbles(conn):
    # source: scrobbling-R.md §7 -- "total source='scrobble' rows". With a
    # 94k-row export in the real DB, dropping the filter would report the
    # whole play table as scrobbles.
    builders.make_play(conn, uri=uri("a"), source="export", ts="2026-06-14T00:00:00Z")
    builders.make_play(conn, uri=uri("b"), source="scrobble", ts="2026-06-16T00:00:00Z")

    assert scrobble.index_data(conn)["total_scrobbles"] == 1


def test_gap_warning_count_counts_only_flagged_polls(conn):
    # source: scrobbling-R.md §7 -- "count of polls with gap_warning set".
    conn.execute(
        "INSERT INTO scrobble_poll (started_at, gap_warning) "
        "VALUES ('2026-06-15T10:00:00Z', 1)"
    )
    conn.execute(
        "INSERT INTO scrobble_poll (started_at, gap_warning) "
        "VALUES ('2026-06-15T11:00:00Z', 0)"
    )
    conn.commit()

    assert scrobble.index_data(conn)["gap_warning_count"] == 1


def test_the_page_shows_at_most_fifty_plays(conn):
    # source: scrobbling-R.md §7 -- "The last 50 plays, ORDER BY ts DESC
    # LIMIT 50". Nothing else pins the limit, so a smaller one is invisible.
    for n in range(55):
        builders.make_play(conn, uri=uri(f"t{n}"), ts=f"2026-06-15T10:{n:02d}:00Z")

    rows = scrobble.index_data(conn)["recent_plays"]
    assert len(rows) == 50
    assert rows[0]["ts"] == "2026-06-15T10:54:00Z"  # newest first


def test_the_page_shows_plays_of_every_source(conn):
    # source: scrobbling-R.md §7 -- the last 50 plays "regardless of source",
    # which is what makes the export/scrobble divider meaningful at all.
    builders.make_play(conn, uri=uri("e"), source="export", ts="2026-06-14T00:00:00Z")
    builders.make_play(conn, uri=uri("s"), source="scrobble", ts="2026-06-15T00:00:00Z")

    sources = {row["source"] for row in scrobble.index_data(conn)["recent_plays"]}
    assert sources == {"export", "scrobble"}


def test_an_absent_enabled_key_reads_as_enabled(conn):
    # source: scrobbling-R.md §3.5 -- "Absent means on -- a fresh deploy
    # scrobbles with no manual step." poll() honours this (a paused-poller
    # test covers that arm); this is the page agreeing with it, so a fresh
    # deploy cannot show "Paused" while the thread is polling.
    assert db.get_meta(conn, "scrobble_enabled") is None
    assert scrobble.index_data(conn)["enabled"] is True


# -- The play row's own columns (§3.3) ----------------------------------------


def test_the_poll_row_records_how_many_items_it_read(fake_spotify, conn):
    # source: scrobbling-R.md §3.1 -- items_read, which /dev/scrobble renders
    # as "read N, stored M". The empty-poll test asserts 0, which a constant
    # 0 would also satisfy.
    for n in range(3):
        fake_spotify.add_recently_played(
            fakes.spotify_track(f"t{n}"), f"2026-06-15T10:0{n}:00.000Z"
        )

    scrobble.poll(conn)

    assert last_poll(conn)["items_read"] == 3


def test_a_scrobble_row_links_to_its_poll(fake_spotify, conn):
    # source: scrobbling-R.md §3.1 -- "play.poll_id INTEGER REFERENCES
    # scrobble_poll(id)". Nothing else reads the FK, so a NULL would be
    # invisible.
    fake_spotify.add_recently_played(fakes.spotify_track("t1"), "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    row = conn.execute(
        "SELECT poll_id, import_id, source_file FROM play WHERE source='scrobble'"
    ).fetchone()
    assert row["poll_id"] == last_poll(conn)["id"]
    assert row["import_id"] is None  # §3.3: NULL for a scrobble, as import_id is
    assert row["source_file"] is None


def test_the_reported_columns_carry_the_track_album_and_album_artist(fake_spotify, conn):
    # source: scrobbling-R.md §3.3 -- reported_artist_name is the **album
    # artist**, "matching the export's meaning of that column, which is the
    # album artist and misses featured credits". Taking track.artists instead
    # would look right and be wrong, so the fixture gives the two different
    # names.
    album = fakes.spotify_album("al1", name="The Album", artists=[fakes.spotify_artist("aa", name="Album Artist")])
    track = fakes.spotify_track(
        "t1", name="A Song", album=album, artists=[fakes.spotify_artist("ta", name="Track Artist")]
    )
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    row = conn.execute(
        "SELECT reported_track_name, reported_artist_name, reported_album_name "
        "FROM play WHERE source='scrobble'"
    ).fetchone()
    assert row["reported_track_name"] == "A Song"
    assert row["reported_artist_name"] == "Album Artist"
    assert row["reported_album_name"] == "The Album"


# -- Unusable items (§1.5) -----------------------------------------------------


def test_an_unusable_item_is_skipped_without_shifting_its_neighbours_gap(fake_spotify, conn):
    # source: scrobbling-R.md §1.5 -- episodes and local tracks are filtered
    # by snapshot._usable_track. The batch is deliberately *not* pre-filtered,
    # because _derive_ms_played indexes into it by position: the newest item's
    # predecessor here is the unusable middle one, so a filtered list would
    # derive its gap against the wrong item and silently store 300s not 60s.
    newest = fakes.spotify_track("t-new", duration_ms=300_000)
    oldest = fakes.spotify_track("t-old", duration_ms=300_000)
    fake_spotify.add_recently_played(newest, "2026-06-15T10:05:00.000Z")
    fake_spotify.recently_played.append(
        {"track": {"id": "ep1", "type": "episode", "uri": "spotify:episode:ep1"},
         "played_at": "2026-06-15T10:04:00.000Z", "context": None}
    )
    fake_spotify.add_recently_played(oldest, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    uris = {r["spotify_track_uri"] for r in conn.execute("SELECT spotify_track_uri FROM play")}
    assert uris == {newest["uri"], oldest["uri"]}  # the episode stored no play
    ms = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newest["uri"],)
    ).fetchone()[0]
    assert ms == 60_000  # the gap to the episode's slot, not to t-old


# -- gap_warning's comparison point (§4.6) ------------------------------------


def test_gap_warning_compares_against_scrobbles_not_exports(fake_spotify, conn):
    # source: scrobbling-R.md §4.6 -- "newer than the newest **scrobble**
    # already stored". The export row is deliberately the *newer* of the two:
    # with the source filter the comparison point is the 09:00 scrobble and
    # this is a real gap, while dropping the filter would make it the 11:00
    # export and silently suppress the warning. A fixture with the export
    # older would pass either way.
    builders.make_play(conn, uri=uri("e"), source="export", ts="2026-06-15T11:00:00Z")
    builders.make_play(conn, uri=uri("s"), source="scrobble", ts="2026-06-15T09:00:00Z")
    fake_spotify.add_recently_played(fakes.spotify_track("t1"), "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 1


def test_gap_warning_does_not_fire_when_the_oldest_item_equals_the_newest_stored(
    fake_spotify, conn
):
    # source: scrobbling-R.md §4.6 -- the test is "newer than", so an exact
    # match is an overlap, not a gap. `>=` here would warn on every poll whose
    # window happens to start on the stored second.
    builders.make_play(conn, uri=uri("s"), source="scrobble", ts="2026-06-15T10:00:00Z")
    fake_spotify.add_recently_played(fakes.spotify_track("t1"), "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 0


# -- The unauthenticated and non-Spotify failure paths (§4.5) -----------------


def test_a_missing_token_records_a_poll_row_and_returns(monkeypatch, conn):
    # source: scrobbling-R.md §4.5 -- get_spotify_client() returning None
    # "logs and continues rather than killing the thread, so a server that has
    # been redeployed but not yet re-consented (§8) recovers on its own". §8's
    # scope change makes this the *expected* state after every deploy.
    monkeypatch.setattr(scrobble, "get_spotify_client", lambda: None)

    scrobble.poll(conn)

    row = last_poll(conn)
    assert row["error"] == "not_authenticated"
    assert row["items_read"] is None


def test_a_non_spotify_exception_records_an_error(fake_spotify, conn):
    # source: scrobbling-R.md §4.5 -- "Other exceptions record
    # scrobble_poll.error and the loop continues." The 429 tests and the
    # not_found test all raise SpotifyException, so nothing else reaches the
    # bare `except Exception` around the request.
    fake_spotify.fail("current_user_recently_played", ConnectionError("dns"))

    scrobble.poll(conn)

    row = last_poll(conn)
    assert "dns" in row["error"]
    assert db.get_meta(conn, "scrobble_backoff_until") is None


# -- Settling the ISRC queue row (§5.3) ---------------------------------------


def test_clearing_the_isrc_row_settles_every_matching_track(conn):
    # source: scrobbling-R.md §5.3 -- "[Clear] ... INSERT OR IGNOREs every
    # currently-matching track_id into track_isrc_absent". The route is in the
    # catalog, which only proves it responds (P2-010) -- a no-op body passes
    # that and leaves the queue reading the same count forever.
    track_id = builders.make_track(conn, "t1", isrc=None)
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 1

    roundtrip.settle_incomplete_isrc(conn)

    assert conn.execute(
        "SELECT 1 FROM track_isrc_absent WHERE track_id = ?", (track_id,)
    ).fetchone() is not None
    assert roundtrip.counts(conn)["incomplete_isrc_uris"] == 0


def test_clearing_the_isrc_row_leaves_tracks_that_have_an_isrc_alone(conn):
    # source: scrobbling-R.md §5.3 -- it settles what the row *counts*, and a
    # track with an ISRC was never in that set. Settling one would suppress a
    # genuine future re-request if its ISRC were ever cleared.
    track_id = builders.make_track(conn, "t1", isrc="ISRCT1")
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)

    roundtrip.settle_incomplete_isrc(conn)

    assert conn.execute("SELECT COUNT(*) FROM track_isrc_absent").fetchone()[0] == 0


def test_the_clear_endpoint_settles_the_isrc_queue(client, conn):
    # source: scrobbling-R.md §5.3 -- the wiring from the button to the
    # settle, which only a route test can see.
    track_id = builders.make_track(conn, "t1", isrc=None)
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri)

    resp = client.post("/api/roundtrip/incomplete-isrc/clear")

    assert resp.status_code == 200
    assert conn.execute(
        "SELECT 1 FROM track_isrc_absent WHERE track_id = ?", (track_id,)
    ).fetchone() is not None


# -- The next-poll line (§7) --------------------------------------------------


def test_no_poller_in_this_process_reports_no_next_poll(conn):
    # source: scrobbling-R.md §7 -- the laptop's permanent state (§4.1: the
    # thread starts only from serve.py). It previously derived a time from the
    # last poll row, which on a machine that never polls is a schedule nothing
    # intends to keep.
    assert scrobble._next_wake_at is None
    assert scrobble.index_data(conn)["next_poll_at"] is None


def test_the_next_poll_time_comes_from_the_thread_not_the_last_poll(fake_spotify, conn):
    # source: scrobbling-R.md §7 -- a manual "Poll now" writes a scrobble_poll
    # row without touching the thread's sleep, so a value derived from that
    # row drifts from the real schedule by up to a full interval. This asserts
    # a poll does *not* move it.
    scrobble._next_wake_at = "2026-06-15T14:00:00Z"

    scrobble.poll(conn)

    assert scrobble.index_data(conn)["next_poll_at"] == "2026-06-15T14:00:00Z"


def test_the_loop_records_its_own_next_wake_time(monkeypatch, conn):
    # source: scrobbling-R.md §7 -- exact, not an estimate: the interval is a
    # fixed sleep, so the thread knows the second it will wake. FROZEN_NOW
    # (12:00:00) + 6000s.
    monkeypatch.setattr(scrobble, "poll", lambda _conn: None)
    monkeypatch.setattr(scrobble.time, "sleep", lambda _s: (_ for _ in ()).throw(_StopLoop))

    try:
        scrobble._loop()
    except _StopLoop:
        pass

    assert scrobble._next_wake_at == "2026-06-15T13:40:00Z"


def test_the_page_names_the_deployed_server_when_nothing_is_scheduled(client, conn):
    # source: scrobbling-R.md §7 -- "if the poller isn't running, it should say
    # so, not a fake estimate". Asserting index_data's None is not enough; the
    # template has three branches and only a render shows which one runs.
    body = client.get("/dev/scrobble").get_data(as_text=True)

    assert "No poller running in this process" in body
    assert "Next poll at" not in body


def test_the_page_gives_the_exact_next_poll_time_when_one_is_scheduled(client, conn):
    # source: scrobbling-R.md §7 -- the exact-time branch, rendered through
    # datetime_exact_span so the browser shows a clock time rather than
    # format.js's relative phrasing.
    scrobble._next_wake_at = "2026-06-15T13:40:00Z"

    body = client.get("/dev/scrobble").get_data(as_text=True)

    assert 'data-datetime-exact="2026-06-15T13:40:00Z"' in body
    assert "Next poll at" in body


def test_a_paused_poller_says_its_next_wake_up_will_skip(client, conn):
    # source: scrobbling-R.md §7/§4.4 -- a scheduled wake-up still exists
    # while paused; it just returns without polling. Showing a bare "Next poll
    # at" there would promise a poll that will not happen.
    scrobble._next_wake_at = "2026-06-15T13:40:00Z"
    db.set_meta(conn, "scrobble_enabled", "0")
    conn.commit()

    body = client.get("/dev/scrobble").get_data(as_text=True)

    assert "will skip without polling" in body
    assert "Next poll at" not in body


def test_the_toggle_returns_the_full_status_payload(client, conn):
    # source: scrobbling-R.md §7 -- both controls "return JSON and update the
    # page in place". Pausing changes the next-poll line as well as the
    # buttons, so the toggle has to hand back the same shape the poll does or
    # the line goes stale until a reload.
    resp = client.post("/api/scrobble/toggle", json={"enabled": False})

    assert resp.get_json()["enabled"] is False
    assert set(resp.get_json()) == set(scrobble.index_data(conn))


# -- Mutation-sweep S survivors (docs/codebase-health/S_survivors.md) --------


def test_start_spawns_a_daemon_thread_targeting_the_poll_loop(monkeypatch):
    # source: S_sweep.md §3 -- true at scrobble.py:54
    #
    # start() is never actually called under test elsewhere -- test_serve.py
    # monkeypatches it away entirely -- so its own body (which thread, and
    # daemon=True vs False) had no coverage. A non-daemon thread would block
    # process shutdown, which is exactly the failure mode daemon=True exists
    # to prevent (serve.py's SIGTERM handler does not join this thread).
    # threading.Thread itself is faked rather than actually started, so this
    # never spawns a real background loop against the test DB.
    captured = {}

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            captured["target"] = target
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(scrobble.threading, "Thread", _FakeThread)

    scrobble.start()

    assert captured["daemon"] is True
    assert captured["target"] is scrobble._loop
    assert captured["started"] is True


def test_backoff_expires_exactly_at_its_own_timestamp(fake_spotify, conn):
    # source: S_sweep.md §3 -- cmp< at scrobble.py:120
    #
    # `jobs.now_iso() < backoff_until` -- at the exact instant backoff_until
    # names, backoff has just ended and polling should resume. The existing
    # backoff test only checks a timestamp safely *after* now, which cannot
    # tell `<` from `<=`: both agree there. Only the boundary itself, where
    # now equals backoff_until exactly, can distinguish "expired" from "still
    # backing off by one more interval".
    db.set_meta(conn, "scrobble_backoff_until", jobs.now_iso())  # == FROZEN_NOW
    conn.commit()
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert fake_spotify.calls != []  # backoff has expired; the request fires


def test_the_poll_reads_at_most_fifty_items_per_request(fake_spotify, conn):
    # source: S_sweep.md §3 -- num at scrobble.py:137
    #
    # Pins the literal `limit=50` passed to current_user_recently_played --
    # §1.4/§4.2's "50 items" is load-bearing (the interval's overflow
    # arithmetic assumes it), and nothing previously exercised the fake with
    # more than 50 seeded items, so a fixture with fewer items couldn't tell
    # limit=50 from limit=51: FakeSpotify.current_user_recently_played slices
    # `self.recently_played[:limit]`, which only differs from a 50-slice once
    # more than 50 items exist to slice from.
    for n in range(55):
        fake_spotify.add_recently_played(
            fakes.spotify_track(f"t{n}", duration_ms=200_000),
            f"2026-06-15T09:{54 - n:02d}:00.000Z",
        )

    scrobble.poll(conn)

    assert last_poll(conn)["items_read"] == 50


def test_the_db_predecessor_query_picks_the_nearest_earlier_play(fake_spotify, conn):
    # source: S_sweep.md §3 -- sqlMAX at scrobble.py:214
    #
    # `SELECT MAX(ts) ... WHERE ts < ?` must pick the *nearest* earlier play,
    # not the earliest one. The existing DB-predecessor test seeds only one
    # qualifying row, so MAX and MIN agree there and cannot tell them apart --
    # this needs two qualifying rows with different ts. duration_ms is set
    # far above either candidate gap so min(gap, duration) reports the gap
    # itself in both cases, rather than collapsing them to the same clamp.
    # source='scrobble' keeps §6.1's export-cutover guard out of this.
    builders.make_play(conn, uri=uri("far"), source="scrobble", ts="2026-06-15T08:00:00Z")
    builders.make_play(conn, uri=uri("near"), source="scrobble", ts="2026-06-15T08:50:00Z")
    item = fakes.spotify_track("t-new", duration_ms=10_000_000)
    fake_spotify.add_recently_played(item, "2026-06-15T09:00:00.000Z")

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (item["uri"],)
    ).fetchone()[0]
    assert ms_played == 600_000  # gap to the *nearer* (08:50) row, not the far one


def test_the_db_predecessor_query_excludes_a_play_at_the_exact_same_second(fake_spotify, conn):
    # source: S_sweep.md §3 -- sql< at scrobble.py:214
    #
    # The predicate is `ts < ?`, strictly less-than: a stored play at exactly
    # this item's truncated second is not its predecessor. `<=` would let it
    # in, deriving a zero gap and silently crediting the full duration instead
    # of the true (larger) gap to the actual earlier row. source='scrobble'
    # here keeps §6.1's export-cutover guard (a separate rule, keyed off
    # source='export') from also suppressing the insert this test needs.
    builders.make_play(conn, uri=uri("same-second"), source="scrobble", ts="2026-06-15T09:00:00Z")
    builders.make_play(conn, uri=uri("earlier"), source="scrobble", ts="2026-06-15T08:00:00Z")
    item = fakes.spotify_track("t-new", duration_ms=10_000_000)
    fake_spotify.add_recently_played(item, "2026-06-15T09:00:00.000Z")

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (item["uri"],)
    ).fetchone()[0]
    assert ms_played == 3_600_000  # the same-second row is excluded; falls to 08:00


def test_a_gap_of_one_millisecond_is_not_treated_as_non_positive(fake_spotify, conn):
    # source: S_sweep.md §3 -- num at scrobble.py:223
    #
    # `if gap_ms <= 0` is the non-positive-gap guard; a gap of exactly 1ms is
    # positive and must derive ms_played = min(gap, duration), not fall back
    # to the full duration. The existing non-positive-gap test only covers
    # gap_ms == 0, which agrees with both `<= 0` and `<= 1`.
    newer = fakes.spotify_track("t-new", duration_ms=200_000)
    older = fakes.spotify_track("t-old", duration_ms=200_000)
    fake_spotify.add_recently_played(newer, "2026-06-15T10:00:00.001Z")
    fake_spotify.add_recently_played(older, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    ms_played = conn.execute(
        "SELECT ms_played FROM play WHERE spotify_track_uri = ?", (newer["uri"],)
    ).fetchone()[0]
    assert ms_played == 1


def test_gap_warnings_comparison_point_is_the_newest_scrobble_not_the_oldest(fake_spotify, conn):
    # source: S_sweep.md §3 -- sqlMAX at scrobble.py:242
    #
    # `prev_newest` is `MAX(ts) WHERE source = 'scrobble'`. Every existing
    # gap_warning test seeds at most one prior scrobble row, so MAX and MIN
    # agree there. With two scrobble rows and the polled item's timestamp
    # strictly between them, MAX (correct) says no gap while MIN (the
    # mutant) would report a spurious one.
    builders.make_play(conn, uri=uri("old-scrobble"), source="scrobble", ts="2026-06-15T08:00:00Z")
    builders.make_play(conn, uri=uri("recent-scrobble"), source="scrobble", ts="2026-06-15T09:30:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T09:15:00.000Z")  # between the two

    scrobble.poll(conn)

    assert last_poll(conn)["gap_warning"] == 0


def test_the_export_cutover_uses_the_newest_export_row_not_the_oldest(fake_spotify, conn):
    # source: S_sweep.md §3 -- sqlMAX at scrobble.py:260
    #
    # `export_through` is `MAX(ts) WHERE source = 'export'`. Every existing
    # §6.1 test seeds at most one export row, so MAX and MIN agree there. With
    # two export rows and the polled item's ts strictly between them, MAX
    # (correct) skips the insert while MIN (the mutant) would not, silently
    # re-admitting a play the export already covers.
    builders.make_play(conn, uri=uri("old-export"), source="export", ts="2026-06-15T09:00:00Z")
    builders.make_play(conn, uri=uri("new-export"), source="export", ts="2026-06-15T11:00:00Z")
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")  # between the two

    scrobble.poll(conn)

    assert conn.execute("SELECT COUNT(*) FROM play WHERE source='scrobble'").fetchone()[0] == 0


def test_rows_inserted_counts_exactly_one_per_row(fake_spotify, conn):
    # source: S_sweep.md §3 -- num at scrobble.py:298
    #
    # `rows_inserted += 1` per actually-inserted row. The only existing
    # assertion on this column is the zero case (nothing inserted), where
    # `+= 1` and `+= 2` are indistinguishable -- the line never executes.
    # This needs a poll that inserts exactly one row and checks the stored
    # count is exactly 1, not 2.
    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")

    scrobble.poll(conn)

    assert last_poll(conn)["rows_inserted"] == 1


def test_total_scrobbles_excludes_more_non_scrobble_rows_than_scrobble_rows(conn):
    # source: S_sweep.md §3 -- sql= at scrobble.py:331
    #
    # `COUNT(*) WHERE source = 'scrobble'`. The existing total_scrobbles test
    # seeds exactly one row of each source, so `= 'scrobble'` (count 1) and
    # `<> 'scrobble'` (also count 1) coincidentally agree. Two non-scrobble
    # rows against one scrobble row breaks that tie.
    builders.make_play(conn, uri=uri("e1"), source="export", ts="2026-06-14T00:00:00Z")
    builders.make_play(conn, uri=uri("e2"), source="export", ts="2026-06-14T01:00:00Z")
    builders.make_play(conn, uri=uri("s1"), source="scrobble", ts="2026-06-15T00:00:00Z")

    assert scrobble.index_data(conn)["total_scrobbles"] == 1


def test_gap_warning_count_excludes_more_unflagged_polls_than_flagged(conn):
    # source: S_sweep.md §3 -- sql= at scrobble.py:334
    #
    # `COUNT(*) WHERE gap_warning = 1`. The existing test seeds exactly one
    # flagged and one unflagged poll, so `= 1` (count 1) and `<> 1` (also
    # count 1) coincidentally agree. Two flagged rows against one unflagged
    # row breaks that tie.
    conn.execute(
        "INSERT INTO scrobble_poll (started_at, gap_warning) VALUES ('2026-06-15T09:00:00Z', 1)"
    )
    conn.execute(
        "INSERT INTO scrobble_poll (started_at, gap_warning) VALUES ('2026-06-15T10:00:00Z', 1)"
    )
    conn.execute(
        "INSERT INTO scrobble_poll (started_at, gap_warning) VALUES ('2026-06-15T11:00:00Z', 0)"
    )
    conn.commit()

    assert scrobble.index_data(conn)["gap_warning_count"] == 2


def test_last_poll_row_is_the_most_recent_poll_not_the_first(fake_spotify, conn):
    # source: S_sweep.md §3 -- sqlDESC at scrobble.py:348
    #
    # `_last_poll_row` is `ORDER BY id DESC LIMIT 1` -- the page's "Last poll"
    # line must show the newest poll, not the first one ever. Nothing
    # previously drove two polls through scrobble.index_data()["last_poll"]
    # and checked which one won; `ASC` would pin the page to the very first
    # poll forever, silently going stale on every later one.
    fake_spotify.fail("current_user_recently_played", fakes.not_found())
    scrobble.poll(conn)  # first poll: records an error

    track = fakes.spotify_track("t1", duration_ms=200_000)
    fake_spotify.add_recently_played(track, "2026-06-15T10:00:00.000Z")
    scrobble.poll(conn)  # second poll: succeeds

    last = scrobble.index_data(conn)["last_poll"]
    assert last["error"] is None


def test_recent_plays_resolves_track_name_through_played_uri_track(fake_spotify, conn):
    # source: S_sweep.md §3 -- sql= at scrobble.py:358 and scrobble.py:359
    #
    # `_recent_plays` joins play -> played_uri_track -> track to resolve a
    # play's own track_id/name. With exactly one track in the library whose
    # own uri equals the play's uri, played_uri_track has exactly one row
    # that DOES equal the play's uri: breaking either `=` into `<>` (at 358,
    # the play->view join, or at 359, the view->track join) excludes that one
    # true match and leaves nothing else to accidentally satisfy `<>`, so the
    # resolved columns fall to NULL under either mutant alone.
    track_id = builders.make_track(conn, "t1")
    track_uri = conn.execute(
        "SELECT uri FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()["uri"]
    builders.make_play(conn, uri=track_uri, source="scrobble", ts="2026-06-15T10:00:00Z")

    rows = scrobble.index_data(conn)["recent_plays"]
    assert rows[0]["track_id"] == track_id


def test_a_tied_timestamp_breaks_by_id_descending(conn):
    # source: S_sweep.md §3 -- sqlDESC at scrobble.py:360
    #
    # `_recent_plays` orders `ts DESC, id DESC`. Two plays at the identical
    # second must break the tie by the more-recently-inserted (higher) id
    # first; `id ASC` would silently reverse tied pairs on every page render.
    tied_ts = "2026-06-15T10:00:00Z"
    builders.make_play(conn, uri=uri("first"), ts=tied_ts)
    builders.make_play(conn, uri=uri("second"), ts=tied_ts)

    rows = scrobble.index_data(conn)["recent_plays"]
    assert [r["spotify_track_uri"] for r in rows] == [uri("second"), uri("first")]
