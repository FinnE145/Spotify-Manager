# Symr — Roadmap

**Status: planning, not a spec.** This is the standing, ordered plan of what gets built next. Each lettered step becomes its own `/symr-plan` session and its own `docs/specs/<feature>.md`. Nothing here is implementation-ready as written.

**How this doc works.** It's append-only in spirit: finished steps stay, marked ✅ DONE and pointing at the spec that is authoritative for what actually shipped. New work is added as a new lettered step (continue the letters — they're labels, not an order; the order is the diagram below). Anything measured goes under *Verified facts* with the date it was measured, so a later session can trust it or re-measure it deliberately.

**Origin.** It started as the *listening data* roadmap — steps A–J below are that plan, drawn from a July 2026 chat session that analysed the Spotify GDPR extended streaming history against `symr.db` and produced a feature inventory (`symr_analysis_features.md`, not committed). This doc supersedes that inventory: every claim was checked against the real codebase, the real DB, and the live API, and the contradictions resolved. Where the two disagree, **this doc is right**. It is no longer scoped to listening data — later steps can be anything.

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
      DONE                      DONE                DONE                SPECCED
  ──► M (grouping fix + album backfill) ──► J (partial pulls) ──► F/G ──► L (better search)
```

**A, I, C, D, E, B and K have landed.** Their sections below are marked, and each points
at the spec that is authoritative for what actually shipped — read the spec, not
the summary here, before touching any of them.

**H is specced but not built** → `docs/specs/scoring-H.md`. Unusually, its *parameters are
already settled* — tuning happened during the planning session against the real DB, and
`docs/scoring/tuning_prototype.py` is the executable reference. Implementation reproduces
it; it does not re-tune.

**M sits after H** because H is correct without it and improves automatically as coverage
grows — M is not a prerequisite for scoring, it just raises the ceiling.

New steps that aren't part of the listening-data chain slot into this order
explicitly when they're added — don't leave them dangling off the end.

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
- **Finn creates the temp playlist manually** and sets its exclude flag. Code only looks up its id (stored in `meta`) — no create/delete logic.
- Add 100 URIs/request, read back 100/page, clear afterwards so it never nears the 10,000-item cap.
- **Map on the requested URI, not the returned id.** Relinking can return a different id and can collapse two URIs onto one.
- Run on a **different day** from A's re-pull.

**Relinking** — Spotify serves market-specific catalogs, so one recording can exist under different ids per market. Request an unavailable id and Spotify substitutes the equivalent, returning the playable id with `linked_from` holding what was asked for. Unhandled this corrupts attribution silently — that part stands, and it's why the round-trip maps on the requested URI rather than the returned id.

It is **not**, however, a grouping signal, as this plan previously claimed. Checking the mechanics: the requested id comes back only as a stub carrying `id`/`uri`/`type`/`href`/`external_urls` — no name, artists, album or duration — so it can never become a `track` row of its own. There is therefore no *pair* of rows to group and nothing for E to inherit; `track_uri_alias` absorbs the relationship completely. What relinking actually buys is better than a grouping hint: when a foreign URI relinks onto a track already in the library, a "foreign" URI turns out to be an owned track, its plays join straight onto it, and the foreign count shrinks by an amount that can't be predicted in advance.

**Dead URIs** — a 20-URI sample resolved **20/20**, no failures, no unplayable. So this should be rare. When a batch does 400: bisect once or twice via the API to narrow, then check candidates against `https://open.spotify.com/track/<id>` — the **public web page, which is not the Web API and costs no quota**. Worst case if bisecting all the way via API is ~400 extra requests; the web-check path makes that moot.

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

## K — Entity viewing pages

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

## H — Scoring

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

## J — Partial / resumable pulls

**Not specced.** Own `/symr-plan` session.

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

**What the spec session has to decide:** how a partial pull records where it got
to (derived like D's, or an explicit cursor); whether it stops on a request budget
rather than waiting to be rate-limited; ordering (most-stale first? smallest
first?); whether resume is manual or automatic; and what the UI shows for "63 of
152 playlists captured, resume tomorrow".

**Useful data:** `roundtrip_run.requests` is the first per-run request count Symr
has ever recorded, and it is deliberately kept for failed runs too — it is the only
evidence of where the ceiling actually sits.

## F / G — metrics, reports, visualisations

Deferred. The full inventory is carried in `feature_ideas.md` under *Listening analytics*; revisit which items are actually worth building once A–D have landed and the data is real. G additionally waits on the Power BI prototype step and the still-open charting-library choice.

**Adoption stagger belongs here, not in scoring.** Moved out of B during B's planning. Distinct add-days ÷ tracks per artist — did an artist arrive gradually or as one discography dump? The original analysis found bulk-added artists survive slightly worse: real, but small. The reason it can't go anywhere near H is that it exists to **discover a mechanism** that drives liking and souring — so feeding it into a score would make the score predict itself. As a descriptive report about how listening actually works, it's interesting; as a scoring input, it's circular.

## M — Grouping-review fixes + album backfill

**Not specced.** Own `/symr-plan` session. Three things in one step: two review-UI defects
that both silently produce *wrong groups*, and the backfill that M1 in particular gates.

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
