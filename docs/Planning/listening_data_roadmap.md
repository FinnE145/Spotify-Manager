# Listening Data — Roadmap

**Status: planning, not a spec.** This is the ordered plan for getting play history into Symr and building analytics on top. Each lettered feature below becomes its own `/symr-plan` session and its own `docs/specs/<feature>.md`. Nothing here is implementation-ready as written.

**Origin.** A July 2026 chat session analysed the Spotify GDPR extended streaming history against `symr.db` and produced a feature inventory (`symr_analysis_features.md`, not committed). This doc supersedes it: every claim has been checked against the real codebase, the real DB, and the live API, and the contradictions resolved. Where the two disagree, **this doc is right**.

---

## Verified facts

Measured in July 2026, **re-measured 2026-08-03** during C's planning session (after A and I landed, and against the real export rather than the earlier analysis). Don't re-derive it; don't trust a number that contradicts it without re-measuring.

### DB / library state

Re-measured 2026-08-03.

| | |
|---|---|
| Playlists | 151 (143 pullable, 7 excluded — permanently 403 on item reads) |
| Tracks | 3,611 |
| Memberships | 12,513 (5 with `removed_at`) |
| Tracks missing ISRC | **0** |
| Memberships missing `added_at` | **0** |
| Last full pull | 2026-08-03 |

**Canonical grouping is complete for the current library.** 288 candidate groups have been produced and *all* reviewed (`candidate_groups()` and `cross_artist_groups()` both return 0). That yielded 106 multi-track version groups covering 221 tracks, and 461 reviewed pairs. The low coverage is not an unworked queue — it's the true answer for the tracks Symr can currently see.

**A full snapshot pull cost 232 requests** as measured in July: 3 for the playlist list (150/50), 222 for playlist items (`ceil(track_count/100)` summed over 143 playlists), 7 wasted on the playlists that 403. A's exclude flag has since landed and removes the wasted 7. Not re-measured in August — the item-page count drifts with the library.

### Streaming history export

**Thirteen** JSON files, 69 MB, **2020-02-12 → 2026-06-30** — nine `Streaming_History_Audio_*.json` **and four `Streaming_History_Video_*.json`**, the latter missed by the original count. The 23 keys are identical in every file, audio and video alike, across all seven years.

Re-measured 2026-08-03 over all 13 files, against the 3,611-track DB.

| | |
|---|---|
| Rows, all files | 90,662 |
| Rows with a track URI | 90,351 (89,858 audio + **493 video**) |
| Rows discarded | 311 — 310 podcast episodes, 1 with neither track nor episode URI |
| Rows stored after dedup | 90,338 (13 byte-identical duplicates collapsed) |
| Distinct track URIs | 8,908 — **all** `spotify:track:`, no local files |
| In library | 2,820 |
| Foreign (never in any playlist) | 6,088 |
| Plays on in-library URIs | 76,399 (**84.6%**) |
| Library tracks never played | 791 |
| Avg play-row JSON | 715 bytes |

**The video files hold real track plays, not podcasts.** 493 of their 501 rows carry a `spotify_track_uri` (music videos — Spotify autoplays video versions on mobile), 7 are podcast episodes, 1 is neither. **None of the 493 appear in the audio files**, so ignoring those files loses real plays. They contribute 128 URIs the audio files never mention.

**`offline_timestamp` has mixed units in the same column** — 73,656 values seconds-scale, 852 milliseconds-scale. Anything treating it as one unit is wrong by 1000× on part of the data.

**New foreign URIs arrive slowly** — this is what makes the round-trip cheap to keep:

| Window | New foreign URIs never seen before | Add-requests |
|---|---:|---:|
| 1 month | 91 | 1 |
| 3 months | 254 | 3 |
| 6 months | 566 | 6 |
| 12 months | 1,243 | 13 |

**Foreign URIs are mostly a genuine long tail, not rare editions of known songs.** Normalized title+artist matching against the library. Both this table and the arrival table above are the **July 2026 audio-only** measurements, not re-run in August — they exclude the 127 video-only foreign URIs, which is under 2% and moves no conclusion here.

| Foreign URI plays | URIs | Name-match a library song |
|---|---:|---:|
| exactly 1 | 3,871 | 282 (7.3%) |
| 2 | 949 | 106 (11.2%) |
| 3–4 | 640 | 111 (17.3%) |
| 5–9 | 344 | 104 (30.2%) |
| 10+ | 162 | 113 (**69.8%**) |
| **total** | **5,966** | **716 (12.0%)** — 31.1% of foreign plays |

So high-play foreign URIs really are alternate ids of owned songs; the 1-play tail really is radio and autoplay. Both get imported anyway (feature D), because the cost difference is 6 requests.

**Connection metadata is richer than expected.** `conn_country` alone supports location features with no IP geolocation:

| CA | US | CR | BS | IT | GT | MX |
|---:|---:|---:|---:|---:|---:|---:|
| 88,524 | 807 | 337 | 268 | 194 | 26 | 5 |

Also present: `incognito_mode` (7,801 plays true), `offline` (4,407), `offline_timestamp`, `platform` (messy — `ios`, `windows`, `not_applicable`, plus full OS strings), `shuffle`, `skipped`, `reason_start`, `reason_end`, `ip_addr` (2,133 distinct).

**Re-requesting the export:** no documented frequency cap; up to 30 days quoted, usually 5–14 in practice, one open request at a time. Community-sourced, not official.

### API capability — what Symr can and cannot ever have

Probed directly (see `docs/spotify_constraints.md` for the full record):

| Endpoint | Result |
|---|---|
| `GET /v1/tracks/{id}`, `GET /v1/playlists/{id}/items` | **work** |
| `GET /v1/tracks?ids=`, `/v1/artists?ids=`, `/v1/albums?ids=`, `/v1/audio-features`, `/v1/artists/{id}/related-artists` | **403** |

Consequences that shape everything below:

- **The track object is the complete and final universe of Spotify metadata.** There is no follow-up pull to plan for. Capture it whole.
- No artist images, genres, followers, or popularity. **Monthly listeners is not in the Web API at all**, quota irrelevant.
- No album label, genres, or copyrights.
- **No audio features** — tempo, key, energy, valence, danceability are all gone (2024-11-27 deprecation).
- `popularity` and `preview_url` are dead (0/3,589 populated; `preview_url` isn't even a key in the response). **No audio access of any kind**, so no locally-computed features and no audio-trained classifier.

Track object is ~1,756 bytes and carries, beyond what `track` stores today: **artist ids** (on both `artists` and `album.artists`), `release_date` + precision, `album_type`, `track_number`, `disc_number`, `total_tracks`, `linked_from`, `is_playable`.

---

## Order

```
A (capture) ──► I (detection on the artist model) ──► C (ingest) ──► D (round-trip)
  ──► E (grouping catch-up) ──► B (generations) ──► H (scoring) ──► F/G
```

B is deliberately late **not** because it's low value — it's the cheapest high-value slice — but because it needs **zero Spotify requests**. It's the work to pick up on a day the API budget is already spent. **I** has the same property.

---

## A — Track metadata capture + re-pull

**Specced → `docs/specs/track-metadata-A.md`.** That spec is authoritative; the summary below is the shape, not the detail.

Read-only. Prerequisite for everything. The one pass that has to be right, because there is no second source.

- **Store the raw track JSON** alongside parsed columns (~17 MB at full scale). This permanently retires "we'll need another pull for a field we didn't think of."
- Parse artist ids into a proper `artist` table, plus **`track_artist` and `album_artist` join tables**. Planning settled on a real `album` table rather than a credit flag on one join: album credits then live once per album, and a featured artist is "in `track_artist`, not in `album_artist`" — structural, not string-matched.
- Album-level fields (`release_date` + precision, `album_type`, `total_tracks`) land on `album`; `track_number`, `disc_number`, `linked_from`, `is_playable`, `uri` stay on `track`.
- **Playlist exclude flag** + snapshot-UI toggle, with a bulk "exclude what just failed" button in the post-pull error list. Exclusion means "don't re-read", never "forget existing rows". The same flag later covers the round-trip's temp playlist.
- Drop `popularity` / `preview_url` outright, and `album_name` / `album_image_url` in favour of an `album` join.
- Then **one full re-pull, ~225 requests.**

**Size:** contained. `_parse_track_item` and `_upsert_track` in `snapshot.py` gain fields; `db.py` gains four tables and a one-time `track` rebuild; the exclude flag is one column plus an endpoint and a bit of UI. Roughly one implement session, and the pull itself runs in minutes.

**Measured:** only **1** track in the library is unreachable by a full pull, so no mop-up pass is needed after it.

**Trap:** never edit a `.py` file while a pull is running — the Flask reloader truncates it silently.

## I — Detection on the artist model

**Specced → `docs/specs/detection-artist-model.md`.** That spec is authoritative; the summary below is the shape, not the detail.

Rework `canonical_detect.py` to match on the **artist ids** A captures (`track_artist` / `album_artist`) instead of the comma-joined `track.artists` string. Needs **no play history and effectively no Spotify requests** — like B, it's work for a day the API budget is already spent.

**Why it sits here and not later.** Right now there is a fully-reviewed grouping baseline: 288 candidate groups all decided, yielding 106 version groups over 221 tracks and 461 reviewed pairs, across 3,589 tracks. That baseline is what makes the rework *checkable* — the new detection output can be diffed against known-good and the difference attributed. After D the library roughly triples with a foreign set skewed toward alternate editions, and there is no baseline left to diff against.

Also unlocks the featured-artist question structurally: an artist credited on the track but not on its album is a feature, readable as `track_artist` minus `album_artist` rather than string-matching "feat." out of a title.

**Scope.** Bigger than "swap the match key" — matching on ids drags in three things that turn out to be prerequisites, not extras:

- **An artist identity model.** Spotify issues more than one id for the same artist, so an id match splits pairs the name match correctly merged. Needs a sparse `artist_alias` table, a `reviewed_artist_pair` companion, and a small curation page to decide them. Without it the rework is a regression. Everything artist-level downstream — H's rollups above all — must resolve through it rather than grouping on the raw id.
- **`track.artists` becomes write-only.** Once matching is on ids, the comma-joined string is the only thing still splitting `Tyler, The Creator` into two artists, so display and search have to move onto the join too. That means a rendered-artist read path in SQL (primaries, then a `feat.` clause), which every later page inherits instead of re-deriving.
- **A metadata backfill.** A track that leaves every playlist between pulls freezes at whatever the last pull captured, because pulls only ever see tracks currently in a playlist — so it never gains its artist credits. One track today, but structural, and D's round-trip will make it routine. Needs a per-track re-fetch action; folding a live request counter into the snapshot UI while there is cheap.

Deciding what to do with the resulting diff over the existing 288 groups stays a separate call, made once the diff is visible.

**Expect the cost to land in the read path, not the rework.** Resolving aliases and deriving primary/featured per track is several joins deep over every credit; done naively it is slow enough to be unusable on the group-heavy pages, and the shape that fixes it is not the obvious one. Budget time for measuring rather than assuming.

> Not to be confused with the mechanical album-column swap inside A: A drops `track.album_name` / `track.album_image_url` in favour of an `album` join, which changes no detection logic. I is the part that changes what detection matches on.

## C — Play history ingestion

**Specced → `docs/specs/play-history-C.md`.** That spec is authoritative; the summary below is the shape, not the detail. Planning measured the real export and **corrected three claims made here**: `(ts, spotify_track_uri)` is not unique (228 duplicated keys, all within a single file — it would drop 255 real plays, so dedup is on a row hash instead); the export also ships `Streaming_History_Video_*.json` holding 493 track plays that appear nowhere in the audio files; and the discarded non-track rows are 311, not 303.

- `play` table, `source` column (`export` | `listenbrainz`), unique `row_hash`, `INSERT OR IGNORE`.
- **No incremental logic.** Offline plays are backdated to when they happened, so a high-water-mark import silently drops rows. Narrowed in the spec: an import re-reads the newest upload folder whole, which is equivalent given exports are cumulative and `play` rows are never deleted.
- **Keep every uploaded export on disk**, one folder per upload — the export's file chunking is not stable between exports, so a flat folder can't say which files belonged to which upload. The files are the durable store; re-parsing costs zero API calls, so no parse decision is ever irreversible.
- **Parse generously**: every field the export offers, including `ip_addr` and the connection metadata. The exception is the seven `audiobook_*` / `episode_*` keys, permanently NULL once the non-track rows are filtered out.
- Filter the 311 non-track rows. Store `platform` raw, normalize at query time; normalize `offline_timestamp`'s mixed units **at import**.
- Upload UI from the start (file upload + progress), not a script — the backend is permanent either way and the UI is minutes of work.
- **Foreign URIs are not flagged** — "foreign" is the absence of a matching `track` row, resolved by a query-time join on `track.uri`. A stored flag would go stale the moment D lands. In-library plays (84.6%) become queryable immediately.

**ListenBrainz, later.** Not an alternative to the export — the export is the only source of 2020–2026 back history. LB becomes a second writer into the same table. Its listens carry **`spotify_id`** as a first-class field, so Spotify-sourced scrobbles land on the same `track_id` with no MBID resolution needed; the recording MBID is a bonus on top. Precedence rule: on export import, delete `listenbrainz` rows inside the export's covered range, then insert. Export wins where it overlaps.

**Caveat to carry:** LB only submits a listen after half the track or 4 minutes. The export logs every play including 3-second skips. Skip-rate and `ms_played` metrics will discontinue at the boundary — restrict them to export-covered ranges or flag it.

## D — Foreign-track round-trip

The project's **first write to the Spotify library**. Turns the 6,088 foreign URIs into real `track` rows with full metadata for ~122 requests instead of 6,088.

- Needs `playlist-modify-private`; delete `.spotipy_cache` and re-auth.
- **Finn creates the temp playlist manually** and sets its exclude flag. Code only looks up its id (stored in `meta`) — no create/delete logic.
- Add 100 URIs/request, read back 100/page, clear afterwards so it never nears the 10,000-item cap.
- **Map on the requested URI, not the returned id.** Relinking can return a different id and can collapse two URIs onto one.
- Run on a **different day** from A's re-pull.

**Relinking** — Spotify serves market-specific catalogs, so one recording can exist under different ids per market. Request an unavailable id and Spotify substitutes the equivalent, returning the playable id with `linked_from` holding what was asked for. Unhandled this corrupts attribution silently. Handled, it's a **gift**: a relink is Spotify authoritatively stating two ids are the same recording — a higher-confidence release-tier grouping signal than any heuristic in `detection.md`, and exactly what that tier exists to absorb.

**Dead URIs** — a 20-URI sample resolved **20/20**, no failures, no unplayable. So this should be rare. When a batch does 400: bisect once or twice via the API to narrow, then check candidates against `https://open.spotify.com/track/<id>` — the **public web page, which is not the Web API and costs no quota**. Worst case if bisecting all the way via API is ~400 extra requests; the web-check path makes that moot.

## E — Grouping catch-up

Detection reruns over ~9,699 tracks instead of 3,611. **Volume is genuinely unknown** — currently 288 groups over the whole library, but the foreign set skews toward alternate editions. Deliberately unplanned until it can be measured after D. If it's a few hundred, the existing Enter-key workflow holds and nothing needs building.

## B — Generation engine

Needs **no play history and no Spotify requests**.

- `generation` table + curation UI. Membership is **manually listed once** (~36 entries), with an automatic rule for new `vXX.Y.Z` playlists going forward.
- Naming cannot be pattern-matched: `favourties 5` is a real generation with a typo, and `music im sick of` / `(no longer) current music` are real generations that were **renamed after the fact** when the next one was created. Posthumous name ≠ not a generation.
- Active spans from earliest `added_at`. Verified: this ordering produces a clean chronological chain with no ties or inversions, from `Songs I Wanna Listen To Rn` (2021-02-09) through `v36.4.1` (2026-07-20).
- Yields: track tenure, right-censoring flag, **intent score**, adoption stagger.

## H — Scoring

A general song ranking that feeds album and artist rankings by aggregation. Motivation: play count over-rewards pleasant background music, tenure under-rewards recent arrivals, and neither handles recency.

- **Two horizons — old and new.** Internally likely one model with a recency-weight parameter, but presented and used as two distinct scores (library-wide retrospective vs. current recommendation context), with the knowledge that the line can move.
- Short horizon leans on recency-weighted plays and current-version membership; long horizon on tenure, comeback behaviour, and post-year-one share.
- **Calibrate against ATG** — it's the only real ground truth in the library.

> ⚠️ **ATG must be corrected before it is used as ground truth.** It currently has missing essentials and a fair amount of over-adding. The convention is right; the contents aren't. Any scoring or validation work waits on that cleanup.

## F / G — metrics, reports, visualisations

Deferred. The full inventory is carried in `feature_ideas.md` under *Listening analytics*; revisit which items are actually worth building once A–D have landed and the data is real. G additionally waits on the Power BI prototype step and the still-open charting-library choice.

---

## Cross-cutting notes

- **Statistics from the source inventory are one-run findings, not features.** Its archetype shares, 9-week half-life, and quartile tables came from a single analysis pass, and its own record shows a 3-month comeback threshold classified 58% of the library as comebacks before being tightened to 6 months. Every threshold belongs in config; every published statistic must be recomputed, never hardcoded.
- **Grouping stays a query-time lens.** `docs/specs/canonical-tracks.md` guarantees nothing mutates `track` or `membership`. That makes two things the source inventory flagged as easy-to-get-wrong — "merges must keep the earliest `added_at`", "generation membership must be unioned" — structurally impossible to get wrong here; they fall out of `MIN(added_at) … GROUP BY version_id`.
- **Right-censoring must be explicit.** Anything first seen in the last two generations hasn't had a chance to survive yet. The inventory measured ATG tracks peaking at a mean of week 72 versus week 17 for non-ATG, so material under ~18 months old is genuinely unevaluable — worth surfacing in the UI, since the instinct is to judge far sooner.
- **The snapshot holds one pull's worth of state.** `membership.removed_at` has 4 rows. Frozen old generations are unaffected, but patch removals from the active version before the first pull are invisible. "Dropped from current-favs" is only observable going forward.
- **Genre and artist imagery are deferred, not impossible.** Both need a non-Spotify source. ISRC is populated for 100% of library tracks and MusicBrainz supports ISRC lookup, so ISRC → recording MBID → MusicBrainz/Last.fm tags (genre) or Wikidata → Wikimedia Commons (artist images) is the route when it's wanted.
