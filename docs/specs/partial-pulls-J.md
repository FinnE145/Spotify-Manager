# J — Partial / resumable pulls, and the API request log

**Step J of `docs/Planning/roadmap.md`.** Read that step's section first; §0 below records
everywhere planning contradicted it.

---

## 0. What planning changed

### 0.1 J's stated premise is not yet true

The roadmap opens: *"The library only grows, so eventually a full pull will not fit inside
one day's budget at all."* Measured 2026-08-15, a full pull costs **232 requests** — 4
playlist-list pages + 224 item pages across 145 pullable playlists + 4 for Liked Songs
(`GET /me` plus 3 pages). That is **the same 232 July measured**, even though the library
went 3,611 → 11,418 tracks, because the round-trip's new tracks carry no playlist
memberships. Live memberships only moved 12,513 → 12,688.

So the growth J anticipates has not arrived, and J is not urgent for the reason it says it
is. It is worth doing now for a different reason, found during planning:

### 0.2 An aborted pull is worse than useless today — it poisons the refresh

`_sync_playlists_and_get_targets` writes and **commits** every playlist's fresh
`snapshot_id` (`snapshot.py:264,268`) *before* any item read. `tracks_pulled_at` is only
set on a **successful** item read (`snapshot.py:740`). **Nothing ever compares the two.**

So after a run that dies at playlist 100 of 145, the 45 it never reached hold a fresh
`snapshot_id` with stale contents. The next refresh computes
`stored["snapshot_id"] != p["snapshot_id"]` → `False`, skips them, and pulls nothing. They
stay stale until Finn happens to edit them on Spotify's side, or until a forced full pull.

This has not bitten yet — all 8 never-item-pulled playlists in the live DB are excluded (the
7 known 403s plus `<Play History Loader>`), so there are no silent orphans right now. But it
is the mechanism that makes an abort destructive rather than merely wasteful, and **it is the
same missing fact resumability needs**: nothing records which `snapshot_id` the stored items
actually came from. Fixing resumability fixes this for free; they are not two changes.

### 0.3 No request budget yet — the log comes first

The roadmap asks *"whether it stops on a request budget rather than waiting to be
rate-limited"*. **Decided: no budget.** There is no data to set one from — Symr has never
recorded what it spent, when, or what the response was, so any number would be invented.
A pull keeps running until Spotify 429s it, exactly as today.

Instead J adds what makes a budget definable later: **an API request log** that records every
outbound Spotify request. Once it has caught a real lockout, the ceiling can be read off it
rather than guessed — including whether the quota window is even 24 hours, which is currently
an assumption. The budget itself, and surfacing it on the pages that spend requests, is
**new roadmap step O**, added in this branch's second commit.

The log was not in the roadmap's J section at all; it is folded in on Finn's instruction and
is roughly half this spec.

### 0.4 The other four open questions, settled

The roadmap lists five things "the spec session has to decide". §0.3 covers the budget one.

- **How progress is recorded** — *derived*, per D's precedent, not an explicit cursor. One
  new column plus one `meta` key (§2).
- **Ordering** — never-captured playlists first, then by `all_time` score descending (§2.5).
- **Manual or automatic resume** — manual, and with **no new button**: under the derived rule
  the existing Refresh and Full pull buttons *are* the resume (§2.4).
- **What the UI shows** — a stale count on the existing status line, and an end-of-run line
  that says what was captured rather than implying a total loss (§5.1).

---

## 1. Scope

**In:** the playlist pull (`snapshot._run_pull`) and the API request log.

**Out:** everything else. `roundtrip.py`, `backfill.py` and `snapshot._run_backfill` already
derive their work lists (`_WORK_LIST_SQL`, `backfill._derive()`, `raw_json IS NULL`) and
resume by simply being re-run. The playlist pull is the only job that starts over.

`roundtrip_run` is **not** made redundant by the request log and is not touched: it carries
`outcome`, `uris_attempted`, `tracks_stored`, `aliases_created`, `uris_failed` and
`left_in_playlist`, and only its `requests` column overlaps.

---

## 2. Resumability

### 2.1 The missing fact

Add one column:

```sql
ALTER TABLE snapshot ADD COLUMN tracks_pulled_snapshot_id TEXT;
```

**The `snapshot_id` in effect when this playlist's item read last succeeded.** `snapshot_id`
keeps its existing job (Spotify's change token, refreshed for every playlist on every run);
it stops doubling as the "have we read the items" marker, which is the whole of §0.2.

It is set in `_apply_playlist_items`, self-referentially, so no value has to be threaded
through:

```sql
UPDATE snapshot SET tracks_pulled_at = ?, tracks_pulled_snapshot_id = snapshot_id,
                    track_count = ?, last_changed_at = ?, last_pull_error = NULL
 WHERE playlist_id = ?
```

Because `_sync_playlists_and_get_targets` has already stored this run's `snapshot_id` by the
time any item read happens, this records the list-read value — which is the correct one, and
the same value the refresh rule will compare against next run.

### 2.2 The two work-list rules

Both are derived. Nothing is checkpointed, so nothing can go stale.

A playlist is a target when it is **not excluded**, is present in this run's playlist-list
response, and:

| | rule |
|---|---|
| **Refresh** | `tracks_pulled_snapshot_id IS NULL OR tracks_pulled_snapshot_id != snapshot_id` |
| **Full pull** | the refresh rule, **OR** `tracks_pulled_at IS NULL OR tracks_pulled_at < pull_force_epoch` |

Compare in Python, not SQL — `NULL != NULL` is `NULL`, and both columns are nullable.

### 2.3 Why a full pull needs the epoch

A forced pull's entire point is to re-read playlists whose `snapshot_id` has *not* changed
(A's re-pull is why the button exists). After an abort, those playlists satisfy
`tracks_pulled_snapshot_id == snapshot_id` and look done under the refresh rule alone. So a
forced pull additionally needs to know "done *for this pull*".

`meta.pull_force_epoch` holds the ISO timestamp the current forced pull started. A playlist
is done for it when `tracks_pulled_at >= pull_force_epoch`. One `meta` key and one existing
column — still derived, still no cursor table.

### 2.4 Resume is the same button

- **Refresh** re-derives its rule and pulls whatever is still stale. A refresh *is* the
  resume of a refresh.
- **Full pull** resumes the current epoch if it still has unfinished targets, and starts a
  **new** epoch (writing `pull_force_epoch = now`) only when the previous one is complete or
  absent.

No Resume button, no new endpoint. The consequence worth naming: while a forced pull is
incomplete, clicking Full pull will **not** force-re-read the playlists it already captured
in that epoch. That is the desired behaviour and the only thing the epoch exists to express.

### 2.5 Ordering

Materialized **once**, at the start of the run, into the existing `targets` list
(`snapshot.py:133`) — so nothing can reorder mid-run, whatever the key. On a resume the order
is recomputed, but the finished playlists have dropped out of the work list, so it only ever
decides which of the *remaining* ones goes next. Ordering instability is therefore a
non-issue, and score ordering is safe.

1. **Never-captured first** (`tracks_pulled_at IS NULL`).
2. Then by `scoring.playlist_scores(conn, ids)[pid]["all_time"]`, **descending**.

A captured playlist with no scored versions falls out as `0.0` (`combine([])` returns `0.0`,
`_display(0.0)` is `0.0`), so it sorts last among captured with no special handling.

The same ordering applies to refresh and full pull — one code path. One `playlist_scores()`
call over ~145 ids per run; it is a single read of the materialized version tier plus a
Python `combine()` each, and is not on any page load.

### 2.6 A failed playlist stays in the work list

Under the new rule a playlist whose item read failed keeps its old
`tracks_pulled_snapshot_id`, so **every subsequent run retries it**. That is a deliberate
behaviour change from today, where the fresh `snapshot_id` silently suppressed the retry.

It is correct — a transient failure should be retried — and it costs nothing in practice: the
7 known permanent 403s are already excluded, so they never enter the work list at all. A new
persistent failure gets retried until Finn excludes it by hand, which is the existing
workflow (`/dev/snapshot`'s exclude toggle and the post-pull bulk-exclude button).

### 2.7 Liked Songs stays the tail step

Unchanged: `_pull_liked_songs` runs after the playlist loop (`snapshot.py:152`). It is not
part of the ordered work list.

This is coherent rather than an omission — Liked Songs is not a real playlist and has **no
`snapshot_id`**, so it cannot participate in the derived rule at all. The consequence is
explicit: **a pull that stops early never reaches Liked Songs.** At 4 requests out of 232 and
106 tracks, that is accepted.

### 2.8 `last_full_pull_at` / `last_refresh_at`

**No change to when they are written.** Both are already set after the loop
(`snapshot.py:167`), so a run that aborts or is stopped simply never reaches them, and the
page keeps showing the older, honest date. They mean "a pull ran to completion", which
includes runs where individual playlists failed — those are reported separately by the
failing count and `last_pull_error`, and must not block the timestamp, or a permanent 403
would freeze it forever.

### 2.9 Migration

Backfill the new column once, in `db._migrate`:

```sql
UPDATE snapshot SET tracks_pulled_snapshot_id = snapshot_id
 WHERE tracks_pulled_at IS NOT NULL AND tracks_pulled_snapshot_id IS NULL;
```

Without this, the first refresh after J ships would treat all 145 playlists as stale and
spend ~228 requests re-reading a current library.

This asserts exactly what today's refresh logic already believes — that a stored
`snapshot_id` matching Spotify's means the stored items are current — so it is no less
accurate than the status quo, and introduces no new assumption.

### 2.10 A playlist is the atom of resumption

**Not a simplification — the only safe unit.** `_diff_playlist_tracks` reconciles stored
memberships against the *complete* item list; a partially-fetched playlist would present as a
mass removal and write `removed_at` across every track past the last fetched page.

Cost of the decision: an abort loses at most the current playlist's pages — worst case 24
(Finn All, 2,347 tracks). Only 6 playlists exceed 200 tracks; 109 of 145 are under 100 and
cost a single page each.

Intra-playlist resume is explicitly **not** in scope, now or later, unless
`_diff_playlist_tracks` is redesigned first.

---

## 3. Stopping

### 3.1 Rate limiting — behaviour unchanged, wording changed

`RateLimited` still aborts the run (`snapshot.py:141`), still rolls back, still recomputes
scores, still records `retry_at`. No state-machine change.

What changes is that the page must not imply the run was a wash. The end-of-run line reports
what was captured and what remains — see §5.1.

### 3.2 Stop button

New `POST /api/snapshot/stop` → `jobs.request_stop("snapshot")`, and a **Stop** button on
`/dev/snapshot` beside the three existing controls, enabled only while a run is live.

The poll goes at the natural safe point in `_run_pull`: **after the playlist's commit, before
the next playlist's fetch.** A stop ends the run with `phase="stopped"` — *not* `"error"` —
and `finished_at` set, leaving everything committed. The same poll is added to
`_run_backfill`'s per-track loop, because the button lives on the page and must do something
during a backfill run too.

Cooperative only, never a kill — `jobs.request_stop`'s existing contract.

---

## 4. The API request log

Every outbound Spotify request gets a row: what it was, when, and how it came back. Headless
for now; the only surface J ships is one line on `/dev` (§5.2).

### 4.1 Hook point: the session, not the callers

`api_log.LoggingSession(requests.Session)` overrides `request()`, times the call, and records
the outcome. `spotify_client.py` uses it in both places it builds a session.

This is chosen over wrapping `jobs.call` because it catches **everything**, including the
entity pages' `entities.fetch_album_tracklist` / `fetch_artist_image`, which never go through
a job. Every request counts against the quota, so every request is logged.

**Two separate instances, not one shared session:**

- The API client's session keeps its existing custom `Retry` adapter (GET-only retries,
  `respect_retry_after_header=False`) — `spotify_client.py:40-60` is unchanged except for the
  class it instantiates.
- `SpotifyOAuth` accepts a `requests_session` parameter (verified against the installed
  spotipy), so `get_auth_manager()` passes a second `LoggingSession` with **default**
  adapters. Sharing one session would silently apply the GET-only retry policy to the token
  refresh POSTs, changing auth behaviour to get logging — not worth it.

The `open.spotify.com` probe in `roundtrip.py` uses plain `requests` and is **not** logged:
it is the public web frontend, not the Web API, and costs no quota.

`jobs.JobStatus.count_request()` and the per-run request counter stay exactly as they are.
They answer "what is this run spending right now"; the log answers "what has been spent, in
total, over time". Both are wanted.

### 4.2 Schema

In `db.py`'s `SCHEMA`, like every other table:

```sql
CREATE TABLE IF NOT EXISTS api_request (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    host           TEXT NOT NULL,
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    query          TEXT,
    status         INTEGER,
    duration_ms    INTEGER,
    response_bytes INTEGER,
    retry_after    INTEGER,
    context        TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_request_ts ON api_request(ts);
```

- **`host`** is what separates quota-counting requests (`api.spotify.com`) from the OAuth
  token refreshes (`accounts.spotify.com`). Both are logged; the rolling counts filter on it.
- **`retry_after`** gets its own column rather than living in a headers blob — reading the
  ceiling off the 429s is the entire point of building this.
- **`error`** is set with `status` NULL when the request raises before a response (timeout,
  connection error). Those cost no quota but you want to see them.

### 4.3 The context label

`contextvars.ContextVar("api_context", default=None)`, read by the hook, set in two places:

- `jobs.try_start`'s `run()` wrapper → the job name (`snapshot`, `roundtrip`,
  `history_import`, `backfill`).
- A new `before_request` in `app.py` → `request.endpoint`.

It must be a **contextvar, not a module global**: a pull's background thread and a page-load
thread run concurrently, and a global would let one overwrite the other's label, attributing
requests to the wrong source. Each thread gets its own context by construction.

**The new hook runs first**, ahead of `require_login` — which itself calls
`get_spotify_client()` on every request and can trigger a token-refresh POST. `app.py` goes
from two app-wide `before_request` hooks to three; the ordering comment there needs updating,
as does `CLAUDE.md`'s map at Verify time.

### 4.4 Write path

`api_log.record(...)` opens a connection via `db.connect()`, inserts one row, commits, and
closes.

A connection per write rather than a shared module-level one: it needs no
`check_same_thread=False`, no lock, no lifetime management, and reuses existing plumbing.
The cost is a fraction of a millisecond against an HTTP request of 100ms+, and Verify should
measure it rather than assume.

Contention is not a concern: WAL is on, every connection carries a 30-second busy timeout
(`db.py:583`), and **no Spotify request is ever issued while a job holds the write lock** —
the pull fetches all of a playlist's pages first, then applies and commits, and the round-trip
and both backfills have the same shape. The worst realistic overlap is an async
`scoring.recompute()` (~1.8s) while an entity page's detail fetch returns.

**A logging failure must never break the request it is logging.** The whole `record()` call
is wrapped and swallows every exception.

### 4.5 Known undercount, accepted

urllib3 retries 5xx *below* `Session.request()`, inside the adapter, so a request retried
three times there logs as **one row with the final status**. The log undercounts by exactly
that much.

429s are unaffected — they are not in `status_forcelist`, so urllib3 does not retry them, and
`jobs.call`'s own 429 retry re-enters `Session.request()` and correctly produces two rows.
Catching adapter-level retries would need a urllib3-level hook; not worth it for a bounded
undercount on a rare path.

### 4.6 Security

CLAUDE.md's one exception to KISS applies here.

- **No headers are ever read or stored**, in either direction. The bearer token lives in the
  outbound `Authorization` header and nothing else in the request carries it. The one header
  value that *is* read is `Retry-After`, by name, into its own integer column.
- **No response body is ever stored** — only `len(response.content)` as an integer. This
  matters most for the token-refresh responses, whose bodies contain the access and refresh
  tokens.
- **Request bodies are never stored.** The OAuth token exchange POSTs the authorization code
  in its body; it is not logged. (`/callback`'s own inbound URL carries that code as a query
  param, but the hook only sees *outbound* requests and never touches Flask's request object.)
- Spotify Web API URLs carry no secrets — `query` holds `limit`/`offset`/`market` and the
  like — so logging path and query is safe.

### 4.7 Retention

**Keep forever. No pruning, no cap.**

An `api_request` row is ~125 B of data, ~180 B on disk with row overhead and the `ts` index.
A full pull is 232 rows ≈ 42 KB. At heavy use (~500 requests/day) a year is ~33 MB; at a
realistic average, where most days are zero, a year is nearer **7 MB** — about a third of the
`album` table, against a 93 MB database already dominated by `play` (22.7 MB) and `track`
(21.6 MB). If it ever becomes the largest table, a one-line `DELETE` fixes it.

---

## 5. UI

### 5.1 `/dev/snapshot`

`snapshot.summary_counts()` gains **`playlists_stale`** — playlists that are not excluded and
match the refresh rule of §2.2, i.e. the size of the work list a Refresh would build.

- The status line gains it: `89 / 145 pulled · 8 excluded · 56 stale`.
- `playlists_stale` is added to the polled status payload alongside the existing fields, so
  it ticks down live.
- The end-of-run line must say what was captured, not just that something failed.
  Rate-limited: **"Rate limited — 89 of 145 captured, 56 still stale. Resume after 14:20."**
  Stopped: **"Stopped — 89 of 145 captured, 56 still stale."** `retry_at` renders through the
  existing `data-datetime` / `format.js` path.
- A **Stop** button (§3.2) beside Full pull / Refresh / Backfill.

Per-run request count already exists (`snapshot.html:38`) and is unchanged.

### 5.2 The `/dev` row

One plain line above the list of links in `dev.html` — **not** a panel, box or section:

```
Requests: 232 in 24h · 1,847 in 7d
```

Quota-counting requests only (`WHERE host = 'api.spotify.com'`). Server-rendered on page
load; `/dev` has no JS today and gains none. Two `COUNT(*)`s against the `ts` index.

**No "remaining today" figure** — there is no known budget to subtract from. That is step O.

### 5.3 No request-log viewing page

Deliberate. The log is headless in J. Its counts surface as §5.2's row and nothing else.

---

## 6. Out of scope — step O

Added to the roadmap in this branch's second commit. Gated on **data, not code**: the log has
to catch a real lockout before a ceiling can be named.

- The "remaining today" figure on §5.2's row.
- Budget/cost estimates on the pages that spend requests — `/dev/snapshot`, `/dev/roundtrip`,
  the album backfill.
- Establishing what the quota window actually is. "24 hours" is an inference from one
  observed `Retry-After`, not a documented fact.

---

## 7. Files touched

| file | change |
|---|---|
| `db.py` | `api_request` table + index in `SCHEMA`; `tracks_pulled_snapshot_id` migration (§2.9) |
| `api_log.py` | **new** — `LoggingSession`, `record()`, the rolling-count reads, the contextvar |
| `spotify_client.py` | `LoggingSession` in both session builds; `requests_session=` on `SpotifyOAuth` |
| `snapshot.py` | derived work list (§2.2), force epoch, ordering, stop poll, `playlists_stale` |
| `jobs.py` | set the context label in `try_start`'s `run()` wrapper |
| `app.py` | context `before_request` (first); `POST /api/snapshot/stop`; `playlists_stale` in the status payload; the `/dev` counts |
| `templates/snapshot.html` | stale count, Stop button, end-of-run wording |
| `templates/dev.html` | the requests row |
| `static/js/snapshot.js` | Stop control, stale count, end-of-run wording |

---

## 8. Verification

1. **Resume.** Start a full pull, Stop it partway. Confirm the captured playlists have
   `tracks_pulled_snapshot_id = snapshot_id`, the rest do not, and clicking Full pull again
   resumes at the right place rather than starting over.
2. **§0.2 is fixed.** After that partial pull, run a Refresh and confirm it targets the
   playlists the aborted run never reached — the case that silently pulls nothing today.
3. **Migration — check this by query *before* running a Refresh, not by running one.**
   Getting §2.9 wrong means the first Refresh treats all 145 playlists as stale and spends
   ~228 requests re-reading a current library, and there is no way to take that back. So
   after the migration runs, count the work list offline:

   ```sql
   SELECT COUNT(*) FROM snapshot
    WHERE excluded = 0
      AND (tracks_pulled_snapshot_id IS NULL OR tracks_pulled_snapshot_id != snapshot_id);
   ```

   Expect ~0. If it comes back near 145, the migration did not apply — stop and fix it
   before touching any pull control.
4. **Ordering.** Confirm never-captured sort first and the rest descend by `all_time` score.
5. **Log completeness.** After a run, `SELECT COUNT(*) FROM api_request` for that window
   should match the run's own request counter (modulo §4.5's 5xx retries).
6. **Log coverage off the job path.** Load an album page not yet detail-fetched and confirm
   its single request is logged with the right `context`.
7. **Token refreshes** land with `host = 'accounts.spotify.com'` and are excluded from the
   `/dev` row.
8. **No secrets.** Inspect real rows and confirm no header, body or token value is present.
9. **Write cost.** Measure `record()` and confirm it is negligible against the HTTP call.
10. **`/dev` row** renders correct counts.

**Do not** test rejection paths by poking live job endpoints on the running app — a start
endpoint starts something. Port 45660 is not negotiable; if it is occupied, ask.
