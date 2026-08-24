# R — Scrobbling from recently-played

Step R of `docs/Planning/roadmap.md`.

Planning contradicted that step's section in three places, all of them because the endpoint was
probed rather than assumed (§1). The roadmap's budget arithmetic and its "missed windows are
accepted" both survive — the latter is now a *hard constraint* rather than a design choice.

---

## 0. What this is

Symr polls `GET /v1/me/player/recently-played` on a fixed schedule and records what comes back as
`play` rows, so the library reflects listening without waiting on a GDPR export.

**Explicitly non-authoritative.** The extended-streaming-history import stays the source of truth.
When an export lands covering a window, every scrobble in that window is **deleted** (§6) — not
kept, not merged, not flagged. A scrobble is a placeholder that the export replaces.

**It is the first thing Symr spends quota on recurring, unattended.** Everything else is attached
to a button someone pressed. That is why §4.4 (pause), §4.5 (429 back-off) and §7 (the page) exist
at all; they are not polish.

---

## 1. The probe — measured 2026-08-23, four requests

The roadmap required this before any design. All of it went into `docs/spotify_constraints.md`;
it is repeated here because three findings changed the design.

### 1.1 It works

`GET /v1/me/player/recently-played` returns **200** with `user-read-recently-played`. It does not
join `/v1/tracks?ids=` and friends in the dev-mode 403 set.

### 1.2 The track object is the *simplified* one with an album attached — not the full object

Keys returned: `album`, `artists`, `disc_number`, `duration_ms`, `explicit`, `external_urls`,
`href`, `id`, `is_local`, `name`, `preview_url`, `track_number`, `type`, `uri`.

**Absent: `external_ids` (so no ISRC), `is_playable`, `linked_from`, `popularity`,
`available_markets`.**

This is the single most consequential finding. `canonical_detect._same_recording_identity` requires
both sides to have a non-null, *equal* ISRC, so a track ingested from this endpoint can reach
version tier (title-based) but **never recording or release tier**. 13,126 of the library's 13,127
tracks have an ISRC; an ISRC-less row would be the second exception ever. §5 is the answer.

`linked_from` being absent also makes relink-aliasing moot here: the uri returned *is* what played,
so there is no requested-vs-substituted pair to record. Nothing writes `track_uri_alias`.

### 1.3 `played_at` is the **stop** time — same semantics as the export's `ts`

Measured, not assumed. Across 12 consecutive items, `played_at[i] − played_at[i+1]` equalled **the
newer** track's `duration_ms` to within 0.2s. That identity holds only for stop-stamps: if the
stamps were start times the gap would match the *older* track's duration instead.

Two consequences: the §6 supersession boundary compares like with like, and §4.3's `ms_played`
derivation is possible at all.

Format is millisecond-precision UTC — `2026-08-23T19:00:54.813Z`. The export's `ts` is
second-precision.

### 1.4 The `next` link is a lie; the endpoint serves only the last 50 plays

`next` is **always present** in the response, but following it returns **zero items**. The store is
50 deep, period.

- The roadmap's "missed windows are accepted" is therefore a **hard constraint**, not a decision
  that could be revisited. Overflow is unrecoverable by any number of requests.
- **No cursor state is needed anywhere.** The `after` cursor would only filter the same 50 items,
  so the poll is issued bare and `INSERT OR IGNORE` on `row_hash` does the deduping. Nothing is
  checkpointed, which is why a missed or crashed poll needs no recovery path.
- Overflow detection becomes exact rather than heuristic (§4.6).

### 1.5 Smaller facts

- 41 unique uris in 50 items — the same track recurs inside one window, so `played_at` is
  load-bearing in the row identity (§3.2).
- `context` is present (playlist/album/artist uri) and is **not stored**; §9.
- Every item in the probe was `type: "track"`; none `is_local`. Episodes are still filtered by
  `snapshot._usable_track`, which already rejects both.

---

## 2. Scope

**In:** the scope change; a `scrobble.py` module with a poller thread and a single-request poll; the
`scrobble_poll` log and `play.poll_id`; track ingest with the ISRC upgrade path (§5); export
supersession (§6); `/dev/scrobble` (§7); a fourth round-trip queue partition (§5.3).

**Out:** §9.

---

## 3. Schema

### 3.1 `scrobble_poll` — one row per poll, kept forever

`play_import` cannot host this. Its `kind` is `CHECK (kind IN ('upload','reimport'))` and its
`folder` is `NOT NULL`, and `folder` / `original_name` / `files_parsed` / `source_file` are all
meaningless for a poll. Widening it would mean a SQLite table rebuild to write dummies into four
dead columns of a table whose own comment calls it the per-run log of *export* imports.

```sql
CREATE TABLE IF NOT EXISTS scrobble_poll (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    items_read    INTEGER,
    rows_inserted INTEGER,
    oldest_played TEXT,
    newest_played TEXT,
    gap_warning   INTEGER NOT NULL DEFAULT 0,
    retry_after   INTEGER,
    error         TEXT
);
```

Every poll logs a row, including one that read 50 items and inserted 0 — that is how "the poller is
alive" is visible at all. ~14.4 rows/day, ~5,300/year. Nothing prunes it, like `roundtrip_run`.

`play.poll_id INTEGER REFERENCES scrobble_poll(id)` is added as an additive `_migrate` column,
NULL for every exported play, exactly as `import_id` is NULL for every scrobbled one.

### 3.2 Row identity

`play.row_hash` is `sha1` over a canonical JSON dict, matching `history_import._row_hash`'s shape
(`sort_keys=True, separators=(",",":")`) but **not** its key list — the export's 16 keys do not
exist here:

```python
{"source": "scrobble", "played_at": <verbatim ms-precision string>, "uri": <track uri>}
```

- `source` is **inside the hashed dict**, not merely the column, so a scrobble digest can never
  collide with an export digest for the same play.
- `played_at` is hashed **verbatim at millisecond precision**, before §3.3's truncation. Maximum
  precision for identity, and re-polling the same item reproduces the digest exactly, so
  `INSERT OR IGNORE` is the whole of the dedupe.

### 3.3 Column values

| Column | Value |
|---|---|
| `source` | `'scrobble'` |
| `poll_id` | the `scrobble_poll.id` |
| `import_id`, `source_file` | NULL |
| `ts` | `played_at` **truncated to seconds**, `%Y-%m-%dT%H:%M:%SZ` |
| `ms_played` | derived, §4.3 |
| `spotify_track_uri` | the item's `track.uri` |
| `reported_track_name` / `_artist_name` / `_album_name` | the item's `track.name`, its **album artist** (matching the export's meaning of that column, which is the album artist and misses featured credits), and `track.album.name` |
| `reason_start`, `reason_end`, `shuffle`, `skipped`, `platform`, `conn_country`, `ip_addr`, `offline`, `offline_ts`, `incognito_mode` | NULL — the endpoint returns none of them |

`ts` is truncated so that `MAX(ts)`, §6's comparison and every existing `ts >= ?` string comparison
in `scoring.py` / `entities.play_stats` see one format. A millisecond string sorts *before* the
second-precision string for the same second (`.` < `Z`), which is exactly the kind of silent
mis-ordering worth not having.

### 3.4 `track_isrc_absent` — §5.2's stop condition

```sql
CREATE TABLE IF NOT EXISTS track_isrc_absent (
    track_id     TEXT PRIMARY KEY REFERENCES track(track_id),
    confirmed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

`roundtrip_failed_uri` was considered and rejected for this: its column is `state` under a
`CHECK (state IN ('not_returned','dead','needs_review','load_failed'))` that SQLite cannot widen
without a table rebuild, and its key is a *uri that failed to resolve* — the opposite of this case,
which is a track that resolved perfectly and simply has no ISRC to give. This is the same
settled-exceptions shape as `reviewed_pair` and `reviewed_artist_pair`.

### 3.5 `meta` keys

| Key | Meaning |
|---|---|
| `scrobble_enabled` | `'0'` pauses the poller. **Absent means on** — a fresh deploy scrobbles with no manual step. |
| `scrobble_backoff_until` | ISO-8601 `Z`. While `now <` this, the poller logs nothing and issues no request (§4.5). |

---

## 4. The poller — `scrobble.py`

### 4.1 It starts in `serve.py`, and only there

`serve.py` is the container entrypoint; `app.py`'s `app.run()` is the laptop dev loop. Starting the
thread in `serve.py` alone makes scrobbling **production-only by construction** — no env flag, no
`meta` key, nothing to set wrong and nothing that can make the laptop spend recurring quota against
the same app-level budget as the server. It is also why §3.5's toggle living in the database is
safe: Q §7.4's backup pull copies `symr.db` to the laptop, but the laptop never reads that key for
scheduling because no thread there ever runs.

The manual **Poll now** button (§7) calls the same `poll()` and works everywhere, so the feature is
fully exercisable on the laptop without the thread.

### 4.2 Cadence

`_POLL_INTERVAL_SECONDS = 100 * 60`, a **module constant** with a warning comment, following H §10's
rule that algorithm parameters are constants rather than environment-tunable — a per-environment
interval would mean two deployments scrobbling on different, unrecorded schedules.

The roadmap's arithmetic, unchanged by the probe: 50 items × a pessimistic 2 min/track is 1h40m of
unbroken listening, so a 100-minute interval cannot overflow the window. **14.4 requests/day.**

A daemon thread polls **immediately on start**, then every interval — a restart must not create a
100-minute blind spot. It sets `api_log.api_context` to `"scrobble"` **inside the thread**, since
`api_context` is a contextvar and a value set on the main thread would not be seen there.

### 4.3 `ms_played`, derived

The endpoint returns no `ms_played`. Because §1.3 established `played_at` is a stop-stamp:

```
predecessor = played_at of the next-older item in the batch,
              else MAX(ts) FROM play WHERE ts < this item's ts,
              else None
ms_played   = duration_ms                       if predecessor is None
            = duration_ms                       if gap <= 0
            = min(gap_ms, duration_ms)          otherwise
```

`min()` is doing the real work and is why an idle break does not inflate the next track: a 10-minute
break followed by a 3-minute song gives `min(13min, 3min) = 3min`. A track paused mid-way and
resumed also clamps correctly, because it genuinely did play in full.

**The derivation is an upper bound and can never underestimate.** Its one wrong case: a track paused
part-way through and then abandoned gets credited its full duration. Narrow, and the direction the
export corrects.

The DB-predecessor fallback compares against second-precision `ts`, so the gap can be up to 1s
short at a batch boundary. Accepted; it moves `ms_played` by under half a percent of a track.

This matters because `scoring._PLAY_WEIGHT_SQL` is `min(ms_played / duration_ms, 1.0)` — storing
`duration_ms` flat would weight **every** scrobble at 1.0 and quietly overstate skipped tracks.

### 4.4 Pause

`scrobble_enabled = '0'` makes the poller return before issuing any request. Toggled from §7.

Stopping the container also stops scrobbling, but it stops the UI with it — at exactly the moment
you are worried about quota and want to look at it. This is the control that does not.

### 4.5 429 back-off

A single request does not justify `jobs.call` (which needs a `JobStatus`), so the poll is a plain
`try` / `except`. On a `SpotifyException` with `http_status == 429`, read `Retry-After` from the
exception's headers — falling back to one interval when absent — record it in
`scrobble_poll.retry_after`, and set `scrobble_backoff_until`.

Without this, an app-level 24h lockout tripped by a pull would be met with ~14 more useless requests
a day against an exhausted quota.

Other exceptions record `scrobble_poll.error` and the loop continues. A missing or scope-invalid
token (`get_spotify_client()` returning `None`) is one of these: it logs and continues rather than
killing the thread, so a server that has been redeployed but not yet re-consented (§8) recovers on
its own the moment consent lands.

### 4.6 Overflow detection

Per §1.4 the response is a bare 50-deep window, so:

> if `oldest_played` in the response is **newer** than the newest scrobble already stored, the plays
> between them were lost.

Set `scrobble_poll.gap_warning = 1`. It is a **warning and a prompt to re-import**, not an error —
the roadmap's "that is the design working, not failing". It is not raised on the first poll ever, or
on the first poll after a pause, where there is no meaningful predecessor.

### 4.7 It is not a job

The poll does not touch `jobs.py`'s slot. It is one request and one short write, not a job, and
taking the slot would make it collide with a pull for no reason. Order is request-first, write-second,
so it never holds a write transaction across a Spotify request — the rule that cost `api_log` its
rows in `_pull_liked_songs`.

Its write is **one transaction**: the `scrobble_poll` row, the `play` rows and any §5.1 track upserts
commit together. That is what makes §4.8 safe.

### 4.8 Shutdown

A daemon thread, **not** part of `jobs.drain()`. `serve.py`'s SIGTERM handler is unchanged. Killing
mid-poll loses at most that poll's rows and can never leave a partial write, because of §4.7's single
transaction — and a lost poll needs no recovery, because §1.4 means there is no cursor to resume.

### 4.9 Recompute

New plays are a scoring input. After a poll that inserted **more than zero** rows: `conn.commit()`
**then** `scoring.request_recompute()` — async per N §4.1 (there is no request to block), and in that
order, because the worker reads through its own connection. A poll that inserted nothing requests
nothing.

---

## 5. Ingest, and the ISRC upgrade path

### 5.1 Ingest what the response carries

For every item whose track passes `snapshot._usable_track`, run `snapshot._parse_track_item` and
`snapshot._upsert_track_full` — the same shared ingest path `roundtrip.py` uses, rather than a second
way to write a track.

This is what makes a foreign track have a name, artists and album the moment it is played, instead of
after the next export plus a round-trip.

Per §1.2 the resulting row has **`isrc`, `is_playable` and `linked_from` NULL**. It is otherwise
complete. Two existing behaviours make that safe rather than lossy:

- `snapshot.py`'s upsert is `isrc = COALESCE(excluded.isrc, isrc)`, with a comment already noting
  Spotify sometimes omits `external_ids`. **A scrobble can never wipe an ISRC Symr already has**, and
  the NULL fills itself in free whenever that track later arrives by any full-object path.
- `raw_json` will hold a simplified object for such a row. Any later full-object upsert replaces it.

### 5.2 The round-trip upgrades them — derived, not flagged

Leaving those rows ISRC-less forever was rejected: it is a silent, permanent data-quality
regression, and because the round-trip derives "done" from the track row merely *existing*, nothing
would ever come back for them.

`isrc IS NULL` **is** the marker, so no flag is introduced. `roundtrip.py` gains a third
`UNION ALL` arm in `_WORK_LIST_SQL`:

```sql
SELECT t.uri AS spotify_track_uri, COUNT(p.id) AS plays
FROM track t
JOIN played_uri_track x ON x.track_id = t.track_id
JOIN play p             ON p.spotify_track_uri = x.uri
WHERE t.isrc IS NULL
  AND t.uri IS NOT NULL
  AND t.track_id NOT IN (SELECT track_id FROM track_isrc_absent)
GROUP BY t.uri
```

Disjoint from the listening arm by construction: that arm is `x.track_id IS NULL` (nothing
resolves), this one is resolved-but-incomplete. The round-trip reads back **full** track objects
from the playlist-items endpoint, so one pass fills the ISRC in via the §5.1 COALESCE.

**A separate arm, deliberately, not a widening of the listening arm.** The listening arm is muted by
`roundtrip_listening_muted`, and folding these in would mean muting "plays I haven't resolved"
silently also muted "tracks missing their ISRC" — two unrelated concerns behind one switch.

**Stop condition.** After the round-trip upserts a returned track, if its `isrc` is *still* NULL,
`INSERT OR IGNORE` the track into `track_isrc_absent`. Without it the one genuinely ISRC-less track
in the library re-requests forever and the queue never reads zero.

### 5.3 The queue box becomes four rows

`roundtrip.counts()` gains `incomplete_isrc_uris`. The `/dev/roundtrip` queue box renders four
partitions — listening / album-page / album-backfill / **incomplete ISRC** — which still sum to
`remaining_uris` with no double-counting, preserving M §4.6's invariant.

The new row gets a `[Clear]` like the others. Clearing it means *stop asking about these*: it
`INSERT OR IGNORE`s every currently-matching `track_id` into `track_isrc_absent`. Unlike the album
rows this is not a free undo — there is nothing to re-add — so it reads as a settle, not a delete.

---

## 6. Supersession by the export

In `history_import._finish`, on a **successful** import (`error IS NULL`) with a non-NULL
`range_end`, before its existing `conn.commit()`:

```sql
DELETE FROM play WHERE source = 'scrobble' AND ts <= :range_end
```

`scoring.recompute` already runs on that path, so the deletion is reflected without a new call.

**Why deleting is the right answer, not merely the cheap one.** A scrobble row is deliberately
low-fidelity: a derived `ms_played`, and NULL for `reason_start`, `reason_end`, `skipped`, `shuffle`,
`platform` and the rest. Keeping it beside the export's real row is not neutral — it double-counts
the play *and* feeds `scoring._PLAY_WEIGHT_SQL` a number Symr invented while the true one sits in the
next column. Nothing downstream changes: every existing reader of `play` stays correct with no view,
no filter and no touching of `scoring.py`'s hot query.

It also makes §7's page simpler. With superseded scrobbles gone there is no overlap band: the cutover
is exactly `MAX(ts) WHERE source = 'export'`, and everything after it is a scrobble.

**Accepted risk, stated rather than discovered later:** the predicate uses the import's `range_end`,
and a *range* is not the same as *coverage*. An export with an internal gap would delete scrobbles it
does not actually replace. Q §7.2's nightly backups with 30-day retention are the recovery path.
A second, narrower case: a play that recently-played reported but the export omits entirely is lost
with the same delete.

`scrobble_poll` rows are never deleted — the log outlives the plays it recorded, like `roundtrip_run`.

---

## 7. `/dev/scrobble`

Its own page, linked from the `/dev` landing list beside Round-trip and Play History. Read path is
`scrobble.index_data(conn)` returning exactly the template's kwargs, per P3's rule that a dev page's
logic lives in the module that owns its data and the route is a 404-free
`render_template(..., **data)`.

**Status** — enabled or paused; the interval; last poll (when, items read, rows inserted, error,
`gap_warning`); approximate next poll; total `source='scrobble'` rows; count of polls with
`gap_warning` set.

**Controls** — `POST /api/scrobble/poll` (Poll now) and `POST /api/scrobble/toggle` (Pause/Resume).
Both return JSON and update the page in place via `static/js/scrobble.js`, no reload.

**The last 50 plays**, `ORDER BY ts DESC LIMIT 50`, **regardless of source**, each linked through the
`entity_link` macro and timestamped through `datetime_span`. Each row shows its source, and the page
draws an explicit divider at the export/scrobble cutover so it is visible where live data stops and
imported data begins.

Any count that can exceed 999 renders with thousands separators in **both** halves — `{{ "{:,}" }}`
server-side and `toLocaleString()` in the JS that updates it live.

**Note the side effect on every other page.** `entities.play_stats` computes
`data_through = MAX(ts) FROM play`; once scrobbling runs that is always ~now, so the "—" staleness
rendering disappears and the 7/30-day windows start showing real numbers everywhere. That is the
feature working — a genuine zero now reads as zero instead of as "the export is old" — and it
correctly reverts to "—" if scrobbling is paused long enough.

---

## 8. The scope change and deployment

`config.SPOTIFY_SCOPES` gains **`user-read-recently-played`**, a read-only scope. It remains the case
that `playlist-modify-private` is the only write scope and `roundtrip.py` its only user.

spotipy's `SpotifyOAuth.validate_token` rejects a cached token whose scope is not a superset of the
requested one, so **nothing needs deleting**: adding the scope makes both caches re-auth by
themselves. Two consents are owed, one per token cache:

- **Laptop** — the next `venv/bin/python app.py` redirects to Spotify; consent at
  `http://127.0.0.1:45660/callback`.
- **Server** — after the next `deploy/deploy.sh`, consent at
  `https://fe-pro.tail78f5ec.ts.net/callback` from a device already on the tailnet. This is an
  interactive browser step on an otherwise headless box and it must be recorded in
  `deploy/deploy.sh`'s output or notes, because the app is simply logged out until it happens
  (§4.5 makes the poller wait rather than die).

**`fe-pro` is offline 2026-08-23 → ~2026-09-05.** This is a deploy-time concern only. Everything in
this spec is written, implemented, tested and verified on the laptop; the feature is complete on
merge and merely not *always-on* until the server is back and re-consented. Nothing here is blocked
by the outage, and the poller is production-only (§4.1) so the laptop never scrobbles in the interim.

---

## 9. Out of scope

- **Recovering an overflowed window.** §1.4 proves it is impossible, not merely unbudgeted.
- **`context`** (the playlist/album/artist a play came from). The endpoint returns it and Symr
  discards it; it is not in the export, so a column carrying it for scrobbles only would be populated
  for a shrinking sliver of `play` that §6 then deletes.
- **Spending `GET /v1/tracks/{id}` per new track to get the ISRC directly.** That is unbounded
  recurring spend, exactly what R is meant not to be; §5.2 gets the same result on a button press.
- **A budget or "remaining today" figure.** That is step O, still gated on the `api_request` log
  catching a real lockout. R's spend is visible through the existing `/dev` line and through
  `api_context = 'scrobble'` in the log.
- **Backfilling plays from before the first poll.** The endpoint has no history to give.

---

## Tests

Two rules this feature **changes**, named because a fixture written against the old one would agree
with a broken implementation and pass:

- **`_WORK_LIST_SQL`'s "done"** was *the uri resolves through `played_uri_track`*. It is now *…and
  the resolved track has an ISRC, or is in `track_isrc_absent`*. A test built only from unresolved
  uris cannot distinguish the two rules.
- **`play.source` was always `'export'`.** Any assertion counting rows in `play` now has to say which
  source it means, or it passes whichever way supersession is implemented.

1. **`ms_played` derivation.** Back-to-back items → the gap. **Idle-then-play → clamped to
   `duration_ms`** — this is the case a naive `gap` implementation gets wrong, and a fixture of only
   back-to-back items cannot tell `gap` from `min(gap, duration)`. Skipped track → the gap.
   Predecessor found in the DB rather than the batch. No predecessor at all → `duration_ms`.
   Non-positive gap → `duration_ms`.
2. **Row identity.** Feeding the same response twice inserts once. Two plays of the same uri at
   different `played_at` insert twice (§1.5's 41-of-50). A scrobble digest differs from
   `history_import._row_hash` for a row describing the same play.
3. **`ts` is stored second-precision** while the hash uses the verbatim millisecond string — assert
   both, since storing the truncated value in the hash would still dedupe and still pass a test that
   only checks the column.
4. **Supersession.** An import deletes scrobbles at or before `range_end` **and leaves later ones
   intact** — a test asserting only deletion cannot tell `ts <= range_end` from `DELETE ALL`. It does
   not run when the import recorded an error, and does not run when `range_end` is NULL. Exported
   rows are never deleted.
5. **Round-trip arm 3.** A NULL-ISRC track with a play appears in the work list; the same track once
   in `track_isrc_absent` does not; a track *with* an ISRC never does; an unresolved uri still appears
   via the listening arm. The four partitions still sum to `remaining_uris` (M §4.6's invariant,
   extended). Muting the listening arm leaves arm 3 present — the whole reason it is a separate arm.
6. **The stop condition fires**: a round-trip that returns a track still lacking an ISRC writes
   `track_isrc_absent`; one that fills the ISRC in does not.
7. **Ingest.** A recently-played item upserts a track with `isrc` NULL but name, album, artists and
   `duration_ms` populated. A subsequent full-object upsert fills the ISRC **without wiping** any
   other column — the COALESCE rule, which a test using only a NULL-to-NULL upsert would not exercise.
8. **The poller skips rather than requests** when `scrobble_enabled = '0'`, and when
   `scrobble_backoff_until` is in the future. Assert the fake `sp` was **never called** — asserting
   "no rows inserted" cannot distinguish skipping from polling and receiving nothing.
9. **429 sets `scrobble_backoff_until`** from `Retry-After`, and falls back to one interval when the
   header is absent. A non-429 exception records `scrobble_poll.error` and does **not** set backoff.
10. **`gap_warning`** is set when the oldest item returned is newer than the newest stored scrobble,
    and not set when the windows overlap, and not set on the first poll ever.
11. **A poll row is written even when it inserts nothing** — that is the liveness signal, and it is
    the row most likely to be optimised away.
12. **`request_recompute` is called only when rows were inserted** (spy on it, as `conftest.py`
    already stubs it), and after the commit.
13. **The poll is not a job**: it does not set `jobs._active`, and it succeeds while a job holds the
    slot.
14. **Routes.** `/dev/scrobble` and both `/api/scrobble/*` endpoints join `tests/routes_catalog.py`,
    with a semantic assertion beside the sweep for the page (P2-010: a catalog entry alone only proves
    it responds). `/api/scrobble/toggle` flips the `meta` key in both directions.

`tests/fakes.py` gains `current_user_recently_played` — its **eleventh** endpoint. It must return a
`next` link whose follow-up yields **zero items**, mirroring §1.4: a fake that pages properly would
let an implementation that tries to page pass.
