# Phase 4 — Detection

Sub-spec of `docs/specs/canonical-tracks.md`. Read the tier model there first.

Detection proposes **candidate groups** — small sets of track ids that probably belong together — and **pre-fills** a suggested tier assignment for each. It decides nothing: pre-fills are suggestions rendered in the review UI and written to the DB only when Finn hits Enter.

Lives in `canonical_detect.py`. Pure computation over `track`, `membership`, and the artist model (`track_artist`, `album_artist`, `artist`, `artist_alias`) — no Spotify calls, no writes. With ~3,600 tracks it's a sub-second single pass, so it recomputes per request; no caching.

## Title normalization

1. Unicode NFKD, strip combining marks (so `Beyoncé` → `beyonce`).
2. Lowercase.
3. **Split off the suffix** at the first occurrence of ` (`, ` [`, ` - `, ` – `, ` — `, or ` /`. Everything before is the **base**, everything after (including the delimiter) is the **suffix**.
4. In the base: drop all characters that aren't alphanumeric or whitespace.
5. Collapse runs of whitespace, trim.

No leading-`the` stripping (it never changes meaning here) and no edit-distance fuzziness — exact match on the normalized base only.

Examples:

| Raw title | Base | Suffix |
|---|---|---|
| `AAA` | `aaa` | *(empty)* |
| `AAA (Taylor's Version)` | `aaa` | `(taylor's version)` |
| `AAA - Remastered 2011` | `aaa` | `- remastered 2011` |
| `AAA (Acoustic) [Bonus Track]` | `aaa` | `(acoustic) [bonus track]` |

## Artist identity and overlap

Matching is on **artist ids**, never on names. Each track carries three id sets, all resolved through `artist_alias` first (`COALESCE(aa.canonical_artist_id, ta.artist_id)`), so Spotify's duplicate ids for one artist compare equal:

| Set | Definition |
|---|---|
| `artist_ids` | every `track_artist` row for the track |
| `primary_ids` | `artist_ids ∩ album_artist(track.album_id)` — **or all of `artist_ids`** when that intersection is empty |
| `featured_ids` | `artist_ids − primary_ids` |

Two tracks **overlap** if their `artist_ids` intersect at all — the most permissive rule, so nothing gets missed.

The `primary_ids` fallback is load-bearing. 63 tracks sit on compilations credited to `Various Artists`, where a plain `track_artist − album_artist` would classify the real artist as a feature (`Winter Wonderland` by Tony Bennett on *Christmas Hits*). With the fallback, 477 tracks carry a genuine featured credit and 228 of those have no "feat." in the title at all.

**Album artists are never an overlap signal in their own right** — they exist only to derive the primary/featured split. 51 tracks share the album artist `Various Artists`, so an album-artist overlap rule would merge unrelated Christmas and soundtrack tracks wholesale.

`track.artists` is write-only: still populated by the pull, never read. Splitting it on `", "` was structurally broken for names that contain a comma (`Tyler, The Creator` tokenises to `{tyler, the creator}`), and name matching wrongly merged genuinely distinct artists such as `LiSA` and `LISA`.

## Artist aliasing

Spotify issues more than one id for the same artist — four cases in a 1,610-artist library (`half•alive`, `BONES`, `Shefali Alvares`, `Thurl Ravenscroft`). Left unresolved, the id model splits pairs the name model correctly merged.

`artist_alias(artist_id, canonical_artist_id)` is sparse: only merged artists get a row, pointing at the id with the most `track_artist` rows. `reviewed_artist_pair` records that a pair was judged, whatever the verdict, so it stops resurfacing — the same convention as `reviewed_pair`.

Candidates are pairs whose names normalize equal (same pipeline as titles) but whose ids differ, curated at `/dev/artists`. Name collision is the only automatic signal, so a `Kanye West` / `Ye` split needs a hand-written row; nothing depends on the detector being exhaustive.

## Candidate groups

1. **Bucket** every track in `track` by normalized base title. All tracks are eligible, including ones whose only memberships are removed.
2. Within a bucket, build components by `artist_ids` overlap (connected components, so `AAA` by X, `AAA` by X & Y, and `AAA` by Y all land together).
3. A component with **≥2 tracks** is a **candidate group**.
4. A bucket containing **≥2 distinct components** also produces a **cross-artist candidate** — the whole bucket, artist overlap ignored. These are the Christmas-song and cover cases; they feed a separate list and a separate queue, never the main queue.
5. Single-track components produce nothing.

A candidate group is **unreviewed** when any pair among its tracks is missing from `reviewed_pair` (same rule for cross-artist candidates, over pairs that span components). Only unreviewed candidates enter the queue.

## Suffix classification

Scan the suffix (case-folded) for these keywords:

| Class | Keywords |
|---|---|
| **undecided** | `instrumental` |
| **version** — *sounds different* | `acoustic`, `live`, `remix`, `demo`, `sped up`, `slowed`, `nightcore`, `piano`, `orchestral`, `reprise`, `stripped` |
| **recording** — *sounds the same* | `remaster`, `remastered`, `taylor's version`, `deluxe`, `anniversary`, `mono`, `stereo`, `clean`, `explicit`, `radio edit`, `single version`, `album version`, `extended` |

Precedence when a suffix matches several: **undecided > version > recording**. Safer wins — the more a suffix looks ambiguous, the less the pre-fill assumes.

An empty suffix classifies as **base**. A non-empty suffix matching no keyword classifies as **unknown**.

## Pre-fill rules

Within a candidate group, in order. At every tier, a pair that **already shares that tier for real** (its last-saved grouping, not just this pre-fill) always merges too, regardless of whether the rule below would independently agree — an existing decision is never silently proposed as undone. This is what makes the cross-artist queue safe to prefill the same way as everywhere else: a real prior match survives even where the heuristics alone wouldn't have found it.

**Song.** Every track classified `base`, `version`, or `recording` goes into a song group **with tracks it shares a `primary_ids` artist with** — disjoint primary artists are never the same song by title alone, even an exact match. This is what keeps covers, Christmas songs, and coincidental same-title tracks by unrelated artists from merging by default; a bucket can therefore prefill into more than one song group. Tracks classified `undecided` or `unknown` are left entirely alone — all four tiers singleton, not even the same song. (An unrecognized suffix like `(Bonus Track)` or `- 2011 Version` could mean anything; guessing there is worse than a click.)

Note the asymmetry with candidate generation, which overlaps on the wider `artist_ids`. `Song X by B` and `Song X by A feat. B` therefore land in the same candidate group but pre-fill as two songs: the pair is surfaced for a decision, never merged on a featured credit alone.

**Version.** Within a song group, all `base` and `recording` tracks share **one** version group — a remaster sounds the same as the original, so it's the same version. Each `version`-classified track gets **its own** version group, *not* merged with same-keyword siblings: two different live cuts are two different-sounding things.

**Recording.** Within a version group, merge when any holds:
- **Same ISRC, different normalized album name** → same recording, different releases. This is the AAA-on-four-releases case, and it's the workhorse rule.
- **Clean/explicit pair**: identical normalized *full* title (base **and** suffix), `artist_ids` overlap, durations within 2s, and differing `track.explicit` → same recording, with the **explicit** track pinned as representative. Clean editions carry no telltale suffix and a *different* ISRC, so nothing else catches them.
- **A release-tier match** (below) between the pair — release ⊆ recording nesting means same release always implies same recording, even when neither recording rule fires on its own (e.g. a literal duplicate upload: same ISRC *and* same album).

Otherwise each track is its own recording.

**Release.** Merge when: **same ISRC, same normalized album name, durations within 2s** — even across different album ids. That's the duplicate-album-upload case. Otherwise each track is its own release.

Duration comparison is on `duration_ms`, tolerance 2000 ms. A NULL ISRC never matches anything, including another NULL.

## Ordering

The queue is ordered by **playlist impact**, descending: the total count of live `membership` rows across all tracks in the candidate group. Ties break by group size descending, then base title ascending.

This puts the songs that appear all over the library first, and — since larger groups accumulate more memberships — naturally floats the big groups to the top.

## Public interface

```python
candidate_groups(conn) -> list[CandidateGroup]      # main queue, ordered, unreviewed only
cross_artist_groups(conn) -> list[CandidateGroup]   # cross-artist queue, same ordering
all_candidate_groups(conn) -> list[CandidateGroup]  # incl. reviewed, for the viewer page
ad_hoc_group(conn, track_ids) -> CandidateGroup     # arbitrary selection, for search → queue
```

A `CandidateGroup` carries: a stable **key** (the normalized base title plus the sorted track ids — used to identify the item across a queue session), the ordered track ids, each track's display fields (title, the rendered artist string, album, `album_image_url`, `duration_ms`, `explicit`, `isrc`, live-membership count), each track's suffix classification, the pre-filled tier labels, and the playlist-impact total.

The artist string is rendered `Primary A, Primary B (feat. Featured C)` — primaries in `position` order, with the `feat.` clause present only when `featured_ids` is non-empty, and all names resolved through `artist_alias`.

`ad_hoc_group` skips detection entirely — it takes whatever tracks it's given, pre-fills nothing, and renders their **current saved** grouping.

## Throwaway page for this phase

Phase 4 ships a plain, interaction-free `/dev/canonical` so detection quality can be judged before the queue UI exists: candidate groups in order, each showing its tracks with title, album, duration, ISRC, explicit flag, live-membership count, suffix class, and pre-filled tier labels; then the cross-artist list; then totals. Phase 6 replaces this page entirely.

Read this page carefully before building phase 5 — a bad normalizer or a wrong keyword is far cheaper to fix here.
