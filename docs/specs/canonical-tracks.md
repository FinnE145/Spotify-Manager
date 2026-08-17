# Canonical Tracks — Feature Spec

Status: **ready to implement**. This spec + its four sub-specs in `docs/canonical-tracks/` are the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase skill: ask live for anything unforeseen, don't decide undecided things yourself.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`), findings P1-008 and P1-018. `grouping-engine.md` and `viewer-page.md` are stamped too, with their own drift noted inline. `detection.md` and `review-ui.md` were in the blind audit's read scope but produced no findings of their own — largely because their subject matter (detection, review UI) is more thoroughly re-covered by `grouping-catch-up-E.md`'s and `detection-artist-model.md`'s own audits. They are **not** separately stamped Audited; treat them as unverified until a finding explicitly clears them.

> **Branch:** this work lives on `feat/canonical-tracks`. Check with `git branch --show-current`.

## Read first
- `CLAUDE.md` — conventions, workflow, tech stack, KISS + **security** rules.
- `docs/specs/snapshot.md` — the data foundation this sits on (`track`, `membership`, the pull/refresh flows).
- `docs/spotify_constraints.md` — hard API limits, especially the **app-level quota exhaustion** section (a careless extra pull can lock the app out for ~24h).
- Existing code: `db.py` (schema + `_migrate`), `snapshot.py` (pull engine, `_parse_track_item`, `_upsert_track`), `app.py` (routes, error handling), `templates/base.html` (navbar).

## What this is

Spotify has no concept of "the same song." Every appearance of a song under a different album, master, or edition is a **separate track id**. That makes it impossible to answer the questions Symr exists to answer: *is this playlist holding the same song twice? how many playlist generations has this song survived? how many times have I actually played it?*

This feature builds the **grouping layer** that answers those: a four-tier set of internal ids linking Spotify track ids that represent the same thing, plus the review UI to build and maintain that mapping by hand.

**Read-only w.r.t. Spotify.** Nothing here writes to the library. The only Spotify calls are reads that fill in two missing columns (ISRC, album cover).

**It is not the duplicate audit.** The Finn All dedup check, the version engine, and analytics *consume* these ids later. This feature only produces them.

## Why one canonical id isn't enough

Worked example — the song "AAA":

| Event | Result |
|---|---|
| Released as its own single | 1 song, 1 recording, 1 version, 1 release |
| Included on two later singles + the album | still 1 recording/version, now **4 releases** (4 track ids) |
| "AAA (Acoustic)" drops on a single | **2 versions** — sounds different |
| "AAA (Remastered)" on a remaster album | **3 recordings**, still 2 versions — sounds the same |

Each of those distinctions matters to a *different* consumer, and no single id can serve them all:

- Play counts should merge all 4 releases and the remaster, but **not** the acoustic (unless you deliberately ask it to).
- Playlist duplicate detection should flag all 4 releases and the remaster as dups, but the acoustic is legitimately its own track.
- Playlist track views must stay **track-id-exact**, so the album cover and `added_at` you actually added are what you see.

Hence four nested tiers.

## The four tiers

| # | Tier | Boundary | Example |
|---|---|---|---|
| 1 | **Song** | the composition, any performance | AAA / AAA (Acoustic) / AAA (Remix) together |
| 2 | **Version** | *sounds different* | AAA (Acoustic) ≠ AAA |
| 3 | **Recording** | *sounds the same, different master* | AAA (Remastered), Taylor's Version, deluxe, clean/explicit |
| 4 | **Release** | *same audio, different album/EP/single* | AAA's four track ids |

Strictly nested: **release ⊂ recording ⊂ version ⊂ song**. Every track carries all four ids; a track nobody ever merged is a singleton at all four levels.

These names map onto MusicBrainz's model (song ≈ *work*, recording ≈ *recording*, release ≈ *release*); the **version** tier is Symr's own, and it's the one that carries the weight — it's the default rollup level for everything.

### How consumers use the tiers

| Consumer | Tier |
|---|---|
| Playlist track views (cover, title, `added_at`) | raw `track_id` — **never** rolled up |
| Playlist duplicate detection | **version** |
| Play counts / listening stats | **version** by default, opt-in toggle to **song** |
| Playlist-generation span ("how many current-favs did this survive") | **version** by default, opt-in toggle to **song** |
| Wrong-cover / wrong-edition audits | **recording** and **release** |

Because recording and release stay distinct underneath version, a future dedup report can grade what it finds: *"same release — literally the same audio"* vs *"different master — pick one."*

### Terminology: track id vs release

A Spotify track id is 1:1 with (recording, release) — the same recording on a single and on the album is two track ids. So `track_id` ≈ release, with two known exceptions that the release tier exists to absorb:

- **Track relinking.** Spotify re-points tracks across markets and over time (`linked_from`); a playlist entry added years ago can hold an id that now resolves differently.
- **Duplicate album uploads.** Distributors sometimes upload the same album twice, producing two album ids with identical audio, cover, and ISRC.

A release group will be a singleton ~99% of the time, and the auto-grouping rules (see `docs/canonical-tracks/detection.md`) pre-fill the rest, so it should almost never need a manual decision.

## Non-interference guarantee

**Nothing in this feature mutates `track` or `membership` rows.** The grouping lives in its own tables keyed by `track_id`. Playlist history — which exact track id was in which playlist, and when it was added or removed — is untouched and stays authoritative forever. Grouping is a **lens applied at query time** by whichever consumer wants it.

The two columns added to `track` (`isrc`, `album_image_url`) are new, additive, and purely descriptive.

## Data model

Additive migrations following the existing `_migrate` pattern in `db.py`.

**`track`** — two new columns:
- `isrc TEXT` — from `external_ids.isrc`. The strongest same-recording signal available.
- `album_image_url TEXT` — the **300px** entry from `album.images` (fall back to the middle entry, then the first, then NULL).

**`canonical_group`** (new) — one row per group, at any tier:
```sql
CREATE TABLE canonical_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL CHECK (tier IN ('song', 'version', 'recording', 'release')),
    representative_track_id TEXT REFERENCES track(track_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```
**The shipped schema also carries `auto_run_id`** (added post-launch by `grouping-catch-up-E.md`'s
auto-group feature — tags which `auto_group_run`, if any, created this group), not listed above
since it predates E. See `grouping-catch-up-E.md` for what it's for.
`created_at` is ISO-8601 with an explicit `Z`. Plain `datetime('now')` is naive UTC, which the front-end parses as local time and renders hours off — `db.py` carries a migration that rewrites any rows written in the old form.
`AUTOINCREMENT` guarantees ids are **never reused**, so a future listening-history table can reference a song id permanently. Ids are globally unique across tiers (no ambiguity about what a bare id means).

`representative_track_id` is **NULL by default**, meaning "compute it" (see below). It's only set when Finn explicitly pins one, so the default keeps tracking reality as playlist memberships change.

**`track_group`** (new) — one row per track, its four ids:
```sql
CREATE TABLE track_group (
    track_id TEXT PRIMARY KEY REFERENCES track(track_id),
    song_id INTEGER NOT NULL REFERENCES canonical_group(id),
    version_id INTEGER NOT NULL REFERENCES canonical_group(id),
    recording_id INTEGER NOT NULL REFERENCES canonical_group(id),
    release_id INTEGER NOT NULL REFERENCES canonical_group(id)
);
CREATE INDEX idx_track_group_song ON track_group(song_id);
CREATE INDEX idx_track_group_version ON track_group(version_id);
CREATE INDEX idx_track_group_recording ON track_group(recording_id);
CREATE INDEX idx_track_group_release ON track_group(release_id);
```
Every track in `track` gets a row with four freshly-allocated singleton groups the first time it's seen (~14,400 `canonical_group` rows for the current 3,589 tracks — trivial for SQLite). This materialized form means every consumer query is a plain join, no union-find at read time.

**`reviewed_pair`** (new) — what makes "decided separate" distinguishable from "never looked at":
```sql
CREATE TABLE reviewed_pair (
    track_id_a TEXT NOT NULL REFERENCES track(track_id),
    track_id_b TEXT NOT NULL REFERENCES track(track_id),
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (track_id_a, track_id_b)
);
```
Always stored with `track_id_a < track_id_b` lexicographically. A candidate group is **unreviewed** if *any* pair within it is missing here — so when a pull later adds a fifth track to a settled group of four, the group returns to the queue with only the new decision outstanding.

There is deliberately **no decision log** — for the hand-reviewed queue. Undo there is in-session only (client-side); anything already committed is fixed by navigating back to that item or re-opening the group from the viewer page. **This does not extend to auto-grouping** (noted 2026-08-17, P1-008/P1-018) — `grouping-catch-up-E.md`'s `auto_group_run` log and snapshot-based undo *are* a decision log, deliberately, for exactly the runs a human never reviewed.

### Representative track

**Rewritten 2026-08-17 (P1-008)** — the rule below is superseded by `scoring-H.md` §11.3, which
made a deliberate change and says so in its own text; this section was simply never updated to
match. `canonical.representative()`'s own docstring already cites §11.3 as current.

When `representative_track_id` is NULL, compute it: **highest `score.all_time`** (the track
tier's own score — not the group's tier; a version group's representative is elected by its
member tracks' *track*-tier scores, defaulting to `0.0` for an unscored track) → **oldest
`added_at`** (over *all* membership rows for the track, live or not — no longer filtered to
`removed_at IS NULL` as the pre-H rule was; a track with no membership rows at all sorts last,
not first) → **lowest `track_id`**. If the `score` table is ever empty (e.g. before the first
recompute) or a recompute is failing, every candidate coalesces to `0.0` and the election
silently collapses to the tail of this rule (oldest `added_at` → lowest `track_id`, without the
old live-membership filter). `track.popularity` and `track.album_image_url` no longer exist at
all (moved to `album.image_url` in `track-metadata-A.md`; `popularity` is simply gone, not just
always-NULL as this section previously said).

The representative is used for group titles and covers on `/dev/canonical` and by future
analytics rows. It is **never** used in playlist track views, which always render the real
track. **It reads the `score` table, so — since `async-recompute-N.md` made recompute
asynchronous — it is a moving target**: group titles/covers on `/dev/canonical`, the cross
queue, tenure, search, and the entity pages can now shift between two page loads with no user
action in between (a review-queue keypress, a pin, or an artist merge each fire a background
recompute). Nothing breaks, but it's a real, silent UX consequence worth knowing about if a
representative ever looks like it "changed on its own." **`representative()`/`group_tree()` are
also now called on version-tier groups** (album/artist/search pages), not song-tier only as this
section implies — pinning stays **song-tier only**; a version group always uses the computed
election, never a pin.

## Phases

Each phase is independently testable; stop and test at each boundary.

| Phase | What | Sub-spec |
|---|---|---|
| **1** | `/dev` reorg — landing page, `/snapshot*` → `/dev/snapshot*`, gear nav icon | — (below) |
| **2** | Data capture — `isrc` + `album_image_url` in pulls, one-time backfill script | — (below) |
| **3** | Grouping engine — tables, nesting invariants, merge/detach/ungroup | `docs/canonical-tracks/grouping-engine.md` |
| **4** | Detection — normalization, candidate groups, pre-fills, ordering | `docs/canonical-tracks/detection.md` |
| **5** | Review queue UI — `/dev/canonical/review` | `docs/canonical-tracks/review-ui.md` |
| **6** | Viewer page — `/dev/canonical`, stats, group browser, search | `docs/canonical-tracks/viewer-page.md` |

Phases 3 and 4 produce no real UI. Phase 4 ships a **throwaway plain listing** at `/dev/canonical` (a flat dump of candidate groups and their pre-fills) so detection quality can be judged *before* the queue UI is built on top of it; phase 6 replaces that page entirely.

### Phase 1 — `/dev` reorg

The snapshot pages are developer/inspection tools, and more will follow. Give them a home.

- New `GET /dev` — a plain landing page listing links to the dev tools: **Snapshot** (`/dev/snapshot`) and **Canonical Tracks** (`/dev/canonical`). Extends `base.html`. Just a title and a list of links with one-line descriptions.
- Move the existing routes, keeping their behavior identical:
  - `/snapshot` → `/dev/snapshot`
  - `/snapshot/playlist/<playlist_id>` → `/dev/snapshot/playlist/<playlist_id>`
  - `/snapshot/track/<track_id>` → `/dev/snapshot/track/<track_id>`
  - `/api/snapshot/*` endpoints keep their current paths (they're API, not pages).

  (Noted 2026-08-17, P1-018: this bullet is a historically-accurate record of what this phase
  did — `/dev/snapshot/playlist/<id>` and `/dev/snapshot/track/<id>` were both later removed
  entirely by `entity-pages-K.md` §12.1, replaced by `/playlist/<id>` / `/track/<id>`. Not a
  correction to this bullet; a pointer forward for a reader who follows one of these links today.)
- **No redirects** from the old paths — this is a local single-user tool; the old URLs simply stop existing.
- Navbar: the existing snapshot icon in `.nav-utility` becomes a **gear**, **icon only** (matching how it renders today), linking to `/dev`. Its `active` state covers every `/dev/*` page.
- Update every internal link and `url_for` reference (templates, `static/js/snapshot.js`) to the new endpoint names. Update the Codebase Map in `CLAUDE.md`.

### Phase 2 — data capture

**In the pull engine** (`snapshot.py`):
- `_parse_track_item` also reads `external_ids.isrc` and the album cover URL (300px rule above).
- `_upsert_track` writes both new columns, including in its `ON CONFLICT DO UPDATE` clause, so re-seen tracks get topped up.

**How the existing 3,589 rows get filled.** `GET /v1/tracks?ids=` — the 50-at-a-time batch endpoint — **403s for this app** (verified Jul 2026; see `docs/spotify_constraints.md`), so there is no cheap bulk top-up. A **full snapshot pull** is the primary mechanism instead: playlist items are full track objects carrying `external_ids` and `album.images`, so one pull populates both columns for every track in a live playlist.

**Mop-up** — `scripts/backfill_track_details.py`, committed, for what a pull can't reach (tracks in the 7 playlists that 403 on item reads, and tracks surviving only in removed memberships):
- Selects `track_id` where `isrc IS NULL OR album_image_url IS NULL`, one request each via `sp.track(id)` (the single-track endpoint works).
- Refuses to run against more than 300 rows without `--yes`, since one request per track is exactly the burst pattern that exhausts the app-level quota (~24h lockout). `--limit N` chips away safely.
- Uses `spotify_client.get_spotify_client()` (reads the existing `.spotipy_cache` token) and `db.connect()`. Prints progress; commits as it goes so an interrupted run resumes cleanly.
- Reuses `snapshot._call`, which sleeps once through a short `429` but raises `RateLimited` on a long `Retry-After` — **never** blind-sleep, per the quota section of `docs/spotify_constraints.md`.
- **The columns, the pull capture, and this script are built and run during the planning session, not by the implementation session** — the data is a hard prerequisite for phases 3–6, so it's verified up front. An implementation session should confirm the columns are populated (`SELECT COUNT(*) FROM track WHERE isrc IS NULL`) rather than rebuild this.

Also add the ISRC and relinking facts from this spec's *Terminology* section to `docs/spotify_constraints.md`.

## Notes for future consumers (hints, not rules)

These are recorded so the seams make sense, **not** as decided design. Each gets its own spec when its time comes.

- ~~**Listening history / ListenBrainz.** A play event resolves to a `track_id` → `track_group` → version id, and stats roll up at version level with a toggle to song level. Plays whose track id has never appeared in a playlist won't have a `track_group` row; that ingestion feature needs to decide whether to create rows for unseen tracks or match them by title/artist/ISRC.~~
  **Built, noted 2026-08-17 (P1-018).** `play-history-C.md` + `foreign-roundtrip-D.md`'s
  `roundtrip.py` answer this: an unseen-track play resolves through the round-trip
  (`played_uri_track`), not by title/artist/ISRC matching.
- ~~**Rollup toggle.** Rather than a binary version/song switch, analytics pages may eventually offer a 3- or 4-position rollup selector (song / version / recording / release), since the ids for all four already exist.~~
  **Partially built, noted 2026-08-17 (P1-018).** `generations.py`'s `tier="version"|"song"`
  parameter is exactly this, at two positions rather than four.
- **Dedup report.** Runs at version level and can grade severity using the finer tiers: same release = pure duplicate; different recording = same-sounding, pick one; different version = not a duplicate at all.
- **Cover audits.** Release and recording ids make "you added the odd-cover edition of this song, unlike everywhere else you added it" a computable check.

## Out of scope

- Any **write** to Spotify.
- The duplicate audit, version engine, and analytics that consume these ids.
- ~~Automatic grouping without review — every merge is confirmed by hand (pre-fills are only pre-fills; nothing is written until Enter).~~ **No longer true, noted 2026-08-17 (P1-008/P1-018).**
  `grouping-catch-up-E.md`'s auto-group feature (`canonical_autogroup.py`) does exactly this —
  closes qualifying queue items in a batch with no human review pass at all, deliberately, once
  its detection rule is confident enough. This scope statement predates E and E reversed it on
  purpose; it's kept here (struck, not deleted) as a record of what this spec originally
  intended, since the two specs actively disagree rather than one simply extending the other.
- Fuzzy matching beyond the normalization in `docs/canonical-tracks/detection.md` (no edit-distance, no external metadata services like MusicBrainz).
- ~~Cross-session undo history.~~ **No longer true, same note as above.** `auto_group_run` plus
  the `auto_group_snapshot_*` tables are exactly a server-side, cross-session decision log with
  restore, built by `grouping-catch-up-E.md` for its auto-group runs specifically (the
  hand-reviewed queue's own undo is still in-session/client-side only, unaffected).
