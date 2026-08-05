# Play History Ingestion — Feature Spec

**Roadmap step C.** Status: **ready to implement**. This spec is the standalone implementation prompt — an implement session can start from just this file. Follow `/symr-implement`: ask live for anything this doesn't decide.

> **Branch:** this work lives on `feat/play-history-C`. Check with `git branch --show-current`.

## Read first
- `CLAUDE.md` — conventions, KISS, the no-assumptions rule.
- `docs/Planning/listening_data_roadmap.md` — step C in context. **Where it disagrees with this spec, this spec is right** (see *Corrections to the roadmap* below).
- Existing code to mirror: `snapshot.py` (`_status` / `_status_lock` / `_set_status` / `get_status` / `_start` / `summary_counts`), `db.py` (`SCHEMA`, `_migrate`), `app.py` (`/api/snapshot/*` routes), `templates/snapshot.html`, `static/js/snapshot.js` (progress bar + status poller), `templates/dev.html`.

## What this is

Import Finn's Spotify GDPR **Extended Streaming History** export into a `play` table, with an upload page that keeps a record of every import.

**No Spotify API requests. No writes to Spotify. No new scopes.** This is a local file import start to finish.

Scope is ingest plus a coverage summary. No charts, no per-play browsing, no ListenBrainz.

## Measured facts

Everything here was measured on 2026-08-03 against Finn's real export (`~/Downloads/Spotify Extended Streaming History/`, dated 2026-07-01) and the live `symr.db` (3,611 tracks). Don't re-derive; don't trust a contradicting number without re-measuring.

| | |
|---|---|
| Files in the zip | 13 JSON (9 `Streaming_History_Audio_*`, 4 `Streaming_History_Video_*`), plus a ReadMe PDF and `__MACOSX/` junk |
| Rows across all 13 | 90,662 |
| Rows with a `spotify_track_uri` | 90,351 (89,858 audio + 493 video) |
| Rows discarded | 311 — 310 podcast episodes, 1 with neither track nor episode uri |
| Rows stored after dedup | **90,338** |
| Date range | 2020-02-12 → 2026-06-30 |
| Distinct played URIs | 8,908 — all `spotify:track:`, no local files |
| In library / foreign | 2,820 / 6,088 |
| Plays on in-library tracks | 76,399 (**84.6%**) |
| Library tracks never played | 791 of 3,611 |
| Parse + hash time, all 13 files | **~1.0 s** |

The 23 keys are identical in every file, audio and video alike, across all seven years — no schema drift to handle.

### Corrections to the roadmap

The roadmap's step-C section was written before the export was measured. Three of its claims are wrong:

1. **`(ts, spotify_track_uri)` is not a unique key.** 228 keys are duplicated, covering 255 extra rows, **all within a single file**, and 225 of those have genuinely different payloads. `ts` is the play *end* time, so a crash-and-resume produces two real plays sharing it:

   ```
   2021-05-28T14:39:10Z  Layla   78,020ms  playbtn → unexpected-exit-while-paused
   2021-05-28T14:39:10Z  Layla  156,040ms  appload → fwdbtn
   ```

   `INSERT OR IGNORE` on that key would silently discard 255 real plays. See *Dedup* for what's used instead.

2. **The export has video files, and they hold real track plays.** `Streaming_History_Video_*.json` is 501 rows: 493 carry a `spotify_track_uri` (music videos), 7 are podcast episodes, 1 is neither. **None of those 493 appear in the audio files.** They are imported.

3. **"303 podcast rows" undercounts.** Across audio *and* video it's 310 episode rows plus 1 all-null row = 311 discarded.

Also worth carrying: the roadmap's "full re-import every time" is preserved in spirit but narrowed — see *Re-import*.

## Data model

Additive tables in `SCHEMA`. Nothing existing is modified, so `_migrate` needs no new entry.

```sql
CREATE TABLE IF NOT EXISTS play_import (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN ('upload', 'reimport')),
    source        TEXT NOT NULL DEFAULT 'export',
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
    original_name TEXT,
    folder        TEXT NOT NULL,
    files_parsed  INTEGER,
    rows_read     INTEGER,
    rows_inserted INTEGER,
    range_start   TEXT,
    range_end     TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS play (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash          TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL DEFAULT 'export',
    import_id         INTEGER REFERENCES play_import(id),
    source_file       TEXT,
    ts                TEXT NOT NULL,
    ms_played         INTEGER NOT NULL,
    spotify_track_uri TEXT NOT NULL,
    reported_track_name  TEXT,
    reported_artist_name TEXT,
    reported_album_name  TEXT,
    reason_start      TEXT,
    reason_end        TEXT,
    shuffle           INTEGER,
    skipped           INTEGER,
    platform          TEXT,
    conn_country      TEXT,
    ip_addr           TEXT,
    offline           INTEGER,
    offline_ts        TEXT,
    incognito_mode    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_play_uri ON play(spotify_track_uri);
CREATE INDEX IF NOT EXISTS idx_play_ts ON play(ts);
CREATE INDEX IF NOT EXISTS idx_play_import ON play(import_id);
```

### Column mapping

| export key | column | notes |
|---|---|---|
| `ts` | `ts` | verbatim ISO-8601 Z |
| `ms_played` | `ms_played` | |
| `spotify_track_uri` | `spotify_track_uri` | |
| `master_metadata_track_name` | `reported_track_name` | |
| `master_metadata_album_artist_name` | `reported_artist_name` | |
| `master_metadata_album_album_name` | `reported_album_name` | |
| `reason_start` / `reason_end` | same | raw strings |
| `shuffle` / `skipped` / `incognito_mode` | same | INTEGER 0/1 |
| `offline` | `offline` | INTEGER 0/1, **NULL preserved** (334 rows) |
| `offline_timestamp` | `offline_ts` | normalized, see below |
| `platform` | `platform` | **raw**, normalized at query time |
| `conn_country` / `ip_addr` | same | |

**Dropped columns.** The four `audiobook_*` keys and the three `episode_*` keys are not stored. Every row that could populate them is discarded by the filter, so the columns would be permanently NULL.

### Notes on the model

- **The `reported_*` prefix is load-bearing.** These are the names the *source claimed at play time*, not resolved entities — they must never be joined against `track.name`, `artist.name` or `album.name`. Note especially that `master_metadata_album_artist_name` is the **album** artist, so it won't match `track.artists` on featured credits. They exist as a fallback label for foreign URIs and as a sanity check, nothing more.
- **No foreign key from `play` to `track`, and no denormalized `track_id`.** 6,088 of the 8,908 played URIs have no `track` row, so an FK is impossible. Resolution is a query-time join, `play.spotify_track_uri = track.uri`, backed by `idx_play_uri`. This is deliberately self-healing: when step D imports the foreign tracks, the same join resolves them with zero reprocessing and no re-resolve pass.
- **"Foreign" is not a stored flag.** It's the absence of a matching `track` row — a `LEFT JOIN … WHERE t.track_id IS NULL`. Storing it would go stale the moment D lands.
- **No per-play `raw_json`.** Unlike `track.raw_json` — irreplaceable, because every enrichment endpoint 403s — the export files stay on disk and re-parsing is free and offline. A blob would add ~64 MB to an 18 MB database to duplicate what's already on disk.
- **No `media_type` column.** Audio vs video is 493 rows (0.55%), and for every downstream use a music-video play is just a play. `source_file` already carries `_Video_` in the filename if it ever needs segmenting.
- **No `recording_mbid`.** Added when ListenBrainz is, not before.
- **`play` rows are never deleted.** Import only ever inserts. So even if a future export were non-cumulative, re-importing could not erase history.

### Normalization

- **`ts`** — stored verbatim. It's already `2020-02-12T02:40:02Z`, which is exactly the convention `membership.added_at` uses. All app-generated timestamps (`play_import.uploaded_at`) use `datetime('now')`, matching `snapshot.pulled_at`.
- **All timestamps stay UTC.** The export carries `conn_country` but no timezone, so there is nothing to convert from. Display-zone choices belong to whatever later feature renders them.
- **`offline_ts`** — the export's `offline_timestamp` has **mixed units in the same column**: 73,656 values are seconds-scale (range `1624628769`–`1782837994`) and 852 are milliseconds-scale (range `1612808878881`–`1665439189035`). Anything treating it as one unit is wrong by 1000× on part of the data. Normalize at import:

  ```
  value < 1e11  → seconds
  value >= 1e11 → milliseconds
  ```

  then format as ISO-8601 Z to match `ts`. The threshold sits three orders of magnitude clear of both ranges. NULL (15,998 rows) stays NULL. The raw value is not stored — it's in the files on disk.

### Dedup

`row_hash TEXT NOT NULL UNIQUE`, inserted with `INSERT OR IGNORE`.

The hash is **SHA-1 of the canonical JSON of exactly these 16 source keys**, taken from the verbatim source row before filtering or normalization:

```python
KEEP = ['ts', 'ms_played', 'spotify_track_uri',
        'master_metadata_track_name', 'master_metadata_album_artist_name',
        'master_metadata_album_album_name', 'reason_start', 'reason_end',
        'shuffle', 'skipped', 'platform', 'conn_country', 'ip_addr',
        'offline', 'offline_timestamp', 'incognito_mode']

row_hash = hashlib.sha1(
    json.dumps({k: row.get(k) for k in KEEP},
               sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
```

**Hash a named key list, never the whole row.** Hashing the whole row would make every hash change the day Spotify adds a key to the export — as it once did with `audiobook_*` — re-inserting all 90k rows as duplicates. Using raw source values (not normalized ones) keeps hashes stable against our own normalization changes too.

Measured: this collapses **13** rows, all byte-identical duplicates within the export. Compare 255 lost by the roadmap's `(ts, uri)` key, 30 by `(ts, uri, ms_played)`, 22 by adding both reasons.

Cost is negligible — canonicalize + SHA-1 over all 90,662 rows is 0.55 s, on top of 0.44 s to parse the files.

## Files on disk

- Uploads live in **`data/streaming_history/<YYYYMMDD-HHMMSS>/`**, one folder per upload, named for the upload time.
- **`.gitignore` gains a single `data/` line.** `history_import.py` creates the tree with `os.makedirs(..., exist_ok=True)` on first upload.
- **Upload accepts the `.zip` only.** It is extracted and then **discarded** — only the JSON is kept.
- Extract **only** entries matching `Streaming_History_*.json`, flattened into the upload folder. Skip `__MACOSX/`, the ReadMe PDF, directory entries, and anything with a path separator or `..` in it after the leading folder is stripped.
- Nothing prunes old upload folders; that's manual (~69 MB each).

**Why a folder per upload rather than one flat folder.** The export's chunking is not stable — this one has both `2023.json` and `2023_1.json`, and a re-export can split the same years differently. Overwriting by filename would leave orphan files from the old chunking sitting beside the new ones, with no way to tell which upload a file belonged to. The `play` table would survive that (the hash dedupes), but `rows_read` and the covered date range on every `play_import` row would silently count files that weren't in that upload.

## Import pipeline

`history_import.py`, mirroring `snapshot.py`'s structure.

1. Create the `play_import` row (`kind='upload'`, `original_name` = the uploaded zip's filename, `folder` = the new folder).
2. Extract the zip into the folder, discard the zip.
3. For each `Streaming_History_*.json` in the folder, sorted by name: parse, then per row —
   - **Discard the row if `spotify_track_uri` is falsy.** That single condition covers all 311: podcast episodes (which carry `spotify_episode_uri` instead), the one all-null row, and audiobooks if they ever appear.
   - Compute `row_hash`, normalize, `INSERT OR IGNORE` with `import_id` and `source_file` (basename).
4. Commit every 5,000 rows (see *Concurrency*).
5. Update the `play_import` row with `files_parsed`, `rows_read` (rows seen, before the filter), `rows_inserted` (`total_changes` delta, so it counts only rows that actually landed), and `range_start` / `range_end` (min/max `ts` among rows read).

`rows_read` and `rows_inserted` diverging is the normal, informative case — a second import of a cumulative export reads ~90k and inserts ~0.

### Re-import

`POST /api/history/reimport` re-parses **the newest usable upload folder only**, without a new upload. "Usable" means `files_parsed > 0`: a failed upload (a corrupt zip, say) still creates its folder and its `play_import` row, and without that filter it would become the newest folder and a re-import would quietly read an empty directory instead of re-checking the last good export. Cumulative exports make this equivalent to re-reading everything, and nothing is ever deleted, so older uploads' rows are unaffected either way.

It writes a **new `play_import` row** with `kind='reimport'`, `folder` copied from the import it re-reads, and `original_name` copied too. Already-present rows keep their original `import_id`, so a clean re-import reads `rows_inserted = 0` — which is the point: a re-import is a verification that nothing changed, and a non-zero count means something did.

### Concurrency

**A snapshot pull and an import must not run at the same time**, in either direction:

- `history_import.start_*` returns `False` when `snapshot.get_status()["running"]` is true.
- `snapshot._start` returns `False` when `history_import.get_status()["running"]` is true.
- Both API routes return `409 {"error": "already_running"}`, matching `/api/snapshot/pull`.

Commit every 5,000 rows anyway. A single 90k-row transaction holds SQLite's write lock long enough to block anything else that wants it, and chunked commits keep each hold short.

### Failure

**Partial imports are kept.** On an exception mid-run: whatever committed stays, the error string lands on `play_import.error`, and the counts reflect what actually landed. A re-import then fills the rest — the hash makes it safe to re-run any number of times. Nothing is rolled back and nothing is deleted.

A failed import still leaves its `play_import` row, so the failure is visible on the page.

## Status

Module-global `_status` + `_status_lock` + `_set_status` + `get_status`, exactly as in `snapshot.py`:

```python
{
    "running": False,
    "phase": None,          # "extracting" | "parsing" | "done" | "error"
    "action": None,         # "upload" | "reimport"
    "current_file": None,
    "files_total": 0,
    "files_done": 0,
    "rows_read": 0,
    "rows_inserted": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
```

Progress is per file (13 of them) with the running row counts alongside — the whole import is a few seconds, so nothing finer is warranted.

## Endpoints

| route | |
|---|---|
| `POST /api/history/import` | multipart upload of the zip; starts the import. `409` if anything is already running. |
| `POST /api/history/reimport` | re-parse the newest upload folder. `409` likewise; `400` if there's no upload yet. |
| `GET /api/history/status` | `get_status()` plus the coverage counts below. |

`MAX_CONTENT_LENGTH = 150 * 1024 * 1024` in `config.py` (the current zip is ~66 MB). Flask raises `413` past it, which the existing `HTTPException` handler already renders as JSON for `/api/*`.

## UI — `/dev/import`

Route `/dev/import` (endpoint `dev_import`), `templates/history_import.html`, `static/js/history_import.js` (an IIFE, like every other page). Linked from `templates/dev.html` alongside Snapshot / Canonical Tracks / Artists. Not in the navbar.

Three sections:

**1. Upload.** A file input accepting `.zip`, an Upload button, and a Re-import button. Progress bar + status line + error line while running, reusing `snapshot.js`'s poller and progress-bar shape.

**2. Coverage.** Recomputed on each status poll:

| | |
|---|---|
| Total plays | `COUNT(*) FROM play` |
| Distinct played URIs | `COUNT(DISTINCT spotify_track_uri)` |
| In library / foreign | split by the `track.uri` join |
| Plays on in-library tracks | count and % of total |
| Date range | `MIN(ts)` / `MAX(ts)` |
| Library tracks never played | tracks with no `play` row |

**3. Import history.** Every `play_import` row, newest first: `uploaded_at`, `kind`, `original_name`, files parsed, rows read, rows inserted, covered range, and the error if there is one. Timestamps render through the existing `data-datetime` mechanism in `format.js`.

No delete action — pruning uploads is manual on disk.

## Out of scope
- **Any Spotify API request or write.** No new scopes.
- **ListenBrainz.** The `source` column exists and defaults to `'export'`; nothing writes `'listenbrainz'` yet. Its precedence rule (delete LB rows inside an export's covered range, then insert) belongs to that feature.
- **Charts, metrics, rankings, per-play browsing.** The coverage panel is the only readout. F/G/H own the rest.
- **Resolving foreign URIs to tracks** — that's step D, and this model needs no change to absorb it.
- **Timezone conversion for display.**
- **Pruning or managing upload folders from the UI.**
