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
4. A bucket containing **≥2 distinct components** also produces a **cross-artist candidate** — the whole bucket, artist overlap ignored. These are the Christmas-song and cover cases; they feed a separate list and a separate queue (`/dev/canonical/cross`), never the main queue.
5. Single-track components produce nothing.

A candidate group is **unreviewed** when any pair among its tracks is missing from `reviewed_pair` (same rule for cross-artist candidates, over pairs that span components). Only unreviewed candidates enter the queue.

## Suffix normalization

`normalize_suffix(s)`, used by `classify_suffix`, by `_same_recording`'s suffix equality, and by the auto-group rule's title comparison:

1. `_strip_accents`, then `casefold`
2. every character that isn't a letter, digit or space becomes a **space**
3. collapse whitespace runs, strip

**Punctuation becomes a space rather than being deleted.** NFKD doesn't fold `’` (U+2019) to `'`, so `(taylor’s version)` only agrees with `(taylor's version)` once both collapse to `taylor s version` — which is why all 14 of the former used to fall through unclassified. Keywords are therefore written in the same form (`taylor s version`).

**Digits are kept.** `1947 version`, `remastered 1999` and `99 luftballons` all need them.

This barely compresses the suffix set (1,093 distinct → 1,022). Its value is correctness.

## Suffix classification

Match keywords as **whole token sequences** against the normalized suffix, never as bare substrings: `feat don toliver` contains `live`, and substring matching classified all 16 of those as `version`.

| Class | Keywords |
|---|---|
| **version** — *sounds different* | `acoustic`, `live`, `remix`, `demo`, `instrumental`, `cover`, `nightcore`, `piano`, `orchestral`, `stripped`, `sped up`, `slowed`, `reprise`; session/venue: `long pond studio sessions`, `recorded at spotify studios`, `unplugged`, `voice memo`, `the voice performance`; plus a generic `… version` catch-all (jazz, guitar, original, 1947) |
| **recording** — *sounds the same* | `remaster`, `remastered`, `taylor s version`, `mono`, `stereo`, `clean`, `explicit`, `radio edit`, `single version`, `album version`, `deluxe`, `anniversary`, `extended` |
| **neutral** — *recognised, but says nothing about the audio* | `feat`, `ft`, `featuring`, `with`, `arr`, `interlude`, `skit`, `bonus track`, `edit` |

`neutral` is not a synonym for "harmless": at version tier it is the *least* trusted class, standing alone unless recording identity or a clean/explicit match earns it a merge. See the version rule below.

Precedence: **version > recording > neutral > generic `… version`**, then neutral as the fallback. Each step earns its place — `radio edit` stays `recording` despite containing `edit`; `(Bonus Track Version)` and `(Arr. Jazz Version)` stay `neutral` rather than being caught by the generic rule.

An empty suffix classifies as **base**. Anything unrecognised classifies as **neutral** — the same class the explicit neutral list produces, which is listed anyway as the record that those families were each considered.

Two of these were close calls, decided deliberately:

- **`arr …` is `neutral`, not `version`.** An arrangement usually *is* a different performance, but this library's classical entries are inconsistent enough to be worth deciding by hand.
- **`instrumental` is `version`**, even though an instrumental *cover* is genuinely a different song. A cover is by a different artist, so it shares no primary artist and can never merge at song tier — it goes to the cross-artist queue and gets rejected there. The `version` class only ever affects same-artist tracks, which is exactly the "instrumental version of their own song" case.

## Pre-fill rules

Within a candidate group, in order. At every tier, a pair that **already shares that tier for real** (its last-saved grouping, not just this pre-fill) always merges too, regardless of whether the rule below would independently agree — an existing decision is never silently proposed as undone. This is what makes the cross-artist queue safe to prefill the same way as everywhere else: a real prior match survives even where the heuristics alone wouldn't have found it.

**Song — merges by default.** Two tracks in a candidate group go into one song group whenever they share a **`primary_ids`** artist, whatever their suffix class. Disjoint primary artists are never the same song by title alone, even an exact match, and a shared *featured* credit is not enough either — those two rules are what keep covers, Christmas songs and coincidental same-title tracks apart, and a bucket can therefore prefill into more than one song group.

There is no eligibility gate. The prefill now guesses where it used to abstain: an unrecognised suffix lands in the same song group and has its finer tiers decided by ISRC, duration and album, rather than sitting singleton at all four. That is the intended trade — an obviously-related track sitting alone was the more annoying failure. It takes wrong song-tier splits from **132 to 7**.

Note the asymmetry with candidate generation, which overlaps on the wider `artist_ids`. `Song X by B` and `Song X by A feat. B` therefore land in the same candidate group but pre-fill as two songs: the pair is surfaced for a decision, never merged on a featured credit alone.

**Version.** Within a song group, all `base` and `recording` tracks share **one** version group — a remaster sounds the same as the original.

`version`-classified tracks (acoustic, live, remix, …) each get **their own**, *not* merged with same-keyword siblings: two different live cuts are two different-sounding things.

`neutral` tracks also stand alone, and that is the point — a neutral suffix is the one we understand *least*, so assuming "sounds the same" would be a guess exactly where there is no evidence. `Speechless (Full)` and `Speechless (Part 2)` are 208 s and 144 s. A neutral track still joins a version group when it earns it, through the two rules below.

Beyond the class rule, two tracks share a version group when either holds:

- **Recording identity** (`_same_recording` or `_same_release`), by nesting. So `Lemonade` and `Lemonade (feat. NAV)`, sharing an ISRC and a duration, stay together despite both being neutral. Without this, two rows of the same `(Live)` track — same ISRC, both `version`-classified — would land in different version components, and since recording/release are assigned *scoped inside* a version component, they could then never merge at recording either.
- **A clean/explicit pair** (`_clean_explicit_pair`): same **base** title, artist overlap, durations within 2 s, `explicit` differing. Same version, *never* same recording — they sound near-identical but are not the same recording, which is the whole distinction between the two tiers. Matched on the base title rather than the full one because the suffixes are usually what differ (`Seven (feat. Latto)` vs `Seven (feat. Latto) (Explicit Ver.)` — the marker announcing the very thing being matched on). A `version`-classified side vetoes it: an instrumental or acoustic cut genuinely sounds different, whatever its explicit flag says.

### Recording identity

Two tracks are the same recording when **all three** hold — `_same_recording_identity`:

**same ISRC · same duration · same `explicit` flag**

Any one of them differing means a different recording. Recording means the tracks *are* the same; version means they *sound* the same. A clean edit has words taken out of it, so it is a different recording that sounds near-identical — "same version, different recording" — and it lands there with no rule of its own, because both sides still share a version group through `shares_base_version`.

The `explicit` guard holds **even when Spotify reports the same ISRC** for the clean and explicit rows, which it does for 15 groups in this library (`Come Hang Out` has five, mixed, across two album editions). The differing flag wins.

Duration is compared with the standard 2,000 ms tolerance. That guard is not cosmetic: of 981 same-ISRC pairs, 194 differ in length and **24 differ by more than 2 s** (up to 76 s), so ISRC equality alone would merge genuinely different cuts.

Step E replaced an earlier rule that did the opposite — it merged differing-`explicit` pairs *into* one recording, on the theory that a clean edition is the same master. Wrong tier.

**Recording.** Within a version group, merge when any holds:
- **Recording identity + different normalized album name** → same recording, different releases. The AAA-on-four-releases case, and the workhorse rule.
- **A release-tier match** (below) — release ⊆ recording nesting means same release always implies same recording (e.g. a literal duplicate upload).

Otherwise each track is its own recording.

**Release.** **Recording identity + same normalized album name** — even across different album ids. That's the duplicate-album-upload case. Otherwise each track is its own release.

A NULL ISRC never matches anything, including another NULL.

## The deterministic auto-group

A pair **matches** when it has full **recording identity** (same ISRC, same duration, same `explicit`) *and* equal normalized base titles *and* equal normalized suffixes. A candidate group **auto-closes** when the rule matches on **every** pair in it — partial matches close nothing.

The `explicit` guard matters more here than anywhere, because a run writes **one shared recording** per group: a group whose rows disagree on `explicit` must not close at all, and stays in the queue whole rather than having certainty asserted over a visible contradiction. That is 14 groups — the run closes **554 of 812**, leaving 258.

Scored against the reviewed-pair baseline it is **114/114**: zero disagreements at any tier. Loosening it to bare ISRC equality produces 7 recording-tier disagreements, and feature-stripping the title buys 3 groups at the cost of 2 disagreements. Neither was adopted — the rule asserts certainty and stays maximally strict; feature-neutrality belongs in the prefill, which only suggests.

**Re-score after any change to `_auto_group_pair`.** The baseline is the only ground truth in the project.

`canonical_detect` decides the rule; `canonical_autogroup` owns the writing, the run log and the whole-table undo snapshot. See `docs/specs/grouping-catch-up-E.md` §3.

## Ordering

The queue is ordered by **playlist impact**, descending: the total count of live `membership` rows across all tracks in the candidate group. Ties break by group size descending, then base title ascending.

This puts the songs that appear all over the library first, and — since larger groups accumulate more memberships — naturally floats the big groups to the top.

## Public interface

```python
candidate_groups(conn) -> list[CandidateGroup]      # main queue, ordered, unreviewed only
all_candidate_groups(conn) -> list[CandidateGroup]  # incl. reviewed, for the viewer page
ad_hoc_group(conn, track_ids) -> CandidateGroup     # arbitrary selection, for search → queue

cross_buckets(conn) -> list[CrossItem]              # the reworked cross-artist queue
pending_song_ids(conn) -> list[int]                 # song groups awaiting a tier pass
pending_tier_items(conn) -> list[CandidateGroup]    # those, as ad-hoc items

auto_group_candidates(conn) -> (list[Closable], int)  # the auto-group rule, decided not written
```

A `CandidateGroup` carries: a stable **key** (the normalized base title plus the sorted track ids — used to identify the item across a queue session), the ordered track ids, each track's display fields (title, the rendered artist string, album, `album_image_url`, `duration_ms`, `explicit`, `isrc`, live-membership count), each track's suffix classification, the pre-filled tier labels, and the playlist-impact total.

The artist string is rendered `Primary A, Primary B (feat. Featured C)` — primaries in `position` order, with the `feat.` clause present only when `featured_ids` is non-empty, and all names resolved through `artist_alias`.

`ad_hoc_group` skips detection entirely — it takes whatever tracks it's given, pre-fills nothing, and renders their **current saved** grouping.

## Throwaway page for this phase

Phase 4 ships a plain, interaction-free `/dev/canonical` so detection quality can be judged before the queue UI exists: candidate groups in order, each showing its tracks with title, album, duration, ISRC, explicit flag, live-membership count, suffix class, and pre-filled tier labels; then the cross-artist list; then totals. Phase 6 replaces this page entirely.

Read this page carefully before building phase 5 — a bad normalizer or a wrong keyword is far cheaper to fix here.
