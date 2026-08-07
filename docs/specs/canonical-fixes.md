# Canonical-tracks fixes: tier-scoped grouping, and page load

Two unrelated fixes to the canonical-tracks feature, in one spec because they
land in the same area and neither is big enough to plan alone:

1. **The review UI destroys finer grouping work** when you build a group up
   over several clicks (§1).
2. **`/dev/canonical` hangs noticeably on load** — ~1.2s, of which ~1.1s is the
   same computation run three times (§2).

Nothing here touches the grouping engine's write path (`canonical.apply_partition`)
or the schema. Fix 1 is entirely client-side; fix 2 is entirely a call-ordering
change.

---

## 1. Tier-scoped grouping in the review UI

### 1.1 What happens now

`applyLevel()` in [static/js/canonical_review.js:111](static/js/canonical_review.js:111)
rewrites **every tier** for every selected track on each click:

```js
const sharedThroughIndex = level - 2;      // TIERS = [song, version, recording, release]
TIERS.forEach((tier, i) => {
  if (i <= sharedThroughIndex) {           // shared fresh label
    const label = freshLabel(tier);
    for (const tid of members) item.labels[tid][tier] = label;
  } else {                                 // fresh label *per track* -- a deliberate split
    for (const tid of members) item.labels[tid][tier] = freshLabel(tier);
  }
});
```

So each button means "**exactly** this close": `1 Song` asserts *same song **and**
different version, different recording, different release*. Every tier coarser
than the button's is also overwritten with a fresh label **scoped to the current
selection**, which quietly drops any non-selected track that shared it.

That makes the two halves of a multi-step grouping fight each other:

- Applying a **finer** tier to a subset re-labels the **coarser** tiers too, so
  the subset leaves the coarser group it shared with the tracks left behind.
- Applying a **coarser** tier to fix that re-splits every **finer** tier, undoing
  the finer grouping just done.

There is no click order that reaches "same song, and two of them also share a
version" — each step destroys the other's work.

### 1.2 The rule

**A click sets its own tier, splits everything finer, and leaves everything
coarser alone.**

For a selection and a button whose tier sits at index `t` in
`TIERS = [song, version, recording, release]`:

| tiers | behaviour |
|---|---|
| coarser than `t` (index `< t`) | **left untouched** — each track keeps its current label (this is the change) |
| exactly `t` | one shared fresh label across the selection (unchanged) |
| finer than `t` (index `> t`) | a fresh label per track — a deliberate split (unchanged) |

`0 None` has no tier of its own: it keeps today's behaviour of a fresh label per
track at every tier, which is what "not even the same song" means.

This makes the buttons mean "**at least** this close, and I'm not touching what
you already decided above me", and it makes grouping **coarse-first**:

```
ABC → 1 Song      song shared; version/recording/release split per track
BC  → 2 Version   song LEFT ALONE (all three still one song);
                  version shared across B,C; recording/release split
```

which is the target state, in two clicks, with no step undoing the previous one.

### 1.3 The one edge case: a finer merge across differing coarser groups

Leaving coarser tiers alone can produce a payload the server rightly rejects. If
B and C **don't already share a song** and you apply `2 Version` to them, the
version label maps to two different song labels — not nested-consistent, and
`canonical._validate_labels` raises `ValueError`, which `app.py` turns into a 400.

Today's force-share hides this by overwriting the coarser tiers.

**Rule:** leave the coarser tiers alone **only when the selection already agrees
on all of them** — i.e. every selected track carries identical labels at every
tier coarser than `t`. If they disagree, fall back to today's behaviour for those
tiers (one shared fresh label across the selection).

The fallback only fires when grouping fine-before-coarse, which the new rule
makes unnecessary. It is there so no click can ever build an invalid payload,
not as a path anyone is expected to use.

### 1.4 What is deliberately *not* changed

- **The coarser-tier fallback still uses a fresh label scoped to the selection**,
  so in that fallback case a non-selected track sharing the old coarser group is
  still dropped from it. Reusing the existing coarser label instead would be the
  fuller fix. Left alone on purpose: changing both halves at once risks stranding
  some path as unreachable, which is exactly how the current bug was introduced.
- **`clearAll()` stays a full reset** — every tier freshened on every track. It is
  the deliberate "start over", and it is the escape hatch when a group has been
  built up wrong.
- **No server-side guard is added.** `_validate_labels` already rejects malformed
  (non-nested) payloads and stays as that backstop. It cannot help with this bug:
  a re-randomised finer tier is perfectly nesting-consistent, so "the user asked
  to split these" and "the UI forgot they were grouped" are byte-identical on the
  wire. A server rule would have to guess intent and would block legitimate splits.
- **No repair of existing data.** This is a UI bug, so anything already committed
  was reviewed and accepted as it stands; groups are not rewritten.

### 1.5 Acceptance

**Simple case — Inwood Hill Park** (3 tracks, 6LACK, main queue):

| track_id | name |
|---|---|
| `4qZLrKsaGkYmVRGUviVPtk` | Inwood Hill Park |
| `7aDKzbz1iAwNkmQcmkKXBH` | Inwood Hill Park |
| `2VREA6dXSKRoW4XNwctpaZ` | Inwood Hill Park - Acoustic |

Target: all three one **song**; the two plain ones share a **version**; the
acoustic is its own version; all three differ at **recording** and **release**.

Reachable in two clicks, and the second must not disturb the first:
1. select all three → `1 Song`
2. select the two plain ones → `2 Version`

Then assert: one song group of 3; two version groups (2 + 1); three recording
groups; three release groups. Re-clicking `1 Song` on all three is no longer part
of the workflow, but if done it still splits the versions — that is the button
doing what it says.

**Larger case — `willow`** (12 tracks, **cross-artist queue**): four by Jasmine
Thompson, seven by Taylor Swift (four plain `willow`, two
`willow - lonely witch version`, one `willow - moonlit witch version`), one by
sombr. Three unrelated songs that share a title, one of which has real internal
version structure. It exercises `0 None` between artists *and* multi-step
refinement within Taylor Swift's seven, on a group large enough that
`clearAll()`-and-redo would be genuinely costly. Use it to confirm the fix holds
when a selection is a small subset of a big item.

Both are live in `symr.db` and unreviewed, so they can be worked in the real UI.
Per the testing convention: leave anything ungrouped back in the queue.

---

## 2. `/dev/canonical` page load

### 2.1 Measured (2026-08-07, 9,693 tracks)

| call | cost |
|---|---:|
| `ensure_track_groups` | 2 ms |
| `tier_counts` | 9 ms |
| `song_groups` (default) | 86 ms |
| `song_tree` × 142 | 14 ms |
| `candidate_groups` | 382 ms |
| `cross_artist_groups` | 361 ms |
| `all_candidate_groups` | 375 ms |
| **total** | **~1.2 s** |

### 2.2 The cause

[canonical_detect.py:404-416](canonical_detect.py:404) — the three detection
entry points are thin wrappers over one builder, and they each rebuild it:

```python
def candidate_groups(conn):      main, _cross = _build_all_groups(conn); ...
def cross_artist_groups(conn):   _main, cross = _build_all_groups(conn); ...
def all_candidate_groups(conn):  main, cross  = _build_all_groups(conn); ...
```

`/dev/canonical` ([app.py](app.py), the `dev_canonical` route) calls **all three**
— for `unreviewed_main`, `unreviewed_cross`, and the cross-artist list. So the
page pays `_build_all_groups` (~370 ms warm, 518 ms cold) three times over,
which is ~1.1 s of the ~1.2 s.

### 2.3 The fix

Build once per request and derive all three results from it. Either shape is
fine, implementer's choice:

- a `_build_all_groups` result memoised on the `db.Connection` object (the
  connection is per-request, so it cannot go stale — but see the warning below), or
- one new function returning all three, with the route calling that.

**Prefer the second.** A per-connection memo is how `canonical._artist_display`
already works, and that one is a known trap: it caches per connection and never
invalidates, so writing artist data and re-rendering displays in the same request
returns stale values. Detection has the same hazard — `/dev/canonical` calls
`ensure_track_groups` and commits *before* reading — so an explicit
"compute once, pass it down" is safer than another invisible cache.

Expected: **~1.2 s → ~0.6 s**, with no cache and nothing to invalidate.

### 2.4 If that isn't enough

Stop there and re-measure. A persisted detection cache is the next step and is
**deliberately not specced** — it needs an invalidation story (every write to
`track`, `track_artist`, `artist_alias` and `reviewed_pair` invalidates it), and
that is not worth designing until 0.6 s is shown to still hang. Note the new
number in `docs/Planning/` if a cache turns out to be needed.

Only `/dev/canonical` is in scope. `/dev/snapshot/track` is already fast enough,
and the review queue is served by `/api/canonical/queue`, which calls a single
detection entry point and so already pays the build once.

---

## 3. Out of scope

- Reusing an existing coarser label instead of a fresh one in the §1.3 fallback
  (see §1.4).
- Any change to `canonical.apply_partition`, the closures, or the schema.
- Repairing existing groups.
- A persisted detection cache (§2.4).
- Re-running detection over the ~6,000 tracks step D added — that is step E in
  `docs/Planning/listening_data_roadmap.md`.
