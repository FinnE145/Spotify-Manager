# Canonical Tracks — Feature Spec

Status: **ready to implement**. This spec + its four sub-specs in `docs/canonical-tracks/` are the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase skill: ask live for anything unforeseen, don't decide undecided things yourself.

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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```
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
    decided_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (track_id_a, track_id_b)
);
```
Always stored with `track_id_a < track_id_b` lexicographically. A candidate group is **unreviewed** if *any* pair within it is missing here — so when a pull later adds a fifth track to a settled group of four, the group returns to the queue with only the new decision outstanding.

There is deliberately **no decision log**. Undo is in-session only (client-side); anything already committed is fixed by navigating back to that item or re-opening the group from the viewer page.

### Representative track

When `representative_track_id` is NULL, compute it: **most live memberships** (`membership` rows with `removed_at IS NULL`) → **oldest `added_at`** → **lowest `track_id`**. Note `track.popularity` is NULL for every row in the library (Spotify no longer returns it on playlist-item track objects), so it plays no part.

The representative is used for group titles and covers on `/dev/canonical` and by future analytics rows. It is **never** used in playlist track views, which always render the real track.

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
- **No redirects** from the old paths — this is a local single-user tool; the old URLs simply stop existing.
- Navbar: the existing snapshot icon in `.nav-utility` becomes a **gear**, **icon only** (matching how it renders today), linking to `/dev`. Its `active` state covers every `/dev/*` page.
- Update every internal link and `url_for` reference (templates, `static/js/snapshot.js`) to the new endpoint names. Update the Codebase Map in `CLAUDE.md`.

### Phase 2 — data capture

**In the pull engine** (`snapshot.py`):
- `_parse_track_item` also reads `external_ids.isrc` and the album cover URL (300px rule above).
- `_upsert_track` writes both new columns, including in its `ON CONFLICT DO UPDATE` clause, so re-seen tracks get topped up.

**One-time backfill** — `scripts/backfill_track_details.py`, committed:
- Selects `track_id` from `track` where `isrc IS NULL OR album_image_url IS NULL`.
- Batches of 50 through `sp.tracks(ids)` — ~72 calls for the current 3,589 tracks. Also stores `popularity` when present (harmless; nothing depends on it).
- Uses `spotify_client.get_spotify_client()` (reads the existing `.spotipy_cache` token) and `db.connect()`. Prints progress; commits as it goes so an interrupted run resumes cleanly on the next invocation.
- Short back-off on `429` then fail fast — **never** blind-sleep on `Retry-After`, per the quota section of `docs/spotify_constraints.md`.
- **The columns and this script are built and run during the planning session, not by the implementation session** — the data is a hard prerequisite for phases 3–6, so it's verified up front. An implementation session should confirm the columns are populated (`SELECT COUNT(*) FROM track WHERE isrc IS NULL`) rather than rebuild this. The script stays committed so a rebuilt DB or a straggler row can be topped up.

Also add the ISRC and relinking facts from this spec's *Terminology* section to `docs/spotify_constraints.md`.

## Notes for future consumers (hints, not rules)

These are recorded so the seams make sense, **not** as decided design. Each gets its own spec when its time comes.

- **Listening history / ListenBrainz.** A play event resolves to a `track_id` → `track_group` → version id, and stats roll up at version level with a toggle to song level. Plays whose track id has never appeared in a playlist won't have a `track_group` row; that ingestion feature needs to decide whether to create rows for unseen tracks or match them by title/artist/ISRC.
- **Rollup toggle.** Rather than a binary version/song switch, analytics pages may eventually offer a 3- or 4-position rollup selector (song / version / recording / release), since the ids for all four already exist.
- **Dedup report.** Runs at version level and can grade severity using the finer tiers: same release = pure duplicate; different recording = same-sounding, pick one; different version = not a duplicate at all.
- **Cover audits.** Release and recording ids make "you added the odd-cover edition of this song, unlike everywhere else you added it" a computable check.

## Out of scope

- Any **write** to Spotify.
- The duplicate audit, version engine, and analytics that consume these ids.
- Automatic grouping without review — every merge is confirmed by hand (pre-fills are only pre-fills; nothing is written until Enter).
- Fuzzy matching beyond the normalization in `docs/canonical-tracks/detection.md` (no edit-distance, no external metadata services like MusicBrainz).
- Cross-session undo history.
