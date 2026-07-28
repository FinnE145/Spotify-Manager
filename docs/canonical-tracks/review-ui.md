# Phase 5 — Review queue UI

Sub-spec of `docs/specs/canonical-tracks.md`. Read `grouping-engine.md` and `detection.md` first — this page is the driver for both.

The click-through: one candidate group on screen at a time, keyboard-first, decided in a second or two each.

## Route and queue sources

`GET /dev/canonical/review`, with three sources:

| URL | Queue |
|---|---|
| `/dev/canonical/review` | main queue — unreviewed candidate groups, playlist-impact order |
| `/dev/canonical/review?queue=cross-artist` | same-title/no-shared-artist candidates (covers, Christmas songs) |
| `/dev/canonical/review?tracks=<id>,<id>,…` | a single ad-hoc item from an arbitrary selection (phase 6's search sends you here) |

All three render the identical UI; only the item list differs — including the cross-artist queue, which uses the same prefill as everywhere else. The song-tier prefill rule requires artist overlap (see `detection.md`), so cross-artist items no longer arrive pre-merged into one song by default; they only start merged where there's already a real existing match or a genuine artist overlap within the bucket.

## Layout

**Header** — a progress bar with `12 / 337 decided`, the queue name, and the tier legend (below).

**Item panel** — the candidate group's base title and artists as a heading, then one row per track:

- selection state (checkbox + full-row highlight; the focused row is separately outlined)
- album cover thumbnail (~48px, from `album_image_url`)
- full **unnormalized** title, artists, album name, duration
- **ISRC**, with identical ISRCs inside the item sharing a colour swatch — same-ISRC-ness is readable at a glance, and "—" when NULL
- **live playlist count** — usually the thing that tells you which one is the real one
- the track's suffix classification, muted (`base`, `version: acoustic`, `unknown`, …)
- four **tier chips**: Song · Version · Recording · Release

**Footer** — the clickable legend.

### Tier chips

Each chip shows a short local tag (`S1`, `V2`, `R1`, `L3`) and a colour. Every distinct group at a tier gets a colour — including singletons — assigned in a fixed high-contrast rotation by order of first appearance within the item (1st group blue, 2nd red, 3rd green, …), cycling if there are more groups than colours. This reads as "here are the groups," not "here's what's selected" — colour identifies which rows belong together, full stop. Chips show only the local tag, never the underlying `canonical_group.id` — the tag is all a reviewer needs, and showing a real id for one chip but not another made "already merged for real" and "proposed here" easy to conflate.

## The label model

The client never invents group ids. It holds, per track in the item, a **label** at each tier — an arbitrary local string. Two tracks with the same label at a tier are in the same group at that tier. On load, labels come from the item's pre-fills (or, for reviewed/ad-hoc items, its saved grouping). On commit the whole label map is POSTed and the engine reconciles (see `grouping-engine.md`).

This keeps every grouping rule server-side; the client's only job is assigning labels.

## Keys

Pressing a **level key** `0`–`4` sets the exact relationship for the current selection `S` in one shot — the highest tier its members are the same at:

| Key | Sets `S` to... |
|---|---|
| `0` | not even the same song — fully separate at all four tiers |
| `1` | same song |
| `2` | same version |
| `3` | same recording |
| `4` | same release |

Tiers at or coarser than the chosen level get **one shared fresh label** across all of `S`. Every tier finer than that gets a **fresh, individually-unique label per track** in `S` — no two members of `S` share anything finer than the chosen level, even if they did before. This redefines `S`'s relationship outright rather than incrementally merging/splitting; whatever `S` was grouped with previously (inside or outside the selection) no longer matters once you press a level key.

This only ever touches the tracks in `S` — it never reaches beyond the selection into other rows in the item, even ones that currently share a tier with a selected track. (An earlier closure-expansion step that pulled in finer-tier neighbors caused surprise pairings when only one track was selected, so it was removed — the server's own reconciliation in `apply_partition` already handles any DB-side nesting consequences beyond this item when the item commits.)

That means: select A B C D, press `3` (same recording) → all four share one recording (and song/version, since nesting requires it), each keeping its own unique release. Select just C D, press `4` (same release) → C and D become one release, splitting off from whatever they were release-grouped with before. Select just C alone, press `0` → C is detached from everything, at every tier, regardless of what it was grouped with.

**Full key map:**

| Key | Action |
|---|---|
| `↑` / `↓`, `j` / `k` | move focus between rows |
| `Space`, click | toggle selection of the focused / clicked row |
| `Shift`+click | select a range |
| `a` | select all rows in the item |
| `1` `2` `3` `4` | set the selection's relationship: same song / same version / same recording / same release |
| `0` | not even the same song |
| `r` | pin the focused track as representative for its groups (all four tiers) |
| `Esc` / **Clear** button | clear every grouping on this item — all four tiers back to singletons |
| `Cmd`+`Z` | undo the last action on this item (in-session only) |
| `Enter` / **Save →** button | **commit** and advance to the next item |
| `Backspace` / **← Back** button | discard uncommitted edits and go back to the previous item |

### Legend

The five level keys are **always-visible clickable buttons** (`1 Song`, `2 Version`, `3 Recording`, `4 Release`, `0 None`) so a decision can be made with the mouse and a misclick is hard. **Clear**, **← Back**, and **Save →** buttons on the right of the same row mirror `Esc`, `Backspace`, and `Enter`. The rest of the key map sits behind a `?` hover/dropdown next to them.

### Commit semantics

Only `Enter` writes. It POSTs the label map, which calls `apply_partition` **and** `mark_reviewed` over the item's tracks, then advances. Reviewing *is* deciding — an item you Enter through unchanged records "these are genuinely separate," which is what keeps it out of the queue next time.

`Backspace` **discards** uncommitted edits on the current item and loads the previous item's saved state for re-editing. Nothing is written, and nothing is marked reviewed, by going backwards.

`Cmd`+`Z` pops a client-side snapshot stack (pushed on every mutating action, `Esc` included). It's in-session only — once an item is committed, fix it by navigating back to it or re-opening it from `/dev/canonical`.

If the engine reports `dragged_in` tracks (closure pulled in something outside the item), show a small note under the item naming them — normally this never fires.

## Queue lifecycle

The queue is **frozen on page load**: the server computes the ordered candidate list once and the page holds it, so the total doesn't shift under you while you work.

When you `Enter` on the **last** item, the page re-requests the queue and appends any candidates that are still unreviewed but weren't in the original list — new tracks from a pull, or groups your own decisions have altered. If there are none, show a done state (a count of what you got through, and a link to `/dev/canonical`).

Progress is `committed items / total in the frozen list`, with the total growing if the end-of-queue recompute appends more.

## Endpoints

```
GET  /dev/canonical/review        page (params: queue, tracks)
GET  /api/canonical/queue         ?queue=main|cross-artist  or  ?tracks=<ids>
                                  → ordered list of items with all display data + pre-fills
POST /api/canonical/apply         {track_ids: [...], labels: {...}, pin_representative: id|null}
                                  → {tracks: {...}, dragged_in: [...]}
```

`GET /api/canonical/queue` returns the **whole** ordered queue in one response (a few hundred groups — small), so item-to-item movement is instant and needs no further requests. Both endpoints are login-gated like everything else, and `/api/*` errors return JSON via the existing centralized handlers.

## Empty and edge states

- No unreviewed candidates → a plain "nothing to review" panel linking to `/dev/canonical`.
- `?tracks=` with fewer than 2 valid ids → the generic 400 error page.
- An item whose tracks were deleted from the DB between load and commit → the apply POST 400s; reload the queue.

Styling stays in `static/css/style.css`, consistent with the existing pages. Function over form — but the tier chips genuinely need distinguishable colours, so pick four to six that stay legible next to each other.
