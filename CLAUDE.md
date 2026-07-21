## Overview
**Symr** (**S**potif**Y** **M**anage**R**, pronounced "simmer") — a web-based music library manager.

A Flask app for maintaining and verifying a Spotify library that follows a specific set of personal conventions (an append-only "Finn All" master playlist, semver-named "current favs" playlists, an ATG playlist, and a CI/CL intake playlist). The full library/usage spec lives in `docs/library_spec.md` (TBD).

## Tech Stack
- **Python 3.14.5** (fall back to 3.12 if something doesn't work), **Flask**.
- **Spotipy** for Spotify Web API access — handles OAuth, token refresh, and paging.
- **SQLite** via the stdlib `sqlite3` + a thin helper (no ORM unless the schema forces it): library snapshot, version history, folder-structure record, cover library, canvas boards, etc.
- **Frontend:** server-rendered **Jinja templates + vanilla JS**, no SPA framework. Richer interactive JS only where a feature needs it (e.g. the org canvas) — settle those specifics in that feature's spec.
- **Env:** `venv` + `requirements.txt`.
- v1 is read-only. Fill in remaining choices (charts, etc.) as features are specced. Never assume an unstated version or library.

## Codebase Map
- `docs/` — specs and reference. Feature specs at `docs/specs/<feature>.md`; per-feature extra files (sub-specs, notes, verification reports) in `docs/<feature>/`; hard Spotify API limits in `docs/spotify_constraints.md`.
- (Rest TBD — update this map as directories are created.)

## Keep It Simple
- KISS. The goal is code that is **done, understandable, and works** — not production-grade or clever. AI tends to overdo complexity; don't. Reach for the simplest thing that fully solves the problem.
- **Security is the one exception to KISS — never do the bare minimum here.** Everything security-related must be done *fully and properly*, not just "right": secure coding practice (never leak tokens/secrets) **and** the implementation of things like login, auth, and session handling. Do those thoroughly.
- The other hard requirement: never corrupt or wrongly modify my real Spotify library. Beyond security and that, favor simplicity over robustness — the rest just needs to be done right.

## Commands
- TBD (run / test / lint). Record the exact commands here as they're established, and use them verbatim.

## The Workflow: Plan → Implement → Verify
Work moves through three phases, each in its own chat, and **every phase is question-driven**. Roles are model-agnostic (today: planning & verification run on Opus, implementation on Sonnet; these may become custom agents later).

1. **Plan.** Conversational and question-driven. I brain-dump; you ask lots of questions and make no assumptions. The output is a committed feature spec at `docs/specs/<feature>.md` that serves as **both the all-decisions-made spec and the standard implementation prompt** — complete enough that I can begin an implementation session by simply saying "go look at `docs/specs/<feature>.md`" with no extra prompt. You build that spec *with* me; you do not decide it for me. If a feature needs extra files (sub-specs, notes, verification reports), they live in `docs/<feature>/`, referenced from the spec.
2. **Implement.** The implementer reads the spec and asks me implementation questions **live, one at a time**, rather than figuring anything out on its own. It is **not in a decision-making position** on code — when a real choice arises, it stops and asks. It works from the spec + my answers; I should not have to re-prompt from scratch.
3. **Verify.** A fresh planning-role chat reviews the diff against the spec, runs the app/tests, and reports. It may make fixes **only with my explicit confirmation**; otherwise it reports and hands back to a new implementation chat.

## No Assumptions & the Stop-and-Ask Rule
- Never assume behavior, versions, libraries, or intent I haven't stated. If something is undefined, ask.
- **Stop-and-ask (token reduction):** the moment you're unsure or catch yourself weighing alternatives, stop right there and ask — do not keep reasoning through the options first. Ask immediately, get my answer, then continue the train of thought.
- **One try, then ask.** If an approach doesn't work on the first attempt, stop and ask for direction. Do not try a second approach, and never layer hacky fixes (overrides, workarounds) to force something through. Surface the problem instead of digging deeper.
- Prefer asking me over reading large, token-expensive docs. Only open those if I point you to them or say broader context is needed.

## When Asking Questions
- Always **number** questions (use sub-letters when nesting, e.g. 1, 2a, 2b) so I can reply item-by-item. This applies to any list I'll respond to point-by-point; plain prose replies don't need identifiers.

## Frontend
- **Function over form.** This is a personal tool; a plain HTML page that does everything I want beats a pretty, half-finished one. Don't spend effort on visual polish unless I ask.
- A lightweight design system will be defined later — follow `docs/style_guide.md` once it exists. Until then, keep the UI minimal and consistent.

## Spotify API Constraints
- Before proposing anything that reads or writes the library, check `docs/spotify_constraints.md` for hard limits (e.g. playlist folders are not accessible via the Web API, cover-image upload rules, required scopes, rate limits). Don't design features the API can't support — flag the limit and ask.

## End of Implementation
When I say a session is done / "looks good" / "finish up":
- Commit logically; put separate features/fixes in their own commits (there may be leftover changes from prior sessions).
- Typically 1–2 commits per session, depending on scope.
- **Commit in my name only. Do NOT add any Claude/AI co-author or attribution line** to commit messages or PR bodies.
- Commit when I ask. **I always push — never push yourself.**
