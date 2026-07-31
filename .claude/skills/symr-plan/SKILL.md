---
name: symr-plan
description: Symr Plan phase — question-driven authoring of a feature spec. Invoke with `/symr-plan <brain-dump>`, or when Finn is clearly brain-dumping a new feature/idea to be turned into a `docs/specs/<feature>.md` spec.
---

# Symr Plan phase

Finn is brain-dumping a feature; you turn it into a committed, fully-decided spec at `docs/specs/<feature>.md`. That spec is the sole implementation prompt later — complete enough that an implement session can start from just "go look at `docs/specs/<feature>.md`". You build it *with* Finn; you do not decide it for him.

The `/symr-plan` argument is the brain-dump / feature idea. Treat it as the opening of the planning conversation, not a full spec.

## First action: settle the branch
Before reading any code, confirm where this work lives — a fresh session inherits whatever branch was last checked out, likely the wrong one.
1. Check `git branch --show-current`.
2. Propose a fresh `feat/<slug>` cut from up-to-date `main` (derive `<slug>` from the feature; confirm it), or a switch to the existing branch this belongs on.
3. Wait for Finn's OK, then dive in.

The spec is the **first commit on that feature branch**; implementation commits stack on top later.

## How to ask
- **Question-driven, no assumptions.** Never decide anything Finn hasn't stated — scope, UX, data model, scopes, naming, all of it. If something's undefined, ask.
- **Batch foreseeable questions up front** as one numbered list — everything you can already see needing a decision from the brain-dump. Reserve trickled, one-off questions for things that genuinely only surface later in the conversation. It's fine to think you're done and surface more — say "last batch…" as many times as needed; Finn won't mind.
- Number questions (sub-letters when nesting: 1, 2a, 2b). If a message mixes discussion points with questions, letter the points (A, B, C) and number the questions (1, 2, 3).
- Prefer asking Finn over reading large, token-expensive docs. Check `docs/spotify_constraints.md` before proposing anything that reads/writes the library — flag any hard limit rather than designing around it.

## Writing the spec
- Only start writing once there is **nothing left to decide**.
- **No "Open questions" section.** The spec ships fully decided. Anything you'd park there is a question to ask now, in chat.
- Per-feature extra files (sub-specs, notes) go in `docs/<feature>/`, referenced from the spec.
- Commit the spec (in Finn's name, when he asks) as the branch's first commit. Don't push.
