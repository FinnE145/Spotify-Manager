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
- **App-level quota exhaustion is a different, much bigger thing than the rolling 30s limit.** Confirmed empirically (Jul 2026, dev-mode app, no extended-quota grant): after two full 149-playlist pulls plus several refreshes in quick succession (a few hundred requests each), a `429` came back with **`Retry-After` in the tens of thousands of seconds (~24h)**. This is Symr's real quota-enforcement mechanism in dev mode, not the rolling window.
- **Spotipy's default retry blindly sleeps through whatever `Retry-After` says, however long.** Worse: urllib3's `Retry` treats `429` specially via a hardcoded `RETRY_AFTER_STATUS_CODES` set — it honors `Retry-After` on a `429` **regardless of `status_forcelist`**, so excluding 429 from `status_forcelist` alone does *not* stop the auto-sleep. The only way to stop it is `respect_retry_after_header=False` on the `Retry` object (`spotify_client.py` builds its own `requests.Session` with this set, and `snapshot.py`'s `_call()` helper does its own short-retry-then-fail-fast handling instead). Forgetting this makes a background pull thread hang invisibly for up to a day with zero UI feedback — happened once during this feature's own build/verification.

## Algorithmic / editorial playlists — NOT retrievable (verified against community reports, Jul 2026)
- `GET /me/playlists` (Spotipy's `current_user_playlists()`) only returns playlists the user actually owns or follows as real playlist objects.
- Since a Spotify API policy change on **2024-11-27**, apps without a pre-existing extended-quota grant (any new app, including Symr) **cannot** get algorithmic/Spotify-editorial playlists via this endpoint at all — Daily Mixes, personalized "genre Mix" playlists (e.g. "Indie Mix"), Blends, "Top Songs of 202X", Discover Weekly, Release Radar, etc. are invisible regardless of whether the user follows them.
- Confirmed empirically: Finn's real 149-playlist pull (Jul 2026) included playlists made *for* him by other real accounts, AI-playlist-maker output, and manually-curated "choose the songs" playlists, but none of his algorithmic/personalized mixes.
- Consequence: the "Spotify Playlists auto-sort" backlog idea (splitting Mixes/Blends/curated) doesn't apply to true algorithmic Mixes since they're not fetchable at all; it still applies to Blends and Spotify-curated genre playlists (e.g. "Summer Indie"), which are real followed playlist objects and do come through.

## Playlist item reads — some followed (not-owned) playlists 403
- `GET /playlists/{id}/items` (Spotipy `playlist_items()`) returns **403 Forbidden** for a subset of playlists Finn follows but doesn't own, even though those same playlists appear fine in `current_user_playlists()` (name/owner/track count readable) and are `public: true`, non-collaborative — same attributes as other followed playlists that read fine. No distinguishing flag found (checked `public`/`collaborative`); root cause unconfirmed (owner-side account restriction? per-playlist Spotify content policy? Filtr US, one of the affected owners, appears to be a Spotify-adjacent branded/marketing account, which hints at a curated-content restriction — but other affected owners are ordinary personal accounts).
- Confirmed empirically: Finn's real 149-playlist full pull (Jul 2026) — 7 playlists 403'd on item reads, all owned by other users (`since11music`, `Atul Gokhale` x2, `Filtr US`, `yuva`, `jasmine.`, `claire ♫˚.🎧`); other non-Finn-owned followed playlists read fine.
- Consequence: Symr's snapshot pull treats a per-playlist track-read failure as **skip and continue** (see `docs/specs/snapshot.md`), not a fatal error — logs which playlists failed and why, keeps the rest of the pull going.

## Playlist item track/episode key — schema quirk (verified Jul 2026)
- `GET /playlists/{id}/items` items key the track/episode object as **`"item"`**, not `"track"` (Spotipy's raw dict). This differs from `GET /me/tracks` (Saved Tracks / Liked Songs), whose items still use `"track"`. Likely because playlist items can hold either a track or an episode (`additional_types=track,episode`) while saved tracks are track-only. Easy to miss since most docs/examples assume `"track"` everywhere — verify against a live response before trusting either key name.

## Data sources beyond the API
- Spotify GDPR "extended streaming history" export (per-play timestamps).
- ListenBrainz (live play tracking / storage) — potential integration.
