# Track Metadata Capture — Feature Spec

**Roadmap step A.** Status: **ready to implement**. This spec is the standalone implementation prompt — an implement session can start from just this file. Follow `/symr-implement`: ask live for anything this doesn't decide.

> **Branch:** this work lives on `feat/track-metadata-A`. Check with `git branch --show-current`.

## Read first
- `CLAUDE.md` — conventions, KISS, the no-assumptions rule.
- `docs/Planning/roadmap.md` — step A in context, and why I / C / D follow it.
- `docs/spotify_constraints.md` — especially *Enrichment endpoints — all 403* and *Dead track-object fields*. Those two sections are the entire justification for this feature.
- Existing code: `snapshot.py` (`_parse_track_item`, `_upsert_track`, `_apply_playlist_items`, `_sync_playlists_and_get_targets`, `summary_counts`), `db.py` (`SCHEMA`, `_migrate`), `app.py` (`/dev/snapshot*` routes, `/api/snapshot/*`), `templates/snapshot.html`, `templates/snapshot_playlist.html`, `static/js/snapshot.js`.
- `scripts/backfill_track_details.py` — the existing standalone-script idiom (argument parsing, its own DB connection, commit-as-you-go) for the migration script to follow.

## What this is

Read-only. The one metadata pass that has to be right, because **there is no second source**.

Every Spotify enrichment endpoint 403s for this app — `/v1/tracks?ids=`, `/v1/artists`, `/v1/albums`, `/v1/audio-features`, `/v1/artists/{id}/related-artists`. Only `GET /v1/tracks/{id}` and `GET /v1/playlists/{id}/items` work. **The track object is therefore the complete and final universe of Spotify metadata Symr can ever hold.** There is no follow-up pull to plan for, because there is nothing left to fetch.

So this feature captures the track object **whole** — verbatim raw JSON alongside a properly normalized artist/album model — and then does one full re-pull to apply it to the existing 3,589 tracks. It also adds a playlist exclude flag so the 7 playlists that permanently 403 on item reads stop costing requests.

**Nothing here writes to Spotify.** No new scopes.

## Data model

Additive tables in `SCHEMA`; `track` is rebuilt (see *Migration*).

```sql
CREATE TABLE IF NOT EXISTS artist (
    artist_id    TEXT PRIMARY KEY,
    name         TEXT,
    external_url TEXT,
    raw_json     TEXT
);

CREATE TABLE IF NOT EXISTS album (
    album_id               TEXT PRIMARY KEY,
    name                   TEXT,
    album_type             TEXT,
    release_date           TEXT,
    release_date_precision TEXT,
    release_year           INTEGER,
    release_date_sortable  TEXT,
    total_tracks           INTEGER,
    image_url              TEXT,
    external_url           TEXT,
    raw_json               TEXT
);

CREATE TABLE IF NOT EXISTS track_artist (
    track_id  TEXT NOT NULL REFERENCES track(track_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (track_id, artist_id)
);

CREATE TABLE IF NOT EXISTS album_artist (
    album_id  TEXT NOT NULL REFERENCES album(album_id),
    artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    position  INTEGER NOT NULL,
    PRIMARY KEY (album_id, artist_id)
);

CREATE INDEX IF NOT EXISTS idx_track_artist_artist ON track_artist(artist_id);
CREATE INDEX IF NOT EXISTS idx_album_artist_artist ON album_artist(artist_id);
```

**`track`**, final shape after the rebuild:

```sql
CREATE TABLE IF NOT EXISTS track (
    track_id       TEXT PRIMARY KEY,
    name           TEXT,
    artists        TEXT,     -- denormalized comma-joined display string, kept
    album_id       TEXT REFERENCES album(album_id),
    duration_ms    INTEGER,
    explicit       INTEGER,
    external_url   TEXT,
    uri            TEXT,
    isrc           TEXT,
    track_number   INTEGER,
    disc_number    INTEGER,
    is_playable    INTEGER,
    linked_from    TEXT,     -- verbatim JSON sub-object; NULL when not relinked
    linked_from_id TEXT,     -- parsed id, for indexed lookup; deliberately no FK
    raw_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_track_album ON track(album_id);
CREATE INDEX IF NOT EXISTS idx_track_linked_from ON track(linked_from_id);
```

**Gone from `track`:** `popularity` and `preview_url` (both permanently dead — `popularity` is NULL for all 3,589 rows, `preview_url` isn't even a key in the response), and `album_name` / `album_image_url` (now on `album`, reached by join).

### Notes on the model

- **No credit flag.** Track credits live in `track_artist`, album credits in `album_artist`. A **featured artist** is then "in `track_artist` for track T, not in `album_artist` for T's album" — structural, not string-matched. Album credits are stored once per album (~2,411 rows) rather than restated on every track of that album.
- **`position`** is 0-based within each credit list, in the order Spotify returns them (index 0 = primary artist).
- **`linked_from_id` gets no foreign key.** It holds the id that was *requested*, which by definition is not the one that came back — there is usually no `track` row for it. It duplicates a value already inside `linked_from`'s JSON; that's accepted, because the alternative is an unindexable `json_extract` on every lookup. In step A this column will be almost entirely NULL — relinking surfaces when you request specific ids, which is step D. It exists now so D has somewhere to put it.
- **`artist` has nothing else to hold.** The nested artist object is only `{external_urls, href, id, name, type, uri}`, and `href`/`uri`/`external_urls` are all the id in different wrappers. Artist images, genres, followers and popularity come from `/v1/artists`, which 403s. `raw_json` is carried anyway for consistency.
- **Never `SELECT *` on `track` or `album`.** Both carry raw JSON blobs (~1.7 KB per track). Every query names its columns. This spec converts the two existing `SELECT *` call sites as part of the work.

## Parsing

`_parse_track_item` in `snapshot.py` grows to return the album and artist structures alongside the track fields. Raw JSON is `json.dumps(obj, separators=(",", ":"))` — the track object for `track.raw_json`, the nested `album` object for `album.raw_json`, the simplified artist object for `artist.raw_json`.

**Store the track object only, not the playlist-item wrapper.** The wrapper's fields (`added_at`, `added_by`, `is_local`) are membership-scoped and already live on `membership`; the track object is the only track-scoped thing in there. That keeps last-write-wins coherent when the same track appears in many playlists.

**Overwrite `raw_json` on every pull** — last write wins, no version history.

Derived album fields, computed at parse time:
- `release_year` = `int(release_date[:4])` when `release_date` is present, else NULL. Works at all three precisions.
- `release_date_sortable` = `release_date` padded to a full date: precision `year` → `YYYY-01-01`, `month` → `YYYY-MM-01`, `day` → unchanged. Keep `release_date_precision` alongside so it's always visible when the day is fabricated.

`image_url` on `album` uses the existing `_album_image_url()` helper (300px, with its current fallbacks).

### Upsert order

`PRAGMA foreign_keys = ON` is set, so within `_apply_playlist_items`:

1. Upsert `artist` rows — from `track.artists` **and** `track.album.artists` (same object shape; upsert by `artist_id`, last write wins).
2. Upsert the `album` row.
3. Upsert the `track` row.
4. Replace `track_artist` for that track, and `album_artist` for that album — `DELETE` then `INSERT`, so a changed credit list doesn't leave orphans.

Keep the existing `COALESCE` guard on `isrc` (Spotify sometimes omits `external_ids`, and a NULL from one pull must not wipe a value already held). Apply the same guard to `album.image_url`.

## Migration — ✅ ALREADY APPLIED

**This is done. `symr.db` is already on the new schema** — applied 2026-08-03 during the plan session, so the implement session starts against a correct database. Backup at `symr.db.bak-20260803-152314`.

> ### ⚠️ Do not re-run the migration
> `scripts/migrate_track_metadata.py` is committed as the record of what happened. It guards on `track.raw_json` already existing and will print "already applied" and exit — but there is no reason to invoke it at all.

### The DB as it stands right now

| | |
|---|---|
| `track` | 3,589 rows, rebuilt into its final 15-column shape |
| `album` | **2,411 rows**, backfilled — `album_id`, `name`, `image_url` populated; every other column NULL until the re-pull |
| `artist`, `track_artist`, `album_artist` | created, **0 rows** — artist ids were never stored, so these only fill on the re-pull |
| `snapshot.excluded` | added, all 0 |
| grouping data | untouched — 13,952 `canonical_group`, 3,589 `track_group`, 461 `reviewed_pair` |

Verified after the run: `PRAGMA integrity_check` = ok, `PRAGMA foreign_key_check` = empty, 0 orphan `album_id`, 0 albums missing a name or image, 0 tracks missing `isrc`, 0 malformed `uri`.

> ### ⚠️ The app is currently broken, by design
> The DB moved ahead of the code, so right now `_upsert_track` writes four columns that no longer exist and `canonical_detect.py:86` selects `t.album_name`. **Making the code match this schema is the implement session's job** — that's the work, not a bug to report. Order is **write code → start app → press Full pull**.

### Two things implement must still do to `db.py`

1. **Update `SCHEMA`** to declare the four new tables and `track`'s new shape, so a *fresh* database is created correct. `SCHEMA` currently still describes the old `track`.
2. **Leave `_migrate` alone.** Nothing destructive belongs behind an app start; the existing additive migrations there stay as they are.

### What the script did, for the record

1. Guarded on `track.raw_json` already existing, so a second run is a no-op.
2. Checkpointed the WAL, then copied `symr.db` to `symr.db.bak-<timestamp>`.
3. `PRAGMA foreign_keys=OFF` — **before** `BEGIN`; the pragma is a no-op inside a transaction.
4. `BEGIN`, then: created the four new tables; `ALTER TABLE snapshot ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0`; backfilled `album` from `track`'s album columns (`GROUP BY album_id`, not `DISTINCT`, so one row per album regardless of drift in the denormalized values); rebuilt `track` via create-temp / copy / drop / rename, computing `uri` as `'spotify:track:' || track_id`; recreated the indexes.
5. `PRAGMA foreign_key_check`, rolling back on any violation.
6. `COMMIT`, then `PRAGMA foreign_keys=ON`.

**Steps 3–6 are SQLite's documented table-rebuild procedure and the order matters.** `membership` and `track_group` both carry `REFERENCES track(track_id)`; doing the drop/rename with foreign keys enforced risks mangling those references. The script opens its own connection rather than `db.connect()`, which forces the pragma on.

**The `album` backfill had to precede the `track` rebuild.** Otherwise there's a window where `album` is empty, so every album name in the UI is blank and `canonical_detect`'s `album_norm` is the empty string — silently degrading detection until the full pull lands.

**One trap worth keeping.** The migration cannot use `conn.executescript()` inside the transaction: it issues an implicit `COMMIT` before it runs, which silently ends the transaction and leaves a failure half-applied. Statements are executed individually for that reason. This was caught by dry-running on a copy, and a fault-injection run confirmed a mid-migration failure rolls back to a completely untouched DB.

## Query-site changes

Four sites read the dropped columns. Each joins `album` and **aliases the result back to the old name**, so every template and `canonical_review.js` keeps working unchanged:

```sql
LEFT JOIN album a ON a.album_id = t.album_id
-- a.name AS album_name, a.image_url AS album_image_url
```

`LEFT JOIN`, so a track with a NULL `album_id` still appears.

- `canonical_detect.py:86` — the detection input query. **Mechanical swap only; no matching logic changes here.** `album_norm` must compute the same string it does today. (Reworking detection onto the artist model is roadmap step **I**, deliberately separate.)
- `app.py:306` — playlist detail rows.
- `app.py:323` — `SELECT * FROM track`; convert to named columns plus the join.
- `canonical.py:334` (`track_display`) — `SELECT * FROM track`; convert to named columns plus the join. Its `dict(row)` result feeds `canonical.html` and `canonical_review.js`, so the aliases must match today's key names exactly.

## Playlist exclude flag

`snapshot.excluded INTEGER NOT NULL DEFAULT 0` (additive `ALTER TABLE`).

**Semantics: skip item reads, nothing else.** An excluded playlist still has its playlist-level metadata refreshed by the 3-request list pass (name, `snapshot_id`, image, description, `track_count`) — it's only `GET /playlists/{id}/items` that fails and only that which is skipped. **Existing membership rows are never touched**; exclusion means "don't re-read", never "forget".

- `_sync_playlists_and_get_targets` filters excluded playlists out of `targets`, in both the full-pull and refresh paths.
- `_pull_liked_songs` is skipped when `__liked__` is excluded. `__liked__` is excludable like any other row — it still holds tracks unique to it.
- `last_pull_error` is **not** cleared on exclusion; the reason stays readable and the counts below depend on it.
- Nothing auto-clears the flag and nothing re-tests an excluded playlist — by definition it isn't read. To re-test, untoggle and pull.
- Excluded playlists still get their Canvas card (unchanged behaviour).

### Counts

`summary_counts` gains `playlists_excluded` and redefines the failure count:

| | |
|---|---|
| **total** | all `snapshot` rows (currently 150, `__liked__` included) |
| **pulled** | `tracks_pulled_at IS NOT NULL AND excluded = 0` |
| **excluded** | `excluded = 1` |
| **failing** | `last_pull_error IS NOT NULL AND excluded = 0` |

Status header renders `143 / 150 pulled · 7 excluded`, with **failing** surfaced only when > 0. That's the point of the split: once the known 7 are excluded, any non-zero failing count is a *new* problem.

### UI

- **Toggle in both places** — a control per row in the playlist table on `/dev/snapshot`, and one in the header of `/dev/snapshot/playlist/<id>`. The toggle is itself the visual indicator; no separate column needed.
- **Bulk button** — "Exclude the N playlists that failed" appears **only at the end of a pull, in the existing failure list** rendered by `showDone()` in [static/js/snapshot.js:118](../../static/js/snapshot.js). No failures, no button. Confirm before applying, then reload.
  To make it target exactly what's listed, add `playlist_id` to the entries `_record_failure` pushes onto `_status["failed_playlists"]`.

### Endpoint

`POST /api/snapshot/exclude` — body `{"playlist_ids": [...], "excluded": true|false}`. One endpoint serves both the single toggle and the bulk button.

## The re-pull

Not automated. Last step of the sequence — code lands, Finn starts the app, then **he presses Full pull.** ~225 requests, a few minutes. This is what populates `artist` / `track_artist` / `album_artist` and every `raw_json`, `release_date`, `track_number` etc. that the migration left NULL.

Per project memory: **never write a `.py` file while a pull is running** — the Flask reloader truncates it silently.

**No mop-up pass is needed.** Measured against the live DB: exactly **1** track in the whole library is unreachable by a full pull (it survives only in a `removed_at` membership). The 7 playlists that 403 never contributed tracks in the first place, and nothing is unfollowed.

`scripts/backfill_track_details.py` is **left alone**. It reads and writes `album_image_url` and will therefore break if it's ever run again; it was a one-time mop-up whose remaining backlog is that single track. If a future run surfaces the breakage, fix it then.

No coverage UI. After the pull, coverage gets reported in chat.

## Out of scope
- Any **write** to Spotify. No new scopes.
- Reworking `canonical_detect.py` onto the artist model — roadmap step **I**, the next step after this one.
- Play-history ingestion (C), the foreign-track round-trip (D), grouping catch-up (E), generations (B), scoring (H).
- Genre and artist imagery — both need a non-Spotify source (ISRC → MusicBrainz), deferred.
- Any UI for browsing artists or albums as entities. This step builds the model; nothing reads it yet beyond the aliased album joins.
