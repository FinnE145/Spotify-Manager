# P3 — Refactor

**Step P of `docs/Planning/roadmap.md`, part 3 of three.** Read `docs/specs/codebase-health-P.md`
§0 first — it is an approach document, not a contract, and this file is the authoritative
instruction for P3's sessions. Its §5 decided extraction over blueprints, and its §6 lists what a
cleanup must not tidy away. Both are binding here.

Predecessors: `docs/codebase-health/P1_spec_audit.md` / `P1_findings.md` (21 findings, all ruled),
`docs/codebase-health/P2_tests.md` / `P2_findings.md` (770 tests, 10 findings, all ruled).

---

## §0 Where we left off

P2 finished and merged 2026-08-22. `venv/bin/python -m pytest` is green at **770 tests in ~11s**
on `main`, and `feat/codebase-health-P` is at the same commit. P3 continues on that branch —
`codebase-health-P.md` §8 puts all three parts on it, merging `--ff-only` into `main` as each
lands.

P2 left three things P3 consumes:

- `tests/golden.py` — byte-exact capture/compare, committed and inert. No snapshots captured.
- `tests/routes_catalog.py` — every route as a concrete, issuable request, 90 cases including the
  21 query-string variants session 5 added. `golden_cases()` is the GET-only subset.
- The permanent 69-route non-5xx sweep plus its semantic assertions, in `tests/test_routes.py`.

---

## §1 The goal, and the single acceptance criterion

P3 has exactly one: **nothing observable changed.** Not "the tests still pass" — 770 tests passing
is necessary and nowhere near sufficient for a refactor that moves several hundred lines between
modules. The criterion is a byte-exact diff of every rendered page, before and after, and §3 is how
that is obtained.

Everything else P3 does — the extraction, the deleted function, the broken cycle — is subordinate
to that. A change that cannot be verified byte-exact does not land in P3.

**Measurements, re-taken 2026-08-22** (the roadmap's are from 2026-08-16, and `create_app` has
grown since):

| | 2026-08-16 | now |
|---|---|---|
| `create_app` | 1,572 lines | **1,620** |
| `app.py` total | — | 1,675 |
| `@app.route` | 69 | 69 |
| `conn.execute` in `app.py` | 42 | 42 |
| inner defs in `create_app` | 71 views | 74 defs |

**Be honest about the headline.** §4.1's extraction takes `create_app` from 1,620 to roughly
**1,130 lines**, not to something small. 69 routes at ~10 lines each is a ~700-line floor and that
is inherent to the extraction-not-blueprints decision — `codebase-health-P.md` §5 chose it knowing
that, on rename blast radius and testability, not on line count. The number that actually moves is
**`conn.execute` in `app.py`: 42 → 12**. Judge P3 by that and by the seams, not by the line count.

---

## §2 What these sessions must not do

**Strictly behaviour-preserving.** P1 was allowed to surface and fix bugs; P3 is not. If a session
finds a bug while moving code, it records it (§7) and moves the code as-is. Fixing it in the same
diff destroys the one thing that makes a byte-exact diff meaningful: that every difference is a
defect.

**`codebase-health-P.md` §6 is binding, and moving code between modules is exactly what violates a
module invariant by accident.** The ones in P3's path:

- **`canonical.py` never commits** — so `canonical.ensure_track_groups(conn); conn.commit()` stays
  in the route. Do not fold the commit into an extracted function that lives in `canonical.py`.
- **`canonical.py` never touches `track` or `membership`.**
- **`entities.py` is read-only w.r.t. the Spotify library**, and owns the two guarded detail
  fetches. It gains read paths in §4.1; it gains no writes to Spotify.
- **`snapshot.py` is read-only w.r.t. Spotify**; **`roundtrip.py` is the only module that writes to
  the library**. Neither is touched by §4.1 except `snapshot.index_data`, a pure read.
- **`track.artists` is write-only, never read.**
- **The 24% comment/docstring density.** Comments move *with* the code they explain. A comment
  explaining why a query is shaped a certain way belongs beside that query in its new home, not
  deleted as "cleanup". Density should come out of P3 at that level or higher.
- **The one-`TODO` discipline** — one `TODO` (roundtrip.py:35), zero `FIXME`/`HACK`/`XXX`, no
  commented-out code. Verified still true 2026-08-22.
- **`entity_link` centralization** — zero `url_for` bypasses outside `_macros.html`. Extraction
  moves no template code, so this should be untouched; `test_template_conventions.py` asserts it.

**No blueprints.** Deferred by §5 and still deferred. If what remains is unpleasant to navigate
after P3, that becomes its own later step decided on evidence.

---

## §3 The golden baseline — the verification story

This is the part to get right before touching a line of production code.

### 3.1 The database

**A plain copy of `symr.db`**, not a sampled one. P2 left "a committed build script producing a
gitignored sampled DB"; an FK-consistent subset across ~20 tables is real engineering for something
P3 deletes at the end, and a copy renders strictly richer pages — a byte-exact snapshot of a page
saying "no results" proves nothing. It is a copy, so the real database is untouched, and `*.db` is
already gitignored (`.gitignore:5`), so nothing can be committed by accident.

Two files: a **pristine** copy taken once, and a **run** copy restored from it before every pass.
The restore is not optional — see 3.2.

### 3.2 Four sources of non-determinism, all of which must be dead

In Symr a plain GET writes, so "capture then compare" is not naturally reproducible:

| source | what it does to a byte diff |
|---|---|
| the async scoring worker | `ensure_fresh()` enqueues, `scoring._worker()` recomputes in the background — a recompute landing mid-pass changes scores for every page rendered *later in the same pass*, differently each run |
| `entities.fetch_album_tracklist` / `fetch_artist_image` | P1-016 made a failed fetch stamp "attempted", so even a *blocked* request writes, and the second pass renders the other branch |
| `queue_wanted_uris`, `ensure_track_groups` | plain GETs that write, bumping `PRAGMA data_version` and so triggering the recompute above |
| the clock | scoring's 90-day `recent` horizon and `play_stats`' 30d/7d windows are `now`-dependent |

**Run the golden passes under pytest, not through `golden.py`'s `__main__`.** `conftest.py` already
provides the frozen clock, blocked sockets and the connect guard; the standalone CLI has none of
them, which is exactly how it reached the real `symr.db` on 2026-08-21 and wrote 9 `wanted_uri`
rows. Its `__main__` guard stays (it is the last line of defence) but is not the path P3 uses.

The expected shape, to be confirmed by 3.3 rather than assumed:

1. restore the run copy from the pristine copy,
2. point `SYMR_DB_PATH` at it, inside the run's temp directory so the connect guard is satisfied,
3. frozen clock and blocked sockets from `conftest.py`,
4. `scoring.request_recompute` no-oped for the duration, so no background pass can land mid-run.

Suppressing the recompute is not cheating: golden is a diff test, and scoring's recompute paths are
covered by P2 session 3's 89 unit tests.

### 3.3 The gate

**Before any production code changes: capture, then compare, with zero code changed, and get zero
diffs.** If that does not come out clean, the mechanism in 3.2 is wrong and the whole verification
story for P3 is void — stop and fix it there, do not start refactoring against a baseline that
already drifts.

This is a hard precondition, not a smoke test. Everything after it depends on "any diff at all is a
bug" being literally true.

### 3.4 Capture once, compare many, delete once

- **Capture once**, at the branch's starting commit, before session 1's first edit.
- **Compare at the end of every session**, expecting zero diffs.
- **Never re-capture.** If session 1 introduced a byte-level regression and session 2 re-captured,
  that regression becomes the new baseline and sessions 2–3 can never see it. Re-capturing is the
  one action that silently converts this suite into decoration.
- **Delete at the very end**, in session 3 (`P2_tests.md` §4.6 — the tooling stays committed, the
  snapshots never are). `.gitignore:20` already excludes `tests/golden_snapshots/`; use that path.

The snapshots live on disk across all three sessions and across the `--ff-only` merges between
them. They are gitignored, so nothing carries them but the working tree — a session that loses them
must stop, not re-capture.

---

## §4 The work

### 4.1 `create_app` — nine views, by extraction

**Routes stay where they are; the work moves to the module that already owns that data.** Every
view was read and judged; this is the list, with non-blank sloc and `conn.execute` count.

| view | sloc | ex | goes to |
|---|---|---|---|
| `album_page` | 132 | 4 | `entities.album_detail(conn, album_id)` |
| `artist_page` | 95 | 5 | `entities.artist_detail(conn, artist_id)` |
| `playlist_page` | 85 | 5 | `entities.playlist_detail(conn, playlist_id)` **+** `generations.generation_view(conn, ordinal, tier)` |
| `canonical_index` | 85 | 3 | `canonical_detect` — see 4.1.1 |
| `search_page` | 83 | 5 | `entities.search(conn, q)` |
| `group_page` | 60 | 2 | `entities.group_detail(conn, tier, group_id)` |
| `snapshot_index` | 56 | 3 | `snapshot.index_data(conn, q)` |
| `dev_generations_tenure` | 54 | 0 | `generations.tenure_page(conn, tier, sort, page)` |
| `track_page` | 34 | 3 | `entities.track_detail(conn, track_id)` |

≈583 sloc, 30 of `app.py`'s 42 `conn.execute`.

**The rules that make these unit-testable, which is the point (§5 reason 2):**

- **No Flask in an extracted function.** No `abort()`, no `redirect()`, no `request`, no
  `render_template`. A missing row returns `None` and the route calls `abort(404, …)` with the
  description it already uses. `request.args` parsing stays in the route; extracted functions take
  plain arguments. This is what lets them be called against a fixture DB with no request context.
- **`artist_page`'s alias redirect stays in the route** — it returns a redirect, so it is routing.
- **`canonical.ensure_track_groups(conn); conn.commit()` stays in the route**, per §2's invariant.
- **A comment moves with its code.** Several of these views carry the most load-bearing comments in
  the tree (`album_page`'s "owned tracks the fetched page didn't contain", `artist_page`'s "counted
  off the rendered rows, not off credit_rows", `search_page`'s rank-before-cap note). They move
  verbatim.

**Explicitly not extracted, and why:**

- **`api_canonical_cross_apply` (56 sloc)** — write orchestration, not read path. Its ordering is
  load-bearing (`apply_partition` → `pending_tier_review` → `mark_reviewed_pairs` → commit →
  `request_recompute`), and moving it into `canonical.py` would either break that module's
  no-commits invariant or split one transaction across two modules. §5's rule does not ask for it
  and the risk is real.
- **`api_canonical_cross_listing` (37 sloc)** — the data half is ~10 lines; the rest renders a
  template into JSON.
- **Everything ≤ 34 sloc besides `track_page`** — already routing. `track_page` is in for
  consistency: all six entity pages then have exactly one seam each.

#### 4.1.1 `canonical_index` — the one view with no clean home

Every candidate was checked against the actual import graph (`canonical.py` imports nothing
project-level; `scoring.py` → `canonical` for one call at scoring.py:414; `canonical_autogroup` →
`canonical_detect`):

- `canonical.py` would need `canonical → scoring`, inverting the direction `CLAUDE.md` documents
  and creating a cycle in the step whose other job is removing one. Out.
- A new module for one page is thin.
- Leaving it keeps the fourth-largest view in `app.py`.

**Decided: `canonical_detect.py`, with the two autogroup-status values staying in the route.**
`canonical_detect` already owns this page's siblings — `canonical_page_groups` is named for it,
alongside `filter_groups` and `pending_song_ids` — and already imports `canonical` and `scoring`,
so no new dependency appears. The listing assembly (`song_group_rows` → score → sort → cap →
expand-deep-link → hydrate → trees → credit ids) is one coherent ~45-sloc unit, plus the
`search_q` block.

`canonical_autogroup.last_run(conn)` and `canonical.auto_grouped_ids(conn)` **stay in the route** as
template kwargs. That is not a workaround: `canonical_autogroup` imports `canonical_detect`, so
pulling them in would create a new cycle — and "status of the last autogroup run" is a different
concern from "build the listing" anyway.

`_cap_listing` and `_LISTING_CAP` are shared with `api_canonical_cross_listing`, which is not being
extracted; leave them in `app.py` and pass the cap in, or leave the capping in the route. Whichever
comes out byte-identical with less indirection.

### 4.2 The circular import

The only cycle in the graph, and it is small and one-sided:

- `artists.py:172` → `canonical_detect.normalize_name` — one call site.
- `canonical_detect.py:187, :535` → `artists.artist_sets` — two call sites.
- `roundtrip.py:608, :618` also use `canonical_detect.normalize_name`.

**Fix: a new `normalize.py`** holding the shared string normalizer and its two helpers —
`_normalize_base_string` (canonical_detect.py:120) plus `_strip_accents` and
`_strip_punct_collapse`, made public in the new module since they are used across it.

- `canonical_detect.normalize_title` / `normalize_suffix` call into `normalize`; they stay put, they
  are detection-specific.
- `canonical_detect.py:236`'s own `_normalize_base_string(…)` call goes through `normalize`.
- `canonical_detect.normalize_name` disappears (only three callers, all listed above).
- `artists.py` drops `import canonical_detect` entirely. **The cycle is gone.**
- `normalize.py` imports nothing project-level, so it introduces no edge.

`tests/test_artists.py:467` asserts `detect.normalize_name is detect._normalize_base_string` and
must be updated to the new home. That is a legitimate update — a function moved, and the test pins
the identity, not a behaviour.

### 4.3 `CLAUDE.md`

Its codebase map must be updated anyway — nine views' worth of functions are moving and `normalize.py`
is new. Do that by hand, in session 3, once the code has settled.

**Plus one narrow mechanical check, and deliberately only one:** a test asserting that every module
in the repo appears in `CLAUDE.md`'s map and every module the map names exists. List against list,
no numbers parsed out of prose, ~15 lines.

**What was considered and rejected:** a test parsing the map's numeric claims — "three app-wide
`before_request` hooks", "the eighth start route", "four background jobs" — and checking them
against `app.url_map` or an AST scan. That is the drift that actually happened (P2-001 found "three
jobs" where there are four; the pre-spec found "two hooks" after J added a third), so the motivation
is real. But it greps English that gets rewritten constantly: rephrase "three hooks" to "a trio of
hooks" and it fails legitimately, which `codebase-health-P.md` §4 identifies as precisely the shape
that gets regenerated reflexively until it protects nothing. And it reaches only the claims that are
numbers, while the map's value is the *why* prose, which nothing can check. The module-list version
catches the drift class that matters most — a module added and never documented, which P3 itself
would commit if 4.2 landed unrecorded — and it cannot fail for a rewording.

### 4.4 The deletion — and the sweep that found nothing else

**Delete `canonical_detect.all_candidate_groups`** (canonical_detect.py:612), condemned by P1-009 on
a full caller search and re-confirmed 2026-08-22: zero references anywhere outside documentation.
Update the two doc references that describe it as live — `docs/canonical-tracks/detection.md:166`
and `docs/specs/canonical-fixes.md` — rather than leaving them naming a function that is gone.

**A seven-category sweep over the whole tree found nothing else**, which is worth recording so
nobody re-runs it:

| | result |
|---|---|
| zero-reference public functions | **1** — `all_candidate_groups` |
| unused imports | 0 |
| orphan templates | 0 |
| unused Jinja macros | 0 of 12 |
| unreferenced JS files | 0 of 11 |
| unreferenced `config.py` settings | 0 |
| unused CSS classes | 0 of 88 |
| `TODO`/`FIXME`/`HACK`/`XXX` | 1 — the sanctioned one at roundtrip.py:35 |

Two near-misses that are **not** deletion candidates: `canonical_detect.stale_recording_groups`
(called by `scripts/reset_misgrouped_pairs.py:66`, covered by three tests) and the
`auto_group_snapshot_*` tables / `resolved_track_artist` view (used by `canonical_autogroup`'s
restore and by another view's SQL respectively). Neither is `all_candidate_groups`' shape.

P2-004 and P2-009 remain **explicitly not deletion candidates** (`codebase-health-P.md` §10) — both
are readings of an algorithm, which is weaker evidence than the caller search that condemned
`all_candidate_groups`.

### 4.5 `SELECT *` — added to P3, not one of the four findings

Three sites select every column where the codebase's own rule is to name them:

- `playlist_page` — `SELECT * FROM snapshot` (15 columns)
- `snapshot_index` — `SELECT * FROM snapshot`
- `_board_state` (app.py:1657) — `SELECT * FROM card` (10 columns) and `FROM label` (5)

**This is the riskiest change in P3 and the one golden snapshots were made for.** `_board_state`
does `dict(row)` and returns it as JSON to `canvas.js`, so a missed column silently changes an API
payload; the `snapshot` sites feed templates that access by name, so a missed column is a template
error. Both are covered — `/api/board` and `/api/export` are in the golden catalog, as are
`/playlist/<id>` and `/dev/snapshot`.

Note the one argument *for* `SELECT *` here: `db._migrate` adds columns additively, so a named list
needs updating when a column is added. That is the cost, and it is accepted — a query that silently
widens is the thing the rule exists to prevent.

---

## §5 The sessions

Three, each ending with a compare against the §3 baseline and a `--ff-only` merge into `main` after
Finn verifies it.

**Session 1 — baseline, then the small independent fixes.**
Build §3's harness. Prove §3.3's gate. Capture. *Then* refactor: §4.4's deletion, §4.2's cycle,
§4.5's `SELECT *`. Compare — zero diffs. This ordering is deliberate: the session that builds the
verification also exercises it on the smallest real changes in P3, so a broken harness surfaces
before 583 lines are in flight.

**Session 2 — the entity pages.** `album_page`, `artist_page`, `playlist_page` (+ `generation_view`
to `generations.py`), `search_page`, `group_page`, `track_page` — 489 sloc, six new seams in
`entities.py` plus one in `generations.py`. The heaviest session; §6's unit tests are most of its
work after the moves.

**Session 3 — the dev pages, then close out.** `canonical_index`, `snapshot_index`,
`dev_generations_tenure` — 195 sloc. Then `CLAUDE.md`'s map and §4.3's check, the spec updates
(§7), `codebase-health-P.md` §10's status table, the roadmap's P3 row, and **delete the snapshots**.

P3 then gets its Verify session, per `codebase-health-P.md` §8.

---

## Tests

P3 adds tests. The suite exists precisely so this refactor is safe, and leaving the new seams
untested would undermine the reason P2 came first.

- **One unit-test set per extracted function** — nine of them (§4.1), called against a fixture DB
  with no HTTP. Spec-derived where a spec clause covers the behaviour (`entity-pages-K.md`,
  `generations-B.md`, `scoring-H.md` §11.1's rank-before-cap, `grouping-fixes-backfill-M.md` §4.4),
  `characterization` otherwise. Every test carries its one-line source comment —
  `test_every_test_declares_where_its_expected_value_came_from` enforces it.
- **Do not delete or weaken a single existing route test.** They are the seam-crossers. P2-008's
  whole finding was that `entities.py` at 100% line coverage plus a route whose guard nobody
  asserted let "spend a Spotify request on every album view" pass 708 tests. Extraction moves those
  guards *into* the extracted functions, and the route tests are still the only thing asserting the
  route calls them — losing one re-creates P2-008 exactly.
- **Ask both of P2's questions of every new test**: *what would a broken implementation have
  produced here?* and *is there a return value or code path here that nothing reads?* A fixture must
  disagree with **every** rule the implementation could fall back on, not just the one the spec
  discusses (P2-005).
- **Update `tests/test_artists.py:467`** for `normalize_name`'s new home (§4.2).
- **New: the `CLAUDE.md` module-list check** (§4.3).
- `tests/test_golden.py` already self-tests the tooling; extend it for whatever §3.2's harness adds.

**No `xfail` should be owed by P3.** P3 is behaviour-preserving, so it fixes no bugs; anything it
finds goes to §7 as a finding, not into the suite as a marker.

---

## §7 Recording what the sessions find

Same convention as P2: findings go to **`docs/codebase-health/P3_findings.md`** with `P3-###` ids,
each ruled by Finn before the session that found it merges. A bug found while moving code is
recorded there and the code moves unchanged (§2).

**Spec updates.** Extraction falsifies any spec sentence claiming this read-path work lives in
`app.py`. Seventeen specs mention `app.py`, but most references are incidental and stay true —
routes do stay in `app.py`. So session 3 greps `app.py` across `docs/specs/` and updates **only the
claims extraction falsifies**, the same rule P1 audited by. Do not rewrite a spec because a file
name appears in it.

---

## §8 Done

P3 is finished when:

1. The §3.3 gate passed, the baseline was captured once, and the final compare over
   `routes_catalog.golden_cases()` reports **zero diffs**.
2. All nine views in §4.1 are extracted, with no Flask call in any extracted function, and
   `conn.execute` in `app.py` is down to ~12.
3. `artists.py` no longer imports `canonical_detect`, and the import graph has no cycle.
4. `all_candidate_groups` is gone, with its two doc references updated.
5. The three `SELECT *` sites name their columns.
6. `CLAUDE.md`'s map is accurate and the module-list check is in the suite.
7. `venv/bin/python -m pytest` is green, with the new seam tests and no weakened route tests.
8. `tests/golden_snapshots/` is deleted; `tests/golden.py` and `tests/routes_catalog.py` stay
   committed.
9. Every P3 finding is in `P3_findings.md` and ruled.
10. `codebase-health-P.md` §10's status table and the roadmap's P row are updated.
11. Each session merged `--ff-only` into `main` as it landed.
