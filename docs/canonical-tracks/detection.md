# Phase 4 — Detection

Sub-spec of `docs/specs/canonical-tracks.md`. Read the tier model there first.

Detection proposes **candidate groups** — small sets of track ids that probably belong together — and **pre-fills** a suggested tier assignment for each. It decides nothing: pre-fills are suggestions rendered in the review UI and written to the DB only when Finn hits Enter.

Lives in `canonical_detect.py`. Pure computation over the `track` and `membership` tables — no Spotify calls, no writes. With 3,589 tracks it's a sub-second single pass, so it recomputes per request; no caching.

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

## Artist normalization and overlap

Split `track.artists` on `", "`, then normalize each name with the same pipeline (NFKD, lowercase, strip punctuation, collapse whitespace). Two tracks **overlap** if their normalized artist sets intersect at all — the most permissive rule, so nothing gets missed.

## Candidate groups

1. **Bucket** every track in `track` by normalized base title. All tracks are eligible, including ones whose only memberships are removed.
2. Within a bucket, build components by artist overlap (connected components, so `AAA` by X, `AAA` by X & Y, and `AAA` by Y all land together).
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

Within a candidate group, in order:

**Song.** Every track classified `base`, `version`, or `recording` goes into **one** song group. Tracks classified `undecided` or `unknown` are left entirely alone — all four tiers singleton, not even the same song. (An unrecognized suffix like `(Bonus Track)` or `- 2011 Version` could mean anything; guessing there is worse than a click.)

**Version.** All `base` and `recording` tracks share **one** version group — a remaster sounds the same as the original, so it's the same version. Each `version`-classified track gets **its own** version group, *not* merged with same-keyword siblings: two different live cuts are two different-sounding things.

**Recording.** Within a version group, merge when either holds:
- **Same ISRC, different normalized album name** → same recording, different releases. This is the AAA-on-four-releases case, and it's the workhorse rule.
- **Clean/explicit pair**: identical normalized *full* title (base **and** suffix), artist overlap, durations within 2s, and differing `track.explicit` → same recording, with the **explicit** track pinned as representative. Clean editions carry no telltale suffix and a *different* ISRC, so nothing else catches them.

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

A `CandidateGroup` carries: a stable **key** (the normalized base title plus the sorted track ids — used to identify the item across a queue session), the ordered track ids, each track's display fields (title, artists, album, `album_image_url`, `duration_ms`, `explicit`, `isrc`, live-membership count), each track's suffix classification, the pre-filled tier labels, and the playlist-impact total.

`ad_hoc_group` skips detection entirely — it takes whatever tracks it's given, pre-fills nothing, and renders their **current saved** grouping.

## Throwaway page for this phase

Phase 4 ships a plain, interaction-free `/dev/canonical` so detection quality can be judged before the queue UI exists: candidate groups in order, each showing its tracks with title, album, duration, ISRC, explicit flag, live-membership count, suffix class, and pre-filled tier labels; then the cross-artist list; then totals. Phase 6 replaces this page entirely.

Read this page carefully before building phase 5 — a bad normalizer or a wrong keyword is far cheaper to fix here.
