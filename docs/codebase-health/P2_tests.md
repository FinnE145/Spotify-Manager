# P2 — Tests

**Instructions for the sessions that run P2.** Read `docs/specs/codebase-health-P.md` first — its
§0 explains why this file is written as instructions rather than as a spec, and §2–§4 and §7 hold
the reasoning behind everything below. **Do not re-litigate those decisions here.** In particular
§2 (characterization vs specification), §3 (the delegation model), §4 (test infrastructure) and §7
(the workflow changes) are settled; this file is what turns them into sessions.

**Where this came from.** The planning session on 2026-08-19, written from what P1 actually found
(`P1_findings.md`, 21 findings, all ruled) rather than from the shape guessed at during P's
original planning.

**What P1 left for P2.** The `xfail(strict=True)` backlog is **empty** — every P1 finding resolved
to "amend spec" or "fix now", none queued. So P2 starts with no inherited debt, and the 13 findings
carrying a real `Test:` target (§5 below) are a floor to build from, not a burden to work off.

---

## §1 The goal

A pytest suite in `tests/` that (a) makes P3's refactor verifiable and (b) stops ordinary feature
work quietly breaking old code — plus the workflow changes that keep it alive after P ends.

The thing to hold onto the whole way through is `codebase-health-P.md` §2: **a test whose expected
value was obtained by running the code tests nothing.** It is the path of least resistance and it
silently ratifies bugs. Every test declares which kind it is, in a one-line comment naming its
source: the spec clause it derives from, or `characterization`. That comment is not decoration — it
is what makes review a scan of (assertion, cited clause) pairs, and during P3 it is what says at a
glance which tests may legitimately be regenerated and which must never be.

**All 17 specs now carry an `Audited 2026-08-17` header line.** That line is the licence to derive
assertions from a spec. `docs/canonical-tracks/detection.md` and `review-ui.md` deliberately do
**not** carry it — they were read during P1's blind audit but produced no findings of their own, so
they are flagged unverified rather than assumed clean. **Do not derive specification tests from
those two.** Characterization is fine there.

---

## §2 What these sessions do, and what they must not do

**Do:** build the infrastructure, write the tests, record what they find.

**Must not:**

- **Fix a bug.** P2 records; it does not fix. A bug found here gets an entry in `P2_findings.md`
  (§6) and a test asserting the *correct* behaviour marked `@pytest.mark.xfail(strict=True)` citing
  that entry. `strict=True` means the day someone fixes it, the unexpected pass fails loudly and
  says "remove this marker". **The findings doc and the xfail set must match exactly** — a bug in
  one and not the other is the failure this convention exists to prevent.
- **Obtain an expected value by running the code and calling it a specification test.** See §1.
- **Weaken a test to make it pass.** If a test that should pass doesn't, that is a finding.
- **Touch `symr.db`.** See §4.1. This is the security-grade part of P.
- **Look at coverage before §7.** Deliberate — see there.

---

## §3 The sessions

Six, sequential, **split by code area rather than by test tier**. A session that has just read
`snapshot.py` deeply writes its pure-function, DB-bound *and* Spotify-loop tests while that context
is loaded; splitting by tier would re-read the same module in three sessions and pay for it three
times. It also matches P1's batches, so the findings doc is already organised the way the work is.

| # | session | primary code |
|---|---|---|
| **0** | Infrastructure | `tests/conftest.py`, builders, fake `sp` |
| **1** | Ingest | `snapshot.py`, `db.py`, `roundtrip.py`, `history_import.py`, `api_log.py`, `jobs.py` |
| **2** | Grouping | `canonical.py`, `canonical_detect.py`, `canonical_autogroup.py`, `artists.py`, `backfill.py` |
| **3** | Scoring | `scoring.py` |
| **4** | Read paths & UI | `entities.py`, `generations.py`, `grouping.py`, `app.py` routes, `templates/` |
| **5** | Coverage pass + workflow changes | `requirements.txt`, `CLAUDE.md`, the three phase skills |

**Session 0 is non-delegable and comes first.** Everything downstream is built on it and a subtle
bug there would be invisible in every test that uses it.

**Start session 1 with Ingest** for the same reason P1 did: it is the code that writes `membership`
and the only code that writes to Spotify, and it carries P2's single largest target
(`_diff_playlist_tracks`).

**Each session merges `--ff-only` into `main` on its own** (`codebase-health-P.md` §8). That is not
bookkeeping — it is what makes it safe to stop for a week between sessions.

### Budget discipline

P1's cost is the lesson: four rounds of sixteen Opus subagents exhausted a weekly quota. P2 is
explicitly shaped to avoid repeating that.

- **Opus does infrastructure and judgment. Sonnet enumerates and fills.** Per
  `codebase-health-P.md` §3 — Sonnet may write specification tests **only** with the relevant spec
  clause quoted into its prompt verbatim, plus a standing instruction to **report and stop** rather
  than guess when the clause does not settle the case.
- **No Haiku tier.** Considered and rejected: the scarce resource is Opus quota, so the Opus→Sonnet
  move is where essentially all the saving is, and §3's argument holds — a wrongly-asserted test is
  worse than a missing one, because it gets "fixed" later by changing correct code. Work that feels
  Haiku-easy ("list every route and its methods") is a `grep` the session runs itself for free.
- **3–4 concurrent subagents, not 16.**
- **No blind re-audit rounds.** That was P1's expensive pattern and it was right there, because
  nothing else could find those bugs. P2's equivalent safety net is the suite itself, which
  re-runs for free forever.

---

## §4 Infrastructure — session 0

Opus writes all of this. It is the part that must simply be correct.

### 4.1 The `symr.db` guard — belt and braces, deliberately

`db.py:8` and `scoring.py:34` both do `from config import DB_PATH`, a **from-import**, so the path
is bound at import time. Setting `SYMR_DB_PATH` after importing anything is a silent no-op and the
suite runs against the real 93 MB `symr.db` — seven years of streaming history, 461+ hand-reviewed
grouping pairs, 37 generations of curation. **None of it is reconstructible and none of it is
re-suppliable by Spotify.**

So, at the very top of `conftest.py`, before any project import:

1. Set `SYMR_DB_PATH` to a temp path.
2. Set dummy credentials — **all three** of `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` and
   `SPOTIFY_REDIRECT_URI` (`config.py:8-10`, verified 2026-08-19). Each is read at import and
   raises `KeyError` without it, so missing the third fails just as hard as missing the first.
   Dummies additionally make it *structurally impossible* for a test to build a real authed client.
3. **Hard-assert** that the resolved `config.DB_PATH` is a temp path, and refuse to run the suite
   at all if it is not.

Step 3 is not redundant with step 1. Step 1 is the mechanism; step 3 is what catches the day
someone changes the mechanism.

### 4.2 Fixture data

**Nothing real is committed.** Hand-built builders — `make_track()`, `make_playlist()`,
`make_album()`, `make_artist()`, `make_membership()`, `make_play()` — producing the tiny purposeful
rows unit tests want and the ~20-track / few-playlist shape route tests want. `db.init_db()`
against a temp path already builds the full schema plus views, so an empty DB is free.

Builders take keyword overrides and default everything else, so a test states only what it is
about. A test whose setup is twenty lines of irrelevant scaffolding is a test nobody will read.

### 4.3 The clock — freezegun

Added to `requirements.txt`. Four direct `datetime.now(timezone.utc)` sites feed rendered output —
`entities.py:30` (30d/7d windows), `scoring.py:426` (the 90-day `recent` horizon), `api_log.py:110`
(the `/dev` rolling counts), `history_import.py:88` (folder naming) — on top of `jobs.now_iso()`,
which covers most writes.

freezegun rather than monkeypatching those sites: each module does `from datetime import datetime`,
so the name is already bound inside it. Patching call sites means finding all of them and
re-finding them every time a fifth appears; freezegun patches the class and reaches all of them
plus any new one for free.

**Known limit, and it is fine:** freezegun does not reach SQLite's `datetime('now')`. Measured
2026-08-19 — all 8 of those uses (`db.py` ×4, `snapshot.py` ×3, `roundtrip.py` ×1) are **write-side
only**, in `INSERT` values and column `DEFAULT`s; none appear in a read query. So a read-only page
render can never pick up an unfrozen timestamp, which is what golden snapshots need. A test that
writes a row and then asserts on its `created_at` **does** see a real timestamp — write those
assertions against `jobs.now_iso()`-populated columns, or don't assert on the value.

Related, worth knowing before asserting on any timestamp: the DB holds **two formats**. SQL-side
`datetime('now')` writes naive UTC; `jobs.now_iso()` writes an explicit `Z` suffix, which is what
`static/js/format.js` parses (see the comment at `db.py:162`).

### 4.4 The fake Spotify client

A fake `sp` object covering **only the endpoints the job loops actually call** — not a general
spotipy mock. It earns its keep on `roundtrip.py`, whose replace-never-append and
read-as-a-bag-never-a-sequence invariants were both learned the hard way and are the
highest-corruption-risk logic in the tree.

It must be able to express the failure modes those loops exist to handle: a 429 with a
`Retry-After`, a 400 on a batch, a page that returns substituted tracks carrying `linked_from`, and
a page that returns substitutes carrying nothing.

### 4.5 Background threads

Jobs spawn real threads. A route test that starts a job must run it inline or join it before
asserting — otherwise it leaks a thread into the next test and leaves `jobs._active` claimed, and
the failure surfaces somewhere unrelated. Provide one fixture that does this properly and have
every job-touching test use it rather than each solving it again.

### 4.6 Route tests, two layers

Per `codebase-health-P.md` §4:

- **Permanent** — every one of the 69 routes returns non-5xx, plus a handful of semantic assertions
  (this page contains this track's name; this count renders with a thousands separator). Robust to
  template edits, useful forever. Auth bypassed by monkeypatching `get_spotify_client`.
  **POST routes are included**, job-starting ones among them: they cannot reach the real app given
  §4.1 plus the fake `sp`, and §4.5 handles the threads.
- **Ephemeral** — byte-exact HTML golden snapshots, captured immediately before P3, diffed after,
  then deleted. **The capture/compare tooling is committed; the snapshots are not.** Build the
  tooling in P2; capture nothing. A permanently-maintained byte-exact suite fails on nearly every
  feature branch for legitimate reasons, and a test that routinely fails legitimately gets
  regenerated reflexively — at which point it protects nothing.

**The gitignored sampled DB is P3's, not P2's.** It exists only so snapshot capture renders real
pages — a byte-exact snapshot of a page rendering "no results" proves nothing. The ordinary suite
runs on §4.2's builders and needs it not at all. Its committed build script is written on P3's
first day.

---

## §5 What to test, per area

P1's `Test:` fields, consolidated. **This is a floor, not a ceiling** — it is what the audit
happened to surface, not a coverage plan. Where a session sees an obvious gap next to one of these,
fill it.

`db.py`'s `_migrate` / `_ensure_views` are **in scope** (session 1). A temp DB always gets the
fresh schema, so migration paths never execute unless a test deliberately builds an old schema and
migrates it — and these are the functions with the power to damage `symr.db`.

### Session 1 — Ingest

- **`_diff_playlist_tracks` — P2's single largest target (P1-002).** Both kinds: characterization
  for refactor safety, specification from the amended `snapshot.md`. Cases: exact-`added_at` match
  surviving a position change; the identity-pass-inverts-fallback interaction (newest survives via
  exact match while an older unmatched copy departs); ambiguous same-`added_at` duplicates and their
  position tie-break; NULL `added_at` on one or more stored rows.
- **`_resolve_force_epoch`'s failing-playlist discount (P1-004)** — a `last_pull_error`-carrying,
  not-excluded playlist that would otherwise satisfy `_is_full_pull_target` must keep
  `pull_force_epoch` alive while every other target is done; excluding it must then let the epoch
  complete with no previously-captured playlist re-entering the work list. Cover the
  never-seen-before case too.
- **`roundtrip._run_batch` (P1-007)** — an all-missing batch whose read-back returns a full page of
  unlabelled substitutes must record every uri as `not_returned` *and* still count as failed toward
  the circuit breaker; an all-missing batch with a genuinely short/empty read-back records nothing
  and also fails. Plus a partial-missing batch.
- **Round-trip queue partition (P1-017)** — the three counts sum to `remaining_uris` in the
  **unmuted** case. NULL-`total_tracks` albums are currently silently and permanently settled.
- **`db.py` migrations** — build an old schema, migrate, assert the result.
- Optional, low value: the end-of-run status string (P1-005), including the no-work-list-yet
  fallback and the stale-vs-remaining divergence on a full pull.

### Session 2 — Grouping

- **`shares_base_version` / `neutral` and `_clean_explicit_pair` (P1-013)** — the clearest
  specification target in the whole audit, and the largest behavioural divergence P1 found. The
  `neutral` exclusion is **confirmed intentional**; the spec was amended to match. Let the test
  double as the record of that decision.
- **`mark_reviewed_pairs` ordering (P1-018)** — an unsorted pair still stores as `(min, max)`.
- **Representative-track tiebreak (P1-008)** — `scoring-H.md` §11.3 is the source of truth: a
  lower-membership, higher-score track beats a higher-membership, lower-score one. Cover the
  degraded fallback where an empty `score` table collapses to the old tail-only rule.
- **Canonical-artist tiebreak (P1-010)** — where the highest-`track_artist`-count id and the
  highest-`all_time`-score id differ, the score wins. **Build the fixture from two unmerged ids**,
  or it does not test what it means to.
- **The cross-listing split (P1-009)** — `/api/canonical/cross/listing` returns what the page
  needs, and the synchronous page load no longer pays detection's cost.
- **Module invariants** (`codebase-health-P.md` §6), which are assertable here: `canonical.py`
  never touches `track`/`membership`, and none of its functions commit.

### Session 3 — Scoring

- **`_failed_fingerprint` (P1-019)** — a recompute failure suppresses auto-retry on that exact
  fingerprint until a fresh commit moves it or the manual button is called. Transient and repeatable
  failures behave identically; the spec no longer distinguishes them. Also: `/dev/generations/confirm`
  does **not** recompute on a "no" decision.
- **Subtier blend (P1-021)** — the blend's own-score component must **not** be shrunk toward a
  bucket baseline. Write the negative case; the positive is what gets accidentally reintroduced.
- **`track_artist_role`'s all-credits-primary fallback** on a track with zero album-artist-matching
  credits.

### Session 4 — Read paths & UI

- **`/api/*` error shape (P1-014)** — every error response, from both the generic
  `abort()`/exception path and the hand-written precondition checks, has exactly `error` and
  `detail` keys. An unauthenticated `/api/*` request gets JSON 401, not a redirect. A request with
  a query string that errors shows the full query string in the HTML error page's request line.
- **`generation_spans()` (P1-015)** — a mid-sequence generation with zero live memberships must not
  desync the preceding generation's `ended_at` from the next real generation's `started_at`. Also
  the tie-break-picks-earliest-run behaviour and `generations(conn, tier=...)`.
- **The four entity-page bugs (P1-016)**, each cheap and clean, written against the fixed
  behaviour: a fully-backfilled album renders no "first 50" note; a credit held under both an alias
  and its canonical id renders once; artist image selection picks max width; a failed detail fetch
  does not retry on the next page view. Plus the Edit link resolving to the deep-linked viewer.
- **Canvas chain-grouping fallback (P1-012)** — does a dead-ending nearest-neighbour search further
  via the next-nearest candidate, or fall straight to Ungrouped? Characterization; it is the one
  canvas item with real behavioural stakes. `## Ungrouped`'s unconditional render and the tray's
  alphabetical sort are cheap to pin alongside.
- **`entity_link` centralization** — zero `url_for` bypasses outside `_macros.html` today. Assert
  it stays that way.

---

## §6 Recording what the sessions find

P2 will find inconsistencies P1 missed — P1's own experience was that every additional pass found
more, and it never reached diminishing returns. The point of this convention is that a finding does
not require exiting and restarting a session to report.

**Subagents append to their own file:** `docs/codebase-health/P2_notes/<area>-<n>.md`. One file per
subagent, never a shared one — concurrent appends to a single file mangle it.

**The session consolidates before it ends** into `docs/codebase-health/P2_findings.md`, with ids
`P2-001`, `P2-002`, … — stable, never reused, never renumbered, same as P1. Same entry template as
`P1_spec_audit.md` §4, and the same four classifications (`spec-stale`, `code-wrong`,
`underspecified`, `unclear`).

Consolidating is not transcription. A subagent note is a candidate; the session verifies it against
the code before it becomes a finding, exactly as P1 did. **A fabricated or misread finding costs
Finn a ruling on something that was never wrong.**

Findings are **presented to Finn, not acted on** — numbered, reasoning above the list, one decision
per line, per `CLAUDE.md`. Where he rules "fix now", fix it and note it. Otherwise it stays an
`xfail(strict=True)` citing its id.

`P2_notes/` is working material and can be deleted once consolidated. `P2_findings.md` is
permanent.

---

## §7 Coverage — session 5 only, and deliberately last

`pytest-cov` is a dependency from session 0, but **no session looks at coverage until session 5.**

The reason is the one that governs everything else here: writing tests while watching a coverage
map optimises for executing lines, and the cheapest way to execute a line is a characterization
test asserting what it already does. That is §2's failure at small scale. Writing all four area
sessions blind, then measuring, gives a coverage number that means something — it reports what an
honest pass actually reached.

**Coverage is a gap-finder, not a gate.** "These 47 branches in `canonical_detect.py` have never
been executed" is a real, cheap, mechanical list, and it is exactly the kind of gap hand
enumeration failed to produce in P1 without burning a quota. But there is **no numeric threshold**,
because a suite of `assert True` reaches 100% and a target rewards the tests worth least.

Session 5 measures, reports the gaps to Finn, and he decides whether any are worth filling — which
may be its own session or may be nothing.

---

## §8 The workflow changes — session 5, final commit

`codebase-health-P.md` §7. These are permanent, outlive P, and are the reason the suite does not
rot. They land **after** the suite exists — telling `symr-implement` to run a suite that isn't
written yet breaks the next session.

- **`symr-verify` runs the suite, and it must pass before the finish-up.** Verify is precisely
  where "looks good, finish up" happens, so this is the load-bearing one.
- **`symr-implement` runs it before handing off**, so breakage is caught in the session that caused
  it rather than the one after.
- **`CLAUDE.md`'s Commands section** replaces the current literal placeholder — "Test / lint: none
  yet. Record them here verbatim once they exist" — with **`venv/bin/python -m pytest`**. Same
  shape as the documented `venv/bin/python app.py`, and `-m` pins the venv interpreter rather than
  relying on a shim.
- **`symr-plan` gains: every spec carries a Tests section.** Deliberately redundant with the Verify
  gate rather than elegant — a single point of failure is what this avoids. The section **may
  legitimately say "none — templates and JS only"** with a reason. A section that can honestly be
  empty is not box-ticking; one that must always list something becomes ritual.

---

## §9 Done

P2 is finished when:

1. `tests/` runs green via `venv/bin/python -m pytest`, with §4.1's guard in place and asserting.
2. All four areas are covered to §5's floor, every test carrying its one-line source comment.
3. The golden-snapshot capture/compare tooling is committed, with no snapshots captured.
4. Every P2 finding is in `P2_findings.md`, ruled by Finn, and every unfixed one has a matching
   `xfail(strict=True)` — **the two sets match exactly**.
5. Coverage has been measured once, reported, and Finn has ruled on the gaps.
6. §8's four workflow changes have landed.
7. `codebase-health-P.md` §10's status table is updated, pointing at `P2_findings.md`.
8. Each session merged `--ff-only` into `main` as it landed (`codebase-health-P.md` §8).

**P2 gets a Verify session** (`codebase-health-P.md` §8) — unlike P1, whose verification was Finn's
rulings. Then write `docs/codebase-health/P3_refactor.md` and start P3.
