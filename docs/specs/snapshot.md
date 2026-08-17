# Snapshot — Feature Spec

Status: **ready to implement**. This spec is the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase workflow in `CLAUDE.md`: ask implementation questions live/one-at-a-time, don't decide undecided things yourself. Open questions are listed at the bottom.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).

> ## ⚠️ Superseded — historical reference only
>
> This spec predates the lettered roadmap and was never revisited after later steps replaced
> nearly everything in it. For current behavior, read the successor specs below. The one
> exception is the **Core model** section (append-only membership log, why copies get their own
> rows, and "Change detection & diffing") — that section is still the living, authoritative
> description of `_diff_playlist_tracks`; nothing else in the codebase documents its mechanics at
> this level of detail, so it was rewritten in place (below) rather than superseded.
>
> | this file | current reality | see |
> |---|---|---|
> | Routes (`/snapshot`, `/snapshot/playlist/<id>`, `/snapshot/track/<id>`) | gone — `/dev/snapshot` + the unified entity pages `/playlist/<id>` / `/track/<id>` | `canonical-tracks.md` Phase 1, then `entity-pages-K.md` §12.1 |
> | `track` table shape (`album_name`, no `isrc`/artist model) | fully replaced | `track-metadata-A.md` |
> | Spotify field list (`track.album.{id,name}`, no artist ids) | fully replaced by the artist/album model | `track-metadata-A.md` |
> | Pull & refresh flow (always-full-reread, no exclude, no resume) | replaced by the derived resumable work list | `partial-pulls-J.md` |
> | Pull progress (module-level status dict) | replaced by `jobs.JobStatus` | `partial-pulls-J.md`, `foreign-roundtrip-D.md` §2 |
> | Rate-limit handling ("rely on Spotipy's built-in 429 handling") | reversed — fail-fast (`RateLimited`) rather than blind-sleeping through an app-quota wait | `spotify_client.py`, `docs/spotify_constraints.md`, `partial-pulls-J.md` |
> | "Still **no write scopes**" (below) | **false** — `playlist-modify-private` has existed since D (fixed inline below too, since it's a security claim) | `foreign-roundtrip-D.md` |
> | Liked Songs "always re-pulled and diffed on every refresh" (below) | now gated on the `excluded` flag | `track-metadata-A.md` |
>
> Finding: P1-003 (whole-file), P1-002 (Change detection & diffing specifically).

> **Branch:** this work lives on `feat/snapshot`. Check with `git branch --show-current` and `git checkout feat/snapshot` if you're not already on it.

## Read first
- `CLAUDE.md` — conventions, workflow, tech stack, KISS + **security** rules.
- `docs/spotify_constraints.md` — hard API limits (`added_at`, `snapshot_id`, scopes, rate limits, folders not readable).
- `docs/Planning/feature_ideas.md` — where this sits in the backlog (this is the "Library snapshot" data foundation).
- Project memory `spotify-workflow` — Finn's library context (append-only playlists, semver versions, ATG, dedup rules).
- Existing code: `app.py` (`/api/snapshot/pull`, `_board_state`), `db.py` (schema + `snapshot` table), `spotify_client.py`, `config.py`.

## What this is
The **data foundation** for every downstream verification/analytics feature, plus a **read-only viewer** to look at that data for debugging and testing. Snapshot captures the full state of Finn's library — every playlist *and its actual track contents* — into SQLite, and keeps it current on demand. It is the layer the Audit features (version ⊆ Finn All, ATG ⊆ Finn All, dedup), the version engine (`added_at` clustering), and analytics all read from.

**Read-only:** it reads playlists and their tracks from Spotify; **nothing writes to Spotify anywhere in this feature.**

This feature is the evolution of the existing `/api/snapshot/pull` endpoint (which today captures only playlist-level metadata to seed canvas cards). We extend it to also capture `snapshot_id` and track contents, and we keep the existing canvas-card upsert intact so the Canvas keeps working.

## Core model: an append-only membership log (not periodic snapshots)

Spotify stamps every playlist track with **`added_at`** (real ISO timestamp) and gives every playlist an opaque **`snapshot_id`** that changes whenever its track contents change. It provides **no** record of removals. So instead of storing repeated full snapshots, we store a membership log:

- One row per **copy** of a track in a playlist, carrying Spotify's real `added_at`.
- Our own **`removed_at`** column, stamped when a previously-recorded copy is gone on a later pull.
- **Reconstruct a playlist as-of any date** = memberships where `added_at ≤ date AND (removed_at IS NULL OR removed_at > date)`.

This is efficient precisely because the library is append-only: refreshes almost always just append rows.

**Honest limitation (accepted):** add dates are exact; **removal dates are only pull-granular** — we know a removal happened *between two pulls*, not the exact moment.

### Why copies get their own rows (duplicate detection)
A playlist can legitimately hold the same track twice. The "Finn All has no duplicates" invariant means Symr must be able to **represent and detect** that — so the log does **not** collapse to unique `(playlist, track)`. Each copy is its own row with its own `added_at`. Exact-dup detection is then trivial: *any `(playlist, track)` with more than one live row.* (The single-vs-album-version dup is a *different* track id → a separate fuzzy title+artist match, not a schema concern here.)

### Change detection & diffing

**Rewritten 2026-08-17 (P1-002)** to describe `_diff_playlist_tracks`'s actual algorithm — the
original text above described a vaguer, different mechanism ("pick the copy whose
position/neighbouring tracks no longer line up") that was never implemented, and directly
contradicted itself elsewhere in this file by ruling position out as copy identity while the
real fallback pass uses position for exactly that. This section is the authoritative
description; nothing else in the codebase documents these mechanics at this level of detail.

- **`snapshot_id` is the cheap gate.** The `current_user_playlists` paging pass returns every
  playlist's current `snapshot_id` in ~3 calls. Compare to stored; only re-pull tracks for
  playlists whose `snapshot_id` changed. **No manual "tracked/archived" flag** — an edited old
  playlist changes its `snapshot_id` and gets caught automatically; an untouched one is never
  re-pulled.
- **Diff runs per track id, in three passes**, comparing stored live `membership` rows against
  the freshly-pulled items for that track:
  1. **Identity pass — exact `added_at` match.** A current copy whose `added_at` exactly matches
     a stored copy's `added_at` is treated as *the same copy*, wherever it now sits — its
     `position` is updated, nothing else changes. This pass runs first and is unconditional: it
     can match the *newest* stored copy while leaving an *older* one unmatched, which inverts the
     "newest presumed departed" intent of pass 3 below for whichever copies it resolves. The
     fallback passes only ever see whatever this pass leaves unmatched.
  2. **Position-order pairing, on a net increase or no change.** Whatever's left after pass 1 —
     unmatched current items and unmatched stored rows — is sorted by `position` (stored rows
     with a NULL position sort as `0`) and paired off index-by-index. **This is a deliberate use
     of position as copy identity**, for whichever copies the identity pass didn't already
     resolve — the earlier text in this file ruling that out entirely was wrong. Any current
     items left over after pairing (a net increase) become new `INSERT`s.
  3. **Oldest-survives fallback, on a net decrease.** If fewer unmatched current items remain
     than unmatched stored rows, the stored leftovers are sorted by `added_at` ascending (a NULL
     `added_at` sorts first, i.e. treated as oldest, and therefore never departs by this rule) —
     ties on equal `added_at` keep their existing position order, so the lowest-position one
     among a tie survives. The oldest `n_survive` (matching the count of unmatched current items)
     survive and are paired to the current leftovers by position order; **the survivor's
     `added_at` is overwritten with its paired current copy's `added_at`**, not just its
     position. The rest are marked `removed_at = now`.
- **Update `position` for every matched/surviving copy each pull.** Position is refreshed *data*
  in all three passes; it is only ever used as copy *identity* in pass 2, and only for copies the
  identity pass didn't already resolve.
- **Two caveats for anyone building fixtures or tests:** the query fetching stored rows has no
  `ORDER BY`, so which of two rows sharing an exact `added_at` gets consumed by pass 1 first is
  unspecified — assert set-level outcomes, not row order. And `position` here is Symr's own
  dense per-pull index (locals/episodes are skipped without incrementing it), not Spotify's raw
  item index.

### Derived recency (`last_changed_at`)
Computed, never a status you set. Seed on first capture from `max(added_at)` across the playlist's live memberships. Going forward it's `max(added_at, removed_at)` across its memberships (so our own removal stamps advance it too). Its only blind spot is a playlist whose most recent event was a removal *before Symr ever saw it* — which Spotify can't date anyway. Retained because it's a future cross-check for **folder placement** (a playlist still changing but filed under "Old Playlists," or a long-dead one sitting up top, is a flag).

## Data model
Extends the existing schema in `db.py` (additive migrations per the existing `_migrate` pattern).

**`snapshot`** (playlist-level — extend existing table; keep current columns `playlist_id` PK, `name`, `image_url`, `owner`, `track_count`, `pulled_at`):
- add `snapshot_id TEXT` — Spotify change token.
- add `last_changed_at TEXT` — computed (see above).
- add `tracks_pulled_at TEXT` — when this playlist's contents were last captured; `NULL` = not captured yet.
- add `unfollowed_at TEXT` — set when the playlist disappears from the user's list; its memberships are **retained**, not deleted.
- add `description TEXT` — playlist description (cheap; the future ATG scan-pointer feature reads it). The synthetic `__liked__` row gets no Canvas card (Canvas behavior stays unchanged — real playlists only).

**`track`** (unique track dimension — new):
- `track_id TEXT PRIMARY KEY`, `name TEXT`, `artists TEXT` (comma-joined artist names), `album_id TEXT`, `album_name TEXT`, `duration_ms INTEGER`. Store a bit more where cheap (useful later).

**`membership`** (one row per copy — new):
- `id INTEGER PK`, `playlist_id TEXT` → `snapshot(playlist_id)`, `track_id TEXT` → `track(track_id)`, `position INTEGER`, `added_at TEXT`, `removed_at TEXT` (NULL = live).
- **No** unique constraint on `(playlist_id, track_id)` (copies allowed).
- Indexes: `(playlist_id)`, `(track_id)` — for the "where does this track appear" query.

**Liked Songs** is included, represented as a **synthetic playlist row** in `snapshot` (fixed sentinel `playlist_id`, e.g. `__liked__`, name "Liked Songs", `owner` = the user). Its memberships live in `membership` like any other playlist. Because Liked Songs (`/me/tracks`, "Saved Tracks") has **no `snapshot_id`**, the cheap change-gate can't apply to it — so it is **always re-pulled and diffed on every refresh, unless excluded** (the `excluded` flag added by `track-metadata-A.md` applies to `__liked__` like any other row). It does have `added_at`, so the log/removal model works normally.

**Pull progress** is tracked in an **in-memory module-level status object** guarded by a lock (single process, single user — no DB table needed): `running`, `phase`, `playlists_total`, `playlists_done`, `current_playlist`, `started_at`, `finished_at`, `error`.

Persistence: **SQLite** via the stdlib `sqlite3` + the existing thin helper (per CLAUDE.md).

## Spotify integration
- Use **Spotipy**. **Scopes:** add **`user-library-read`** to `SPOTIFY_SCOPES` (for Liked Songs / Saved Tracks) alongside the existing `playlist-read-private`, `playlist-read-collaborative`. **This scope change forces a one-time re-login** (Finn re-consents; delete/refresh the cached token). At the time this feature was built, no write scopes were requested — **that changed with `foreign-roundtrip-D.md`**, which added `playlist-modify-private` for the round-trip's scratch-playlist replace. This app now holds a write scope; see `foreign-roundtrip-D.md` for what it's used for (`roundtrip.py` only, per its own module invariant).
- **Playlist list:** page `current_user_playlists(limit=50)` via `sp.next` (as the existing endpoint does). Grab `snapshot_id`, name, image, owner, `tracks.total`.
- **Tracks:** page each playlist's items (100/page) via `sp.next`. For each item read `track.id`, `track.name`, `track.artists[].name`, `track.album.{id,name}`, `duration_ms`, and the item's `added_at` + position.
- **Liked Songs:** page `current_user_saved_tracks(limit=50)` via `sp.next` into the synthetic `__liked__` playlist; each item has `added_at`.
- **Skip** items with no usable track id — local files (`is_local`) and unavailable/null tracks; podcast episodes (`track.type == 'episode'`). Count skipped for display.
- **Rate limits:** rely on Spotipy's built-in `429`/`Retry-After` handling; batch (50 playlists / 100 tracks per page). A full cold pull is ~149 playlists + ~15–20k memberships ≈ a few hundred requests / a few minutes — slow, hence progress reporting.
- **Folder placement is NOT available via the API** — do not attempt to read it.
- Credentials are already wired (`config.py` / `.env`); do not hardcode or handle secrets.

## Pull & refresh flows

Both flows run in a **background thread** so the page can poll `/api/snapshot/status` for a live progress bar. Both run under a single-pull guard (reject a second concurrent pull). Manual trigger only — no scheduling.

**Full pull** (`POST /api/snapshot/pull`, cold / one-time-ish):
1. Page all playlists → upsert `snapshot` (incl. `snapshot_id`) **and** the existing canvas-card upsert (keep Canvas working).
2. Mark any `snapshot` playlist no longer in the list with `unfollowed_at` (retain its memberships).
3. For **every** playlist: pull tracks, upsert `track` rows, diff into `membership` (all live rows on first pull are inserts), set `tracks_pulled_at`, recompute `last_changed_at` + `track_count`.
4. Pull Liked Songs into `__liked__` and diff the same way.

**Refresh** (`POST /api/snapshot/refresh`, cheap / routine):
- Same as full pull, but step 3 runs **only** for playlists that are new or whose `snapshot_id` changed since stored. Unchanged playlists are skipped entirely. Step 4 (Liked Songs) always runs — it has no `snapshot_id` to gate on.

## The pages
Replace the `coming_soon` stub. Three server-rendered pages (each extends `base.html`) + vanilla JS. **Detail views are their own routes** (real, bookmarkable/reloadable URLs) so the search and changes panels can deep-link into them.

**`/snapshot`** (index) — panels 1–3, 5, 6:
1. **Status header** — total playlists, how many captured, total live memberships, last full-pull / last-refresh time. Live progress bar while a pull runs (playlists done / total, current playlist).
2. **Controls** — "Full pull" and "Refresh" buttons (disabled while running).
3. **Playlist table** — cover, name, owner, track count, `last_changed_at`, captured-yet. Each row links to `/snapshot/playlist/<id>`.
5. **Track "where does it appear"** — search a track (name/artist) → every playlist it's in (links to those playlist pages), with add/remove dates. Answers "how many current-favs has this song shown up in." Results link to `/snapshot/track/<id>`.
6. **Changes** — most recent detected adds and removes across the library (order by `added_at` / `removed_at` desc); rows link into the relevant playlist/track pages.

**`/snapshot/playlist/<playlist_id>`** (panel 4) — that playlist's tracks: track, artist, album, `added_at`, `removed_at`, position. Sortable. Removed rows visually distinguished. Renders **all** rows (no pagination — fine for a local single-user tool, even Finn All at ~2300).

**`/snapshot/track/<track_id>`** — every playlist a track appears in, with add/remove dates (links back to playlist pages).

Keep it minimal and consistent with existing pages (function over form). Style in the single `static/css/style.css`.

## Routes & endpoints (all read-only w.r.t. Spotify; login-gated like the rest)
**Pages** (server-rendered from the DB — Jinja, no parallel JSON needed):
- `GET  /snapshot` — index (panels 1–3, 5, 6). Track search is a `?q=` query param the route reads and renders server-side.
- `GET  /snapshot/playlist/<playlist_id>` — playlist detail.
- `GET  /snapshot/track/<track_id>` — track detail.

**JSON endpoints** (for the dynamic bits only):
- `POST /api/snapshot/pull` — kick off a full cold pull in a background thread (extends existing endpoint).
- `POST /api/snapshot/refresh` — kick off a `snapshot_id`-gated refresh in a background thread.
- `GET  /api/snapshot/status` — pull progress + summary counts (polled by the index page while a pull runs).

## Out of scope (design the seams, don't build)
- Any **write** to Spotify.
- The Audit/verification checks, the version engine, analytics — they *read* this data later; not built here.
- Scheduled/background auto-refresh.
- Extended streaming history / ListenBrainz ingestion.
