# Symr — Feature Ideas / Backlog

Living list of features surfaced during planning. Not yet specced unless linked to a `docs/specs/<feature>.md`. Ordering is rough priority, not commitment.

## Next up (first build)
- **Org canvas** — read-only pull of every playlist as draggable cards on an open canvas. Drag to cluster, add freeform text labels, and "copy as text list" to paste into claude.ai for bouncing organization ideas. This is the tool Finn uses to do the one-time categorization pass, which then feeds `docs/library_spec.md`. Read-only. Foundation: needs the library snapshot below.
- **Library snapshot (read-only)** — pull all playlists (name, owner, track list, `added_at`, counts) into SQLite; underlies the canvas and every later feature. Folder placement is NOT readable via the API — tracked separately (see Organization).

## Verification / problems dashboard (read-only first)
- **Finn All dedup** — flag exact-duplicate tracks, and fuzzy dups (same song as single vs album version, wrong-version/album-cover cases). These + moral removals are the only sanctioned deletions from append-only playlists.
- **Current-favs ⊆ Finn All** — flag tracks in the active `vXX.Y.Z` playlist that aren't in Finn All (failure mode: added to the version, forgot Finn All).
- **ATG ⊆ Finn All** — ATG should be fully contained in Finn All.
- **One-time report: Finn All songs not in ANY current-favs playlist** — historical curiosity; can't fix the frozen old playlists, but interesting.
- **AI / dodgy-artist cross-check** — compile community-made AI-artist lists (later maybe release-cadence heuristics), cross-check the library, flag candidates for moral removal.

## Version engine (verification, not auto-edit)
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
- **Extended streaming-history integration** — full GDPR per-play JSON.
- **ListenBrainz integration** — live play tracking/storage.
- **Dashboards** — Finn experiments in Power BI on Windows first (raw data export), then reproduce the views he likes natively in JS. No Power BI built into Symr.

## Liked Songs re-think
- Liked Songs is effectively frozen/unused. Idea: data-derived "classic" proposals (e.g. present across N consecutive major versions, or high long-run play count from history + ListenBrainz), batch-approved — removing the "is it good enough / did I remember" burden while keeping it meaningful.
