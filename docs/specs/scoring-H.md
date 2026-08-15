# Scoring

**Step H of `docs/Planning/roadmap.md`.**

A general score that ranks a version group, and by aggregation ranks songs, albums,
artists, playlists and any arbitrary collection of tracks against each other on one
absolute scale.

Motivation, from the roadmap: play count over-rewards pleasant background music,
tenure under-rewards recent arrivals, and neither handles recency.

---

## 0. What planning changed

Read this section before trusting the roadmap's H summary.

### 0.1 ATG is a holdout, not a calibration target

The roadmap says *"Calibrate against ATG — it's the only real ground truth"* and carries
a ⚠️ blocker saying scoring waits on ATG being cleaned up. **Both are wrong and are
rewritten by this spec.**

ATG is a personal list of favourites. If the algorithm is designed to reproduce it, then
either the algorithm is overfit or the playlist isn't genuine — agreement proves nothing
either way. So the algorithm is designed from scratch against no target, and ATG is
looked at **afterwards, once**, as an unbiased sanity check.

Consequence: **ATG's uncleaned state does not block this step.** It is not consulted
during design or tuning. The tuning harness deliberately does not dump ATG membership
(§12).

### 0.2 Tuning already happened, during planning

There is **no tuning portal**, no live parameter controls, no user-facing charts. What
ships is one settled algorithm with fixed parameters. Changing them later may be
migration-shaped (a full recompute) rather than live-update-shaped, and that is acceptable.

**The tuning was done in the planning session, not deferred to implementation.** Every
parameter in §10 is settled, with its derivation recorded. The working prototype that
produced them is kept at `docs/scoring/tuning_prototype.py` — it implements every stage of
§4–§8, including both horizons, the blend, `FEATURED_WEIGHT`, `SUBTIER_W` and album
padding, and is the executable reference for this spec. An implementation that disagrees
with it is wrong, or the prototype is, and the difference must be resolved deliberately.

Running it prints the validation-set ordering, both horizons over the artist set, and the
padded-vs-unpadded album comparison — the three checks that back §2.7, §2.9 and §5.4.

**Two things it implements but does not check**, so nobody reads more assurance into a clean
run than is there:

- `subtier_score()` (§6) is defined and never called. There is nothing to call it *with*
  until the real sub-tier scores exist, and §10.1 already records `SUBTIER_W` as the one
  parameter set on principle rather than measured.
- The album stage prints the per-album padded-vs-unpadded pair, which is what §5.4's worked
  examples quote (O My Heart 79.8 → 69.4, Very Good Bad Thing 71.3 → 62.0, both reproduced
  exactly). It does **not** compute §5.4's headline correlation against Finn's canvas
  fractions (+0.854 padded vs +0.530 unpadded) — that came from throwaway analysis and the
  fractions are not in the DB. Treat those two numbers as recorded history, not as something
  a run re-verifies.

Charts made during tuning were throwaway analysis, so **the project's charting-library
choice stays deferred to F/G** — nothing here picks one.

### 0.3 Terms the roadmap proposed that are struck

- **Intent score** (roadmap: *"an artist's mean track tenure minus the library baseline"*)
  — struck. It is an artist-specific term, and the aggregation here is deliberately
  uniform: the combiner does not know or care whether it is combining an artist, a
  playlist, an album or a song group (§5).
- **Comeback / run-count** — struck, and the reason matters because it is not obvious.
  Comebacks *are* a good signal where they occur, but they are granted arbitrarily: there
  are many old greats Finn would re-add and deliberately doesn't, to avoid getting sick of
  them again. So a song *with* a comeback is genuinely good, but a song *without* one has
  been snubbed for no reason. Including the term would rank the snubbed ones below the
  lucky ones on an accident of curation. (Supporting measurement: only 22 of 8,950 version
  groups have more than one run, and 0 of the 13 longest-tenured members of the current
  generation are revivals — the signal is near-inert as well as unfair.) Comeback
  behaviour belongs in F/G as a *descriptive report*, not in a score.
- **Post-first-year play share** — struck as arbitrary, time-dependent and messy.
- **`shuffle`** — struck as low-signal and high error-rate.
- **`reason_start`** — struck. It looks like a deliberateness signal but produces false
  positives: almost all listening is current-favs playlists or queued songs rather than
  clicked rows, and `playbtn` mostly just marks where playback was last paused.
- **`reason_end` / `REASON_END_WEIGHT`** — planned as a deliberateness multiplier on the
  play weight, then **struck on measurement**. `fwdbtn` plays average a listen fraction of
  0.184, so although they are 20% of all plays they carry only **5.1% of total weight**.
  Setting the multiplier to 0.85 changes 0.76% of the library's weight; even 0.3 changes
  3.6%, and neither moves any collection in the validation set by more than 0.001. The
  continuous listen fraction (§4.2) has already priced the skip. Do not re-add it: it costs
  a parameter and a `CASE` in the hottest query in the system for no measurable effect.
- **Removed memberships** — struck as a signal in their own right. A removal is as likely
  to mean "added to the wrong playlist by accident" as dislike. The only place removal
  carries meaning is the generations, and that is already expressed by a tenure run
  ending. `removed_at IS NULL` filtering (what `impact` and `generation_presence` already
  do) is therefore correct and needs no new handling.

### 0.4 Other roadmap facts that have moved

- The roadmap's DB figures are from 2026-08-03 and are stale. See §2.
- The roadmap's `entities.play_stats` note stands: it is absorbed here (§11).

---

## 1. The shape, in one screen

1. **The version group is the atom.** Scores are computed from data at the version tier and
   nowhere else (§3.2).
2. A version's score comes from **weighted plays, play rate over exposure, live membership
   count and tenure**, each saturated and weighted, then **shrunk toward the baseline of its
   bucket** — where the bucket baseline is built from median *inputs*, not from any output
   score, so nothing is circular (§4).
3. **Everything above version is aggregated by one combiner** — a weighted power mean with
   two dials, head-dominant and size-independent. Songs, albums, artists, playlists and
   arbitrary collections all go through the identical function; the combiner does not know
   what it is combining (§5).
4. **Recording, release and track** get their own scores by the same function over their
   own track set, blended heavily toward their version's (§6).
5. **Two horizons, one algorithm.** `all_time` and `recent` differ only by the time window
   the inputs are read through, plus a small all_time blend that stops 86% of the library
   tying at zero on `recent` (§7).
6. **All arithmetic in normalized space**, one monotonic transform to a displayed number
   that typically lands 10–99, is unbounded above and floored at 0. The *stored* score is
   the displayed one (§8).
7. **Version and below are materialized**; collections aggregate at query time. Recompute
   is always whole-library, triggered by every mutating job and backstopped by a check no
   forgotten write path can bypass (§9).
8. **Every parameter is already settled** (§10.1), tuned in planning against the real DB.
   `docs/scoring/tuning_prototype.py` is the executable reference; §2.7's eleven-collection
   validation set is the acceptance test.

The invariant that constrains all of it: **absence of plays is never negative evidence**
(§4.6).

---

## 2. Verified facts

Measured **2026-08-14** against the real `symr.db`. Don't re-derive these; don't trust a
number that contradicts them without re-measuring.

**These are a snapshot of a live, drifting database.** Curation moves them continuously —
grouping a pair merges two version groups into one, a pull adds tracks, and §2.10's seven
deleted `reviewed_pair` rows re-opened groupings that have since changed. A later reader
finding version groups at 8,945 rather than 8,950, or bucket A at 5,406 rather than 5,434,
is seeing normal drift of a few tens of rows, **not** a bug and not grounds for re-measuring
the whole section. Only a discrepancy of a different order of magnitude means something
broke.

### 2.1 Library state

| | |
|---|---:|
| Tracks | 9,949 |
| Albums | 6,214 |
| Artists (raw / alias-resolved) | 4,108 / 4,096 |
| Playlists | 153 |
| Generations | **37** (the roadmap says 36; #37 opened 2026-08-11) |
| Version groups | 8,950 |
| Song groups | 8,794 |
| Plays | 93,063 |
| Play range | 2020-02-12 → **2026-08-06** |
| Distinct played uris | 9,193 |
| Plays that fail to resolve to a track | **0** |

**The round-trip is closed.** 100% of plays resolve through `played_uri_track`. The
roadmap's foreign-uri problem no longer exists.

### 2.2 The library is mostly music that was never chosen

| | tracks |
|---|---:|
| Played **and** in a playlist | 2,878 |
| Played, **never** in any playlist | **6,297** |
| In a playlist, never played | 755 |
| Neither | 19 |

Only 3,633 of 9,949 tracks have a live playlist membership. This is the single most
important fact for this step: the thing being ranked is not "the library" in the sense of
"music Finn chose". All of it is in scope (§3.1), and the score must degrade gracefully
across populations this different.

### 2.3 A fifth of plays are barely plays

| | plays |
|---|---:|
| under 5 seconds | 12,302 |
| under 10% of track duration | 18,576 |
| flagged `skipped = 1` | 20,559 |
| 90%+ of track duration | 60,784 |

Per-track play counts are a long tail: 4,248 tracks have exactly one play ever; 55 tracks
have 100+; the maximum is 235.

### 2.4 Tenure is sparse and low-cardinality

Only **2,151 of 8,794 song groups** (2,169 of 8,950 version groups) have ever appeared in
a generation at all. Of those, 74% have tenure 1–2 and the maximum is 10. Tenure cannot
carry a score on its own.

**Tenure is quantized by generation boundaries**, so a tenure cohort is a single point,
not a distribution. Measured on the current generation's members: tenure-2 is *exactly* 25
days for all 41 of them, tenure-3 is *exactly* 78 days for all 35. This is why the 90-day
recency window (§7.2) is robust — anywhere from 79 to 142 days produces an identical
cohort split.

### 2.5 Generations track calendar time better than listening volume

Tested because it decides whether the recency cutoff should be counted in generations or
in days.

| | all 36 | last 12 |
|---|---:|---:|
| corr(generation span in days, total plays) | **+0.534** | **+0.681** |
| corr(generation span in days, plays/day) | −0.392 | −0.319 |
| CV of total plays per generation | 0.52 | 0.45 |
| CV of plays per day | 0.49 | **0.34** |

If a generation were a constant quantum of attention, total plays per generation would be
roughly constant and `corr(days, plays)` would be near zero. It is +0.53 and rising — a
longer generation genuinely contains more listening. The negative `corr(days, rate)`
shows the self-adjustment is *present but partial*: long generations do run at lower daily
rates, not enough to cancel out. Recent listening runs at a fairly steady ~47 plays/day.

**Consequence: the recency cutoff is measured in days, not generations.**

**This does not undermine tenure being counted in generations** (`docs/specs/generations-B.md`).
That is a different claim and it survives: tenure counts *rounds of curation survived*,
each a deliberate decision to carry a song forward, which is meaningful regardless of how
much time or listening a round contained. What this measurement weakens is only the
"attention-weighted time" argument in B's rationale, and only as a basis for the recency
cutoff.

### 2.6 The three shrinkage buckets are well separated

At version tier (§4.5):

| bucket | versions | med plays | med weighted plays | med memberships | med tenure | % with 0 plays |
|---|---:|---:|---:|---:|---:|---:|
| A — in no playlist | 5,434 | 1 | 1.0 | 0 | 0 | 0% |
| B — in a playlist, never in a current-favs | 1,347 | 1 | 0.2 | 1 | 0 | **46%** |
| C — has been in a current-favs | 2,169 | 29 | 21.5 | 4 | 2 | 0% |

Bucket C carries ~29× the plays of A and B. Bucket B is genuinely distinct: median raw
plays 1 but median *weighted* plays 0.2 — the typical member has a single mostly-skipped
play, and 46% were never played at all.

### 2.7 The validation set

Eleven collections, with the tier Finn assigned **before** seeing any score, from his own
knowledge rather than from any fitted target. This is the guardrail every parameter was
checked against — deliberately a guardrail and not an objective, since 11 collections give
only 47 tier-crossing pairs and fitting 8 parameters to 47 binary comparisons overfits (see
§4.4 for the case where it did).

| tier | collections |
|---|---|
| TOP | My playlist #134 (4 all-time favourites) · half•alive (favourite artist) |
| HIGH | v37.2.1 (current generation) · My playlist #149 (expanded #134) |
| MID | Indie Rock Mix (Spotify-generated, uncurated but taste-matched) · Schur · Phoebe Bridgers |
| LOW | The Weeknd · 1984 Playlist (a classmate's) · k-poop (not his, disliked genre) |
| BOTTOM | taking my airpods out asap (songs he actively dislikes) |

**Final result: 2 inversions of 47 (96% concordant)**, both being half•alive below the two
HIGH playlists — which §2.8 shows is correct behaviour rather than error.

Useful incidental findings: the airpods playlist has only 9 plays across 5 tracks, so Jams
listening does **not** appear in the export; and 24 of Indie Rock Mix's 50 tracks already
carry tenure, so Spotify's recommendations were substantially songs Finn had already
adopted independently.

### 2.8 Artists score below curated playlists, and that is correct

half•alive (Finn's stated favourite artist) scores 0.537 against My playlist #149's 0.669.
Four candidate fixes were tried and **all four failed**: raising `p` (lifts every collection
equally), lowering `TAIL_FLOOR` (actively worse), aggregating over song groups instead of
versions (0.442 → 0.461), and shrinking sparse versions toward their song (0.442 → 0.459).

The diagnosis is structural, and the top-N ladder proves it:

| half•alive slice | score | lands on |
|---|---:|---|
| top 5 | 0.708 | My playlist #134 (0.706) |
| top 20 | 0.591 | My playlist #149 (0.599) |
| full 60 | 0.442 | Indie Rock Mix (0.433) |

**Same artist, same version scores — only the member count changes.** half•alive's best
songs measure exactly as strong as Finn's favourite playlists. The full-catalogue number is
lower because a curated playlist contains only things Finn chose, while an artist contains
everything of theirs that entered the library, including acoustic takes, Vevo live versions
and a spoken interlude. No aggregation parameter can reconcile those populations, because
the difference is in *what is in the set*.

**Decision: artists aggregate over their full catalogue**, because it is truthful. Do not
"fix" this. Restricting an artist to their engaged-with songs would quietly redefine an
artist score as "their hits", and would make an artist known for one song score identically
to one loved entirely.

Related, for later: slicing artists by top-N reorders them (Schur beats half•alive at top-5
and top-10; half•alive wins at top-20 and full), because top-N asks "whose best songs do I
love most" while full catalogue asks "whose body of work engages me most". Both are
legitimate; only the second is built here.

### 2.9 Independent validations

Three checks made after the parameters were frozen, none of which fed back into tuning.

**Membership really does mean "songs I like".** Finn's canvas fractions, written
independently, are reproduced almost exactly by counting tracks with ≥1 live membership:
Very Good Bad Thing 4/10 → 4, No Culture 7/13 → 7, El Camino 4/11 → 4, The Attractions Of
Youth 13/14 → 13, High Noon 6/11 → 6; Dance And Cry, O My Heart and Michigan Left each off
by one. Five exact of eight. This is the empirical basis for `W_MEM` carrying real weight.

**There are no loved-but-never-added songs.** Asked for examples, Finn's answer was that
none exist — loving something *is* adding it to a playlist. So the never-added 60% of the
library is genuinely all radio, autoplay and discovery. The rate term's job there is to
rank that population sensibly, **not** to surface hidden gems, which is why `W_RATE` can sit
as low as 0.40 without losing anything real.

**The two horizons separate current from faded taste.** Artists, all_time → recent:

| artist | all_time | recent | drop | Finn's description |
|---|---:|---:|---:|---|
| half•alive | 73.3 | 47.7 | +25.6 | all-time favourite |
| Schur | 66.2 | **50.1** | **+16.1** | new-ish, currently into |
| Mother Mother | 67.9 | 33.2 | +34.7 | — |
| Noah Kahan | 57.9 | **22.8** | **+35.1** | "used to like, now don't at all" |
| Balu Brigada | 54.4 | 26.3 | +28.1 | soured through over-suggestion |
| The Weeknd | 46.5 | 18.0 | +28.5 | disliked throughout |

Noah Kahan shows the largest drop of any artist tested, landing beside The Weeknd on recent
while staying respectable on all_time — exactly the souring signature the split exists to
capture. Schur shows the smallest, confirming him as the most current.

**Version-level spot check.** The library's top 25 by score was reviewed directly: no entry
looked wrong or out of place, though exact ordering was not claimed.

### 2.10 A grouping bug found during this planning session

Two ISRC-identical Mother Mother pairs ("Free", "Family") were left ungrouped, so the
deluxe track carried all plays and memberships while its twin sat at zero. Cause:
`/api/canonical/cross/apply` calls `canonical.mark_reviewed(conn, track_ids)` over the
**entire bucket**, and `mark_reviewed` inserts every unordered pair — including same-artist
pairs the cross-artist queue never asked about. Answering such a bucket (even with the
one-keypress default) permanently suppresses those pairs from the main queue, where the
same-ISRC auto-group rule would have caught them.

Measured impact: 10 of 775 multi-track ISRCs split across version groups, 21 tracks (0.2%).
The 7 affected `reviewed_pair` rows were deleted on 2026-08-14 so the pairs can be
re-reviewed. **The fix is tracked separately and is not part of this step**, but it should
land before any large album-tracklist backfill, since backfilled tracks with common titles
are exactly this shape.

### 2.11 Cost

| | |
|---|---:|
| Whole-library weighted-play rollup to all version groups | 671 ms |
| Membership rollup | 18 ms |
| `generations.tenures()` | 19 ms |
| Fetch version scores for the biggest artist (232 versions) | 2.4 ms |
| Fetch version scores for the biggest playlist (Finn All, 2,329 versions) | 3.7 ms |
| Fetch **every** (artist, version-score) pair in the library (12,521) | 11.9 ms |
| `PRAGMA data_version` | 0.03 ms |
| `COUNT(*)` fingerprint over the 8 input tables | 87 ms |

A whole-library recompute is on the order of a second. Aggregation above version is
effectively free. Both facts shape §9.

---

## 3. The atom

### 3.1 Scope

**Every track in the library is scored.** No hard gate on membership or plays. A version
with no memberships and no tenure scores low because those inputs are 0, not because it
was excluded — 0 is a real zero that pulls the score down (§4.6 constrains how).

### 3.2 The version group is the atom

Scores are computed from data at the **version** tier and nowhere else.

Version is the project's core definition of what a song entity *is*: "same composition,
sounds different". If two tracks sound the same there is no reason to distinguish them in
most contexts; if they don't sound the same, there usually is. That makes it the right
unit for "how much do I like this".

Everything else follows from version scores:

- **Song** groups aggregate their constituent version scores, using the *same* combiner as
  albums, artists and playlists (§5). A song is not special.
- **Recording**, **release** and **track** each get their own score, computed by the same
  function over their own narrower track set, then blended heavily toward their version's
  score (§6).

---

## 4. The version score

One horizon at a time. The two horizons run this identical computation over different time
windows (§7).

### 4.1 Inputs

Per version group `v`, aggregated over its member tracks:

| symbol | meaning |
|---|---|
| `W` | weighted plays (§4.2) |
| `E` | exposure in days (§4.3) |
| `R` | play rate **in plays per 30 days**, `30·W / E` (§4.3) |
| `M` | live membership count across member tracks |
| `T` | the number of distinct generations the version has ever been present in — `generations.tenures()`'s **`total_generations`**, *not* its `tenure` field (§4.1a) |

`M` counts memberships with `removed_at IS NULL` (§0.3). Note `M` is intrinsically
cumulative: Finn rarely removes tracks or deletes playlists, so memberships accrue and
almost never decay. That is fine — good and meaningful songs are more likely to be added
to any given playlist, so it is a real signal — but it means `M` must never be read as a
*rate* or compared against plays (§4.6).

### 4.1a Which tenure number `T` is

`generations.tenures()` returns two counts per group and they are not the same thing:
`tenure` is the **longest consecutive run** of generations, `total_generations` is the
**count of distinct generations** ever present in. They differ only for groups with a gap
— 22 of ~8,950 version groups (§0.3).

**`T` is `total_generations`.** Two reasons:

1. It is what produced every number in §10.1 and §12 — the prototype computes
   `COUNT(DISTINCT gp.ordinal)`. Using `tenure` would make the implementation disagree with
   the executable reference (§12).
2. `tenure` quietly re-introduces the comeback penalty §0.3 struck. A group present in two
   non-consecutive generations scores `tenure = 1` — the same as a group present once —
   which punishes exactly the arbitrary snub §0.3 says must not be encoded.

### 4.2 Play weight

Each play contributes:

```
contribution = min(ms_played / duration_ms, 1.0)
```

That is the whole rule. The continuous listen fraction handles skips without any separate
skip term: a 3-second skip contributes ~0.02 while a full listen contributes 1.0. It is
what makes "a loved song with many skips" outrank "a hated song skipped once"
automatically — the loved song accumulates far more *weighted* plays despite more raw
skips. Measurement confirmed no `reason_end` multiplier is worth adding on top (§0.3).

Tracks with `duration_ms` of 0 or NULL contribute their raw play at weight 1.0 rather than
dividing by zero.

### 4.3 Exposure and rate

`E` is the days between the version's **first opportunity** and the window end, where first
opportunity is the earlier of its first play and its earliest `added_at`. A version cannot
have been listened to before it existed in either sense.

A version with *neither* a play nor an `added_at` has no first opportunity at all; its `E`
is `MIN_EXPOSURE_DAYS`. This never matters numerically — no plays means `W = 0` means
`R = 0` at any `E` — but it keeps the formula total.

```
R = 30 · W / max(E, MIN_EXPOSURE_DAYS)
```

The floor prevents a version first seen yesterday from posting an unbounded rate off one
play.

**`R` is plays per 30 days, not per day.** The ×30 is load-bearing and easy to drop: it is
what puts `R` on the scale `K_RATE = 0.5` is a half-value *for* (§10.1 records the
distribution as R30 — p50 0.047, p75 0.343, p90 0.96). Implementing `R = W / E` instead
makes every rate 30× too small, collapses `g(R, K_RATE)` toward zero for the whole library,
and fails §12's acceptance test.

Rate rather than raw count is what stops new material being punished for not having
accumulated plays yet. It is the first half of the age-normalization; shrinkage is the
second.

### 4.4 Combining the terms

Each term is mapped through a saturating transform into `[0, 1)` and then weighted:

```
g(x, K) = x / (x + K)
raw     = W_RATE·g(R, K_RATE) + W_MEM·g(M, K_MEM) + W_TEN·g(T, K_TEN)
```

with `W_RATE + W_MEM + W_TEN = 1`, so `raw ∈ [0, 1)`.

**`K` is the half-value** — the input level scoring 0.5 on that term — which is what makes
each one legible: `K_MEM = 4` says "four memberships is a middling membership score".

**Why hyperbolic and not `log`, `tanh` or `1−e^−x`.** Three reasons, the third decisive:

1. **No reference maximum needed.** A log transform must be normalised against some
   ceiling, which would be the observed maximum — so every recompute would silently
   rescale every score. Fatal for an absolute scale (§8). `x/(x+K)` is bounded by
   construction.
2. **`K` is interpretable**, in a way the curvature of `tanh`/`exp` is not.
3. **It never fully saturates, so ordering survives in the tail.** `exp` and `tanh` are
   numerically flat beyond ~5K: at 10K and 20K they differ by one part in a thousand, which
   would make the 55 tracks with 100+ plays mutually indistinguishable. `x/(x+K)` decays as
   `1 − K/x`, giving 0.909 and 0.952 at those points — compressed enough that an outlier
   cannot dominate, separated enough to stay rankable.

**On the weights, one trap worth recording.** Fitting `W_RATE`/`W_MEM`/`W_TEN` against the
validation set (§2.7) drives `W_RATE` to **0**, because that set is mostly playlists and
playlist tiers are defined by membership — textbook target leakage. Taking it would be
catastrophic: with `W_RATE = 0`, all 5,434 bucket-A versions have `M = T = 0` and collapse
to **one identical score**, making 60% of the library a single undifferentiated blob, which
the tier metric cannot see. The weights are therefore set structurally, not by fitting:
rate must carry enough to resolve the never-added majority (68% distinct at `W_RATE = 0.40`),
while `W_MEM + W_TEN = 0.60` keeps curation outweighing consumption, which is the whole
motivation for the step.

### 4.5 Shrinkage and the three buckets

`raw` is an *estimate*, and its reliability varies enormously — a version with 200 plays
over 4 years is well-measured; one with 2 plays over 3 weeks is not. Shrinkage pulls
under-evidenced estimates toward a baseline, and releases them as evidence accumulates:

```
pull  = min( K_SHRINK / (W + K_SHRINK),  SHRINK_MAX )
score = raw + pull · (baseline(bucket(v)) − raw)
```

where `W` (weighted plays) is the evidence volume.

**`SHRINK_MAX` bounds it.** Without the cap, a version with no evidence would land exactly
on its bucket's typical value, which is not a measurement of anything. At `SHRINK_MAX = 0.5`
a zero-evidence version moves **at most halfway** toward its baseline and can never reach
it — its own data always retains at least half the say, and the score stays data-driven
rather than collapsing to the median.

This is the two-sided form of the "a long-term signal has less uncertainty" idea. It pulls
uncertain estimates toward the middle **from both directions**: a new song with 5 plays in
a week doesn't rocket to the top on a freak rate, and a slow starter isn't condemned.

**The baseline is computed in input space, not output space.** This is what makes it
non-circular. Shrinking toward "the library's mean score" would be self-referential —
every score influences the mean, so it would need iterating to a fixed point. Instead:

1. Split every version into one of three buckets.
2. For each bucket, take the **median** of each input independently (`R`, `M`, `T`).
3. Assemble one synthetic version from those marginal medians.
4. Run §4.4 on it **once**. That number is the bucket's baseline.

No output score is ever read, so there is no fixed point to solve.

The buckets, which are exhaustive and mutually exclusive:

| bucket | definition |
|---|---|
| **A** | `M = 0` — in no playlist at all |
| **B** | `M > 0` and `T = 0` — in a playlist, never in a current-favs |
| **C** | `T > 0` — has been in a current-favs generation |

**Buckets are assigned from all-time `M` and `T` on both horizons**, never from the
windowed values. A version's bucket says what kind of thing it *is* — chosen, curated, or
never chosen — and that does not change because a 90-day window is in front of it. Assigning
from windowed inputs would put almost the whole library in bucket A on `recent` (only 3.1%
have a new membership in the window, §7.1a), shrinking curated and never-chosen material
toward the same target and destroying the separation the buckets exist for.

They exist because a new song added to a generation playlist is a completely different
proposition from a new song played once with no memberships, and they should not be pulled
toward the same target. Measured separation in §2.6 confirms they are far apart on exactly
the inputs the score uses.

**Median, not mean** — plays are brutally skewed (4,248 versions have exactly one play,
max 235), so a mean baseline would sit well above typical. And the synthetic version is
built from *marginal* medians, so it is a deliberately typical member rather than any real
track. Both are intentional; don't "fix" either.

### 4.6 Invariant: absence of plays is never negative evidence

**No term may treat "no plays" as bad.** Specifically: no membership-to-play ratio, and
nothing that reads a high membership count with low plays as a negative signal.

The reason is structural. Play data always lags: it ends at the last export while
memberships are current to the last pull. Measured 2026-08-14, tracks whose *first* add
falls after the export edge have plays only 38.5% of the time, against a stable ~88%
baseline for every longer window. A ratio term would read that gap as dislike and actively
penalise the newest material — worse than being uninformed about it.

Adds without plays are a *better* signal than no signal at all, and they resolve
themselves on the next import. The correct behaviour is a partial score — a few
memberships and no plays should land meaningfully above zero and below the same
memberships with plays — which shrinkage toward the bucket baseline delivers naturally.
A new song should appear halfway up the list, not dropped at the bottom.

If any future term would use absence-of-plays as a signal, it must be **explicitly
suppressed for the period after `MAX(play.ts)`**, which is always known.

---

## 5. Aggregation

One function, used for every collection: song groups over their versions, albums over
their tracks, artists over their versions, playlists over their tracks, and any arbitrary
grouping. **The combiner does not know what kind of thing it is combining.** There are no
artist-specific or album-specific terms.

### 5.1 The combiner

A weighted power mean over member scores `sᵢ`:

```
M_p = ( Σ uᵢ·wᵢ·sᵢᵖ / Σ uᵢ·wᵢ )^(1/p)
```

`p` controls head dominance: `p = 1` is a plain average, `p → ∞` is pure max. Size
independence is structural — it comes from the division inside, not from any
normalization step afterwards.

### 5.2 The two dials, and why one isn't enough

The required behaviours pull against each other:

- **Tail must barely hurt.** A 10-track album with 1 ATG track, 4 bangers and 5 unlistened
  should sit *one step below* a 5-track playlist of just the good ones — not be averaged
  down to "mid".
- **But the proportion of great tracks must matter.** Two great tracks on a 10-track album
  does not make it a great album; five does.

Both are driven by `p`, in opposite directions. Worked on the example above (ATG = 1.0,
banger = 0.8, dead = 0.1):

| p | playlist (5 good) | album (5 good + 5 dead) | gap | album B (2 good + 8 dead) | A − B |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.840 | 0.470 | 0.370 | — | — |
| 3 | 0.848 | 0.673 | 0.175 | 0.534 | 0.139 |
| 4 | 0.852 | 0.717 | 0.135 | 0.613 | 0.104 |
| 6 | 0.862 | 0.768 | 0.094 | 0.708 | 0.060 |
| 8 | 0.872 | 0.800 | 0.072 | 0.762 | 0.038 |

High `p` protects the tail but blurs "half great" against "two great". So a **second
parameter** decouples them: instead of every member weighing 1, a low-scoring member
weighs less:

```
wᵢ = max(sᵢ / max(s), TAIL_FLOOR)
```

`TAIL_FLOOR = 1` gives full proportion sensitivity (harsh tail); `TAIL_FLOOR = 0` weights
purely by score (dead members nearly ignored).

**Tuning found `TAIL_FLOOR = 1.0` — i.e. inert — is best at every `p`.** Lowering it makes
the ranking *worse*, because every collection has a tail, so downweighting lifts k-poop and
the generated Indie Rock Mix exactly as much as it lifts a favourite artist. It is kept as
a documented parameter rather than deleted, since the decoupling argument above is sound
and a future case may need it, but **it is not a live dial and should not be moved without
evidence that the specific failure it addresses is actually occurring.**

**`p = 2.5`, and the ceiling is real.** Two things break above it, both measured against
the validation set:

- At `p = 3`, Spotify's generated Indie Rock Mix passes half•alive (0.487 vs 0.486). Soft —
  24 of its 50 tracks already carry tenure, so it is not a random playlist, and Finn's call
  is that it should nonetheless sit below a favourite artist.
- At `p ≥ 4`, **Schur passes half•alive on full catalogue**, which contradicts the stated
  standing of both artists. This one is not soft.

Below 2.5 the cost is Schur ranking lower than Finn judges correct (0.347 at p=1.5 versus
0.429 at p=2.5). 2.5 is the value that satisfies every ordering constraint at once.

**The intuition, in Finn's words**, worth keeping because it describes what the combiner
measures better than the formula does: *hit shuffle on #134 and there's a 100% chance of
loving what comes up; on v37 maybe 10% love and 95% like; on half•alive maybe 10% love and
70–80% like.* A head-dominant size-independent mean is exactly an estimator of that.

### 5.3 Collection membership weight

`uᵢ` is how strongly a member belongs to the collection, and is the only place collection
type enters — as a weight on membership, never as a different term:

- **1.0** by default.
- **`FEATURED_WEIGHT` (< 1)** for a version that reaches an artist only through a featured
  credit. Available structurally from step I: an artist credited on the track but not on
  its album (`track_artist` minus `album_artist`, exposed by the `track_artist_role` view).

### 5.4 Albums are padded with their untouched tracks

An album's member list is padded with `total_tracks − known` zero-scoring members before
combining.

This costs **no API calls**: every track that was ever played or added is already in the
library, so an album's untouched tracks are known to be 0 plays / 0 memberships / 0 tenure,
and `album.total_tracks` is populated for all 6,214 albums.

**Validated against Finn's own judgement.** A canvas made independently, long before this
step, lists albums with hand-written "songs I like / total" fractions. Correlating those
against computed album scores:

| | correlation with Finn's fraction |
|---|---:|
| **padded** | **+0.854** |
| unpadded | +0.530 |

Padding nearly doubles the agreement. The largest corrections are the right ones — O My
Heart falls 79.8 → 69.4 (6 of 12 owned) and Very Good Bad Thing 71.3 → 62.0 (5 of 10).
Without padding, owning one great song rates the whole album.

**Known interaction — duplicate album rows.** Spotify issues several album rows for one
release ("1989" exists five times: Deluxe Edition 18/19, Taylor's Version [Deluxe] 22/22,
Taylor's Version 11/21, plain 7/13, and Deluxe 1/19). Padding punishes whichever row your
listening didn't land on. This is **not** a grouping failure — the tracks themselves are
correctly grouped at version tier, so their *scores* are right; it is the album row that
holds few members. It resolves as album-tracklist coverage improves, because a backfilled
track joins its twin's version group by ISRC and inherits that score. No album-identity
feature is required for the score to be correct.

### 5.5 What the combiner must not be

Recorded because each was considered and rejected for a reason that will otherwise be
re-litigated:

- **Sum** — size dominates; an artist with 358 tracks always beats one with 3.
- **Plain mean** (`p = 1`) — a tail of unlistened tracks drags a genuinely great album to
  mid. This is the failure mode the whole design exists to avoid.
- **Top-N mean** — collapses toward top-1 on small collections, and many collections here
  are small.
- **Noisy-OR** — the original inspiration (a pipeline-risk POE or-gate, where one severe
  feature carries the bulk and twenty benign ones add almost nothing). It only *looks*
  size-independent because POE lives on a ~15-decade log scale. On this scale 20 members
  at 0.1 combine to 0.88, so a tail of dead tracks would **inflate** an album. Rejected.
- **Dividing by collection size** (the POE/km analogue). POE/km is right for pipelines
  because risk genuinely accumulates with length, so it must be normalized away. Here the
  combiner is *already* size-independent, and dividing by N on top collapses it straight
  back to a plain average — reintroducing the exact failure above.

---

## 6. Tiers below version

Recording, release and track each get their own score, computed by §4 over their own
narrower track set, then blended toward their version's score:

```
score(x) = (1 − SUBTIER_W)·score(version(x)) + SUBTIER_W·score_own(x)
```

`SUBTIER_W` is small. The intent is that any two track objects under one version score
almost identically — close enough that comparing the wrong one is a rounding error — while
still differing enough to break ties, which is what makes score usable for choosing a
version's representative track (§11).

---

## 7. The two horizons

### 7.1 One algorithm, two windows

`all_time` and `recent` are **the same computation over different time windows**, not two
models. `recent` is not a differently-weighted score; it simply makes only recent signals
visible:

| | `all_time` | `recent` |
|---|---|---|
| Plays counted | all | within the window |
| Memberships counted | all live | those **added** within the window |
| Tenure | full | generations that **began** within the window |
| Exposure `E` | since first opportunity | clamped to the window |

Memberships count by `added_at` inside the window because membership is cumulative — a
3-year-old add sitting in a stale playlist is not a recent signal, and "live during the
window" would be nearly every membership ever, which varies not at all.

**Tenure counts generations that began within the window, not generations that overlap it**
— the same cumulative-signal argument. A generation's start is its earliest live `added_at`,
which is exactly `generations.generation_spans()`'s `started_at`. The distinction is not
academic: measured at the 90-day window on 2026-08-14, *began within* gives generations
{35, 36, 37} while *overlaps* gives {34, 35, 36, 37} — one extra generation on a scale whose
observed maximum is 10.

### 7.1a The recent horizon is blended with all_time

A pure 90-day window leaves **7,697 of 8,950 versions (86%) scoring exactly 0** — only
13.7% have a play in the window and 3.1% a new membership. That is not wrong (zero *is* the
honest recent activity of a song untouched for three months) but it produces one enormous
tie with no internal order, and renders as a flat 0 on most entity pages.

So the recent score carries a small floor derived from all_time:

```
recent = (1 − RECENT_ALLTIME_BLEND)·recent_windowed + RECENT_ALLTIME_BLEND·all_time
```

At `RECENT_ALLTIME_BLEND = 0.15`, those 7,697 dead versions go from **one** distinct value
to **4,389**, spanning 0.002–0.119, while the library's recent maximum stays at 0.693. So
anything genuinely active still outranks anything inactive by roughly 6×, and the inactive
majority acquires a sensible internal order in which old favourites sit above never-played
tracks.

**Why not exponential decay**, which was the original proposal and would avoid the cliff
natively: it costs nothing to blend (both horizons are computed anyway, so it is one
multiply-add on two numbers already in hand), whereas decay requires re-deriving the whole
play aggregation with per-play weights; and tenure does not decay cleanly, since generations
are discrete and irregular (22–151 days), so a half-life over generation *age* runs straight
into §2.5's finding that generations are not proportional to time. The window sidesteps that
because "generations that began within the window" is unambiguous.

**Known consequence:** this makes the two horizons correlated by construction. That is
wanted here — it is what gives old favourites their edge — but they are no longer
independent views of the library.

### 7.1b Both horizons use the same shrinkage

**`recent` uses the identical `K_SHRINK = 3.0` / `SHRINK_MAX = 0.5` as `all_time`.** There
is no recent-specific shrink parameter.

(An earlier draft of this section said `recent` should use "little or none". That was
written before tuning and never settled; every recorded figure in §2.9 and §7.1a was
produced with the same parameters as `all_time`, and this is the reconciliation.)

**Its bucket baselines all come out at 0.000, and that is correct, not broken.** Baselines
are the median inputs of each bucket (§4.5), and inside a 90-day window the median version
has no plays, no new memberships and no tenure — because 86% of the library is inactive
(§7.1a). So the honest typical value really is zero, and shrinkage toward it degenerates to
a scaling by `1 − pull`, i.e. between 1× and 2× down. Anyone seeing `A=0.0000 B=0.0000
C=0.0000` in the prototype output should recognise it as expected.

Disabling shrinkage on `recent` was measured and **rejected**: it lifts half•alive from
47.7 to 55.0 and Schur from 50.1 to 53.7, flipping them, which puts the all-time favourite
above the artist Finn identifies as his most current — the wrong answer for a score whose
entire job is "what should I listen to now".

### 7.2 The window

`RECENT_WINDOW_DAYS = 90`, measured from **now**, tunable.

90 rather than 60 because tenure is quantized (§2.4): the current generation's tenure-2
cohort sits at exactly 25 days and its tenure-3 cohort at exactly 78, so a 60-day window
bisects tenure-3 — 37% of the current generation — while 90 sits mid-corridor and captures
86% of what is currently live. Any value from 79 to 142 gives an identical split, so the
boundary is robust rather than marginal.

Measured in days, not generations, per §2.5.

The window is anchored to **now**, not to the export edge, so memberships stay current.
Play data simply runs out a few days early inside it; §4.6's invariant is what makes that
safe. Once scrobbling lands, staleness drops to sub-day and the distinction disappears.

### 7.3 Uses

Both render on every entity page. Elsewhere, the caller picks: `all_time` is the one that
would inform populating ATG; `recent` is the one that answers "what should I listen to
next".

Neither holds recent material out of any ranking. `recent` must not penalise a song for
being new at all; `all_time` must not penalise it much — that is what §4.3's rate and
§4.5's shrinkage are for.

---

## 8. Scale and display

**All arithmetic happens in normalized space** — `[0, 1)` scores, exponents, saturating
transforms, the power mean. A single monotonic transform maps to the displayed number at
the very end:

```
display = SCALE · score ^ GAMMA
```

Requirements on the displayed number:

- Typical entities land in roughly **10–99**. Not tens of thousands; not needing three
  decimals to tell two things apart at a glance.
- **Unbounded above.** Nothing is pinned at 100, and a new all-time favourite does not
  push everything else down.
- **Floored at 0.** Negative scores are not worth having: for something to sit far below
  "never heard it" Finn would have to have heard it a lot to specifically dislike it, and
  heavy listening is itself a positive signal. **As designed the clamp never fires** — every
  term of `raw` is non-negative, and shrinkage moves `raw` toward a non-negative baseline, so
  no score can go below 0. Keep the `max(s, 0)` anyway, as a guard against a future term that
  can go negative; do not read its presence as evidence that negatives occur today.
- **The bottom of the *displayed* range is not 0, it is ~11.** A version with no plays, no
  memberships and no tenure has `raw = 0` and shrinks halfway to bucket A's baseline of
  0.024, landing at 0.012 → **11.0 displayed** (§12's p10 is 11.7). That is the intended
  behaviour, not a bug: §4.6 requires absence of evidence to be a partial score rather than
  a zero.
- **Not percentile-normalized against the current library.** An absolute scale means a
  score means the same thing across recomputes and across time; a relative one reshuffles
  everything on every rebuild.

Because the transform is monotonic, ranking is identical in normalized and display space,
so `SCALE` and `GAMMA` can be re-tuned without changing any ordering.

**The exponent is not decoration — it is required by the data.** Raw scores are severely
bottom-heavy: two thirds of versions fall below 0.10 and the median is 0.052, because over
half have no memberships and three quarters no tenure, so most versions score on rate alone
and rate is tiny for a song played once. Displayed with `GAMMA = 1`, half the library would
sit below 5.2 and the lower quartile below 1.2 — unreadable without three decimals. At
`GAMMA = 0.5` the library spans p10 = 11.7 to max 92.6 and the validation collections span
32–87, which is the legible range.

**Materialized scores are display scores.** The stored value is the post-transform number,
so "score" means the displayed 0–100-ish figure everywhere in the project from here on.
Normalized space exists only inside the computation.

---

## 9. Storage and recompute

### 9.1 Materialize version and below

One table keyed by `(tier, group_id)` holding both horizons, covering **version, recording,
release and track** only.

Song, album, artist, playlist and arbitrary collections are **aggregated at query time**
from those. Measured (§2.11): the biggest artist costs 2.4 ms, the biggest playlist 3.7 ms,
and every artist in the library at once 11.9 ms. There is no case for materializing them.

### 9.2 Full recompute, never targeted invalidation

A whole-library pass is about a second (§2.11). Targeted per-row updates would buy nothing
and cost exactly the robustness that matters here — every targeted path is a path someone
forgets. **Recompute everything, every time.**

**Recompute replaces the table wholesale** — clear it and re-insert, in one transaction,
rather than upserting row by row. Grouping changes destroy group ids (merging two version
groups leaves one of them referenced by nothing), and an upsert would leave those rows
behind forever as scores for entities that no longer exist. Clearing is also what makes a
recompute idempotent, which §14 tests.

### 9.3 Triggers, and the backstop that makes them unnecessary to get right

Every job that mutates an input ends by recomputing. Traced against every write site in the
codebase:

| input table | written by | entry point |
|---|---|---|
| `play` | `history_import.py` | export upload / reimport |
| `membership`, `track` | `snapshot.py` | full pull / refresh / backfill |
| `track`, `track_uri_alias` | `roundtrip.py` | round-trip run |
| `track_group`, `canonical_group`, `reviewed_pair` | `canonical.py` | review apply, cross-artist apply, pin |
| `canonical_group` | `canonical_autogroup.py` | auto-group run and undo |
| `artist_alias` | `artists.py` | merge / unmerge |
| `generation` | `generations.py` | confirm-generation |

Plus a manual **"recompute now"** button. Built as its own `/dev/scoring` page rather than a
box on `/dev`, because the button needs somewhere to report back to: it shows the per-tier
materialized counts and the last run's outcome/duration, and updates both in place against
`POST /api/scoring/recompute`. On `/dev` it was a form-POST-and-redirect with no feedback,
so two clicks a second apart were indistinguishable.

**Two write paths are not jobs, and this is why the backstop is mandatory rather than
belt-and-braces politeness:**

- `canonical.ensure_track_groups()` **inserts `track_group` rows on an ordinary page load**
  (`canonical.py:38`) — every canonical and entity page calls it, so a plain GET can mutate
  a scoring input.
- `scripts/backfill_track_details.py` updates `track` from outside the app entirely.

The backstop, on read:

1. `PRAGMA data_version` — 0.03 ms. Unchanged since last check means no other connection
   has committed; stop here. This is the normal case on every page load.
2. Only when it moves, pay the 87 ms `COUNT(*)` fingerprint over the input tables to see
   whether a *scoring* input changed rather than, say, a canvas card.
3. Only when the fingerprint moves, recompute.

This cannot be bypassed by a write path nobody remembered, which is the property the
explicit trigger list can never have on its own.

**Two exceptions, both found in the verify pass, both about not doing the same second of
work twice.** Each one is a *deferral*, never a suppression — the rule they share is that
neither advances the remembered `(data_version, fingerprint)` pair past anything a
recompute hasn't actually covered, so the worst either can do is cause one redundant
recompute, and neither can skip a real change:

- **A completed recompute tells the backstop what it accounted for.** Otherwise the very
  next request sees the writes that triggered the recompute and redoes the whole thing:
  measured at 1208 ms for an explicitly-triggered recompute followed by 1184 ms for the
  request behind it, i.e. every canonical review action paying ~2.4 s instead of ~1.2 s.
  The pair is captured **before** the recompute reads its inputs and published only on
  success. Capturing it first is what makes it safe: anything committed while the recompute
  runs leaves the real fingerprint ahead of the published one, so the next check still sees
  a difference. Capturing it afterwards would record that change as already-handled and
  lose it.
- **The check is skipped entirely while `jobs.active()`.** All three jobs commit
  continuously as they run (per playlist, per batch, per chunk) while their page polls
  status every second, so checking here recomputes the whole library on every poll and
  fights the job for SQLite's single writer — measured at 1210 ms per poll, for the whole
  duration of a pull, all of it thrown away since every job ends with a recompute of its
  own on the success path and both failure paths. Nothing is remembered while deferring, so
  the first request after the slot is released compares against the same pre-job state and
  recomputes if the job's own recompute never happened (it raised, the process died
  mid-run, a future job forgets to call it). Verified by killing a job mid-run with its
  writes committed and no recompute: the next request recomputed. Staleness is bounded by
  the job, which is the one window in which scores would be computed from a half-updated
  library anyway.

**Step 1 requires a dedicated long-lived connection, and will silently not work without
one.** `PRAGMA data_version` is only defined *relative to the connection it is invoked on*:
two reads of it are comparable only when both happen on the same connection, and it never
moves for that connection's own writes. `db.get_db()` builds a **fresh connection per
request** ([db.py:563](../../db.py), closed at teardown), so comparing its value across two
page loads compares two unrelated numbers — SQLite documents that as meaningless, and it
would fail open or closed unpredictably rather than erroring.

So the check owns **one module-level connection in the scoring module**, opened once per
process, used for nothing but this pragma, `check_same_thread=False`, guarded by a module
lock (the same single-lock idiom `jobs.py` already uses), and left in autocommit so it never
pins the WAL.

That is not a workaround — it is what makes the backstop work as designed. Because the
checker connection is never the writer, every real writer (per-request `get_db()`
connections, job threads' `connect()`) is "another connection" from its point of view, so
its commits *are* visible. That includes `ensure_track_groups()` writing on a request's own
connection — the exact path this backstop exists for, and the one a per-request connection
could never have seen.

The rejected alternative, for the record: a version counter persisted in `meta` that every
writer bumps. It reintroduces precisely what the backstop exists to eliminate — a write path
someone forgets to instrument.

**The fingerprint's table list** is the union of the trigger table above: `play`,
`membership`, `track`, `track_uri_alias`, `track_group`, `canonical_group`, `reviewed_pair`,
`artist_alias`, `generation` — nine, not the eight §2.11 says. `reviewed_pair` cannot move a
score on its own (it records that a pair was judged, not how tracks group); it stays in the
list because it is written in the same breath as `track_group` and an occasional redundant
recompute costs a second.

---

## 10. Parameters

Every tunable lives as a **module-level constant in the scoring module**, documented, with
a prominent warning comment.

Not `config.py`, and not environment-tunable, deliberately. Scores are materialized, so
there must be no opportunity for parameters to differ between rows — a per-environment or
per-restart parameter would silently produce a table scored under two different
algorithms. Burying them in the module with a warning ensures anyone changing one knows
they must immediately recompute **everything**, including anything materialized that
depends on score.

The warning comment must say exactly that, and name the recompute entry point.

### 10.1 The settled values

All tuned in the planning session against the real DB via
`docs/scoring/tuning_prototype.py`. Ship these.

| parameter | value | how it was set |
|---|---:|---|
| `MIN_EXPOSURE_DAYS` | 14 | near-inert (v37 moves 0.587→0.572 across 1–60d); two weeks is the shortest window where a rate means anything |
| `K_RATE` (rate in plays/30d) | 0.5 | half-value; R30 is p50 0.047 / p75 0.343 / p90 0.96, so 0.5 spreads the middle without flattening the top |
| `K_MEM` | 4 | p90 of M is 5 |
| `K_TEN` | 2 | p90 of T is 2 |
| `W_RATE` | 0.40 | structural, **not fitted** — see §4.4. Resolves 68% of bucket A |
| `W_MEM` | 0.35 | `W_MEM + W_TEN = 0.60`, so curation outweighs consumption |
| `W_TEN` | 0.25 | tenure is 75% zero, so it cannot carry more than this |
| `K_SHRINK` | 3.0 | flat across 0.5–10 on every metric; 3 ≈ the library's weighted-play p60 |
| `SHRINK_MAX` | 0.5 | Finn's bound: data keeps at least half the say |
| `P_AGG` | **2.5** | ceiling — p≥3 and p≥4 each break an ordering (§5.2) |
| `TAIL_FLOOR` | 1.0 | inert; lowering it makes rankings worse (§5.2) |
| `FEATURED_WEIGHT` | 0.6 | on principle; 13% of credits library-wide are featured, 26% for Phoebe Bridgers, so it does bite |
| `SUBTIER_W` | 0.05 | **on principle only, not validated** — see below |
| `RECENT_WINDOW_DAYS` | 90 | §7.2; robust anywhere in 79–142 |
| `RECENT_ALLTIME_BLEND` | 0.15 | §7.1a |
| `SCALE` | 100 | |
| `GAMMA` | 0.5 | §8; clean square root, p10 = 11.7, max = 92.6 |

`REASON_END_WEIGHT` was struck outright (§0.3).

**`SUBTIER_W` is the one number here with no empirical backing.** It only affects
tie-breaking *within* a version group, so its blast radius is representative-track
selection (§11.3) and nothing else. Verify during implementation that representatives look
sensible; if they don't, this is the dial.

---

## 11. Consumers

### 11.1 Ordering

Score replaces the current ordering at each of these:

| site | today |
|---|---|
| `/dev/canonical` viewer listing (`canonical.song_group_rows`) | `impact` |
| Canonical review queue and cross-artist buckets (`canonical_detect.py`, see below) | `impact` |
| Entity **group** page member-track list (`app.py:157`) | name |
| Artist page track list (`app.py:412`) | name |
| Artist page album list (`app.py:418`) | name |
| `/search`, all four groups (`app.py:558`–`596`) | name, `LIMIT 50` each |
| `/dev/canonical` search results (`app.py:689`) | name, `LIMIT 100` |
| `/dev/snapshot` playlist list (`app.py:973`) | name |
| `/dev/generations/tenure` (`app.py:1064`) | add score as a sort column |
| `/dev/artists` duplicate-pair queue (`artists.py:191`) | name |

The two search sites matter more than they look: both are capped, so name-ordering means
they currently return the alphabetically-first N rather than the best N. L
(`docs/Planning/roadmap.md`) formalizes search ranking later; this fixes the cap's bite now.

**`impact` is retired** once these land. It was the summed live-membership count, used as
a stand-in for exactly this score.

Retiring it is three distinct jobs, and the second and third are easy to miss:

1. **The ordering key.** In `canonical_detect.py` this is *one* key written twice — the
   helper `_order()` ([canonical_detect.py:530](../../canonical_detect.py), five callers)
   and a byte-identical inline `sorted(...)` at
   [canonical_detect.py:730](../../canonical_detect.py). **Fold the inline one into
   `_order()` first**, so switching to score is a one-line change in one place instead of
   two that can silently drift apart. `canonical.song_group_rows` is the other site.
2. **The `impact` value itself**, still computed in `canonical_detect.py` at three places
   (`sum(tracks[tid]["live_count"] …)`) and selected in `song_group_rows`'s aggregate. Drop
   these once nothing sorts on them.
3. **The rendered text.** `impact` is *user-visible* in three templates —
   `canonical.html`'s leaf-meta line, `_canonical_cross.html`'s group heading, and
   `_macros.html`'s `listing_cap_note` ("Showing the top N of M by impact"). These must
   change with it, using §11.4's macro rather than a hand-rolled number, or the pages will
   keep claiming an ordering they no longer use.

### 11.2 Order that must NOT change

- **Tracks within an album** keep disc/track order (`app.py:328`, `app.py:336`).
- **Tracks within a playlist** keep playlist position order (`app.py:468`).

Ordering a *list of* albums or playlists by score is fine and is covered above. It is only
the contents of one album or one playlist that keep their native order.

### 11.3 Behavioural, not display

- **`canonical.representative()`** (`canonical.py:244`) — currently most live memberships
  → oldest `added_at` → lowest track id. Becomes highest score. It stays **computed at
  read**, not materialized, so this costs no extra invalidation; a manual pin
  (`canonical_group.representative_track_id`) still wins over the score. §6's `SUBTIER_W`
  exists to make this tie-breaking meaningful.
- **`artists._canonical_choice()`** (`artists.py:59`) — picks which artist id wins a merge,
  currently by raw track count. Becomes score-weighted.
- ~~**Round-trip work-list ordering** (`roundtrip._WORK_LIST_SQL`, currently by play
  count)~~ — **struck during implementation, left ordering by play count.** The work list is
  defined by `played_uri_track` *not* resolving (`x.track_id IS NULL`), so every uri on it
  has no track row and therefore no score, by construction — there is nothing to order by
  until the uri resolves, at which point it leaves the list. Play count already is "resolve
  the uris that matter most first" here.

### 11.4 Rendering

Both horizons render on every entity page: group pages (all four tiers), track, album,
artist, playlist. A shared macro in `templates/_macros.html`, alongside K's `play_stats`.

**One macro is the whole design system for this.** There is no separate styling exercise:
because every site renders through the same macro, a score looks the same everywhere by
construction, which is the only consistency worth having here (`entity_link` earns its keep
the same way). It takes both horizons and renders the ones it is given:

- **Entity pages** — `score 73 · recent 48`.
- **Listings**, wherever `impact N` appears today — `score 73`. One number, because the
  rows are dense and all-time is the sort key there.

Both spelled out in words rather than a bare number or a symbol: the figure is unbounded and
on no familiar scale, so an unlabelled `73` beside a track name reads as a play count, a
duration or a percentage. This is the `Live#` lesson — a compact label nobody can decode is
worse than a slightly longer one that needs no explanation.

### 11.5 Absorb `entities.play_stats`

K's per-entity play read returns **raw play counts** over total / past-30d / past-7d. H
materializes **scores** over all_time and a 90-day window, from **weighted** plays. Those
are different quantities over different windows, so "absorb" cannot mean H's table serves
`play_stats` — there is nothing for it to be absorbed into, and storing raw counts in the
score table would couple two unrelated things.

**What it does mean:** `play_stats` stays its own query returning its own numbers, but
shares H's **play-resolution and track-set expansion** — the join through
`played_uri_track` and the mapping from an entity to its track ids. That is the only place
the two can genuinely disagree, and disagreeing there would mean one of them counts plays
for a track the other doesn't. The differing windows and weighting are deliberate and
should stay.

**The staleness rule is non-negotiable and survives unchanged:** a window whose start
predates `MAX(play.ts)` renders `—`, never a lying `0`. That behaviour belongs to
`play_stats` and is independent of anything here.

---

## 12. The prototype, and what implementation owes it

Tuning is **done** (§0.2). `docs/scoring/tuning_prototype.py` implements every stage of
§4–§8 against the real DB and produced every value in §10.1. It is the executable
reference: run it, and the implementation must reproduce its numbers.

The check that matters, and the one verification step that cannot be skipped: **the eleven
validation collections in §2.7 must come out in the order recorded there, at 2 inversions
of 47.** If the real implementation disagrees with the prototype, one of them is wrong and
the difference must be resolved deliberately rather than by re-tuning until the numbers
look nice.

**The acceptance test is the ordering and the inversion count — not the exact scores.**
Scores drift as grouping is curated: merging two version groups changes which tracks pool
together, so every collection containing them moves slightly. Several figures below already
shifted by ~0.1 during the split-ISRC cleanup on 2026-08-14. Treat them as a reference
snapshot, not as fixtures.

Reference output at the settled parameters, `GAMMA = 0.5`, on the DB as of 2026-08-14:

| display | collection | | display | collection |
|---:|---|---|---:|---|
| 87.0 | My playlist #134 | | 59.6 | Phoebe Bridgers |
| 81.8 | My playlist #149 | | 46.5 | The Weeknd |
| 78.8 | v37.2.1 | | 45.6 | 1984 (noah) |
| 73.3 | half•alive | | 44.7 | k-poop |
| 69.1 | Indie Rock Mix | | 32.5 | taking my airpods out asap |
| 66.2 | Schur | | | |

Version-score percentiles as displayed: p10 = 11.7, p25 = 14.4, p50 = 22.3, p75 = 48.9,
p90 = 74.9, p99 = 84.3.

Phoebe Bridgers and The Weeknd sit ~1–2 points below earlier drafts of this table because
`FEATURED_WEIGHT` is now actually applied (§5.3); they are the only two collections here
with featured-only credits. The inversion count is unchanged at 2/47.

Bucket baselines: **A = 0.024, B = 0.074, C = 0.530**. Bucket-A resolution: 3,680 distinct
scores across 5,434 versions (68%).

**The prototype is kept, not deleted**, matching the convention every `scripts/` one-off
follows. It is the only evidence for *why* the parameters are what they are, which is
otherwise unreconstructable.

**It does not touch ATG** (§0.1). The unbiased look happens after the parameters are
frozen, and is not part of this spec.

---

## 13. Out of scope

- **ATG cleanup.** Not a blocker (§0.1) and not part of this step.
- **Charting-library choice.** Stays deferred to F/G (§0.2).
- **Comeback behaviour as a report.** Belongs in F/G (§0.3).
- **Search ranking.** L's job; this step only replaces the ordering key at the existing
  capped sites (§11.1).
- **Algorithmic liked-songs, or anything else that would consume score.** Noted as a future
  dependent of the parameter-change discipline in §10, not built here.

---

## 14. Verification

- Every version, recording, release and track has a score for both horizons.
- A version with no plays, no memberships and no tenure scores **≈11 displayed** (0.012
  normalized — half of bucket A's 0.024 baseline, §8), not 0 and not NULL.
- A version with memberships and no plays scores meaningfully above zero and below an
  otherwise-identical version that has plays (§4.6).
- No ordering site listed in §11.2 has changed order.
- `impact` no longer appears in any ordering path.
- Score-ordered `representative()` still yields the pinned track where one is pinned.
- Recompute from a cold `/dev/scoring` button completes in the expected order of magnitude
  (§2.11), and a second run with no intervening writes changes nothing.
- Editing a parameter and recomputing changes scores; editing a parameter *without*
  recomputing is caught by the §9.3 backstop on the next page load.
- Both horizons render on all eight entity page types.
- **The eleven validation collections (§2.7) reproduce the order and scores in §12**, at 2
  inversions of 47. This is the acceptance test for the whole step.
- Bucket-A versions do not collapse to a single value — at least ~65% distinct scores
  (§4.4). A regression here means the weights drifted.
