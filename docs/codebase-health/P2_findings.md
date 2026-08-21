# P2 — Findings

One file for all of P2 (`docs/codebase-health/P2_tests.md` §6). Stable ids — `P2-001`, `P2-002`,
… — never reused and never renumbered, because `xfail(strict=True)` markers cite them. Same entry
template and the same four classifications as P1 (`P1_spec_audit.md` §4).

**P2 records; it does not fix.** A finding here gets a ruling from Finn, and anything not ruled
"fix now" carries a matching `@pytest.mark.xfail(strict=True)` in the suite citing its id. **The
two sets must match exactly** — a bug in one and not the other is the failure this convention
exists to prevent.

**Status: session 0 (infrastructure) complete, 1 finding.** P1's backlog was empty, so P2 started
with nothing inherited; this is the first entry.

---

## Session 0 — Infrastructure

### P2-001 — `jobs.py` and `scoring.py` still describe three background jobs, not four

- **Spec:** none — this is code documentation, not a spec clause. The count is settled by
  `jobs.py:22`'s own `_active` comment: `None | "snapshot" | "history_import" | "roundtrip" |
  "backfill"`, and by `CLAUDE.md`, which calls `backfill.py` "the **fourth** background job".
- **Code:** three sites, all stale since `backfill.py` landed in step M:
  - `jobs.py:2` — "the status/event-log plumbing all three jobs share"
  - `jobs.py:4` — "Symr runs three long jobs -- the snapshot pull, the play-history import and
    the foreign-track round-trip -- and exactly one may run at a time", which then enumerates the
    three and omits the backfill
  - `scoring.py:664` — "All three jobs commit continuously as they run"
- **Difference:** there are four jobs sharing the one slot. The module docstring of the file that
  *owns* the slot names three and lists them, so a cold session reading `jobs.py` top-to-bottom is
  told the wrong set six lines before the correct one. No behaviour is affected — `try_start` is
  name-agnostic and `backfill.py` claims the slot correctly.
- **Classification:** `spec-stale` (the doc side of it — code is right, the prose describing it is
  not)
- **Ruling:** _(Finn)_
- **Action:** _(Finn — amend / fix now / queue / no change)_
- **Test:** none. There is nothing assertable here: the count lives only in prose. Session 1 owns
  `jobs.py` and is where the correction naturally lands.

**Why it is recorded rather than fixed.** `P2_tests.md` §2: P2 records, it does not fix — and this
was found during a Verify pass over session 0, whose diff deliberately touches no production file.
Session 0's own infrastructure defects (three fidelity gaps in the fake `sp`, two overstated
comments in `conftest.py`) were fixed in place rather than recorded, because that infrastructure
*is* session 0's deliverable and is not production code.
