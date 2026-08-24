# S — whole-codebase mutation sweep

Step S of `docs/Planning/roadmap.md`; spec at `docs/specs/mutation-sweep-S.md`.
Run 2026-08-24 against `feat/mutation-sweep-S` rebased onto `main` at `35abdb8`.

The bounded precedent is `post_P_sweep.md` — 372 mutants over the four modules P
worked on directly, 364 killed, three of them at 100%. **This run is the other
seventeen modules, and it does not look like that one.**

---

## 1. Method

By reference: `post_P_sweep.md` §1 holds the method, the operator list and the
`.pyc` trap, and none of it is re-derived here. What S changed:

- **The tooling is committed**, at `scripts/mutation/` — `generate.py`,
  `sweep.py`, `verify.py`. The bounded run's scripts survived only in a session
  scratchpad under `/private/tmp`, which made this session's first ten minutes
  an archaeology exercise. That is the half of this step most likely to pay off
  again.
- **A SQL pass** (spec §2.2), disjoint from the Python one, over string literals
  holding a SQL keyword. The bounded run mutated no SQL at all.
- **`broken` extended to SQL** (spec §3.2): a SQLite `OperationalError` with no
  assertion failure behind it is an invalid query, not a kill. 39 mutants were
  excluded on that rule, 23 of them SQL.
- **Negative return codes are `crashed` up front**, not read as kills.
- **A green-baseline pre-flight**, which is new and which §1.1's trap did not
  cover. See §1.1 below — it is the reason this document reports 68% and not
  the 89% the first run claimed.

### 1.1 The first run was wrong, and the way it was wrong is the lesson

The first full run reported **892 caught of 997, 100% on twelve of seventeen
modules** — including `config.py` and `serve.py`, which a smoke run an hour
earlier had measured at **0%**, because nothing asserts them at all.

It was not a result. Jobs were assigned to workers round-robin by index and
mapped across a pool of the same size, which does not pin one directory to one
thread: the pool hands whichever thread is free the next job, so two jobs for
one copy overlap as soon as mutants take unequal time. The second thread read
the first's *mutated* file as its "original" and restored that at the end,
welding the mutant into the copy. Every later run in that worker was red, and
**a red run is indistinguishable from a kill**.

Three fixes, and the order matters — only the third would have caught it:

1. One thread and one queue per worker directory, so apply/run/restore is atomic.
2. Restore copies from the pristine proto tree, never from a string read at entry.
3. **The suite must be GREEN in an unmutated copy before any mutant runs.**

This failure mode is silent and its symptom is a *near-perfect kill rate* —
the single wrong answer nobody thinks to question. Twelve seconds of pre-flight
turns it into a refusal. It is `post_P_sweep.md` §1.1's trap in a new costume:
the tool lying in the flattering direction.

---

## 2. Result

**Pre-fix measurement. 997 mutants generated, 39 `broken` excluded, 958 scored.**

**651 killed — 68.0%.** 307 survivors, 3 timeouts (counted as caught, listed in
§2.1).

| module | generated | broken | scored | survivors (py/sql) | kill rate |
|---|---:|---:|---:|---:|---:|
| `app.py` | 185 | 0 | 185 | 86 (77/9) | 53.5% |
| `history_import.py` | 107 | 7 | 100 | 52 (27/25) | 48.0% |
| `entities.py` | 159 | 7 | 152 | 38 (25/13) | 75.0% |
| `canonical_detect.py` | 156 | 0 | 156 | 37 (22/15) | 76.3% |
| `db.py` | 94 | 20 | 74 | 18 (1/17) | 75.7% |
| `scrobble.py` | 76 | 1 | 75 | 18 (5/13) | 76.0% |
| `jobs.py` | 35 | 0 | 35 | 10 (10/0) | 71.4% |
| `backfill.py` | 49 | 0 | 49 | 9 (8/1) | 81.6% |
| `artists.py` | 26 | 1 | 25 | 8 (4/4) | 68.0% |
| `spotify_client.py` | 8 | 0 | 8 | 8 (8/0) | 0.0% |
| `api_log.py` | 14 | 0 | 14 | 7 (6/1) | 50.0% |
| `canonical_autogroup.py` | 17 | 1 | 16 | 6 (4/2) | 62.5% |
| `config.py` | 4 | 0 | 4 | 4 (4/0) | 0.0% |
| `generations.py` | 47 | 2 | 45 | 2 (2/0) | 95.6% |
| `grouping.py` | 16 | 0 | 16 | 2 (2/0) | 87.5% |
| `serve.py` | 2 | 0 | 2 | 2 (2/0) | 0.0% |
| `normalize.py` | 2 | 0 | 2 | 0 (0/0) | 100.0% |
| **total** | **997** | **39** | **958** | **307** | **68.0%** |

Five of these reproduce independent single-module runs exactly (`backfill.py`
81.6%, `grouping.py` 87.5%, `config.py` 0%, `serve.py` 0%, `normalize.py` 100%),
which is the cross-check that the corrected runner is measuring what it claims.

### 2.1 Timeouts

Three, counted as caught and reported separately per spec §3.2:
`app.py:1113 [eq]`, `canonical_detect.py:262 [ne]`, `jobs.py:159 [or]`.

### 2.2 The SQL pass earned its place

274 SQL mutants generated, 251 scored, **100 survivors — a 40% survival rate
against the Python pass's 29%**. Proportionally more gaps per mutant, in a
region the bounded run's operator set could not reach at all. Spec §0 called SQL
"the substance of the step" and the measurement agrees.

`db.py` is the sharpest instance: 18 survivors, **17 of them SQL**. Its Python
half is almost perfectly covered and its queries are almost entirely unasserted.

### 2.3 What the totals do and do not say

The bounded run's 97.8% was never a tree-wide baseline — it measured the four
modules P had *just finished* working on. 68% is the first honest reading of
everything else, and the two numbers should not be subtracted from one another.

What can be compared fairly is `scrobble.py`: **76.0%**, 18 survivors. That is
step R, the newest module in the tree, written with every P-era convention in
force and shipped with a 1092-line test file of its own. See §5.

### 2.4 The corrected re-run — 74.1%, and why it does not replace §2

Persisting the survivor list surfaced a **third generator bug** (after §1.1's
race and §3.1's SQL comments): Python 3.12's PEP 701 tokenizes an f-string as
`FSTRING_START`/`FSTRING_MIDDLE`/`FSTRING_END` rather than one `STRING`, so
both passes saw straight through to the literal text. The Python pass mutated
*inside* f-strings (`f"<h1>Error {code}."` → `f"<=h1>..."`, 11 mutants, 6 of
them sitting in the survivor list as junk) and the SQL pass **missed every
f-string query in the tree** — ~49 lines across six modules, all the
`IN ({placeholders})` builders. That is a hole in the measurement, not an
imprecision: 34 SQL mutants had never been executed at all.

Fixed, and the sweep re-run: **1018 mutants, 44 broken, 974 scored, 722 killed
— 74.1%**, 252 survivors.

| module | kill rate | Δ vs §2 |
|---|---:|---|
| `serve.py` | 100.0% | +100.0 |
| `backfill.py` | 85.5% | +3.9 |
| `generations.py` | 88.2% | −7.4 |
| `db.py` | 81.7% | +6.0 |
| `entities.py` | 81.6% | +6.6 |
| `canonical_detect.py` | 78.3% | +2.0 |
| `scrobble.py` | 76.0% | 0.0 |
| `jobs.py` | 71.4% | 0.0 |
| `app.py` | 69.5% | +16.0 |
| `canonical_autogroup.py` | 68.4% | +5.9 |
| `artists.py` | 68.0% | 0.0 |
| `history_import.py` | 56.0% | +8.0 |
| `api_log.py` | 50.0% | 0.0 |
| `spotify_client.py` | 12.5% | +12.5 |
| `config.py` | 0.0% | 0.0 |
| `grouping.py` | 87.5% | 0.0 |
| `normalize.py` | 100.0% | 0.0 |

**§2 stays as the headline and this does not replace it.** The two numbers
measure different trees: §2 is the pre-fix, pre-test measurement, and by the
time this ran the suite had gained the 39 tests of §4. The rise is mostly
those tests doing their job — `serve.py` 0→100, `app.py` +16.0 — mixed with
the operator-set correction. Neither number is wrong; they answer different
questions, and subtracting one from the other means nothing.

`generations.py` moving **down** 7.4 points is the one figure worth reading:
no test of ours touched it, so that is the f-string SQL pass finding real
survivors the original run could not generate. Same for `api_log.py` and
`canonical_autogroup.py`'s new SQL survivors.

---

## 3. The survivors, and what each turned out to be

Triage runs mechanical-classes-first, to shrink 307 before anything is
delegated. This section is the committed ledger; the classes below are settled,
the remainder is not yet.

### 3.1 Two classes are equivalent by construction — 15 survivors

- **`EXISTS (SELECT 1 …)` → `SELECT 2`, 11 survivors.** The constant inside an
  `EXISTS` subquery is meaningless in SQL; any value behaves identically. No
  test can kill these and none should be written.
- **Digits inside a SQL `--` comment, 4 survivors.** A generator defect, not a
  gap: `sql_string_ranges` masked nothing inside an eligible string, so the
  numeric operator mutated the prose in comments — four of them landed on
  "6,070 membership-less round-tripped tracks". **Fixed**: SQL ranges now stop
  at `--`, the mirror of the Python pass masking `#`. Scope drops 997 → 988 and
  these nine mutants are no longer generated at all.

The first class is worth recording rather than suppressing: `SELECT 1` is
idiomatic, it will recur, and the next sweep should recognise it on sight
instead of re-deriving it.

### 3.2 The three 0% modules — 14 survivors, and one real finding

`spotify_client.py`, `config.py` and `serve.py` scored 0%. Eleven of the
fourteen are **recorded, not fixed**: urllib3 retry tuning (`total=3`,
`status=3`, the four entries of `status_forcelist`, `read=False`), and
`MAX_CONTENT_LENGTH = 150 * 1024 * 1024` / `APP_DEBUG`'s comparison in
`config.py`. No behaviour hinges on the exact values.

**Three were fixed**, and the first triage pass got one of them wrong. This
section originally counted `sys.exit(0)` in the SIGTERM handler among the
recorded ones, on the reasoning that it "needs a subprocess-level test of the
container entrypoint that the suite has no shape for". That was wrong: the
handler is an ordinary function, and calling it with `jobs.drain` patched
raises `SystemExit` whose `.code` is directly assertable. Both of
`serve.py`'s survivors died to `tests/test_serve.py` (§4), taking the module
from 0% to 100%.

The other two are not tuning:

- **`respect_retry_after_header=False` → `True` survives.** This is a
  documented, deliberate, correctness-critical setting: `CLAUDE.md` records
  that it exists so a 429 raises immediately instead of blocking for an
  hours-long app-quota `Retry-After`, with `snapshot._call()` handling the wait
  itself. Flipping it reinstates exactly the hours-long block the design
  removed, and **nothing asserts it**. This is the sharpest single gap in the
  three modules — a load-bearing constant, named in the codebase map, with no
  test.
- **`serve.py` was untested outright.** Both its mutants survived, including
  `if __name__ == "__main__":` → `!=`, which under the mutation runs the
  server body on *import*. Nothing imported it, so nothing noticed — a
  statement about the module's coverage, not about either line. Fixed; and
  that mutant turned out to shape the test, since a top-level `import serve`
  would hang collection inside `waitress.serve()` rather than fail (§4).

### 3.3 The mechanical numeric classes — 129 survivors sorted

| class | n | verdict |
|---|---:|---|
| HTTP status codes in `abort()` / `api_error()` | 25 | gap — nothing asserts which code |
| `EXISTS (SELECT 1 …)` | 11 | equivalent (§3.1) |
| retry tuning constants | 8 | recorded, not fixed |
| `or 1` / `or 0` NULL defaults | 6 | gap — narrow, bites only on NULL |
| counter initialisers (`rows_read=0`, `pending=0`) | 4 | gap |
| digits in SQL comments | 4 | equivalent, generator fixed (§3.1) |
| percentage and display precision | 4 | mixed — needs eyes |
| batch sizes (`range(0, len(ids), 500)`) | 2 | cosmetic |
| remainder | 65 | not yet triaged |

The 25 status codes are the **P2-010 shape the spec predicted for `app.py`**:
`test_routes.py` sweeps every route for non-5xx, and a 400 mutated to a 401 is
still non-5xx, so the sweep passes and the code goes unasserted.

### 3.4 The settled gaps, as a work order

41 mutants across 41 distinct source lines, verdict **gap — fixed**, grouped by
what it takes to kill them. Everything below is settled: the reading is done,
and what remains is writing the test. Twelve more of this class are already
closed (§4).

**Every test carries** `# source: S_sweep.md §3.4 -- <op> at <module>:<line>`,
and **is not accepted until proven**:

```
venv/bin/python scripts/mutation/verify.py --work /tmp/symr-kill kill \
    --module app.py --line 446 --op num --test tests/test_error_status_codes.py
```

That must print `KILL PROOF: PASS`, and the suite must be green without the
mutant. A test that cannot fail cannot kill anything, which is the defect step
P found in every session it ran.

#### A. Status codes reachable by one malformed request — 12

Target `tests/test_error_status_codes.py`, extending its existing parametrize
table. Assert the exact code **and** the description fragment; the code alone
cannot distinguish a working guard from a deleted one where several refusals
share a status.

| line | route | refusal |
|---:|---|---|
| 418 | `/dev/canonical/group/<int:group_id>` | 404 "Group has no members." — needs a group row **with no members**, distinct from 414's "no such group" |
| 446 | `/api/canonical/cross` | 400 "tracks= needs at least 2 known track ids" |
| 519 | `/api/canonical/cross/apply` | 400 "tracks not in this bucket: …" |
| 536 | `/api/canonical/cross/apply` | 400 "no track_group row for …" |
| 820 | `/api/history/import` | 400 "A .zip export file is required." |
| 822 | `/api/history/import` | 400 "Upload the export .zip itself, not its contents." |
| 837 | `/api/history/reimport` | 400 "Nothing uploaded yet…" |
| 885 | `/api/roundtrip/alias` | 400 "aliases must be a non-empty list…" |
| 912 | `/api/roundtrip/wanted/clear` | 400 "source must be one of …" |
| 949 | `/api/backfill/start` | 400 "generations must be one of …" |
| 974 | `/api/artists/alias` | 400 "artist_id_a and artist_id_b required" |
| 987 | `/api/artists/unmerge` | 400 "artist_id required" |

**418 and 949 are the two worth care.** 418 needs a `canonical_group` row whose
`track_group` set is empty — if the fixture gives it members, the request 302s
and the test asserts nothing. 949 pins `_BACKFILL_GENERATION_COUNTS = (2, 7)`,
which `CLAUDE.md` says *is* the backfill's budget control; send a third value.

#### B. The `not_authenticated` 401s — 5, and the plan below was wrong

`app.py` lines **769** (`/api/snapshot/pull`), **777** (`/refresh`), **785**
(`/backfill`), **858** (`/api/roundtrip/reconcile`), **945**
(`/api/backfill/start`).

This section originally read the same as A: monkeypatch `get_spotify_client`
to `None`, hit the route, assert 401. **That plan was wrong, and it is
recorded because it would have produced a green, worthless test.**

`require_login` (`app.py`'s first-registered `before_request` hook) already
returns `api_error("not_authenticated", 401)` for **any** unauthenticated
`/api/*` request, before the view function runs at all — none of these five
routes is in `_PUBLIC_ENDPOINTS`. So the in-view
`if get_spotify_client() is None: return api_error(...)` block on each of
these five lines is **dead code**, unreachable through any HTTP request in
the suite or in production, because the hook always answers first.

Verified rather than assumed: the obvious test (monkeypatch to `None`, POST
the route, assert 401) passes against the real code — and run through
`verify.py kill` against the 769 mutant, reports **0 failing under the
mutant**. The test cannot distinguish 401 from 402 because it never observes
the mutated line; the hook's own identical 401 is what the response actually
carries.

**Verdict: equivalent**, not gap. No test can kill these five, and none
should be written to try. Worth flagging to Finn as a possible cleanup — the
five blocks are redundant with the hook and could be deleted — but that is a
behavior-shape decision, out of scope for a testing pass.

#### C. `abort(400, description=str(e))` wrappers — 5

`app.py` lines **546**, **608**, **652**, **729**, **890**.

These fire only when the domain call underneath raises. **Read the called
function first** — the input that makes it raise is the test, and guessing
produces a test that hits a different branch and asserts nothing. Assert the
400 and that the raised message reaches the body: the mutation being killed is
the *code*, but a test that never exercises the wrapper kills nothing.

#### D. `or` NULL defaults — 8 lines, closed

`app.py:706`; `entities.py:65`, `66`, `191`, `502`, `510`; `backfill.py:77`,
`133`. 12 mutants total (some lines carry more than one) — 11 fixed, 1
equivalent.

**This group had one predicted trap, and a second one the plan didn't
predict, and the second one bit twice before the fix stuck.**

The predicted trap: a fixture that always stores a real `disc_number` passes
against an implementation with no default at all, since the default only
bites when the column is NULL. Real, and avoided throughout by giving the
None-valued row an explicit, non-degenerate competitor.

**The trap the plan missed: a competing value that is itself falsy is
just as blind to the mutation as no competitor at all.** `x or 0` and
`x or 1` only disagree when `x` is falsy — so a fixture built to "disagree
with the rule" by pairing a `None` row against a `0` row doesn't disagree
with anything: both values are falsy, both get the *same* substituted
default under the mutant, and the two rows shift together with their
relative order unchanged. This is the P2-005 shape one level down — not "the
fixture agrees with a rule the implementation could fall back on" but "the
fixture's control value is degenerate for the specific literal under test" —
and it produced a red herring twice in this group alone:

- `entities.py:502`/`510`'s track_number tests initially paired the None row
  against an explicit `0`. Both entries in verify.py's kill table came back
  FAIL. The fix was a competing value of `1` (truthy, immune to the `or`
  entirely) instead of `0`.
- `entities.py:191`'s `fetch_artist_image` test made the identical mistake
  against `or 0` → `or 1` on an image width, with the same FAIL and the same
  fix (a competing width of `1`, not `0`).

**A second, unrelated wrinkle surfaced only in `_owned_rows`' unordered
scan**, which line 510's tests read through: with no `ORDER BY` in that
query, a genuine tie's resolution order was empirically confirmed to follow
`track_id`'s own lexical order, not insertion order — the opposite of what
the first construction assumed. Verified by direct probing (build the tie,
print the order, mutate, print again) rather than guessed, after the first
guess was wrong. Tests that rely on a tie now name their ids so the tie
breaks in the direction the test needs, with the reasoning recorded inline.

`backfill.py:133`'s `or 0` → `or 1` mutant is the group's one **equivalent**
verdict, and it doesn't need a probe to see why: `max(1, ceil(total_tracks /
50))` clamps the whole expression to at least 1 regardless of which literal
the fallback substitutes, because `ceil(0/50)` and `ceil(1/50)` are both `0`
or `1` — either way `max(1, ·)` erases the difference. No fixture, for any
value of `total_tracks`, can make the two literals disagree.

#### E. Counter initialisers — 11 lines, closed

`history_import.py:63`, `64`, `65`, `66`, `128`, `129`, `130`, `199`, `239`,
`243`; `backfill.py:28`. 8 fixed, 3 recorded — not fixed.

A fresh status object must report **zero** before any work happens. The trap
here is the mirror of D: assert the initial value on a *newly created* status,
not after a run that would overwrite it — a test that reads the counter after
processing kills nothing, because the initialiser's value is gone by then.
Two shapes needed it: `_status`'s `JobStatus` constructor defaults
(`history_import.py:63-66`, `backfill.py:28`), read back through
`.reset()` — which rebuilds from exactly those defaults before applying
whatever the caller passes, none of which touch the counters — and
`history_import._run_import`'s local `counts` dict (`128-130`), read back
through `_finish`'s write to `play_import`, calling `_run_import` on a
folder with no matching JSON files so `_parse_folder` never touches any of
the three.

**`199`, `239` and `243` are `recorded, not fixed`, and not by the trap
above.** All three are the *same* local variable, `pending`, inside
`_parse_folder`'s row loop — a chunk counter that decides when to call the
loop's `checkpoint()` closure, nothing else. It is never returned, never
part of `counts` or `_status`, and every value `checkpoint()` writes is an
absolute running total (`files_done`, `rows_read`,
`conn.total_changes - before`), not a delta — so an extra, redundant
`checkpoint()` call produces byte-identical output to not calling it. The
*only* way any of the three literals is ever observable is the **count of
`checkpoint()` calls**, which only diverges at the `_COMMIT_EVERY = 5000`
boundary — a fixture would need on the order of 5,000 JSON rows in one file
to shift that boundary by the one-off the mutation introduces, and even
then the only way to observe it is by monkeypatching `conn.commit` (or
similar) to count invocations, since `checkpoint()` is a closure with no
name reachable from outside `_parse_folder`. The property is real —
committing on a slightly wrong cadence is a real behavior — but nothing
about it is worth several thousand fixture rows and an internal-plumbing
spy to pin one commit-timing off-by-one that changes no stored value.

---

Visible in the untriaged remainder, and worth naming before it is lost:
`_BACKFILL_GENERATION_COUNTS = (2, 7)` — `CLAUDE.md` says those two fixed
buttons *are* the backfill's budget control, and neither value is asserted;
`CASE WHEN MAX(is_album_artist) = 1` in a `db.VIEWS` definition; and several
representative picks (`members[0]`, `sorted(members)[0]`) where `[1]` survives.

---

## 4. What was added

**§3.4 is complete.** **39 new test cases**, taking the suite from 944 to 983.
Each is proven by §6's gate — the named test observed failing under its
mutant, the suite green without it — and each kills with **exactly one**
failing test (D's two combined-mutant tests kill two named mutants each, both
confirmed).

**63 mutants are now accounted for**, across four disjoint sets:

| set | mutants | fixed | equivalent | recorded |
|---|---:|---:|---:|---:|
| §3.2 — the three 0% modules | 14 | 3 | 0 | 11 |
| §3.3 — status codes | 26 | 21 | 5 | 0 |
| §3.4 D — `or` NULL defaults | 12 | 11 | 1 | 0 |
| §3.4 E — counter initialisers | 11 | 8 | 0 | 3 |
| **total** | **63** | **43** | **6** | **14** |

The status-code row splits as 4 in the first batch, 12 in group A, 5 in group
C, and group B's 5 equivalents. That leaves **244 of the original 307
survivors untriaged** — see the end of this section.

- **`tests/test_serve.py`** (4). `serve.py` goes 0% → 100%. The import is
  deliberately lazy, inside a helper that patches `waitress.serve` first: the
  `__name__ != "__main__"` mutant runs the guarded block on import, so a
  top-level import hangs collection inside waitress instead of failing, and a
  hanging suite is billed as a 300-second timeout rather than a kill.
- **`tests/test_spotify_client.py`** (2). `respect_retry_after_header=False`,
  the sharpest single gap in the sweep. The tuning constants stay unasserted
  per §3.2.
- **`tests/test_error_status_codes.py`** (25 cases + 2 group-D tests). §3.4
  group A (12) and group C (5) both fully closed, on top of the 5 already
  there — 22 of the 26 status-code survivors. Group B is *not* among them:
  see below. Plus the `app.py:706` and `entities.py:65`/`66` group-D tests.
- **`tests/test_entities.py`** (7 group-D tests, 1 rewritten). The
  `fetch_artist_image` width tiebreak and all four `album_detail` sort-key
  survivors (§3.4 D) — see §3.4 D's account of the two false starts these
  needed before they actually killed anything.
- **`tests/test_backfill.py`** (3 group-D/E tests). `_settled_map`'s NULL
  and no-owned-tracks boundaries, plus its own `_status` reset default.
- **`tests/test_history_import.py`** (2 group-E tests). `_status`'s reset
  defaults and `_run_import`'s `counts` dict on an empty-folder reimport.

**Group B (5 `not_authenticated` 401s) turned out not to need tests at all.**
§3.4's plan for them was wrong — building the obvious test and running it
through `verify.py kill` showed 0 failing under the mutant, because
`require_login`'s `before_request` hook answers every unauthenticated
`/api/*` request before the view body runs, making the in-view check dead
code. Verdict corrected to **equivalent** in §3.3 B. This is the sweep's
second finding of the "the ledger's own plan was wrong" shape (the first
being §3.1's SQL-comment generator bug), and it is why §6's gate is framed as
mandatory rather than a formality — it caught a plan that read perfectly
reasonably and would have shipped a green test asserting nothing.

`test_routes.py`'s non-5xx sweep was **not** widened to assert exact codes.
Deriving each case's expected status by running the suite is characterization,
not specification, and `codebase-health-P.md` §2 is explicit that the
distinction is the point. The error paths are supplemented instead.

**§3.4 leaves nothing outstanding.** What remains of the sweep as a whole is
the untriaged majority of the 307 original survivors — the ~65 numeric ones
outside §3.4's five named groups, and the ~178 non-numeric ones nothing in
this document has looked at yet — plus the writeup's §5 (what this run says
about the next sweep), which is written after triage finishes.

---

## 5. What this says about the next sweep

*Written after triage. The one thing this run can already settle is §8.5's
question — whether the sessions after P kept the standard P set — and the
answer is no. `scrobble.py` at 76% is the cleanest case: a brand-new module,
a dedicated 1092-line test file, every convention documented and in force, and
roughly one mutant in four still survives. Whatever P installed did not
propagate on its own.*
