# Phase 6 — Viewer page

Sub-spec of `docs/specs/canonical-tracks.md`. Replaces the throwaway listing page from phase 4.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`),
finding P1-018. Five differences found, all documentation catch-up: the ordering claim below is
stale (H retired impact-based ordering everywhere — same change `detection-artist-model.md` and
`grouping-catch-up-E.md` needed the same fix for), the no-pagination claim is stale (the listing
is capped at 50 unless a filter is set), the cross-artist entry point named below no longer
exists, a dead-route link, and the "unreviewed" rule is narrower than stated for cross-artist
buckets specifically. See inline notes below.

`/dev/canonical` is where you look at what you've built, find mistakes, and fix them. It never edits inline — every fix opens the phase 5 queue UI, so there's exactly one grouping interface in the app.

## Stats block

At the top, computed from `track_group` / `canonical_group`:

- **Tracks** — total rows in `track`.
- Per tier (Song, Version, Recording, Release): **distinct groups**, and **how many are non-singleton**. `3,589 tracks → 3,401 songs (112 grouped)` reads as "112 song groups hold more than one track."
- **Unreviewed** — candidate groups still in the main queue, and in the cross-artist queue, each linking straight into that queue.
- **Reviewed** — pairs decided, with the most recent `decided_at`.

This block is the "am I done?" answer and the standing check that the engine's invariants haven't drifted.

## Group browser

The main list: **non-singleton song groups**, ordered by ~~playlist impact (same measure as the queue — total live memberships across the group's tracks)~~ **score, descending** (`scoring.group_score`, per `scoring-H.md` §11.1 — `impact`-based ordering was retired site-wide by H; this line was never updated). **Also stale, noted 2026-08-17 (P1-018): the listing is capped at 50 rows unless a filter (`?q=`) is set**, not unpaginated as this section originally said — same cap-then-filter pattern as `/dev/canonical`'s other listings.

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

Leaves are tracks, each with its cover thumbnail, full title, album, duration, ISRC, live-membership count, and a link to `/dev/snapshot/track/<id>`. The **song** group's representative is marked (★), pinned ones distinguished from computed ones. Song is the only tier that carries a representative — see the *Representatives* section of `grouping-engine.md` for why.

The **song group** has an **"Edit in queue"** action that opens `/dev/canonical/review?tracks=…` with all of its tracks as a single ad-hoc item — that's how unmerging works, using the same semantics as everywhere else. The finer levels deliberately have no action of their own: the queue page edits all four tiers of the whole song group at once, so a per-subtree link would only offer a strictly weaker view of the same decision.

Default view is non-singleton groups only; a toggle includes singletons for completeness. Plus a filter box matching title or artist.

## Search → multiselect → queue

For pairs detection will never propose — a cover, a re-titled reissue, two songs that share nothing textually:

1. A search box over all tracks (title or artist substring, case-insensitive), showing cover, title, artists, album, ISRC, live count, and current group ids.
2. Checkboxes to select any number of results across searches (the selection persists as you re-search).
3. A **"Group selected"** button opening `/dev/canonical/review?tracks=<ids>` — one ad-hoc item, no pre-fills, current saved grouping rendered, full keyboard UI.

## Cross-artist list

A separate section: same normalized base title, **no shared artist** — the covers and Christmas songs worth a human look. Each entry lists its tracks with artist prominent, and there's a **"Review in queue"** button opening ~~`/dev/canonical/review?queue=cross-artist`~~ (**stale, noted 2026-08-17, P1-018** — that entry point is gone, replaced by the dedicated `/dev/canonical/cross` route and its assign-to-group model, per `grouping-fixes-backfill-M.md`'s rework).

Kept apart from the main list because most of these are *not* the same song, and mixing them into the main queue would poison it. **Also noted:** the "unreviewed if any pair is missing" rule (§Data model, `docs/specs/canonical-tracks.md`) is narrower here than site-wide — a cross-artist bucket settles on **cross-component pairs only** (`grouping-fixes-backfill-M.md` §1's M1 fix), not every pair in the bucket.

## Track page addition

~~`/dev/snapshot/track/<track_id>`~~ **(dead route, noted 2026-08-17, P1-018 — now `/track/<track_id>`, per `entity-pages-K.md` §12.1)** gains a **Canonical** line: its four group ids, each linking to that group on `/dev/canonical`, and its siblings at each tier (the other tracks sharing that id) with links to their own track pages. A track in no group shows all four as singletons.

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
