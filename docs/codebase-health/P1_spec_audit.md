# P1 — Spec audit

**Instructions for the session that runs P1.** Read `docs/specs/codebase-health-P.md` first — its
§0 explains why this file is written as instructions rather than as a spec, and §1–§3 hold the
reasoning behind everything below. Do not re-litigate those decisions here.

**Where this came from.** The planning session on 2026-08-16 that produced `codebase-health-P.md`.
Nothing has been built yet; this is P's first working session.

---

## §1 The goal

Bring all 17 specs in `docs/specs/` into exact correspondence with the code, so that P2 can write
specification tests from them and trust the answer.

The reasoning, restated once because it is the thing to keep in mind the whole way through: a test
written from code encodes *what it does* and freezes existing bugs into a permanently green suite.
A test written from an audited spec encodes *what it should do*. **P1 is what makes P2's assertions
worth anything.** Every hour spent here is an hour P2 does not spend accidentally canonizing a bug.

There is a second output worth as much: `docs/specs/` is 6,381 lines and is the main thing a cold
session reads. Its drift is the same class of problem as the `CLAUDE.md` drift the pre-spec flags,
and this fixes it directly.

---

## §2 What this session does, and what it must not do

**Does:** read each spec against the code it describes, enumerate every difference, classify each
one, and put it in front of Finn.

**Must not:**

- **Rule on a finding.** Whether the spec is wrong or the code is wrong is a statement about
  *intent*, and intent is Finn's. Classify and present; never decide.
- **Fix a bug on your own initiative.** P1 is explicitly not behaviour-preserving and inline fixes
  are allowed — but only ones Finn asks for in the moment, item by item.
- **Edit a spec before its finding is ruled on.** Amendments are applied after the ruling, not as
  you go.
- **Tidy specs for style.** Wording, structure and length are not findings. Only correspondence
  with the code is.

**Delegation** follows `codebase-health-P.md` §3. For P1 that means: a subagent reads a spec
against its code and **enumerates differences**; it never decides which side is right. Give it the
spec section and the module, ask for candidate differences with exact file:line and the exact spec
clause, and verify its output before it reaches the findings doc — a fabricated or misread
difference costs Finn a ruling on something that was never wrong.

---

## §3 Batches

Grouped so each session reads one coherent slice of code once. Four batches, near-balanced by spec
volume.

| batch | specs | spec lines | primary code |
|---|---|---:|---|
| **1 — Ingest** | `track-metadata-A`, `snapshot`, `partial-pulls-J`, `play-history-C`, `foreign-roundtrip-D` | 1,817 | `snapshot.py`, `db.py`, `history_import.py`, `roundtrip.py`, `api_log.py`, `jobs.py`, `spotify_client.py` |
| **2 — Grouping** | `canonical-tracks`, `canonical-fixes`, `detection-artist-model`, `grouping-catch-up-E`, `grouping-fixes-backfill-M` | 1,678 | `canonical.py`, `canonical_detect.py`, `canonical_autogroup.py`, `artists.py`, `backfill.py` |
| **3 — Scoring** | `scoring-H`, `async-recompute-N` | 1,685 | `scoring.py` |
| **4 — Read paths & UI** | `entity-pages-K`, `generations-B`, `site-shell`, `error-pages`, `org-canvas` | 1,201 | `entities.py`, `generations.py`, `grouping.py`, `app.py` routes, `templates/` |

**Start with batch 1.** It carries the highest corruption risk — it is the code that writes
`membership` and the only code that writes to Spotify — and it is what P2's Spotify-bound tier will
test. It also doubles as the calibration run: expect to adjust the finding template in §4 after it,
and say so rather than silently diverging.

**Batches overlap.** `grouping-fixes-backfill-M` describes `roundtrip.py` and `entities.py` as well
as the grouping modules; `partial-pulls-J` touches `jobs.py` and `api_log.py`, which several others
lean on. **File each finding once**, in whichever batch finds it, and cross-reference rather than
duplicating.

Each batch is its own session unless Finn says otherwise. Batch 1 is likely more than one.

---

## §4 The findings document

One file for all of P1: **`docs/codebase-health/P1_findings.md`**, appended to as batches complete.

Stable ids — `P1-001`, `P1-002`, … — never reused and never renumbered, because P2's tests and
`xfail` markers will cite them.

```markdown
### P1-017 — `_is_stale` treats a null stored snapshot_id as stale

- **Spec:** `partial-pulls-J.md` §2.1 — "<exact quoted clause>"
- **Code:** `snapshot.py:278` — <what it actually does>
- **Difference:** <one or two sentences, precise>
- **Classification:** `underspecified`
- **Ruling:** _(Finn)_
- **Action:** _(Finn — amend spec / fix now / queue for fix session / no change)_
- **Test:** <what P2 should assert, and in which tier — or "none">
```

**Classifications**, exactly four:

| | meaning |
|---|---|
| `spec-stale` | Code is right; the spec describes something it no longer does |
| `code-wrong` | Spec is right; the implementation does not match — a bug |
| `underspecified` | The spec is silent, or too vague to write a test from. The code may still be right |
| `unclear` | Neither obviously matches intent; needs Finn to decide what the behaviour should be |

`underspecified` is expected to be the largest category and is not a lesser finding — a clause too
vague to write a test from is precisely what blocks P2.

**The `Test:` field is not optional bookkeeping** — it is P2's test list, being written as a side
effect of the audit. Fill it in for every finding, including the ones where the answer is "none".

---

## §5 Working with Finn

He is sitting with this. Batch findings and present them numbered, per `CLAUDE.md`'s rules —
reasoning above the list, one decision per line inside it. Do not present 40 findings at once; go
in coherent runs and let him rule as you go.

Where a ruling is `fix now`, fix it in that session and note the commit in the finding. Where it is
`queue for fix session`, leave the code alone — P2 will carry it as an `xfail(strict=True)` citing
the finding id.

---

## §6 When a spec's findings are all resolved

Apply the ruled amendments, then add one line to that spec's header:

```
**Audited 2026-XX-XX** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).
```

That line is what P2 reads to know a spec is a trustworthy source for specification tests. A spec
without it has not been audited, and P2 must not derive assertions from it.

---

## §7 Done

P1 is finished when:

1. All 17 specs are audited and carry the §6 line.
2. Every finding in `P1_findings.md` has a classification, a ruling and an action.
3. Every `fix now` is committed; every `queue for fix session` is listed in one place for P2 to
   pick up as `xfail` markers.
4. `codebase-health-P.md` §10's status table is updated and points at `P1_findings.md`.
5. The branch is merged `--ff-only` into `main` (`codebase-health-P.md` §8) — no Verify session for
   P1; Finn's rulings are its verification.

Then write `docs/codebase-health/P2_tests.md` from what the audit actually found, and start P2.
