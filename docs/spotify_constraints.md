# Spotify API Constraints

Hard limits of the Spotify Web API that shape what Symr can and can't do. Check this before designing any feature that reads or writes the library. Verify anything marked (unverified) against current API docs before relying on it.

## Playlist folders — NOT accessible
- The Web API returns playlists as a **flat list**. The folder hierarchy (e.g. `Old Playlists > 'All of the songs. ive. loved. beforeee'`) is **not exposed** for reading or writing.
- Consequence: folder organization can't be automated via the official API. Options are (a) track intended folder placement in Symr's own DB and surface a manual to-do list, or (b) fragile unofficial approaches (reading desktop-client local state). Default to (a); ask before pursuing (b).

## Playlist cover images — partial support
- **Read:** cover images for any playlist are available via the API.
- **Write/upload:** custom covers can be uploaded — base64-encoded **JPEG**, **≤256 KB**, scope `ugc-image-upload`.

## Liked Songs
- Exposed as **Saved Tracks**; readable and writable (add/remove) via the API.

## Auth / scopes (verified against developer.spotify.com/documentation/web-api/concepts/scopes)
- Reading private playlists: `playlist-read-private` (and `playlist-read-collaborative` for collaborative ones).
- Modifying playlists: `playlist-modify-private`, `playlist-modify-public` (note: adding tracks to a *public* playlist still needs `playlist-modify-public`).
- Saved tracks (Liked Songs): `user-library-read`, `user-library-modify`.
- Cover upload: `ugc-image-upload`.

## Rate limits (verified)
- Calculated over a **rolling 30-second window**. On `429`, honor the **`Retry-After`** header (seconds) and back off — don't retry before it elapses.
- Spotify does **not** publish an exact numeric limit; it differs between *development mode* and *extended quota mode*. Community testing suggests ≈180 requests/min before `429`. Treat as a guideline, not a guarantee — batch and cache.

## Algorithmic / editorial playlists — NOT retrievable (verified against community reports, Jul 2026)
- `GET /me/playlists` (Spotipy's `current_user_playlists()`) only returns playlists the user actually owns or follows as real playlist objects.
- Since a Spotify API policy change on **2024-11-27**, apps without a pre-existing extended-quota grant (any new app, including Symr) **cannot** get algorithmic/Spotify-editorial playlists via this endpoint at all — Daily Mixes, personalized "genre Mix" playlists (e.g. "Indie Mix"), Blends, "Top Songs of 202X", Discover Weekly, Release Radar, etc. are invisible regardless of whether the user follows them.
- Confirmed empirically: Finn's real 149-playlist pull (Jul 2026) included playlists made *for* him by other real accounts, AI-playlist-maker output, and manually-curated "choose the songs" playlists, but none of his algorithmic/personalized mixes.
- Consequence: the "Spotify Playlists auto-sort" backlog idea (splitting Mixes/Blends/curated) doesn't apply to true algorithmic Mixes since they're not fetchable at all; it still applies to Blends and Spotify-curated genre playlists (e.g. "Summer Indie"), which are real followed playlist objects and do come through.

## Data sources beyond the API
- Spotify GDPR "extended streaming history" export (per-play timestamps).
- ListenBrainz (live play tracking / storage) — potential integration.
