"""Tests of the row builders (tests/builders.py).

The point of most of these is not that the builder inserted a row -- it is that
the row it inserted is one the *real* read paths accept. A builder that
produces a shape production code never produces would make every test written
on top of it prove nothing.
"""

import pytest

import canonical
import entities
import generations
import scoring
from builders import (
    days_ago,
    make_album,
    make_artist,
    make_generation,
    make_group,
    make_membership,
    make_play,
    make_playlist,
    make_score,
    make_track,
)


# -- Ids and the clock ------------------------------------------------------


def test_ids_are_generated_in_sequence(conn):
    # source: P2_tests.md §4.2 -- a test must be able to name an id literally
    assert make_track(conn) == "track-1"
    assert make_track(conn) == "track-2"


def test_ids_reset_between_tests(conn):
    # source: P2_tests.md §4.2 -- paired with the test above, which already
    # consumed track-1 and track-2
    assert make_track(conn) == "track-1"


def test_days_ago_is_relative_to_the_frozen_clock():
    # source: P2_tests.md §4.3 -- builders read the same frozen now the code does
    assert days_ago(0) == "2026-06-15T12:00:00Z"
    assert days_ago(5) == "2026-06-10T12:00:00Z"


def test_days_ago_follows_a_deliberate_tick(freezer):
    # source: P2_tests.md §4.3
    freezer.move_to("2026-06-20T12:00:00Z")
    assert days_ago(0) == "2026-06-20T12:00:00Z"


# -- Parents are filled in (the FKs are real) --------------------------------


def test_make_track_creates_its_album_and_artist(conn):
    # source: P2_tests.md §4.2 -- PRAGMA foreign_keys is ON, so a track with no
    # album or artist rows cannot be inserted at all
    track_id = make_track(conn)
    row = conn.execute(
        "SELECT album_id FROM track WHERE track_id = ?", (track_id,)
    ).fetchone()
    assert row["album_id"] is not None
    assert conn.execute("SELECT COUNT(*) FROM album").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM track_artist WHERE track_id = ?", (track_id,)
    ).fetchone()[0] == 1


def test_make_membership_creates_its_playlist_and_track(conn):
    # source: P2_tests.md §4.2
    make_membership(conn)
    assert conn.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM track").fetchone()[0] == 1


def test_reusing_an_id_does_not_duplicate_or_clobber(conn):
    # source: P2_tests.md §4.2 -- builders fill in parents, so the same artist
    # named by two tracks must be created once and keep its first name
    make_artist(conn, "a1", name="Radiohead")
    make_track(conn, "t1", artists=["a1"])
    make_track(conn, "t2", artists=["a1"])
    assert conn.execute("SELECT COUNT(*) FROM artist WHERE artist_id = 'a1'").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM artist WHERE artist_id = 'a1'").fetchone()[0] == "Radiohead"


def test_credit_position_follows_the_order_given(conn):
    # source: P2_tests.md §4.2 -- position is what carries "first credit is the
    # primary artist" (db.py's track_artist.position)
    make_track(conn, "t1", artists=["a1", "a2"])
    positions = dict(
        conn.execute("SELECT artist_id, position FROM track_artist WHERE track_id = 't1'")
    )
    assert positions == {"a1": 0, "a2": 1}


# -- The rows match what production writes -----------------------------------


def test_track_artists_display_string_matches_snapshots_format(conn):
    # source: snapshot.py:536 -- ", ".join of the credited names. Write-only,
    # but a fixture that left it empty would not look like a real row.
    make_artist(conn, "a1", name="Bicep")
    make_artist(conn, "a2", name="Clara La San")
    make_track(conn, "t1", artists=["a1", "a2"])
    row = conn.execute("SELECT artists FROM track WHERE track_id = 't1'").fetchone()
    assert row["artists"] == "Bicep, Clara La San"


def test_track_uri_matches_the_spotify_format(conn):
    # source: characterization -- confirmed against symr.db's own rows. The
    # round-trip resolves plays to tracks through exactly this string.
    make_track(conn, "t1")
    row = conn.execute("SELECT uri FROM track WHERE track_id = 't1'").fetchone()
    assert row["uri"] == "spotify:track:t1"


def test_a_built_play_resolves_through_played_uri_track(conn):
    # source: db.py's played_uri_track view -- a play carries a uri and has no
    # FK to track, so "the builder inserted a row" is not the interesting part
    track_id = make_track(conn)
    make_play(conn, track_id=track_id)
    resolved = conn.execute(
        "SELECT track_id FROM played_uri_track WHERE uri = ?", (f"spotify:track:{track_id}",)
    ).fetchone()
    assert resolved["track_id"] == track_id


def test_a_play_for_an_unknown_uri_resolves_to_nothing(conn):
    # source: db.py's played_uri_track view -- the round-trip's entire subject
    make_play(conn, uri="spotify:track:never-seen")
    assert conn.execute(
        "SELECT COUNT(*) FROM played_uri_track WHERE uri = 'spotify:track:never-seen'"
    ).fetchone()[0] == 0


def test_membership_defaults_to_live(conn):
    # source: characterization -- "live" means removed_at IS NULL everywhere in
    # this codebase, and it is the default a test almost always wants
    make_membership(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM membership WHERE removed_at IS NULL"
    ).fetchone()[0] == 1


# -- The real read paths accept what the builders produce --------------------


def test_play_stats_reads_built_plays(conn):
    # source: entities.play_stats -- the 30d ("month") and 7d ("week") windows
    # are computed from the frozen now, so days_ago() lands where it says it
    # does. data_through is MAX(play.ts), so the most recent play must be
    # inside both windows or they would correctly report None instead.
    track_id = make_track(conn)
    make_play(conn, track_id=track_id, ts=days_ago(2))
    make_play(conn, track_id=track_id, ts=days_ago(20))
    make_play(conn, track_id=track_id, ts=days_ago(200))
    stats = entities.play_stats(conn, [track_id])
    assert stats["total"] == 3
    assert stats["month"] == 2
    assert stats["week"] == 1


def test_play_stats_reports_none_for_a_window_a_stale_export_predates(conn):
    # source: entities.play_stats -- "a window whose start predates MAX(play.ts)
    # returns None, which renders as an em dash rather than a lying 0". A
    # builder that could not express a stale export could not test this.
    track_id = make_track(conn)
    make_play(conn, track_id=track_id, ts=days_ago(200))
    stats = entities.play_stats(conn, [track_id])
    assert stats["total"] == 1
    assert stats["month"] is None
    assert stats["week"] is None


def test_make_group_puts_tracks_in_one_group_at_every_tier(conn):
    # source: db.py's track_group -- four NOT NULL columns, one per tier
    groups = make_group(conn, ["t1", "t2"])
    rows = conn.execute(
        "SELECT track_id, song_id, version_id, recording_id, release_id FROM track_group"
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["song_id"] == groups["song"]
        assert row["release_id"] == groups["release"]


def test_make_group_leaves_the_representative_unpinned(conn):
    # source: canonical.py -- _INSERT_GROUP_SQL never writes
    # representative_track_id, and pin_representative writes it only at song
    # tier. A builder that pinned one would short-circuit
    # canonical.representative() before the score election ran, so every
    # scoring-H.md §11.3 tiebreak test would assert the pin instead. It did,
    # until session 2.
    groups = make_group(conn, ["t1", "t2"])
    pins = conn.execute(
        "SELECT DISTINCT representative_track_id FROM canonical_group"
    ).fetchall()
    assert [row["representative_track_id"] for row in pins] == [None]
    # And the election still returns a member, so read paths are unaffected.
    assert canonical.representative(conn, groups["song"]) in ("t1", "t2")


def test_make_group_can_share_a_tier_to_build_two_versions_of_one_song(conn):
    # source: canonical-tracks.md's four tiers -- two versions under one song is
    # the shape most grouping tests need, and it must be one call to express
    first = make_group(conn, ["t1"])
    second = make_group(conn, ["t2"], song=first["song"])
    assert second["song"] == first["song"]
    assert second["version"] != first["version"]


def test_canonical_read_paths_accept_a_built_group(conn):
    # source: canonical.py's viewer read path -- the assertion that matters is
    # that the engine's own reader recognises the fixture, not that rows exist
    groups = make_group(conn, ["t1", "t2"])
    assert canonical.representative(conn, groups["song"]) is not None
    counts = canonical.tier_counts(conn)
    # One group at each tier, holding both tracks -- so it is non-singleton,
    # which is the distinction the viewer's counts are actually about.
    assert counts["song"] == {"total": 1, "non_singleton": 1}
    assert counts["release"] == {"total": 1, "non_singleton": 1}


def test_generations_reads_built_generations(conn):
    # source: generations-B.md -- ordinal is stored, not derived from sort order
    make_generation(conn, ordinal=1)
    make_generation(conn, ordinal=2)
    rows = generations.generations(conn)
    assert [row["ordinal"] for row in rows] == [1, 2]


def test_make_generation_can_build_a_gap_in_the_sequence(conn):
    # source: P1-015 -- a mid-sequence generation is exactly the case that
    # desynced generation_spans(), so a fixture must be able to express one
    make_generation(conn, ordinal=1)
    make_generation(conn, ordinal=3)
    assert [row["ordinal"] for row in generations.generations(conn)] == [1, 3]


def test_make_score_writes_display_space_values(conn):
    # source: scoring-H.md -- stored values are display space (the 10-99-ish
    # number); combine() normalizes on the way in, not on the way out
    groups = make_group(conn, ["t1"])
    make_score(conn, "version", groups["version"], all_time=73.0, recent=61.0)
    row = conn.execute(
        "SELECT all_time, recent FROM score WHERE tier = 'version' AND group_id = ?",
        (str(groups["version"]),),
    ).fetchone()
    assert (row["all_time"], row["recent"]) == (73.0, 61.0)


def test_recompute_runs_over_a_built_library(conn):
    # source: scoring-H.md §9 -- recompute is whole-library and wholesale
    # replacing. If the builders produce a shape it cannot score, every scoring
    # test written on them would be worthless, so this is the load-bearing one.
    playlist = make_playlist(conn)
    for index in range(3):
        track_id = make_track(conn)
        make_membership(conn, playlist_id=playlist, track_id=track_id, position=index)
        make_play(conn, track_id=track_id, ts=days_ago(3))
    canonical.ensure_track_groups(conn)
    conn.commit()
    scoring.recompute(conn)

    counts = scoring.tier_counts(conn)
    assert counts["version"] == 3
    assert counts["track"] == 3
    assert scoring.recompute_status()["outcome"] == "ok"


# -- Committing -------------------------------------------------------------


def test_builders_commit_so_another_connection_sees_the_rows(conn):
    # source: P2_tests.md §4.2 -- route tests build on `conn` and then hit
    # `client`, which uses its own connection
    import db

    make_track(conn, "t1")
    other = db.connect()
    try:
        assert other.execute("SELECT COUNT(*) FROM track").fetchone()[0] == 1
    finally:
        other.close()


@pytest.mark.parametrize(
    "builder, table, column",
    [
        (make_artist, "artist", "artist_id"),
        (make_album, "album", "album_id"),
        (make_track, "track", "track_id"),
        (make_playlist, "snapshot", "playlist_id"),
    ],
)
def test_every_simple_builder_is_idempotent_on_a_repeated_id(conn, builder, table, column):
    # source: P2_tests.md §4.2 -- builders fill in parents, so they are called
    # again with an id that already exists as a matter of course
    builder(conn, "fixed-id")
    builder(conn, "fixed-id")
    assert conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} = 'fixed-id'"
    ).fetchone()[0] == 1
