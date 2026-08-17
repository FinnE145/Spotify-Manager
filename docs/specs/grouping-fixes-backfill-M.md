# M — Grouping-review fixes + album backfill

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`), finding P1-017. M1/M1b/M1c confirmed matching exactly. M2 (album backfill) had 8 documentation gaps, mostly about the exact edges of its "derived, checkpoint-free" guarantees, plus two deliberate rulings on where those guarantees stop — both noted inline where they occur.

**Step M of `docs/Planning/roadmap.md`.** Four things in one step: three defects in the
canonical-review surfaces (two of which silently produce *wrong groups*), and the album
backfill that M1 in particular gates.

They ship together because they are one implement session's worth of work and they touch
overlapping files, not because they interact. M1/M1b/M1c are independent bug fixes; M2 is
a new feature that reuses machinery K and D already built.

---

## 0. What planning corrected in the roadmap

The roadmap's step-M section was written 2026-08-14. Planning re-measured on **2026-08-15**
and four of its claims changed.

### 0.1 M1's fix is narrower than the roadmap proposed

The roadmap's fix direction was "mark only the pairs the queue actually asked about
(newcomer vs existing group members)". That is close, but the sharp version is better:
`_cross_component_reviewed` (`canonical_detect.py:509`) — the *only* thing that decides
whether a cross-artist bucket resurfaces — checks pairs **across** artist components and
nothing else. So the set of pairs the cross queue needs to write is exactly the
cross-component pairs. Every within-component pair it writes today is collateral, and those
are precisely the main queue's pairs.

Marking cross-component pairs is therefore both the minimal fix and an exact match for what
settles the bucket. It is also simpler to implement than a newcomer/established distinction,
because the component split already exists in `_bucket_components`.

### 0.2 The damage is already repaired — no repair script

The roadmap says "10 of 775 multi-track ISRCs split across version groups, 21 tracks (0.2%).
Seven `reviewed_pair` rows were cleared and re-reviewed by hand that day," which left it
ambiguous whether all ten were resolved. Measured 2026-08-15:

| | |
|---|---:|
| Multi-track ISRCs | 775 |
| Still split across version groups | **1** |

The one is `QZ8GX1702008` — Night Cap's *Blanks* (3:23) and George Barnett's *Secrets*
(3:36), the upstream distributor collision the roadmap already documents. It **should** stay
split. The other nine are gone.

Latent damage that would not show as an ISRC split was also checked: 95 within-component
`reviewed_pair` rows sit reviewed-and-ungrouped inside multi-component buckets, and **none of
them share an ISRC** — so none would auto-group even if un-decided, and spot-checking they are
legitimate main-queue calls (`Blank Space (Taylor's Version)` vs `Blank Space - Voice Memo`,
`Anti-Hero` vs `Anti-Hero - Acoustic Version`). There is nothing a repair pass could safely
un-decide.

**So M1 ships as fix-only.** No `scripts/` one-off, no bulk `reviewed_pair` deletion. The
`reset_misgrouped_pairs.py` precedent is deliberately *not* followed here.

### 0.3 The backfill costs fewer requests than the roadmap's table says

The roadmap's cost table counts one request per album in scope. It does not account for
albums Symr already holds in full: `album.total_tracks` is captured on every pull, so an
album whose owned-track count already equals `total_tracks` needs **no request at all** and
can be skipped for free. Re-measured 2026-08-15:

| scope | albums in scope | with missing tracks | **need a fetch** | missing tracks | **requests** |
|---|---:|---:|---:|---:|---:|
| last 2 generations (36–37) | 117 | 69 | 69 | 576 | **~70** |
| last 7 generations (31–37) | 312 | 176 | 176 | 1,465 | **~178** |
| every generation | 1,403 | 845 | 844 | 7,342 | ~848 |

Library-wide: **6,217 albums, of which 2,210 are already complete** and 4,007 would need a
fetch. 55,872 catalogue tracks against 9,953 owned (17.8%). Only **9 tracklists** have ever
been fetched, and there are **37 `wanted_uri` rows, all `source='album'`**, queued by
browsing album pages under K.

Data quality is clean: **0 albums with a NULL `total_tracks`**, and **0 where the owned count
exceeds `total_tracks`** — so the arithmetic below has no degenerate cases to guard.

The roadmap's "~208 requests for the last 7 generations" is superseded by **~178**.

### 0.4 M2's shape changed completely

The roadmap imagined a scoped backfill job with a request budget, resumable, possibly
chaining into the round-trip and auto-group. Finn settled a much smaller design: the backfill
is **an extra way to put uris into the round-trip's existing queue**, nothing more. There is
no chaining, no auto-group step, no budget parameter — the two fixed-size buttons *are* the
budget control. §4 is authoritative; the roadmap's M2 sketch is not.

### 0.5 K left a re-add gap nobody had noticed

`app.py:269` only calls `entities.fetch_album_tracklist` when `tracklist_pulled_at IS NULL`,
and the `wanted_uri` queuing happens *inside* that function. So once an album's tracklist has
been pulled, revisiting its page never re-queues anything. This was not previously recorded
anywhere, and it is what §4.5 fixes.

---

## 1. M1 — the `mark_reviewed` over-reach

### 1.1 The defect

`/api/canonical/cross/apply` (`app.py:983`) ends with `canonical.mark_reviewed(conn, track_ids)`
over the **whole bucket**, and `mark_reviewed` (`canonical.py:228`) inserts *every unordered
pair* in what it is given. But the cross-artist queue only ever asks "does this newcomer
belong to that existing song group?" — never "are these two same-artist tracks the same
recording?". So answering a bucket, including with the one-keypress "none of these are
related" default, marks same-artist pairs inside it as decided and permanently suppresses
them from the main queue, where the deterministic same-ISRC rule would have grouped them.

Worse than the general case: `_make_cross_item` deliberately renders a newcomer that shares a
primary artist with a group as **nested and unassignable** (`canonical_detect.py:662`),
explicitly because "asking about it here would be doing the main queue's job twice" — and then
the apply marks exactly that pair as reviewed anyway.

### 1.2 The fix

At the cross-apply site **only**, mark **cross-component pairs and nothing else**.

Two additions:

- **`canonical_detect.cross_component_pairs(conn, track_ids)`** — splits the given bucket into
  artist-overlap connected components and returns every unordered pair whose two tracks fall in
  *different* components. It must use the same rule as `_bucket_components`
  (`canonical_detect.py:544`): alias-resolved `artist_ids` from `artists.artist_sets(conn)`,
  unioned with the existing `_group_by_rule` helper. **Share `_group_by_rule` rather than
  re-implementing the union-find** — if this function's components ever disagree with
  `_bucket_components`, a bucket can fail to settle and resurface forever.

  It does **not** call `_fetch_tracks` (~350ms, whole-library). `artists.artist_sets(conn)` is
  one scan of `track_artist_role` and is where `_fetch_tracks` gets `artist_ids` from anyway,
  so calling it directly is both cheaper and exactly equivalent.

- **`canonical.mark_reviewed_pairs(conn, pairs)`** — the pair-level writer, carrying the
  `INSERT … ON CONFLICT DO UPDATE SET decided_at` body that `mark_reviewed` has today.
  `mark_reviewed(conn, track_ids)` becomes a thin wrapper that generates all pairs and calls
  it, so its two correct callers are untouched.

`app.py:983` then becomes `canonical.mark_reviewed_pairs(conn, canonical_detect.cross_component_pairs(conn, track_ids))`.

### 1.3 The other two callers stay exactly as they are

Both were checked, and neither over-reaches:

- **`/api/canonical/apply` (`app.py:1032`)** — the main queue. It asks the reviewer to
  partition a same-artist candidate group, so every pair in it genuinely was asked about.
- **`canonical_autogroup.py:103`** — only ever receives groups from
  `canonical_detect.auto_group_candidates`, which closes a group **only when the rule matches
  on every pair in it** (`canonical_detect.py:878`, `:897`). A 3-track group where the rule
  fires on two of three pairs stays in the queue whole.

The roadmap's "check every other `mark_reviewed` caller for the same over-reach" is therefore
discharged: M1 is a one-site fix.

### 1.4 What must still hold afterwards

- Answering a cross-artist bucket still settles it — `_cross_component_reviewed` returns True
  and the bucket does not resurface until a new track joins it.
- Within-component pairs in that bucket remain unreviewed, so they appear in the main queue.
- A **nested** newcomer's pair with the group it is nested under is within-component by
  construction, so it is now left for the main queue — which is what `_make_cross_item`'s own
  comment says should happen.

---

## 2. M1b — the viewer's selection never clears

### 2.1 The defect

`/dev/canonical`'s search results carry checkboxes backed by a `sessionStorage` key
(`canonical_viewer_selection`, `static/js/canonical_viewer.js:2`). That key is only ever read
and written — nothing clears it once the selection has been used, and `canonical_review.js`
never touches it. So handing a selection to the ad-hoc review queue and applying it leaves
those ids selected for the life of the tab; the next search-and-select silently carries them
along and "Group selected" merges the old tracks into the new group. Found 2026-08-14 when
three already-grouped tracks were dragged into an unrelated two-track merge.

The persistence is deliberate and *is* wanted for gathering tracks across several searches. The
defect is that it has no completion event.

### 2.2 The fix

**`canonical_review.js` clears `canonical_viewer_selection` on a successful ad-hoc apply** —
i.e. when the review session was entered via `?tracks=` and `/api/canonical/apply` returned OK.

Chosen over clearing it at hand-off in `canonical_viewer.js` because backing out of the review
screen must not cost the selection; only actually applying it counts as done. It couples the
two pages by one `sessionStorage` key name, which is accepted — put the key name in a named
constant in `canonical_review.js` with a comment pointing at `canonical_viewer.js`.

Clearing happens **only for the ad-hoc queue**, not for main- or pending-queue applies: those
sessions did not come from a viewer selection and have nothing to complete.

### 2.3 Explicitly not doing

The roadmap notes the selected-count sits below the search results and is easy to miss.
Decided: **no change** — clearing the key is enough, and the count is only misleading because
of the bug being fixed here.

---

## 3. M1c — album links and the `entity_link` sweep

### 3.1 The two missing album links

K's sweep missed two spots where `templates/canonical.html` renders an album as bare text while
the track and artists on the same line are linked:

- **`:217`** — the search-results table, `<td>{{ t.album_name or "" }}</td>`
- **`:28`** — the group listing's track line, inside `leaf-meta`

Both become `entity_link('album', t.album_id, t.album_name)`. `track_display` already selects
`album_id` (`canonical.py`) and `entity_link` is already imported into that template, so no
read-path change is needed.

**When `album_id` is null, render the album name as bare text** (and nothing at all when the
name is also null, matching today's `or ""`).

### 3.2 `entity_link` gains optional query parameters

Four sites legitimately cannot use the macro today because they pass a query param that
`entity_link` has no way to express — `entity_playlist.html:27,42,44` (`generation=1`) and
`generations.html:40` (`tier=`).

**Teach the macro an optional params argument**, e.g.
`entity_link('playlist', p.id, p.name, {'generation': 1})`, defaulting to an empty mapping and
splatted into the existing `url_for` calls. Every branch of the macro takes it, not just the
playlist one.

**Corrected 2026-08-17 (P1-017):** the `tier=` call sites above are on `entity_playlist.html:43,45`
(the Version/Song generation-view toggle), not `generations.html:40` as originally written — a
small misattribution. Separately, the shipped
macro carries a **second** optional parameter beyond `params`: **`css_class`**, added because the
playlist page's generation-view toggle needed an `active` class that `params` alone can't
express. An empty `css_class` emits no attribute at all, so every existing call site is
unaffected.

### 3.3 Sweep every bypass onto the macro

With §3.2 in place there is no longer any reason for a site to build an entity URL by hand.
**Convert all of them**, both the 11 that emit correct URLs today and the 4 that needed the
params argument:

- `canonical.html:24, 215`
- `snapshot.html:76, 102, 130, 131`
- `generations.html:41`
- `entity_playlist.html:27, 42, 44` (params)
- `generations.html:40` (params)

Line numbers are from the pre-change tree and will drift as edits land — find them by pattern
(`url_for('track_page'` / `'album_page'` / `'artist_page'` / `'playlist_page'`, and the group
tiers) rather than by number, and finish by grepping the templates to confirm **no `url_for`
call to an entity page survives outside `_macros.html`**. That grep is the acceptance test for
this sub-step.

Output must be byte-identical to what those sites emit today, params aside.

---

## 4. M2 — album backfill

### 4.1 What it is

Symr only ever sees tracks that are *in a playlist*, so it holds 9,953 of the 55,872 tracks on
the albums it has touched. It knows an album has 13 tracks and that it owns 4, but has no idea
what the other 9 **are** — and cannot queue a uri it has never seen. Learning them costs one
`GET /v1/albums/{id}` per album.

Filling this in is what makes album scores truthful, because H pads an album with its untouched
tracks (`docs/specs/scoring-H.md` §5.4) and a backfilled track joins its twin's version group
by ISRC, inheriting that score.

**M2 is a new way to put uris into the round-trip's existing queue, and nothing else.** The
round-trip then resolves them into real `track` rows exactly as it already does for played
uris, and Finn groups them on `/dev/canonical` exactly as he already does. There is no
chaining, no auto-group step, no scope parameter beyond the two buttons, and no request budget
beyond choosing which button to press.

### 4.2 The derived model

Everything below is **derived**, in D's discipline: nothing is checkpointed, so nothing can go
stale, and clearing the queue is a free and complete undo.

For an album `A`:

- `owned(A)` = tracks with `album_id = A`
- `queued(A)` = `wanted_uri` rows for `A` that are still **unresolved** — i.e. that do not
  resolve through `played_uri_track`, the same rule the round-trip's work list uses to decide
  "done"
- `missing(A)` = `A.total_tracks - owned(A) - queued(A)`
- **`A` is settled** ⟺ `missing(A) <= 0`

  **Ruled 2026-08-17 (P1-017):** if `total_tracks` is NULL, this arithmetic treats it as `0`,
  which makes `missing(A) <= 0` unconditionally — the album computes as permanently settled and
  silently drops out of every future backfill run. Zero albums are in this state today (this
  spec's own original measurement), so it's current, if untested, policy rather than a live bug.
  Documented here rather than guarded in code, since there's nothing real to guard against yet.

A generation `G` is **handled** ⟺ every album with at least one track present in `G` is settled.

This one definition gives every behaviour the design needs:

| situation | falls out as |
|---|---|
| album never fetched | `queued = 0`, so `missing > 0` → not settled → gets a request |
| album fetched and queued | `owned + queued = total` → settled |
| round-trip resolves the uris | `owned` rises, unresolved `queued` falls → still settled |
| a uri that 404s permanently | stays in `wanted_uri` unresolved → counted in `queued` → settled, never re-offered |
| **Finn clears the backfill queue** | `queued` drops to 0 → not settled → the same generations are offered again, at **zero requests**, because the tracklists are stored |

That last row is the Q17 requirement: a clear is a true undo rather than a trap that silently
pushes the buttons on to older generations and strands what was cleared.

### 4.3 Schema

One additive migration in `db.py`:

- **`wanted_uri.album_id TEXT`**, nullable. Without it, "which uris belong to this album" can
  only be answered by parsing `album.tracklist_json` for every album on every page load, which
  would make `/dev/roundtrip` scale with the library. With it, §4.2's arithmetic is plain SQL.
  Nullable because a future `wanted_uri` source need not have an album.
- The migration **backfills `album_id` for the existing 37 rows** by scanning the 9 stored
  `tracklist_json` blobs. Tiny, one-time, and it keeps the counts honest from the first page
  load rather than only after each album is revisited.

`wanted_uri.uri` stays the primary key and inserts stay `INSERT OR IGNORE`, so if the backfill
meets a uri an album page already queued, the row keeps its original `source` and `album_id`.
**The counts in §4.6 therefore reflect which route queued a uri first**, which is the only
sensible reading and needs no extra machinery.

Also promote `snapshot.py`'s private `_set_meta` / `_get_meta` (`snapshot.py:775`, `:783`) to
**`db.set_meta` / `db.get_meta`**, updating snapshot's two call sites. §4.6's mute flag needs
them and a second copy is exactly the drift worth avoiding.

### 4.4 Splitting fetch from queue

`entities.fetch_album_tracklist` currently does two things in one guarded call: fetch the
tracklist, and queue the uris with no `track` row. Split them:

- **`fetch_album_tracklist(conn, album_id)`** keeps its guard — at most one Spotify request,
  on that album's own page, first view only, every failure swallowed so the page still renders
  from the DB.
- **`queue_wanted_uris(conn, album_id, source)`** (new — **`source` is a required positional
  argument, corrected 2026-08-17, P1-017**; the signature above originally omitted it even
  though the very next sentence requires "the caller's `source`") runs off the **stored**
  `tracklist_json` and costs **zero requests**. It writes `wanted_uri` rows for every tracklist
  item with no `track` row, stamped with `album_id` and `source`.

The album page then calls the fetch when `tracklist_pulled_at IS NULL` **and calls the queue
step on every view**, with `source='album'`. That closes §0.5's re-add gap: clearing
album-page-queued uris and revisiting the album genuinely restores them, for free.

**"The backfill job calls the same two functions" is only half true, noted 2026-08-17
(P1-017).** Only `queue_wanted_uris` is actually shared. The backfill job has its **own**
tracklist-fetch function (`backfill._fetch_full_tracklist`), never
`entities.fetch_album_tracklist` — and has to, since §4.5 requires paging past the first 50
items, which the entity page's fetch deliberately never does. `CLAUDE.md`'s `entities.py` map
entry repeats this section's incomplete version and should be corrected the same way if it's
ever touched (out of scope for a spec-only pass).

### 4.5 The backfill job

A **fourth background job**, `"backfill"`, alongside `snapshot` / `history_import` /
`roundtrip`. It claims the one job slot through `jobs.try_start` like the others, so it cannot
overlap a round-trip — which matches the workflow anyway, since the backfill runs first and the
round-trip second.

It has to be a job rather than a synchronous handler because ~178 requests is roughly 30–60s of
wall clock, which would hang and time out a plain click.

**Work list**, recomputed at start and never stored:

1. Order generations by `ordinal` **descending**.
2. Skip generations that are already **handled** (§4.2).
3. Take the next **N** unhandled ones — `N = 7` or `N = 2` depending on the button.
4. The albums to process are every **unsettled** album with at least one track present in any
   of those N generations, via `generation_presence` joined to `track.album_id`.

Per album:

- If `tracklist_pulled_at IS NULL`, fetch it — **paging past the first 50 items**, unlike the
  entity page's deliberate one-request cap. 55 albums library-wide exceed 50 tracks; only 2 fall
  in the last-7-generations scope, so this costs ~2 extra requests there and ~4 across every
  generation.
- Then run `queue_wanted_uris(conn, album_id)` with `source='backfill'`, whether or not a fetch
  just happened — so an album whose tracklist is already stored is requeued for free.
- **Commit per album**, so a run that dies keeps everything it got. **Refined 2026-08-17
  (P1-017):** this durability is real but is a side effect of `queue_wanted_uris`'s own trailing
  commit, not a commit the job's per-album loop issues itself — and that commit is *skipped* by
  an early return when `tracklist_json` comes back empty. So an album whose fetch succeeds but
  has nothing to queue can, in that narrow edge case, leave the fetch's own write uncommitted.
  Narrow, not currently known to bite, but worth knowing rather than trusting "commits per
  album" as the job's own guarantee.

**Missing the closing `scoring.recompute()` call, noted 2026-08-17 (P1-017).** Every other job
call site (eleven of them, per `CLAUDE.md`) ends with a `scoring.recompute()` after a run that
touched a scoring input; `backfill.py` doesn't import `scoring` at all, even though its
`_refresh()` does commit one (`canonical.ensure_track_groups`). Likely benign —
`async-recompute-N.md`'s `ensure_fresh()` backstop should catch it on the next request that reads
scores — but it means backfill is a documented exception to the "all eleven" pattern, not one of
the eleven. This landed after M, when N introduced the backstop; not M's fault, just never
folded back here.

**Stopping**, all three inherited from the existing jobs with no new machinery:

- Every Spotify call goes through `jobs.call(status, fn, …)`, so `RateLimited` aborts the run
  immediately rather than sleeping through a multi-hour `Retry-After`.
- `jobs.stop_requested()` is polled between albums (a safe point — the previous album is
  committed).
- Resume is free: re-clicking the same button recomputes the work list, and everything already
  done is settled and skipped.

**Status fields** on its `JobStatus`: albums total, albums done, uris queued, requests spent,
plus the standard capped event log. Progress is per-album, which is the only unit that means
anything here.

### 4.6 The round-trip page's new box

One new box on `/dev/roundtrip`, above or beside the existing controls.

```
Round-trip queue
   1,182 listening tracks        [Clear]
      37 album page tracks       [Clear]
       0 album backfill tracks   [Clear]

Album backfill
  Next 7 generations (31–37): 176 albums, ~178 requests   [Add]
  Next 2 generations (36–37):  69 albums,  ~70 requests   [Add]
```

**The three counts partition the queue exactly** — **except while the listening arm is muted,
ruled 2026-08-17 (P1-017): that exception is deliberate and stays.** Arm 2 of `_WORK_LIST_SQL`
already excludes uris that appear in `play`, so listening / album-page / album-backfill sum to
the total remaining with no double-counting *when unmuted*. While muted, `_WORK_LIST_SQL`'s
listening arm drops to zero (so `remaining_uris` correctly excludes it), but the **displayed**
listening count deliberately keeps showing what it *would* contribute — `_LISTENING_REMAINING_SQL`
omits the mute filter on purpose, so muting doesn't collapse the row to an uninformative zero and
Finn can see what `[Re-add]` would bring back. The sum breaks in exactly this one state, and
that's accepted: a partition-sum test should assert the invariant in the unmuted case and
explicitly carve out the muted one, not treat it as a bug to fix. They supersede the single
`wanted_uris` figure that `roundtrip.status()` returns today via `_WANTED_REMAINING_SQL`.

- **listening** — arm 1 of `_WORK_LIST_SQL`: unresolved `play.spotify_track_uri` not in
  `roundtrip_failed_uri`.
- **album page** / **album backfill** — arm 2, split by `wanted_uri.source`.

**Clearing.** All three fire on a **single click, with no confirm step** — every one of them is
reversible, so a two-step inline confirm would be friction for nothing. (The no-native-dialogs
rule still stands; nothing here uses `confirm()`.)

- **Album page** and **album backfill** clears are `DELETE FROM wanted_uri WHERE source = ?`.
  They come back by revisiting the album page (§4.4) or re-clicking the Add button (§4.2), both
  free.
- **Listening tracks cannot be deleted** — arm 1 is a live query over `play`, and there are no
  rows to remove short of destroying the imported history. Instead, a **`meta` flag
  (`roundtrip_listening_muted`) gates that arm**, checked inside `_WORK_LIST_SQL` itself as a
  correlated subquery so the constant stays one string with no Python branching. `[Clear]` sets
  it; `[Re-add]` unsets it. Nothing is stored about *which* uris, so plays that arrive while it
  is muted are picked up the moment it is unmuted.
- When muted, the row still shows the count that *would* be included, marked `(excluded)`, with
  `[Re-add]` in place of `[Clear]` — so Finn can see what he would be getting back.

**Only the listening row gets a `[Re-add]`.** The other two have their own free re-add routes
and do not need one.

**The cost figures beside the Add buttons are server-rendered on page load**, computed with no
Spotify calls from §4.2's arithmetic. No preview-then-confirm step: seeing the numbers before
clicking is the whole budget control Finn wants. The generation label shows the ordinals
actually chosen, collapsed into ranges — they are contiguous today but need not stay so once
generations are handled out of order.

**These "computed with no Spotify calls" figures still write to the database, noted 2026-08-17
(P1-017).** `previews()`'s call chain runs `canonical.ensure_track_groups()` and **commits**, on
every ordinary `GET /dev/roundtrip` page view. Not a Spotify-request cost — this section's claim
about that holds — but it is a database write on a plain page load that §4.2's "derived,
checkpoint-free" framing doesn't flag. Harmless (it's the same idempotent bootstrap every
`/dev/canonical*` request already triggers), just worth knowing.

The counts and the job's progress refresh through `roundtrip.js`'s existing status poller;
adding a second poller would be two things to keep in step.

### 4.7 Not in scope

- **No auto-group chaining.** The backfilled tracks go through the existing grouping queue and
  the existing auto-group rule, untouched. M2 is an ingest route, not a grouping change.
- **No round-trip chaining.** Finn clicks backfill, then the round-trip button, as two
  deliberate acts.
- **No "everything" scope.** Only the two generation buttons. A full backfill takes the library
  from 9,953 to ~55,872 tracks and every one needs grouping; the roadmap is right that it wants
  J (resumable pulls) first.
- **No re-fetch of an already-pulled tracklist.** `tracklist_pulled_at` is set once. An album
  that gains tracks upstream will not be noticed, which is accepted.

---

## 5. Verification

M1, M1b and M1c are verifiable offline against `symr.db`; M2's job is not, because it spends
real Spotify requests.

1. **M1** — pick a multi-component bucket, answer it with the one-keypress default, then assert
   (a) `_cross_component_reviewed` is now True for it and it does not resurface in
   `cross_buckets`, and (b) no within-component pair from that bucket was written to
   `reviewed_pair`. Per the testing convention, leave anything ungrouped back in the queue.
2. **M1b** — select tracks in the viewer, hand off to the ad-hoc queue, apply, and confirm
   `sessionStorage.canonical_viewer_selection` is gone; then repeat but back out instead of
   applying, and confirm it survives.
3. **M1c** — grep the templates for surviving `url_for` calls to entity pages outside
   `_macros.html`; there must be none. Load `/dev/canonical` and confirm the album cells link,
   and that a track with a null `album_id` still renders its album name as plain text.
4. **M2, offline** — confirm the counts partition the queue, that each `[Clear]` empties its own
   row and leaves the other two alone, that `[Re-add]` restores the listening count, and that
   the Add buttons' album counts and request estimates match §0.3's table.
5. **M2, live** — **left for Finn to run**, with the 2-generation button first (~70 requests).
   The implement session builds it and stops there. The round-trip writes to the real Spotify
   library, and the app's dev-mode quota has produced a ~24h lockout before.

The app runs on **port 45660** and nothing else; if it is occupied, stop and ask.
