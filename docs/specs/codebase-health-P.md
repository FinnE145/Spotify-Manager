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
| **P2** — tests | *not yet written* | Unblocked — P1's rulings have landed. Not started; the per-session instructions are still to be written. |
| **P3** — refactor | *not yet written* | Blocked on P2 |

Update this table as each part starts, lands and merges, and add pointers to any findings
documents produced along the way.

**P1 batching, as actually run** (`P1_spec_audit.md` §3's four batches, further split): 1a
(`track-metadata-A`, `snapshot.md`, `partial-pulls-J`) and 1b (`play-history-C`,
`foreign-roundtrip-D`) were each their own pass; batches 2/3/4 ran as specced. A side effect of
resolving one cross-batch question (which spec introduced `jobs.py`'s single-lock design —
`foreign-roundtrip-D.md` §2) produced `docs/Planning/roadmap.md`'s new **Spec index** section,
mapping all 17 specs to the code they own — worth checking before assuming a piece of behavior
is undocumented anywhere.
