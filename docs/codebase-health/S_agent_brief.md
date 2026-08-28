# Triage-agent brief — Symr mutation sweep

**This is the brief handed to every triage subagent in step S's four delegated
rounds, kept because it is the reusable half of the step.** It closed 212
survivors across thirteen agents with every returned verdict reproducing under
re-verification. Hand it to an agent as-is; the round-specific survivor list,
working-tree path, `--work` path and test-file assignment go in the spawn
prompt, not here.

Its companion is `S_sweep.md` §5.4, which records why the design works. The
spec is `docs/specs/mutation-sweep-S.md` (§5 verdicts, §6 the gate, §7 the
split).

---

You are one of several triage subagents. Read this whole file before touching
anything. Your own survivor list is in your spawn prompt, not here.

Working tree: **your own directory copy**, given in your prompt. Everything you
do happens there. It has a `venv` symlink back to the real one, so
`venv/bin/python -m pytest` and `scripts/mutation/verify.py` both work.

Background, if you want it: `docs/specs/mutation-sweep-S.md` §5–§7 and
`docs/codebase-health/S_sweep.md` §3.5–§3.8. You do **not** need to read them
to do the job — this brief carries everything operational. Do not read
`docs/codebase-health/P2_coverage_SEALED.md`.

---

## 1. The job

For each survivor on your list: open the code, decide **one verdict**, and if
the verdict is `gap — fixed`, write a test and prove the kill.

| verdict | meaning |
|---|---|
| **gap — fixed** | a real unasserted property → write a test, prove the kill (§3) |
| **gap — recorded, not fixed** | real, but the test costs more than the property is worth |
| **equivalent** | no test *could* kill it — the mutation does not change behaviour |
| **cosmetic** | output formatting nothing depends on |
| **harness-masked** | untestable through the suite because `conftest.py` overwrites the value |

Every survivor gets exactly one verdict **with a written reason**. There is
deliberately **no kill-rate floor** — "recorded, not fixed" is sometimes the
honest answer and is not a failure. But be strict about the boundary:
`equivalent` is a claim that no test *could* kill it, not "no test I want to
write". That distinction has been got wrong before.

**Fix at the level below the one you found.** A test that pins the mutated
line's *output* without exercising the rule that produces it is the single most
common defect in this codebase's history. Ask, of every test you write: *what
would a wrong implementation have produced here?* If the answer is "the same
thing", the test is worthless however green it is.

---

## 2. Work order — this one is not negotiable

**Finish each survivor completely — verdict, test, kill proof — before you
start the next.** Do not read everything and write at the end.

Three of step S's four rounds were interrupted — twice by the machine
sleeping, once by an account limit. Every agent that had been writing per
survivor lost almost nothing; every agent that had read for hours and written
nothing lost everything. In round 4 an agent died having finished all twelve of
its survivors and the only casualty was its unwritten report. Assume you will
be interrupted.

---

## 3. The kill-verification gate

**A test is not accepted until its mutant is re-applied and _that named test_
is observed to fail.** Not "the suite goes red" — the named test, by name.

```
venv/bin/python scripts/mutation/verify.py --work <YOUR-WORK-DIR> kill \
    --module app.py --line 884 --col 36 --op and \
    --test tests/test_roundtrip.py::test_alias_rejects_missing_track_id
```

- `--work` goes **before** the subcommand. Your own `--work` path is in your
  prompt. **Never use the default** — it is one shared directory and three
  agents in it is a race that has already broken this tool twice.
- Pass `--col` whenever the line carries more than one mutant. The tool refuses
  rather than guessing.
- The proof is two runs and `verify.py kill` does both: the suite green without
  the mutant, and that named test failing with it. You want `KILL PROOF: PASS`.
- `verify.py --work <dir> one --module … --line … --col … --op …` runs the
  mutant against the whole suite. Use it to *check a non-fix verdict*: an
  `equivalent` / `cosmetic` / `recorded` claim says no test kills the mutant,
  so it should report `SURVIVED`. Run it — that claim is exactly as checkable
  as a kill, and I re-run all of them.

Everything you return gets re-run by me. Do not report a proof you have not
personally seen pass.

---

## 4. Six traps, each of which has already cost real time

1. **A falsy competitor is as blind as no competitor.** `x or 0` vs `x and 0`,
   or `x or 0` vs `x or 1`, differ only when `x` is falsy — so pairing a `None`
   row against a `0` row tests nothing; both take the same substitution and
   shift together. Use a **truthy** competitor. This bit twice in one session.

2. **The swap trap.** A symmetric two-item fixture lets a JOIN-condition or
   comparison inversion produce a *coincidental one-for-one swap* — same count,
   same sort order, different truth. Break the symmetry with a third,
   unrelated row, or assert on a **value** rather than a count.

3. **Never judge testability from the survivor row.** Three wrong calls came
   from reading `file:line [op]` without opening the function — including a
   whole group ruled "gap" that turned out to be unreachable dead code, and one
   line that reads exactly like the login guard and is not it. Open the call
   path every time.

4. **Probe unordered scans empirically; do not reason about them.** A query
   with no `ORDER BY` broke ties by `track_id` *lexically*, not by insertion
   order — the opposite of the obvious guess. Build it, print it, then assert.

5. **A test that derives its expected value from the mutated thing moves
   with the mutant and can never fail.** Round 3 found this already in the
   suite: the event-log cap test both *filled* and *asserted* through
   `jobs._LOG_LIMIT`, so `_LOG_LIMIT = 201` logged 201, retained 201, and
   passed green. Write the expected value as a **literal**, never read it back
   off the constant, the default argument, or the query you are testing. If
   you find an existing test doing this, fixing it is inside your remit — say
   so in your report.

6. **`# source:` goes _inside_ the function body**, or
   `test_every_test_declares_where_its_expected_value_came_from` fails. Format:

   ```python
   # source: S_sweep.md §3 -- <op> at <module>:<line>
   ```

   Add a sentence naming what the mutant would have done, so the next sweep
   does not re-derive it.

---

## 5. Five equivalent classes are already known

Recognise them; do not re-derive them, and do not spend a test on them:

- a digit or SQL keyword mutated **inside a docstring** that happens to quote
  SQL;
- **`LIMIT 1` → `LIMIT 2` paired with `.fetchone()`** — the read takes the
  first row whatever the bound;
- **provably unreachable from this call site** (e.g. an `or` fallback whose
  left operand came from a row read moments earlier on the same connection);
- **a loop bound made dead by an in-body guard** that terminates on the
  second-to-last index — `for attempt in range(2)` whose body raises on
  `attempt == 1`, so any `range(n>=2)` behaves identically (round 3);
- **a comparison-boundary flip whose two branches coincide at the boundary** —
  `(a, b) if a < b else (b, a)` mutated to `<=` differs only when `a == b`,
  where both branches build the same tuple. Note this is *not* the
  unreachable-state class: the state is trivially reachable, the two answers
  are simply equal (round 3).

If one of yours is a *new* class, say so explicitly in your report — that is a
finding, not bookkeeping.

---

## 6. Hard limits

- **No commits, no pushes.** Ever. I merge your work.
- **Edit only your own test file(s)**, named in your prompt.
- **Never change a non-test module.** Not one line, not a comment.
- **Never edit the shared test infrastructure**: `tests/conftest.py`,
  `builders.py`, `fakes.py`, `routes_catalog.py`, `golden.py`. Define helpers
  locally in your own test file instead. (The file-ownership partition keeps
  two agents out of one test file but says nothing about these.)
- **Never run the app or a dev server.** No network. No Spotify calls.
- **Escalate, do not fix.** A survivor that reveals a *real bug* rather than a
  missing test comes back to me as a question. That is Finn's call and a
  separate branch. Guessing is the one failure mode this design cannot check.
- Anything you cannot decide comes back as a numbered question.

Keep the suite green in your copy: `venv/bin/python -m pytest -q` should end at
**1072 passed, 3 skipped** plus however many tests you added.

---

## 7. What to return

A compact report, one block per survivor, in list order:

```
app.py:884 col36 [and]  — gap — fixed
  reason: <one or two sentences: what the mutant does, why nothing caught it>
  test:   tests/test_roundtrip.py::test_alias_rejects_missing_track_id
  proof:  KILL PROOF: PASS
```

For non-fix verdicts give the reason and the `one` result (`SURVIVED`).
Then: the final `pytest` line from your copy, and any escalations or questions.

Do not paste test source into the report — the files are on disk in your copy
and I read them there. Do tell me every test file you touched.
