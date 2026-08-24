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
