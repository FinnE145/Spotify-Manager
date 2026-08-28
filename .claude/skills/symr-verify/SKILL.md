---
name: symr-verify
description: Symr Verify phase — review a feature's diff against its spec, run the app, report, and do the finish-up merge. Invoke with `/symr-verify`, or when Finn is clearly reviewing/validating a completed feature against its spec.
---

# Symr Verify phase

Fresh chat that checks a completed feature against its spec, then does the finish-up. You review and report; you make fixes **only with Finn's explicit confirmation**, otherwise hand back to a new implement chat.

## First action: check the model
Verify runs on **Opus** (`claude-opus-5`). Check this *before anything else* — before resolving what you're verifying, before reading a single file. Read it straight off the environment block in your system prompt ("You are powered by the model named…"). **No command — don't shell out for this.**

If it isn't Opus, **stop right there**: say which model you're on and that Verify wants Opus, and do nothing else — no diffing, no orientation, no scanning the codebase. Wait for Finn to switch models or tell you to carry on.

That block is written at session start, so it's a *fresh-session* check. If Finn has switched models mid-session it can be stale — so if he says he's already on Opus and the block disagrees, take his word and carry on. Never stop him twice for the same check.

## Then resolve what you're verifying
`/symr-verify` takes no argument — infer from the current checkout.
1. Check `git branch --show-current` and find the matching spec in `docs/specs/`. Slugs aren't always 1:1 (e.g. `feat/canvas` → `docs/specs/org-canvas.md`).
2. If one spec obviously matches the branch, use it. If several plausibly match, list them and ask which.
3. Echo what you're verifying — "verifying `docs/specs/X.md` on branch `feat/Y` vs `main`" — before starting.

## Review
- **Run the test suite first: `venv/bin/python -m pytest`.** Do this before reading the diff — a red suite changes what the review is about, and finding out after you've reviewed everything wastes the pass.
- **Then measure the session's tests, mutation first and coverage second — both before you change anything.** The ordering is what makes them worth taking. Verify does fix things in place, often, and that is fine; what it must not do is read a gap list *before* the work it is measuring is finished, because a gap list in view buys executed lines rather than assertions. Measured against a finished diff, it can only direct your own targeted fixes, which is the whole point of measuring.
  - **Mutation.** Break what the session's new tests cover — invert a comparison, empty a returned key, drop a sort — and confirm the suite fails, and fails *at that test*. **Every one of step P's most valuable findings came from a Verify pass re-running a measurement rather than reading the session's account of it.** A green suite nobody tried to break is not evidence that anything is asserted. **Restore the file by *writing* the original back, never by moving a `.bak` over it, and run the child with `PYTHONDONTWRITEBYTECODE=1`.** Python validates a `.pyc` on `(source mtime, source size)`, so a same-second restore of a byte-identical change (`1` -> `2`, `MAX` -> `MIN`) leaves the interpreter running **mutated bytecode from a clean source tree** — invisible to `grep`, to `git diff` and to `git status`, all of which read the source. It has already cost one session an hour of chasing a bug that did not exist.
  - **Coverage, second.** `venv/bin/python -m pytest --cov=<the modules this session touched>`. A gap-finder, not a gate — there is no threshold, and a suite of `assert True` reaches 100%. It goes second because it is structurally blind to what mutation just looked for: code producing a value nothing reads executes exactly as it would if the value were read.
- Diff the branch against `main` and check it against the spec: is everything specced actually built, and is anything built that the spec didn't call for?
- Run the app and confirm the feature works. Prefer driving the real behavior over trusting the code.
- **Hand focus/blur-dependent or final-visual checks to Finn** rather than engineering synthetic-event workarounds — a programmatic `blur()` won't fire without real OS focus, so it's wasted effort. Batch such manual checks to the end. An ungated harness for *objective* checks (dimensions, DOM state) is fine — delete it when done.
- Report findings. Fix only what Finn confirms; if bigger work is needed, hand back to a new implement chat.

## Finish-up (only when Finn says so)
**The suite must pass before any of this.** `venv/bin/python -m pytest`, green, on the branch as it stands — this is the gate, and it is the reason the suite exists (`docs/specs/codebase-health-P.md` §7): regression coverage that only runs when someone remembers is not coverage. Verify is exactly where "looks good, finish up" happens, so this is where it is enforced. A failure is a finding to report, never something to skip past or to fix by weakening the test.

When Finn says finish up / looks good, and once verification and the suite have both passed:
1. **Update the Codebase Map in `CLAUDE.md`** to cover anything the feature added or moved — new modules, templates, `static/js/` files, directories, renamed routes. It's the map a fresh session reads first, and it goes stale silently. Check it against the real tree (`ls` the repo), not just against the diff.
2. **Check the step off in `docs/Planning/roadmap.md`** if the spec names a roadmap step (its header line says so). Mark that step's section `✅ DONE`, point it at the spec that is now authoritative for what shipped, update the *Order* diagram and the "X, Y and Z have landed" line, and correct anything the section claims that implementation disproved. If the spec came from no roadmap step, skip this and say so.
3. Commit any confirmed verify-phase fixes in his name (logical units), including that map update.
4. **`git merge --ff-only` the feature branch into `main`** — this keeps history linear and avoids merge-commit clutter. If it can't fast-forward, stop and flag it rather than forcing a merge commit.
5. Push — this is the one sanctioned push, and only when Finn has asked to finish up.
6. Leave the feature branch in place by default — a feature *category* usually accrues more work later (e.g. more canvas specs), so `feat/*` branches are kept. Only delete a branch that's a genuine one-off (a `chore/`/`fix/` task that won't recur as the same thing), and only if Finn wants it gone.
7. **Stop any dev server this session started** (port 45660) — the Review step above runs the app, and that server must not outlive the session. Don't leave it occupied for whatever comes next.
8. **Read `docs/Planning/roadmap.md` and tell Finn what's next** — the next few steps in order, one line each, said in your own words from the roadmap's own sections. This is the last thing you do, and it exists because the roadmap is the standing plan but nobody reads it at the *end* of a session, which is exactly when the next step gets chosen. Name each step by its letter and title, say in one line what it is, and flag anything that gates it or that this session's work just changed. Three or four is usually right — enough to choose from, not a recitation of the file. If the roadmap's order looks wrong now because of what just landed, say so rather than reading it out unchanged.
