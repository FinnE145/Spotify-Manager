Continue step P1 (spec audit) of the codebase-health roadmap step for Symr, on branch
`feat/codebase-health-P` (confirm with `git branch --show-current` — should already be checked
out, don't switch or create a new one).

**Read first, in this order:**
1. `CLAUDE.md` — conventions, and specifically the "When Asking Questions" numbered-question
   rules, since that's how this whole session runs.
2. `docs/specs/codebase-health-P.md` §0 (why P1 isn't a normal spec-driven session) and §10
   (current status).
3. `docs/codebase-health/P1_spec_audit.md` — P1's actual instructions: what this session does
   and must not do (§2), the findings-doc template and the four classifications (§4), how to
   work with Finn (§5), and what "done" means (§6–§7).
4. `docs/codebase-health/P1_findings.md` in full — **21 findings (P1-001–P1-021), all recorded,
   the audit itself is complete.** No more background agents are running; this list is final.

**Where things stand:** all 17 specs in `docs/specs/` have been read against their code, every
one independently re-audited at least once beyond the original pass (via clustered review, solo
dedicated re-review, and/or a full from-scratch blind audit — several got two or three of
these). Every real difference found is filed in `P1_findings.md` with a stable id, a spec quote,
a code citation, a classification, and a proposed test. **Nothing has been ruled on. Nothing has
been amended. No spec is stamped "Audited."** That's this session's job — purely working through
rulings now, no more auditing needed.

**What this session does, per `P1_spec_audit.md` §5:**
- Work through the findings in batches, presenting them to Finn **numbered**, per CLAUDE.md's
  rules — reasoning above the list, one decision per line, not one finding per message. The
  findings file's index table groups by spec; go spec by spec, or by whatever grouping makes a
  coherent batch, not all 21 at once.
- For each finding, get Finn's ruling on two things: (1) does the classification stand as
  written (`spec-stale` / `code-wrong` / `underspecified` / `unclear`), and (2) the action —
  amend the spec, fix the code now, queue it for the P2 fix session, or no change.
- Apply what's ruled: edit the spec file for "amend," edit the code for "fix now" (only when
  Finn asks for it in the moment — this is explicitly allowed by P1, see the pre-spec's own
  framing that P1 is not behaviour-preserving), leave code untouched and note the finding id for
  "queue," just record the ruling for "no change."
- **When every finding tied to one spec is resolved**, add the line
  `**Audited 2026-XX-XX** against the code, as part of P1 (docs/codebase-health/P1_spec_audit.md).`
  to that spec's header (today's date), per §6. A spec without that line hasn't been ruled on
  yet and P2 must not derive tests from it.
- Update `docs/specs/codebase-health-P.md` §10's status table as specs get resolved.
- Keep a running list, same spirit as the findings file itself, of everything ruled `queue for
  fix session` — that's P2's `xfail(strict=True)` backlog, and the findings doc and that backlog
  must match exactly per `codebase-health-P.md` §4.

**A few findings worth flagging up front, since they're not just documentation drift** (check
`P1_findings.md` for current ids/details, don't take this list as exhaustive or final):
- A real request-cost bug in the pull-resume logic (an undocumented discount silently turns a
  targeted retry into a full ~230-request re-pull once the only unfinished playlist is a
  permanently-failing one).
- A round-trip reconciliation bug (a probe-confirmed-dead uri never actually changes state and
  gets re-probed forever; a stop mid-reconciliation gets recorded as a completed run).
- An inverted boolean in the grouping engine's `neutral`-suffix handling — the single largest
  behavioral divergence found in the whole audit.
- Several small independent bugs on the entity pages (a misleading "first 50 tracks" note, a
  mislinked Edit button, artist-image selection not picking the largest, a failed detail fetch
  retrying forever instead of once).
- A few stale security-relevant claims (specs asserting "no write scopes" or "no auth" that are
  now false).
- `async-recompute-N.md`'s transient-failure self-heal guarantee appears to no longer hold in
  the code — worth a real decision, not just a doc fix.
- `scoring-H.md`'s subtier blend (§6) calls for shrinkage the code deliberately skips, and
  neither the spec nor its own executable reference (`docs/scoring/tuning_prototype.py`) ever
  actually settled the question — worth closing explicitly even though the blast radius is
  small. Otherwise `scoring-H.md` came back as the best-verified spec in the project — only this
  one real gap in 1265 lines.
- `canonical-tracks.md`'s "Out of scope" section is contradicted twice more (no-review
  auto-grouping, cross-session undo — same pattern as its already-known representative-track
  drift), and a latent ordering bug where `mark_reviewed_pairs` doesn't enforce the invariant
  its own docstring claims.

**When P1 is fully done** (`P1_spec_audit.md` §7): all specs audited and stamped, every finding
has a classification/ruling/action, every "fix now" is committed, `codebase-health-P.md` §10
points at the finished findings doc, then `git merge --ff-only` into `main` per
`codebase-health-P.md` §8 — **no Verify session for P1**, Finn's rulings are the verification.
Once merged, write `docs/codebase-health/P2_tests.md` from what the audit actually found, and
that starts P2.

**Git tripwires still apply** (`CLAUDE.md`): confirm the branch before new work (already done
above), no premature merge/push — the `--ff-only` merge only happens at the very end once P1 is
completely finished, not partway through.
