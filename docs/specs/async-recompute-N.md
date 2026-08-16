# Async score recompute — step N

**Step N of `docs/Planning/roadmap.md`.**

Stop blocking a request on `scoring.recompute()`. The math is untouched; only *when* and *on
whose thread* it runs changes.

---

## 0. What planning changed from the roadmap section

N's roadmap section poses three questions and one of them turned out to be a dead end.

- **"Drop the explicit call and trust the read-time backstop" does not work.** `ensure_fresh()`
  runs in an app-wide `before_request` hook, so it fires on *every* authenticated request —
  including the next apply and the queue refetch the review UI is already awaiting
  (`static/js/canonical_review.js`, the `api(queueUrl())` call after a successful apply). Removing
  the explicit call moves the 1.8s from the end of one request to the front of the next one in
  the same interaction. Same felt delay. A background worker is the only option that helps, so
  §3 builds one and the backstop question becomes a separate decision (§5).

- **"Just the two canonical-queue endpoints, or `/dev/artists` too?" is settled by a rule, not a
  list** (§4.1). Five of the nine request-path call sites go async; four stay synchronous.

- **Recompute *cannot* live in `jobs.py`, and this is a hard fact rather than a preference.**
  The roadmap notes that "recompute isn't a `jobs.py` job today, so none of that module's
  single-job-slot machinery covers it" and leaves the door open. It has to stay shut: the three
  jobs that recompute do so **while holding the slot**, so routing recompute through
  `jobs.try_start()` would make every one of those closing recomputes fail to claim it.
  `JobStatus`'s progress fields, 200-entry event log and cooperative stop are all wrong-shaped
  for a ~1.8s uninterruptible pass besides.

**Verified during planning, and load-bearing for §4 and §5:** *nothing in the codebase writes a
decision derived from a score.* The complete reader set is `app.py` (page rendering and list
ordering), `artists.py` (candidate-list display and ordering) and `canonical_detect.py`'s
`_order()` (review-queue ranking). `canonical_autogroup`'s rule is ISRC + normalized title +
duration and never touches a score. So a stale `score` table can only ever show a number that is
~1.8s out of date or rank a list slightly wrong — it cannot cause a wrong durable decision
anywhere.

---

## 1. The problem

`scoring.recompute()` costs **1.75–1.80s** on the current library (measured 2026-08-15 during
verify, over four consecutive runs; a cold first run costs ~2.5s) and is called synchronously at
**20** call sites (`docs/specs/scoring-H.md` §9.2 is the design this implements against). Nine
are in a request path, eleven are inside background jobs where 1.8s is invisible.

The felt cost is the grouping review queue: `/api/canonical/apply` and
`/api/canonical/cross/apply` are awaited by the UI before it advances, so every Enter/Next pays
the full recompute.

The concrete worst case of a stale table is not a wrong number but a **missing** one: a merge
creates a new group id, and a stale `score` table has no row for it, so for ~1.8s that group
renders as absent and sorts to the bottom. In the review queue that decides only which item you
see first, in a queue you work to exhaustion.

---

## 2. What does not change

- `recompute()`'s body, its inputs, every scoring constant, the `score` table, and both horizons.
  N is scheduling, not scoring.
- The eleven call sites inside `snapshot.py` (6), `roundtrip.py` (3) and `history_import.py` (2).
  They stay synchronous and are not edited. `backfill.py` has **none** — it writes only
  `wanted_uri`, which is not a scoring input and is not in `_FINGERPRINT_TABLES`.
- `jobs.py`. Not edited at all.
- Nothing becomes incremental. The roadmap's reasoning stands: shrinkage pulls each version toward
  its bucket's **median** input, and a median does not update incrementally.

---

## 3. The recompute worker

New, in `scoring.py`, below the existing recompute section.

### 3.1 Public API

```python
def request_recompute():
```

Marks a recompute as wanted and returns immediately. Callers never wait, never get a result, and
never see an exception from the recompute itself.

### 3.2 State

Module-level, beside the existing `_status_lock` / `_backstop_lock` pair:

```python
_worker_lock = threading.Lock()
_worker_pending = False   # a recompute has been asked for and not yet started
_worker_alive = False     # a worker thread exists right now
```

### 3.3 `request_recompute()`

```python
def request_recompute():
    global _worker_pending, _worker_alive
    with _worker_lock:
        _worker_pending = True
        if _worker_alive:
            return
        _worker_alive = True
    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception:
        with _worker_lock:
            _worker_alive = False
        raise
```

`_worker_alive` is set **before** the thread is spawned, deliberately: it closes the window in
which `ensure_fresh()` (§5) could see "no worker running" and act on a change the worker is about
to cover.

That ordering is also why the `start()` failure has to be caught. The flag is only ever cleared by
the worker itself, so a thread that never ran leaves it raised forever — see §3.4 for what that
costs.

### 3.4 The loop

```python
def _worker():
    global _worker_pending, _worker_alive
    try:
        conn = db.connect()
        try:
            while True:
                with _worker_lock:
                    if not _worker_pending or jobs.active():
                        return
                    _worker_pending = False
                try:
                    recompute(conn)
                except Exception:
                    # recompute() has already recorded the failure via
                    # _record_recompute() and armed _failed_fingerprint (§6.2).
                    # Stopping here rather than looping is what stops a
                    # deterministic failure spinning.
                    return
        finally:
            conn.close()
    finally:
        with _worker_lock:
            _worker_pending = False
            _worker_alive = False
```

**This is the coalescing.** Commits landing during a 1.8s pass all set the same flag, and are
absorbed by one extra pass — so holding Enter through ten queue items costs two or three
recomputes, not ten.

**The outer `finally` is why the flag is cleared in exactly one place.** Clearing it at each
`return` — as this section originally specced — leaves two paths uncovered: `db.connect()` and
`jobs.active()` both sit outside any handler, and either raising strands `_worker_alive` at `True`.
That is the one unrecoverable state this module has, and it is entirely silent:
`request_recompute()` would never spawn again, `ensure_fresh()` (§5.2) would defer forever, and the
§7.1 banner would never appear, because `recompute()` never ran to record an error. Scoring would
just stop, with every safety net disabled at once. Caught and fixed in verify.

### 3.5 Connection

`db.connect()` — a normal standalone connection (`foreign_keys=ON`, 30s busy timeout), opened once
per worker run and closed in the `finally`. **Not** `_checker()`, which is autocommit and
read-only by contract and must never be the connection that recomputes.

`scoring.py` gains `import db`. This introduces no cycle: `db` imports only `hashlib`, `json`,
`re`, `sqlite3`, `flask.g` and `config`, and `canonical` (which `scoring` already imports) does not
import `scoring`.

### 3.6 Deferring to a job

The worker exits without recomputing while `jobs.active()`, dropping `_worker_pending`. Nothing is
lost, for two reasons, and it takes both:

- **Every job that touches a scoring input ends with its own `recompute()` on the success path and
  both failure paths** — the property `ensure_fresh()` already documents and relies on for the same
  deferral. `backfill.py` is the exception that proves it: it never recomputes, because it writes
  only `wanted_uri`, which no score reads.
- **`ensure_fresh()` re-catches whatever the drop lost.** Deferring remembers nothing, so
  `_last_fingerprint` still sits behind the dropped request's write; the first request after the
  slot frees sees the moved fingerprint and enqueues. This is what covers a request-path apply
  landing mid-backfill, and equally a job that dies before its own closing recompute.

This also avoids fighting SQLite's single writer: the read phase takes no write lock, but
`DELETE FROM score` plus the ~30–40k-row `executemany` does, and a job committing per
playlist/batch would stall behind it (a stall, never a failure — `db._BUSY_TIMEOUT_SECONDS` is 30s).
And it avoids briefly materializing scores computed from a half-updated library.

---

## 4. The call-site split

### 4.1 The rule

> **Async where you are working a queue. Synchronous where you clicked once and are waiting for
> the outcome anyway.**

Justified by the §0 finding: since no stale score can cause a wrong decision, the only thing a
synchronous recompute buys is that the page you land on next is ordered correctly — worth ~1.8s on
a click you make monthly, worthless on a keypress you make hundreds of times an hour.

### 4.2 Async — `conn.commit()` then `scoring.request_recompute()`

| Site | File |
|---|---|
| `/api/canonical/apply` | `app.py` |
| `/api/canonical/cross/apply` | `app.py` |
| `/api/canonical/pin` | `app.py` |
| `artists.mark_same()` | `artists.py` |
| `artists.unmerge()` | `artists.py` |

`/dev/artists`' mark-same and unmerge are in because they are the same per-decision shape as the
review queue, just a shorter and less frequently worked queue.

**Order is load-bearing:** commit first, then request. The worker reads through its own connection
and would otherwise miss the write it was asked about.

### 4.3 Synchronous — unchanged

| Site | Why |
|---|---|
| `/api/scoring/recompute` (`app.py`) | The one deliberately blocking recompute. Its response carries the fresh tier counts and outcome that `static/js/scoring.js` renders in place; async would mean inventing a poll for no gain. It is also the manual retry after a failure (§6.2). |
| `/dev/generations/confirm` (`app.py`) | A form POST that redirects straight onto `/dev/generations` or `/dev/snapshot`, both score-ordered, and a new generation shifts tenure library-wide. Roughly monthly. |
| `canonical_autogroup.run()` / `.undo()` | One deliberate click each, already multi-second, and the page reloads onto score-ordered content when they return. |
| The 11 job call sites | Amortized into a multi-second-to-minute run. |

### 4.4 Click feedback on the synchronous sites

Three of the four already have it and need no work:

- `/api/scoring/recompute` — `static/js/scoring.js` disables the button, relabels it
  "Recomputing…", and sets the status line to "Running…".
- `canonical_autogroup.run()` — `static/js/canonical_viewer.js` disables both buttons and shows
  "Grouping…".
- `canonical_autogroup.undo()` — same file, disables both and shows "Restoring…".

`/dev/generations/confirm` is the gap and is fixed in §7.2.

---

## 5. The read-time backstop

`ensure_fresh()` (`docs/specs/scoring-H.md` §9.3) changes in three ways.

**5.1 It enqueues instead of recomputing.** On a moved fingerprint it calls `request_recompute()`
and returns. Two reasons this is right rather than merely convenient: it cannot keep its freshness
guarantee anyway (see 5.2 — it must defer while the worker runs, so a page loaded right after an
edit shows stale scores regardless), and the `score` table is durable in SQLite, so even the first
request after a process restart renders real scores rather than an empty table.

**Accepted consequence:** a page load immediately after an uninstrumented write —
`canonical.ensure_track_groups()` inserting `track_group` rows on a plain GET is the one this
backstop is mandatory for — renders scores one edit stale, and is correct on the next load.

**5.2 It defers while the worker is alive**, alongside the existing `jobs.active()` guard and under
the same discipline: **remember nothing while deferring**, so the next check compares against the
same older state.

```python
if jobs.active():
    return False
with _worker_lock:
    if _worker_alive:
        return False
```

Without this, the request right after an async apply would see the moved fingerprint and fire a
*synchronous* recompute, reinstating exactly the delay N exists to remove.

**5.3 It loses its `conn` parameter.** It never recomputes inline, so the only connection it needs
is `_checker()`. Update the sole caller, `refresh_scores()` in `app.py`'s `before_request` hook,
and its comment (the "cheap on the common case" note stays true; the "`conn` is used for the
recompute itself" sentence in the docstring goes).

Everything else stands: the ~0.002ms `PRAGMA data_version` fast path, the ~5ms nine-`COUNT(*)`
fingerprint on a moved data_version, and `_mark_seen()` on the "something committed but no scoring
input moved" branch. The `needs_recompute` branch still records nothing — the worker's `recompute()`
publishes its own, fresher `_observe()` pair.

---

## 6. Failure handling

A synchronous recompute that raises today 500s the request and you find out immediately. Off the
request thread, it is silent — so it needs a place to be seen and a rule that stops it spinning.

**6.1 Status.** Unchanged. `recompute()` already records every outcome through
`_record_recompute()` and `/dev/scoring` already renders it. A background failure lands there for
free.

**6.2 Do not auto-retry an identical failed fingerprint.**

The self-healing already in H covers a *transient* failure: `_mark_seen()` is never called on the
error path, so the fingerprint stays behind and the next backstop check retries. A *repeatable*
failure is the problem — `_last_data_version` never advances either, so every subsequent request
would pay the fingerprint read and spawn another doomed 1.8s pass, forever, silently.

- New module global `_failed_fingerprint`, guarded by `_backstop_lock`.
- `recompute()` initializes `observed = None` before its `try` body (a failure inside
  `canonical.ensure_track_groups()` happens before `_observe()`), and on the error path records
  `observed`'s fingerprint as `_failed_fingerprint` when it is not `None`.
- `_mark_seen()` clears `_failed_fingerprint`. Any success — background or the manual button —
  re-arms auto-retry.
- `ensure_fresh()` skips when the freshly-read fingerprint equals `_failed_fingerprint`: it neither
  enqueues nor marks anything seen.

**Accepted cost:** while broken with nothing else committing, each request re-pays the ~5ms
fingerprint read, because `_last_data_version` is deliberately not advanced (it and
`_last_fingerprint` only ever move together, via `_mark_seen()`). ~5ms per page load while a banner
is telling you scoring is broken is the right trade for not splitting that invariant.

The manual button always retries, since it calls `recompute()` directly and never consults
`_failed_fingerprint`.

**6.3 A visible signal.** §7.1.

---

## 7. UI

### 7.1 Failure banner

- `app.py` gains a `@app.context_processor` in `create_app` exposing the failure state, read from
  `scoring.recompute_status()` — an in-memory dict copy under a lock, so it costs nothing per
  request. Expose whether the last outcome was `"error"` and the error text.
- `templates/base.html` renders a one-line banner between the navbar and `{% block content %}`,
  only when that flag is set: the message, the error text, and a link to `/dev/scoring`. Reuse the
  existing `.error` styling; add a minimal banner rule to `static/css/style.css` only if needed.
- It clears itself — the next successful recompute overwrites the status.
- **It renders everywhere `base.html` does, including the immersive full-viewport pages**
  (`canonical_review.html`, `canvas.html`). That is deliberate: the review queue is exactly where a
  silently-broken recompute would otherwise go unnoticed. A slightly squashed canvas while scoring
  is broken is acceptable.

### 7.2 `/dev/generations/confirm` click feedback

New `static/js/generation_confirm.js` — an IIFE that no-ops when the form is absent, wired on
`DOMContentLoaded`, loaded site-wide from `base.html`'s `<head>` beside `format.js`. Site-wide
rather than added to `snapshot.html` and `generations.html` individually so it cannot drift from
the macro it serves, which is itself shared for that reason.

`generation_confirm_banner` in `templates/_macros.html` gains a stable id on its `<form>` (it
renders at most once per page — `pending_new_generation()` returns a single pending playlist).

Behaviour: on **submit**, disable both buttons and relabel the clicked one to "Working…".

> **The trap:** the form carries its decision on the submit button (`name="decision"`,
> `value="yes"|"no"`), and a disabled control contributes no value — **the submitter included**.
>
> `event.submitter` correctly identifies *which* button was clicked, but that is not enough on its
> own: the browser constructs the form's entry list after the `submit` event finishes dispatching,
> in the same synchronous step, and skips any disabled field. So disabling the button synchronously
> inside the `submit` handler still drops `decision`, and the route 400s with "playlist_id and a
> yes/no decision are required".
>
> Defer the disable past that step (`setTimeout(…, 0)`) so the browser reads the still-enabled
> button first. Corrected during implementation — planning had assumed `event.submitter` alone
> settled it — and verified in verify, both Yes and No, against the live route.

---

## 8. Concurrency and safety

- **Two recomputes at once** (the manual button while the worker runs) is safe and needs no
  coordination. Both wholesale-replace the table from the same committed data and produce identical
  rows; SQLite serializes the write phase and the 30s busy timeout absorbs the wait.
- **Out-of-order completion** is covered by the existing `_mark_seen()` discipline: an
  earlier-started recompute finishing last moves the remembered fingerprint *backwards*, which at
  worst causes one redundant recompute. It can never over-claim.
- **Process death mid-recompute** leaves `score` intact (the `DELETE` + `INSERT` is one
  transaction) and `_mark_seen()` uncalled, so the first request after restart re-enqueues.
- **The Flask reloader** kills daemon threads when a `.py` file is written — the existing
  don't-edit-during-a-job trap now nominally covers a running recompute too, with a blast radius of
  nil: the next request re-enqueues.

---

## 9. Out of scope

Incremental or faster scoring; any change to `recompute()`'s math or constants; any change to
`jobs.py`; the eleven job call sites; a live "recompute running" indicator on `/dev/scoring`
(considered and declined — the page's own button is synchronous and reports its own progress).

---

## 10. Files touched

| File | Change |
|---|---|
| `scoring.py` | `request_recompute()` + `_worker()` + worker state (§3); `ensure_fresh()` enqueues, defers on the worker, loses `conn` (§5); `_failed_fingerprint` (§6.2); `import db` |
| `app.py` | 3 sites → `request_recompute()` (§4.2); `refresh_scores()` hook drops the `conn` argument; failure-banner context processor (§7.1) |
| `artists.py` | 2 sites → `request_recompute()` (§4.2) |
| `templates/base.html` | Failure banner; `generation_confirm.js` script tag |
| `templates/_macros.html` | Stable form id on `generation_confirm_banner` |
| `static/js/generation_confirm.js` | New (§7.2) |
| `static/css/style.css` | Banner rule, only if `.error` alone is not enough |

---

## 11. Verification

1. **The felt fix.** Apply a group in the review queue; the `/api/canonical/apply` response drops
   from ~1.8s to milliseconds (verify measured **95ms**).
2. **Coalescing.** Apply several items in quick succession, then check `/dev/scoring`: one
   recompute finishes within ~1.8s of the *last* apply, not one per apply.
3. **Scores actually update.** Merge two tracks, wait ~2s, load the new group's page — it has a
   score, not a blank.
4. **The backstop still works.** With the app freshly restarted, load a page that makes
   `canonical.ensure_track_groups()` write, and confirm a recompute follows (`/dev/scoring`'s last
   run advances) without any page having blocked on it.
5. **Deferral holds.** During a background job, confirm no recompute is started by the worker and
   that the job's own closing recompute lands.
6. **The generation-confirm form still submits its decision** with the buttons disabled on submit
   — the one change here that could break silently. Test both Yes and No.
7. **The banner** requires forcing a failure to see; if verify does that, it must revert the
   forced break and confirm the next successful recompute clears the banner.
8. `/dev/scoring`'s manual button is unchanged in behaviour.
