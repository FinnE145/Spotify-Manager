# Org Canvas — Feature Spec

Status: ~~ready to implement (Phase 1)~~ — **shipped and long since merged**; the status line and
the branch note below are stale process metadata from before this repo used lettered roadmap
steps. This was Symr's **first** feature, and it was never revisited after landing — see the
corrections section immediately below for everything that has drifted since.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).

> **Branch:** this work lives on `claude/spotify-library-manager-a4b129`. The implementation chat must **switch itself to that branch** (don't start a new worktree/branch or work off `main`) — check with `git branch --show-current` first and `git checkout claude/spotify-library-manager-a4b129` if you're not already on it, so all Phase 1 commits land alongside this spec.

---

## Corrections to current behavior (P1-012)

**This spec is significantly out of date — read everything below the divider as historical
design intent, not as a description of what runs today.** 17 differences found during P1, all
here in one place (rather than scattered inline) specifically so a P2 test-writer has one
concrete reference for what's actually true, rather than piecing it together from stale prose.
Corrections not listed below (i.e. anything not mentioned here) can be assumed still accurate.

**Security-relevant (fix regardless of anything else, same shape as `snapshot.md`'s already-fixed
"no write scopes" line):**
- **Scopes.** §Spotify integration claims "No write scopes, no library/liked scopes for this
  feature." False — `config.py` requests **`user-library-read`** (Liked Songs, added by step
  `track-metadata-A`) and **`playlist-modify-private`** (the round-trip's scratch playlist, added
  by step D) alongside the two read scopes this spec names. Both are real, intentional, and used
  by later features; this spec was just never told.
- **Auth.** §Non-goals claims "No auth/login (local/Tailnet single-user for now)." False since
  `site-shell.md` — the app-wide login guard (`app.py`'s `require_login` before-request hook)
  gates `/canvas` exactly like every other page.

**A real algorithmic divergence, ratified as the intended design (code kept, spec amended):**
- §Export "Phase 1.5" says "Chains that never reach a label → Ungrouped," read as: stop at the
  first dead-end. **The actual rule in `grouping.py`'s `group_cards()` is a full nearest-first
  search, not a single hop.** Each card's candidate neighbors (`_sorted_candidates`, line 14-18)
  are sorted nearest-first, labels breaking ties before cards; `resolve()` (line 48-65) walks that
  sorted list and only commits to Ungrouped once **every** candidate within the cutoff has been
  tried and none reaches a label — including candidates reached transitively through other cards,
  with a `visiting` set to skip anything that would cycle back rather than aborting. So a card
  whose *nearest* neighbor dead-ends can still attach via a longer alternate path through a
  different, farther-but-still-in-cutoff neighbor. This changes real export output from what the
  literal spec text describes, and is being kept as the better design.

**Scope creep on the Pull button, doc-only fix (already correctly owned elsewhere):**
- §Spotify integration describes a lightweight, metadata-only playlist pull. The button now
  triggers `/api/snapshot/pull` → `snapshot.start_full_pull()`, the entire snapshot engine (every
  playlist's full track contents, Liked Songs, artist/album records). Real behavior is documented
  correctly in `snapshot.md` / `track-metadata-A.md` / `partial-pulls-J.md`; this spec's own
  description of its own Pull button was just never updated to match.

**Export mechanics, current and exact:**
- §Export "Phase 1" (the simple nearest-label algorithm) **no longer exists in any form** — only
  Phase 1.5's chained version above ships. Reading only the Phase 1 section and testing against it
  would test dead code.
- **Tie-break and cycle handling, both implemented** (`grouping.py:14-18,48-65`), where the spec's
  own "Open implementation questions" still lists them as unresolved: ties broken by
  nearest-distance, then label-before-card, then lower id; a cycle (e.g. two cards mutually
  nearest to each other) falls back to the next-nearest unvisited candidate rather than giving up.
- **`## Ungrouped` always renders**, even with zero cards under it (`render_export_text`,
  unconditional). **Tray cards sort alphabetically by `display_name`.** Neither ordering rule is
  specced.

**Real, undocumented UI subsystems that shipped:**
- **`card.note`** (`db.py`'s `card` table) — a free-text field, editable via the card UI and
  `PATCH /api/card/<id>`, entirely undocumented here, and **not included in the export text**.
- **Download button** — `static/js/canvas.js`'s `downloadBtn` saves the export as `symr-export.md`
  — shipped despite §"Later tiers" explicitly listing file-download export as a later, deferred
  tier.
- **Proximity cutoff UI**: a number input (`#cutoff-input`, default `300`) plus a "show grouping
  radius" checkbox/overlay (`#radius-checkbox`, `.radius-circle`) visualizing the cutoff per card
  — neither specced.
- **Grid-snap subsystem**: cards/labels snap to a `GRID = 17.5` (world units) lattice that scales
  with the intrinsic-size slider (`canvas.js`'s `gridUnit()`) — a whole subsystem with no
  counterpart in this spec.
- **Delete/Backspace and drop-on-tray both *unplace*, not delete, a card**; both keys remove a
  label outright. Wider and more specific than "Delete key removes it."
- **Multi-select**: `ctrl` works alongside `shift`/`⌘` (not specced as an option), and marquee
  selection is **midpoint-containment** (`card.x`/`card.y`, the stored midpoint, tested against
  the marquee's bounds) — not bounding-box intersection.

**Every "Open implementation question" at the bottom was resolved during implementation and never
recorded back here** — concrete answers, for a test-writer who'd otherwise have nothing to go on:
- **Save cadence**: on drag/move **completion** (`persistPosition()`, called from each
  mouseup/drop handler) — one POST per completed move, not continuous and not debounced.
- **Routes**: `GET /canvas` (page); `GET /api/board` (full state); `POST /api/card/<id>` (position
  + placement); `PATCH /api/card/<id>` (note/x/y/placement, partial); `POST /api/label`;
  `PATCH /api/label/<id>`; `DELETE /api/label/<id>`; `GET /api/export?cutoff=`.
- **DB location**: `symr.db`, same connection/schema helper (`db.py`) as everything else — no
  separate canvas database.
- **Zoom slider range**: `0.25`–`2` (`canvas.html`'s `#zoom-slider`). **Intrinsic-scale slider
  range**: `0.4`–`1.5` (`#scale-slider`).
- **Cutoff default**: `300` (`#cutoff-input`'s `value`).
- **Nearest-label tie rule**: see "tie-break and cycle handling" above — same mechanism serves
  both Phase 1's (dead) nearest-label rule and Phase 1.5's chain resolution.

**Still genuinely open, not resolved by implementation:**
- **Unfollowed playlists** (`snapshot.unfollowed_at`) are never removed or flagged on their canvas
  card — a card for a playlist Finn no longer follows renders indistinguishable from a live one.
  This is exactly the spec's own "removed → decide" open question (§Open questions), still
  undecided today, not silently resolved like the others above.

**Schema, undocumented:**
- **`card` carries `UNIQUE(board_id, entity_type, entity_id)`** (`db.py`), not mentioned in
  §Data model — the snapshot-pull upsert path relies on it to avoid duplicate cards on a re-pull.

---

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
