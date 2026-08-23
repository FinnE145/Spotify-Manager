# Post-P mutation sweep

**Not part of step P.** P3-003 left a class behind — *which properties of the code are unasserted
by the suite?* — that P3 could only ever answer for the ~583 lines it happened to be moving
(P3-004 sampled it, P3-005 and P3-007 enumerated it twice more, P3-008 found a granularity below
all three). This is the first pass at that question over code P was not touching, run 2026-08-23
immediately after P merged.

**Bounded on purpose.** Four modules, chosen as the highest-risk by blast radius: `scoring.py`,
`canonical.py`, `snapshot.py`, `roundtrip.py`. The whole-codebase version is a roadmap step
(**S**), deliberately placed a few steps out so that it also measures whatever the intervening
feature work adds — including whether sessions after P keep the standard P set.

---

## 1. Method

**372 mutants**, one substring change each, generated mechanically and run against the **full**
suite in six isolated worker copies of the repo.

Operators: comparison flips (`<`/`<=`/`>`/`>=`/`==`/`!=`), `is`/`is not`, `in`/`not in`,
`and`/`or`, `True`/`False`, `min`↔`max`, `reverse=True`, sort-key sign, and numeric literal `n` →
`n+1`. `in` inside a `for` header is skipped — that is a loop keyword, not a membership test.
Every mutant is a single substring swap on one line, which is what makes a survivor inspectable
by eye rather than a puzzle.

A mutant is **caught** if the suite fails, **survived** if it passes. Nothing is inferred from
coverage.

### 1.1 The trap, recorded because it is silent and it did catch us

Restoring a mutated file with `shutil.copy` + `shutil.move` gives it an mtime inside the **same
second** as the mutated write. Python validates a `.pyc` on `(source mtime, source size)` — so a
mutation that is **the same number of bytes** as the original (`WHERE 1 = 1` → `WHERE 2 = 1`,
`MAX(` → `MIN(`) leaves a cache entry that still looks valid, and the interpreter keeps executing
**mutated bytecode from a clean-looking source tree**.

It is undetectable by every check you would reach for: `grep` reads the source, `git diff` and
`git status` read the source, and the suite passes because the mutation is one that survives. It
was found only by spying on the SQL that actually reached SQLite.

**So: restore by writing the original file** (a fresh write always bumps the mtime), or force it
with `os.utime(path, None)`, and run the child with `PYTHONDONTWRITEBYTECODE=1`. The main sweep
here restored by writing and is unaffected; the small hand-written scripts used the vulnerable
pattern and were all re-run after the fix, with identical results.

---

## 2. Result

| module | mutants | killed |
|---|---|---|
| `canonical.py` | 49 | **100%** |
| `snapshot.py` | 107 | **100%** |
| `roundtrip.py` | 119 | **100%** |
| `scoring.py` | 97 | 92% |
| **total** | **372** | **364 (98%)** |

**275 mutants across the three modules P2 worked hardest, every one caught.** That is the headline
and it is worth stating plainly: P2's suite holds up under an instrument it was never tested with.

All eight survivors were in `scoring.py`.

A crash-verification pass re-ran all 366 caught mutants with signal detection, after one worker
took a `SIGSEGV` mid-run (a signal-killed child returns a *negative* return code, which the first
runner read as "the suite caught it"). **366/366, zero anomalies** — the segfault misclassified
nothing.

---

## 3. The survivors, and what each turned out to be

| # | site | verdict |
|---|---|---|
| 1 | `RECENT_WINDOW_DAYS = 90` → `91` | **gap — fixed** |
| 2 | `_recent_ordinals`: `started_at >= win` → `>` | **gap — fixed** |
| 3 | `_first_opportunity_days`: `fo < win` → `<=` | **equivalent mutant** |
| 4 | `_version_horizons`: `(1 - BLEND)` → `(2 - BLEND)` | **gap — fixed** |
| 5 | `_fetch_own_inputs`: tenure CTE emptied | **gap — fixed** |
| 6 | `round(duration, 2)` → `3` | cosmetic; no test |
| 7 | `_worker_alive = False` → `True` | masked by the fixture; recorded |
| 8 | `have.get(aid, 0)` → `1` | narrow; recorded |

### 3.1 The blend — a P2-005 in the middle of a test that names the right clause

`test_the_recent_horizon_is_blended_toward_all_time` cites §7.1a correctly and asserts true
things. Its own docstring says why it cannot fail: *"recent_windowed is 0 for both, since nothing
they did falls inside the window."* The term under test is `(1 - BLEND) × 0`, and zero times any
coefficient is zero — so the assertion pins the `all_time` half of the formula and is structurally
blind to the half it is named after.

This is P2-005's rule restated one more time: **a fixture must disagree with every rule the
implementation could fall back on.** Here the fallback was not another rule but an arithmetic
degeneracy, which is harder to see and just as fatal.

The new test gives the version real in-window activity and asserts explicitly that the result is
**not** the degenerate `BLEND × all_time` case, so the fixture's own adequacy is checked rather
than assumed.

### 3.2 The own-tier tenure CTE

Two queries compute the same §4 inputs — `_fetch_version_inputs` for the version tier and
`_fetch_own_inputs` for recording/release/track (§6). Only the version one was asserted, so
emptying the own tier's tenure CTE changed nothing any test read. Narrow by construction:
`SUBTIER_W` is small and §10.1 already records its blast radius as representative-track
tie-breaking. Fixed with a paired present/absent assertion.

### 3.3 The equivalent mutant, and why it gets a test anyway

`fo < win` and `fo <= win` are **the same function** at the boundary: clamping a first opportunity
that already equals the window start assigns it the value it already has. No test can kill it and
none should try. The test written for it pins the boundary's *answer* (90, the full window) and
says in its docstring that the mutation is equivalent — so the next sweep does not re-derive this,
and nobody "fixes" the comparison.

### 3.4 The one the harness masks

`_worker_alive = False` → `True` survives only because `conftest._reset_module_state` sets that
global before every test. Production always starts `False`, so there is nothing to fix — but it is
worth recording as an instance of a shape the sweep can surface and a coverage report cannot:
**a module-level initial value that the test harness overwrites is untestable through the suite.**

---

## 4. What was added

Five tests in `tests/test_scoring_version.py`, under a section naming this sweep. **Each was
verified by re-running its mutant and confirming that exactly that test fails** — four kills plus
the documented equivalent. Suite: **866**.

---

## 4.1 The coverage pass, run second

Per `symr-verify`'s ordering, coverage was measured over the same four modules **after** the
mutation pass. It found **no gap outside the rulings already in
`docs/codebase-health/P2_coverage_SEALED.md`**, and every one of the five gaps mutation found sat
on a line the suite already executed. Figures stay in that file, per `P2_tests.md` §7.

---

## 5. What this says about the next sweep

Two things, and they point in opposite directions.

**The suite is stronger than a sampled pass would have suggested.** Three of four modules at 100%
is not a result P3's findings would have predicted — every P3 sweep found something, and the
inference that *every* module therefore has gaps turns out to be wrong. Where P2 aimed carefully,
it landed.

**And the residue concentrates where the arithmetic is.** Every survivor was in `scoring.py`, and
the substantive ones were all in the `recent` horizon — the same region as P2-007, whose entire
finding was that the `recent` column went unasserted. P2-007 made the column observed; it did not
make the computation behind it observed, and this sweep is what noticed. **A finding fixed at the
level it was found at leaves the level below it untouched**, which is the same lesson P3-008 drew
from key-level mutation, arriving here from a different direction.
