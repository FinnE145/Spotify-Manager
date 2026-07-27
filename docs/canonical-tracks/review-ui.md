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

All three render the identical UI; only the item list differs.

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

Each chip shows a short local tag (`S1`, `V2`, `R1`, `L3`) and a colour, so two rows in the same group are unmistakable. Singletons render neutral grey; only real groups get colour, so the eye catches groupings rather than noise. Where a chip corresponds to a group that already exists in the DB, its `canonical_group.id` appears muted next to the tag (and in the chip's `title` tooltip) — pre-commit groups have no id yet and show nothing.

## The label model

The client never invents group ids. It holds, per track in the item, a **label** at each tier — an arbitrary local string. Two tracks with the same label at a tier are in the same group at that tier. On load, labels come from the item's pre-fills (or, for reviewed/ad-hoc items, its saved grouping). On commit the whole label map is POSTed and the engine reconciles (see `grouping-engine.md`).

This keeps every grouping rule server-side; the client's only job is assigning labels.

## Keys

Pressing a **tier key** `1`–`4` acts on the current selection:

**Step 0 — expand.** The selection first grows to its *finer closure* within the item: any track sharing a finer-tier label with a selected track joins the selection. (Detaching one half of a release pair at recording level would otherwise break nesting.)

**Then, with `L` = the set of tier-*N* labels in the expanded selection `S`:**

| Case | Condition | Result |
|---|---|---|
| **Ungroup** | `\|L\| == 1` and every track in the item with that label is in `S` | `S` splits into its constituent finer groups, each taking a fresh label (at the release tier, into singletons) |
| **Detach** | `\|L\| == 1` but tracks outside `S` share the label | `S` takes one fresh label; the rest keep the old one |
| **Merge** | `\|L\| > 1` | `S` takes one fresh label |

After a **merge**, coarser tiers unify automatically — everything now sharing the tier-*N* label is given one label at each coarser tier, because nesting demands it. Ungroup and detach leave coarser tiers alone.

That yields exactly the behaviour discussed: select A B C D, press `3` → one recording group. Then select C D, press `3` → C and D detach into their own recording group (this is also how you split a group in two). Select just C, press `3` → C alone. Select the whole group, press `3` → it ungroups.

**Full key map:**

| Key | Action |
|---|---|
| `↑` / `↓`, `j` / `k` | move focus between rows |
| `Space`, click | toggle selection of the focused / clicked row |
| `Shift`+click | select a range |
| `a` | select all rows in the item |
| `1` `2` `3` `4` | apply Song / Version / Recording / Release to the selection |
| `r` | pin the focused track as representative for its groups (all four tiers) |
| `Esc` | clear every grouping on this item — all four tiers back to singletons |
| `Cmd`+`Z` | undo the last action on this item (in-session only) |
| `Enter` | **commit** and advance to the next item |
| `Backspace` | discard uncommitted edits and go back to the previous item |

### Legend

The four tier keys are **always-visible clickable buttons** (`1 Song`, `2 Version`, `3 Recording`, `4 Release`) so a decision can be made with the mouse and a misclick is hard. The rest of the key map sits behind a `?` hover/dropdown next to them.

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
