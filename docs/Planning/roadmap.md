# Symr — Roadmap

**Status: planning, not a spec.** This is the standing, ordered plan of what gets built next. Each lettered step becomes its own `/symr-plan` session and its own `docs/specs/<feature>.md`. Nothing here is implementation-ready as written.

**How this doc works.** It's append-only in spirit: finished steps stay, marked ✅ DONE and pointing at the spec that is authoritative for what actually shipped. New work is added as a new lettered step (continue the letters — they're labels, not an order; the order is the diagram below). Anything measured goes under *Verified facts* with the date it was measured, so a later session can trust it or re-measure it deliberately.

**Origin.** It started as the *listening data* roadmap — steps A–J below are that plan, drawn from a July 2026 chat session that analysed the Spotify GDPR extended streaming history against `symr.db` and produced a feature inventory (`symr_analysis_features.md`, not committed). This doc supersedes that inventory: every claim was checked against the real codebase, the real DB, and the live API, and the contradictions resolved. Where the two disagree, **this doc is right**. It is no longer scoped to listening data — later steps can be anything.

---

## Spec index

What each of the 17 audited specs in `docs/specs/` actually covers, and the code it's
authoritative for — built during P1 (`docs/codebase-health/P1_spec_audit.md`) after tracing a
cross-module question (which spec introduced `jobs.py`'s single-lock design?) through four
files before landing on the answer. This table exists so that question, and ones like it, are
a lookup from now on rather than a re-derivation. `codebase-health-P.md` itself isn't in the
17 — it's the standing approach doc for this step, not an audited spec (see its own §0).

Four predate the lettered steps entirely (Symr's original build-out, before this roadmap
existed); the rest map onto the lettered order above. **P1 audited** tracks
`docs/codebase-health/P1_findings.md` — update as each spec picks up its "Audited" header line.

| spec | scope | primary code | step | P1 audited |
|---|---|---|---|---|
| `snapshot.md` | Original playlist/track pull design — largely superseded, see P1-003 | (superseded — `track-metadata-A.md`, `partial-pulls-J.md`, `entity-pages-K.md` now own this territory) | pre-lettering | no |
| `org-canvas.md` | The drag-and-drop playlist canvas, Symr's first feature | `grouping.py`, `static/js/canvas.js`, `templates/canvas.html` | pre-lettering | no |
| `site-shell.md` | Shared base template + navbar; turned the single-page canvas into a multi-page site | `templates/base.html`, page routes' shell | pre-lettering | no |
| `error-pages.md` | Centralized HTTP error handling — styled page for browsers, JSON for `/api/*` | `app.py` error handlers, `templates/error.html` | pre-lettering | no |
| `canonical-tracks.md` | The four-tier (release→recording→version→song) canonical grouping engine | `canonical.py`, `db.py` (`canonical_group`/`track_group` schema) | pre-lettering | no |
| `canonical-fixes.md` | Two fixes: review-UI losing finer grouping work; `/dev/canonical` load time | `canonical_review.js`, `app.py` (`/dev/canonical`) | pre-lettering | no |
| `track-metadata-A.md` | Full track/album/artist metadata capture (raw JSON + normalized model); playlist exclude flag | `snapshot.py` (parsing/upsert), `db.py` schema | A | partial — see P1-001, P1-002 |
| `detection-artist-model.md` | Reworks `canonical_detect` onto artist ids instead of the comma-joined name string; artist alias model | `canonical_detect.py`, `artists.py`, `db.py` (`artist_alias`) | I | no |
| `play-history-C.md` | GDPR streaming-history export ingestion into `play`, row-hash dedup | `history_import.py` | C | partial — see P1-006 |
| `foreign-roundtrip-D.md` | Resolves played-but-unknown uris into real `track` rows via the scratch-playlist round-trip; introduces `jobs.py`'s single-lock design (§2) | `roundtrip.py`, `jobs.py` | D | partial — see P1-007 |
| `grouping-catch-up-E.md` | Closed the post-D review backlog (810 main + 541 cross-artist candidates) | `canonical_detect.py`, `canonical_autogroup.py` | E | no |
| `generations-B.md` | The `generation` table, tenure derivation, confirm-on-pull for new versions | `generations.py` | B | no |
| `entity-pages-K.md` | Unified song/version/recording/release/track/album/artist/playlist entity pages | `entities.py`, `app.py` entity routes, `templates/entity_*.html` | K | no |
| `scoring-H.md` | The one materialized score (version tier), query-time aggregation for everything else | `scoring.py` | H | no |
| `partial-pulls-J.md` | Resumable/partial playlist pulls (derived work list, no cursor); the API request log | `snapshot.py` (pull logic), `api_log.py` | J | partial — see P1-004, P1-005 |
| `grouping-fixes-backfill-M.md` | Three review-UI bugs (M1/M1b/M1c) + the album-tracklist backfill job | `canonical.py`, `backfill.py`, `entities.py` | M | no |
| `async-recompute-N.md` | Moves `scoring.recompute()` off the request path for queue-driven writes | `scoring.py` (worker/backstop) | N | no |

---

## Verified facts

Everything in this section was measured for the listening-data steps (A–J). Later steps should add their own subsections rather than assume these still hold.

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

> **Superseded for anything current — re-measured 2026-08-14 during H's planning.** The
> library has grown to **9,949 tracks / 6,214 albums / 4,108 artists / 153 playlists / 37
> generations**, plays run to 2026-08-06 and **100% of them now resolve** to a track (D's
> foreign-uri problem is closed). Only 3,633 tracks have a live membership — 6,297 were
> played and never added. The full current figures live in `docs/specs/scoring-H.md` §2;
> the 2026-08-03 table above is kept as the record of what was true then.

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
- No artist genres, followers, or popularity. **Monthly listeners is not in the Web API at all**, quota irrelevant. ~~No artist images~~ — **corrected by K**: only the *bulk* `/v1/artists?ids=` 403s; `GET /v1/artists/{id}` works and returns images, which the artist page now fetches one-per-first-view. `GET /v1/albums/{id}` likewise works and carries the tracklist inline. See `docs/spotify_constraints.md`.
- No album label or genres. (Copyrights do come back on the singular album endpoint; nothing wants them yet.)
- **No audio features** — tempo, key, energy, valence, danceability are all gone (2024-11-27 deprecation).
- `popularity` and `preview_url` are dead (0/3,589 populated; `preview_url` isn't even a key in the response). **No audio access of any kind**, so no locally-computed features and no audio-trained classifier.

Track object is ~1,756 bytes and carries, beyond what `track` stores today: **artist ids** (on both `artists` and `album.artists`), `release_date` + precision, `album_type`, `track_number`, `disc_number`, `total_tracks`, `linked_from`, `is_playable`.

---

## Order

```
A (capture) ──► I (detection on the artist model) ──► C (ingest) ──► D (round-trip)
   DONE              DONE                              DONE           DONE

  ──► E (grouping catch-up) ──► B (generations) ──► K (entity pages) ──► H (scoring)
      DONE                      DONE                DONE                DONE
  ──► M (grouping fix + album backfill) ──► N (async score recompute) ──► J (partial pulls)
      DONE                                  DONE                          DONE

  ──► P (codebase health — P1 spec audit ▸ P2 tests ▸ P3 refactor)
                            DONE            DONE        NEXT
  ──► O (request budgets) ──► F/G ──► L (better search)
```

**P is three parts on one branch, not a normal step.** It is specced, but its entry point
(`docs/specs/codebase-health-P.md`) is a standing *approach* document rather than a contract —
read its §0 before treating it like any other spec. Each part merges into `main` on its own.

**A, I, C, D, E, B, K, H, M, N and J have landed**, and **P is two parts of three done** (P1 and
P2; P3 is the next thing to plan). Their sections below are marked, and each points
at the spec that is authoritative for what actually shipped — read the spec, not
the summary here, before touching any of them.

**`score` is now available to everything downstream** → `docs/specs/scoring-H.md`. Anything
below that wants a ranking should read it rather than inventing one, and anything that
would *consume* a stored score inherits H §10's discipline: the parameters are module
constants, and changing one means recomputing everything that depends on them.

**M sits after H** because H is correct without it and improves automatically as coverage
grows — M is not a prerequisite for scoring, it just raises the ceiling.

New steps that aren't part of the listening-data chain slot into this order
explicitly when they're added — don't leave them dangling off the end.

**N sat right after M by priority, not dependency.** Finn asked for it next while M was
still in flight; it had no technical dependency on M, J, or anything else here.

**O is gated on data, not code.** It follows J because J ships the `api_request` log, but the
wait is for that log to *catch a real lockout* — until it has, there is no measured ceiling to
budget against and O has nothing to display. Time spent on other steps is not time O is
blocked by; it is time the log is collecting.

B is deliberately late **not** because it's low value — it's the cheapest high-value slice — but because it needs **zero Spotify requests**. It's the work to pick up on a day the API budget is already spent. **I** has the same property.

J sits after H for the same reason in reverse: **everything from E through H needs
zero Spotify requests**, so none of it is blocked by the quota problem J solves. J
becomes urgent when the next full pull is due, not before.

---

## A — Track metadata capture + re-pull ✅ DONE

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

## I — Detection on the artist model ✅ DONE

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

## C — Play history ingestion ✅ DONE

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

## D — Foreign-track round-trip ✅ DONE

**Specced → `docs/specs/foreign-roundtrip-D.md`.** That spec is authoritative; the summary below is the shape, not the detail, and planning corrected several of its numbers (6,085 foreign URIs at 3,620 tracks, 61 batches, ~125 requests including the guard's two reads) and its playlist-id handling (a hardcoded module constant with a `TODO`, not a `meta` lookup — the playlist can't be picked from a list until the next full pull makes it visible in `snapshot`).

The project's **first write to the Spotify library**. Turns the foreign URIs into real `track` rows with full metadata for ~125 requests instead of one per URI.

- Needs `playlist-modify-private`; delete `.spotipy_cache` and re-auth.
- **Finn creates the temp playlist manually** and sets its exclude flag. Code only looks up its id — a hardcoded module constant with a `TODO`, not a `meta` lookup (corrected above; this bullet previously said `meta`).
- Add 100 URIs/request, read back 100/page, clear afterwards so it never nears the 10,000-item cap.
- **Map on the requested URI, not the returned id.** Relinking can return a different id and can collapse two URIs onto one.
- Run on a **different day** from A's re-pull.

**Relinking** — Spotify serves market-specific catalogs, so one recording can exist under different ids per market. Request an unavailable id and Spotify substitutes the equivalent, returning the playable id with `linked_from` holding what was asked for. Unhandled this corrupts attribution silently — that part stands, and it's why the round-trip maps on the requested URI rather than the returned id.

It is **not**, however, a grouping signal, as this plan previously claimed. Checking the mechanics: the requested id comes back only as a stub carrying `id`/`uri`/`type`/`href`/`external_urls` — no name, artists, album or duration — so it can never become a `track` row of its own. There is therefore no *pair* of rows to group and nothing for E to inherit; `track_uri_alias` absorbs the relationship completely. What relinking actually buys is better than a grouping hint: when a foreign URI relinks onto a track already in the library, a "foreign" URI turns out to be an owned track, its plays join straight onto it, and the foreign count shrinks by an amount that can't be predicted in advance.

**Dead URIs** — a 20-URI sample resolved **20/20**, no failures, no unplayable. So this should be rare. When a batch does 400: probe all 100 uris against `https://open.spotify.com/track/<id>` — the **public web page, which is not the Web API and costs no quota** — rather than bisecting via the API, which would spend the very quota the round-trip exists to protect. Drop the confirmed-404s, retry the batch once with the survivors.

## E — Grouping catch-up ✅ DONE

**Specced → `docs/specs/grouping-catch-up-E.md`.** That spec is authoritative.

Measured after D, the volume was not "a few hundred": **810 unreviewed main candidates and 541 cross-artist ones**. So E closed 70% of the main queue deterministically (same ISRC + identical normalized full title + duration within 2s, scored 114/114 against the corrected reviewed-pair baseline), stopped the prefill splitting songs it shouldn't, and rebuilt the cross-artist queue — whose historical merge rate is **0 of 292** — around the one-keypress "none of these are related" answer.

## B — Generations & tenure ✅ DONE

**Specced → `docs/specs/generations-B.md`.** That spec is authoritative; the summary below is the shape, not the detail.

Needs **no play history and no Spotify requests**.

- `generation` table, seeded once by a `scripts/` one-off with the verified list of **exactly 36**, plus a confirm-on-pull rule for new `vXX.Y.Z` playlists going forward.
- Naming cannot be pattern-matched: `favourties 5` is a real generation with a typo, and `music im sick of` / `(no longer) current music` are real generations that were **renamed after the fact** when the next one was created. Posthumous name ≠ not a generation.
- Spans from earliest `added_at`, ending when the next generation starts. Verified: a clean chronological chain, no ties or inversions, `Songs I Wanna Listen To Rn` (2021-02-09) through `v36.4.2`. **From position 25 the ordinal equals the major number in the playlist name** — independent proof the chain is complete at the tail.
- **Tenure** is presence in the generations, and is not the same thing as membership (presence in any playlist). Counted in **generations first** because a generation is attention-weighted time: a playlist runs long when listening is sparse and short when it's dense, so counting generations weights a song equally either way.
- Yields: **tenure** (longest run of consecutive generations), total generations, and run count. Derived at query time, never materialized.

**Planning moved three things out of B.** *Intent score* goes to H, *adoption stagger* to F/G (see both), and the *right-censoring flag* is dropped from this layer entirely — tenure is a raw measurement, so a song appearing only in the newest generation has tenure 1 regardless of age. Censoring is the interpreting consumer's job, and H must not read a low tenure on recent material as failure.

## K — Entity viewing pages ✅ DONE

**✅ DONE → `docs/specs/entity-pages-K.md`.** That spec is authoritative for what actually
shipped; the summary below is the original shape, kept for the reasoning. Planning **corrected
two things here**: `GET /v1/artists/{id}` and `GET /v1/albums/{id}` both work (only the bulk
forms 403), so **artist images and full album tracklists are obtainable** — the *API capability*
section above is wrong on artist images; and the library has tripled since those measurements
(3,611 → 9,930 tracks, 63% with no playlist membership). It also settled nine tiers-and-URLs
questions the summary below doesn't cover.

**What shipped differs from the summary below in three ways.** Group pages are the *primary*
entity pages, one per tier at flat top-level URLs (`/version/<id>`, never `/song/<id>/version/<id>`)
— the track page is the narrow one, carrying only what can't be aggregated. The verification pass
decided **three surfaces keep their bare names on purpose** (both canonical review queues and the
round-trip's manual-alias table — spec §12.3), so the sweep below is complete without them; the
canvas stayed out of scope as planned. And the album/artist detail fetches are capped at **one
Spotify request per page load, first view only**, which is why an album past 50 tracks renders
the first page plus any owned track that fell beyond it rather than paging.

Proper pages for viewing a **song**, an **album**, an **artist** and a **playlist** — the canonical
place each entity is displayed, which every other page then links into instead of
re-deriving its own display. Right now there is no real track-viewing page:
`/dev/snapshot/track/<id>` exists as an inspection tool, not a destination, and nothing
links to an album or artist at all.

- **Absorbs B's per-generation view.** B's generation list links each generation to a stub;
  K replaces that stub with a **"generation view" toggle on the playlist page** for
  current-favs playlists, showing the extra per-generation detail (its tracks split into
  carried-forward vs. new, span, survival into the next generation). One page to maintain,
  not two.
- **This step is not done until every existing page links into these.** That back-pass is
  the point of the step, not a follow-up to it — a viewing page nothing links to changes
  nothing. Sweep `/dev/snapshot*`, `/dev/canonical*`, `/dev/artists`, `/dev/generations*`,
  `/dev/roundtrip` and the canvas for places currently rendering a bare name where an entity
  link belongs.
- The artist page must resolve through `artist_alias`, like everything else artist-level.
  The song page is the natural home for the canonical group's version/recording/release
  nesting that `canonical.song_tree` already builds.

Sits after B so the generation view has something to show, and before H so scoring has
somewhere to display its output.

## H — Scoring ✅ DONE

**✅ DONE → `docs/specs/scoring-H.md`.** That spec is authoritative for what actually
shipped; the summary below is the original shape, kept for the reasoning. Its §0 exists
specifically to list what planning reversed here, and is worth reading before trusting any
line of this section.

**Five terms proposed below were struck**, each for a recorded reason (spec §0.3): the
**intent score**, because aggregation is deliberately uniform and an artist-specific term
breaks that; **comeback / run-count**, because comebacks are granted arbitrarily — many old
greats Finn would re-add and deliberately doesn't — so absence of one is a snub rather than a
verdict, and only 22 of ~8,950 version groups have more than one run anyway;
**post-first-year play share**, as arbitrary and time-dependent; and `shuffle` / `reason_start`
/ `reason_end`, the last struck on measurement (skip plays carry 5.1% of total weight, and the
continuous listen fraction has already priced them).

**The two horizons are not two models.** `all_time` and `recent` are the *same* computation
over different time windows, not a long model and a short one with different terms — plus a
15% all-time blend, without which 86% of the library ties at exactly zero on `recent`.

**Right-censoring needed no special handling.** Rate-over-exposure plus shrinkage toward a
bucket baseline covers it: new material is measured per unit of exposure, and thin evidence is
pulled toward its bucket rather than to the floor. The load-bearing invariant is stronger and
more specific than "don't read new as failed" — **absence of plays is never negative evidence**
(§4.6), which forbids any membership-to-play ratio outright.

**`impact` is retired**, as this section asks — not just at the review queue but everywhere it
ordered anything, and in the three templates that rendered it.

**`entities.play_stats` was *not* absorbed**, deliberately (§11.5). It returns raw play counts
over total/30d/7d; H materializes *weighted* scores over all-time and 90 days. Those are
different quantities over different windows, so folding one into the other would couple two
unrelated things. What they share is the play-resolution path and the entity→track-ids
expansion — the only place they could genuinely disagree.

A general song ranking that feeds album and artist rankings by aggregation. Motivation: play count over-rewards pleasant background music, tenure under-rewards recent arrivals, and neither handles recency.

- **Two horizons — old and new.** Internally likely one model with a recency-weight parameter, but presented and used as two distinct scores (library-wide retrospective vs. current recommendation context), with the knowledge that the line can move.
- Short horizon leans on recency-weighted plays and current-version membership; long horizon on tenure, comeback behaviour, and post-year-one share.
- ~~**Calibrate against ATG** — it's the only real ground truth in the library.~~
  **Reversed during H's planning.** ATG is a **holdout, not a calibration target**: it's a
  personal favourites list, so an algorithm that reproduces it is either overfit or the
  playlist isn't genuine — agreement proves nothing either way. The algorithm was designed
  against no target and validated instead against an eleven-collection set whose tiers Finn
  stated in advance (spec §2.7). ATG is looked at once, afterwards, unbiased.

> ⚠️ ~~**ATG must be corrected before it is used as ground truth.**~~ **No longer a blocker.**
> ATG's uncleaned state does not gate H, because H never reads it. The cleanup is still
> worth doing on its own merits, but nothing waits on it. (The ATG *convention* is right;
> the contents aren't.)

**`impact` is a placeholder for the score.** It is currently the summed live-membership count (and before step E, a broken one — see that spec's §0.1), used as the review queue's ordering. When H lands, replace it there.

The score must be **aggregation-comparable across arbitrary group sizes** — a song, an album, an artist's discography and a playlist all need to be rankable against each other. Naive averaging of per-song scores fails: there are albums that are genuinely top-10 where only half the tracks get played, and averaging drags them to mid. Per-song inputs in play: plays, memberships, tenure, recency.

**Also consider — intent score.** Moved here from B during B's planning. An artist's mean track tenure minus the library baseline: *when I add this artist's songs, do they stick?* It separates preference from consumption, which matters because play count alone ranks pleasant background music top. It's a pure function of B's tenure, so it costs nothing but a query once B has landed — decide at scoring time whether it earns its place.

**Right-censoring lives here, not in B.** B's tenure is deliberately raw: a song present only in the newest generation has tenure 1, and B provides no flag saying it hasn't had its chance yet. Any scoring that skips that distinction reads new material as failed material.

**Two things K leaves for H.** K's entity pages (`docs/specs/entity-pages-K.md`) deliberately
apply **no ordering** to any track, album or artist list — they sort by name, because ranking is
this step's job. When the score lands, order those lists by it and **render the score on the
entity pages themselves**, which is the natural place to see it.

**Fold in `entities.play_stats`.** K added a small per-entity play read (total / past 30 days /
past 7 days for a set of track ids, resolved through `played_uri_track`). It is deliberately
simple. Whatever bulk play aggregation H — or F/G — builds should absorb it rather than leaving
two read paths that can disagree.

## J — Partial / resumable pulls ✅ DONE

**✅ DONE → `docs/specs/partial-pulls-J.md`.** That spec is authoritative for what actually
shipped; the summary below is the original shape, kept for the reasoning. Its §0 records
where planning contradicted this section, and two corrections matter enough to state here:

**J's stated premise below is not yet true.** Measured 2026-08-15, a full pull costs **232
requests** — the *same* 232 measured in July, even though the library went 3,611 → 11,418
tracks, because the round-trip's new tracks carry no playlist memberships. Live memberships
only moved 12,513 → 12,688. The growth this section anticipates has not arrived.

**The real reason J was worth doing now** was found during planning: an aborted pull was
worse than useless, it *poisoned the next refresh*. Every playlist's fresh `snapshot_id` was
committed before any item read, and nothing compared it against what the stored items
actually came from — so the playlists an aborted run never reached looked unchanged forever.
Resumability and that bug were the same missing fact, fixed by one new column.

**No request budget shipped** — there was no data to set one from. J ships the `api_request`
log instead, which is what makes a budget definable later; that is step **O**, gated on the
log catching a real lockout.

A full pull is one indivisible run of ~225 requests across 152 playlists. The app
is dev-mode with no extended-quota grant, and exhausting that quota returns a
`Retry-After` in the **tens of thousands of seconds** — a real ~24h lockout, hit
more than once now (see `docs/spotify_constraints.md`). The library only grows, so
eventually a full pull will not fit inside one day's budget at all, and the day it
doesn't there is no way to make progress: the run dies partway and the next attempt
starts over from the beginning.

**What already exists**, and is most of the machinery:

- `snapshot.py`'s **refresh** already skips playlists whose `snapshot_id` is
  unchanged, so incremental *change* detection is solved. The gap is a *first* or
  *forced* full pull that can't finish in one budget.
- `RateLimited` aborts a run immediately rather than sleeping through a multi-hour
  wait, and records `retry_at`.
- `jobs.py` gives cooperative stop at a safe point, and every run counts its own
  requests.
- The `excluded` flag already takes playlists out of the item-read pass.
- D established the pattern for a resumable run whose progress is **derived, not
  stored** — its work list recomputes what is left rather than checkpointing, so
  nothing can go stale.

**What the spec session had to decide, and did** (all five settled in the spec's §0.3–§0.4):
progress is **derived**, per D's precedent — one new column plus one `meta` key, no cursor;
**no request budget** (§0.3); ordering is never-captured first, then `all_time` score
descending; resume is manual and needs **no new button**, because under the derived rule
Refresh and Full pull *are* the resume; and the UI gets a stale count on the status line plus
an end-of-run line that says what the run captured rather than implying a total loss.

**Useful data:** `roundtrip_run.requests` is the first per-run request count Symr
has ever recorded, and it is deliberately kept for failed runs too — it is the only
evidence of where the ceiling actually sits.

## F / G — metrics, reports, visualisations

Deferred. The full inventory is carried in `feature_ideas.md` under *Listening analytics*; revisit which items are actually worth building once A–D have landed and the data is real. G additionally waits on the Power BI prototype step and the still-open charting-library choice.

**Adoption stagger belongs here, not in scoring.** Moved out of B during B's planning. Distinct add-days ÷ tracks per artist — did an artist arrive gradually or as one discography dump? The original analysis found bulk-added artists survive slightly worse: real, but small. The reason it can't go anywhere near H is that it exists to **discover a mechanism** that drives liking and souring — so feeding it into a score would make the score predict itself. As a descriptive report about how listening actually works, it's interesting; as a scoring input, it's circular.

## M — Grouping-review fixes + album backfill ✅ DONE

**Shipped 2026-08-15 → `docs/specs/grouping-fixes-backfill-M.md`, which is authoritative for
what actually landed. Read the spec, not this section.** Four things, not the three below:
planning split the review-UI defects into M1 (the `mark_reviewed` over-reach), M1b (the
viewer selection) and M1c (the missing album links), then M2 for the backfill.

**What this section got wrong**, all corrected in the spec's §0:

- **M1's fix is narrower than "the pairs the queue asked about"** — it is exactly the
  **cross-component** pairs, the only ones `_cross_component_reviewed` checks, which makes it
  both the minimal fix and an exact match for what settles a bucket. Verified over all 741
  real multi-component buckets: components always agree with `_bucket_components`, every
  bucket settles, and no within-component pair ever leaks in.
- **No repair script, and none was safe.** Nine of the ten split ISRCs were already fixed by
  hand; the tenth (`QZ8GX1702008`) is the upstream distributor collision and *should* stay
  split. The 95 within-component reviewed-and-ungrouped pairs share no ISRC, so none would
  auto-group even if un-decided. `reset_misgrouped_pairs.py` was deliberately **not** followed
  as a precedent.
- **M1b took the second option** — `canonical_review.js` clears the key on a successful ad-hoc
  apply — because backing out of the review screen must not cost the selection.
- **The cost table below is superseded**: ~208 requests for the last 7 generations was really
  **~178**, because an album Symr already holds in full needs no request at all.
- **M2's shape changed completely.** No budget parameter, no resumability machinery, no
  chaining into the round-trip or auto-group. It is one thing — an extra way to put uris into
  the round-trip's existing queue — and everything is derived, so clearing the queue is a free
  and complete undo.

**Measured on the real run, 2026-08-15** (last 7 generations, ordinals 31–37): albums with a
stored tracklist went **9 → 186** and **1,465 uris were queued**, against the spec's
prediction of 176 albums and 1,465 missing tracks. The round-trip then resolved all 1,465 in
**33 requests**, taking the library from **9,953 to 11,418 tracks**. Generations 31–37 are now
handled, so the buttons have moved on to **24–30 (200 albums, ~199 requests)** and **29–30
(60 albums, ~60 requests)**.

### M1 — the `mark_reviewed` bug

`/api/canonical/cross/apply` (`app.py`) ends with `canonical.mark_reviewed(conn, track_ids)`
over the **whole bucket**, and `mark_reviewed` (`canonical.py`) inserts *every unordered
pair* in it. But the cross-artist queue only ever asks "does this newcomer belong to that
existing song group?" — never "are these two tracks the same recording?". So answering a
bucket (including with the one-keypress default) marks same-artist pairs inside it as
decided, permanently suppressing them from the main queue where the deterministic same-ISRC
rule would have grouped them.

Confirmed instance: Mother Mother's "Free" and "Family" each appear on both *No Culture
(Deluxe)* and *No Culture* with identical ISRC, title and duration. Both buckets also
contained a same-titled track by another artist, making them cross-artist; both same-artist
pairs were silently marked and left ungrouped, stranding the plays on one row and the
memberships on the other. "Love Stuck" survived only because no other artist shares that
title.

**Measured 2026-08-14:** 10 of 775 multi-track ISRCs split across version groups, 21 tracks
(0.2%). Seven `reviewed_pair` rows were cleared and re-reviewed by hand that day. Fix
direction: mark only the pairs the queue actually asked about (newcomer vs existing group
members), not pairs internal to the newcomers — and check every other `mark_reviewed`
caller for the same over-reach.

**Not every split ISRC is a defect — ISRC alone is not a safe merge key.** `QZ8GX1702008`
is held by *both* "Blanks" (Night Cap, 3:23) and "Secrets" (George Barnett, 3:36) — two
unrelated songs on different albums, an upstream distributor collision. E's auto-group rule
(same ISRC **and** identical normalized title **and** duration within 2s) correctly refused
it; any single one of those guards alone would have merged them. Measured collision rate
~1 in 775. So of the 10 splits, 9 were real and 1 was correct behaviour.

### M1b — the viewer's selection never clears

`/dev/canonical`'s search results carry checkboxes and a "Group selected" button, backed by
a `sessionStorage` key (`canonical_viewer_selection`, `static/js/canonical_viewer.js`). That
key is only ever read and written — **nothing clears it after the selection is used.**
`canonical_review.js` never touches it either.

So handing a selection to the ad-hoc review queue and applying it leaves those track ids
still selected for the life of the tab. The next search-and-select silently carries them
along, and "Group selected" merges the old tracks into the new group. Found 2026-08-14 while
cleaning up split ISRCs: three already-grouped tracks were dragged into an unrelated
two-track merge.

The persistence is deliberate and *is* wanted for gathering tracks across several searches —
it has no completion event, which is the actual defect. The count beside the button does
show the carried-over total on page load, but it sits below the search results and is easy
to miss. Manual "Clear selection" is the workaround.

Fix direction: clear the key when "Group selected" hands off (simplest; costs the selection
if you back out of the review screen), or have `canonical_review.js` clear it on a
successful ad-hoc apply (more correct, couples the two pages). Either is a couple of lines.

### M1c — album names not linked in the canonical listings

K's sweep (`docs/specs/entity-pages-K.md`) missed two spots. `templates/canonical.html`
renders the album as bare text while the track and artists on the same line are linked:

- **`:217`** — the search-results table (`<td>{{ t.album_name or "" }}</td>`)
- **`:28`** — the group listing's track line

One-line fix each: `track_display` already selects `album_id` (`canonical.py`) and
`entity_link` is already imported into that template, so it's
`entity_link('album', t.album_id, t.album_name)` with a guard for tracks whose album is
null. No read-path change needed.

**Latent, same area:** 11 sites build entity links with `url_for` directly rather than
through the `entity_link` macro — `canonical.html:24,215`, `snapshot.html:76,102,130,131`,
`generations.html:41`. They emit correct URLs today, so nothing is broken, but this is
precisely the page-to-page drift `entity_link` exists to prevent. **Four other bypasses are
legitimate and must stay** — `entity_playlist.html:27,42,44` and `generations.html:40` pass
`generation=1` / `tier=` query params, which `entity_link` has no way to express. Either
teach the macro to take extra params or leave those four alone deliberately; don't
"normalise" them by accident.

### M2 — album-tracklist backfill

Symr holds **9,949 of 55,852 tracks (17.8%)** of the full catalogue of every album it has
touched. Filling that in is what makes album scores fully truthful, because H pads an album
with its untouched tracks (`docs/specs/scoring-H.md` §5.4) and a backfilled track joins its
twin's version group by ISRC, inheriting that score.

**Measured 2026-08-14.** Cost is dominated by tracklist fetches (~1 request per album,
regardless of size), not the round-trip:

| scope | albums | missing tracks | requests |
|---|---:|---:|---:|
| everything | 6,214 | 45,903 | ~5,007 |
| any track in a generation | 1,403 | 7,342 | ~997 |
| **any track in the last 7 generations** | 312 | 1,465 | **~208** |
| top 10% of albums by plays | 621 | 3,420 | ~474 |

Ongoing upkeep: median **82 new albums and ~92 requests per month** over the last 12 months.
55 albums exceed 50 tracks and need tracklist paging.

**The binding constraint is grouping review, not quota.** A full backfill takes the library
from 9,949 to 55,852 tracks — 5.6× — and every one needs grouping. E's queue was painful at
810 candidates. The last-7-generations slice is a 15% increase and comfortable; the full one
is not, and wants J (resumable pulls) to exist first.

**M1 must land before M2 runs at any scale**, since every backfilled track with a common
title is exactly the shape that triggers the bug.

## N — Async score recompute ✅ DONE

**Shipped 2026-08-15 → `docs/specs/async-recompute-N.md` is authoritative for what actually
shipped.** Read it, not this summary. Surfaced 2026-08-15, mid-M-implement, when Finn noticed a
half-second-plus delay after every Enter/Next in the grouping review queue.

**Measurements.** `scoring.recompute()` was recorded at **1.35–1.50s** when N was scoped
(2026-08-15; H's spec recorded ~1.2s before that — the library has grown, and `scoring.py` was
untouched by M, confirmed by an empty diff, so none of the drift is a regression). Verify
re-measured it the same day at **1.75–1.80s** over four consecutive runs, ~2.5s cold; that is
the number the spec and code now carry. Verify also found two long-standing figures in H's
comments to be badly stale and corrected them: the backstop's fingerprint read is **~5ms**, not
the ~87ms claimed, and its `PRAGMA data_version` fast path **~0.002ms**, not 0.03ms.

**What shipped.** Recompute moved onto a single coalescing background worker in `scoring.py`
(`request_recompute()` + `_worker()`), not into `jobs.py` — the spec's §0 established that as a
hard impossibility rather than a preference, since jobs call `recompute()` *while holding the
slot* and would deadlock against their own closing call. Holding Enter through ten queue items
now costs two or three recomputes rather than ten, and `/api/canonical/apply` dropped from ~1.8s
to **95ms** (measured in verify).

**The two open questions were both settled, one of them against its premise:**
- *Background thread vs. dropping the explicit call and trusting `ensure_fresh()`* — the second
  is a **dead end**. `ensure_fresh()` runs in an app-wide `before_request` hook, so dropping the
  explicit call just moves the delay from the end of one request to the front of the next one in
  the same interaction. Same felt delay. A worker was the only option that helped.
- *Scope: the two queue endpoints, or `/dev/artists` too?* — settled **by a rule, not a list**:
  async where you are working a queue, synchronous where you clicked once and are waiting for the
  outcome anyway. Five of the nine request-path sites went async, four stayed synchronous.

**Why it stayed whole-library.** Unchanged and still true: shrinkage pulls every version's raw
score toward its bucket's **median** input (§4.5), and a median doesn't update incrementally the
way a sum or count does. A single grouping edit's blast radius is also bigger than the tracks in
the request — merges/splits shift membership/tenure/play counts for every version involved,
`apply_partition` can drag in tracks outside the edited item, and each affected version cascades
into its recording/release/track blend (§6). Incremental scoring remains a real redesign of
scoring's core, and is still not on this roadmap.

**Load-bearing finding, verified during planning and worth not re-deriving:** *nothing in the
codebase writes a durable decision derived from a score.* Scores are read only for rendering and
ordering (`app.py`, `artists.py`, `canonical_detect._order()`); `canonical_autogroup`'s rule is
ISRC + normalized title + duration and never touches one. That is what makes a briefly-stale
`score` table safe, and it is the assumption to re-check before anything downstream starts
*branching* on a score.

## P — Codebase health

**Specced, and unusually shaped → `docs/specs/codebase-health-P.md`.** That file is the entry
point, but it is a standing **approach document, not a contract** — read its §0 first, because
every other file in `docs/specs/` is a complete fully-decided spec and this one deliberately is
not. The per-part instructions live in `docs/codebase-health/<part>.md` and are written one at a
time, as the previous part's findings come in.

**Findings record → `docs/Planning/codebase_health_P.md`.** A dated bird's-eye review from the end
of J's planning; everything measured in it should be trusted or re-measured deliberately, never
re-derived by accident. Its §8 listed six things a plan session had to decide — all six are
answered in `codebase-health-P.md` §9.

**Three parts, one branch (`feat/codebase-health-P`), each merged into `main` as it lands:**

- **P1 — spec audit.** All 17 specs (6,381 lines) cross-referenced against the code; every
  difference classified and ruled on by Finn. **This part was added during planning and is not in
  the pre-spec.** It exists because tests written from code encode *what it does* and freeze
  existing bugs into a permanently green suite — the audit is what makes P2's assertions
  trustworthy. Explicitly **not** behaviour-preserving: it will surface real bugs, some fixed
  inline. → `docs/codebase-health/P1_spec_audit.md`
- **P2 — tests. ✅ DONE** (six sessions, each merged on its own; verified 2026-08-22). pytest in
  `tests/`, all four tiers (pure functions, routes, DB-bound logic, Spotify-bound loops), **JS out
  of scope by decision**. `venv/bin/python -m pytest`, **770 tests**, a few seconds. All three
  permanent workflow changes landed and are live: Verify runs it before finish-up, Implement runs
  it before handing off, and every future spec carries a Tests section. **Authoritative for what
  shipped:** `docs/codebase-health/P2_tests.md` (the instructions, incl. §5's test floor and §7's
  coverage discipline) and `docs/codebase-health/P2_findings.md` (**10 findings**, all ruled; the
  `xfail` ledger is empty, so nothing is deferred). Two findings are carried forward deliberately
  as `unclear` and are **explicitly not P3 deletion candidates** — P2-004 and P2-009.
- **P3 — refactor. ← next.** The pre-spec's four findings, verified against P2 by byte-exact HTML
  golden snapshots over all 69 routes (the tooling is committed and inert in `tests/golden.py`;
  P3 captures before, diffs after, deletes). Strictly behaviour-preserving. One deletion is
  already queued and evidenced: `canonical_detect.all_candidate_groups`, condemned by P1-009 on a
  full caller search.

The four original findings, in the order they'd bite:

- **No automated tests at 15,400 lines**, against a `symr.db` that is not reconstructible.
  The specs cover acceptance; nothing covers regression. `snapshot._diff_playlist_tracks` is
  the obvious first target — pure, deterministic, 80 lines, and the one function whose bugs
  silently corrupt membership history.
- **`create_app` is 1,572 lines** — 10% of the codebase in one function, holding 71 view
  functions and 42 `conn.execute` calls. Its five largest views are all doing read-path work
  that `CLAUDE.md`'s own stated rule says belongs in `entities.py`.
- **`CLAUDE.md` is a hand-maintained second source of truth** whose accuracy is load-bearing
  and already drifting (it says two `before_request` hooks; J adds a third).
- **A circular import** between `artists.py` and `canonical_detect.py`. Fragile rather than
  broken, and the only cycle in an otherwise clean graph.

**`create_app` gets query extraction, not blueprints** (`codebase-health-P.md` §5) — the read-path
work moves into the modules that already own that data, leaving the routes where they are.
Blueprints would namespace 53 `url_for` call sites, which would make the golden snapshots
legitimately differ and destroy the one clean verification story P3 has. Deferred, not rejected:
easier after extraction than before, and decidable on evidence then.

**Lint and formatting are skipped** (pre-spec §8.5, answered on measurement): zero unused imports
and zero trailing whitespace across all 18 modules, so a linter would find nothing, and a formatter
would flatten `git blame` on a codebase whose why-comments are its most valuable asset.

**The pre-spec's §6 and §7 matter as much as the findings.** They record what is healthy and
must survive a cleanup — the 24% why-not-what comment density above all — and which
apparent problems are deliberate decisions not to "fix" (the `raw_json` capture, the spent
`scripts/` one-offs, the no-bundler frontend).

**Placed here because it is the next actionable step after J**, not because anything depends
on it: O is gated on the request log catching a real lockout, so it cannot start immediately
whatever the order says. P has no dependencies at all and gets more expensive the longer it
waits — `create_app` only grows. Easy to move if something else matters more.

## O — Request-budget surfacing

**Not specced.** Own `/symr-plan` session. **Gated on data, not code** — it cannot be
started until J's `api_request` log has caught a real lockout.

J (`docs/specs/partial-pulls-J.md`) deliberately ships **no budget**: Symr had never recorded
what it spent, when, or how the response came back, so any number would have been invented.
What J ships instead is the log that makes a budget definable, plus one bare line on `/dev`
reading `Requests: x in 24h · y in 7d`.

O is what turns that record into something that stops you spending the quota by accident:

- **The "remaining today" figure**, added to the row J already ships — deliberately left off
  there because there is nothing to subtract from yet.
- **Cost/budget estimates on the pages that spend requests** — `/dev/snapshot` (a pull's
  estimated cost against what is left), `/dev/roundtrip`, the album backfill. M established
  the pattern worth copying: server-render the number beside the button, because seeing it
  before clicking *is* the budget control, with no preview-then-confirm step.
- **Establish what the quota window actually is.** "24 hours" is an inference from a single
  observed `Retry-After` in the tens of thousands of seconds (`docs/spotify_constraints.md`),
  not a documented fact and not necessarily a rolling day. The log is the first evidence
  Symr will ever have had; read it rather than assuming.

**No viewing page for the log**, decided during J's planning — rolling counts in the places
you are about to spend requests are the whole of the wanted UI, and a page listing every
request would be read once and never again.

**Useful when this is picked up:** `roundtrip_run.requests` remains the only *per-run* request
count from before the log existed, kept deliberately for failed runs too.

## L — Better search

**Not specced.** Own `/symr-plan` session.

K ships the deliberately plain version: a navbar box posting to `/search?q=`, four
`LIKE '%q%'` groups (songs, albums, artists, playlists), each capped at 50 and ordered by name,
server-rendered with no JS. It is enough to reach any entity page and no more.

L is what makes it good: a **dropdown** that answers as you type, **fuzzy matching** so a typo
or a half-remembered title still finds the track, and **ranked results** — across types, so the
artist you have 358 tracks by outranks a one-play song whose title happens to contain their
name. Ranking is the part with real design in it, and it wants H's score to exist first, which
is why this sits at the end rather than next to K.

---

## Cross-cutting notes

- **Statistics from the source inventory are one-run findings, not features.** Its archetype shares, 9-week half-life, and quartile tables came from a single analysis pass, and its own record shows a 3-month comeback threshold classified 58% of the library as comebacks before being tightened to 6 months. Every threshold belongs in config; every published statistic must be recomputed, never hardcoded.
- **Grouping stays a query-time lens.** `docs/specs/canonical-tracks.md` guarantees nothing mutates `track` or `membership`. That makes two things the source inventory flagged as easy-to-get-wrong — "merges must keep the earliest `added_at`", "generation membership must be unioned" — structurally impossible to get wrong here; they fall out of `MIN(added_at) … GROUP BY version_id`.
- **Right-censoring must be explicit.** Anything first seen in the last two generations hasn't had a chance to survive yet. The inventory measured ATG tracks peaking at a mean of week 72 versus week 17 for non-ATG, so material under ~18 months old is genuinely unevaluable — worth surfacing in the UI, since the instinct is to judge far sooner.
- **The snapshot holds one pull's worth of state.** `membership.removed_at` has 4 rows. Frozen old generations are unaffected, but patch removals from the active version before the first pull are invisible. "Dropped from current-favs" is only observable going forward.
- **Genre is deferred, not impossible.** It needs a non-Spotify source. ISRC is populated for 100% of library tracks and MusicBrainz supports ISRC lookup, so ISRC → recording MBID → MusicBrainz/Last.fm tags is the route when it's wanted. **Artist imagery is no longer on this list** — K gets it from `GET /v1/artists/{id}`; only richer-than-Spotify imagery would need Wikidata → Wikimedia Commons.
