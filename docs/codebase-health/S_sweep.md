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

### 2.5 The crash-verification pass — clean

Spec §3.3 closes the sweep by re-running every mutant classified `caught`,
asking whether any was really a signal-killed child that `if rc:` read as a
kill. Run against the §2.4 tree: **719/719 came back `caught`, zero anomalies,
zero negative return codes** — matching the bounded run's 366/366.

That is the §2.4 measurement's `caught` half confirmed, and it is worth
recording that it took two attempts. The first run of this pass reported three
`!! SURVIVED` anomalies, all false: `verify.py`'s `cmd_caught` still carried
the worker race §1.1 had fixed in `sweep.py` four commits earlier, applied to
one call site rather than to the class. The tool whose entire job is catching
false results was manufacturing them. See §5.

Run **before** any further tests land and **alone** — a later run measures a
different tree, and under competing CPU load its timeouts surface as anomalies
that read exactly like findings.

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

### 3.5 Round 1 — 71 survivors closed, by feature domain

2026-08-24/26. Three triage agents plus the master session. **59 gap — fixed,
9 equivalent, 3 gap — recorded, not fixed.** 40 tests added; the suite went
983 → 1023 passed, 3 skipped.

| domain | test file | survivors | fixed | equiv | recorded |
|---|---|---:|---:|---:|---:|
| org canvas (`app.py` ×17, `grouping.py` ×2) | `test_grouping_canvas.py` | 19 | 18 | 1 | — |
| play-history import (`history_import.py` ×27, `app.py` ×3) | `test_history_import.py` | 30 | 23 | 4 | 3 |
| scrobbling (`scrobble.py` ×18) | `test_scrobble.py` | 18 | 15 | 3 | — |
| auth + app-wide hooks (`app.py` ×4) | `test_routes.py` | 4 | 3 | 1 | — |

**The partition rule needed correcting, and this is the finding.** §7.2 says
partition by test-file ownership, and the first plan read that as *per module*,
with `app.py`'s 50 survivors as one or two agents' work. They are not one job:
they are nine feature clusters, and each cluster's fix belongs in the test file
that owns the **feature** — `test_roundtrip.py`, `test_artists.py`,
`test_grouping_canvas.py` — never a file owning `app.py`. Splitting `app.py` by
line range would have put two agents in files a *third* agent already owned,
which is the exact collision §7.2 exists to prevent. By operator the 50 look
homogeneous (21 are `{"ok": True}` → `False`), and that is the trap: the shared
shape is the *question*, not the answer. Twenty-one endpoints, twenty-one
different things to know.

**What the survivors turned out to be:**

- **The canvas write endpoints asserted nothing at all.** Card POST/PATCH and
  label PATCH/DELETE were reached only by `routes_catalog.py`'s non-5xx sweep,
  so all twelve `WHERE id = ? AND board_id = ?` mutants lived. One test per
  endpoint kills all four of its mutants: two rows on one board disagree with
  every variant at once — `id <> ?` hits the other row, `OR` hits both,
  `board_id <> ?` hits neither. Verified empirically, per §3's trap 3, not
  reasoned.
- **`scoring_failed` had zero coverage.** The name appeared nowhere in `tests/`
  — only in `base.html`. It is the sole visible signal of a failing background
  recompute (async-recompute-N §7.1), and inverting it (banner on success,
  silence on failure) broke nothing. Now asserted both ways.
- **`app.py:80` is not the login guard.** It is `refresh_scores`, which guards
  on the same `_PUBLIC_ENDPOINTS` set and reads identically; the login guard is
  `:63` and its mutant was already killed. Inverting `:80` is a staleness bug,
  not an auth hole — worth recording precisely because the two lines are
  indistinguishable at a glance in a survivor row.
- **Three `history_import.py` survivors are commit-*cadence* only**
  (`_COMMIT_EVERY` and the `pending` comparison). Every checkpoint writes
  absolute totals and the final one always fires, so the stored `play_import`
  row is byte-identical either way. `recorded, not fixed`: reaching them needs
  a monkeypatch on internal commit calls, which tests the harness, not the rule.

**A new equivalent shape, for sweep #3's `generate.py`.** `scrobble.py:235/237`
are mutations *inside a docstring* that happens to quote SQL — same root cause
as §3.1's SQL-comment equivalents, but §3.1's fix clips `--` comments and does
not see triple-quoted strings. Cheap to eliminate at the generator; left alone
here because changing `generate.py` mid-step would invalidate the measurement.

**Two `LIMIT 1` → `LIMIT 2` survivors are equivalent for the same reason** and
were found independently by two agents (`scrobble.py:348`,
`history_import.py:347`): the statement is paired with `.fetchone()`, which
reads the first row whatever the bound. Worth naming as a class, not a pair.

**The gate held, and was checked.** Every one of the agents' 67 returned
verdicts was re-run by the master session — 56 kill proofs to `PASS`, 11
claimed equivalents still `SURVIVED`. Nothing was overstated. Two details made
that cheap enough to be routine: `verify.py --work` defaults to a **single
shared path** (`$TMPDIR/symr-mutation`), so parallel agents must each be given
their own or they corrupt each other; and the master's own re-run driver has to
bucket jobs **per thread**, not by `idx % N` across a pool — the same race
`sweep.py` and then `verify.py` were each fixed for, which is now three
appearances of one bug and an argument for putting the bucketing in one place.

**The briefs also had to forbid shared-fixture edits.** §7.2's partition keeps
two agents out of one *test* file but says nothing about `conftest.py`,
`builders.py`, `fakes.py` or `routes_catalog.py`, which any of them might
reasonably extend. Agents were told to define helpers locally instead. No
collisions occurred; the rule belongs in §7.3 for the next round.

---

### 3.6 Round 2 — 79 survivors closed, and a lesson about interruption

2026-08-26/27. Four triage agents. **68 gap — fixed, 8 equivalent, 3 cosmetic,
1 gap — recorded, not fixed.** 49 tests added; the suite went 1023 → 1072
passed, 3 skipped. All 79 verdicts were re-run by the master session and **all
79 reproduced** — 68 kill proofs to `PASS`, 11 non-fix verdicts still
`SURVIVED`.

| domain | test file(s) | survivors | fixed | equiv | cosm | rec |
|---|---|---:|---:|---:|---:|---:|
| `canonical_detect.py` rules half | `test_canonical_detect_rules.py` | 16 | 12 | 2 | 2 | — |
| `canonical_detect.py` queues half + `app.py` ×8 | `test_canonical_detect_queues.py`, `_page.py`, `_routes.py` | 24 | 22 | 1 | 1 | — |
| `entities.py` | `test_entities.py` | 27 | 25 | 2 | — | — |
| `db.py` | `test_db_schema.py` | 12 | 9 | 2 | — | 1 |

**`canonical_detect.py` split 16/16 along function boundaries**, not by line
range, and the halves already had separate test files — which is what made a
fourth agent safe rather than merely possible. §3.5's rule generalises: the
unit of assignment is the test file, and a module large enough to need two
agents must already *have* two test files, or it does not divide.

#### The interruption, which is the operationally important part

All four agents died at once: the machine slept and none recovered. The outcome
split cleanly in two, and the difference is worth building on.

- Two agents had been **reading source and writing nothing**. Roughly three
  hours of work, zero artifacts. Nothing to salvage.
- Two had been **writing tests as they went**. Their files were intact, parsed,
  and their copies were green — 21 of their 28 survivors already had killing
  tests.

**So the instruction changed: finish each survivor completely — verdict, test,
proof — before starting the next.** Both resumed agents then worked that way,
and both were observed running `verify.py` continuously rather than batching
proofs at the end. This costs nothing when nothing goes wrong and is the whole
difference when something does.

**Progress is reconstructible from disk with no agent involvement.** Running
`verify.py one` for each of a dead agent's assigned survivors, *inside its own
copy*, reports exactly which are already caught. That is how the 21/28 figure
above was established before any agent was resumed. **But `one` is not the §6
gate** — it proves the suite fails, not that a *named* test fails, which is
precisely the distinction that stops a test failing for an incidental reason
from counting as a kill. Every one of those 21 still had a real `kill` proof
run afterwards. Reconstruction tells you where you are; it does not discharge
the gate.

**Resuming works and is cheaper than respawning.** All four agents resumed with
context intact and completed. Handing each one the externally-measured state —
these are killed, these are open — meant none of them re-derived it.

#### Findings

- **The swap trap** (`entities.py:296`, `453` col68). A symmetric two-item
  fixture makes a JOIN-condition inversion produce a *coincidental one-for-one
  swap*: same row count, same sort order, different truth. `296` needed a third
  unrelated playlist to break the symmetry; `453` was killable on value instead,
  because a swapped artist *name* is observable where a swapped count is not.
  This is `post_P_sweep.md` §3.1's arithmetic degeneracy in a new costume, and
  it is now the most common way a fixture here agrees with the mutant.
- **A third equivalent class: provably unreachable from this call site**
  (`entities.py:676`). `canonical.representative()`'s `or` fallback cannot fire
  because `vid` always comes from a `track_group` row read moments earlier on
  the same connection. Distinct from §3.1's `EXISTS (SELECT 1 …)` and from
  §3.5's docstring-quoted SQL. `db.py:531` is the same shape reached
  differently — a `LEFT JOIN` guarding a dangling FK that `PRAGMA foreign_keys
  = ON` makes unreachable.
- **`_BUSY_TIMEOUT_SECONDS = 30` is `recorded, not fixed`, deliberately not
  `equivalent`.** 30 vs 31 genuinely changes when SQLite's C-level busy-wait
  raises, so "equivalent" would be false. Pinning it needs a held file lock for
  ~30 real seconds. The distinction matters: `equivalent` is a claim that no
  test *could* kill it, and the flattering error here is to use it for
  "no test I want to write".
- **The `{"ok": True}` question, settled the same way twice.** Round 1 ruled
  four of them `gap — fixed` by asserting the flag *alongside* a real
  behavioural fix. Round 2 reached the same call independently on `app.py:563`
  and `655` after grepping the JS and finding both handlers branch on
  `.error`, never `.ok` — and ruled `447` **cosmetic**, because there the field
  is hardcoded and its only consumer reads `items[0]` and nothing else. The
  rule that emerges: assert the flag where a real assertion is already being
  made, and record it as cosmetic where there is nothing to attach it to.

---

### 3.7 Round 3 — 29 survivors closed, and a test that could never fail

2026-08-28. Three triage agents. **25 gap — fixed, 3 equivalent, 1
harness-masked, 0 recorded.** 21 tests added and one existing test rewritten;
the suite went 1072 → 1093 passed, 3 skipped. All 29 verdicts were re-run by
the master session and **all 29 reproduced** — 25 kill proofs to `PASS`, 4
non-fix verdicts still `SURVIVED`.

| domain | test file | survivors | fixed | equiv | masked |
|---|---|---:|---:|---:|---:|
| round-trip (`app.py` ×9) | `test_roundtrip.py` | 9 | 9 | — | — |
| jobs (`jobs.py` ×10) | `test_jobs.py` | 10 | 8 | 1 | 1 |
| artists (`artists.py` ×8 + `app.py` ×2) | `test_artists.py` | 10 | 8 | 2 | — |

**The headline finding is a test that was already in the suite and could never
fail.** `test_the_event_log_is_capped_so_a_long_run_stays_small` both *filled*
and *asserted* through `jobs._LOG_LIMIT` — `range(jobs._LOG_LIMIT + 50)`, then
`assert len(log) == jobs._LOG_LIMIT`. Under `_LOG_LIMIT = 201` it logged 201,
retained 201 and passed green. This is `codebase-health-P.md` §2's defect in
its purest form: a test citing a real clause, asserting something true, that a
broken implementation satisfies identically. **The general rule it yields is
new and belongs in every future brief: a test that derives its expected value
from the thing being mutated moves with the mutant.** Write the expectation as
a literal — never read it back off the constant, the default argument, or the
query under test. Coverage cannot see this and neither can review at a glance;
only mutation finds it.

**Two new equivalent classes**, taking the named set from three to five:

- **A loop bound made dead by an in-body guard** that terminates on the
  second-to-last index. `jobs.call` states its retry cap twice — `range(2)` and
  `attempt == 1` — and only the guard is binding, so any `range(n >= 2)`
  behaves identically. Expect this in every bounded-retry loop carrying its own
  last-attempt branch.
- **A comparison-boundary flip whose two branches coincide at the boundary.**
  `_pair_key`'s `(a, b) if a < b else (b, a)` mutated to `<=` differs only at
  `a == b`, where both branches build `(a, a)`. Distinct from the
  unreachable-state class: the state is trivially reachable, the two answers
  are simply equal.

**`jobs.py:23` is the sweep's first `harness-masked` verdict**, and it is a
clean instance of the shape. `_stop_requested = False` is the module-level
initialiser, and `conftest.py`'s autouse `_reset_module_state()` sets it before
every test body runs — so no test can observe the initial value at all, and
`conftest.py` is off-limits to an agent by construction. Coverage cannot
surface this: the line executes on import every time.

**A `recorded, not fixed` precedent was correctly refused.** `jobs.drain`'s
`timeout=40` and `max(1, …)` were flagged in the brief as probably matching
`_BUSY_TIMEOUT_SECONDS = 30`'s "needs real elapsed time" reasoning. The agent
checked rather than inherited it, and the reasoning does not transfer:
`_BUSY_TIMEOUT_SECONDS` is handed to SQLite, which spends the time inside C
with nothing to observe, whereas drain spends it in `time.sleep` calls the
suite's `no_sleep` fixture already intercepts — so the poll *count* is an
instant, exact assertion and the timeout is recoverable from it. Three
survivors moved from "probably recorded" to killed. The lesson is about
precedent, not about drain: a prior verdict is a hypothesis to re-test, not a
rule to apply.

**The `{"ok": True}` question, settled a third time and now stable.** Both
agents that met it grepped the consuming JS first, as instructed, and both
found what round 2 found: the handlers branch on `.error` and never read `.ok`
or `.started` at all. Neither ruled the flag cosmetic, because at all eleven
sites there was a real unasserted behavioural property at the same endpoint to
attach it to — six round-trip endpoints and two artist endpoints had no test
beyond `routes_catalog.py`'s non-5xx sweep. The rule from §3.6 holds unchanged:
assert the flag where a real assertion is already being made; `cosmetic` is
right only where the flag is the sole thing left.

**One more instance of "the guard is duplicated one layer down".**
`app.py:884`'s shape check is behaviourally redundant with
`roundtrip.set_manual_aliases`'s own validation — both refuse, and both refuse
with 400, so no status assertion can separate them. It is still killable, on
*which* layer refused: the test spies on the writer and asserts it was never
reached. Worth naming because it will recur, and because the tempting verdict
(`equivalent`) is wrong.

**The partition needed one correction, of exactly §3.5's kind.** The handoff's
round 3/4 split assigned `app.py:940` to round-trip and `app.py:952` to
backfill — but 940 is `_BACKFILL_GENERATION_COUNTS`, the constant that the view
returning 952 validates against, twelve lines up in one route. Two agents in
two rounds would have written tests for one endpoint into two files. No rule
was missing; the domain boundary was simply drawn at the wrong line, which is
the failure mode §3.5 already predicted and is worth recording as its second
sighting.

---

### 3.8 Round 4 — the last 33, and an interruption that cost almost nothing

2026-08-28. Three triage agents. **24 gap — fixed, 9 equivalent.** 23 tests
added; the suite went 1093 → 1116 passed, 3 skipped. All 33 verdicts were
re-run by the master session and **all 33 reproduced**.

| domain | test file(s) | survivors | fixed | equiv |
|---|---|---:|---:|---:|
| backfill (`backfill.py` ×7 + `app.py` ×2) | `test_backfill.py` | 9 | 5 | 4 |
| generations + autogroup | `test_generations.py`, `test_canonical_autogroup.py` | 12 | 7 | 5 |
| snapshot page + `api_log` | `test_snapshot_page.py`, `test_api_log.py` | 12 | 12 | — |

**Two of the three agents died mid-run**, together, on an account spend limit —
the third interruption in three rounds, and the first from a cause that has
nothing to do with the machine. **§3.6's rule paid for itself completely.**
Both had been finishing each survivor before starting the next, so:

- the `api_log`/snapshot agent had **finished all twelve** and lost only its
  report, which the master reconstructed from its tests;
- the generations agent had **five of twelve** done, all intact.

**Reconstruction took one batch and no agent.** Feeding all 24 of their
survivors to `recheck.py` as *survive-checks* inverts the tool into a progress
probe: a row that comes back `MISMATCH` — did not survive — is one a test
already kills. That established 17 of 24 closed in a single parallel run,
against §3.6's serial `verify.py one` loop. Neither discharges §6: every one of
those 17 still needed a named kill proof afterwards, and getting the
test-to-mutant attribution right turned out to be the actual work.

**A fifth equivalent class, and it is a common one: `SELECT DISTINCT` whose
rows are consumed by a Python set.** Five of this round's nine equivalents are
this — `backfill.py:90/111` into a `defaultdict(set)`, `generations.py:70/151`
the same, `generations.py:202` into a set comprehension. The DISTINCT is a
database-side optimisation; the Python side dedupes again, so no fixture can
distinguish the two queries however many duplicate rows the join produces.
Since `sqlDISTINCT` will keep generating these, the rule for sweep #3 is:
**check the collection type at the call site before spending any time on a
`sqlDISTINCT` survivor.**

**A sub-variant of the unreachable class: unreachable because of an enforced
FOREIGN KEY.** `backfill.py:100/150` are `dict.get(k, False)` defaults that can
only fire on a referential-integrity violation the schema forbids —
`track.album_id REFERENCES album(album_id)` with `PRAGMA foreign_keys = ON` on
every connection. Second sighting after §3.6's `db.py:531`. The boundary was
drawn deliberately and is worth stating: a test *could* reach these by opening
a connection with foreign keys off, and that was rejected as asserting
behaviour on a database state no code path can produce.

**One line pair, two opposite verdicts** — and reading the survivor rows would
have got it wrong. `canonical_autogroup.py`'s chunked tag-back carries the
stride at :122 and the slice at :123, and the mutants look interchangeable:
`step 501` versus `slice + 501`. They are not. The stride mutant opens a
one-element **gap** at index 500 and that group never gets its `auto_run_id`,
so undo cannot find it — a real defect, killed by a fixture of 130 qualifying
pairs (650 decided groups, which crosses the 500 boundary in 0.2s; the "this
needs a huge fixture" instinct was wrong because each pair decides four tier
groups). The slice mutant makes the chunks **overlap**, and the duplicated
`UPDATE` writes the same `run_id` — identical final state at every length, so
it is equivalent. This is §3's trap 3 again: never judge from the row.

**Both of the master's own steers this round were wrong, and the gate caught
both.** Round 3's was `drain`'s timeouts (§3.7). Round 4's was `api_log`'s two
`duration_ms` mutants, briefed as probably `harness-masked` on the reasoning
that freezegun freezes `time.monotonic` and `0 * 1000 == 0 * 1001`. The clock
is frozen but it can be **ticked**, so the agent advanced it two seconds and
asserted 2000 against the mutant's 2002. The lesson is not about freezegun: a
brief's confident aside is an untested hypothesis with authority attached, and
the only thing standing between it and a wrong verdict is that §6 makes the
claim re-runnable.

#### The fourth tool failure — and the first that did not flatter

§5 said to assume a fourth. It arrived, in `recheck.py`: a 17-job batch
returned **six false `MISMATCH`es with empty output**, every one of which
passed when re-run alone. The cause was not a latent bug but an operating
constraint the tool never stated: **`recheck.py` copies the live repo once per
job, so the working tree must be frozen for the whole batch.** The master had
been creating and deleting a probe file and rewriting a test file while the
batch ran, and the six failures are exactly the contiguous block of jobs
scheduled during that window — a time signature, not the per-worker signature
of §1.1's race, which is what identified it.

Two things make it worth recording. First, it is the **first of the four that
erred pessimistically**: it reported failure where there was success, so it
could only cost time, never manufacture a false pass. Second, the fix is free
and generalises — **run a long batch from a frozen snapshot copy**, whose
`scripts/mutation/recheck.py` computes `REPO` as that copy, leaving the live
tree editable throughout. The end-of-step survivor re-run (§4.1) was run that
way.

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

**§3.4 left nothing outstanding, and neither does the step.** The remaining
212 survivors were closed by the four delegated rounds in §3.5–§3.8. Across the
whole step the suite went **944 → 1116 passed, 3 skipped — 172 new tests** —
and every one of the 252 survivors carries a verdict and a reason.

The per-round figures below are the *verified* ones: the fixed column is kill
proofs re-run to `PASS` by the master session, the non-fix column is verdicts
re-run and still `SURVIVED`. Each round's own section breaks the non-fix half
into equivalent / cosmetic / recorded / harness-masked.

| round | survivors | fixed | non-fix |
|---|---:|---:|---:|
| §3.5 round 1 | 71 | 59 | 12 |
| §3.6 round 2 | 79 | 68 | 11 |
| §3.7 round 3 | 29 | 25 | 4 |
| §3.8 round 4 | 33 | 24 | 9 |
| **four rounds** | **212** | **176** | **36** |

§3.1–§3.4's 40 are not re-tabulated here — that work predates the delegated
rounds and its accounting is by *mutant set* (63 mutants, some of which were
never in the survivor list) rather than by survivor, so the two cannot be added
without double-counting. Its own table stands above.

**One known inconsistency, left visible rather than silently corrected.**
§3.6's prose lists its non-fix verdicts as 8 equivalent + 3 cosmetic + 1
recorded = 12, but its verified count is 11 (68 kill proofs + 11 survive-checks
= 79). One of those three figures is off by one and the round-2 data needed to
say which is gone. The verified totals are the ones used here.

---

### 4.1 The end-of-step survivor re-run — every fix holds

Spec §10's last mechanical criterion: re-run the **whole survivor set** against
the finished tree and confirm that everything ruled `gap — fixed` is now caught
and everything ruled otherwise still survives. Run 2026-08-28 over all 252
survivors of the §2.4 measurement, against a suite of 1116 passed / 3 skipped.

**182 caught, 70 still surviving — and both halves reconcile exactly.**

| | caught | still surviving |
|---|---:|---:|
| the four delegated rounds (§3.5–§3.8) | 176 | 36 |
| §3.1–§3.4 | 6 | 34 |
| **total** | **182** | **70** |

The 70 are the non-fix verdicts, and they decompose with nothing left over:
36 from the four rounds, plus §3.1's 15 equivalents by construction, §3.2's 11
`recorded, not fixed` on the 0% modules, §3.3 B's 5 `not_authenticated`
equivalents and §3.4 E's 3 commit-cadence records. Two independent checks that
this is the right 70 rather than a coincidence of totals: the still-surviving
`config.py` (4) and `spotify_client.py` (7) sum to exactly §3.2's 11, and
`history_import.py`'s 19 are round 1's 7 non-fix plus 12 from the earlier
tranche — the group-E lines `:199/:239/:243` are all present, as named there.

**Every mutant was confirmed to still point at the source it was measured
against.** The results file carries `before` for each, so the check is
mechanical: all 70 surviving rows match their recorded line text. That matters
because line numbers are relative to the measured tree, and a re-run that had
silently drifted onto different code would have produced a clean-looking
result meaning nothing — the same shape as §1.1's failure. Nothing drifted.

**No `gap — fixed` mutant survives.** That is the criterion, and it is met.


## 5. What this says about the next sweep

### 5.1 The question the step was set to answer

`codebase-health-P.md` §8.5 asked whether the sessions after P kept the
standard P set. **The answer is no.** `scrobble.py` is the cleanest case: a
brand-new module from step R, a dedicated 1092-line test file, every convention
documented and in force — and 76%, roughly one mutant in four surviving.
`generations.py` at 88% and `backfill.py` at 85% say the same thing more
quietly. Whatever P installed did not propagate on its own, and nothing in the
ordinary workflow would have revealed that: every one of those modules was
green, reviewed, and shipped.

### 5.2 What mutation found that nothing else could

`CLAUDE.md` poses two questions — *of each test, what would a wrong
implementation have produced?* and *of each module, what does it produce that
no test reads at all?* This run is the evidence that the second is not a
refinement of the first.

The sharpest single finding is a test that **was already green and could never
fail**: `test_the_event_log_is_capped_so_a_long_run_stays_small` both filled
and asserted through `jobs._LOG_LIMIT`, so raising the constant raised the
expectation with it (§3.7). Coverage rated that line fully covered, because it
was — executed, and unobserved. Review had passed it repeatedly. It is the
defect P found in every session, surviving in the suite P built.

The general rule it yields is the most portable thing this run produced: **a
test that derives its expected value from the thing it is testing moves with
the mutant.** Write the expectation as a literal.

### 5.3 The equivalent-class catalogue, and what it is worth

Five classes are now named, and roughly 16 of the ~29 equivalent verdicts fall
into one of them:

1. a digit or SQL keyword mutated **inside a docstring** quoting SQL;
2. **`LIMIT 1` → `LIMIT 2` paired with `.fetchone()`**;
3. **provably unreachable from this call site** — including the sub-variant
   *unreachable because an enforced FOREIGN KEY forbids the state* (§3.8);
4. **`SELECT DISTINCT` whose rows are consumed by a Python set** (§3.8);
5. **a comparison-boundary flip whose branches coincide at the boundary**
   (§3.7).

Be honest about what naming them buys. **Only the first is cheaply eliminable
at the generator** — the rest depend on what the *call site* does with the
result, which `generate.py` cannot see. What the catalogue actually saves is
triage time: these become recognition rather than derivation, which is the
difference between a minute and an hour on each. Sweep #3 should fix the
docstring case in `generate.py` and treat the other four as a checklist to run
before opening the function.

### 5.4 The delegation design worked, and one rule carried it

Spec §7's shape — a master that measures and decides, agents that do the
bounded checkable part — held across four rounds and 212 survivors, with
**every one of 212 returned verdicts re-run and reproduced**. Nothing was ever
overstated by an agent. That is a claim about the *gate*, not about the agents:
§6 is what makes a cold agent's output checkable rather than trusted, and it
twice caught the **master's** own confident briefing being wrong (§3.7's
`drain` timeouts, §3.8's `duration_ms`).

The operational rule that mattered most was learned the hard way in round 2 and
paid for itself twice after: **finish each survivor completely — verdict, test,
proof — before starting the next.** Three of the four rounds were interrupted,
twice by the machine sleeping and once by an account limit. Agents that had
batched their writing lost everything; agents that had not lost almost nothing.
In round 4 an agent died having finished all twelve of its survivors, and the
only thing lost was its report.

Add to that: give every agent its own directory copy and its own `--work` path,
forbid edits to the shared fixtures, and remember that progress is
**reconstructible from disk with no agent involvement** — feeding a dead
agent's survivors to `recheck.py` as survive-checks inverts it into a parallel
progress probe (§3.8).

### 5.5 The instrument lied four times

Three of them flattered — a worker race reporting 89% and 100% on a red
baseline, the same race reproduced in the crash-verification pass whose entire
job is catching false results, and f-strings invisible to both passes. The
fourth (§3.8) erred pessimistically for the first time.

**The standing lesson stands: when this tooling is wrong, the symptom is
usually a *better* number.** A pleasing result is a prompt to check the
instrument. The green-baseline pre-flight is twelve seconds and turns the worst
of those failures into a refusal; the per-worker bucketing now has exactly one
home in `recheck.py`, after being got wrong in three separate places; and a
long batch should be run from a **frozen snapshot copy**, so editing the live
tree cannot corrupt it.

### 5.6 Where not to spend the next pass

Per-module kill rates are published in §2.4 for exactly this. `normalize.py`
(100%), `serve.py` and `config.py` (now closed) and `generations.py` (88%) are
not where the next hour goes. The SQL pass is: it survived at 40% against the
Python pass's 29%, and `db.py` — 18 survivors, 17 of them SQL — remains the
clearest statement that this codebase's Python is well covered and its queries
were not. A sweep #3 that ran the SQL pass alone would find most of what a full
one would, at a quarter of the runtime.

