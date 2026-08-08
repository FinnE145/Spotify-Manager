# Grouping catch-up — step E

D tripled the library and the review queues went with it: **810 unreviewed main
candidates and 541 unreviewed cross-artist ones**, at a few seconds each. This
feature closes 70% of the main queue deterministically, stops the prefill from
splitting songs it shouldn't, and rebuilds the cross-artist queue around the
question it actually asks.

Read `docs/canonical-tracks/detection.md` and `review-ui.md` first — this feature
changes both.

**No Spotify requests anywhere in this feature.** Nothing here reads or writes the
library.

---

## Measurements (2026-08-07)

Against `symr.db` at 9,693 tracks / 12,537 memberships / 503 reviewed pairs.

| | |
|---|---:|
| Unreviewed main candidates | 810 |
| — of those, 2-track | 658 |
| Unreviewed cross-artist candidates | 541 |
| Tracks in the main queue | 1,813 |
| Tracks in the cross queue | 1,631 |

**The 503 reviewed pairs are a validation baseline and this feature is the first
thing to use them.** Every rule below was scored against them before being
adopted. Re-score after any rule change — it is the only ground truth in the
project.

> ⚠️ **The baseline stops being independent the moment auto-group runs.** A run
> calls `mark_reviewed` over every group it closes, so its own decisions become
> `reviewed_pair` rows and the rule then scores against its own output. After
> the first real run the table went 491 → 1,195 pairs and the rule "passed"
> 806/806 — a number that means nothing.
>
> **Any future scoring must exclude pairs an auto run wrote.** There is no
> marker for them today; the run id would have to be recorded per pair, or the
> score taken against a snapshot of the human-only baseline. Until that exists,
> the last trustworthy figure is the one below, taken immediately before the
> first run. This matters beyond E: **H is meant to calibrate against this
> same data.**

### Amendments after implementation (2026-08-07)

The measurements below are what the spec was written against and are left as
the record. Two decisions taken during implementation moved them:

- **§0.3 landed**, removing 12 reviewed pairs. The baseline is **491 pairs**,
  and the auto-group rule scores **114/114** on it — 114 fires, zero
  disagreements at any tier. (The spec predicted 116/116; the correction
  removed two pairs the rule fired on.)
- **Recording identity gained an `explicit` guard** — same ISRC *and* same
  duration *and* same `explicit`. Clean and explicit are not the same
  recording, though they sound near-identical, so a clean/explicit pair is now
  same version / different recording. That drops 14 groups the rule would
  otherwise have closed: **554 of 812, leaving 258**, not 568 of 810.

### The auto-group rule, scored against those 503 pairs

Rule: **same ISRC + identical normalized full title + duration within 2,000 ms.**

| | |
|---|---:|
| Baseline pairs the rule fires on | 116 |
| Song-tier disagreements | 0 |
| Version-tier disagreements | 1 |
| Recording-tier disagreements | 1 |
| Release decided by "same normalized album" — mismatches | 1 |

All three disagreements are the **same pair** — `City of Angels - Neanderthal
Remix`, identical title, identical ISRC `QZK6H2019075`, both 210,000 ms, identical
album — which Finn has since called a mistake in the baseline, not in the rule. On
the corrected baseline the rule is **116/116**.

Loosening to bare ISRC equality (dropping the title and duration guards) produces
**7** recording-tier disagreements. The guards are what buy the accuracy; don't
drop them.

Applied live: **568 of 810 main groups close, covering 1,211 tracks, leaving 242.**
Cross-artist: 0 close, as expected.

### Rules considered and rejected

- **Feature-stripping the title before comparison** (ignore a trailing `feat.` /
  `with` clause). Buys 3 extra groups (568 → 571) and adds 2 baseline
  disagreements, both being pairs Finn wants to eyeball. Rejected: the auto-rule
  asserts certainty and stays maximally strict; feature-neutrality belongs in the
  prefill, which only suggests.
- **Duration as a version-tier signal.** Against the 207 reviewed pairs placed in
  the same song group, a pure duration threshold peaks at ~87% agreement (at
  3,000 ms) versus the ISRC rule's ~99%. Not adopted as a rule.
- **An outlier review queue** (auto-closed groups where durations differ >200 ms or
  primary-artist sets differ — 62 groups). Rejected: the one known error, City of
  Angels, has delta 0 and identical artists, so it would not appear. False
  reassurance.
- **Collapsing cross-queue newcomers by artist component.** 1,631 rows → 1,460, an
  11% reduction; average 3.0 → 2.7 rows per bucket. Not worth losing per-track
  visibility.
- **Artist aliasing to clear the cross queue.** If artists matched on normalized
  *name* instead of id, **0 of 541** cross buckets would collapse. The components
  have genuinely disjoint artist names. Aliasing cannot help here.

### Why the cross-artist queue is being rebuilt rather than tuned

**0 of 292** reviewed cross-artist pairs have ever been merged. The queue's hit
rate over its entire history is zero, and sampling the top of it shows why: `Home`
by six unrelated artists, `She` by Harry Styles / dodie / Ethan Gander, `Winter
Wonderland` by five crooners.

It is not being deleted, because the rare true positive is real and the data is
worth keeping clean. It is being rebuilt so that the overwhelmingly common answer
— "no, none of these are related" — costs one keypress instead of parsing four
tier columns across a dozen interleaved rows.

108 of the 541 buckets contain tracks that were already reviewed and are currently
re-scanned every time; those collapse into a separate section.

### Suffix survey

1,690 tracks carry a suffix; 1,093 distinct raw, 1,022 after permissive
normalization — normalization barely collapses the set, so the tail is genuinely
diverse and keyword-listing it exhaustively is not a winnable game.

131 main groups are split at song tier by the current prefill. **125 of those are
caused by an `unknown` suffix class**, not by artist logic. The reclassification in
§2 takes that to **51**.

---

## 0. Preliminaries

Three small things, first commit, before any of the rest.

### 0.1 The `live_count` phantom-membership bug

`canonical_detect.py:89`:

```sql
COUNT(CASE WHEN m.removed_at IS NULL THEN 1 END) AS live_count
```

On a `LEFT JOIN membership` with no matching row, `m.removed_at` is NULL, so the
`CASE` yields 1 and `COUNT` counts it. Every track with no membership reports
**one phantom membership**. That is **6,070 tracks** — the entire round-tripped
foreign set.

It was latent before D, when nearly every track had a membership. It currently
corrupts `impact` ordering and the live-count shown on every review card.

Fix:

```sql
SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_count
```

**This is the only occurrence.** The four other `removed_at IS NULL` call sites
(`app.py:304`, `canonical.py:246`, `canonical.py:366`, `canonical.py:382`) use
correlated subqueries or explicit `WHERE` clauses and are correct. Don't touch
them.

### 0.2 `_cleanup_tier` batching

`canonical._cleanup_tier` runs a full `canonical_group × track_group` LEFT JOIN
per tier on **every** `apply_partition` call. Over the 568-group auto-group run
that is 11.75 s, essentially all of it cleanup.

Give `apply_partition` a `cleanup=True` keyword. When `False`, skip the four
`_cleanup_tier` calls; the caller runs them once when its batch is done. Measured
on a copy of the real DB: **11.75 s → 1.15 s**, with byte-identical tier counts
and identical resulting queue sizes.

Only the auto-group run passes `cleanup=False`. Every existing caller keeps the
current behaviour by default.

### 0.3 Correct three reviewed pairs

Delete the `reviewed_pair` rows for these, so they re-enter the queues. A one-off
`scripts/` script, committed as the record of what happened, in the style of
`migrate_track_metadata.py`.

| Tracks | ISRC | Duration | Outcome |
|---|---|---|---|
| `6Iqs50fjfNdphBdsg2SLFs`, `1UgSHLYqInYkxcCbSx8LzI` — *City of Angels - Neanderthal Remix* | `QZK6H2019075` | both 210,000 | auto-grouped correctly by §3 |
| `7hxHWCCAIIxFLCzvDgnQHX`, `02kDW379Yfd5PzW5A6vuGt`, `1p0rEzrK7YtdRZVtiyV7RN` — *Lemonade* ×3 | `QZJ842000368` | all 195,428 | back to the main queue, prefilled merged |
| `4OkiWfrZKmmVoILXk8JEtl`, `124IHGAzY9F3unizZ08iRc` — *In The Morning* | `USQX91100167` | 234,386 / 234,192 | back to the main queue, prefilled merged |

Note *Lemonade* is **three** tracks sharing one ISRC, not two — the third is
`Lemonade (feat. NAV)`.

The two feat-clause cases deliberately do **not** auto-group (the strict rule sees
different full titles) and land in the queue with the merge pre-filled, one
keypress each.

Also delete any `track_group` grouping that only exists because of those pairs, so
the prefill starts from a clean state rather than `_same_real` re-asserting the old
decision.

---

## 1. Suffix normalization

`classify_suffix` currently matches keywords against a case-folded but
**punctuated** string. `_strip_accents` uses NFKD, which does not fold `’` (U+2019)
to `'`. So `(taylor’s version)` never matches the `taylor's version` keyword, and
all 14 such tracks fall through to `unknown`.

Add `normalize_suffix(s)`, used by `classify_suffix` and by the auto-group rule's
title comparison:

1. `_strip_accents`, then `casefold`
2. replace every character that is not a letter, digit, or space with a **space**
3. collapse runs of whitespace, strip

**Keep digits.** `1947 version`, `remastered 1999` and `99 luftballons` all need
them; dropping digits was considered and rejected.

Replacing punctuation with a space rather than deleting it matters: `taylor’s` →
`taylor s`, so keywords must be written in the same normalized form
(`taylors version` will not match `taylor s version` — write the keyword as
`taylor s version`, or match on tokens).

This barely collapses the distinct-suffix count (1,093 → 1,022). Its value is
correctness, not compression.

---

## 2. Suffix classes and prefill

### 2.1 Classes

The class set becomes **`base` · `version` · `recording` · `neutral`**.
`undecided` and `unknown` are both gone: `instrumental` moves to `version`,
everything unrecognised becomes `neutral`.

| Family | Class |
|---|---|
| *(no suffix)* | `base` |
| `feat.` / `ft.` / `featuring` / `with` | `neutral` |
| acoustic, live, remix, demo, instrumental, cover, nightcore, piano, orchestral, stripped, sped up, slowed, reprise | `version` |
| generic `… version` — jazz, guitar, original, 1947 | `version` |
| session / venue — long pond studio sessions, recorded at spotify studios, unplugged, voice memo, the voice performance | `version` |
| `arr …` classical arrangements | `neutral` |
| remaster, remastered, taylor s version, mono, stereo, clean, explicit, radio edit, single version, album version, deluxe, anniversary, extended | `recording` |
| interlude, skit, bonus track, edit, soundtrack and franchise markers, alternate title parts, uncredited artist names | `neutral` |
| anything unrecognised | `neutral` |

Two of these were close calls, decided deliberately:

- **`arr …` is `neutral`, not `version`.** An arrangement usually *is* a different
  performance, but the classical entries in this library are inconsistent enough
  that Finn would rather decide them himself than have the prefill guess.
- **Bare `- edit` is `neutral`**, folded into the general clump rather than given a
  rule of its own. It's 3 tracks; a special case isn't worth it. Note `radio edit`
  is still `recording` via its own keyword.

**`instrumental` as `version` is safe** even though an instrumental *cover* is
genuinely a different song, not a different version. A cover is by a different
artist, so it has no shared primary artist and can never merge at song tier — it
goes to the cross-artist queue and gets rejected there. The `version` class only
ever affects same-artist tracks, which is exactly the "instrumental version of
their own song" case.

### 2.2 Prefill changes

**Song tier merges by default.** `same_song` drops the `_eligible` gate entirely:
two tracks in a candidate group merge at song tier whenever they share a
**primary** artist id, whatever their suffix class. A shared *featured* credit is
still not enough, and disjoint primary artists still never merge — those two rules
are unchanged and are what keeps covers apart.

This is the change that takes wrong song-tier splits from **131 to 51**. It also
means the prefill now guesses where it used to abstain: an unrecognised suffix
lands in the same song group and has its version and recording decided by ISRC,
duration and album, rather than sitting singleton at all four tiers. That is the
intended trade — an obviously-related track sitting alone was the more annoying
failure.

**Version tier.** `shares_base_version` is true for `base`, `recording` and
`neutral`, false for `version` — two different live cuts remain two different
things. Unchanged in spirit; `neutral` simply joins the set that `base` and
`recording` were already in.

**Version tier gains a nesting fix.** Today `same_version_group` consults only
`_same_real` and `shares_base_version`, so two tracks that are the *same recording*
by ISRC but both carry a `version`-class suffix (two rows of the same
`(Live)` track) land in different version components and can then never merge at
recording, because `assign_recording_release` runs scoped inside a version
component. Add: `same_version_group` also returns true when `_same_recording` or
`_same_release` holds. Same recording implies same version by nesting.

**`_same_recording` compares normalized suffixes.** Its `ra["suffix"] ==
rb["suffix"]` branch must use `normalize_suffix`, not the raw string.

**Recording and release tiers otherwise unchanged**, minus the `_eligible` gate,
which no longer exists.

---

## 3. Deterministic auto-group

### 3.1 The rule

A pair `(a, b)` **matches** when all of:

- both ISRCs are non-null and equal
- normalized base titles are equal **and** normalized suffixes are equal
- both durations are non-null and differ by ≤ 2,000 ms

A candidate group **auto-closes** when the rule matches on **every** pair in the
group. Partial matches close nothing — a 3-track group where the rule fires on two
pairs of three stays in the queue whole.

### 3.2 What it writes

Per auto-closed group, one `apply_partition` call with `cleanup=False`:

- one shared **song** label
- one shared **version** label
- one shared **recording** label
- **release** label keyed on the normalized album name, so tracks on the same album
  share a release and tracks on different albums don't

then `mark_reviewed` over the group's tracks. After the loop, four `_cleanup_tier`
calls and one commit.

Main queue only. The cross-artist queue is untouched — no group in it closes under
this rule anyway.

### 3.3 The button

On `/dev/canonical`. Two steps:

1. **Preview** — `GET /api/canonical/autogroup/preview` runs the rule without
   writing and returns the counts. Render as *"This will resolve 568 of 810 queue
   items, leaving 242."*
2. **Confirm** → `POST /api/canonical/autogroup` runs it.

Synchronous, with a plain client-side spinner. It takes ~1.2 s with §0.2 in place;
it does not need the job slot, a progress bar, or a background thread.

The button stays available permanently — it is not a one-time migration. It is
also the reason the §2 prefill improvements matter beyond this catch-up: both keep
applying to every future pull.

### 3.4 Run log and undo

`auto_group_run(id, started_at, finished_at, groups_closed, tracks_affected)`.

**Undo is a whole-table snapshot, restored wholesale.** Before the run, copy
`track_group`, `canonical_group` and `reviewed_pair` into three snapshot tables
keyed by run id. Undo deletes the three live tables' contents and re-inserts from
the snapshot.

This is deliberately blunt rather than clever. The alternative — recording per-track
prior group ids — has to reason about groups that `_cleanup_tier` deleted and about
tracks outside the run that shared a prior group with a track inside it. The tables
are 9,693 + 38,321 + 503 rows, a few MB. Copy them.

**Only the most recent run's snapshot is kept.** Running auto-group again replaces
it. Undo is one level deep; say so in the UI.

### 3.5 Viewer badge

`canonical_group.auto_run_id INTEGER NULL`. After a run completes, tag exactly the
groups it decided.

> ⚠️ **Corrected during implementation.** This section originally specified:
>
> ```sql
> UPDATE canonical_group SET auto_run_id = ?
> WHERE id NOT IN (SELECT id FROM <the snapshot of canonical_group>)
> ```
>
> **That tags nothing.** It assumes a run *creates* groups, and it doesn't:
> `apply_partition` step 4 reuses an existing group id whenever a part's
> members fully cover one, and a singleton group always qualifies. So a run
> overwhelmingly re-points existing ids and deletes the orphans — the real run
> took `canonical_group` **down** from 38,321 rows to 36,173. An id-diff
> against the snapshot matched **zero** groups.

Take the ids from what `apply_partition` actually wrote instead — it returns
the final tier ids for every track it touched, including anything its closure
pulled in — and tag those. On the real run that is 2,653 groups: 568 song, 568
version, 568 recording and 949 release, with no singleton song groups among
them.

`/dev/canonical` renders a small badge on any group with a non-null
`auto_run_id`, so a group that looks wrong while browsing is identifiable as
machine-decided. A later manual edit that reconciles the group away takes the
flag with it, which is correct.

No spot-check flow, no sampling UI, no outlier queue. The audit that matters
already happened: 114/114 against real decisions is stronger evidence than
anything a post-hoc skim would produce.

---

## 4. Cross-artist queue rework

New route `/dev/canonical/cross`, new template `canonical_cross.html`, new
`static/js/canonical_cross.js`. The old `?queue=cross-artist` mode of
`/dev/canonical/review` is removed.

Bucket membership and ordering are unchanged: a bucket appears when any
cross-component pair is unreviewed (`_cross_component_reviewed`), ordered by the
existing `_order`.

### 4.1 New vs established

A track in a bucket is **established** if it has at least one `reviewed_pair` with
another track in the *same bucket*. Otherwise it is **new**.

The asymmetry is the point. When a newcomer arrives in a settled bucket, only the
newcomer is new — the tracks it creates fresh unreviewed pairs against stay
established and stay collapsed. On a bucket's first appearance nothing is reviewed,
so everything is new, which is the right answer for a bucket like `home` (7 tracks,
none reviewed).

A track that was reviewed and left **ungrouped** is still established. Having
decided it doesn't belong with the others, you don't want to be asked again — you
only need it visible in case a future newcomer belongs with *it*.

### 4.2 Layout

Two sections.

**New songs** — one row per new track: cover thumbnail, full unnormalized title,
artists, album, duration, ISRC, not-in-library badge (§5), selection checkbox.

**Existing groups** — one row per song group containing at least one established
track from the bucket: representative cover, representative title, track count,
distinct artists, and album names truncated with `…`. The count and albums cover
the **whole song group**, including members outside this bucket — that's correct
and is what tells you what you'd be joining.

**Same-artist newcomers are not assignable.** A new track that shares a primary
artist with an established group already forms an unreviewed *main*-queue candidate
with it, by construction — same base title, shared artist id, unreviewed pairs. Ask
about it here and you're doing the main queue's job twice. Render it nested under
its group's row, no checkbox, labelled with its actual state (already queued in
main, or already decided there).

Worked example, from Finn's own walkthrough. Four new `willow` tracks — two Taylor
Swift, one BBB, one Jasmine Thompson — against existing TS and JT groups:

```
New songs:
  Willow | BBB | AlbumBBB

Existing groups:
  Willow | 5 tracks | TS | Single, evermore, lonely witch edition…
      ↳ Willow             | TS | Willow (Single)     — queued in main
      ↳ Willow (acoustic)  | TS | evermore            — queued in main
  Willow | 2 tracks | JT | Single, album…
      ↳ Willow             | JT | Willow              — queued in main
```

One question on the page, not four.

### 4.3 Assigning

Select one or more **New songs** rows, then either attach them to an existing group
row or group them with each other when there's no existing group to attach to.
Assigned rows move under their target and can be unassigned before saving.

Keys, consistent with `review-ui.md` where they overlap:

| Key | Action |
|---|---|
| `↑` / `↓`, `j` / `k` | move focus |
| `Space`, click | toggle selection |
| `1`–`9` | assign the selection to the *n*th existing group row |
| `g` | group the selected new songs together as a new song group |
| `u` | unassign the focused row |
| `Enter` | save and advance |
| `Backspace` | discard and go back |

`Enter` with nothing assigned is the "none of these are related" answer, which is
the answer 292 times out of 292 so far. That's the one keypress the whole rework
exists to produce.

### 4.4 What save writes

1. **The song-tier merge, immediately** — `apply_partition` with every track in an
   assignment sharing one song label, each keeping its **current** version,
   recording and release group ids. The decision is durable the moment it's made;
   it does not wait on the tier pass.

   > ⚠️ **Corrected during implementation.** This originally said the newcomer
   > gets *singleton* finer-tier labels. It must not: a newcomer is often
   > already in a real version/recording/release group of its own, and handing
   > it fresh singletons would silently detach it from that group as a
   > side-effect of a song-tier decision. Passing its current ids back is what
   > makes the write purely additive at song tier. Where a newcomer genuinely
   > has no finer grouping its current ids *are* singletons, so the original
   > wording was right only for that case.
2. **A `pending_tier_review(track_id)` row per assigned newcomer.**
3. **`mark_reviewed` over every pair in the bucket** — assigned or not — so the
   bucket doesn't resurface until another newcomer arrives.

### 4.5 `pending_tier_review`

```sql
CREATE TABLE pending_tier_review (
  track_id TEXT PRIMARY KEY REFERENCES track(track_id)
);
```

Keyed on **track id, not group id.** A cross-artist assignment is not derivable
from title or artist overlap, so detection can never regenerate it — it has to be
stored. But group ids are reconciled by `apply_partition` and a group can be
absorbed into another, leaving a stored group id pointing at nothing. Track ids
never move.

The queue reads each row as *"review whichever song group this track is in right
now"*, resolving through `track_group` and serving a full candidate item over that
group's current members. Two newcomers landing in the same group produce two rows
that resolve to one item — **dedupe by resolved song group at read time**. A row is
deleted when its item is saved.

> ⚠️ **Corrected during implementation.** This originally said `ad_hoc_group()`.
> It can't be: `ad_hoc_group` pre-fills nothing and renders the tracks' saved
> grouping, which here is four singleton chips per row — the exact state being
> reviewed. The pending queue exists to *assign* the finer tiers, so the
> prefill has to run. It serves `_make_candidate_group` instead. The song tier
> still comes out shared, because `_prefill_labels`' `same_song` consults
> `_same_real` first and every member is already in the one song group, so the
> assignment stands and the prefill only proposes below it.

### 4.6 The tier pass

`/dev/canonical/review?queue=pending`, reusing the existing review UI unchanged —
the items are ordinary candidate items with the assignment already applied at song
tier and the prefill filling in below it (see §4.5).

Reached two ways:

- **Redirect** when the cross queue empties, which is the flow Finn described.
- **A link and count on `/dev/canonical`** — *"3 groups awaiting tier review"*.

The link is what stops work stranding. The cross queue gets worked in sittings, and
a redirect that only fires on the last item would leave pending assignments
unreachable until some future session happened to finish the queue.

---

## 5. Not-in-library badge

A track with no `membership` row where `removed_at IS NULL` — i.e. `live_count = 0`
once §0.1 lands — renders a small muted badge in the main review UI, the cross
queue, and the `/dev/canonical` viewer.

6,070 of 9,693 tracks are in this state. Without the badge a group of tracks you
recognise gives no hint that none of them are actually in a playlist, and a
group that ought to be attached to an owned track looks settled when it isn't.

---

## 6. Docs this feature must update

- **`docs/canonical-tracks/detection.md`** — the suffix classes, the normalization,
  song-tier merge-by-default, the version-tier nesting fix, the auto-group rule.
- **`docs/canonical-tracks/review-ui.md`** — remove the `?queue=cross-artist` row,
  add `?queue=pending`, note the not-in-library badge.
- **`docs/Planning/roadmap.md`** — mark E landed, pointing at this
  spec, and add to **H**:
  > `impact` (currently membership count, and before step E a broken one) is a
  > placeholder for the score. When H lands, replace it as the queue ordering.
  > The score must be **aggregation-comparable across arbitrary group sizes** — a
  > song, an album, an artist's discography and a playlist all need to be
  > rankable against each other. Naive averaging of per-song scores fails:
  > there are albums that are genuinely top-10 where only half the tracks get
  > played, and averaging drags them to mid. Per-song inputs in play: plays,
  > memberships, tenure, recency.
- **`CLAUDE.md`** — the codebase map gains `canonical_cross.html` /
  `canonical_cross.js`, and the `canonical.py` entry gains the `cleanup` keyword.

---

## 7. Out of scope

- **Main-queue review card layout.** The original brief asked whether scanning
  could be made faster. With the queue at 242 and the prefill splitting far less,
  Finn's call is that per-item thinking time is now acceptable. Revisit only if it
  still bites after working the real queue.
- **Deciding the cover question.** Whether a cover is the same song at song tier
  stays open. Covers remain split by default; the reworked cross queue makes
  merging one cheap on the rare occasion it's wanted.
- **Artist aliasing work.** Measured to have no effect on the cross queue (0 of
  541). Nothing here touches `artist_alias`.
- **Replacing `impact`.** Deferred to H, noted above.
- **Auto-grouping the cross-artist queue.** No rule proposed for it, and 0 of 292
  historical merges gives nothing to calibrate one against.
