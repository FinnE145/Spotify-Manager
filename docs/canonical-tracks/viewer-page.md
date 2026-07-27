# Phase 6 — Viewer page

Sub-spec of `docs/specs/canonical-tracks.md`. Replaces the throwaway listing page from phase 4.

`/dev/canonical` is where you look at what you've built, find mistakes, and fix them. It never edits inline — every fix opens the phase 5 queue UI, so there's exactly one grouping interface in the app.

## Stats block

At the top, computed from `track_group` / `canonical_group`:

- **Tracks** — total rows in `track`.
- Per tier (Song, Version, Recording, Release): **distinct groups**, and **how many are non-singleton**. `3,589 tracks → 3,401 songs (112 grouped)` reads as "112 song groups hold more than one track."
- **Unreviewed** — candidate groups still in the main queue, and in the cross-artist queue, each linking straight into that queue.
- **Reviewed** — pairs decided, with the most recent `decided_at`.

This block is the "am I done?" answer and the standing check that the engine's invariants haven't drifted.

## Group browser

The main list: **non-singleton song groups**, ordered by playlist impact (same measure as the queue — total live memberships across the group's tracks), descending.

Each entry shows the song group's representative cover and title/artists, its track count, and its group id. Expanding it reveals the nesting:

```
Song 4182 — "All Too Well"  (7 tracks)
├─ Version 4183                                    ← the base version
│  ├─ Recording 4184
│  │  ├─ Release 4185 — Red (album)
│  │  └─ Release 4190 — Red (single)
│  └─ Recording 4191 — "…(Taylor's Version)"
│     └─ Release 4192
└─ Version 4200 — "…(Live)"
   └─ Recording 4201 → Release 4202
```

Leaves are tracks, each with its cover thumbnail, full title, album, duration, ISRC, live-membership count, and a link to `/dev/snapshot/track/<id>`. The representative at each level is marked (★), pinned ones distinguished from computed ones.

Every level has an **"Edit in queue"** action that opens `/dev/canonical/review?tracks=…` with that subtree's tracks as a single ad-hoc item — that's how unmerging works, using the same detach/ungroup semantics as everywhere else.

Default view is non-singleton groups only; a toggle includes singletons for completeness. Plus a filter box matching title or artist.

## Search → multiselect → queue

For pairs detection will never propose — a cover, a re-titled reissue, two songs that share nothing textually:

1. A search box over all tracks (title or artist substring, case-insensitive), showing cover, title, artists, album, ISRC, live count, and current group ids.
2. Checkboxes to select any number of results across searches (the selection persists as you re-search).
3. A **"Group selected"** button opening `/dev/canonical/review?tracks=<ids>` — one ad-hoc item, no pre-fills, current saved grouping rendered, full keyboard UI.

## Cross-artist list

A separate section: same normalized base title, **no shared artist** — the covers and Christmas songs worth a human look. Each entry lists its tracks with artist prominent, and there's a **"Review in queue"** button opening `/dev/canonical/review?queue=cross-artist`.

Kept apart from the main list because most of these are *not* the same song, and mixing them into the main queue would poison it.

## Track page addition

`/dev/snapshot/track/<track_id>` gains a **Canonical** line: its four group ids, each linking to that group on `/dev/canonical`, and its siblings at each tier (the other tracks sharing that id) with links to their own track pages. A track in no group shows all four as singletons.

## Endpoints

Server-rendered from the DB, following the existing snapshot pages — no parallel JSON layer.

```
GET /dev/canonical            the page (params: ?q= filter, ?singletons=1)
GET /dev/canonical/group/<id> optional deep link that expands and scrolls to one group
```

Search results render server-side from `?q=`; multiselect is client-side (checkbox state in `sessionStorage`, so it survives a re-search) and only becomes a request when "Group selected" navigates to the queue.

## Notes

- Keep it minimal and consistent with the existing snapshot pages. The nesting tree is the one place worth a little care — indentation and per-tier colours matching the review UI's chips make the four levels readable at a glance.
- No pagination. 3,589 tracks and a few hundred groups render fine, same call as the playlist detail page.
