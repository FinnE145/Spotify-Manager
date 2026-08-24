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

`spotify_client.py`, `config.py` and `serve.py` scored 0%. Twelve of the
fourteen are **recorded, not fixed**: urllib3 retry tuning (`total=3`,
`status=3`, the four entries of `status_forcelist`, `read=False`),
`MAX_CONTENT_LENGTH = 150 * 1024 * 1024`, and `sys.exit(0)` in the SIGTERM
handler. No behaviour hinges on the exact values, and the exit code needs a
subprocess-level test of the container entrypoint that the suite has no shape
for.

Two are not tuning:

- **`respect_retry_after_header=False` → `True` survives.** This is a
  documented, deliberate, correctness-critical setting: `CLAUDE.md` records
  that it exists so a 429 raises immediately instead of blocking for an
  hours-long app-quota `Retry-After`, with `snapshot._call()` handling the wait
  itself. Flipping it reinstates exactly the hours-long block the design
  removed, and **nothing asserts it**. This is the sharpest single gap in the
  three modules — a load-bearing constant, named in the codebase map, with no
  test.
- **`serve.py` is untested outright.** Both its mutants survive, including
  `if __name__ == "__main__":` → `!=`, which under the mutation would run the
  server body on *import*. Nothing imports it, so nothing notices. That is a
  statement about the module's coverage, not about either line.

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

Visible in the untriaged remainder, and worth naming before it is lost:
`_BACKFILL_GENERATION_COUNTS = (2, 7)` — `CLAUDE.md` says those two fixed
buttons *are* the backfill's budget control, and neither value is asserted;
`CASE WHEN MAX(is_album_artist) = 1` in a `db.VIEWS` definition; and several
representative picks (`members[0]`, `sorted(members)[0]`) where `[1]` survives.

---

## 4. What was added

*Pending triage.*

---

## 5. What this says about the next sweep

*Written after triage. The one thing this run can already settle is §8.5's
question — whether the sessions after P kept the standard P set — and the
answer is no. `scrobble.py` at 76% is the cleanest case: a brand-new module,
a dedicated 1092-line test file, every convention documented and in force, and
roughly one mutant in four still survives. Whatever P installed did not
propagate on its own.*
