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
- Diff the branch against `main` and check it against the spec: is everything specced actually built, and is anything built that the spec didn't call for?
- Run the app / tests and confirm the feature works. Prefer driving the real behavior over trusting the code.
- **Hand focus/blur-dependent or final-visual checks to Finn** rather than engineering synthetic-event workarounds — a programmatic `blur()` won't fire without real OS focus, so it's wasted effort. Batch such manual checks to the end. An ungated harness for *objective* checks (dimensions, DOM state) is fine — delete it when done.
- Report findings. Fix only what Finn confirms; if bigger work is needed, hand back to a new implement chat.

## Finish-up (only when Finn says so)
When Finn says finish up / looks good, and once verification has passed:
1. **Update the Codebase Map in `CLAUDE.md`** to cover anything the feature added or moved — new modules, templates, `static/js/` files, directories, renamed routes. It's the map a fresh session reads first, and it goes stale silently. Check it against the real tree (`ls` the repo), not just against the diff.
2. **Check the step off in `docs/Planning/roadmap.md`** if the spec names a roadmap step (its header line says so). Mark that step's section `✅ DONE`, point it at the spec that is now authoritative for what shipped, update the *Order* diagram and the "X, Y and Z have landed" line, and correct anything the section claims that implementation disproved. If the spec came from no roadmap step, skip this and say so.
3. Commit any confirmed verify-phase fixes in his name (logical units), including that map update.
4. **`git merge --ff-only` the feature branch into `main`** — this keeps history linear and avoids merge-commit clutter. If it can't fast-forward, stop and flag it rather than forcing a merge commit.
5. Push — this is the one sanctioned push, and only when Finn has asked to finish up.
6. Leave the feature branch in place by default — a feature *category* usually accrues more work later (e.g. more canvas specs), so `feat/*` branches are kept. Only delete a branch that's a genuine one-off (a `chore/`/`fix/` task that won't recur as the same thing), and only if Finn wants it gone.
7. **Stop any dev server this session started** (port 45660) — the Review step above runs the app, and that server must not outlive the session. Don't leave it occupied for whatever comes next.
