# Detection on the artist model — step I

Step **I** of `docs/Planning/listening_data_roadmap.md`. Reworks `canonical_detect.py` to match on the **artist ids** step A captured (`track_artist` / `album_artist`) instead of the comma-joined `track.artists` string, and carries the four consequences that fall out of it.

Read `docs/canonical-tracks/detection.md` for the detection rules themselves — this spec covers the change and the three pieces of surrounding work; that file is the living description of how detection behaves.

Read-only w.r.t. the Spotify library, with one exception: the backfill action issues `GET /v1/tracks/{id}` reads. Nothing here writes to Spotify.

---

## Why now, and what the measurement showed

The roadmap schedules I before C/D because the 461 reviewed pairs form a known-good baseline that the reworked detection can be diffed against — a baseline that disappears once D triples the library. That diff was measured before writing this spec, across every pair inside every normalized-title bucket:

| | pairs |
|---|---:|
| both models overlap | 169 |
| name-only (the id model drops) | **1** |
| id-only (the id model adds) | **0** |

`id-overlap ⊆ name-overlap` is structural, not luck: a shared artist id implies a shared artist name, so the id model can only ever split, never merge. **No diff surface is being built** — the diff is that one pair, and it is a false split:

```
[youre a mean one mr grinch]
  You're A Mean One, Mr. Grinch — Thurl Ravenscroft, Boris Karloff  → 5LCQTpuQCzKjfv233UFQnb
  You're A Mean One, Mr. Grinch — Thurl Ravenscroft                 → 5Gejwv3xz2DpLcxVpMD6hL
```

Two Spotify ids for the same person. The alias model below is what repairs it, and is therefore a prerequisite for the swap rather than a nice-to-have.

The forward-looking case stands on its own: the comma-split is structurally broken (`Tyler, The Creator` tokenises to `{tyler, the creator}`), and `LiSA` vs `LISA` are genuinely different artists that the name model merges today.

---

## 1. Artist identity — `artist_alias`

Spotify issues more than one artist id for the same artist. In a 1,610-artist library there are four:

| Name | ids | tracks each |
|---|---|---|
| `half•alive` | `7sOR7gk6XUlGnxj3p9F54k` / `3kO2yXd1wo6JMzXh4rUKtu` | 46 / 1 |
| `BONES` | `5v2WhpA59TJSdPh7LCx1lN` / `3JLwkZjknapI8Z5dF5kgwk` | 24 / 1 |
| `Shefali Alvares` | `2JNtggH8euHrxePDp6m72P` / `1Jt7JB3WIWaANzwHoybsVn` | 1 / 3 |
| `Thurl Ravenscroft` | `5LCQTpuQCzKjfv233UFQnb` / `5Gejwv3xz2DpLcxVpMD6hL` | 1 / 1 |

Plus one pair that must **not** merge: `LiSA` and `LISA` are different artists.

### Schema

```sql
CREATE TABLE IF NOT EXISTS artist_alias (
    artist_id           TEXT PRIMARY KEY REFERENCES artist(artist_id),
    canonical_artist_id TEXT NOT NULL REFERENCES artist(artist_id),
    decided_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS reviewed_artist_pair (
    artist_id_a TEXT NOT NULL REFERENCES artist(artist_id),
    artist_id_b TEXT NOT NULL REFERENCES artist(artist_id),
    decided_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (artist_id_a, artist_id_b)
);
```

This mirrors the track machinery deliberately, with one simplification. Like `reviewed_pair`, `reviewed_artist_pair` records only that a pair was *looked at* — the verdict is implicit in whether the two now resolve to the same canonical id — which is what stops a decided pair resurfacing. Unlike `track_group`, `artist_alias` is **sparse**: only merged artists get rows, because artists have no tier structure and resolution is a single lookup. There is no `ensure_*` pass.

Resolution is always `COALESCE(aa.canonical_artist_id, <raw id>)` via `LEFT JOIN artist_alias aa`. `canonical_artist_id` is the id with the most `track_artist` rows (ties broken by id ascending); the canonical artist never gets a row pointing at itself. Merging three or more ids works for free — they all point at one canonical id.

`reviewed_artist_pair` stores its pair with `artist_id_a < artist_id_b`, matching `reviewed_pair`'s `_pair_key`.

### Candidate detection

Two artists are a candidate when their names normalize equal (the existing `_normalize_base_string` pipeline: NFKD, strip combining marks, lowercase, drop non-alphanumerics, collapse whitespace) but their ids differ. Cheap enough to recompute per request.

A pair is suppressed once it appears in `reviewed_artist_pair`, or once both ids already resolve to the same canonical id.

Name collision is the only automatic signal, so it will not catch a `Kanye West` / `Ye` split. That is accepted: the table is hand-editable for those, and nothing depends on the detector being exhaustive.

### `/dev/artists`

A plain page, linked from the `/dev` landing page. Lists each candidate pair with, for both sides: artist id, name, `track_artist` count, `album_artist` count, and up to four track titles — enough to judge without leaving the page. Two buttons per pair:

- **Same** — writes both `artist_alias` (the smaller side pointing at the canonical id) and `reviewed_artist_pair`.
- **Not same** — writes `reviewed_artist_pair` only.

Below the queue, a section lists already-merged aliases with an **Unmerge** action (deletes the `artist_alias` row and the `reviewed_artist_pair` rows tying that artist to its former group, returning those pairs to the queue). Clearing only the pair against the canonical id would strand the rest: in a 3+-id group the review that merged an artist may have been recorded against a sibling, which would then stay suppressed despite no longer being merged. Consistent with the canonical-tracks convention that nothing is a one-way door.

Endpoints: `POST /api/artists/alias` `{artist_id_a, artist_id_b, same: bool}` and `POST /api/artists/unmerge` `{artist_id}`.

---

## 2. Detection rework

Full rules live in `docs/canonical-tracks/detection.md`; this is what changes.

**Artist sets.** `_fetch_tracks` loads, per track, three alias-resolved id sets in place of the current `artist_set` of normalized names:

- `artist_ids` — every `track_artist` row.
- `primary_ids` — `artist_ids ∩ album_artist(track.album_id)`, **falling back to all of `artist_ids` when that intersection is empty**.
- `featured_ids` — `artist_ids − primary_ids`.

The fallback is not cosmetic. 63 tracks (1.7%) sit on compilations credited to `Various Artists`, where a naive `track_artist − album_artist` classifies the actual artist as a feature:

```
Winter Wonderland | track: Tony Bennett | album: Christmas Hits (compilation) | album artists: ['Various Artists']
```

With the fallback, **477 tracks carry a genuine featured credit — and 228 of them have no "feat." anywhere in the title.** That is the structural win the roadmap promised.

**Where each set is used.**

| Site | Before | After |
|---|---|---|
| Bucket → components (candidate generation) | shared normalized name | shared `artist_ids` |
| `same_song` prefill | shared normalized name | shared **`primary_ids`** |
| `_same_recording` clean/explicit rule | shared normalized name | shared `artist_ids` |

Candidate *generation* stays maximally permissive on any credit, so nothing is missed. The *prefill* tightens to primary artists only, so `Song X by B` and `Song X by A feat. B` still land in the same candidate group but are pre-filled as two songs — a click to merge, never a silent merge. That matches the spec's standing rule that guessing is worse than a click.

**Album artists are never an overlap signal in their own right.** They exist only to derive the primary/featured split. 51 tracks share the album artist `Various Artists` (`0LyfQWJT6nXafLPZqxe9Of`); an album-artist overlap rule would make every one of them overlap every other, merging unrelated Christmas and soundtrack tracks.

**Unchanged:** title normalization, suffix classification and keywords, the tier model, ordering by playlist impact, the `_same_real` override, ISRC/duration/album rules at recording and release tier, the cross-artist queue definition (a bucket with ≥2 distinct components — now components by id), and the public interface.

**Deleted:** `normalize_artists`. It has no other caller.

**The one track with no `track_artist` rows** (`5Rv0O0Bv90IqC97T68zesG`, `California Girls`) resolves to an empty `artist_ids`, so it overlaps with nothing and forms no candidate group. That is correct, and §4 removes the condition anyway.

---

## 3. Artists rendered from the join

`track.artists` becomes **write-only** — still populated by the pull, like `raw_json`, but never read. Every display and search path moves to `track_artist`.

**Display string.** Built in SQL, since SQLite 3.50.4 supports ordered aggregates:

```sql
group_concat(ar.name, ', ' ORDER BY ta.position)
```

Rendered as **`Primary A, Primary B (feat. Featured C, Featured D)`** — primaries in `position` order, then a parenthesised `feat.` clause only when `featured_ids` is non-empty. Artist names resolve through `artist_alias`, so a track credited to a duplicate id shows the canonical artist's name.

*As built:* this landed as a chain of views in `db.VIEWS` rather than one shared SQL fragment — `resolved_track_artist` / `resolved_album_artist` (alias resolution, applied once per join table) → `track_artist_credit` (one row per resolved credit, flagged `is_album_artist`) → `track_artists` (the rendered string). Each step exists for a measured reason: expressing alias resolution inline as a correlated subquery made a full scan take 17s, and deriving the primary fallback via `NOT EXISTS` instead of a joined per-track aggregate took 19s. `track_artist_role` (primary vs featured, one row per credit) is a sibling of `track_artists`, not its input — routing the display string through it cost a second ungrouped aggregate that no `WHERE track_id = ?` could filter into, at 44ms per single-row lookup. `artists.artist_sets()` reads `track_artist_role`; the two agree on every track.

Single-row reads stay slow for the same reason (SQLite won't push a `track_id` filter through the view's `GROUP BY`), so `canonical._artist_display` caches the whole view once per connection. That cache is never invalidated and is safe only because no request both writes artist data and renders track displays — see the note in its docstring before adding one that does.

One track reads oddly under this rule and is left alone: `boygenius — Not Strong Enough` is credited `boygenius` on an album by `boygenius`, with the three members on the track, so it renders `boygenius (feat. Julien Baker, Phoebe Bridgers, Lucy Dacus)`. It is what Spotify says, it is exactly one track, and any fix needs a band-membership concept that exists nowhere in the data.

**Search.** `t.artists LIKE ?` becomes an `EXISTS` over the join:

```sql
EXISTS (SELECT 1 FROM track_artist ta JOIN artist ar USING(artist_id)
        WHERE ta.track_id = t.track_id AND ar.name LIKE ?)
```

This closes a real gap — today `t.artists LIKE '%Tyler, The Creator%'` matches, but the mangled tokens mean artist-name search is unreliable for any name containing `", "`.

**Call sites.** Four SQL reads in `app.py` (each `COALESCE(ta.artists, '')`, so a track with no credits renders empty rather than `None`) plus `canonical.track_display`, two search predicates (`app.py`, `canonical.py`), two JS spots (`canonical_review.js`), six template spots (`snapshot_track.html`, `snapshot_playlist.html`, `snapshot.html` ×2, `canonical.html` ×3). Every one reads the `track_artists` view rather than repeating the SQL.

---

## 4. Snapshot: backfill and a live request counter

Both land on `/dev/snapshot`, reusing the existing progress bar and status poller.

### Backfill

A track that leaves every playlist between pulls freezes at whatever the last pull captured — pulls only see tracks currently in a playlist. `California Girls` was added to Liked Songs on `2026-07-25` and removed on `2026-07-26`, before A's re-pull, so it kept the pre-A shape: name, artists, ISRC, but `raw_json IS NULL` and no `track_artist` rows. Nothing malfunctioned; it is structural, and it will recur.

- A third button, **`Backfill (n requests)`**, beside `Full pull` and `Refresh`, where `n = COUNT(*) FROM track WHERE raw_json IS NULL` (currently 1). **Disabled when `n = 0`.**
- `n tracks awaiting backfill` in the status box at the top, as a new `summary_counts` field so the poller updates it live.
- Runs through the same `_start` / status machinery with `phase = "backfill"`, `run_total = n`, `run_done = i`, so the existing progress bar works unchanged.
- Fetches one track at a time via `GET /v1/tracks/{id}` — the batch `?ids=` endpoint 403s for this app (`docs/spotify_constraints.md`), so requests really do equal tracks and the button's count is honest.
- Reuses A's existing parse/upsert path, so it fills `raw_json`, the album, and the `track_artist` / `album_artist` rows exactly as a pull would. A 404 or other per-track failure is recorded in `failed_playlists` (reused as the run's generic failure list) and does not abort the run; `RateLimited` aborts, as in a pull.
- Those failures carry **track** ids, so the post-run "exclude what failed" button — a playlist-level action — must never be offered for a backfill. `_status` therefore records the run's `action` (`pull` | `refresh` | `backfill`), which unlike `phase` survives into the terminal state; the page reads it from the status poll rather than remembering what it started, so a mid-run reload still suppresses the button.
- `POST /api/snapshot/backfill`, mirroring `/api/snapshot/pull`.

### Request counter

Every Spotify call in `snapshot.py` routes through `_call()`, so this is one increment in one place.

- `_status["requests"]`, reset to 0 at the start of each run, incremented inside `_call` on **every attempt** — a 429 retry really does hit the API and must be counted.
- Rendered as `n requests` to the right of the progress bar, visible for pulls, refreshes, and backfills alike.
- Per-run only; nothing is persisted to `meta`. Because it lives in server-side `_status`, a page reload mid-run reconnects to the running total rather than restarting at zero.

---

## Out of scope

- **Deciding what to do with the detection diff.** It is one pair, and the alias merge repairs it. No further action.
- **A full artist-grouping engine.** Four duplicates in 1,610 artists, with a flat yes/no relation rather than four ambiguous tiers, does not justify the candidate/queue/tier machinery.
- **Artist pages, rankings, images, genres.** Aliasing exists so those aggregate correctly when they arrive; none are built here.
- **`track.artists` removal.** The column stays, still written, merely unread — reversible.
