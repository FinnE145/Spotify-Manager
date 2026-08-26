# S sweep — handoff to the next leader session

Step S is **part-done**. This is what a fresh session needs to finish it
without re-deriving anything. Read `docs/specs/mutation-sweep-S.md` (the spec)
and `S_sweep.md` (the ledger) first; this file is the operational layer on top.

Branch: `feat/mutation-sweep-S`, **not pushed, not merged**. Suite green at
**1023 passed / 3 skipped**. Round 1's four test files are **modified but
uncommitted** — `test_grouping_canvas.py`, `test_history_import.py`,
`test_routes.py`, `test_scrobble.py`.

---

## 1. Where it stands against spec §10

| §10 criterion | state |
|---|---|
| sweep has run over §1's scope | ✅ twice — §2 (997) and §2.4's corrected re-run (1018) |
| `broken`/`crashed` classified, **crash pass clean** | ✅ 719/719, zero anomalies (`S_sweep.md` §2.5) |
| every survivor has a verdict and a reason | ❌ **141 of 252 outstanding** (`S_survivors.md`) |
| every "gap — fixed" has a proven test | ✅ for the 111 done; every proof re-run by the master |
| survivor set re-runs with fixed mutants caught | ❌ end-of-step, not yet run |
| `scripts/mutation/` committed, map names it, map test recurses | ✅ |
| `S_sweep.md` written | ⚠️ §1–§4 + §3.5 written; **§5 is a stub** |
| `pytest` green | ✅ 1023 / 3 skipped |

**111 closed** = §3.1–§3.4's 40 + round 1's 71. **141 remain.**

---

## 2. The work: 141 survivors, by feature domain

`S_survivors.md` is the committed work list, keyed on `before` text so it
survives a rebase. It is still *listed* by module — but it must be *assigned*
by **feature domain**, which is the correction round 1 produced (`S_sweep.md`
§3.5). `app.py` is not one agent's job: its survivors belong to the test file
that owns the feature, and assigning them by module puts two agents in a file a
third already owns.

`app.py`'s remaining 26 break down as: round-trip 10, canonical 8, snapshot 5,
artists 2, backfill 1.

Proposed rounds — **3 agents at a time, and Finn approves each round before it
starts** (his instruction, so he can check usage):

| round | domains (test file) | survivors |
|---|---|---:|
| 2 | canonical (`canonical_detect.py` 32 + `app.py` 8) · entities (27) · db (12) | 79 |
| 3 | round-trip (`app.py` 10) · jobs (10) · artists (`artists.py` 8 + `app.py` 2) | 30 |
| 4 | `api_log` 7 + `backfill` 8 · `generations` 6 + `canonical_autogroup` 6 · snapshot (`app.py` 5) | 32 |

Round 2's canonical block is 40 on its own and is the one worth splitting —
`test_canonical_detect_rules.py` and `test_canonical_detect_queues.py` are
separate files, so it divides cleanly if a fourth agent is acceptable.

Give each agent its **own plain directory copy** of the repo — not a git
worktree (standing rule) — and merge their work yourself. Build the copies by
excluding `.git`, `venv`, `data`, `*.db`, `__pycache__`, `.pytest_cache`, then
**symlink `venv` back in**: `verify.py` computes its interpreter as
`<copy>/venv/bin/python`, so a copy without one cannot run the gate.

---

## 3. What every agent brief must carry

**The gate is non-negotiable** (spec §6). A test is not accepted until its
mutant is re-applied and *that named test* observed to fail:

```
venv/bin/python scripts/mutation/verify.py --work /tmp/symr-kill-<agent> kill \
    --module app.py --line 446 --col 12 --op num \
    --test tests/test_x.py::test_y
```

`--work` goes **before** the subcommand. **Give every agent its own `--work`
path**: the default is `$TMPDIR/symr-mutation`, one shared directory, and three
agents in it is the same race that has already broken this tool twice. Pass
`--col` wherever a line carries several mutants — the tool refuses rather than
guessing. **Re-run every returned proof yourself**; that is the whole point of
§6 and what makes a cold agent's output checkable rather than trusted. Bucket
those re-runs **one directory per thread** — not `idx % N` across a pool, which
is that same race a third time.

**Forbid edits to the shared test infrastructure** — `conftest.py`,
`builders.py`, `fakes.py`, `routes_catalog.py`, `golden.py`. §7.2's partition
keeps two agents out of one test file but says nothing about these, and any
agent might reasonably extend one. Tell them to define helpers locally.

**The four traps, all of which cost real time already:**

1. **A competing value that is itself falsy is as blind as no competitor.**
   `x or 0` and `x or 1` differ only when `x` is falsy, so pairing a `None` row
   against a `0` row tests nothing — both take the same substitution and shift
   together. Use a *truthy* competitor. This bit twice in one session.
2. **Never judge testability from the survivor row.** Three wrong calls came
   from reading `file:line [op]` without opening the function — including one
   whole group ruled "gap" that turned out to be unreachable dead code, and
   `app.py:80`, which reads exactly like the login guard and is not it. Open
   the call path.
3. **Probe unordered scans empirically, don't reason about them.** A query with
   no `ORDER BY` broke ties by `track_id` lexically, not insertion order — the
   opposite of the first guess. Build it, print it, then assert it.
4. **`# source:` goes *inside* the function body**, or
   `test_every_test_declares_where_its_expected_value_came_from` fails. Format:
   `# source: S_sweep.md §3 -- <op> at <module>:<line>`.

**Hard limits** (spec §7.3): no commits, no pushes; edit only its own test
files; never change a non-test module; never run the app or a dev server;
**escalate a survivor that reveals a real bug rather than fixing it** — that is
Finn's call and a separate branch.

**Verdicts** (spec §5): `gap — fixed` / `gap — recorded, not fixed` /
`equivalent` / `cosmetic` / `harness-masked`. Every survivor gets exactly one,
with a reason. There is deliberately **no kill-rate floor**.

**Two equivalent classes are now known** and should be named in briefs so
nobody re-derives them: a digit or keyword mutated **inside a docstring** that
happens to quote SQL, and **`LIMIT 1` → `LIMIT 2` paired with `.fetchone()`**.

---

## 4. Two mechanical criteria, and when each runs

They are not the same kind of thing, which is easy to get wrong:

- **Crash pass** (`verify.py caught`) validates the *measurement*. **Done —
  719/719 caught, zero anomalies** (`S_sweep.md` §2.5), run against the §2.4
  tree before any further tests landed, which is the only point at which it
  means anything. Nothing to repeat unless the sweep itself is re-run.
- **Survivor-set re-run** validates the *fixes*, so it is genuinely
  end-of-step: confirm every "gap — fixed" mutant is now caught. **Not yet
  run**, and premature while 141 survivors are untriaged.

---

## 5. Three tool bugs found so far — assume a fourth

Each was silent, and each made the tool lie in the *flattering* direction:

1. **Worker race in `sweep.py`** (§1.1). Reported 89% and 100% on twelve
   modules; was measuring a red baseline. Fixed with per-worker queues,
   restore-from-proto, and a green-baseline pre-flight.
2. **The same race in `verify.py`'s crash pass**, fixed four commits later
   after it manufactured three false `!! SURVIVED` anomalies — in the one tool
   whose job is catching false results. *The first fix was applied to one call
   site and not the class* — and round 1's master driver reproduced it a third
   time before it was caught pre-run. It is one bug in three places; consider
   giving the bucketing a single home.
3. **f-strings invisible to both passes** (§2.4). PEP 701 tokenizes them as
   `FSTRING_MIDDLE`, not `STRING`. The Python pass mutated inside f-string
   text; the SQL pass missed every f-string query — 34 SQL mutants never
   executed.

**The lesson to carry:** when this tool is wrong, the symptom is a *better*
number, not a worse one. Treat a pleasing result as a prompt to check the
instrument.

---

## 6. Regenerating the survivor list

If the sweep is re-run, rebuild `S_survivors.md` by filtering
`sweep_results.jsonl` for `status == "SURVIVED"` and excluding everything
§3.1–§3.5 already ruled on: the three 0% modules; any `abort(...)`/`api_error`
status code; the group-D lines (`app.py:706`, `entities.py:65/66/191/502/510`,
`backfill.py:77/133`); the group-E lines (`history_import.py:63-66/128-130/
199/239/243`, `backfill.py:28`); the `EXISTS (SELECT 1 …)` / SQL-comment
equivalents; and **everything in §3.5's four domains**. Keep `before` and
`col` — both are load-bearing.
