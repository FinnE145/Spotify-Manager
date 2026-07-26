---
name: verify
description: Symr Verify phase — review a feature's diff against its spec, run the app, report, and do the finish-up merge. Invoke with `/verify`, or when Finn is clearly reviewing/validating a completed feature against its spec.
---

# Verify phase

Fresh chat that checks a completed feature against its spec, then does the finish-up. You review and report; you make fixes **only with Finn's explicit confirmation**, otherwise hand back to a new implement chat.

## Resolve what you're verifying
`/verify` takes no argument — infer from the current checkout.
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
1. Commit any confirmed verify-phase fixes in his name (logical units).
2. **`git merge --ff-only` the feature branch into `main`** — this keeps history linear and avoids merge-commit clutter. If it can't fast-forward, stop and flag it rather than forcing a merge commit.
3. Push — this is the one sanctioned push, and only when Finn has asked to finish up.
4. Leave the feature branch in place by default — a feature *category* usually accrues more work later (e.g. more canvas specs), so `feat/*` branches are kept. Only delete a branch that's a genuine one-off (a `chore/`/`fix/` task that won't recur as the same thing), and only if Finn wants it gone.
