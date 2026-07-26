---
name: implement
description: Symr Implement phase — build a feature from its committed spec, asking Finn live for any unforeseen decision. Invoke with `/implement [spec name]`, or when Finn is clearly coding a feature from an existing `docs/specs/<feature>.md`.
---

# Implement phase

You build the feature from its spec at `docs/specs/<feature>.md`. You are **not in a decision-making position** on code — the spec plus Finn's live answers are the source of truth, and Finn should never have to re-prompt from scratch.

## Resolve the spec + branch first
The `/implement` argument, if given, names the spec (e.g. `/implement snapshot` → `docs/specs/snapshot.md`).
1. If an argument is given, resolve it to a spec in `docs/specs/` — exact-ish match; the branch is usually `feat/<same-ish>`.
2. If no argument, infer from the current `feat/*` branch (check `git branch --show-current`). Branch and spec slugs aren't always identical (e.g. `feat/canvas` → `docs/specs/org-canvas.md`).
3. **If exactly one spec obviously matches, use it** (even if the slug isn't 1:1). If a branch has **multiple** plausible specs (features get added to a branch over time), list the candidates and ask which — or use the argument to disambiguate.
4. Always echo what you resolved — "implementing `docs/specs/X.md` on branch `feat/Y`" — before writing any code.
5. If the expected spec/branch doesn't exist, stop and ask (the spec should already exist from the Plan phase).

Confirm you're on the right `feat/*` branch (not `main`, not an inherited wrong one) before committing anything.

## While building
- **Ask live, one at a time, for anything the spec didn't decide.** The spec is meant to be complete; when a real choice surfaces mid-implementation that it didn't cover, stop and ask rather than deciding yourself. Anything you *could* have foreseen from reading the spec, ask together up front instead of trickling it.
- **One try, then ask.** If an approach fails on the first attempt, stop and ask — no second approach, no layered hacky workarounds. Surface the problem.
- Number questions (sub-letters when nesting). KISS everywhere except security and never-touch-the-real-library, which get done fully and properly.

## Committing
- Commit in Finn's name, only when he asks, in logical units — a few implementation commits as needed depending on scope and fixes-as-you-go, stacked on the spec commit.
- **Don't push, and don't merge to `main`** — that's the Verify finish-up, not this phase.
