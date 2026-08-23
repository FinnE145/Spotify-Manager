"""Play-history import: reads Spotify's GDPR Extended Streaming History
export (a local zip) into the play table (see docs/specs/play-history-C.md).

Local file import start to finish -- no Spotify API requests, no writes to
the user's library, no new scopes. play rows are only ever inserted, never
deleted or updated."""

import fnmatch
import glob
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone

import config
import db
import jobs
import scoring

# One folder per upload, named for the upload time (UTC, like every other
# timestamp here). The export's chunking isn't stable between exports, so
# overwriting a shared folder by filename would leave orphans from the old
# chunking that later imports would silently re-count.
UPLOAD_ROOT = config.UPLOAD_ROOT

_ZIP_NAME = "export.zip"
_JSON_GLOB = "Streaming_History_*.json"

# A single 90k-row transaction holds SQLite's write lock long enough to
# block anything else that wants it; chunked commits keep each hold short.
_COMMIT_EVERY = 5000

# The exact source keys the dedup hash covers. Hashing a named list rather
# than the whole row keeps the hashes stable the day Spotify adds a key to
# the export (it has before, with audiobook_*) -- hashing the whole row
# would change every hash and re-insert all 90k rows as duplicates.
_HASH_KEYS = [
    "ts",
    "ms_played",
    "spotify_track_uri",
    "master_metadata_track_name",
    "master_metadata_album_artist_name",
    "master_metadata_album_album_name",
    "reason_start",
    "reason_end",
    "shuffle",
    "skipped",
    "platform",
    "conn_country",
    "ip_addr",
    "offline",
    "offline_timestamp",
    "incognito_mode",
]

_status = jobs.JobStatus(
    "history_import",
    phase=None,  # "extracting" | "parsing" | "done" | "error"
    action=None,  # "upload" | "reimport"
    current_file=None,
    files_total=0,
    files_done=0,
    rows_read=0,
    rows_inserted=0,
    started_at=None,
    finished_at=None,
    error=None,
)


def get_status():
    return _status.get()


def busy():
    """True if any background job is running. Lets the upload route reject
    early instead of accepting a ~66 MB body it can't use."""
    return jobs.active() is not None


# -- Triggering -------------------------------------------------------


def save_upload(upload):
    """Persists the uploaded zip into a fresh upload folder and returns it.
    Runs in the request, because the FileStorage doesn't outlive it."""
    folder = os.path.join(UPLOAD_ROOT, datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    upload.save(os.path.join(folder, _ZIP_NAME))
    return folder


def start_upload(folder, original_name):
    return _start("upload", folder, original_name)


def start_reimport(folder, original_name):
    return _start("reimport", folder, original_name)


def _start(action, folder, original_name):
    # One job slot for the whole app -- an import, a snapshot pull and a
    # round-trip are mutually exclusive, and jobs.try_start settles that
    # with a single atomic check-and-set.
    return jobs.try_start("history_import", _run_import, action, folder, original_name)


def _reset_status(action):
    _status.reset(
        phase="extracting" if action == "upload" else "parsing",
        action=action,
        started_at=jobs.now_iso(),
    )


# -- The import run -------------------------------------------------


def _run_import(action, folder, original_name):
    conn = db.connect()
    import_id = None
    # Only ever updated right after a commit, so an error mid-run leaves
    # counts describing what actually landed -- the rolled-back chunk is
    # excluded, even though conn.total_changes still counts it.
    counts = {
        "files_parsed": 0,
        "rows_read": 0,
        "rows_inserted": 0,
        "range_start": None,
        "range_end": None,
    }
    try:
        _reset_status(action)
        import_id = conn.execute(
            "INSERT INTO play_import (kind, folder, original_name) VALUES (?, ?, ?)",
            (action, folder, original_name),
        ).lastrowid
        conn.commit()

        if action == "upload":
            zip_path = os.path.join(folder, _ZIP_NAME)
            # The zip goes even when extraction fails. Nothing ever re-reads it
            # -- a re-import reads the extracted JSON -- so a corrupt one would
            # just sit there at ~66 MB waiting to be deleted by hand.
            try:
                _extract(folder, zip_path)
            finally:
                os.remove(zip_path)

        _parse_folder(conn, import_id, folder, counts)
        _finish(conn, import_id, counts, None)
        scoring.recompute(conn)
        _status.set(phase="done", current_file=None, finished_at=jobs.now_iso())
    except Exception as e:
        # Partial imports are kept: the committed chunks stay, only the
        # in-flight one is discarded. A re-import fills the rest -- the row
        # hash makes re-running safe any number of times.
        conn.rollback()
        if import_id is not None:
            _finish(conn, import_id, counts, str(e))
            scoring.recompute(conn)
        _status.set(phase="error", error=str(e), finished_at=jobs.now_iso())
    finally:
        conn.close()


def _extract(folder, zip_path):
    """Pulls only the history JSON out of the export zip, flattened. Every
    other entry -- the ReadMe PDF, __MACOSX/ junk, directories -- is skipped,
    and so is anything whose path could escape the upload folder."""
    _status.set(phase="extracting")
    with zipfile.ZipFile(zip_path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            # Strip the export's single top-level folder, then require what's
            # left to be a bare filename: no separators, no "..", nothing to
            # traverse with. __MACOSX entries keep a folder component here and
            # are dropped by that check alone.
            name = entry.filename.split("/", 1)[-1]
            if "/" in name or "\\" in name or ".." in name:
                continue
            if not fnmatch.fnmatch(name, _JSON_GLOB):
                continue
            with archive.open(entry) as src, open(os.path.join(folder, name), "wb") as dst:
                shutil.copyfileobj(src, dst)


def _parse_folder(conn, import_id, folder, counts):
    files = sorted(glob.glob(os.path.join(folder, _JSON_GLOB)))
    _status.set(phase="parsing", files_total=len(files))

    rows_read = 0
    files_done = 0
    range_start = range_end = None
    before = conn.total_changes
    pending = 0

    def checkpoint():
        conn.commit()
        counts.update(
            files_parsed=files_done,
            rows_read=rows_read,
            rows_inserted=conn.total_changes - before,
            range_start=range_start,
            range_end=range_end,
        )
        _status.set(
            files_done=files_done,
            rows_read=counts["rows_read"],
            rows_inserted=counts["rows_inserted"],
        )

    for path in files:
        source_file = os.path.basename(path)
        _status.set(current_file=source_file)
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)

        for row in rows:
            rows_read += 1
            ts = row.get("ts")
            if ts:
                if range_start is None or ts < range_start:
                    range_start = ts
                if range_end is None or ts > range_end:
                    range_end = ts
            # The one filter. It covers all of it: podcast episodes (which
            # carry spotify_episode_uri instead), the single all-null row,
            # and audiobooks if they ever show up.
            if not row.get("spotify_track_uri"):
                continue
            _insert_row(conn, import_id, source_file, row)
            pending += 1
            if pending >= _COMMIT_EVERY:
                checkpoint()
                pending = 0

        files_done += 1
        checkpoint()
        pending = 0


def _finish(conn, import_id, counts, error):
    conn.execute(
        "UPDATE play_import SET files_parsed = ?, rows_read = ?, rows_inserted = ?, "
        "range_start = ?, range_end = ?, error = ? WHERE id = ?",
        (
            counts["files_parsed"],
            counts["rows_read"],
            counts["rows_inserted"],
            counts["range_start"],
            counts["range_end"],
            error,
            import_id,
        ),
    )
    conn.commit()


# -- Row handling -------------------------------------------------


def _row_hash(row):
    canonical = json.dumps(
        {key: row.get(key) for key in _HASH_KEYS}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha1(canonical.encode()).hexdigest()


def _flag(value):
    """0/1, preserving NULL -- `offline` is genuinely absent on 334 rows."""
    return None if value is None else (1 if value else 0)


def _offline_ts(value):
    """The export's offline_timestamp mixes units in a single column: most
    values are seconds-scale, a few hundred are milliseconds-scale. Anything
    treating it as one unit is wrong by 1000x on part of the data. The
    threshold sits three orders of magnitude clear of both ranges."""
    if value is None:
        return None
    seconds = value / 1000 if value >= 1e11 else value
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_row(conn, import_id, source_file, row):
    conn.execute(
        """
        INSERT OR IGNORE INTO play (
            row_hash, import_id, source_file, ts, ms_played, spotify_track_uri,
            reported_track_name, reported_artist_name, reported_album_name,
            reason_start, reason_end, shuffle, skipped, platform, conn_country,
            ip_addr, offline, offline_ts, incognito_mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _row_hash(row),
            import_id,
            source_file,
            row.get("ts"),
            row.get("ms_played"),
            row.get("spotify_track_uri"),
            row.get("master_metadata_track_name"),
            row.get("master_metadata_album_artist_name"),
            row.get("master_metadata_album_album_name"),
            row.get("reason_start"),
            row.get("reason_end"),
            _flag(row.get("shuffle")),
            _flag(row.get("skipped")),
            row.get("platform"),
            row.get("conn_country"),
            row.get("ip_addr"),
            _flag(row.get("offline")),
            _offline_ts(row.get("offline_timestamp")),
            _flag(row.get("incognito_mode")),
        ),
    )


# -- Reads for the page -------------------------------------------------


def latest_upload(conn):
    """The newest *usable* upload folder -- what a re-import re-reads.

    Reimport rows copy their folder from the upload they re-read, so only
    uploads count. files_parsed > 0 skips uploads that failed before parsing
    anything (a corrupt zip, say): they still created a folder and a row, so
    without this the newest one would be an empty folder and re-import would
    quietly read nothing instead of re-checking the last good export. It
    also excludes an in-flight upload, whose files_parsed is still NULL."""
    return conn.execute(
        "SELECT id, folder, original_name FROM play_import "
        "WHERE kind = 'upload' AND files_parsed > 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def import_rows(conn):
    return conn.execute(
        "SELECT id, kind, uploaded_at, original_name, files_parsed, rows_read, "
        "rows_inserted, range_start, range_end, error FROM play_import ORDER BY id DESC"
    ).fetchall()


def coverage_counts(conn):
    """Coverage on two different bases, because step D split them apart (see
    docs/specs/foreign-roundtrip-D.md §8):

    - **known to Symr** -- the played uri resolves through played_uri_track,
      i.e. Symr holds a track row for it. After a round-trip this approaches
      100% by construction and stops being an interesting number.
    - **in your library** -- it resolves to a track that also has a membership
      row. That is the number that actually means something, and the
      round-trip does not change it.

    Every resolution goes through played_uri_track, never a bare track.uri
    join, so relinked uris resolve too."""
    total_plays = conn.execute("SELECT COUNT(*) FROM play").fetchone()[0]
    distinct_uris = conn.execute(
        "SELECT COUNT(DISTINCT spotify_track_uri) FROM play"
    ).fetchone()[0]
    known_uris = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT p.spotify_track_uri FROM play p "
        "JOIN played_uri_track x ON x.uri = p.spotify_track_uri)"
    ).fetchone()[0]
    known_plays = conn.execute(
        "SELECT COUNT(*) FROM play p WHERE EXISTS "
        "(SELECT 1 FROM played_uri_track x WHERE x.uri = p.spotify_track_uri)"
    ).fetchone()[0]
    in_library_uris = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT p.spotify_track_uri FROM play p "
        "JOIN played_uri_track x ON x.uri = p.spotify_track_uri "
        "WHERE EXISTS (SELECT 1 FROM membership m WHERE m.track_id = x.track_id))"
    ).fetchone()[0]
    in_library_plays = conn.execute(
        "SELECT COUNT(*) FROM play p WHERE EXISTS "
        "(SELECT 1 FROM played_uri_track x WHERE x.uri = p.spotify_track_uri "
        " AND EXISTS (SELECT 1 FROM membership m WHERE m.track_id = x.track_id))"
    ).fetchone()[0]
    range_row = conn.execute("SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM play").fetchone()
    return {
        "total_plays": total_plays,
        "distinct_uris": distinct_uris,
        "known_uris": known_uris,
        # Still derived, never a stored flag: a uri is foreign exactly as long
        # as Symr holds no track for it, and the round-trip is what changes
        # that.
        "foreign_uris": distinct_uris - known_uris,
        "known_plays": known_plays,
        "known_pct": round(known_plays * 100 / total_plays, 1) if total_plays else 0,
        "in_library_uris": in_library_uris,
        "in_library_plays": in_library_plays,
        "in_library_pct": round(in_library_plays * 100 / total_plays, 1) if total_plays else 0,
        "play_range_start": range_row["lo"],
        "play_range_end": range_row["hi"],
        "tracks_total": conn.execute("SELECT COUNT(*) FROM track").fetchone()[0],
        # Both of these count *library* tracks only -- a track with a
        # membership row. Counting every track row would make this nonsense
        # the day the round-trip adds ~6,000 tracks that are in no playlist.
        "library_tracks_total": conn.execute(
            "SELECT COUNT(*) FROM track t "
            "WHERE EXISTS (SELECT 1 FROM membership m WHERE m.track_id = t.track_id)"
        ).fetchone()[0],
        "tracks_never_played": conn.execute(
            "SELECT COUNT(*) FROM track t "
            "WHERE EXISTS (SELECT 1 FROM membership m WHERE m.track_id = t.track_id) "
            "  AND NOT EXISTS (SELECT 1 FROM played_uri_track x "
            "                  JOIN play p ON p.spotify_track_uri = x.uri "
            "                  WHERE x.track_id = t.track_id)"
        ).fetchone()[0],
    }
