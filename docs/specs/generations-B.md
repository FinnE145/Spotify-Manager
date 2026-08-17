# Generations & Tenure

**Step B of `docs/Planning/roadmap.md`.** Establishes the *generation* — one current-favs
playlist in the chain of 36 — as a first-class thing in the DB, and derives **tenure** from it.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).

## Read first

- `docs/Planning/roadmap.md`, the B section — this spec supersedes it where they differ
  (see *Corrections to the roadmap* at the end).
- `docs/specs/canonical-tracks.md:55-65` — the consumer table already committed this
  feature to the **version** tier with an opt-in **song** toggle. This spec honours that.

## Terminology — tenure vs membership

These two are deliberately distinct and must not be used interchangeably, in code,
comments, or UI copy:

- **Membership** — a track's presence in *any* playlist. The existing `membership` table.
  Unchanged by this spec.
- **Tenure** — a version group's presence in the *generations* of current-favs playlists,
  and nothing else. Measured in **generations first, days second**.

Why generations are the primary unit: a generation is **attention-weighted time**. A
playlist lasts longer when Finn is spread thin between listens and sours on songs more
slowly, and shorter when he's listening heavily. Counting generations therefore gives a
song that survived a dense listening period and one that survived a sparse period the
same weight — which is the intended meaning. Days are kept as a secondary, literal number.

## What a generation is

One playlist in the chain of current-favs playlists, numbered 1–36. A minor or patch bump
**renames the active playlist in place** (same `playlist_id`, so no new generation); a major
bump **creates a new playlist**, which is a new generation.

### The 36 — verified

Matched by name against `snapshot` and ordered by earliest `added_at`. All 36 names resolve
to exactly one playlist each; the chain is strictly chronological with no overlaps or
inversions. **From position 25 onward the ordinal equals the major number in the playlist
name** (position 25 is `25`, position 30 is `v30.1.2`, position 36 is `v36.4.2`) — independent
confirmation the chain is complete at the tail. Positions 1–24 are the pre-numbering era of
the same sequence.

| # | Playlist | # | Playlist | # | Playlist |
|---:|---|---:|---|---:|---|
| 1 | Songs I Wanna Listen To Rn | 13 | favourites 4 | 25 | 25 |
| 2 | Songs I wanna listen to 2 | 14 | favourties 5 | 26 | 26 |
| 3 | Songs I wanna listen to 3 | 15 | favourites 6 | 27 | 27 |
| 4 | Good Songs | 16 | favourites 7 | 28 | 28 |
| 5 | Fairly Ok Songs | 17 | music idk | 29 | 29 |
| 6 | Pretty Good Songs | 18 | music 2 | 30 | v30.1.2 |
| 7 | music im sick of | 19 | music 3 | 31 | v31.7.0 |
| 8 | (no longer) current music | 20 | music 4 | 32 | v32.23.5 |
| 9 | current music | 21 | music 5 | 33 | v33.14.3 |
| 10 | favourites | 22 | music 6 | 34 | v34.12.4 |
| 11 | favourites 2 | 23 | music 7 | 35 | v35.18.1 |
| 12 | favourites 3 | 24 | music 8 | 36 | v36.4.2 (active) |

`favourties 5` is a real generation with a typo. `music im sick of` and
`(no longer) current music` were **renamed posthumously**, when the next generation was
created — a posthumous name does not disqualify a generation. Naming cannot be
pattern-matched; this list is the authority.

**Deliberately excluded**, confirmed not generations: `all time favourites` (an ATG
predecessor), `My music is better`, `songs`, `✨Leafy✨`.

### Spans

A generation's span **starts** at the earliest `added_at` of its live memberships and
**ends when the next generation starts**. The spans therefore tile with no gaps: a new
generation begins by copying forward everything from the previous one that's still liked,
so there is no dead time between them. The **active** generation's span is open — it ends
*today*, and tenure inside it is still accruing.

### The pre-generation era is out of scope

Finn All starts 2020-02-13, generation 1 starts 2021-02-09. **57 Finn All tracks predate
generation 1**: 42 from a single bulk seed on 2020-02-13 (all sharing one `added_at`), and
15 added in the weeks leading up to generation 1. 29 carried into generation 1; **28 did
not, and not one of those 28 ever appears in any of the 36 generations.**

So tenure starts at generation 1 and a Finn All add date has no bearing on it. Those 28
tracks have tenure 0, which is the honest answer — they never entered the system. **Any UI
showing tenure alongside an add date must not imply the two are related**, or "added
2020-02-13, tenure 0" reads as a bug.

## Data model

### `generation` (new table, in `SCHEMA`)

```sql
CREATE TABLE IF NOT EXISTS generation (
    ordinal INTEGER PRIMARY KEY,
    playlist_id TEXT NOT NULL UNIQUE REFERENCES snapshot(playlist_id)
);
```

Two columns and nothing else. In particular **no name column** — the display name is
`snapshot.name`, which already tracks in-place renames, so a generation naturally ends up
showing whatever version it reached (`v36.4.2`, not `v36.0.0`).

`ordinal` is **stored, not derived from sort order.** It is the generation's identity, not a
computed artifact: for 25–36 it is literally the major number Spotify's own playlist name
carries. Deriving it by sorting on earliest `added_at` would let an inserted mid-chain
generation silently renumber everything above it — changing the history of the generations
should require a deliberate edit, not fall out of a table sort.

### `snapshot.generation_declined` (new column)

`INTEGER NOT NULL DEFAULT 0`. Added to `SCHEMA` **and** to `_migrate` as an additive
`ALTER TABLE`, following the existing pattern. Set when Finn answers "no" to the
new-generation prompt, so it stops asking on every subsequent pull.

### `generation_presence` (new view, in `VIEWS`)

```sql
CREATE VIEW generation_presence AS
SELECT DISTINCT g.ordinal, m.track_id, tg.version_id, tg.song_id
FROM generation g
JOIN membership m ON m.playlist_id = g.playlist_id AND m.removed_at IS NULL
JOIN track_group tg ON tg.track_id = m.track_id;
```

`DISTINCT` matters — `membership` is an append-only log, so one track can hold several rows
for the same playlist.

Editing `VIEWS` changes its hash, so `_ensure_views` rebuilds automatically on next start.

**Trap:** the join to `track_group` silently drops any track without a `track_group` row.
Every read path here must call `canonical.ensure_track_groups(conn)` first, exactly as the
canonical pages do.

## Tenure semantics

### What counts as present

A **version group** is present in generation *N* if **any** of its tracks holds a live
membership in that generation's playlist. That also decides the carried/new split: a group
is "carried" if it was present in generation *N−1*, even if a *different track id* of the
same version was the one carried forward.

### Removals never happened

A track with `removed_at` set **is treated as never having been in that generation at all** —
not as having been there and left. A patch removal means the song shouldn't have been added
in the first place, not that it was liked for a while and then soured on. This also keeps
the modern generations consistent with the historical ones, where removals are simply
unknowable. (Only 3 `removed_at` rows exist across all 36 generations today, so this changes
almost nothing now, but it fixes the meaning going forward.)

### Runs, and the three numbers

Collapse a group's set of generation ordinals into **runs** of consecutive generations. A
group present in 5, 6, 7, 10 has two runs: 5–7 and 10.

| Number | Definition | Meaning |
|---|---|---|
| **tenure** | length of the **longest run**, in generations | the headline |
| **total generations** | sum of all run lengths | breadth — "has been around" |
| **runs** | number of runs | the comeback signal |

The longest run is the headline because a return is only *sometimes* a genuine second
tenure — often it's just "oh, I forgot about this one!" — and nothing in the data
distinguishes the two. The longest run never over-credits: it is the longest stretch the
song was continuously worth carrying forward, which is exactly what tenure means here.

**First→last span is explicitly not tenure** (it counts the gaps) and must not be presented
as such. Store and expose every run, though, so a later consumer can decide differently
without re-deriving anything.

**Ties for longest run favour the earliest one** (noted during P1, P1-016 — the code's own
docstring already documented this, just never folded back here): a group present in just two
non-consecutive generations is two length-1 runs, tied, and it's common. The reported `days` comes
from whichever tied run appears first in run order — i.e. the oldest — not the most recent.
(`first_ordinal`/`last_ordinal` are unaffected — those are the min/max over *every* ordinal the
group was ever present in, not just the winning run.) Worth stating explicitly since `days`
silently favors the older tie.

### Tenure in days

Per run: from the start of the run's first generation to the **end** of the run's last
generation — i.e. the start of the generation after it, since spans tile. If the run's last
generation is the **active** one, the span ends **today** and is still accruing. Report days
for the longest run alongside the generation count.

### No right-censoring here

Tenure is a **raw measurement**. A song appearing only in generation 36 has tenure 1, full
stop — its age is irrelevant at this layer. Right-censoring belongs to the consumers that
interpret tenure (step H), and this spec deliberately provides no censoring flag.

**The spec must carry this warning forward:** a low tenure on recent material means "hasn't
had the chance yet", not "failed". A consumer that skips that distinction will read new
songs as bad ones.

### Rollup tier

Tenure computes at the **version** tier by default, with **song** as an opt-in. Acoustic and
studio cuts are separate tenures; the single and album releases of one recording are the
same tenure. The tier is a **parameter to the backend function**, not two code paths — the
toggle exists at the *display* level so any future page can ask for version, song, or both
independently.

## Read path — SQL where it's strong, Python where it is

Derived at query time. **Nothing is materialized**, so nothing can go stale.

The deciding factor is that tenure keys on `version_id`, and version ids change *every time
a group is reviewed in the canonical queue*. A stored table would need invalidating after
pulls, after every grouping write, and after generation curation — three paths to get right
for a speedup that isn't perceptible at this scale (~4,300 live memberships across the 36
generations, ~2,171 distinct version groups).

The split: **the view does the joins and tier resolution; Python collapses the runs.** Pure-SQL
gaps-and-islands is a window-function puzzle, and a view can't take the tier parameter
anyway — in Python it's a few readable lines and the tier is just an argument.

### `generations.py` (new module)

- `generations(conn, tier="version")` → the generation list, ordered by ordinal. Per row:
  `ordinal`, `playlist_id`, `name` (from `snapshot`), `started_at`, `ended_at` (the next
  generation's start; `None` for the active one), `group_count`, `carried_in`, `new_in`,
  `survived_out` (how many of its groups appear in the next generation). Counts are at the
  requested tier. (Signature corrected during P1, P1-016 — the `tier` parameter existed in the
  adjacent prose but was missing from this bolded signature.)
- `tenures(conn, tier="version")` → one entry per group ever present in a generation:
  `group_id`, `runs` (list of `(start_ordinal, end_ordinal)`), `tenure`, `total_generations`,
  `run_count`, `first_ordinal`, `last_ordinal`, `days` (for the longest run).

`tier` is `"version"` or `"song"` and selects the view column. Reject anything else rather
than interpolating it into SQL.

Both functions read only; neither commits.

**The module's public surface is three functions larger** (noted during P1, P1-016):
`generation_spans()`, `runs()`, and `presence_for_tracks()` all now exist and are consumed by
step K's entity pages — none mentioned here. `generation_spans()` factors out the per-generation
span-tiling both `generations()` and `tenures()` share; `runs()` is the public run-collapsing
helper described above, callable directly rather than only through `tenures()`;
`presence_for_tracks(conn, track_ids)` gives the sorted ordinals any of a track set was present
in — the strip every K entity page renders.

### Display resolution

Compute tenure for **all** groups in Python (trivial over ~4,300 presence rows), sort, then
**slice to the visible page**, and only then resolve display strings — via the existing
`canonical.representative()` and `canonical.track_display()`, for the ~100 rows on screen.

This ordering is load-bearing. `representative()` runs correlated subqueries per member and
calling it 2,171 times would be slow; calling it 100 times matches what `/dev/canonical`
already does. **Do not add a batch representative helper, and do not refactor
`canonical.song_groups()`** — both are out of scope.

## Seeding the 36

`scripts/seed_generations.py` — a standalone one-off following the existing `scripts/`
conventions (own DB connection, own arg parsing, commit as you go). It holds the ordered
list of 36 playlist **names** from the table above and inserts `(ordinal, playlist_id)` rows.

- Resolve each name to exactly one `snapshot` row. **Abort the whole run** if any name misses
  or matches more than once — a partial seed is worse than none.
- Idempotent: `INSERT OR IGNORE`, safe to re-run.
- Print the resolved chain (ordinal, name, start date) so the result is eyeballable.

Kept afterwards as the record of what happened, like `migrate_track_metadata.py` — not
meant to be re-run.

**No UI picker.** The page detects and confirms *new* generations (below); it does not offer
a way to bulk-assign the historical ones, because that's 36 clicks of work that would never
be repeated.

## Detecting a new generation

A new major creates a new playlist, so after a pull there may be one that should become
generation 37.

**Detection** (a query, not stored state): any `snapshot` row whose name matches
`^v(\d+)\.\d+\.\d+$`, whose major number is not already a `generation.ordinal`, and whose
`generation_declined` is 0.

**Multiple simultaneous pending generations** (undocumented policy, noted during P1, P1-016):
`pending_new_generation()` collects every candidate but surfaces only the lowest-major one at a
time; the rest come up in turn as each is resolved (confirmed, per the function's own docstring).
Ratified as-is — a real, if unlikely, multi-pending state resolves itself one at a time with no
change needed.

**Confirmation** — a yes/no line rendered on **`/dev/snapshot`** (where the pull output is,
so it's in front of Finn right after a pull) and also on **`/dev/generations`**. Never
silent, never automatic:

> `v37.0.0` looks like a new generation 37. Add it? **[Yes] [No]**

- **Yes** → insert `(37, playlist_id)`. The ordinal comes from the major number in the name,
  so it never needs typing.
- **No** → set `snapshot.generation_declined = 1`, and stop asking.

**Known limitation, documented as current behavior (P1-016):** the insert is `INSERT OR IGNORE`
— a conflicting ordinal or playlist id is silently swallowed, and Finn is redirected as if it
succeeded. No spec clause covers this failure mode. Left as-is: it needs two colliding
confirmations to ever trigger, which hasn't happened and isn't expected to.

Implement as a plain HTML **form POST** to `/dev/generations/confirm` that redirects back to
the referring page. That keeps it identical on both pages with no duplicated JS, and avoids
native browser dialogs entirely (they're suppressed in the in-app browser).

The regex only matches the modern `vXX.Y.Z` scheme, which is correct — every future
generation uses it, and the historical schemes are handled by the seed script.

## UI

~~All of this lives under `/dev`.~~ **Stale (P1-016):** true for the pages *this spec* built, but
tenure numbers and the 36-cell strip now render on the public entity pages and the playlist
generation view too, per step K — this feature's data outgrew `/dev` even though the standalone
`/dev/generations*` pages themselves didn't move. It's DB monitoring and setup, the same as the
rest of the dev pages; it can graduate to the main navbar later if ongoing features accrue around
it. **No change to `base.html`'s navbar.**

Add an entry to `templates/dev.html`'s list:

> **Generations** — the 36 current-favs playlists and the tenure derived from them.

### `/dev/generations` — the generation list

`templates/generations.html`, `active="dev_generations"`.

- The new-generation confirmation line, when one is pending.
- A table of the 36: **number** ("Generation 12"), playlist name, date span, group count,
  carried-in / new split, and how many survived into the next generation.
- The version/song tier toggle (`?tier=`), affecting the counts.
- A link to the tenure table.
- Each row links to a **per-generation page**, which for now is a `coming_soon.html` stub.
  Step K folds it into the playlist page as a "generation view" toggle rather than growing a
  second thing to maintain.

### `/dev/generations/tenure` — the tenure table

`templates/generations_tenure.html`, same `active`.

One row per version group ever present in a generation (~2,171). Columns: representative
track (name + artists, via `track_display`), **tenure**, **total generations**, **runs**,
first and last generation, days.

**Gained a Score column and a fourth sort mode** (noted during P1, P1-016; expected addition from
step H, never folded back here): every row's `scoring.song_scores()`/`scores_for_tier()` value is
computed before sorting, over *every* row, not just the visible page — real whole-library work
this section's own Performance note (below) never budgeted for.

- **The strip.** A 36-cell row per track showing which generations it was in — filled or
  empty — making gaps and comebacks readable at a glance. Render as **real table cells** with
  hover identifying the generation.
- Sortable by tenure, total, or runs, via query params; server-rendered, default tenure
  descending.
- Paged at 100 rows.
- The tier toggle applies here too.

> **Performance note.** 100 rows × 36 cells = 3,600 elements per page, and Jinja rendering is
> the standing performance suspect (detection itself measured sub-linear). This is the
> deliberate first cut — measure it before optimising. If it drags, the fallback is to render
> each strip as a single 36-character monospace string in one span, which drops the page to
> 100 nodes; don't do that pre-emptively.

## Routes

| Route | Endpoint | Purpose |
|---|---|---|
| `GET /dev/generations` | `dev_generations` | generation list |
| `GET /dev/generations/tenure` | `dev_generations_tenure` | tenure table |
| ~~`GET /dev/generations/<int:ordinal>`~~ | ~~`dev_generation`~~ | **Stale (P1-016)**: gone, exactly as this row's own note predicted — absorbed into `/playlist/<id>?generation=1` by step K. |
| `POST /dev/generations/confirm` | `dev_generations_confirm` | accept/decline a detected generation, then redirect back |

No `/api/*` endpoints and ~~no new JS file~~ — every interaction is a link or a form POST.
`/dev/snapshot` gains only the rendered confirmation line, sharing a macro in `_macros.html`
with the generations page.

**"No new JS file" is stale (P1-016):** `static/js/generation_confirm.js` now exists, added by
`async-recompute-N.md` §7.2 for click feedback on the confirm form. The underlying mechanism this
section describes — a plain form POST, no fetch, no JSON — is unchanged; the JS only disables the
buttons and relabels the clicked one, it doesn't change what gets submitted.

## Out of scope

- **Intent score and adoption stagger.** Both are pure functions of tenure and `added_at`, so
  both come free later; neither is built here. Intent score moves to step H (scoring);
  adoption stagger moves to F/G, because it exists to *discover a mechanism* that drives a
  score — feeding it back into the score would make the score predict itself.
- **Right-censoring flags** — consumers' business (see above).
- Any change to `membership`, `track`, or the canonical tables. This feature is read-only
  over all of them.
- Batch representative resolution, or refactoring `canonical.song_groups()`.
- "In Finn All but never in any generation" — a real report, but it belongs elsewhere.
- The per-generation detail view, which step K absorbs.

## Corrections to the roadmap

The B section of `docs/Planning/roadmap.md` says *"Membership is manually listed once (~36
entries)"* — confirmed exactly 36, and the list is now verified rather than approximate. Its
*"Active spans from earliest `added_at`"* claim is confirmed: the chain is clean, chronological,
with no ties or inversions, and the ordinal matches the major number from 25 on.

Two of its four listed yields — **intent score** and **adoption stagger** — are moved out of B
(to H and F/G respectively), and the **right-censoring flag** is dropped from this layer
entirely, leaving B as generations + tenure. The roadmap is updated to match in a separate
commit.

Two anomalies found while verifying, recorded here because they'll look like bugs on the
page: **generation 30 (`v30.1.2`) carried 103 of its 106 tracks from generation 29**, and
**generation 3 carried 100 of 105 from generation 2** — near-total copies, so those majors
were cut almost immediately. They are real, not a seeding error.
