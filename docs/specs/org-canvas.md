# Org Canvas — Feature Spec

Status: **ready to implement (Phase 1)**. This spec is the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase workflow in `CLAUDE.md`: ask implementation questions live/one-at-a-time, don't decide undecided things yourself. Open implementation questions are listed at the bottom.

> **Branch:** this work lives on `claude/spotify-library-manager-a4b129`. The implementation chat must **switch itself to that branch** (don't start a new worktree/branch or work off `main`) — check with `git branch --show-current` first and `git checkout claude/spotify-library-manager-a4b129` if you're not already on it, so all Phase 1 commits land alongside this spec.

## Read first
- `CLAUDE.md` — conventions, workflow, tech stack, KISS + security rules.
- `docs/spotify_constraints.md` — hard API limits (esp. folders not readable).
- `docs/Planning/feature_ideas.md` — where this sits in the backlog.
- Project memory `spotify-workflow` — Finn's library context (why he needs to categorize hundreds of playlists).

## What this is
Symr's first feature: a **read-only** drag-and-drop canvas for organizing Finn's Spotify playlists. He has hundreds of playlists across messy folders and needs to work out a category taxonomy for a one-time library reorg. The canvas lets him pull every playlist in as a card, drag cards into clusters, add freeform text labels, and export the arrangement as text (with coordinates) to paste into claude.ai for bouncing organization ideas. The export feeds the eventual `docs/library_spec.md` and the folder-reorg work.

Read-only: it reads playlists from Spotify; **nothing writes to Spotify anywhere in this feature.**

## Scope

### Phase 1 (build now)
Read-only snapshot + canvas + tray + labels + persistence + simple nearest-label text export to clipboard.

### Phase 1.5 (immediately after Phase 1 export works)
Chained/proximity grouping in the export (see Export → Chained grouping). Only affects export output.

### Later tiers (design the seams, don't build)
- Download `.txt`/`.md` export (fast-follow once exports routinely run to hundreds).
- Snapshot **refresh reconciliation** (new playlists → tray, removed → flagged/greyed).
- Generalized card types: **albums, songs, artists**; manual lookup/add by search; customizable card fields.
- **Multiple boards** surfaced in the UI.

## Data model
Design so the card model does **not** hard-assume "playlist" (albums/songs/artists slot in later). Phase 1 only creates playlist cards.

- **board** — supports multiple boards at the schema/backend level, but Phase 1 UI uses a single default board. (id, name, created/updated.)
- **card** — belongs to a board. Fields: id, board_id, a generic entity type (e.g. `playlist` for now) + entity reference/id, display name, image URL, placement state (`tray` | `placed`), and world-space `x`/`y` (midpoint) when placed. Keep type-specific data separable so new entity types are additive.
- **label** — belongs to a board. Fields: id, board_id, text, world-space `x`/`y` (midpoint).
- **snapshot** — the pulled playlist catalog (playlist id, name, image URL, owner, etc.) that cards are created from. Store more playlist fields than Phase 1 renders where it's cheap (useful later).

Persistence: **SQLite** via the stdlib `sqlite3` + thin helper (per CLAUDE.md). Board state (card positions, placement, labels) persists across reloads and re-snapshots.

## Spotify integration (read-only snapshot)
- Use **Spotipy**. Pull all of Finn's playlists into the snapshot table (at minimum: id, name, cover image URL; grab owner + counts too where cheap).
- Scopes needed: `playlist-read-private`, `playlist-read-collaborative`. (No write scopes, no library/liked scopes for this feature.)
- **Folder placement is NOT available via the API** — do not attempt to read it.
- Cover images: store the Spotify-hosted **image URL** and render via `<img>` (KISS). Downloading/caching covers locally is a later option, not Phase 1.
- Snapshot is triggered by an explicit action (e.g. a "pull/refresh library" button). Phase 1 reconciliation can be minimal (see Open questions).
- Credentials: Finn will create/confirm the Spotify developer app **just before implementation** and holds the client secret himself — flag this at implementation start; don't hardcode or handle secrets. Spotipy's token cache lives on disk locally.

## Canvas UI & interactions
- **Rendering:** DOM cards — absolutely-positioned elements inside a single pan/zoom-**transformed container** (CSS transform). Sized for a few hundred cards.
- **Cards (Phase 1 content):** playlist **name + cover picture** only. No track count/owner shown yet.
- **Side tray:** all snapshot cards start **unplaced in a side tray**; Finn drags them out onto the canvas one-by-one as he sorts. A card is either in the tray (unplaced) or on the canvas (placed with x/y). (This is also how re-pulled new playlists will surface later.)
- **Drag:** drag a card to move it. **Multi-select** via (a) marquee box (drag on empty canvas) and (b) shift/⌘-click to add/remove from selection; dragging any selected card moves the **whole selection** together.
- **Pan:** two-finger trackpad scroll pans; **hold space** (or middle-mouse) + drag pans as a mouse fallback. Dragging on empty canvas = marquee select (not pan).
- **Zoom (viewport):** trackpad **pinch** (arrives as `wheel` + `ctrlKey`) zooms; also a **zoom slider** control. Scales the whole viewport (cards + labels together, world-space) — faithful layout, no new overlaps.
- **Intrinsic scale slider:** a separate slider that scales the **intrinsic size of cards *and* labels uniformly** in world units — the lever for packing many cards without physical overlap. (Labels scale with this slider exactly like cards.)

## Labels
- **Double-click empty canvas** to create a label. Drag to move. Click to edit text inline. **Delete** key removes it.
- Labels are standalone canvas objects (not attached to a card). They scale with the intrinsic scale slider like cards.

## Export
A **"Copy as text"** button copies plain markdown-ish text to the clipboard.

### Phase 1 — simple nearest-label grouping
- Each **placed** card is assigned to the label whose **midpoint is nearest** (Euclidean, midpoint-to-midpoint).
- **Max-distance cutoff** (configurable, tunable): if a card's nearest label is farther than the cutoff, it goes to **Ungrouped**.
- If there are **zero labels**, all placed cards go to **Ungrouped**.
- Within a group, order cards **top-to-bottom by y**, ties broken **left-to-right by x**.
- Order groups top-to-bottom by label position (then left-to-right); **Ungrouped** near the end; **Unplaced** (tray) last.
- Include world-space midpoint coordinates on every label and card, so Claude can recover both the grouping and any spatial encoding Finn used (e.g. "within a label I placed them left-to-right by upbeatness").

Format example:
```
## Upbeat  (label @ 320,140)
- Summer Bangers  (card @ 180,260)
- Roadtrip 2024  (card @ 410,300)

## Chill  (label @ 900,150)
- Late Night  (card @ 880,320)

## Ungrouped
- Weird One  (card @ 1500,1500)

## Unplaced (in tray)
- Some Playlist
- Another Playlist
```

### Phase 1.5 — chained/proximity grouping (replaces the nearest-label step)
- Each card links to its **nearest node among {all labels + all other cards}**. If that node is a **label**, the card belongs to it. If it's another **card**, the card inherits that card's group, followed **transitively up the chain** until a label is reached.
- Purpose: a downward stack of cards under a label all attribute to that label (each card's nearest neighbor is the card above it), even when another label is physically nearby.
- The **max-distance cutoff applies to each link** — a gap larger than the cutoff breaks the chain, separating stacked groups (this is how the "small space" between stacks works).
- Chains that never reach a label → **Ungrouped**.
- This will need tuning; it only affects export output, so it's deliberately after Phase 1.

## Non-goals (Phase 1)
- No writes to Spotify. No folder reading. No album/song/artist cards, manual lookup, or custom card fields. No download-file export. No multi-board UI. No refresh reconciliation beyond the minimum. No charts. No auth/login (local/Tailnet single-user for now — but follow CLAUDE.md's "security done fully" rule for anything security-touching).

## Tech
Per `CLAUDE.md`: Python 3.14.5 (fallback 3.12), Flask, Spotipy, SQLite via stdlib `sqlite3` + thin helper, server-rendered Jinja + vanilla JS (the canvas is the vanilla-JS-heavy part), venv. KISS — a plain working canvas beats a polished half-built one.

## Open questions for the implementation chat
- Exact Spotify OAuth flow wiring (redirect URI, where the token cache lives, first-run auth UX).
- SQLite file location and schema specifics; how the thin helper is shaped.
- Board state save cadence: save-on-every-move vs debounced vs explicit save.
- "Nearest label" ties and the default cutoff value (will be tuned by feel).
- Snapshot re-pull behavior in Phase 1: how a refresh reconciles with existing placed cards (minimum viable: new → tray, keep existing placements by playlist id; removed → decide) — how minimal for v1?
- App structure/routes (canvas page + JSON endpoints for snapshot pull, board load, position/label updates).
- Card size, tray layout, and any perf considerations at the high end of "a few hundred" cards.
- Zoom/scale slider ranges and defaults.
