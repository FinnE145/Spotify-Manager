# Symr — Feature Ideas / Backlog

Living list of features surfaced during planning. Not yet specced unless linked to a `docs/specs/<feature>.md`. Ordering is rough priority, not commitment.

## Built
- **Library snapshot** — `docs/specs/snapshot.md`. Playlists, tracks, memberships → SQLite.
- **Org canvas** — `docs/specs/org-canvas.md`. Later tiers still open: album/song/artist cards, download export, refresh reconciliation.
- **Canonical tracks** — `docs/specs/canonical-tracks.md` + the four sub-specs in `docs/canonical-tracks/`. The four-tier grouping layer (song / version / recording / release) and its review UI. Consumers — dedup, version engine, analytics — are listed below and still to build.

## Listening analytics
The ordered plan lives in **`docs/Planning/listening_data_roadmap.md`** — read that first; it carries the measured facts and the resolved decisions. The items below are the backlog it feeds, kept here so nothing gets lost. Most are **not yet worth committing to** — revisit once play history has actually landed.

**Foundations** (roadmap features A–E, planned)
- Track metadata capture + re-pull; play-history ingestion; foreign-track round-trip; grouping catch-up.

**Generation metrics** (roadmap feature B, planned)
- **Track tenure** — generations survived, first/last, span, with an explicit right-censored flag.
- **Intent score** — an artist's mean track tenure minus the library baseline. Separates preference from consumption: play count alone ranks background music top.
- **Adoption stagger** — distinct add-days ÷ tracks per artist. Bulk-added artists survive slightly worse; real but small, worth logging rather than advising on.

**Play-shape metrics** (need play history)
- **Decay profile** — per-song play histogram from first play; half-life, share in first four weeks, peak week, share after year one. Needs a cohort restriction (enough plays, enough elapsed time) or it's noise.
- **Archetype classification** — comeback / evergreen / flash / slow burn / standard fade, from ordered rules on the decay profile.
- **Comeback detection** — a real run, then a long silence, then a substantial share of lifetime plays after the gap. Threshold-sensitive: too loose and most of the library qualifies. Only works because Finn All is append-only — low generation tenure *plus* a play comeback is the signature, and pruning Finn All would destroy it.
- **Early-warning / promotion signal** — front-loading measured early predicts long-run outcome. Intended for patch-removal decisions, never auto-dropping.
- **"Too early to judge" flag** — surface that recent material is genuinely unevaluable, since the instinct is to assess far sooner.

**Artist metrics** (need play history + artist ids from roadmap A)
- **Artist aggregates** — plays, unique tracks, completion, skip rate, sub-30s rate, share owned, first/last play. Known bias: skip rate only registers when you're paying enough attention to reach for the button, so pleasant background music reads as approval.
- **Song-fan vs artist-fan** — what share of an artist's plays come from their single biggest track. High intent + high concentration means the songs, not the artist.
- **Feature-credit discount** — streaming history credits plays to the *album* artist, inflating guest appearances. Solvable properly once `track_artist` carries a track-credit vs album-credit flag, instead of by string-matching `feat.`.

**Reports**
- **One-song relationships** — songs played heavily where the artist has few other tracks and this one dominates. Flagged as the highest-value output of the analysis session: artists already validated whose name never got encoded. Needs a manual ignore-list for cultural furniture.
- **Phantom exposure** — artists with real play history that never got saved, or saved without the artist registering.
- **External-name scorer** — score any list of artist names (tour supports, label rosters, recs) against play history: already a keeper / real history / brushed past / never heard.

**Scoring** (roadmap, after B)
- General song score feeding album and artist rankings by aggregation, in two horizons (old / new). Corrects for play count over-rewarding background music and tenure under-rewarding recent arrivals. **Calibrates against ATG, which must be cleaned up first.**

**Visualisations** — blocked on the Power BI prototype step (see Dashboards) and the charting-library choice.
- **Track lifecycle Gantt** — per track, monthly play bars above a generation-membership band on a shared time axis, generation boundaries as dividers, time-scaled so generation width is real duration. The best view built in the analysis session: makes visible songs that left and came back, and short playlist tenure alongside a long real listening life.
- **Decay curve** — share of lifetime plays by week since first play, aggregated, half-life marked.
- **Front-loading quartile chart** — early behaviour vs eventual outcome.
- **Coverage / calibration bars** — any artist list banded by liked / known / unknown.

## Verification / problems dashboard (read-only first)
- **Finn All dedup** — the *report*, built on the canonical-tracks grouping layer (`docs/specs/canonical-tracks.md`, built). Runs at **version** level and grades what it finds using the finer tiers: same release = literal duplicate; same recording, different release = single-vs-album-version; different recording = same-sounding, pick one. Different versions aren't duplicates at all. These + moral removals are the only sanctioned deletions from append-only playlists.
- **Current-favs ⊆ Finn All** — flag tracks in the active `vXX.Y.Z` playlist that aren't in Finn All (failure mode: added to the version, forgot Finn All).
- **ATG ⊆ Finn All** — ATG should be fully contained in Finn All.
- **One-time report: Finn All songs not in ANY current-favs playlist** — historical curiosity; can't fix the frozen old playlists, but interesting.
- **ATG entries with no play history under their URI** — a useful canary rather than an invariant: in the analysis session all such entries turned out to be missed dedups. Needs play history.
- **AI / dodgy-artist cross-check** — compile community-made AI-artist lists (later maybe release-cadence heuristics), cross-check the library, flag candidates for moral removal.

## Version engine (verification, not auto-edit) — an Audit sub-section
Lives under the **Audit** page (`/audit`), not its own nav entry.
- Detect adds via `added_at`; propose a **minor bump** based on day boundaries or an N-hour gap between adds. Propose the playlist rename. Track the major-version lifecycle (new playlist = new major; minor/patch = rename of the active playlist). Verification-only for now; automate later if reliable.

## Organization
- **Folder-structure record** — store Finn's intended/current folder tree in the DB; he keeps it synced manually (reorgs are rare and predictable). Payoff: analytical aid for the big one-time cleanup + reusable framework for future clutter.
- **Spotify Playlists auto-sort** — split Spotify-owned playlists into Blends / curated / Mixes. Signal: owner = Spotify; Blends list two real account authors ("Finn + Friend") instead of "Spotify" / "Made for Finn".
- **Artist-playlist detection** — "you added a new <artist> song but not to their artist playlist"; "you have N <artist> songs — make a playlist?"

## Enrichment
- **Cover library** — central storage of all playlist-series cover art in one place (scattered across devices/downloads/photos today). Finn handles uploads; Symr aggregates/stores/organizes. Covers: read for all playlists; upload = base64 JPEG ≤256 KB.
- **ATG scan pointer** — read/track/advance the pointer stored in the ATG playlist description (e.g. `0-2076` → resume at song 2077); surface "scanned X of Y Finn All songs, Z unscanned."

## Analytics / aggregation
- **Top albums by % of songs saved**, and similar aggregations.
- **Extended streaming-history integration** — full GDPR per-play JSON. **Planned in detail: see `listening_data_roadmap.md`.**
- **ListenBrainz integration** — live play tracking/storage. Not an alternative to the export (which is the only source of back history) but a second writer into the same table; its listens carry `spotify_id`, so they join on the existing `track_id` with no MBID resolution needed.
- **Dashboards** — Finn experiments in Power BI on Windows first (raw data export), then reproduce the views he likes natively in JS. No Power BI built into Symr.

## Liked Songs re-think
- Liked Songs is effectively frozen/unused. Idea: data-derived "classic" proposals (e.g. present across N consecutive major versions, or high long-run play count from history + ListenBrainz), batch-approved — removing the "is it good enough / did I remember" burden while keeping it meaningful.
- Why this is safe where a third hand-curated list wasn't: **a derived set is not a record.** Finn All holds the history, so an auto-filled Liked Songs can be recomputed freely without violating append-only.
- Deliberately **not** an ATG generator. ATG records what songs *mean*, and nothing in streaming data can see a memory.
- **This is a write feature** — it modifies the real library, so it lands only once Symr is past read-only. Scoring inputs overlap with the general scoring feature in the roadmap.
