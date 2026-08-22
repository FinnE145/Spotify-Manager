# P — Codebase health

**Step P of `docs/Planning/roadmap.md`.** Findings record: `docs/Planning/codebase_health_P.md`
(dated 2026-08-15) — that file holds the measurements and the four findings and is not
duplicated here.

---

## §0 What this document is, and what it is not

**This is not a normal spec. Do not read it as one.**

Every other file in `docs/specs/` is a complete, fully-decided contract: an implement session can
start from it alone, and `CLAUDE.md` and the three phase skills all train a cold session to treat
anything found here as authoritative and finished. This file is not that, and a session that
mistakes it for one will either over-trust it or discard it as stale.

**What this file is:** the standing approach document for a multi-session step. It records the
reasoning and the decisions that hold across all of P — the ones that must not be re-litigated
each session — plus a live status section (§10) pointing at where the work actually is.

**Where the instructions live:** `docs/codebase-health/<part>.md`. Those are the authoritative
prompts for individual sessions, and they are written as *instructions* — where we left off, what
this session is for, what "done" looks like — not as contracts. They get written one at a time,
as the previous part finishes and its findings are known.

**Why the departure.** P is not a plan → implement → verify feature. It is a spec audit whose
output is a pile of decisions Finn has to make by hand, then a test suite built from those
decisions, then a refactor verified by that suite. The middle of it is unusually interactive, and
what P3 does depends on what P1 found and what P2 actually covered. A single canonical spec
written up front would be edited continuously, which is exactly what a spec must never be.

---

## §1 The three parts, and why in this order

| | | output |
|---|---|---|
| **P1** | Spec audit | Every spec matched against the code; each difference classified and ruled on |
| **P2** | Tests | The suite, plus the workflow changes that keep it alive |
| **P3** | Refactor / cleanup | The pre-spec's four findings, verified against P2 |

**Tests come before the refactor** because the pre-spec's own §8.4 is right: a behaviour-preserving
refactor is very hard to verify without them. `create_app`'s extraction has exactly one acceptance
criterion — *nothing observable changed*, across 69 routes — and that is the one thing a human
clicking through pages cannot confirm.

**The spec audit comes before the tests**, and this part was not in the pre-spec at all. Tests
written against code can only ever encode *what it does*; they freeze every existing bug into a
green suite and make it permanent. Tests written against an audited spec encode *what it should
do*, and the difference between the two lands on Finn's desk as a decision instead of being
silently ratified. P1 is what makes P2's assertions trustworthy.

P1 has a second output that matters independently of tests: accurate specs. `docs/specs/` is
6,381 lines across 17 files and is the primary thing a cold session reads. Its drift is the same
class of problem as the pre-spec's §4 finding about `CLAUDE.md`, and P1 fixes it directly.

**P1 is explicitly not behaviour-preserving.** It will surface real bugs, and Finn may choose to
fix some inline rather than queue them. P3 *is* behaviour-preserving, strictly.

---

## §2 The central distinction: characterization vs specification tests

Everything else in P — what gets delegated, who writes what, what may be regenerated after a
refactor — follows from this one split. It is the concept to hold onto.

**Characterization tests.** The expected value *is* the current output. Obtaining it by running
the code is correct here — that is the entire point. Their purpose is refactor safety: they
answer "did this change anything?" and nothing else. They say nothing about whether the current
behaviour is right.

**Specification tests.** The expected value comes from the audited spec. Obtaining it by running
the code is a **tautology that tests nothing and silently ratifies bugs** — and it is the path of
least resistance for any agent told "write tests for this function", which is why it is called out
as a hard rule in §3. Their purpose is correctness now and regression prevention later.

**Both are needed, for the two different reasons P exists.** Characterization tests are what make
P3 safe. Specification tests are what stop ordinary feature work quietly breaking old code. A
suite of only the first is useless the moment P3 lands; a suite of only the second has no
coverage of the vast surface no spec describes.

**Every test carries a one-line source comment** naming where its expected value came from: the
spec clause it derives from, or `characterization`. This is not decoration. It makes review a fast
scan of (assertion, cited clause) pairs rather than a re-derivation, and during P3 it is what tells
you at a glance which tests may legitimately be updated (function-level characterization, when a
function moves) and which must never be (anything spec-derived).

**Where characterization survives a refactor, and where it doesn't.** P3 moves code but not
routes (§5), so route-level characterization is immune by construction while function-level
characterization churns. That asymmetry is why the heavy characterization investment goes at the
HTTP boundary.

**The corollary, found the hard way in P2 session 1 and in every session since: a test can be
neither of these and still be green.** Both kinds above assume the assertion would *notice* a wrong
answer. A test citing a real clause, asserting something true, can still be one a broken
implementation would satisfy. It has taken **two distinct shapes, and they need two different
questions** — asking only the first is what let session 3's through:

- **The fixture is too simple** to separate two mechanisms that agree on the easy input. Bites
  hardest on orderings, fallbacks and which-pass-handled-this questions. Found by asking, of each
  test, *what would a broken implementation have produced here?*
- **The observation was never made** — a column, a return value or a code path that no assertion
  reads at all. The first question cannot find this one, because there is no test to ask it of.
  Found by asking, of each *module*, *what does this produce that nothing reads?*

Both are invisible to the runner by construction, and **coverage is blind to the second by
definition**: the code producing an unread value runs exactly as it would if the value were read.
`docs/codebase-health/P2_tests.md` §1 carries the real examples of each and what had to change; the
same trap applies to any characterization test P3 regenerates.

---

## §3 The delegation model

P2 is realistically 2.5–4k lines of test code. One session writing that in one context drifts.
But the naive fix — farm it all out — trades one problem for a worse one: **a subagent cannot ask
Finn.** It hits an ambiguity and guesses, and a wrongly-asserted test is worse than a missing one.
A missing test is a known gap; a wrong test is a landmine that gets "fixed" later by changing
correct code.

**The shape:** per-area sequential implement sessions, each an ordinary session that can ask Finn,
with subagents used *inside* each for the genuinely mechanical work. That keeps the
context-exhaustion fix and the parallelism where it is safe, without giving up the question-driven
workflow the whole project runs on.

**Who does what:**

- **Opus (the session itself) owns all infrastructure** — `conftest.py`, the DB guard, the fixture
  builders, the fake Spotify client, the parametrize skeletons. Non-delegable: a subtle bug here
  poisons every test downstream and would be invisible in all of them.
- **Sonnet enumerates; it never judges intent.** "List every branch in `_diff_playlist_tracks` and
  the input classes reaching each" is mechanical and checkable by reading the function. "Decide
  which tests are needed" is where intent gets guessed. The what-should-it-do decision stays with
  the audited spec, the session, and Finn.
- **Sonnet may write specification tests only** with the relevant spec clause quoted into its
  prompt verbatim, and a standing instruction to **report and stop** rather than guess when the
  clause does not settle the case.
- **Haiku fills parametrize case lists for characterization tests**, and only those.

**The hard rule, stated in every subagent prompt:** never obtain an expected value by executing
the code under test, except in tests explicitly labelled `characterization`.

This model applies to P1 as well as P2 — a Sonnet reading a spec against its code enumerates
differences and never rules on which side is right. That ruling is Finn's.

---

## §4 Test infrastructure — decisions already made

**Runner: pytest**, added to `requirements.txt`. Not a toss-up given §3: `@pytest.mark.parametrize`
is what makes "fill in twelve more cases" about as safe as delegation gets, fixtures give proper
temp-DB teardown, and `conftest.py` is the one natural place to set the environment *before* any
project import — which the next paragraph makes load-bearing. Tests live in `tests/`.

**The `symr.db` guard — this is the security-grade part of P, not the KISS part.** `db.py:8` and
`scoring.py:34` both do `from config import DB_PATH`, a *from-import*, so the path is bound at
import time. Setting `SYMR_DB_PATH` after importing anything is a silent no-op and the suite runs
against the real 93 MB `symr.db` — seven years of streaming history, 461+ hand-reviewed grouping
pairs, 37 generations of curation, none of it reconstructible and none of it re-suppliable by
Spotify. So: the environment is set at the very top of `conftest.py`, before any project import,
**and** a hard assertion refuses to run the suite at all if the resolved path is not a temp one.
Belt and braces, deliberately, per `CLAUDE.md`'s never-corrupt rule.

`config.py` also reads `os.environ["SPOTIFY_CLIENT_ID"]` at import and raises `KeyError` without
it, so `conftest.py` sets dummy credentials — which additionally makes it structurally impossible
for a test to build a real authed client.

**Fixture data: nothing real is committed.** Hand-built builders (`make_track()`,
`make_playlist()`, …) produce the tiny purposeful rows unit tests want and the ~20-track / few
playlist shape route tests want. `db.init_db()` against a temp path already builds the full schema
plus views, so an empty DB is free. The only thing wanting realistic content is the ephemeral
snapshot run (below), and it runs on Finn's machine against a **gitignored** sampled DB built by a
committed script. Nothing real enters git, so no anonymization work is needed.

**Route tests, in two layers with different lifespans:**

- **Permanent** — every route returns non-5xx, plus a handful of semantic assertions (this page
  contains this track's name; this count renders with a thousands separator). Robust to template
  edits, useful forever. Auth is bypassed by monkeypatching `get_spotify_client`.
- **Ephemeral** — byte-exact HTML golden snapshots, captured immediately before P3, diffed after,
  then deleted. The capture/compare tooling stays committed for the next site-wide refactor.

The split exists because a permanently-maintained byte-exact snapshot suite would fail on nearly
every feature branch for entirely legitimate reasons, and **a test that routinely fails
legitimately gets regenerated reflexively — at which point it protects nothing.** Generated for one
specific refactor, "any diff at all is a bug" stays unambiguous.

**Golden snapshots need a frozen clock.** Server-rendered output is `now`-dependent in places —
scoring's 90-day `recent` horizon, `play_stats`' 30d/7d windows — so a capture and a compare an
hour apart would differ for reasons that have nothing to do with the refactor. The fixture
infrastructure pins "now" alongside the DB path.

**Scope: all four test tiers** — pure functions, route tests, DB-bound logic, Spotify-bound loops.
The last needs a fake `sp` object covering only the endpoints those loops call (not a general
spotipy mock), and it earns its keep: `roundtrip.py`'s replace-never-append and read-as-a-bag
invariants were both learned the hard way and are the highest-corruption-risk logic in the tree.

**JS is out of scope.** 3,260 lines across 11 IIFEs with no bundler and no node anywhere in the
project; testing it means adding node + vitest + jsdom to a Python repo, and most of it is DOM
wiring that a fake DOM tests badly. Revisit only if it becomes a real source of regressions.

**Bugs found during P2 are recorded, not fixed there.** Each gets a test asserting the *correct*
behaviour, marked `@pytest.mark.xfail(strict=True)` with a comment naming its findings-doc entry.
The suite stays green today; when the bug is fixed, `strict=True` turns the unexpected pass into a
loud failure saying "remove this marker". **The findings doc and the xfail set must match
exactly** — a bug in one and not the other is the failure this convention exists to prevent. This
is a debt ledger, which the pre-spec's §6 warns against, and it is accepted here on one condition:
it is executable, self-clearing, and the fix session is already planned.

---

## §5 P3 — query extraction, not blueprints

**Decided: extraction. Blueprints deferred, and possibly never.**

Extraction leaves every route where it is and moves the *work* out of the view bodies into the
modules that already own that data — `album_page`'s 131 lines become ~15 lines of routing calling
into `entities.py`, and the 42 `conn.execute` calls in `app.py` largely relocate to `entities.py` /
`canonical.py` / `generations.py`.

Four reasons it wins:

1. **It fixes the rule the codebase already wrote down and then broke.** `CLAUDE.md` states that
   entity read paths belonging to no existing owner live in `entities.py`; four page routes kept
   theirs inline anyway. Blueprints fix a size problem nobody ever documented as a rule.
2. **It creates the testable seams P2 wants.** `entities.album_detail(conn, id)` is unit-testable
   against a fixture DB with no HTTP at all. Extraction makes the permanent test layer better;
   blueprints leave testability roughly where it is.
3. **Zero rename blast radius.** Blueprints namespace endpoint names, so `url_for("album_page")`
   becomes `url_for("entities.album_page")` — **53 call sites across 24 distinct endpoint names**
   in templates (measured 2026-08-16, excluding `static`). Templates would change, and the golden
   snapshots would then legitimately differ, turning "any diff is a bug" into "any diff except the
   expected ones". Extraction should come out byte-identical.
4. **It is genuinely incremental** — one route at a time, each independently verifiable — where
   blueprints is closer to big-bang.

The sequencing is one-way in our favour: extraction removes several hundred lines from
`create_app`, after which blueprints is *easier* if still wanted (thin routes move cleanly; fat
ones drag their SQL along). If what remains is still unpleasant to navigate, blueprints becomes its
own later step decided on evidence rather than guessed at now.

**Also in P3:** the `artists.py` ↔ `canonical_detect.py` circular import (pre-spec §5), and the
`CLAUDE.md` accuracy question (pre-spec §4). Both are bounded; neither has been designed yet, and
P3's instruction subspec will settle them once P1's findings are in.

**Lint and formatting are skipped.** Measured 2026-08-16 across all 18 modules: **zero unused
imports, zero trailing whitespace**, 36 lines over 100 characters, quotes 6,362 double to 558
single (most of those apostrophes inside strings). The code is already essentially formatter-clean,
so a linter would find close to nothing — and `ruff format` over the existing tree would flatten
`git blame` on a codebase whose why-comments are its single most valuable asset. If a linter is
ever wanted, check-only mode; never a formatter over existing files.

---

## §6 What must survive P

The pre-spec's §6 and §7 are binding, not advisory, and a cleanup session's biggest risk is tidying
away something that is load-bearing. Restated in short — read the pre-spec for the reasoning:

- **The 24% comment/docstring density.** Overwhelmingly *why*, not *what*, and most record a
  failure that actually happened. A refactor that strips comments to "clean up" destroys the most
  valuable thing in the repo. Density should come out of P at that level or higher.
- **The stated module invariants** — `canonical.py` never touches `track`/`membership`,
  `snapshot.py` is read-only w.r.t. Spotify, `roundtrip.py` is the only module that writes to the
  library, `track.artists` is write-only, none of `canonical.py`'s functions commit. Moving code
  between modules is exactly what would violate one by accident. P2 should assert the ones that
  are assertable.
- **The one-TODO discipline** — one `TODO`, zero `FIXME`/`HACK`/`XXX`, no commented-out code. The
  xfail markers in §4 are the single sanctioned exception, and they are temporary.
- **`entity_link` centralization** — zero `url_for` bypasses today; keep it that way.
- **Deliberate non-problems**: the ~30 MB of `raw_json`, the spent one-offs in `scripts/`, the
  no-bundler frontend, the plain unstyled HTML. None of these are cleanup targets.

---

## §7 Workflow changes that land with P2

These are permanent and outlive P. They are the reason the suite does not rot.

- **`symr-verify` runs the suite, and it must pass before the finish-up.** This is the whole point:
  regression coverage that only runs when someone remembers is not coverage.
- **`symr-implement` runs it before handing off**, so breakage is caught in the session that caused
  it rather than the one after.
- **`CLAUDE.md`'s Commands section records the invocation verbatim.** It currently reads "Test /
  lint: none yet. Record them here verbatim once they exist."
- **Every future spec carries a Tests section.** Deliberate redundancy with the Verify gate rather
  than elegance: Verify is precisely where "looks good, finish up" happens, and a single point of
  failure is what this avoids. The section **may legitimately say "none — templates and JS only"**
  with a reason; a section that can honestly be empty is not box-ticking, whereas one that must
  always list something becomes ritual.

---

## §8 Process

**One branch, `feat/codebase-health-P`**, cut from `main` at `6e46101`. All three parts live on it.

**`git merge --ff-only` into `main` after each part**, not once at the end. P1's spec corrections
and P2's suite are each independently valuable on `main`, and P2 in particular exists to run in
every future Verify session — it should not be held hostage to P3 finishing.

**P2 and P3 each get a Verify session. P1 does not** — its real verification is Finn ruling on
each finding, and a formal Verify pass over that would be ceremony.

**Commits** follow the usual rules: Finn's name only, whole files, only when asked.

---

## §9 Where this landed against the pre-spec's §8

The pre-spec listed six things a plan session had to decide. For the record:

| §8 question | Decision |
|---|---|
| 1. Scope — all four findings or a subset? | All four, in one step, split into three parts — **plus a fifth workstream the pre-spec did not anticipate: the spec audit (P1)** |
| 2. Tests — how far? | All four tiers; JS out; pytest in `tests/`; hand-built fixtures, nothing real committed |
| 3. `create_app` — blueprints or extraction? | Extraction (§5) |
| 4. Behaviour-preserving only? | P3 strictly yes. **P1 explicitly not** — it will surface bugs and may fix some inline |
| 5. Lint/format? | Skipped, on measured evidence (§5) |
| 6. How is it verified? | Ephemeral byte-exact HTML golden snapshots over all 69 routes, plus the permanent suite (§4) |

The pre-spec's own framing of the tests question — "a dozen unit tests over the pure functions
probably would not violate KISS" — was **overtaken deliberately**. The scope here is larger than
that, on the grounds that the suite has two jobs (refactor safety *and* ongoing regression
prevention), and the second one only pays off if coverage is real.

---

## §10 Status

| part | instructions | state |
|---|---|---|
| **P1** — spec audit | `docs/codebase-health/P1_spec_audit.md` | **Done.** All 17 specs read against code, every one independently re-audited at least once beyond the original pass (batches 1a/1b/2/3/4, plus a full blind re-audit of every spec that hadn't produced a finding, plus the core scoring/grouping specs on top of that). **21 findings**, all ruled, in `docs/codebase-health/P1_findings.md` — the authoritative record of what P1 found and decided; this row is a pointer, not a summary. Ruling session ran 2026-08-17, all four batches (1 Ingest, 2 Grouping, 3 Scoring, 4 Read paths & UI) resolved in order. **All 17 specs stamped Audited**, plus the two canonical-tracks sub-specs findings touched (`grouping-engine.md`, `viewer-page.md`); `detection.md`/`review-ui.md` deliberately remain unstamped (read during the blind audit but produced no findings of their own — flagged unverified, not assumed clean, per P1-018). **Every finding resolved to either "amend spec" or "fix now" — none queued.** P2's `xfail(strict=True)` backlog is therefore empty; there is nothing to carry forward from P1 as a known, deferred bug. Real code fixes landed in `snapshot.py` (P1-004, the force-epoch discount — a live quota-spend bug — and the pull-resume force-epoch bookkeeping), `static/js/snapshot.js` (P1-005), `roundtrip.py` (P1-007: reconciliation state-transition + stop-mid-reconciliation bugs), `canonical.py` (P1-018: `mark_reviewed_pairs` now sorts its own pairs), `canonical_autogroup.py` (P1-013: stale validation-figure docstring), `app.py` (P1-014: unauthenticated `/api/*` gets JSON 401 not an HTML redirect, one shared `api_error()` helper for every `/api/*` error response, query string no longer dropped from the HTML error page; P1-016: album-artist dedup query), `entities.py` (P1-016: failed detail fetches now stamp "attempted" so they don't retry forever, artist image now picks largest by width not `images[0]`), `templates/entity_album.html`/`entity_group.html` (P1-016: the "first N of total" note and the Edit-link label), and `generations.py` (P1-015: `generation_spans()` now skips a mid-sequence empty generation instead of desyncing the preceding one's `ended_at` — found and fixed after the *originally claimed* `NULL`-crash in the same area didn't survive empirical verification). P1-013 also settled the audit's single largest behavioral-divergence finding: the `neutral`-suffix `shares_base_version` exclusion in `canonical_detect.py` is confirmed intentional, spec amended to match. `org-canvas.md` (P1-012, 17 differences, Symr's first-built feature and never touched since) got a consolidated "Corrections to current behavior" section rather than 17 scattered inline edits, specifically so a P2 test-writer has one concrete reference. **Merged to `main`** on 2026-08-19 (`--ff-only`, per the Verify finish-up). |
| **P2** — tests | `docs/codebase-health/P2_tests.md` | **Done and verified 2026-08-22. All six sessions landed, each written and verified and merged on its own, then P2 as a whole got the Verify pass `codebase-health-P.md` §8 promises it; 770 tests.** **Session 5 (coverage + workflow) added 39** and closed §9's remaining items. Its consolidated whole-suite pass is the first whole-repo measurement P2 took and the last one it takes (figures in the sealed file only, per `P2_tests.md` §7 — none appears here by design). It found two things a per-session pass could not. **First, session 1's one deferral did not come true**: it had recorded `roundtrip.py`'s three write-path gaps — `_match_substitutes` (the auto-alias-vs-flag decision), `set_manual_aliases` (the hand-driven write into `track_uri_alias`) and `_reconcile_batch` — as "worth filling" but predicted session 4's route tests would cover them "by definition", and asked for a re-measure. Re-measured, the module had moved eleven statements and all three were still entirely uncovered, on the one module that writes to the real Spotify library. The transferable lesson is about the deferral rather than about session 4: **"a later session will cover this" is a prediction, and an unrecorded prediction stops being anyone's** — the consolidated pass was the first thing to notice, five sessions late. **Second, the permanent route sweep had a structural blind spot invisible to its own completeness check**: `routes_catalog.py` compares itself against `app.url_map` both ways, but `catalog_rules()` keys on `(endpoint, method)` and a query string is neither, so every alternate render path behind a query param was unswept while the check that exists to catch exactly this reported complete. Session 4's Verify had hit one instance (`?generation=1`) and filled it as a one-off; it was systematic, at 39 of `app.py`'s 92 missed statements from that single cause. Fixed structurally — 21 variant cases now live in the shared catalog, so the golden capture gets them too — plus six semantic assertions over what the filtered pages actually render, since a variant that only proves non-5xx is §1's cheapest non-observation wearing a third hat. Session 5 also covered `snapshot._run_backfill`, which was entirely unreached and carries the **same load-bearing `except` ordering** session 2's Verify found in `backfill.py`: a per-item `except RateLimited: rollback; raise` above a generic `except Exception` that logs and continues, where swapping them turns a quota block into a per-item failure while the job keeps spending one request per track. Session 2 had called `backfill.py`'s absence "an inconsistency between the two sessions rather than a house style"; it was in a third place too — the arm exists in four jobs and was tested in two, now four. Plus `_run`'s circuit breaker (whose discriminating case is `F,F,T,F,F` — four failures in five batches, never three consecutive, the only sequence separating "consecutive" from "total"), the seven job-start routes' `already_running` 409 arm, and the entity pages' 404 branches and `/artist/<alias_id>` redirect that session 4 explicitly deferred here. **22 mutations; two survived the first pass and both were the session's own fixtures** — the evidence-guard test whose candidate was not itself evidence-free (P2-005's shape, a fourth time) and the unauthenticated-run test, which asserted an outcome both implementations produce. One finding, **P2-009**: `group_page`'s `row["tier"] != tier` clause cannot change the status code, because `canonical_group.id` is one id space across all four tiers so a release id can never appear as a `song_id` and the *next* guard 404s it anyway — verified empirically, ruled `unclear` on P2-004's asymmetry, and the reason its test asserts the error description rather than the status code. **§7's coverage argument now has evidence on both sides in one pass, which is why session 5's entry keeps both**: the gap-finder half found the query-string blind spot that nothing else could (you cannot mutate a branch no test reaches), while the not-a-gate half is the same table showing `entities.py`, `generations.py` and `grouping.py` at 100% with P2-008's six real gaps inside them. Session 5 also landed **§8's four workflow changes** and made P2's own source-comment convention mechanical: `test_every_test_declares_where_its_expected_value_came_from` scans every `def test_*` in `tests/` and fails naming the offenders. It found 35 tests carrying no source line — three of them leaning on a section header above the function rather than their own line — and it carries a companion test asserting the scan actually sees the suite, since a whole-suite scan asserting an empty list is the shape likeliest to silently stop testing anything. **P2's own Verify pass added 16 tests and one finding, P2-010**, from an independent 40-mutation pass against the session's 22: 26 killed, **14 survived, and all 14 were at the route layer** — every mutation aimed at session 5's new unit tests died. Its shape completes the arc P2-008 started. P2-009 had just established that a status code cannot discriminate where the guard after it returns the same one; the same reasoning was unapplied one route over, on `/callback`, where **deleting the OAuth state check outright passed all 754 tests** (so did dropping only its `not expected` disjunct, which alone is what refuses an unsolicited callback, since `None != None` is False) — the one route where `CLAUDE.md`'s security carve-out applies and the only one whose tests asserted nothing but non-5xx. Also: `/api/history/import` is the **eighth** job-start route where the new test covered seven, losing the one that needs a request body; eight of the 21 new query-string variants could ignore their own argument undetected, which is the session's structural fix landing half-done — proven to *respond*, not to *do*; and session 4's `?generation=1` test asserted a track name the ordinary playlist render also contains, so `if False:` on the branch passed it. **One of the fixes was itself un-failable on the first attempt** and is the most transferable thing in the finding: the upload-ordering test compared an `UPLOAD_ROOT` listing before and after, but `save_upload` names its folder from the clock, freezegun never moves it, and the root is redirected once per session — so every upload in the run lands on one constant path `os.makedirs(exist_ok=True)` quietly reuses. **A frozen clock turns a timestamped path into a constant**, and any test whose evidence is "a new file appeared" is blind to whatever already put one there. **Sessions 0 (infrastructure), 1 (Ingest), 2 (Grouping), 3 (Scoring) and 4 (Read paths & UI) preceded it.** **Session 4 added 108** (115 after Verify) over `entities.py` / `generations.py` / `grouping.py`'s org-canvas grouping / the `/api/*` error shape, covering P1-014's error-shape Test field, P1-015's `generation_spans` mid-sequence-empty-generation fix and tie-break, all four of P1-016's entity-page bugs, and P1-012's chain-fallback correction (the fixture whose nearest neighbor dead-ends but a farther one reaches a label). Landed the shared `routes_catalog.py` the permanent 69-route sweep and the golden-snapshot capture/compare tooling both read from (P2_tests.md §4.6 reassigned the golden tooling here from session 0, since capture needs concrete ids and P3's sampled-DB strategy, both session 4 knowledge), plus the `entity_link` centralization scan and the sweep's semantic assertions (§1's warning that "returned 200" is this session's cheapest non-observation). The session found **zero findings of its own** — a 12-item mutation pass against `entities.py`, `generations.py`, `app.py`'s error handling and `grouping.py` killed 11 of 12; the 12th (`resolve()`'s cycle-skip `continue` mutated to `break`) survived because every existing fixture had the visited candidate last in its list, fixed by nesting it one level deeper. **Verify's own 66-mutation pass found six more gaps and recorded them together as P2-008** — 52 killed, 14 survived, 9 of them real. Its shape is new and is the row's real content: sessions 1–3's un-failable tests were all inside a module's unit tests, where **session 4's cluster is mostly one layer up, at the seam between a well-tested function and its caller**. `entities.py` was at 100% lines and correct, and deleting `app.py`'s `if album["tracklist_pulled_at"] is None:` guard — so that *every* album-page view spends a Spotify request, against `entity-pages-K.md`'s hardest constraint — passed all 708 tests, because the stamp was asserted and the guard that reads it was not. Same for the artist page, and for `queue_wanted_uris`' route wiring (M §4.4's undo property), where both "only on first view" and "delete the call" survived. The other three: the canvas tie-break test could not fail (P2-005's shape — one label on the board, so backtracking reaches it either way), `generation_spans`' `MIN(added_at)` was unasserted (no fixture gave one generation two live memberships at different dates), and the spans-*ordering* test asserted something no fixture can test, since `generation.ordinal` is `INTEGER PRIMARY KEY` and therefore the rowid — a bare scan is ordinal-ordered however the rows went in, so dropping the `ORDER BY` passes and always will. All ten previously-surviving real mutants now die; 708 → 715; **no production code was wrong and no `xfail` is owed**. Verify also corrected the session's process note, which is the one thing here that was not test-shaped: a standalone (non-pytest) smoke test of the golden CLI ran against the real `symr.db` when an exported `SYMR_DB_PATH` didn't carry across shell invocations, and the session recorded it as "read-only, nothing modified" on the reasoning that `golden.py` only issues GETs. **That reasoning does not hold in Symr** — `create_app()` calls `db.init_db()`, which migrates, and a plain GET writes: the live database shows 9 `wanted_uri` rows and an `api_request` token-refresh row from that run. Nothing irreplaceable was touched and the rows were left in place (the album page re-queues them by design), but `tests/golden.py` is **the one thing in `tests/` that runs outside pytest**, so none of §4.1's four guard layers reach it — its `__main__` now refuses to start unless `SYMR_DB_PATH` is set *and* resolves away from the real database. **Session 3 added 89** over `scoring.py` — §4's version math (play weight, exposure/rate, saturation, the three buckets, shrinkage), §5's combiner and every query-time aggregator, §6's subtier blend, §7's two horizons, §9's materialization and the whole of `async-recompute-N.md`'s worker and read-time backstop — covering §5's scoring floor entire (P1-019's `_failed_fingerprint` both ways including the "no" decision, P1-021's negative subtier case, and `track_artist_role`'s VA fallback). It wrote **no infrastructure and touched no production code**, which is what the area was: `scoring.py` is pure computation over SQLite and the builders reach it directly. One finding of its own, **P2-006** — `docs/scoring/tuning_prototype.py`'s play-weight formula uses `MIN(x/NULLIF(duration,0), 1.0)`, which yields NULL for a NULL/0-duration track and so scores a version whose only evidence is such a play as never-played, the opposite of §4.2. `scoring.py` is correct; the *executable reference* §12 calls authoritative is the thing that is wrong, and Finn ruled leave-as-is (the prototype is a frozen record of the tuning session, not live tooling). **Verify ran 60 mutations** — the session ran none of its own — and re-derived every numeric literal in the suite independently from the spec's formulas, including §5.2's published p=1/p=3 table, to confirm none had been read off a run. **52 died; of the 8 survivors, 4 were real and 4 equivalent**, recorded together as **P2-007** and fixed in place. Its lesson is a *second* shape of un-failable test, distinct from session 2's: three of the four were not a fixture too simple but **an observation never made** — the `score` table's whole `recent` column was unasserted (so writing `all_time` into it passed all 596 tests), `tier_counts` was compared only against itself, and the worker's stop-on-failure test never queued the request a spin would need. Coverage is structurally blind to that class, since the producing code runs either way — the coverage pass had a single defensive line left to report about `scoring.py` while an entire materialized horizon sat unobserved (figures stay in the sealed file, per `P2_tests.md` §7). The four equivalent mutants are written down in the sealed coverage file so nobody re-hunts them — chief among them `album_scores`' `max(pad, 0)`, inert only because `[0.0] * -1` is `[]`, where the plausible wrong guard (`abs`) *is* caught. Session 1 added 169 over `snapshot.py` / `roundtrip.py` / `db.py` / `history_import.py` / `jobs.py` / `api_log.py`, covering §5's whole ingest floor bar one item deferred to session 2 (NULL-`total_tracks` albums settling permanently, which is `backfill.py`'s arithmetic). **Session 2 added 242** over `canonical.py` / `canonical_detect.py` / `canonical_autogroup.py` / `artists.py` / `backfill.py`, covering §5's whole grouping floor plus session 1's deferred item, and ran **31 mutations** against those modules to check each assertion could actually fail — the practice session 1's Verify introduced, moved forward into the writing session. It found **two tests that could not fail**, both fixture-shaped rather than assertion-shaped: a `_clean_explicit_pair` case whose suffixes both classified `recording` and so merged via `shares_base_version` regardless, and a `backfill` case that never exercised the "still unresolved" half of `queued(A)`. Session 2 also wrote the **17 route tests in `tests/test_canonical_routes.py`**, whose rules live in `app.py` itself: five for M §1's cross-component-only marking and P1-009's detection-off-the-page-load split, the rest for the canonical endpoints' `async-recompute-N.md` §4.1 call sites and the shared `api_error()` JSON shape. Deliberately *not* the permanent non-5xx sweep over all 69 routes, which stays session 4's. Two findings: **P2-003** (session 0's `make_group` pinned a representative on every group at every tier, which production never does, so every P1-008 tiebreak test asserted the pin instead of the election — fixed in place) and **P2-004** (`_cleanup_tier`'s stale-pin branch looks unreachable through `apply_partition`; ruled `unclear` and **explicitly not a P3 deletion candidate**, since a reading of an algorithm is weaker evidence than the caller search that condemned `all_candidate_groups`). **Verify added P2-005** and ran its own 23 mutations, independent of the session's 31: 22 died, and the survivor was the P1-010 tiebreak test — its ids were named so that the higher-scoring artist was *also* the alphabetically-first one, and `_canonical_of` falls through to id-ascending on a tie, so an implementation that never read a score passed the whole suite. Fixed by renaming the fixture so **both** wrong rules elect the loser. The lesson is sharper than P2-003's: **§5's floor wording was fully satisfied and the hole was still there**, because the floor named one wrong rule (credit count) where the code has two. Verify also filled the one real gap its coverage pass found — `backfill.py`'s `RateLimited` path, whose two `except` arms must stay in order or a quota block degrades into a per-album failure while the job keeps spending — bringing session 2 to 511 tests. Findings live in `docs/codebase-health/P2_findings.md` from here on — **P2-001** (`jobs.py`/`scoring.py` said "three jobs"; there are four) and **P2-002** (§5's restatement of P1-004 attached "nothing re-enters the work list" to the exclusion case, where a freshly-minted epoch makes it false), both ruled and closed. Session 1's own lesson — **two of its tests passed without discriminating**, invisibly to the runner — is written up where the later sessions will actually meet it: §2 above, and `P2_tests.md` §1/§2, not here. Session 1's Verify found two more of the same shape by mutation — breaking the code to check the test noticed — and fixed both, plus a `conftest.py` guard defect that would have blocked session 5 entirely: coverage.py stores its data in a SQLite file, which §4.1's connect guard refused, so `pytest --cov` passed the suite and then died on the report. **`P2_tests.md` §7 is revised accordingly** — coverage is measured in each session's *Verify* pass rather than only at session 5, on the reasoning that Verify writes no tests and so cannot be bent by a gap list, and the measurements are sealed in `docs/codebase-health/P2_coverage_SEALED.md`, **which sessions 2–4 must not open** and which is the only place any coverage figure appears. Verify additionally drove the guards' *failure* paths, not just their happy ones: an early project import aborts the whole run before any test executes, and the leaked-thread teardown fails the offending test by name. Session 0 landed **four** `symr.db` guard layers rather than §4.1's two — the env redirect and the resolved-path check it specifies, plus `sqlite3.connect` (and `sqlite3.dbapi2.connect`, a separate binding) refusing any path outside the temp dir, plus a redirect of `history_import.UPLOAD_ROOT`, which is the **one filesystem path `config.py` does not own** and resolves to the real GDPR exports. Outbound HTTP and sockets are blocked outright. The fake `sp` has **no append method by construction**, and does **not** register the loader playlist by default, so roundtrip's guard cannot pass for free. Two `conftest.py` lists that enumerate the codebase (modules from-importing `get_spotify_client`; the `JobStatus` singletons) are checked against the source by a test, since both drift silently. Six sequential sessions, split by **code area** rather than by test tier (infrastructure ▸ Ingest ▸ Grouping ▸ Scoring ▸ Read paths & UI ▸ coverage + workflow), each merged `--ff-only` on its own so the work can pause between them. §5 of that file is P2's test floor, built from the 13 P1 findings carrying a real `Test:` target — `_diff_playlist_tracks` (P1-002) and the `shares_base_version`/`neutral` rule (P1-013) are the two largest. Three decisions were taken during planning that this file's §4 did not settle: **coverage is measured once at the very end, never during the writing passes** (a coverage map in view optimises for executing lines, and the cheapest way to execute a line is exactly the tautological characterization test §2 warns about) and carries **no numeric gate**; **freezegun** rather than per-site monkeypatching, on the measured fact that all 8 SQL-side `datetime('now')` uses are write-side only, so a read-only page render can never pick up an unfrozen timestamp; and **no Haiku delegation tier** — the scarce resource is Opus quota, so the Opus→Sonnet move carries essentially all the saving, and §3's landmine argument holds below that. P2 also gains a live-findings convention (`P2_notes/<area>-<n>.md` per subagent, consolidated into `P2_findings.md` with `P2-###` ids), so an inconsistency found mid-session no longer needs the session to end to be reported. **One correction to §4 found while planning:** it says conftest sets dummy `SPOTIFY_CLIENT_ID` / secret, but `config.py:8-10` reads **three** required vars at import — `SPOTIFY_REDIRECT_URI` too — so a conftest built from §4 verbatim would `KeyError` on first run. `P2_tests.md` §4.1 carries the corrected list. |
| **P3** — refactor | `docs/codebase-health/P3_refactor.md` | **Planned 2026-08-22, not yet started.** Three sessions on `feat/codebase-health-P`: (1) build the golden harness, prove the determinism gate, capture, then the small independent fixes; (2) the six entity pages; (3) the three dev pages plus close-out. **Nine views extracted, ~583 sloc**, taking `create_app` 1,620 → ~1,130 and `app.py`'s `conn.execute` **42 → 12** — that second number is the one to judge P3 by, since 69 routes at ~10 lines each is a ~700-line floor extraction cannot go below. Decisions taken while planning that §4/§5 did not settle: the golden DB is a **plain copy** of `symr.db`, not the sampled one P2 imagined (an FK-consistent subset across ~20 tables is real engineering for something P3 deletes, and a snapshot of a page reading "no results" proves nothing); the passes run **under pytest** rather than through `golden.py`'s `__main__`, since `conftest.py` already supplies the frozen clock, blocked sockets and connect guard the CLI has none of; and **capture happens exactly once**, because re-capturing mid-way promotes any regression already introduced into the new baseline. §3.2 names the four things that must be dead before a byte diff means anything — chief among them the async scoring worker, which lands mid-pass and changes every page rendered after it. `canonical_index` is the one view with no clean home and the reasoning is in §4.1.1: it goes to `canonical_detect.py` (which already owns `canonical_page_groups` and `filter_groups` for that same page) with the two autogroup-status values left in the route, because `canonical_autogroup` imports `canonical_detect` and pulling them in would create a **new** cycle in the step whose other job is removing one. Two additions to the four findings: `_board_state`'s and the two `snapshot` sites' `SELECT *` (§4.5 — the riskiest change in P3, since `_board_state` `dict(row)`s straight into an API payload, and exactly what golden snapshots exist to catch), and a **narrow module-list check** for `CLAUDE.md` rather than the numeric-claim test that was considered and rejected in §4.3 for greping English that gets reworded. A seven-category dead-code sweep re-run 2026-08-22 found **nothing beyond `all_candidate_groups`** — zero unused imports, orphan templates, unused macros, unreferenced JS, unreferenced config settings or unused CSS classes, and the one sanctioned `TODO` — so P3 carries no cleanup backlog past what P1 already named. |

Update this table as each part starts, lands and merges, and add pointers to any findings
documents produced along the way.

**P1 batching, as actually run** (`P1_spec_audit.md` §3's four batches, further split): 1a
(`track-metadata-A`, `snapshot.md`, `partial-pulls-J`) and 1b (`play-history-C`,
`foreign-roundtrip-D`) were each their own pass; batches 2/3/4 ran as specced. A side effect of
resolving one cross-batch question (which spec introduced `jobs.py`'s single-lock design —
`foreign-roundtrip-D.md` §2) produced `docs/Planning/roadmap.md`'s new **Spec index** section,
mapping all 17 specs to the code they own — worth checking before assuming a piece of behavior
is undocumented anywhere.
