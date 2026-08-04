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
- `app.py` — Flask app factory. Page routes (`/` home, `/canvas`, `/dev` dev-tools landing, `/dev/snapshot*`, `/dev/canonical*`, `/dev/artists`, and stubs `/audit`, `/covers`, `/folders`, `/analytics`), OAuth (`/login`, `/callback`), `/api/*` endpoints, an app-wide `before_request` login guard (exempts `login`/`callback`/`static`), and centralized error handlers (`HTTPException` + `Exception`) that render `error.html` for pages and JSON for `/api/*`.
- `db.py` — SQLite `SCHEMA` + additive migrations (`_migrate`) + `VIEWS`. Library metadata is normalized: `track`, `album`, `artist`, plus `track_artist` / `album_artist` join tables carrying per-credit `position`; each of `track`/`album`/`artist` also keeps a verbatim `raw_json` of the Spotify object. `membership` is the append-only per-playlist log; `snapshot` is per-playlist state (including the `excluded` flag that skips item reads on repeat pulls). `artist_alias` / `reviewed_artist_pair` resolve Spotify's duplicate artist ids. `VIEWS` is dropped and recreated on every `init_db` (so a changed definition always takes effect) and holds the artist read path: `resolved_track_artist` / `resolved_album_artist` (alias-resolved), `track_artist_credit`, `track_artist_role` (primary vs featured), and `track_artists` (the rendered display string) — **`track.artists` is write-only, never read**.
- `artists.py` — artist identity: per-track alias-resolved id sets for detection (`artist_sets`), plus duplicate-candidate detection and the merge/unmerge curation writes. Owns `artist_alias` / `reviewed_artist_pair` only.
- `templates/` — Jinja templates. `base.html` (shared shell: navbar + content block; `body_class` block for the immersive full-viewport pages); `home.html`; `canvas.html` (org canvas, extends base); `dev.html` (dev-tools landing page); `snapshot.html`, `snapshot_playlist.html`, `snapshot_track.html`; `artists.html` (duplicate-artist curation); `coming_soon.html` (shared stub placeholder); `error.html` (generic HTTP error page, extends base).
- `static/css/style.css` — the single stylesheet (navbar, shared page styles, canvas).
- `scripts/` — standalone one-off scripts, each with its own DB connection, own argument parsing, commit-as-you-go: `backfill_track_details.py` (isrc/album-image mop-up, superseded), `migrate_track_metadata.py` (the step-A schema migration — already applied against `symr.db`; kept as the record of what happened, not meant to be re-run).
- `.claude/skills/` — the phase skills (`symr-plan`, `symr-implement`, `symr-verify`); committed.
- (Rest TBD — update this map as directories are created.)

## Keep It Simple
- KISS. The goal is code that is **done, understandable, and works** — not production-grade or clever. AI tends to overdo complexity; don't. Reach for the simplest thing that fully solves the problem.
- **Security is the one exception to KISS — never do the bare minimum here.** Everything security-related must be done *fully and properly*, not just "right": secure coding practice (never leak tokens/secrets) **and** the implementation of things like login, auth, and session handling. Do those thoroughly.
- The other hard requirement: never corrupt or wrongly modify my real Spotify library. Beyond security and that, favor simplicity over robustness — the rest just needs to be done right.

## Commands
- **Run:** `venv/bin/python app.py` — serves on port 45660 (`SYMR_PORT` to override, `SYMR_DEBUG=1` for the reloader). Registered in `.claude/launch.json` as `symr`, so Claude Code can start it via the preview tooling instead of a raw shell command.
- **Port 45660 is not negotiable** — the Spotify OAuth redirect URI is registered against it, so the app can't authenticate on any other port. If it's occupied (usually another chat's server) or the app is otherwise unreachable, **stop and ask me to free it**. Don't reassign the port, don't fall back to the test client or a headless workaround, and don't skip the verification.
- Test / lint: none yet. Record them here verbatim once they exist.

## The Workflow: Plan → Implement → Verify
Work moves through three phases, each in its own chat, each **question-driven**. Every phase's specific rules live in its own skill — invoke it at the start of the chat:
- **Plan** → `/symr-plan <brain-dump>` — question-driven spec authoring; output is a committed `docs/specs/<feature>.md`.
- **Implement** → `/symr-implement [spec]` — build from that spec, asking live.
- **Verify** → `/symr-verify` — review the diff against the spec, run the app, finish up.

If I'm clearly in one phase but didn't invoke its skill, infer and load that **one** skill (never load all three; if the phase is ambiguous, ask which one). The skills are self-contained — this file holds only what's true across all three.

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

## Git Workflow
Solo repo, single checkout at `/Users/finne/Projects/Spotify-Manager` (no worktrees). Branch prefixes: **`feat/`** features, **`chore/`** tooling/docs, **`fix/`** fixes. A feature branch carries both the spec (its first commit) and the implementation commits stacked on top.

These are **always-on tripwires** — warn *before* acting, then do whatever I decide. Never silently proceed past one; never refuse once I've answered.
1. **No committing to `main`.** Before any commit, check `git branch --show-current`; if it's `main` and this isn't a tiny main-level fix, stop and flag it — the work belongs on a branch.
2. **Confirm the branch before new work.** A fresh session inherits whatever branch was last checked out, which is likely wrong for new work. Confirming the branch is the *first* action of any new phase, before reading code: check `git branch --show-current`, propose a fresh branch off up-to-date `main` (or a switch to the right existing one), wait for my OK, then dive in.
3. **No premature merge/push.** Merging a branch into `main` and pushing happen only in the Verify finish-up — never during Plan or Implement.

Commits: commit only when I ask, in logical units — one for the spec, a few for implementation as needed, optionally one or more from verify. Commit in my name only — no Claude/AI co-author or attribution line. **Don't push without being asked.** The sanctioned merge+push is the Verify finish-up: a **`git merge --ff-only`** into `main` (keeps history linear, no merge-commit clutter) then push. This repo is solo and single-copy, so `--force-with-lease` is safe when a rewrite is the agreed fix.
