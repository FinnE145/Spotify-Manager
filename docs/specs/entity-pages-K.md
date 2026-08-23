# Entity viewing pages

**Step K of `docs/Planning/roadmap.md`.**

Proper pages for viewing a **song**, **version**, **recording**, **release**, **track**,
**album**, **artist** and **playlist** — the canonical place each entity is displayed, which
every other page links into instead of re-deriving its own display. Plus a navbar search box,
so an entity is reachable without already being on a page that links to it.

This is the first set of **real app pages** in Symr that aren't dev tools. They live at the
top level (`/song/<id>`, `/artist/<id>`, …), not under `/dev`.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).

---

## 0. Measured facts

Measured **2026-08-13** against the live `symr.db` and the live API. The roadmap's numbers are
from July and are superseded for anything below; see §14 for what this contradicts.

| | |
|---|---:|
| Tracks | 9,930 |
| — with no live membership (played, never in a playlist) | 6,297 (63%) |
| Albums | 6,214 (5,090 with exactly 1 known track) |
| — tracks on an album where >1 track is known | 4,840 (49%) |
| — albums with `total_tracks` > 50 | 55 (max 460) |
| Artists, alias-resolved | 4,096 (12 aliased-away ids) |
| Song / version groups | 8,785 / 8,941 |
| — version groups with >1 track | 842 |
| Playlists | 153 |
| Plays | 93,063, spanning 2020-02-12 → **2026-08-06** |
| Generations | **37** (`v37.0.0`, added by B's confirm-on-pull flow) |
| `track_uri_alias` rows | 34 |

**API probe, 2026-08-13.** Only the *bulk* forms had ever been probed. The singular forms all
work:

| Endpoint | Result |
|---|---|
| `GET /v1/artists/{id}` | **works** — returns `images` (640/320/160). `genres`, `followers`, `popularity` keys are absent. |
| `GET /v1/albums/{id}` | **works** — returns `copyrights`, `external_ids`, `genres` (empty), and **the tracklist inline** (50/page). `label` and `popularity` absent. |
| `GET /v1/albums/{id}/tracks` | **works** — simplified track objects. |

The album tracklist items are **simplified** track objects: `artists`, `disc_number`,
`duration_ms`, `explicit`, `id`, `name`, `track_number`, `uri`, `is_local`, `external_urls`.
No `album`, **no `external_ids`/ISRC**, no `is_playable`, no `linked_from`. This is load-bearing
— see §5.3.

`docs/spotify_constraints.md` is updated with all three results as part of this step.

---

## 1. The shape

### 1.1 Group pages are the primary entity pages

The four canonical tiers exist to *group tracks*, so a version must not be a second-class view
of a track. It's the other way round:

- **Song / version / recording / release pages carry the whole useful set** — plays, playlists,
  generation presence and tenure, artists, albums, the nested subtree — all **aggregated over
  every track in the group**.
- **The track page is the narrow one.** It carries only what exists per-row and can't be
  aggregated: uri, ISRC, track/disc number, `linked_from`, `is_playable`, `external_url`, and
  that specific row's memberships.

**User-facing lists link to `/version/<id>`**, not to tracks: playlist track tables, album
tracklists, artist track lists and search results. The version page then lists its member
tracks, each linking to `/track/<id>`. Dev pages (`/dev/canonical*`, `/dev/roundtrip`,
`/dev/snapshot`) keep linking to `/track/<id>`, where the specific row *is* the point.

### 1.2 URLs are flat, not tiered

`/version/<id>`, never `/song/<id>/version/<id>`.

**Group ids are not stable.** `db.py` says so itself, in the comment explaining why
`pending_tier_review` is keyed on track id: group ids "are reconciled by `apply_partition` and
a group can be absorbed into another, leaving a stored group id pointing at nothing. Track ids
never move."

A tiered URL would therefore carry three reconcilable ids instead of one, and could go
*internally inconsistent* (version V no longer under song S) — which forces a chain-validation
query on every request plus a policy for when the chain breaks. Flat carries one id, and the
breadcrumb is derived by a single lookup, exactly how `nested_tree` / `song_tree` already treat
the hierarchy. Flat is also what makes each tier a **peer**: a top-level `/version/<id>` is the
opposite of filing versions underneath songs in the URL space.

A reconciled-away group id **404s**, matching the precedent `/dev/canonical/group/<id>` already
sets.

### 1.3 One request per page load, hard ceiling

`/album/<id>` and `/artist/<id>` fetch from Spotify on **first view only**, then cache in the DB
forever. The ceiling is absolute:

- **At most one Spotify request per page load**, and only on that entity's own page. Never on a
  list, never on search, never on a group/track/playlist page.
- An album with more than 50 tracks renders the 50 that came inline plus a note; it does **not**
  page for the rest. 55 albums are affected. This is what keeps the ceiling absolute.
- Any failure — 429, network, 404 — degrades to "not fetched yet" on the page and is **not** an
  error. The page always renders from what the DB has.

Step J exists because exhausting the dev-mode quota is a real ~24h lockout. A page view must
never be able to cause one.

---

## 2. Routes

All top-level, all covered by the existing `before_request` login guard.

| Route | Endpoint | Purpose |
|---|---|---|
| `GET /song/<int:group_id>` | `song_page` | group page, tier `song` |
| `GET /version/<int:group_id>` | `version_page` | group page, tier `version` |
| `GET /recording/<int:group_id>` | `recording_page` | group page, tier `recording` |
| `GET /release/<int:group_id>` | `release_page` | group page, tier `release` |
| `GET /track/<track_id>` | `track_page` | the leaf |
| `GET /album/<album_id>` | `album_page` | album, fetches on first view |
| `GET /artist/<artist_id>` | `artist_page` | artist, fetches on first view |
| `GET /playlist/<playlist_id>` | `playlist_page` | playlist, `?generation=1` toggle |
| `GET /search` | `search_page` | `?q=`, server-rendered |

The four group routes are **four `@app.route` decorators on one view function**, each supplying
its tier — not four functions. Unknown or reconciled-away ids `abort(404)`.

**Removed** (§12): `/dev/snapshot/track/<id>`, `/dev/snapshot/playlist/<id>`,
`/dev/generations/<ordinal>`.

---

## 3. The group page

`templates/entity_group.html`, one template for all four tiers. No `active` navbar slot.

### 3.1 Header

- **Title**: the representative track's name, via `canonical.representative(conn, group_id)` →
  `canonical.track_display()`. Already tier-agnostic.
- Artists (linked to `/artist/<id>`), album cover thumbnail and album name (linked), duration.
- The tier as a label ("Version"), the member track count, and a **pinned** marker when
  `representative_track_id` is set.
- **Breadcrumb**: `Song › Version › Recording › Release`, with the current tier not linked.
  Derived from any one member track's `track_group` row — a single `SELECT song_id, version_id,
  recording_id, release_id FROM track_group WHERE <tier>_id = ? LIMIT 1`.

### 3.2 Sections

1. **Plays** — total / past 30 days / past 7 days over every member track, plus the data-through
   date (§8).
2. **Playlists** — every playlist any member track is or was in: playlist name (→
   `/playlist/<id>`), which member track, `added_at`, `removed_at`. Removed rows carry the
   existing `removed` class. This is also the answer to "is this in my library at all" — a group
   with no rows here is a played-but-never-added track, and needs no separate flag.
3. **Generations** — the 37-cell presence strip (real `<td>`s, as B's tenure table renders it),
   plus tenure / total generations / run count for this group. Computed from member **track
   ids** against `generation_presence`, which works uniformly at all four tiers (`song_id` and
   `version_id` are the only group columns that view exposes, but `track_id` covers every tier).
4. **Subtree** — the nesting from this tier down: song → versions → recordings → releases →
   tracks. A release group shows just its tracks. Every node links to its own page.
5. **Member tracks** — a table of the group's tracks: name (→ `/track/<id>`), artists, album,
   duration, ISRC.
6. **Edit** — a link to `/dev/canonical/group/<group_id>`, which redirects into **the canonical
   viewer** (`/dev/canonical`, deep-linked to that group), not the review queue — corrected during
   P1 (P1-016). A group with its own entity page already exists and is settled, so "the review
   queue," which holds only unreviewed candidate pairs, was never the right destination; the
   redirect target was already correct, only the link's own label text said otherwise, and has
   been fixed to match. The page itself is **read-only**: no pin, no merge, no detach.

### 3.3 Read paths this needs

Added to `canonical.py`, alongside the existing ones:

- **`subtree(conn, tier, group_id)`** — the existing `nested_tree` generalized to start at any
  tier. `nested_tree(conn, song_id)` becomes a thin wrapper for `subtree(conn, "song", song_id)`.
- **`group_tree(conn, tier, group_id)`** — the display-enriched form, generalizing `song_tree`.
  **`song_tree(conn, song_id)` stays as a wrapper** so `/dev/canonical` is untouched by this
  step.

Added to `generations.py`:

- **`presence_for_tracks(conn, track_ids)`** — the sorted ordinals those tracks were present in,
  from `generation_presence`.
- **`runs(ordinals)`** — the existing `_runs` made public. Same function, same behaviour; the
  group page needs the run collapsing that `tenures()` does internally.

Both group and track pages call `canonical.ensure_track_groups(conn)` and commit first, exactly
as the canonical and generations pages already do.

---

## 4. The track page

`templates/entity_track.html`. Replaces `snapshot_track.html`.

- **Header**: name, artists (linked), album (linked, with cover), duration, explicit flag.
- **Spotify identity**: `track_id`, `uri`, `isrc`, `track_number`, `disc_number`, `is_playable`,
  `linked_from_id`, `external_url` as an outbound Spotify link. **`linked_from` (the full uri) is
  deliberately not shown** — it says nothing the id doesn't, and an id on its own is all this row
  is ever read for.
- **Canonical**: the four groups, each linked to its group page. This replaces the existing
  Canonical table, whose sibling lists become unnecessary — the group page shows them properly.
- **Memberships**: playlist (→ `/playlist/<id>`), `added_at`, `removed_at`, `position`. As the
  current page, minus the re-derived display.
- **Plays**: total / 30d / 7d for this track alone.
- **Relink aliases**: any `track_uri_alias` rows pointing at this track — the uris Spotify
  substituted onto it during the round-trip. 34 rows exist and are currently invisible
  everywhere.

---

## 5. The album page

`templates/entity_album.html`.

### 5.1 Header

Cover, name, album artists (linked, from `resolved_album_artist`), `album_type`, `release_date`,
`total_tracks`, and **"14 of 19 known"**. Outbound Spotify link.

**Corrected during P1 (P1-016):** the artist list is actually built by an inline query in the
album page's own read path — `app.py`'s `album_page` when this was written, `entities.album_detail`
since P3 extracted it (`docs/codebase-health/P3_refactor.md` §4.1) — not a direct read of
`resolved_album_artist`, because that view carries no
`position`, which this listing needs to preserve credit order. The inline query now `GROUP BY`s
the resolved artist id (same reasoning as `track_artist_credit`'s existing `GROUP BY`), which it
originally didn't — an album credited under both an alias id and its already-canonical id would
have rendered that artist's name twice.

### 5.2 Tracklist

Rendered in `(disc_number, track_number)` order from the cached tracklist:

- **Owned** tracks (a `track` row exists for the id) link to `/version/<id>`, resolved via
  `track_group`.
- **Unowned** tracks render from the simplified object — name, artists, duration, explicit —
  greyed and unlinked. That is everything an album row displays anyway.
- If the album has never been fetched, the page shows only the owned tracks and says so.
- **Corrected during P1 (P1-016):** the note is keyed on how many tracks were actually stored in
  `tracklist_json`, not on `total_tracks > 50` — those diverge once the album backfill job
  (`grouping-fixes-backfill-M.md` §4.5) has paged past 50 into the same `tracklist_json` this page
  reads. The original wording fired the note on `total_tracks > 50` unconditionally, so a
  fully-backfilled album (all N tracks stored) still claimed "first 50 of N" directly above a
  table that in fact showed all of them. Now: render whatever `tracklist_json` holds, and only
  when that count is *less than* `total_tracks` note "first `<count>` of `<total_tracks>`;
  Spotify pages beyond this and we don't follow them" (with `<count>` being the true number
  fetched — 50 on a page-load-only fetch, everything on a backfilled one).
- **Any owned track the fetched page didn't contain is appended after it**, in the same
  `(disc_number, track_number)` order, under a divider saying so. Without this an album whose one
  known track sits at position 312 renders 50 rows that are all unowned and never shows the track
  Symr actually has — the page would contradict its own "1 of 460 known" header. Costs no
  request: those rows come from the `track` table.

Below it: plays (total / 30d / 7d) over the owned tracks, and the playlists they appear in.

### 5.3 The fetch, and why unowned tracks are not `track` rows

On first view — `tracklist_pulled_at IS NULL` — spend one request on `GET /v1/albums/{id}`,
store the response's `tracks.items` into `album.tracklist_json` and stamp
`album.tracklist_pulled_at`. Never re-fetched automatically.

**Fixed during P1 (P1-016): a failed attempt now also stamps `tracklist_pulled_at`.** The
original behaviour only stamped it on success, so a transient failure (429, network) retried on
*every* subsequent page view forever rather than the intended "first view only, cached forever" —
`tracklist_json` stays `NULL` on a failed attempt, `fetched` in the route stays `False`, and the
page still renders from what Symr already owns; it simply stops re-spending a request on every
later visit. (No client at all — not logged in — still doesn't stamp anything; that's not a real
attempt.) The artist-image fetch (§7.1) gets the identical fix, for the identical reason.

**Fetching and queuing are two separate functions, not one combined step**
(`grouping-fixes-backfill-M.md` §4.4, noted here since this section originally described them as
one): `entities.fetch_album_tracklist` is the guarded, at-most-once-per-album Spotify request
above; `entities.queue_wanted_uris(conn, album_id, source)` is a separate, zero-Spotify-cost
function that reads the *stored* `tracklist_json` and runs on **every** view of the page, not just
the first — which is what makes clearing the round-trip queue (§6) a real, working undo rather
than a one-shot action: a cleared uri comes right back the next time this page is loaded.

**Not into `album.raw_json`**: the snapshot pull overwrites that with the *simplified* album
object embedded in every track, so a richer value there would be destroyed on the next pull.

**Unowned tracklist entries never become `track` rows.** They carry no ISRC and no album, so
storing them would create a new class of partial track row and break step A's invariant that
"the track object is the complete and final universe of Spotify metadata — capture it whole."
Instead their uris are **queued** (§6), so the next round-trip resolves them properly through
the shared ingest path and the album fills in by itself on a later visit.

---

## 6. The wanted-uri queue

One new table, merged into the round-trip's existing work list — **not** a second queue, a
second button or a second run.

```sql
CREATE TABLE IF NOT EXISTS wanted_uri (
    uri          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,   -- 'album' for now; the column exists so a later
                                  -- source is additive rather than a migration
    requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**A later source did arrive** (noted here during P1, P1-016): the table gained a fourth column,
`album_id`, and a second live `source` value, `'backfill'`, both from
`grouping-fixes-backfill-M.md` §4 — `'album'` is queued by browsing an album page (this section),
`'backfill'` by the backfill job. `album_id` is what lets the backfill job's settled/missing
arithmetic stay plain SQL instead of a per-page scan of every `tracklist_json`. Whichever route
queues a uri first owns its `source` (`INSERT OR IGNORE`).

- The album fetch inserts `INSERT OR IGNORE` for every unowned track uri it saw.
- `roundtrip._WORK_LIST_SQL` gains a `UNION` arm selecting `wanted_uri.uri` for uris that don't
  resolve through `played_uri_track` and aren't in `roundtrip_failed_uri`, ordered after the
  play-derived ones (which are ranked by play count — a wanted uri has no plays by definition).
- **D's "done is derived" property survives**: a wanted uri is done when it resolves through
  `played_uri_track`, exactly like a played one. Nothing is checkpointed and nothing goes stale.
- `roundtrip.counts()` reports the wanted arm's contribution so `/dev/roundtrip` shows what it
  is about to do.

Nothing else about the round-trip changes: same guard, same batching, same breaker, same page.

---

## 7. The artist page

`templates/entity_artist.html`.

- **Alias redirect first.** If the requested id has an `artist_alias` row, redirect to the
  canonical id. Every read below resolves through `resolved_track_artist` /
  `resolved_album_artist`, per the standing rule that anything artist-level resolves aliases.
- **Header**: image (§7.1), name, outbound Spotify link, **version** and album counts. Counting
  versions, not `track` rows, is what makes the number agree with the list under it — the track
  lists below are deduped to one row per version group, so a track count reads as wrong the
  moment an artist has the same song on two releases.
- **Tracks**: split **primary** vs **featured** using `track_artist_role`, which gives the split
  structurally. Each links to `/version/<id>`, deduped to one row per version group.
- **Albums**: albums where they hold an album credit, linked, with release date.
- **Playlists**: distinct playlists their tracks appear in. **Ambiguous on tense** (noted during
  P1, P1-016): this includes a playlist where every one of the artist's tracks has since been
  *removed*, with no visual distinction from one that still carries them — "appear in" reads
  present-tense but the code means "ever appeared in." Not changed; recorded as the actual
  behavior since it wasn't clear which tense was originally intended.
- **Generations**: which generations any of their tracks were present in, as the same 37-cell
  strip.
- **Plays**: total / 30d / 7d across all their tracks.
- **No ordering.** ~~Track and album lists sort by name; nothing is ranked.~~ **Stale, rewritten
  during P1 (P1-016):** step H shipped exactly as this section anticipated, and comprehensively —
  every list on every entity page (this one included) now sorts by materialized score
  (`all_time`) descending, and every header renders `score_display`. Nothing sorts by name
  anymore.

### 7.1 The image fetch

Same pattern as the album: on first view, if `detail_pulled_at IS NULL`, one request to
`GET /v1/artists/{id}`, store the largest `images[].url` into `artist.image_url` and stamp
`artist.detail_pulled_at`. Nothing else on the response is worth storing — `genres`,
`followers` and `popularity` are all absent for this app.

**Two bugs fixed during P1 (P1-016).** "The largest" wasn't actually computed — the code took
`images[0]`, trusting Spotify's undocumented (and unguaranteed) response ordering rather than
comparing `width`. Now picks by `max(width)` explicitly. Separately, a failed attempt didn't stamp
`detail_pulled_at` (only success did), so a transient failure retried on every subsequent page
view forever instead of the intended once — same fix and same reasoning as §5.3's tracklist-fetch
bug.

---

## 8. Play counts

One shared read path, in the new `entities.py`, used by every page above:

**`play_stats(conn, track_ids)`** → `{"total": n, "month": n, "week": n, "data_through": ts}`.

- Resolves through the **`played_uri_track`** view, never `track.uri` directly, so relinked uris
  count. This is the standing rule from step C/D.
- Windows are past 7 and past 30 days relative to now.
- `data_through` is `MAX(play.ts)` across the whole `play` table — currently 2026-08-06.
- **When `data_through` is older than a window's start, that window renders `—`, not `0`**, so a
  stale export is visibly different from a genuine zero. This is a property of the data, not of
  the entity: it applies to every page at once, and per-entity zeros still render as `0`.
- Every page showing play numbers also shows the `data_through` date next to them.

A `play_stats` macro in `_macros.html` renders the three figures plus the date identically
everywhere.

> **Note for H / F/G.** This is a deliberately simple per-entity read. When scoring or the
> analytics reports land they will want bulk play aggregation, and should fold `play_stats` into
> whatever read path they build rather than leaving two. Recorded in the roadmap (§15).

---

## 9. The playlist page

`templates/entity_playlist.html`. Replaces `snapshot_playlist.html`.

- **Header**: cover image, name, description, owner, track count, `last_changed_at`,
  `tracks_pulled_at`, `unfollowed_at` when set.
- **Exclude toggle** — carried over unchanged from the current page, including its `snapshot.js`
  handler.
- **Totals**: summed runtime of live tracks, and the `added_at` range (first → last).
- **Plays**: total / 30d / 7d over its live tracks.
- **Track table**: track (→ `/version/<id>`), artists, album (→ `/album/<id>`), `added_at`,
  `removed_at`, `position`. Removed rows keep the existing `removed` class. Sortable, as now.
- **Generation banner**: when the playlist has a `generation` row — "Generation 31" plus its span
  — and a link to the generation view.
  **Stale (P1-016):** the banner itself renders only the ordinal and the link
  (`templates/entity_playlist.html:25-30`); the span only appears one click deeper, inside the
  generation view (§9.1) itself.

### 9.1 The generation view — `?generation=1`

Only rendered when the playlist is a generation; ignored otherwise. This is what step K absorbs
from B, instead of a second page to maintain.

- Generation ordinal, playlist name, span (`started_at` → `ended_at`, or "ongoing").
- The generation's groups split into **carried forward** (present in the previous generation)
  and **new in this one**, each listed, not just counted.
- How many survived into the next generation.
- The `?tier=version|song` toggle, matching `/dev/generations`.

Everything comes from `generations.generations()` and `generation_presence`; no new derivation.
**What left since the previous generation is deliberately not shown** — it's the arithmetic
complement of what the previous generation's own page already lists.

---

## 10. Search

`templates/search.html`, plus a plain `GET` form in `base.html`'s navbar (`/search`, one `q`
input, no JS, no dropdown).

`/search?q=` renders four groups, each `LIKE '%q%'`, each capped at 50, each ordered ~~by
name~~ **by materialized score descending — stale, corrected during P1 (P1-016)**, same
site-wide change §7's "No ordering" bullet already flags. Groups:

| Group | Matches | Links to |
|---|---|---|
| Songs | `track.name`, or any credited artist's name | `/version/<id>`, deduped by version group |
| Albums | `album.name` | `/album/<id>` |
| Artists | `artist.name`, alias-resolved | `/artist/<id>` |
| Playlists | `snapshot.name` | `/playlist/<id>` |

An empty `q` renders the page with no results. ~~No ranking~~, no fuzzy matching, no typo
tolerance — substring only. **Step L** is the follow-up that makes search good; this is
deliberately the plain version.

**`q`'s `LIKE` wildcards are unescaped** (noted during P1, P1-016) — a literal `%` or `_` in the
query behaves as a SQL wildcard rather than a literal character. Deliberate and already documented
on `/dev/canonical`'s equivalent search (`CLAUDE.md`'s `_LISTING_CAP` note: "searching `%` still
gets the whole thing"), just never stated here.

---

## 11. Templates, JS, CSS

New templates: `entity_group.html`, `entity_track.html`, `entity_album.html`,
`entity_artist.html`, `entity_playlist.html`, `search.html`.

`_macros.html` gains:

- `play_stats(stats)` — the three figures plus the data-through date.
- `entity_link(kind, id, text)` — so a link to a version/track/album/artist/playlist is written
  one way everywhere and the back-pass can't drift.
  **Signature grew two more optional params** (noted during P1, P1-016; both added by
  `grouping-fixes-backfill-M.md`): `params` (splatted into `url_for`, e.g. `{'generation': 1}`)
  and `css_class` (an empty value emits no attribute at all, so every existing call site is
  unaffected). Functionally harmless, just undocumented here.
- `generation_strip(ordinals, spans)` — the 37-cell strip, **extracted from
  `generations_tenure.html`** so the tenure table and the group/artist pages share one
  implementation rather than growing two.

`base.html` gains the navbar search form. **No new navbar links** — entity pages are reached by
link and by search, not browsed to.

**No new JS file.** The only interactive control on any of these pages is the exclude toggle,
which `snapshot.js` already handles; the playlist page keeps including it. Everything else is a
link or a plain form.

`static/css/style.css` gains styling for the unowned-tracklist row (greyed), the breadcrumb, the
entity header block and the navbar search box. Function over form: reuse `panel`, `data-table`,
`meta`, `empty` and `removed` as they stand. `docs/style_guide.md` still doesn't exist, so
nothing here is a design system.

---

## 12. Removals and the back-pass

**The back-pass is the point of this step, not a follow-up to it.** A viewing page nothing links
to changes nothing.

### 12.1 Deleted

| Gone | Replaced by |
|---|---|
| `/dev/snapshot/track/<id>` + `snapshot_track.html` | `/track/<track_id>` |
| `/dev/snapshot/playlist/<id>` + `snapshot_playlist.html` | `/playlist/<playlist_id>` |
| `/dev/generations/<ordinal>` (the `coming_soon` stub) | `/playlist/<playlist_id>?generation=1` |

`coming_soon.html` itself stays — `/audit`, `/covers`, `/folders` and `/analytics` still use it.

### 12.2 Relinked

Every one of these currently renders a bare name, or links to a route being deleted:

- **`/dev/snapshot`** — playlist list, track search results, and the recent-changes table
  (playlist and track columns).
- **`/dev/canonical`** — group headers link out to their new group pages; representative and
  member track names to `/track/<id>`; artist names to `/artist/<id>`. The existing
  `/dev/canonical/group/<id>` deep link stays, as the *edit* direction from a group page.
- **`/dev/artists`** — artist names on both the candidate-pair and merged-group lists.
- **`/dev/generations`** — playlist name → `/playlist/<id>`; the per-generation link →
  `/playlist/<id>?generation=1`.
- **`/dev/generations/tenure`** — the representative track → its group page at the current tier.
- **`/`** (home) — add a line pointing at search, since it's now the way into everything.

The **org canvas is deliberately out of scope**; that page is due its own rework.

### 12.3 Deliberately not relinked

Three surfaces keep their bare names, decided during verification:

- **`/dev/canonical/review` and `/dev/canonical/cross`.** These are keyboard-driven queues whose
  whole job is triaging the rows in front of you. A link in every title is a tab-stop and a
  mis-click away from losing the queue position, and the queue already offers no reason to leave
  it.
- **`/dev/roundtrip`'s manual-alias table.** Its candidates live in `<option>` elements, which
  cannot hold a link at all; the "Played as" column is a uri with no `track` row *by definition*,
  which is the entire reason the row is in that table. The Spotify link it already carries is the
  only useful target.

Verification for this step is not complete until every bare entity name in §12.2 is a link.

---

## 13. Schema changes

Additive only, via `db.py`'s `_migrate`:

```sql
ALTER TABLE album  ADD COLUMN tracklist_json     TEXT;
ALTER TABLE album  ADD COLUMN tracklist_pulled_at TEXT;
ALTER TABLE artist ADD COLUMN image_url          TEXT;
ALTER TABLE artist ADD COLUMN detail_pulled_at   TEXT;
```

plus the `wanted_uri` table in §6. No view changes, no data migration, nothing dropped.

New module **`entities.py`** — the read paths these pages need that don't belong to an existing
owner: `play_stats`, the playlist/album/artist rollups over a track set, and the two guarded
Spotify detail fetches (album tracklist, artist image), which catch everything and return `None`
rather than raising. Read-only w.r.t. the Spotify library, like everything except `roundtrip.py`.

**Narrower than planned (noted during P1, P1-016):** only `play_stats` and `playlists_for_tracks`
actually ended up here. The album/artist rollups this paragraph describes as belonging to
`entities.py` are inline in `app.py`'s own route bodies instead (`album_page`, the artist page
view) — never moved out. Not itself flagged as a problem to fix; `CLAUDE.md`'s `entities.py` map
entry already describes the narrower, actual scope, and P3's query-extraction step
(`codebase-health-P.md` §5) is the natural place to revisit where these rollups live, not this
audit.

**Settled by P3, 2026-08-22 (`P3_refactor.md` §4.1).** That step did revisit them, and the
original paragraph above is now the accurate one: every entity page's read path lives in
`entities.py`, one function per page (`group_detail` / `track_detail` / `album_detail` /
`artist_detail` / `playlist_detail` / `search`), with the route reduced to its 404 guard, its
`request.args` parsing and one `render_template`. The rollups this paragraph is about are inside
`album_detail` and `artist_detail`.

---

## 14. Corrections to the roadmap

- **"No artist images, genres, followers, or popularity"** is wrong on the first item.
  `GET /v1/artists/{id}` returns images; that claim came from the bulk `/v1/artists?ids=` 403.
  Genres, followers and popularity are correctly gone. `GET /v1/albums/{id}` likewise works and
  returns the tracklist inline. `docs/spotify_constraints.md` is corrected in this step.
- **The library has tripled** since the roadmap's July measurements: 3,611 → 9,930 tracks, with
  63% now having no playlist membership at all. K's pages are read mostly against material that
  arrived via D.
- **There are 37 generations, not 36.** `v37.0.0` was added by B's confirm-on-pull flow, which
  is the first evidence that flow works end to end.
- K's roadmap section says the generation view is a toggle "for current-favs playlists" — kept
  exactly, and `/dev/generations/<ordinal>` is deleted rather than redirected, per Finn.

## 15. Roadmap edits made by this step

Made as a separate commit from the spec:

- **H** gains: entity pages currently apply no ordering to track/album/artist lists, and should
  render the score once one exists.
- **H / F/G** gain: `entities.play_stats` is a per-entity read that should be folded into
  whatever bulk play aggregation they build.
- **New step L — better search**: dropdown, fuzzy matching, ranked results. Slotted at the end
  of the Order diagram.

---

## 16. Out of scope

- **Ordering or ranking of any list.** Step H.
- **Better search.** Step L.
- **The org canvas.** Due its own rework; its cards keep pointing where they point.
- **Any write to the Spotify library.** `roundtrip.py` remains the only module that writes; K
  only ever *queues* uris for it.
- **Editing from entity pages** — no pin, no merge, no detach. Group pages link to
  `/dev/canonical` for that.
- **Storing partial track rows** from album tracklists (§5.3).
- **Following album tracklist pages past the first 50** (§1.3).
- **Genre and artist imagery beyond the Spotify image** — still needs a non-Spotify source, per
  the roadmap's cross-cutting note.
- **`/audit`, `/covers`, `/folders`, `/analytics`** stay stubs.
