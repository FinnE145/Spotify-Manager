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

`UPLOAD_ROOT` is redirected by conftest to a temp path, because its default
(unset) otherwise resolves to Finn's real exports.
"""

import io
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


# -- save_upload --------------------------------------------------------


class _FakeUpload:
    """A minimal stand-in for Werkzeug's FileStorage -- save_upload only ever
    calls .save(path) on whatever it's given."""

    def save(self, path):
        with open(path, "wb") as fh:
            fh.write(b"fake zip bytes")


def test_save_upload_reuses_the_same_folder_within_one_frozen_second(conn):
    """The folder name is derived from the clock, and the suite's autouse
    freezer never advances it -- so two uploads in the same test session (or
    the same test) land on the identical folder path. Without exist_ok=True,
    the second os.makedirs would raise FileExistsError."""
    # source: S_sweep.md §3 -- true at history_import.py:90
    first = history_import.save_upload(_FakeUpload())
    second = history_import.save_upload(_FakeUpload())

    assert first == second


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
        # Exactly at the threshold: the boundary is >=, so a value equal to
        # 1e11 is treated as milliseconds-scale, not seconds-scale. A >
        # comparison here would read this as seconds and land in the year
        # 5138 instead.
        (100_000_000_000, "1973-03-03T09:46:40Z"),
    ],
)
def test_offline_timestamps_are_read_in_whichever_unit_they_carry(value, expected):
    # source: S_sweep.md §3 -- cmp>= at history_import.py:295. Also:
    # history_import._offline_ts -- "The export's offline_timestamp mixes
    # units in a single column ... Anything treating it as one unit is wrong
    # by 1000x on part of the data."
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


def test_a_reimport_of_an_empty_folder_records_zero_of_everything(conn, folder):
    # source: S_sweep.md §3.4 E -- `_run_import`'s local `counts` dict
    # initialises files_parsed/rows_read/rows_inserted to 0 (lines 128-130).
    # Calling `_run_import` directly (bypassing jobs.try_start's slot, which
    # isn't this line's concern) with a folder holding no matching JSON files
    # means `_parse_folder` never touches any of the three counters, so
    # whatever `_finish` writes to `play_import` is exactly the literal these
    # lines initialise them to -- 1, under the mutant, not 0.
    history_import._run_import("reimport", folder, "export.zip")

    # `_run_import` inserts its own play_import row (that's the point of
    # calling it rather than _parse_folder directly), so it's read back by
    # folder rather than by a pre-made id.
    row = conn.execute(
        "SELECT files_parsed, rows_read, rows_inserted FROM play_import WHERE folder = ?",
        (folder,),
    ).fetchone()
    assert row["files_parsed"] == 0
    assert row["rows_read"] == 0
    assert row["rows_inserted"] == 0


def test_a_freshly_reset_status_reports_zero_of_everything(conn):
    # source: S_sweep.md §3.4 E -- `_status`'s JobStatus constructor defaults
    # (lines 63-66). `jobs.JobStatus.reset()` rebuilds `_fields` from exactly
    # these defaults before applying whatever `_reset_status` passes (phase,
    # action, started_at) -- none of which touch the four counters -- so a
    # freshly reset status is a direct read of the constructor's literals.
    history_import._reset_status("upload")

    status = history_import.get_status()
    assert status["files_total"] == 0
    assert status["files_done"] == 0
    assert status["rows_read"] == 0
    assert status["rows_inserted"] == 0


def test_reset_status_sets_the_starting_phase_for_the_action(conn):
    """An upload still has a zip to pull apart first; a reimport re-reads
    JSON that's already on disk and starts straight into parsing."""
    # source: S_sweep.md §3 -- eq at history_import.py:112
    history_import._reset_status("upload")
    assert history_import.get_status()["phase"] == "extracting"

    history_import._reset_status("reimport")
    assert history_import.get_status()["phase"] == "parsing"


def test_reimport_never_tries_to_extract_a_zip(conn, folder):
    """A reimport's folder holds only the already-extracted JSON -- no
    export.zip. If `_run_import` ever attempted to extract one anyway for a
    reimport, it would fail immediately (no such file), and this would come
    back as an error rather than a clean parse."""
    # source: S_sweep.md §3 -- eq at history_import.py:142
    write_history(folder, "Streaming_History_Audio_2024.json", [EXPORT_ROW])

    history_import._run_import("reimport", folder, "export.zip")

    status = history_import.get_status()
    assert status["phase"] == "done"
    assert status["error"] is None


def test_a_failed_import_still_records_its_error_on_the_play_import_row(conn, folder):
    """`_finish` is what writes the error onto the play_import row that
    `/dev/import` reads back -- it has to run even when the parse itself
    blew up partway through, or a failed import would leave its row looking
    exactly like one still in progress."""
    # source: S_sweep.md §3 -- isnot at history_import.py:161
    with open(
        os.path.join(folder, "Streaming_History_Audio_2024.json"), "w", encoding="utf-8"
    ) as fh:
        fh.write("not valid json{")

    history_import._run_import("reimport", folder, "export.zip")

    row = conn.execute(
        "SELECT error FROM play_import WHERE folder = ?", (folder,)
    ).fetchone()
    assert row["error"] is not None


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


def test_extract_refuses_a_name_with_a_slash_even_without_a_backslash(conn, folder):
    """The three refusal conditions are ORed independently -- a name needs
    only one of them to be unsafe. This name has a "/" but no "\\" and no
    "..", and the fnmatch glob's `*` happily spans the embedded slash, so
    only the traversal check itself stands between this and a write into a
    subdirectory of `folder` that doesn't exist."""
    # source: S_sweep.md §3 -- or at history_import.py:183 col 27 (first `or`)
    zip_path = os.path.join(folder, "export.zip")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("top/Streaming_History_x/y.json", "[]")

    history_import._extract(folder, zip_path)

    assert os.listdir(folder) == ["export.zip"]


def test_extract_refuses_a_name_with_a_backslash_even_without_a_slash(conn, folder):
    """The mirror of the slash case: a name with only a literal backslash and
    no "/" or ".." is still refused. On POSIX a lone backslash is just an
    ordinary filename character, so if the refusal ever slipped, this would
    silently succeed and leave an oddly-named file behind."""
    # source: S_sweep.md §3 -- or at history_import.py:183 col 43 (second `or`)
    zip_path = os.path.join(folder, "export.zip")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("top/Streaming_History_x\\y.json", "[]")

    history_import._extract(folder, zip_path)

    assert os.listdir(folder) == ["export.zip"]


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


def test_latest_upload_picks_the_highest_id_among_two_qualifying_uploads(conn):
    """The existing "newest" tests each pair one qualifying upload against a
    row the WHERE clause filters out entirely (corrupt, in-flight, or a
    reimport) -- none of them puts two genuinely qualifying uploads side by
    side, so none of them actually exercises ORDER BY ... DESC."""
    # source: S_sweep.md §3 -- sqlDESC at history_import.py:347
    older = upload_row(conn, files_parsed=3, folder="/data/older")
    newer = upload_row(conn, files_parsed=5, folder="/data/newer")
    conn.commit()

    assert history_import.latest_upload(conn)["id"] == newer


# -- `import_rows` -----------------------------------------------------------


def test_import_rows_are_ordered_newest_first(conn):
    # source: S_sweep.md §3 -- sqlDESC at history_import.py:354
    first = upload_row(conn, folder="/data/a")
    second = upload_row(conn, folder="/data/b")
    conn.commit()

    rows = history_import.import_rows(conn)

    assert [r["id"] for r in rows] == [second, first]


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


def test_distinct_and_known_and_in_library_uris_count_uris_not_plays(conn):
    """Three separate COUNT(DISTINCT ...) queries collapse to plain COUNT(*)
    if the DISTINCT is dropped -- invisible on the existing fixtures because
    none of them ever play the same uri twice. Two plays of one in-library
    track is the minimal case that tells "how many uris" apart from "how many
    plays"."""
    # source: S_sweep.md §3 -- sqlDISTINCT at history_import.py:373, :376, :384
    track = builders.make_track(conn)
    builders.make_membership(conn, track_id=track)
    builders.make_play(conn, track_id=track)
    builders.make_play(conn, track_id=track)

    counts = history_import.coverage_counts(conn)

    assert counts["total_plays"] == 2
    assert counts["distinct_uris"] == 1
    assert counts["known_uris"] == 1
    assert counts["in_library_uris"] == 1
    # These two count plays, not uris, so replaying the one track doubles them.
    assert counts["known_plays"] == 2
    assert counts["in_library_plays"] == 2


def test_the_play_range_reports_earliest_and_latest_ts(conn):
    """A separate MIN/MAX query from the one `_parse_folder` computes during
    an import -- this one reads straight off the `play` table, and nothing
    exercised it before."""
    # source: S_sweep.md §3 -- sqlMIN/sqlMAX at history_import.py:393
    builders.make_play(conn, uri="spotify:track:a", ts="2020-01-01T00:00:00Z")
    builders.make_play(conn, uri="spotify:track:b", ts="2024-06-01T00:00:00Z")

    counts = history_import.coverage_counts(conn)

    assert counts["play_range_start"] == "2020-01-01T00:00:00Z"
    assert counts["play_range_end"] == "2024-06-01T00:00:00Z"


def test_coverage_percentages_scale_and_round_correctly(conn):
    """The zero-division guard is already covered; this is the arithmetic
    itself -- both the *100 scaling and the round-to-one-decimal-place --
    with numbers chosen so a wrong multiplier or a wrong rounding precision
    each produce a value that disagrees with the correct one."""
    # source: S_sweep.md §3 -- num at history_import.py:403, :406
    in_library = builders.make_track(conn)
    builders.make_membership(conn, track_id=in_library)
    known_only = builders.make_track(conn)

    builders.make_play(conn, track_id=in_library)
    builders.make_play(conn, track_id=known_only)
    builders.make_play(conn, uri="spotify:track:never-seen")

    counts = history_import.coverage_counts(conn)

    assert counts["known_pct"] == 66.7
    assert counts["in_library_pct"] == 33.3


def test_tracks_never_played_only_counts_a_tracks_own_absence_of_plays(conn):
    """The subquery correlates on `x.track_id = t.track_id` and joins on
    `p.spotify_track_uri = x.uri`. Either `=` becoming `<>` turns "this
    specific track has no matching play" into "some other track has (or
    hasn't) a play", which the single-played-track fixture above can't tell
    apart -- it needs at least two played tracks with different uris before
    a mismatched join can accidentally find a match."""
    # source: S_sweep.md §3 -- sql= at history_import.py:421 and :422
    played_a = builders.make_track(conn)
    builders.make_membership(conn, track_id=played_a)
    builders.make_play(conn, track_id=played_a)

    played_b = builders.make_track(conn)
    builders.make_membership(conn, track_id=played_b)
    builders.make_play(conn, track_id=played_b)

    unplayed = builders.make_track(conn)
    builders.make_membership(conn, track_id=unplayed)

    counts = history_import.coverage_counts(conn)

    assert counts["tracks_never_played"] == 1


# -- app.py: /dev/import and its two POST routes -----------------------------


def test_the_import_page_toggles_the_reimport_button_on_has_upload(client, conn):
    """`has_upload` decides whether the Re-import button is disabled. Without
    a usable upload it must be disabled; with one, it must not be."""
    # source: S_sweep.md §3 -- isnot at app.py:322
    without = client.get("/dev/import")
    assert b'id="reimport-btn" type="button" disabled' in without.data

    upload_row(conn, files_parsed=3)
    conn.commit()

    with_upload = client.get("/dev/import")
    assert b'id="reimport-btn" type="button" disabled' not in with_upload.data


def _real_zip_upload():
    """A genuine (if trivial) export zip -- valid enough for `_extract` to
    read cleanly end to end, unlike `_zip_upload()`'s fake bytes in
    test_routes.py, which is fine there because it never gets past the slot
    check."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("Streaming_History_Audio_2024.json", "[]")
    buf.seek(0)
    return {
        "data": {"file": (buf, "export.zip")},
        "content_type": "multipart/form-data",
    }


def test_the_import_route_reports_started_once_the_job_claims_the_slot(
    client, run_jobs_inline
):
    """The route's own literal response, not the hook's -- there is no
    authentication guard shadowing this one the way there is for the
    roundtrip/backfill/snapshot starters (S_sweep.md §3.4 B)."""
    # source: S_sweep.md §3 -- true at app.py:830
    resp = client.post("/api/history/import", **_real_zip_upload())

    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}
    assert run_jobs_inline == ["history_import"]


def test_the_reimport_route_reports_started_once_the_job_claims_the_slot(
    client, conn, run_jobs_inline
):
    # source: S_sweep.md §3 -- true at app.py:840
    upload_row(conn, files_parsed=3, folder=os.path.join(conftest.TMP_DIR, "reimport-src"))
    conn.commit()

    resp = client.post("/api/history/reimport")

    assert resp.status_code == 200
    assert resp.get_json() == {"started": True}
    assert run_jobs_inline == ["history_import"]
