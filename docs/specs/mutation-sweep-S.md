# S — Whole-codebase mutation sweep

**Step S of `docs/Planning/roadmap.md`.**

Run the instrument P built on the code P did not touch: mutate every remaining module, run the
full suite against each mutant, and turn every survivor into either a test or a written verdict.

---

## 0. What planning changed from the roadmap section

Four things, all measured rather than argued.

- **The scope is 1.8×, not "3–4×".** The roadmap estimated the whole tree at three to four times
  the bounded run's 372 mutants. Generating them says **668** for the modules in §1 — because
  mutant density tracks branching, not line count, and the remaining modules are flatter than
  `scoring.py`. With SQL (below) the total is **911**.

- **The bounded run's tooling still existed, and is now committed.** The roadmap asks whether to
  keep hand-rolling or adopt `mutmut`/`cosmic-ray`. The question is moot: the generator and
  runner survived in a session scratchpad under `/private/tmp`, were recovered, audited (§3.4),
  and this time land in `scripts/mutation/` (§4). **That is the change that stops sweep #3
  re-deriving all of this** — the roadmap already records the method, but a method doc plus a
  vanished script is what made this session's first ten minutes an archaeology exercise.

- **SQL string literals are in scope, and they are the substance of the step.** The generator
  masks string literals, so the bounded run mutated no SQL at all. On `scoring.py` that barely
  mattered. On this set it is the difference between measuring a module and measuring its Python
  wrapper: **759 lines of SQL live inside string literals** across the target modules, and
  `entities.py`, `db.py`, `history_import.py` and `generations.py` keep most of their logic
  there. §2.2 adds an operator set for it — **243 further mutants**. Note that the bounded run's
  own §1.1 trap was found on exactly these mutations, using hand-written side scripts, precisely
  because the main sweep could not reach them.

- **`scripts/` is excluded, deliberately.** The roadmap's module list does not name it and it
  should not: two of the four scripts are described by `CLAUDE.md` as already applied and kept as
  the record of what happened, no test touches any of them, and 88 mutants there would produce
  88 guaranteed survivors carrying no information. Excluding them is a judgement about what a
  survivor *means*, so it is recorded here rather than left implicit.

The delegation shape in §7 is new and has no precedent in the bounded run.

---

## 1. Scope

Every module in the repo root that the bounded run did not cover. Counts measured 2026-08-23
against `feat/mutation-sweep-S` at branch point `89c3536`.

| module | Python | SQL | total |
|---|---:|---:|---:|
| `app.py` | 168 | 16 | 184 |
| `entities.py` | 104 | 55 | 159 |
| `canonical_detect.py` | 133 | 23 | 156 |
| `history_import.py` | 61 | 39 | 100 |
| `db.py` | 29 | 63 | 92 |
| `backfill.py` | 35 | 14 | 49 |
| `generations.py` | 35 | 12 | 47 |
| `jobs.py` | 35 | 0 | 35 |
| `artists.py` | 12 | 14 | 26 |
| `canonical_autogroup.py` | 13 | 4 | 17 |
| `grouping.py` | 16 | 0 | 16 |
| `api_log.py` | 11 | 3 | 14 |
| `spotify_client.py` | 8 | 0 | 8 |
| `config.py` | 4 | 0 | 4 |
| `normalize.py` | 2 | 0 | 2 |
| `serve.py` | 2 | 0 | 2 |
| **total** | **668** | **243** | **911** |

`serve.py` and `config.py` are in scope though the roadmap's list omits them — `serve.py` because
Q shipped it after that list was written, `config.py` because it is four mutants.

**Out of scope:** `scoring.py`, `canonical.py`, `snapshot.py`, `roundtrip.py` (done, 2026-08-23 —
`docs/codebase-health/post_P_sweep.md`); `scripts/` (§0); `tests/` itself; templates and JS.

These counts are the plan's estimate, not a contract. The runner reports what it actually
generates, and **that** number goes in the writeup.

---

## 2. Operators

### 2.1 Python — unchanged from the bounded run

Kept byte-identical so the two sweeps' kill rates are comparable:

comparison flips (`<`/`<=`/`>`/`>=`/`==`/`!=`), `is`/`is not`, `in`/`not in`, `and`/`or`,
`True`/`False`, `is None`/`is not None`, `min`↔`max`, `reverse=True`→`False`, sort-key sign, and
numeric literal `n` → `n+1`.

`in` inside a `for` header is skipped — a loop keyword, not a membership test. Comments and string
literals are masked: **the Python operators never apply inside a string**, and that stays true
now that §2.2 exists.

### 2.2 SQL — new

A **second, disjoint pass** over string-literal tokens only. A string is eligible when it contains
a SQL keyword (`SELECT|FROM|WHERE|JOIN|GROUP BY|ORDER BY|INSERT|UPDATE|DELETE`), which is what
keeps template names, URLs and log messages out of it.

| operator | replacement |
|---|---|
| `=` | `<>` |
| `>` / `>=` / `<` / `<=` | swap for the adjacent comparison |
| `MIN(` ↔ `MAX(` | |
| `ASC` ↔ `DESC` | |
| `LEFT JOIN` | `JOIN` |
| `DISTINCT ` | deleted |
| `AND` ↔ `OR` | |
| `IS NULL` ↔ `IS NOT NULL` | |
| `IN` ↔ `NOT IN` | |
| numeric literal `n` | `n+1` |

`=` → `<>` is 124 of the 243 and was kept knowingly: most should die easily, and a survivor there
is the interesting case — a filter or join condition nothing asserts.

**`db.SCHEMA` is excluded**; `db.VIEWS` and the migration SQL inside `_migrate` are not. The views
are logic and the migrations carry real work (`tracks_pulled_snapshot_id`'s backfill is an
`UPDATE … WHERE`), whereas mutating `CREATE TABLE` DDL yields broken-or-equivalent mutants and
nothing else. The exclusion is found **by locating the `SCHEMA = """…"""` assignment in the AST**,
never by hardcoding a line range, which drifts on the next migration.

---

## 3. The runner

### 3.1 How a mutant is run

Every mutant is a **single substring swap on one line** — the property that makes a survivor
inspectable by eye rather than a puzzle, and it holds for the SQL pass too.

Each mutant is applied in one of **N isolated worker copies** of the repo (N = 6, measured as the
right number for an 8-core laptop), then the **full suite** runs in that copy with
`-q -x --no-header -p no:randomly`. Full suite, not the module's own test file: a mutant in one
module is routinely killed by a test in another, and restricting the run would report gaps that
are not there.

Worker copies exclude `.git/`, `venv/`, `data/`, `__pycache__/`, `*.pyc`, `*.db` and `.coverage*`.
Copying the 93 MB `symr.db` into six workers is pure cost — the suite could not open it anyway
(`conftest.py`'s connect guard).

### 3.2 Classification

| status | meaning | counted |
|---|---|---|
| `caught` | the suite failed | numerator |
| `SURVIVED` | the suite passed | denominator only |
| `broken` | the mutant is not valid code — Python `SyntaxError`/`IndentationError`, **or** a SQLite `OperationalError` with no assertion failure behind it | **excluded from both** |
| `timeout` | the suite did not finish inside 300s | counted as caught, **reported separately** |
| `crashed` | the child was killed by a signal (negative return code) | **excluded, re-run** |

`broken` is where the SQL pass needs care: an invalid query fails every test that touches it,
which reads as a kill and would silently inflate the rate with meaningless catches. It is the
same ruling the Python pass already makes for `SyntaxError`, applied to the new operator set.

`timeout` is worth watching on `=` → `<>` inside a join condition, which can turn a join into
something close to a cross product.

### 3.3 The crash-verification pass

A signal-killed child returns a **negative** return code, and `if rc:` reads that as "caught".
The bounded run took a `SIGSEGV` mid-run and only discovered the misclassification afterwards.
This runner classifies negative return codes as `crashed` up front and re-runs them, and the
sweep closes with a **re-run of every caught mutant** confirming the totals, exactly as the
bounded run's after-the-fact pass did (366/366, zero anomalies).

### 3.4 Audit findings on the recovered tooling

Three corrections, all required before the sweep runs:

1. **`PYTHONDONTWRITEBYTECODE=1` in the child environment.** `post_P_sweep.md` §1.1 documents the
   trap: restoring a file inside the same second as the mutated write, with the same byte count,
   leaves a `.pyc` the interpreter still considers valid — so a clean-looking source tree executes
   mutated bytecode, undetectably by `grep`, `git diff` or `git status`. The recovered runner
   restores by writing (correct) but never sets the variable, so its only protection is that each
   worker immediately overwrites with the next mutant. That leaves the last mutant per worker and
   any baseline run exposed. Set the variable; keep restore-by-write; `os.utime` as a belt.
2. **Negative return codes** (§3.3) — inherent in the classifier, not a later pass.
3. **The `broken` rule extended to SQL** (§3.2).

Everything else about the recovered scripts stands as written and should not be rewritten for
taste: the tokenize-based masking, the `for … in` filter, the per-mutant restore, `-x`, and the
plain `ThreadPoolExecutor` over worker directories.

---

## 4. Where the tooling lives

Committed to **`scripts/mutation/`**:

| file | what it is |
|---|---|
| `generate.py` | the operator sets and the mutant generator (Python pass + SQL pass) |
| `sweep.py` | worker copies, the parallel run, classification, results JSON |
| `verify.py` | re-run one mutant; the "exactly this test fails" proof; the crash-verification pass |

`tests/test_codebase_map.py` currently scans the repo root and the **top level** of `scripts/`, so
a subfolder would slip out of the map's guarantee. Its `_repo_modules()` walks `scripts/`
recursively instead, and `CLAUDE.md`'s codebase map gains a bullet naming these three.

**No unit tests for the tooling.** It is `scripts/`-class one-off code by `CLAUDE.md`'s
convention, and it is verified the way a mutation tool is actually verified — by its own output
being read: a generator that stopped masking strings, or a runner that stopped detecting kills,
shows up immediately as an absurd mutant count or a 0%/100% rate. See §11.

**Full results are not committed.** The runner writes `sweep_results.json` to the scratchpad; the
committed record is the survivor table in §8, which is small, is the part a later session
actually reads, and is what makes the work resumable across sessions when the scratchpad is not.

---

## 5. Verdicts

Every survivor gets exactly one, written down with its reason:

| verdict | meaning | action |
|---|---|---|
| **gap — fixed** | a real unasserted property | write a test; prove the kill (§6) |
| **gap — recorded, not fixed** | real, but the test costs more than the property is worth | record the reason in §8's table; no test |
| **equivalent** | no test can kill it — the mutation does not change behaviour | record; add a test **only** where the boundary's *answer* is itself worth pinning, with a docstring saying the mutant is equivalent so the next sweep does not re-derive it |
| **cosmetic** | output formatting nothing depends on | record |
| **harness-masked** | untestable through the suite because `conftest.py` overwrites the value | record — and note it as an instance of the shape, since coverage cannot surface it at all |

**No numeric kill-rate floor**, deliberately. A floor turns triage into a chase for the number,
and `app.py`'s route-level survivors are exactly where "recorded, not fixed" is sometimes the
honest verdict. What is required is that **every survivor has a verdict** and every verdict has a
reason. Per-module kill rates are published (§8) because they say where *not* to spend the next
pass — that was the useful half of the bounded run's result.

**Fix at the level below the one you found.** `post_P_sweep.md` §5: every substantive survivor
there sat one level under a finding already "fixed" by asserting the column rather than the
computation filling it. A test that pins the mutated line's *output* without exercising the rule
that produces it is that same mistake again.

---

## 6. The kill-verification gate

**A test written for a survivor is not accepted until its mutant has been re-applied and exactly
that test observed to fail.** Not "the suite fails" — the named test, by name.

This is the gate that makes §7's delegation safe. A triage verdict is otherwise a judgement to be
trusted; with it, the claim "this test kills that mutant" is a re-runnable fact, so a cold agent's
output can be *checked* rather than believed. It also directly blocks the defect P found in every
single session — a test that passes, cites a real clause, and could never fail — because a test
that cannot fail cannot kill anything.

The proof for each test is two runs: the suite green with the test and no mutant, and the mutant
applied with that test failing. `verify.py` does both.

---

## 7. How the work is split

One **master implement session**, spawning **triage subagents**. The master does everything that
is a measurement or a decision; agents do the bounded, checkable part.

### 7.1 The master session

1. Recover the tooling, apply §3.4's corrections, commit `scripts/mutation/` + the map-test
   change (`CLAUDE.md` bullet included).
2. **Run the whole sweep itself, once** — one run over all 911, one results file. Nothing about
   the measurement is delegated.
3. Partition the **survivors only** (§7.2) and spawn agents, 3–4 in parallel.
4. Re-run **every** returned kill proof itself. Cheap, mechanical, and the point of §6.
5. Review every returned test against P's two questions — *what would a wrong implementation have
   produced here?* and *is the fixture adequate, or does it agree with a rule the implementation
   could fall back on?* (`P2_findings.md`, `P3_findings.md`).
6. Land the tests, re-run the **survivor set** to confirm every "gap — fixed" mutant is now
   caught, write §8's document, and put any escalated question to Finn.

### 7.2 The partition rule

By **test-file ownership**: survivors whose fixes would touch the same test file go to the same
agent, so two agents never edit one file. Target roughly equal batches of ~10–12 survivors.

The partition is decided **after** the sweep, never before — survivor counts per module are
unknowable until it has run, and that is the whole reason this is a rule rather than a list.
`app.py` gets no carve-out; if its survivor count comes back small enough, the master triages it
directly, since its survivors are likeliest to be the P2-010 shape (a query-string branch that
responds but whose semantics nothing asserts) and that judgement is the subtlest here.

### 7.3 What an agent gets, returns, and may not do

**Gets:** its survivor list (module, line, operator, before/after); the path to **its own plain
directory copy** of the repo — *not* a git worktree, per the standing no-worktrees rule; §5's
verdict taxonomy; §6's gate and the `verify.py` invocation; and the suite's conventions — the
`# source:` comment, and the fixture-adequacy rule that a fixture must disagree with every rule
the implementation could fall back on, including an arithmetic degeneracy (`post_P_sweep.md`
§3.1).

**Returns:** one verdict per survivor with its reason, the test text for each "gap — fixed", and
the kill-verification evidence — which test failed under the mutant, and that the suite is green
without it.

**May not:** commit or push; edit anything outside its own test files; change a non-test module;
run the app or a dev server; or decide a behaviour change. **A survivor that points at a real bug
rather than a missing test is escalated, not fixed** — that is Finn's call, and it becomes a
separate branch, not a line item in this one. Anything an agent cannot decide comes back as a
question; guessing is the one failure mode this design cannot check for itself.

---

## 8. The writeup

`docs/codebase-health/S_sweep.md`, following `post_P_sweep.md`'s shape:

1. **Method**, by reference — it points at `post_P_sweep.md` §1 and records only what S changed
   (the SQL pass, the `broken` ruling, the classifier fixes, the delegation).
2. **Result** — mutants generated, `broken` excluded, kill rate **per module**, and the totals.
   States plainly that it is the pre-fix measurement.
3. **The survivors, and what each turned out to be** — the full table, one row per survivor with
   its verdict and reason. This is the committed ledger §4 refers to.
4. **What was added** — the tests, and the confirming re-run of the survivor set.
5. **What this says about the next sweep** — including the one thing this run can measure that
   the bounded one could not: whether the sessions after P (M, N, J, Q and P's own three parts)
   kept the standard P set, which is the failure mode a large testing investment actually has.

---

## 9. Constraints

- **No dev server may be running.** The sweep writes `.py` files continuously in worker copies and
  the master edits `tests/` — with a Flask reloader up, that is the known hazard, and the master
  confirms port 45660 is clear before starting.
- **Zero Spotify requests**, by construction: every run is the test suite, whose `conftest.py`
  blocks sockets outright. Nothing in this step touches the API, the library, or the real
  `symr.db`.
- **~2 hours of laptop CPU** for the run (911 mutants, 6 workers, at the bounded run's measured
  rate), plus triage. The suite is 872 tests / 18.0s as of 2026-08-23.
- The sweep measures the branch as it stands. Tests added during triage are proven against their
  own mutants (§6); the headline rate is not re-derived after the fixes, only the survivor set is
  re-run.

---

## 10. Done when

- The sweep has run over §1's scope, with `broken` and `crashed` classified per §3.2 and the
  crash-verification pass clean.
- **Every survivor has a verdict and a reason** (§5).
- Every "gap — fixed" has a test, each proven by §6's gate and re-proven by the master.
- The survivor set re-runs with every fixed mutant now caught.
- `scripts/mutation/` is committed, `CLAUDE.md`'s map names it, and `test_codebase_map.py`
  recurses.
- `docs/codebase-health/S_sweep.md` is written (§8).
- `venv/bin/python -m pytest` is green.

---

## 11. Tests

The step's product **is** tests, so this section is about the two things that are not.

- **The tests written during triage** each carry a `# source:` line naming this sweep and the
  mutant they came from (`# source: S_sweep.md §3 -- mutant N, <op> at <module>:<line>`), which
  is what `test_every_test_declares_where_its_expected_value_came_from` enforces and what lets a
  later session tell a mutation-derived test from a spec-derived one. Each is proven by §6 — and
  §6's proof, not coverage and not a green run, is what says the test can fail.

- **`test_codebase_map.py` recursing into `scripts/`** is a real assertion with a real failure
  mode: it fails unless `CLAUDE.md` names all three new files. Its existing companion test
  (`_repo_modules()` actually seeing the tree) covers the scan going blind.

- **`scripts/mutation/` itself gets no tests**, and the reason is not "it's a script": a mutation
  tool is verified by the output of every run it makes. A generator that stopped masking strings
  reports an absurd mutant count; a runner that stopped detecting failures reports 0% or 100%.
  Both are read by a human every time the tool is used, which is exactly the property a test
  would otherwise have to supply. Pinning the operator table in a test would instead pin it
  against deliberate edits — the next sweep is expected to change it.
