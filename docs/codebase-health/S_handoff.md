# S sweep — handoff, discharged

**Step S is complete.** This file was the operational layer for finishing it
across sessions; it is kept because the operating recipe is the part a future
sweep would otherwise re-derive, which is the mistake `mutation-sweep-S.md` §0
exists to complain about.

Where things now live:

| what | where |
|---|---|
| the result, round by round, and every verdict | `S_sweep.md` §3.1–§3.8 |
| what the run says about the next sweep | `S_sweep.md` §5 |
| the end-of-step re-run that validates the fixes | `S_sweep.md` §4.1 |
| the brief handed to every triage agent | `S_agent_brief.md` |
| why the delegation design works | `S_sweep.md` §5.4 |
| the tooling | `scripts/mutation/` |

---

## The operating recipe, for sweep #3

**Worker copies, not worktrees** (standing no-worktrees rule). Build each by
excluding `.git`, `venv`, `data`, `*.db`, `__pycache__`, `.pytest_cache`,
`.coverage*`, then **symlink `venv` back in** — `verify.py` computes its
interpreter as `<copy>/venv/bin/python` and a copy without one cannot run the
gate:

```
rsync -a --exclude='.git' --exclude='venv' --exclude='data' --exclude='*.db' \
      --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.coverage*' \
      /Users/finne/Projects/Spotify-Manager/ "$DEST/"
ln -s /Users/finne/Projects/Spotify-Manager/venv "$DEST/venv"
```

Smoke-test one before spawning: the suite must be green *inside the copy*, and
`verify.py one` must report `SURVIVED` for a known survivor.

**Give every agent its own `--work` path.** The default is a single shared
directory and parallel agents in it is the race that has broken this tooling
twice.

**Re-run every returned verdict** with `scripts/mutation/recheck.py` — kill
proofs *and* the `equivalent` / `cosmetic` / `recorded` claims, which are
exactly as checkable. That is spec §7.1 step 4 and it is not delegable.

**Freeze the working tree for the duration of a batch, or run the batch from a
frozen snapshot copy.** `recheck.py` copies the live repo once per job, so
editing the tree mid-batch corrupts jobs silently — six false `MISMATCH`es with
empty output, `S_sweep.md` §3.8. Running from a snapshot makes `REPO` resolve
to the snapshot and leaves the live tree editable, which is how §4.1 was run.

**Merge each agent's work yourself, and verify the file claim rather than
trusting it** — `diff -rq` the whole copy against the repo. Every agent's claim
held, and checking cost seconds.

## The two mechanical criteria, and when each runs

Not the same kind of thing, which is easy to get wrong:

- **Crash pass** (`verify.py caught`) validates the *measurement* — that no
  signal-killed child was read as a kill. Run it against the sweep's own tree
  **before any further tests land**, which is the only point at which it means
  anything. Done for this run: 719/719, zero anomalies (§2.5).
- **Survivor-set re-run** validates the *fixes*. Genuinely end-of-step, and
  meaningless while survivors are untriaged. Done for this run: §4.1.

## What a future sweep should change

`S_sweep.md` §5.3 and §5.6 carry this properly. In short: fix the
docstring-quoted-SQL case in `generate.py`, treat the other four
equivalent classes as a pre-triage checklist rather than a generator change,
and consider running **the SQL pass alone** — it survived at 40% against the
Python pass's 29% and would find most of what a full sweep finds at a quarter
of the runtime.
