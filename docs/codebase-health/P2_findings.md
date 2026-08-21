# P2 — Findings

One file for all of P2 (`docs/codebase-health/P2_tests.md` §6). Stable ids — `P2-001`, `P2-002`,
… — never reused and never renumbered, because `xfail(strict=True)` markers cite them. Same entry
template and the same four classifications as P1 (`P1_spec_audit.md` §4).

**P2 records; it does not fix.** A finding here gets a ruling from Finn, and anything not ruled
"fix now" carries a matching `@pytest.mark.xfail(strict=True)` in the suite citing its id. **The
two sets must match exactly** — a bug in one and not the other is the failure this convention
exists to prevent.

**Status: sessions 0 (infrastructure), 1 (Ingest) and 2 (Grouping) complete, 4 findings.** P1's
backlog was empty, so P2 started with nothing inherited. **No finding so far has needed an
`xfail`** — every one has resolved to a fix or to a documentation question, so the debt ledger
`codebase-health-P.md` §4 sanctions is still empty.

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
- **Ruling:** Fix now (2026-08-20).
- **Action:** **Fixed, session 1.** `jobs.py`'s module docstring now says four and names the
  backfill; `scoring.py:664` likewise. Two adjacent claims in the same two sentences were stale
  from the same cause and were corrected with them, rather than left to contradict the corrected
  count: `jobs.py`'s "two of them spend the same Spotify request budget" is now **three** (the
  backfill fetches tracklists; only the history import makes no Spotify requests), and
  `scoring.py`'s "every job ends with a recompute of its own" now names the backfill as the
  exception it has been since M — it doesn't import `scoring` at all, which
  `grouping-fixes-backfill-M.md` §4.5 already documents via P1-017, and `ensure_fresh()` is
  precisely what catches it.
- **Test:** none. There is nothing assertable here: the count lives only in prose. Session 1 owns
  `jobs.py` and is where the correction landed.

**Why it is recorded rather than fixed.** `P2_tests.md` §2: P2 records, it does not fix — and this
was found during a Verify pass over session 0, whose diff deliberately touches no production file.
Session 0's own infrastructure defects (three fidelity gaps in the fake `sp`, two overstated
comments in `conftest.py`) were fixed in place rather than recorded, because that infrastructure
*is* session 0's deliverable and is not production code.

---

## Session 1 — Ingest

### P2-002 — `P2_tests.md` §5's restatement of P1-004's exclusion case asserts something that does not hold

- **Spec:** `docs/codebase-health/P2_tests.md` §5, session 1: "excluding it must then let the
  epoch complete with no previously-captured playlist re-entering the work list." The same
  sentence is in `P1_findings.md`'s P1-004 `Test:` field, at slightly greater length ("on the next
  forced pull with no *other* previously-captured playlist re-entering").
- **Code:** `snapshot.py`, `_resolve_force_epoch` + `_is_full_pull_target`. Excluding the failing
  playlist drops it out of `candidates` upstream, so the remaining candidates are all finished and
  the epoch resolves as complete — which mints a **fresh** epoch. Every captured playlist then
  satisfies `tracks_pulled_at < epoch`, so on that same forced pull **all of them re-enter the
  work list**. The clause as written cannot be asserted; a test that tried would fail against
  correct code.
- **Difference:** the two halves of the sentence describe different runs. "The epoch completes" is
  true and testable. "No previously-captured playlist re-enters" is true only of the *epoch-preserved*
  case — the one where the failing playlist keeps pinning the epoch and the work list stays the
  targeted retry of just that playlist. That is P1-004's actual fix and is where the assertion
  belongs. Once the epoch legitimately completes, a full re-read is the defined meaning of a fresh
  Full pull, not a regression.
- **Why it matters beyond wording:** this is the shape §2 warns about from the other direction. A
  session working the floor literally would write a failing test and then have to decide whether
  the code or the instruction was wrong — and the cheap resolution is to "fix" correct code.
  P1-004's own **ruling** text gets this right ("exclusion alone already lets the epoch complete
  correctly with **no silent re-read**" — *silent* is the load-bearing word, meaning a re-read the
  user did not ask for while resuming). Only the compressed `Test:` restatement lost it.
- **Classification:** `unclear` (an instruction that cannot be satisfied as written; no spec or
  code is wrong — `partial-pulls-J.md` §2.4 itself is accurate).
- **Ruling:** Amend (2026-08-20).
- **Action:** **Amended, session 1.** `P2_tests.md` §5's session-1 bullet now attaches the
  "nothing else re-enters" claim to the epoch-*preserved* case and says only "mint a fresh one"
  of the exclusion case, with a note recording what was wrong; `P1_findings.md`'s P1-004 `Test:`
  field carries the same correction inline, pointing at its own **Ruling** paragraph, which had
  it right all along.
- **Test:** already written, split the way the behaviour actually divides —
  `test_a_failing_playlist_keeps_the_epoch_alive` asserts the epoch is preserved **and** that the
  work list is exactly the failing playlist (the "nothing else re-enters" claim, in the run where
  it holds), and `test_excluding_the_failing_playlist_lets_the_epoch_complete` asserts only that a
  fresh epoch is minted. Both in `tests/test_snapshot_targets.py`.

---

## Session 2 — Grouping

### P2-003 — `builders.make_group` pinned a representative on every group, silently defeating every P1-008 tiebreak test

- **Spec:** none directly — this is P2's own test infrastructure. The behaviour it contradicts is
  `canonical.py`'s: `_INSERT_GROUP_SQL` never writes `representative_track_id`, and
  `pin_representative` writes it **only at the song tier**. `canonical-tracks.md`'s "Representative
  track" section is what the affected tests derive from.
- **Code:** `tests/builders.py`'s `make_group`, as landed in session 0 —
  `(tier, representative_track_id or track_ids[0])`, applied at **all four tiers**.
- **Difference:** every group a fixture built was pinned, at every tier. `canonical.representative()`
  returns the pin before the election runs at all, so a test of the score tiebreak asserted the pin
  instead. It is not a hypothetical: the first version of
  `test_a_higher_score_beats_more_live_memberships` **passed against the pin**, because the pinned
  track (`track_ids[0]`) happened to be the one the score would also have elected. Four further
  tests in the same file failed outright once written, which is what exposed it.
- **Why it matters beyond this session:** this is exactly `P2_tests.md` §1's second failure mode,
  reached through the *fixture* rather than the assertion — a true assertion, a real cited clause,
  green, and unable to fail. Sessions 3 and 4 both use `make_group`; anything they assert about a
  representative would have inherited it.
- **Classification:** `code-wrong` (test infrastructure, not production code — no shipped behaviour
  is affected).
- **Ruling:** Fix in place, session 2 (2026-08-21) — same disposition as session 0's own
  infrastructure defects, which `P2-001`'s closing note records as fixed rather than queued, on the
  grounds that this infrastructure *is* a P2 deliverable.
- **Action:** **Fixed, session 2.** `make_group`'s `representative_track_id` now defaults to NULL,
  matching what `canonical.py` writes. A test that wants a pin calls `canonical.pin_representative`
  or passes the argument. The builder's docstring carries the reasoning so the default is not
  "tidied" back. No existing test depended on the pin —
  `test_canonical_read_paths_accept_a_built_group` asserts only that the election returns *some*
  member, which still holds.
- **Test:** `tests/test_builders.py::test_make_group_leaves_the_representative_unpinned`, plus the
  six tiebreak tests in `tests/test_canonical_read.py` that now discriminate. All four
  `representative()` mutations (elect by live-membership count; join `score` on the group's own
  tier; sort a membership-less track first; re-filter `oldest_added` to live rows) were confirmed
  to fail the suite afterwards.

---

### P2-004 — `_cleanup_tier`'s stale-pin branch may be unreachable through `apply_partition`

- **Spec:** `docs/canonical-tracks/grouping-engine.md`, "Reconciliation algorithm" step 6 — "For a
  surviving group whose membership changed, clear `representative_track_id` to NULL if the pinned
  track is no longer a member."
- **Code:** `canonical.py`, `_cleanup_tier`'s second loop (the `reps` query and the `UPDATE ... SET
  representative_track_id = NULL`).
- **Difference:** the clause describes a state that `apply_partition` appears unable to produce.
  Step 4 reuses an existing group id only when a part's members cover that group's **full** current
  membership — which includes the pinned track — so a group that *survives* still contains its pin.
  A group that loses its pinned track is one the item genuinely split, and that group yields new
  ids for both halves and is deleted as an orphan. Three adjacent routes were checked and none
  reaches it either: upward closure (step 3) drags a pinned track's group-mates along with it
  rather than leaving them behind; under `cleanup=False` batching an emptied group is removed by
  `_cleanup_tier`'s **first** loop, which runs before the rep check in the same pass; and
  `canonical_autogroup.undo()` restores `canonical_group` and `track_group` together from one
  consistent snapshot, so it cannot strand a pin either.
- **Not claimed:** that it is definitely dead. This is a reading of the algorithm plus three
  checked callers, not an exhaustive proof, and the branch is cheap and defensive. Recorded so P3
  — which moves code and would otherwise have to re-derive this — has the question written down.
- **Classification:** `unclear` (possibly-dead defensive code; no behaviour is wrong either way).
- **Ruling:** Leave as `unclear` (2026-08-21). **Not a P3 deletion candidate** — explicitly
  distinguished from `all_candidate_groups`, which P1-009 *did* flag for removal on the strength of
  a full-codebase search for callers. The reasoning is asymmetric on purpose: the cost of keeping a
  cheap defensive branch is nothing, and the cost of removing one that turns out to be load-bearing
  is a silent wrong pin. A reading of an algorithm is not the same evidence as a caller search, so
  it does not license the same action. Recorded so P3 inherits the question rather than
  re-deriving it, and so nobody deletes it as "obviously dead" while moving code.
- **Test:** `tests/test_canonical_engine.py::test_cleanup_clears_a_pin_whose_track_is_no_longer_a_member`
  drives `cleanup_all_tiers` **directly** on a hand-built stale pin, and its docstring says why it
  does not go through `apply_partition`. Its sibling
  `test_a_pin_survives_a_change_that_keeps_the_pinned_track_in_the_group` covers the negative case,
  which *is* reachable normally. No `xfail` is owed: nothing is asserted to be broken.
