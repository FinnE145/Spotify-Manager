# Org Canvas — PLANNING HANDOFF (not yet a spec)

> **This file is a handoff prompt, not the finished spec.** When the org-canvas planning session runs, follow the Plan-phase workflow in `CLAUDE.md` (ask lots of questions, make no assumptions, stop-and-ask the moment you're unsure) and **overwrite this entire file** with the finalized, all-decisions-made spec so it can serve as the standalone implementation prompt. Until then, treat everything below as context to plan *from*, not as decided.

## Read these first
- `CLAUDE.md` — project conventions and the plan/implement/verify workflow.
- `docs/spotify_constraints.md` — hard API limits.
- `docs/Planning/feature_ideas.md` — full backlog; the "Org canvas" + "Library snapshot" entries are this feature.
- `docs/library_spec.md` — may not exist yet; the canvas is the tool Finn uses to produce the categorization that fills it in.

## What Symr is (one line)
Symr — a read-only, self-hosted, single-user Flask + SQLite web app for managing/verifying Finn's Spotify library. v1 is read-only; UX centers on surfacing things to review. (Full context in CLAUDE.md.)

## Why the org canvas is the first feature
Finn has hundreds of playlists across messy folders and needs to work out a category taxonomy for a one-time library reorg. Doing that on paper / in Visio doesn't scale to hundreds of items. The canvas lets him drag playlist cards into clusters, label them, and export the result so Claude can see and reason about his implicit organization. Its output feeds `docs/library_spec.md` and the future folder-reorg work.

## Phase 1 scope (what to actually build first)
- **Read-only library snapshot:** pull all of Finn's playlists into SQLite (at minimum: name + cover image; pull more fields where cheap). Folder placement is NOT available via the Web API — do not try to read it. Read-only; nothing writes to Spotify.
- **Canvas UI:** an open, pannable canvas of **playlist cards**. Phase-1 card = **name + picture + freeform text label(s)**. Drag cards freely to cluster them.
- **Freeform text labels:** place standalone text labels anywhere on the canvas (these act as cluster names / axes).
- **Export:** a "copy as text list" (paste into claude.ai) that outputs, for each card, its **midpoint (x,y) coordinates**, **grouped by the nearest text label** (include each label's own midpoint coordinates), listed **top-to-bottom** within a group. Rationale: this makes Finn's implicit grouping visible to Claude, and preserves coordinates so Claude can *derive* spatial encodings — e.g. if Finn says "within this label I placed them left-to-right by how upbeat the mood is," the x-coordinates carry that signal.

## Design for generalizability (build the seams in Phase 1; features later)
Finn categorizes/ranks music-related things often, so the canvas should generalize beyond playlists. Phase 1 ships playlist cards only, but the card/data model should not hard-assume "playlist." Later phases (NOT phase 1):
- Pull in **albums**, **songs** (e.g. all songs from a given album/playlist), and **artists** as cards.
- **Look up / manually add** playlists, albums, artists, songs by search.
- **Customize which fields** are shown on each card.
Keep the Phase-1 model open enough that these slot in without a rewrite.

## Known constraints / notes for the planner
- Read-only only. No Spotify writes anywhere in this feature.
- Spotify developer-app credentials: Finn will confirm/create the app **just before implementation** — flag it there, don't block planning on it. He (not Claude) holds the client secret.
- Cover images: readable via API for all playlists.
- Keep it simple (KISS per CLAUDE.md) — a plain, working canvas beats a polished half-built one. Security is the one place not to cut corners, but Phase 1 is local/Tailnet single-user with no auth yet.

## Open questions to resolve during planning (non-exhaustive — ask more)
- Canvas tech: plain HTML/JS/Canvas or SVG vs a library? (KISS; Finn cares about working > pretty.)
- How is card position persisted — DB, localStorage, exportable JSON board file? Does a board survive reload / re-snapshot?
- "Nearest label" grouping rule: pure Euclidean distance to label midpoint? Ties? Unlabeled cards?
- Snapshot refresh: how does re-pulling the library reconcile with an in-progress board (new/removed playlists)?
- Multiple boards, or one? Naming/saving boards?
- What exactly renders on a card in Phase 1, and card size vs. hundreds-of-cards performance.
