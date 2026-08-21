"""`history_import.py` -- the GDPR streaming-history export, into `play`.

No Spotify API requests anywhere in it. Its interesting parts are all about
being run twice safely and about not trusting the zip:

- **the dedup hash** covers 16 *named* source keys rather than the whole row,
  so the day Spotify adds a key to the export (it has before, with
  `audiobook_*`) the hashes stay stable instead of re-inserting all 90k rows
  as duplicates;
- **`_extract` treats the archive as hostile** -- it flattens one top-level
  folder and then requires a bare filename, so nothing can traverse out of the
  upload folder;
- **`_offline_ts` has to handle a column that mixes units**, seconds-scale for
  most values and milliseconds-scale for a few hundred.

`UPLOAD_ROOT` is redirected by conftest to a temp path, because it is the one
filesystem path `config.py` does not own and it otherwise resolves to Finn's
real exports.
"""

import json
import os
import zipfile

import pytest

import builders
import conftest
import history_import

# A row shaped as the export actually writes one.
EXPORT_ROW = {
    "ts": "2024-03-01T10:00:00Z",
    "ms_played": 210000,
    "spotify_track_uri": "spotify:track:t1",
    "master_metadata_track_name": "A Song",
    "master_metadata_album_artist_name": "An Artist",
    "master_metadata_album_album_name": "An Album",
    "reason_start": "trackdone",
    "reason_end": "trackdone",
    "shuffle": False,
    "skipped": False,
    "platform": "ios",
    "conn_country": "GB",
    "ip_addr": "1.2.3.4",
    "offline": False,
    "offline_timestamp": 1709287200,
    "incognito_mode": False,
}


@pytest.fixture
def folder():
    """An upload folder under conftest's temp root."""
    path = os.path.join(conftest.TMP_DIR, "upload")
    os.makedirs(path, exist_ok=True)
    yield path
    for name in os.listdir(path):
        os.remove(os.path.join(path, name))
    os.rmdir(path)


def write_history(folder, name, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(rows, fh)


def import_into(conn, folder):
    counts = {
        "files_parsed": 0, "rows_read": 0, "rows_inserted": 0,
        "range_start": None, "range_end": None,
    }
    import_id = conn.execute(
        "INSERT INTO play_import (kind, folder, original_name) VALUES ('upload', ?, 'export.zip')",
        (folder,),
    ).lastrowid
    conn.commit()
    history_import._parse_folder(conn, import_id, folder, counts)
    return counts


# -- The dedup hash ---------------------------------------------------------


def test_the_hash_covers_only_the_named_source_keys(conn):
    """A key Spotify adds later must not change the hash of a row already
    imported, or a re-import would insert all 90k rows again as duplicates."""
    # source: history_import._HASH_KEYS -- "Hashing a named list rather than
    # the whole row keeps the hashes stable the day Spotify adds a key."
    base = history_import._row_hash(EXPORT_ROW)
    extended = history_import._row_hash({**EXPORT_ROW, "audiobook_title": "Something New"})

    assert extended == base


def test_the_hash_changes_when_a_covered_field_changes(conn):
    """The other half: two genuinely different plays must not collide, or the
    second would be silently dropped by the INSERT OR IGNORE."""
    # source: history_import._row_hash -- the 16 keys are what identifies a
    # play; characterization that they actually do.
    other = history_import._row_hash({**EXPORT_ROW, "ts": "2024-03-01T11:00:00Z"})

    assert other != history_import._row_hash(EXPORT_ROW)


def test_the_hash_ignores_key_order(conn):
    # source: history_import._row_hash -- json.dumps(..., sort_keys=True).
    reversed_row = dict(reversed(list(EXPORT_ROW.items())))

    assert history_import._row_hash(reversed_row) == history_import._row_hash(EXPORT_ROW)


def test_re_importing_the_same_rows_inserts_nothing_new(conn, folder):
    """The row hash is what makes re-running safe any number of times, which
    is what lets a partial import be finished by simply re-importing."""
    # source: history_import._insert_row -- INSERT OR IGNORE on the row_hash
    # primary key.
    write_history(folder, "Streaming_History_Audio_2024.json", [EXPORT_ROW])

    first = import_into(conn, folder)
    second = import_into(conn, folder)

    assert first["rows_inserted"] == 1
    assert second["rows_inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM play").fetchone()[0] == 1


# -- Field handling ---------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        # Seconds-scale: what most of the column holds.
        (1709287200, "2024-03-01T10:00:00Z"),
        # Milliseconds-scale: a few hundred rows, and 1000x wrong if treated
        # as seconds.
        (1709287200000, "2024-03-01T10:00:00Z"),
        (None, None),
    ],
)
def test_offline_timestamps_are_read_in_whichever_unit_they_carry(value, expected):
    # source: history_import._offline_ts -- "The export's offline_timestamp
    # mixes units in a single column ... Anything treating it as one unit is
    # wrong by 1000x on part of the data."
    assert history_import._offline_ts(value) == expected


@pytest.mark.parametrize(
    "value, expected", [(None, None), (True, 1), (False, 0), (1, 1), (0, 0)]
)
def test_flags_preserve_null_rather_than_collapsing_it_to_false(value, expected):
    """`offline` is genuinely absent on 334 rows, and "we don't know" is not
    the same fact as "no"."""
    # source: history_import._flag -- "0/1, preserving NULL".
    assert history_import._flag(value) == expected


def test_a_row_is_stored_with_its_reported_names(conn, folder):
    """The export's own names are kept verbatim -- they are what a uri Symr
    cannot resolve is matched on by hand later."""
    # source: history_import._insert_row -- master_metadata_* map to the
    # reported_* columns.
    write_history(folder, "Streaming_History_Audio_2024.json", [EXPORT_ROW])

    import_into(conn, folder)

    row = conn.execute(
        "SELECT reported_track_name, reported_artist_name, reported_album_name, "
        "spotify_track_uri, offline_ts FROM play"
    ).fetchone()
    assert row["reported_track_name"] == "A Song"
    assert row["reported_artist_name"] == "An Artist"
    assert row["reported_album_name"] == "An Album"
    assert row["spotify_track_uri"] == "spotify:track:t1"
    assert row["offline_ts"] == "2024-03-01T10:00:00Z"


# -- Parsing a folder -------------------------------------------------------


def test_rows_without_a_track_uri_are_read_but_not_stored(conn, folder):
    """The one filter, and it covers all of it: podcast episodes carry
    `spotify_episode_uri` instead, plus the single all-null row and audiobooks
    if they ever show up."""
    # source: history_import._parse_folder -- "The one filter. It covers all
    # of it."
    episode = {**EXPORT_ROW, "spotify_track_uri": None, "spotify_episode_uri": "spotify:episode:e1"}
    write_history(folder, "Streaming_History_Audio_2024.json", [EXPORT_ROW, episode])

    counts = import_into(conn, folder)

    # Read counts everything; inserted counts only what was stored.
    assert counts["rows_read"] == 2
    assert counts["rows_inserted"] == 1


def test_the_played_range_spans_every_file(conn, folder):
    """`range_start`/`range_end` are computed over every row read, including
    ones filtered out of `play` -- they describe the export, not the import."""
    # source: history_import._parse_folder -- the range is updated before the
    # spotify_track_uri filter.
    write_history(folder, "Streaming_History_Audio_2020.json", [
        {**EXPORT_ROW, "ts": "2020-01-01T00:00:00Z", "spotify_track_uri": "spotify:track:a"},
    ])
    write_history(folder, "Streaming_History_Audio_2024.json", [
        {**EXPORT_ROW, "ts": "2024-06-01T00:00:00Z", "spotify_track_uri": "spotify:track:b"},
    ])

    counts = import_into(conn, folder)

    assert counts["files_parsed"] == 2
    assert counts["range_start"] == "2020-01-01T00:00:00Z"
    assert counts["range_end"] == "2024-06-01T00:00:00Z"


# -- `_extract` treats the archive as hostile -------------------------------


def test_extract_takes_only_history_json_and_flattens_one_folder(conn, folder):
    # source: history_import._extract -- "Pulls only the history JSON out of
    # the export zip, flattened."
    zip_path = os.path.join(folder, "export.zip")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("MyData/Streaming_History_Audio_2024.json", "[]")
        archive.writestr("MyData/ReadMeFirst_Extended.pdf", "not json")

    history_import._extract(folder, zip_path)

    assert sorted(os.listdir(folder)) == ["Streaming_History_Audio_2024.json", "export.zip"]


def test_extract_refuses_anything_that_could_escape_the_upload_folder(conn, folder):
    """A crafted archive must not be able to write outside the folder it was
    uploaded into. The check is positive -- what survives flattening has to be
    a bare filename -- rather than a blocklist of bad shapes."""
    # source: history_import._extract -- "no separators, no '..', nothing to
    # traverse with."
    zip_path = os.path.join(folder, "export.zip")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("MyData/../../Streaming_History_Audio_evil.json", "[]")
        archive.writestr("__MACOSX/MyData/Streaming_History_Audio_2024.json", "[]")
        archive.writestr("a/b/Streaming_History_Audio_2020.json", "[]")

    history_import._extract(folder, zip_path)

    assert os.listdir(folder) == ["export.zip"]
    assert not os.path.exists(
        os.path.join(conftest.TMP_DIR, "Streaming_History_Audio_evil.json")
    )


# -- `latest_upload` --------------------------------------------------------


def upload_row(conn, kind="upload", files_parsed=3, folder="/tmp/x"):
    return conn.execute(
        "INSERT INTO play_import (kind, folder, original_name, files_parsed) "
        "VALUES (?, ?, 'export.zip', ?)",
        (kind, folder, files_parsed),
    ).lastrowid


def test_the_latest_upload_is_the_newest_one_that_parsed_something(conn):
    """A corrupt zip still creates a folder and a row. Without the
    `files_parsed > 0` filter the newest upload would be an empty folder, and
    a re-import would quietly read nothing instead of re-checking the last
    good export."""
    # source: history_import.latest_upload -- its docstring, verbatim.
    good = upload_row(conn, files_parsed=3, folder="/data/good")
    upload_row(conn, files_parsed=0, folder="/data/corrupt")
    conn.commit()

    assert history_import.latest_upload(conn)["id"] == good


def test_an_in_flight_upload_is_not_the_latest_upload(conn):
    # source: history_import.latest_upload -- "It also excludes an in-flight
    # upload, whose files_parsed is still NULL."
    done = upload_row(conn, files_parsed=3)
    upload_row(conn, files_parsed=None)
    conn.commit()

    assert history_import.latest_upload(conn)["id"] == done


def test_a_reimport_is_never_the_latest_upload(conn):
    """Reimport rows copy their folder from the upload they re-read, so
    counting them would just point back at the same folder by a longer route."""
    # source: history_import.latest_upload -- "only uploads count".
    upload = upload_row(conn, kind="upload", files_parsed=3)
    upload_row(conn, kind="reimport", files_parsed=3)
    conn.commit()

    assert history_import.latest_upload(conn)["id"] == upload


# -- Coverage, on the two bases (step D split them apart) -------------------


def test_coverage_counts_known_to_symr_separately_from_in_your_library(conn):
    """Two different questions. "Known" approaches 100% by construction once
    the round-trip has run and stops being interesting; "in your library" is
    the number that means something, and the round-trip does not change it."""
    # source: foreign-roundtrip-D.md §8 / history_import.coverage_counts --
    # the two bases and why they differ.
    in_library = builders.make_track(conn)
    builders.make_membership(conn, track_id=in_library)
    known_only = builders.make_track(conn)

    builders.make_play(conn, track_id=in_library)
    builders.make_play(conn, track_id=known_only)
    builders.make_play(conn, uri="spotify:track:never-seen")

    counts = history_import.coverage_counts(conn)

    assert counts["total_plays"] == 3
    assert counts["distinct_uris"] == 3
    assert counts["known_uris"] == 2
    assert counts["foreign_uris"] == 1
    assert counts["in_library_uris"] == 1


def test_a_relinked_uri_counts_as_known(conn):
    """Every resolution goes through `played_uri_track`, never a bare
    `track.uri` join, so a uri Spotify substituted resolves too."""
    # source: history_import.coverage_counts -- "Every resolution goes through
    # played_uri_track ... so relinked uris resolve too."
    track = builders.make_track(conn)
    builders.make_play(conn, uri="spotify:track:requested")
    conn.execute(
        "INSERT INTO track_uri_alias (requested_uri, track_id) VALUES ('spotify:track:requested', ?)",
        (track,),
    )
    conn.commit()

    counts = history_import.coverage_counts(conn)

    assert counts["known_uris"] == 1
    assert counts["foreign_uris"] == 0


def test_coverage_percentages_are_zero_rather_than_dividing_by_zero(conn):
    # source: history_import.coverage_counts -- the `if total_plays else 0`
    # guard on both percentages.
    counts = history_import.coverage_counts(conn)

    assert counts["known_pct"] == 0
    assert counts["in_library_pct"] == 0


def test_never_played_counts_library_tracks_only(conn):
    """Counting every `track` row would make this nonsense the day the
    round-trip adds ~6,000 tracks that are in no playlist."""
    # source: history_import.coverage_counts -- "Both of these count library
    # tracks only -- a track with a membership row."
    unplayed_in_library = builders.make_track(conn)
    builders.make_membership(conn, track_id=unplayed_in_library)
    # Known to Symr, in no playlist, never played: counts toward neither.
    builders.make_track(conn)

    counts = history_import.coverage_counts(conn)

    assert counts["tracks_never_played"] == 1
    assert counts["library_tracks_total"] == 1
    assert counts["tracks_total"] == 2
