---
name: symr-plan
description: Symr Plan phase — question-driven authoring of a feature spec. Invoke with `/symr-plan <brain-dump>`, or when Finn is clearly brain-dumping a new feature/idea to be turned into a `docs/specs/<feature>.md` spec.
---

# Symr Plan phase

Finn is brain-dumping a feature; you turn it into a committed, fully-decided spec at `docs/specs/<feature>.md`. That spec is the sole implementation prompt later — complete enough that an implement session can start from just "go look at `docs/specs/<feature>.md`". You build it *with* Finn; you do not decide it for him.

The `/symr-plan` argument is the brain-dump / feature idea. Treat it as the opening of the planning conversation, not a full spec.

## First action: check the model
Plan runs on **Opus** (`claude-opus-5`). Check this *before anything else* — before the branch, before reading a single file. Read it straight off the environment block in your system prompt ("You are powered by the model named…"). **No command — don't shell out for this.**

If it isn't Opus, **stop right there**: say which model you're on and that Plan wants Opus, and do nothing else — no branch check, no orientation, no scanning the codebase. Wait for Finn to switch models or tell you to carry on.

That block is written at session start, so it's a *fresh-session* check. If Finn has switched models mid-session it can be stale — so if he says he's already on Opus and the block disagrees, take his word and carry on. Never stop him twice for the same check.

## Second action: settle the branch
Before reading any code, confirm where this work lives — a fresh session inherits whatever branch was last checked out, likely the wrong one.
1. Check `git branch --show-current`.
2. Propose a fresh `feat/<slug>` cut from up-to-date `main` (derive `<slug>` from the feature; confirm it), or a switch to the existing branch this belongs on. **When the work is a roadmap step, the slug ends with that step's letter** — `feat/entity-pages-K`, `feat/foreign-roundtrip-D`, `feat/grouping-catch-up-E`. Non-roadmap work carries no suffix.
3. Wait for Finn's OK, then dive in.

The spec is the **first commit on that feature branch**; implementation commits stack on top later.

## How to ask
- **Question-driven, no assumptions.** Never decide anything Finn hasn't stated — scope, UX, data model, scopes, naming, all of it. If something's undefined, ask.
- **Batch foreseeable questions up front** as one numbered list — everything you can already see needing a decision from the brain-dump. Reserve trickled, one-off questions for things that genuinely only surface later in the conversation. It's fine to think you're done and surface more — say "last batch…" as many times as needed; Finn won't mind.
- Number questions (sub-letters when nesting: 1, 2a, 2b). If a message mixes discussion points with questions, letter the points (A, B, C) and number the questions (1, 2, 3).
- Prefer asking Finn over reading large, token-expensive docs. Check `docs/spotify_constraints.md` before proposing anything that reads/writes the library — flag any hard limit rather than designing around it.

## Writing the spec
- Only start writing once there is **nothing left to decide**.
- **Say up front whether this came from the roadmap.** Every spec's header states its provenance in one line: either *"Step X of `docs/Planning/roadmap.md`."* (read that step's section first — it carries measured facts and already-resolved decisions, and note in the spec anywhere planning contradicted it) or *"Not a roadmap step — standalone."* Verify's finish-up reads that line to decide whether to check a step off, so it can't be left implicit. If a brain-dump obviously *is* a roadmap step but Finn didn't say so, ask.
- **No "Open questions" section.** The spec ships fully decided. Anything you'd park there is a question to ask now, in chat.
- Per-feature extra files (sub-specs, notes) go in `docs/<feature>/`, referenced from the spec.
- Commit the spec (in Finn's name, when he asks) as the branch's **first** commit. Don't push.
- **Planning routinely produces edits outside the spec** — a new roadmap step, a correction to an existing doc, a skill or `CLAUDE.md` change. Those never join the spec commit: they land as a **second commit at the end of the plan phase**, so the first commit on a feature branch is always exactly the spec and nothing else.
