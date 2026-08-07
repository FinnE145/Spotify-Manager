# Foreign-track round-trip (step D)

Step D of `docs/Planning/listening_data_roadmap.md`. Turns the played-but-unknown
Spotify URIs in `play` into real `track` rows with full metadata, for ~124 requests
instead of 6,085, by pushing them through a scratch playlist and reading the full
track objects back out of the playlist-items endpoint.

**This is the project's first write to the Spotify library.** Everything below is
built around that: the write is confined to one playlist, guarded before it happens,
and never touches anything else.

---

## Request budget — read this first

The app is in Spotify dev mode with no extended-quota grant. Quota exhaustion is
real and returns a `Retry-After` in the **tens of thousands of seconds** (~24h) —
see `docs/spotify_constraints.md`. A full run is ~124 requests and we do not know
how close that is to the ceiling.

Therefore, binding on the implement session:

- **Spend no requests on testing.** Nothing in this feature may be exercised
  against the live API to "check it works" — not a single add, not a single
  read-back, not a probe of the loader playlist. Build it, verify it by reading
  the code and by whatever can be checked without the network.
- **Finn presses the button.** The first and every real run is started by Finn,
  deliberately, at a time he chooses. Do not start a run to demonstrate anything.
- The public `open.spotify.com` probe (below) is **not** the Web API and costs no
  quota — that one is free to exercise.
- Every run's request count is recorded so we learn where the ceiling actually is
  (§7). That log is the only way we find out, so it must be written before the
  run can die.

Last full pull was 2026-08-03, so D is not sharing a day with a pull.

## Current measurements (2026-08-06)

| | |
|---|---:|
| Tracks in library | 3,620 |
| Play rows | 90,338 |
| Distinct foreign URIs (no `track` row) | **6,085** |
| Plays on foreign URIs | 13,947 |
| Batches of 100 | 61 |
| Requests: 1 guard + 61 add + 61 read + 1 clear | **124** |

Re-derive the foreign count at run time; don't hardcode it.

---

## 1. Prerequisites (already done)

- `config.py` requests `playlist-modify-private`; `.spotipy_cache` was deleted and
  re-authed on 2026-08-06. The cached token carries all four scopes. **No further
  re-auth is needed.**
- The scratch playlist exists, is private, and is owned by Finn:
  **`<Play History Loader>`** (angle brackets are part of the name), id
  `3Sr9aUZKYT8DDmWdcRQh00`.

The id is **hardcoded** in this step, as a module constant with a
`TODO: replace with a UI field` comment. Replacing it with an identify-in-the-UI
field is deliberately deferred to the next full pull, when the playlist will show
up in `snapshot` and can be picked from a list. Store the **id only** — the `si`
and `pt` parameters on a Spotify share link are account-scoped tokens and must
never enter the repo.

---

## 2. `jobs.py` — one job slot, one lock

D is the third long-running background job, and the existing mutual exclusion does
not survive a third. Build this **first**, port the two existing jobs onto it, then
build D on top.

### The bug being fixed

`snapshot._start` and `history_import._start` each check the *other* module's
status with no lock held, then take their own lock:

```
snapshot._start:  reads history status → not running
history._start:   reads snapshot status → not running
snapshot._start:  takes its lock, sets running = True
history._start:   takes its lock, sets running = True   ← both running
```

The existing comments are right that holding one lock while waiting on the other
would deadlock — but two locks cannot enforce one shared invariant. There is also
a second, competing notion of "is anything running" in `history_import.busy()`,
used only to reject an upload body early, which can disagree with the start path.

### The replacement

One module-level lock and one `_active` job name in `jobs.py`. Claiming the slot is
a single atomic check-and-set under that one lock, so there is no lock ordering,
no deadlock, and no race:

```python
_lock = threading.Lock()
_active = None          # None | "snapshot" | "history_import" | "roundtrip"
_stop_requested = False

def try_start(name, target, *args) -> bool
def active() -> str | None
def request_stop() -> bool      # asks the active job to wind up
def stop_requested() -> bool    # polled by the job between units of work
def now_iso() -> str
```

`try_start` claims the slot, spawns the daemon thread, and clears the slot in a
`finally` inside the wrapper so a crashing job can never wedge the app. It also
clears `_stop_requested`, so a stop from a previous run can't kill the next one
before it starts.

**Stopping is cooperative, never a kill.** `request_stop()` sets a flag; the job
checks `stop_requested()` at its own safe points and exits through its normal
"stopped early" path with everything committed. Nothing is interrupted mid-batch
and no thread is ever killed — a forced stop mid-write is exactly how a half-written
batch would end up misattributed. The mechanism lives in `jobs.py` so it's generic,
but **only the round-trip wires it up** in this step; snapshot and import can adopt
it later with a one-line check, and doing that is not in scope here.

`jobs.py` also owns a small `JobStatus` class, killing the copy-pasted
`_status_lock` / `_set_status` / `get_status` / `_reset_status` / `_now_iso` in both
existing modules:

- `JobStatus(name, **default_fields)` — per-job progress fields stay per-job
  (playlists vs files vs batches genuinely differ).
- `.set(**kw)`, `.reset(**kw)`, `.get()` — all lock-guarded.
- `.log(message)` — appends `{ts, message}` to a **capped event log** (last 200
  entries, oldest dropped), cleared by `.reset()` and returned by `.get()`. This is
  the live feed described in §6.2; it lives in `JobStatus` rather than the
  round-trip so the other two jobs can adopt it for free.
- `.get()` derives `running` from `jobs.active() == name` rather than storing it,
  so there is exactly one source of truth for whether a job is live. The terminal
  fields (`phase`, `finished_at`, `error`, `action`) stay in the dict so a page
  that reloads after a run still renders its outcome.

### Ports

- `snapshot.py`: `_start` becomes `jobs.try_start("snapshot", …)`; drop
  `_status_lock`, `_now_iso`, and the `history_import` import-inside-function.
  `_status` becomes a `JobStatus`. `_count_request` and `_record_failure` use it.
- `history_import.py`: same; `busy()` becomes `jobs.active() is not None` (keep the
  function — the early upload rejection is still worth having).
- Neither module's public API changes shape: `get_status()` keeps returning a plain
  dict with the same keys, so `snapshot.js` / `history_import.js` need no changes.

### Also in this pass

`db.connect()` gains a **30-second busy timeout**
(`sqlite3.connect(DB_PATH, timeout=30)`). WAL is already on, so readers never
block, but WAL still serializes *writers*: a background job holding a write
transaction plus any request that writes (exclude toggle, canonical merge) races
sqlite3's 5s default and raises `database is locked`. One line, and the number of
write paths only grows.

Leave `_artist_display`'s per-connection cache alone — it is safe as long as
nothing builds a long-lived connection on it, which nothing here does.

---

## 3. Schema

```sql
-- A played uri that Spotify relinked onto a different track. We ask for X and
-- get back track Y with linked_from.id = X; Y's object is complete, X's is a
-- stub carrying only id/uri/type/href/external_urls -- no name, artists, album
-- or duration -- so X can never become a track row of its own. Many requested
-- uris can collapse onto one track, which is why this is a table and not a
-- column on track. track.linked_from / linked_from_id still record what the
-- returned track itself was linked from.
CREATE TABLE IF NOT EXISTS track_uri_alias (
    requested_uri TEXT PRIMARY KEY,
    track_id      TEXT NOT NULL REFERENCES track(track_id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_track_uri_alias_track ON track_uri_alias(track_id);

-- Uris a run could not resolve, so a later run doesn't retry them forever and
-- stall on the same batch. Clearable from the page (§6) when a failure looks
-- transient.
CREATE TABLE IF NOT EXISTS roundtrip_failed_uri (
    requested_uri TEXT PRIMARY KEY,
    reason        TEXT,
    failed_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- track.uri had no index; every play-resolution query probes it now.
CREATE INDEX IF NOT EXISTS idx_track_uri ON track(uri);
```

And a view, so no read path ever hand-rolls the alias union:

```sql
CREATE VIEW played_uri_track AS
SELECT uri, track_id FROM track WHERE uri IS NOT NULL
UNION ALL
SELECT requested_uri, track_id FROM track_uri_alias;
```

`VIEWS` is hash-rebuilt (`_ensure_views`), so adding this takes effect on restart
with no migration. **Every** `play` → `track` resolution moves onto this view,
including `history_import.coverage_counts`.

---

## 4. The run

### 4.1 Guard (1 request)

`sp.playlist(LOADER_ID)`. Refuse to continue, with a clear error and **zero
writes**, unless:

- the name is exactly `<Play History Loader>`, and
- the owner id equals the current user's id.

This is the whole insurance policy against the one thing that must never happen —
a bug pointing the clear-playlist call at a real playlist. It costs one request per
run and is not optional.

On success, upsert a `snapshot` row for the playlist with `excluded = 1`, so a
later full pull never reads it and it never contributes `membership` rows. Record
the playlist's current item count as the **read offset base** (a resumed run finds
leftovers from the previous one).

### 4.2 Work list

```sql
SELECT p.spotify_track_uri, COUNT(*) AS plays
FROM play p
LEFT JOIN played_uri_track x ON x.uri = p.spotify_track_uri
WHERE x.track_id IS NULL
  AND p.spotify_track_uri NOT IN (SELECT requested_uri FROM roundtrip_failed_uri)
GROUP BY p.spotify_track_uri
ORDER BY plays DESC, p.spotify_track_uri ASC
```

**Ordered by play count, descending** — if the run dies at 30%, that 30% is the
most-played third, not a random one. `uri ASC` is the tie-break so the order is
deterministic across runs. The playlist is filled in exactly this order, so its
contents visibly show how far the run got.

This query is also what makes the run **resumable with no run-state to go stale**:
"done" is derived — a uri is done when it resolves through `played_uri_track`, which
covers both a direct track row and a relink alias. A re-run simply recomputes and
finds less to do.

### 4.3 Per batch of 100

1. **Add** — `sp.playlist_add_items(LOADER_ID, uris)` (1 request). 100 is the API
   maximum. 6,085 items is far under the 10,000-item playlist cap.
2. **Read back** — `sp.playlist_items(LOADER_ID, offset=base, limit=100)`
   (1 request). Adds append, so the batch lands at `base … base+99`.
3. **Map positionally.** The k-th returned item is the k-th requested uri. This is
   what "map on the requested uri, not the returned id" means in practice.
   **If `len(returned) != len(requested)`, do not trust positions** — record the
   whole batch in `roundtrip_failed_uri` with that reason and move on. Silent
   misattribution is the one failure mode worth being paranoid about.
4. **Store.** `_upsert_track` from `snapshot.py`, unchanged, so the round-trip
   fills `track` / `album` / `artist` / `track_artist` / `album_artist` exactly as
   a pull does. **Write no `membership` rows** — these tracks are in no playlist of
   Finn's, and the loader is a scratch buffer, not a library playlist.
5. **Alias.** For each pair, if `returned.uri != requested_uri`, insert
   `track_uri_alias(requested_uri → returned.id)`. That covers relinks and any
   other substitution without special-casing `linked_from`.
6. **Commit**, then advance `base` and the progress counters. Committing per batch
   is what makes a quota death cost nothing already earned.

### 4.4 Clear (1 request)

On a run that completes every batch: `sp.playlist_replace_items(LOADER_ID, [])` —
one request, clears the playlist whatever its size. This is why the design records
and clears once rather than deleting per batch: per-batch removal is a third
request per batch (183 total vs 124) and buys nothing, because durability comes
from committing each batch to SQLite, not from removing it from Spotify.

**On a run that stopped early, do not clear.** A quota stop has no requests left to
spend anyway, and the leftover items are the visible record of how far it got. The
page reports the count and Finn can clear it by hand in the Spotify client
(select all → remove) if he wants to. If the clear itself fails, that is a one-click
manual fix and explicitly not worth code.

---

## 5. Failure handling

**Quota / 429.** `_call()`'s existing `RateLimited` fail-fast applies: stop the run
immediately, never sleep on a multi-hour `Retry-After`. Record `retry_at`, the
batch reached, and the request count. Everything committed stays.

**A batch rejected with 400** — one dead uri poisons the whole add. Rather than
skipping 100 tracks to lose 1, or bisecting via the API (which spends the quota the
run is trying to protect):

1. Probe each of the 100 at `https://open.spotify.com/track/<id>` — the **public
   web page, not the Web API, costing no quota**. Verified 2026-08-06: a live track
   returns **200**, a non-existent id returns **404**. Status code only; no HTML
   parsing — use a `HEAD` if it returns the same codes, else `GET` with the body
   discarded.
2. **5 concurrent probes, no artificial delay between them** — roughly 10/s, so a
   bad batch costs ~10 seconds, not 100. Use an honest user-agent identifying Symr
   and a short per-request timeout (5s) so one hanging probe can't stall the pool.
   `open.spotify.com/robots.txt` allows `/track/` under `User-agent: *` (only
   `/local/`, `/download/`, `/embed/` are disallowed), and ~100 requests on an
   occasional failure is negligible traffic for a CDN-backed web frontend. Treat
   any non-200/404 (timeout, 5xx, 429) as **inconclusive → keep the uri**; only a
   definite 404 drops it.
3. Drop the 404s into `roundtrip_failed_uri` and **retry the batch once** with the
   survivors (1 request).
4. If the retry still fails, record all remaining uris of that batch in
   `roundtrip_failed_uri` and continue with the next batch.

> **Caveat, deliberately carried:** the probe was verified against *fabricated*
> ids, not a genuinely withdrawn track. A delisted track may well still render a
> page and return 200. So the probe is best-effort narrowing, and step 4 is the
> real backstop — do not assume the probe always finds the culprit.

A 20-URI sample resolved 20/20 during planning, so all of this is expected to be
rare.

### Why the probe stays inline, not backgrounded

Running the probe asynchronously while the main loop continues would break the
run's central invariant. The read-back maps requested uri → returned track
**positionally** (§4.3), which only holds because adds append in a known order at a
known offset. Overlapping a batch's repair with the next batch's add interleaves
writes to the same playlist, and the offsets stop being predictable — which turns a
recoverable failure into silent misattribution, the one outcome worth being
paranoid about. At ~10 seconds the pause isn't worth that risk.

### Circuit breaker

**Three consecutive failed batches stop the run.** Without this, a systemic
fault — a bad token, a scope revocation, a malformed request — would fail all 61
batches and fire 6,100 public probes at the web frontend for nothing. The count
resets on any successful batch, so scattered dead uris never trip it. The run ends
in the normal stopped-early state with the reason logged.

**Anything else** — the usual `except` → `phase="error"`, message in the status,
committed work kept.

---

## 6. The page — `/dev/roundtrip`

Follows the `snapshot.html` pattern; no new UI vocabulary.

### 6.1 Controls and counts

- **Header** with a one-line description, plus a link **"Upload new plays"** →
  `/dev/import`. The description must be explicit that this page, unlike every
  other one, **does write to Spotify** — adding to and clearing the
  `<Play History Loader>` scratch playlist and nothing else.
- **Counts:** foreign uris remaining, tracks already resolved by round-trip,
  aliases recorded, known-failed uris, plus the batch total the next run would do
  and its request estimate (`2 × batches + 2`).
- **Start button** — one action, no options. Disabled while any job is active,
  with the active job named.
- **Stop button** — enabled only while the round-trip is running. Calls
  `jobs.request_stop()`; the run finishes its current batch, commits, skips the
  clear (§4.4), and ends in the stopped-early state. The button switches to a
  "stopping…" state immediately so it's obvious the request landed, since the
  actual stop waits for the batch boundary.
- **Live status** while running: phase, batch *n* of *m*, uris done, **requests
  issued this run**, current activity, and errors.
- **Terminal state:** completed vs stopped-early, request count, `retry_at` when
  rate-limited, how many items were left in the loader playlist if it wasn't
  cleared, and the list of newly-failed uris (with their `open.spotify.com` links,
  so checking one is a click).
- **Clear failures** action — empties `roundtrip_failed_uri` so a later run retries
  them, for when a failure looked transient. No confirmation needed; it only
  re-opens work.
- **Run history** — one row per run (see §7).

### 6.2 The live event feed

A scrolling, timestamped feed of what the run is doing, rendered from
`JobStatus.log` and updated by the same poller as the progress bar. This is the
primary reason the design can stay simple: **with the run observable and stoppable,
it does not need clever self-defending retry logic** — anything going systematically
wrong is visible within a batch or two and Finn stops it by hand.

It must log at least:

- run start, with the batch count and request estimate
- the guard result (playlist name and owner as verified)
- each batch: `batch 12/61 — added 100, stored 97, aliased 3`
- **every failure, loudly**: a batch 400, how many probes came back 404, whether
  the retry succeeded, a count mismatch on read-back, the consecutive-failure
  count as it climbs toward the circuit breaker
- rate limiting, with the `retry_at`
- stop requested / stopping at batch boundary
- run end: completed vs stopped, totals, requests spent, items left in the playlist

Keep entries to one short line each — this is a monitoring feed, not a debug log.
The feed persists after the run finishes (it is only cleared by the next
`.reset()`), so a run that ended while Finn was away can still be read afterwards.

`/dev/import`'s header gains the reciprocal link **"Add new imports to db"** →
`/dev/roundtrip`, appended to the existing `<p class="meta">` at
[history_import.html:9-12](templates/history_import.html:9), right after
"…no Spotify requests, no writes to the library." `/dev`'s landing page gets a
tile for the new page.

Static JS: `static/js/roundtrip.js`, an IIFE like the others, polling the status
endpoint.

Endpoints: `POST /api/roundtrip/start`, `POST /api/roundtrip/stop`,
`GET /api/roundtrip/status`, `POST /api/roundtrip/clear-failures`. `/api/*` error
shape as established in `app.py`.

`/api/roundtrip/status` returns the event log alongside the progress fields. It is
polled on the same interval `snapshot.js` uses; the log is capped at 200 entries so
the payload stays small, and the poller replaces the rendered feed wholesale rather
than diffing it.

---

## 7. Run log

A `roundtrip_run` table, mirroring `play_import`'s role — one row per run, kept
even when the run failed, because **the request count is the only way we learn
where the quota ceiling is**:

```sql
CREATE TABLE IF NOT EXISTS roundtrip_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at     TEXT,
    uris_attempted  INTEGER,
    tracks_stored   INTEGER,
    aliases_created INTEGER,
    uris_failed     INTEGER,
    requests        INTEGER,
    left_in_playlist INTEGER,
    -- How the run ended. 'stopped' is a deliberate user stop, not a fault, and
    -- must not render as an error; 'breaker' is the consecutive-failure trip.
    outcome         TEXT CHECK (outcome IN
                        ('running', 'completed', 'stopped', 'rate_limited',
                         'breaker', 'error')),
    error           TEXT
);
```

The row is inserted at run start and updated **after each batch commit**, so a
process that dies mid-run still leaves an accurate request count behind.

---

## 8. What this changes elsewhere

**"Foreign" stops meaning "no track row."** `docs/specs/play-history-C.md` defines
foreign as the absence of a `track` row, which is exactly what D eliminates. Left
alone, `/dev/import` would report ~100% coverage and 0 foreign the day D runs —
true under the old definition, meaningless under any useful one.

`coverage_counts` therefore splits its single "in library" number into two:

- **Known to Symr** — the played uri resolves through `played_uri_track`. After D
  this approaches 100% by construction, and stops being an interesting number.
- **In your library** — it resolves to a track with at least one `membership` row.
  This is the number that actually means something, and D does not change it.

`tracks_never_played` must likewise become "library tracks never played" — counting
`track` rows with a membership — or the ~6,000 round-tripped tracks make it
nonsense.

**Detection and grouping.** The library roughly triples, so `canonical_detect`'s
candidate volume is unknown until measured. That is step **E**, deliberately
unplanned until after D. Nothing here runs detection.

**Relinking is not a grouping signal.** The roadmap describes a relink as a
high-confidence release-tier grouping gift. Checking the mechanics, it isn't: the
second id never becomes a `track` row, so there is no pair of rows to group, and
`track_uri_alias` absorbs the relationship completely. What relinking *does* give
is better — when a foreign uri relinks onto a track already in the library, a
"foreign" uri turns out to be an owned track, its plays join straight onto it, and
the 6,085 shrinks by an amount we can't predict. See §9.

**Artist duplicates.** ~6,000 new tracks will re-open the `/dev/artists` candidate
queue and invalidate the page-cost timings measured at 3,611 tracks. Both are
expected consequences to be measured after the run, not work for this step.

---

## 9. Doc changes this feature must make

Not optional extras — these are part of the work, because each one is currently
wrong or will become wrong the moment D runs.

1. **`docs/Planning/listening_data_roadmap.md`, the D section** — replace the
   "**Relinking** … Handled, it's a **gift**" paragraph. The claim that a relink is
   a higher-confidence release-tier grouping signal than any heuristic in
   `detection.md` is not correct: the relinked-from id never becomes a `track` row,
   so there is no pair to group and nothing for E to inherit. Replace it with what
   relinking actually buys (the alias model, and foreign uris resolving onto tracks
   already owned), and keep the warning that unhandled relinking corrupts
   attribution silently — that part is right.
2. **`docs/Planning/listening_data_roadmap.md`, the order line** — mark A, I and C
   as landed and D as in progress, so the roadmap stops reading as if nothing has
   been built.
3. **`docs/specs/play-history-C.md`** — annotate the two places that define foreign
   as "no `track` row" (the data-model note and the closing scope line) with a
   pointer to §8 here. C's definition was correct when written; D is what makes it
   stale, so D is what should say so.
4. **`docs/spotify_constraints.md`** — add what this feature establishes:
   `playlist-modify-private` is now granted; add/replace item limits (100 per
   request, `PUT` with an empty `uris` array clears a playlist of any size in one
   request); relinking behaviour and the `linked_from` stub's contents; and that
   `open.spotify.com/track/<id>` returns 200/404 and is not the Web API, with the
   robots.txt position.
5. **`CLAUDE.md`'s Codebase Map** — entries for `jobs.py`, `roundtrip.py`,
   `templates/roundtrip.html`, `static/js/roundtrip.js`, and the note that
   `snapshot.py` / `history_import.py` no longer own their own job locks.

## 10. Out of scope

- Any UI for identifying the playlist (hardcoded id + `TODO` until the next pull).
- Rerunning detection or grouping over the new tracks — step E.
- ListenBrainz, generations, scoring — steps B/H.
- Removing individual tracks from the loader playlist.
- Deleting or creating the loader playlist from code. It is created and, if ever
  needed, deleted by hand.
