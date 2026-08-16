---
name: symr-implement
description: Symr Implement phase — build a feature from its committed spec, asking Finn live for any unforeseen decision. Invoke with `/symr-implement [spec name]`, or when Finn is clearly coding a feature from an existing `docs/specs/<feature>.md`.
---

# Symr Implement phase

You build the feature from its spec at `docs/specs/<feature>.md`. You are **not in a decision-making position** on code — the spec plus Finn's live answers are the source of truth, and Finn should never have to re-prompt from scratch.

## First action: check the model
Implement runs on **Sonnet** (`claude-sonnet-5`). Check this *before anything else* — before resolving the spec, before the branch, before reading a single file. Read it straight off the environment block in your system prompt ("You are powered by the model named…"). **No command — don't shell out for this.**

If it isn't Sonnet, **stop right there**: say which model you're on and that Implement wants Sonnet, and do nothing else — no spec resolution, no branch check, no orientation, no scanning the codebase. Wait for Finn to switch models or tell you to carry on.

That block is written at session start, so it's a *fresh-session* check. If Finn has switched models mid-session it can be stale — so if he says he's already on Sonnet and the block disagrees, take his word and carry on. Never stop him twice for the same check.

## Then resolve the spec + branch
The `/symr-implement` argument, if given, names the spec (e.g. `/symr-implement snapshot` → `docs/specs/snapshot.md`).
1. If an argument is given, resolve it to a spec in `docs/specs/` — exact-ish match; the branch is usually `feat/<same-ish>`.
2. If no argument, infer from the current `feat/*` branch (check `git branch --show-current`). Branch and spec slugs aren't always identical (e.g. `feat/canvas` → `docs/specs/org-canvas.md`).
3. **If exactly one spec obviously matches, use it** (even if the slug isn't 1:1). If a branch has **multiple** plausible specs (features get added to a branch over time), list the candidates and ask which — or use the argument to disambiguate.
4. Always echo what you resolved — "implementing `docs/specs/X.md` on branch `feat/Y`" — before writing any code.
5. If the expected spec/branch doesn't exist, stop and ask (the spec should already exist from the Plan phase).

Confirm you're on the right `feat/*` branch (not `main`, not an inherited wrong one) before committing anything.

## Gate: dev server must not already be running
Before editing any file, check whether something is already listening on port 45660 (e.g. `lsof -i :45660 -sTCP:LISTEN`). This is a read-only check — do it before the first `Edit`/`Write` call, not after.

This isn't the same concern as CLAUDE.md's port rule (which is about *you* needing the port later, for preview/verification). This is about protecting whatever's running *right now*: with `SYMR_DEBUG=1`, Flask's reloader restarts on every `.py` save — including edits from this session, in this same single checkout — and a restart re-runs `db.init_db()`, migrations and all, against the real `symr.db`, with zero review gate, before anything has been tested. A DB migration should never get applied to live data as a side effect of someone else's server noticing a file changed.

If port 45660 is occupied, **stop and flag it to Finn before writing any code** — name the port, say it's likely another chat's session, and ask him to stop it. Don't work around it (different port, editing files anyway, etc.). Once he confirms it's free, proceed normally.

## While building
- **Ask live, one at a time, for anything the spec didn't decide.** The spec is meant to be complete; when a real choice surfaces mid-implementation that it didn't cover, stop and ask rather than deciding yourself. Anything you *could* have foreseen from reading the spec, ask together up front instead of trickling it.
- **One try, then ask.** If an approach fails on the first attempt, stop and ask — no second approach, no layered hacky workarounds. Surface the problem.
- Number questions (sub-letters when nesting). KISS everywhere except security and never-touch-the-real-library, which get done fully and properly.

## Committing
- Commit in Finn's name, only when he asks, in logical units — a few implementation commits as needed depending on scope and fixes-as-you-go, stacked on the spec commit.
- **Don't push, and don't merge to `main`** — that's the Verify finish-up, not this phase.
- **If this session started a dev server (e.g. via the preview tooling) to verify the feature, stop it once the commit lands.** Don't leave port 45660 occupied for whatever session comes next — that's exactly the collision the gate above exists to prevent.
