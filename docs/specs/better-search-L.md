# L — Better search

Step L of `docs/Planning/roadmap.md`.

Planning contradicted the roadmap in one place, recorded in §11: L was taken **before F/G and O**,
which the Order block had ahead of it. No technical dependency either way — F/G and O are not
prerequisites for search, and search is not one for them.

> **Partly superseded by `docs/specs/better-search-L2.md`.** L shipped as specced and this file
> remains the record of what it built, but using it on the real library exposed three defects in
> its *formula*. **§3's exact-match decision (one bullet only), §4.4, §4.5's constants list in
> §4.6, and §10.1's worked numbers are replaced by L2 §3, §4 and §7** — read L2, not the sections
> below, for how the matcher scores today. Everything else here is still authoritative: §4.1's
> normalization, §4.2's index and cache, §4.3's trigram prefilter, §4.7's cost rules, §5's routes,
> §6's page, §7's dropdown, §8's guard-order fix, §9's module placement and §10.2's costs. The
> defects, and every figure L's Verify pass measured, are in `docs/better-search/L2_handoff.md`.

---

## 1. What this is

K shipped search deliberately plain: a navbar box posting to `/search?q=`, four `LIKE '%q%'`
groups, each capped at 50 and ordered by materialized score descending. It reaches any entity
page and no more.

L makes it good, and the roadmap names the three parts: a **dropdown** that answers as you type,
**fuzzy matching** so a typo or a half-remembered title still finds the track, and **ranked
results across types**, so the artist you have hundreds of tracks by outranks a one-play song
whose title happens to contain their name.

**The shape of the answer is one matcher, used by three surfaces.** There is no separate
"dropdown ranking" and "page ranking" — the dropdown is a shortcut into the same ordered list the
page renders, and anything true of one is true of the other.

---

## 2. What changes from K §10, clause by clause

This section exists because three of L's decisions **replace** documented K behaviour rather than
adding to it. A test written from the new rule alone would leave a fixture free to keep agreeing
with the old one.

| K §10 said | L says |
|---|---|
| A song matches `track.name` **or any credited artist's name** | A song matches on **its own title**. An artist or album name match only *bumps* it (§4.4). A song whose title does not match must not appear in the Songs section **or** the combined list. |
| Each group `LIKE '%q%'` — substring only, no fuzzy, no typo tolerance | Two-stage fuzzy matcher, §4 |
| `q`'s LIKE wildcards are unescaped, so a literal `%` acts as a wildcard | No LIKE anywhere. `%` and `_` are ordinary characters, and `normalize.base_string` deletes them as punctuation |
| Each group capped at 50, ordered by materialized score descending | 10 rendered per section, up to 200 more fetched on demand (§6), ordered by `score × relevance ** ALPHA` (§4.5) |
| Four groups, nothing above them | A **Most Relevant** box of 20 above the four (§6.1) |

Everything else in K §10 stands: the four types, the entity each links to, songs deduped by
version group, and an empty query rendering the page with no results.

---

## 3. Decisions taken in planning, with their reasons

- **Generations are not a fifth type.** They are playlists.
- **Songs stay at the `version` tier**, linking to `/version/<id>`, exactly as K shipped.
- **Played-but-not-owned tracks are not treated specially.** ~13.2k `track` rows against ~3.6k
  with a live membership, and the score demotes them if they deserve it. No badge, no filter, no
  separate section.
- **Ranking is `all_time` only.** `recent` is not consulted. If quality turns out poor in use,
  switching horizons is a one-constant change — but it is not this step.
- **An exact match does not automatically sort first.** Score still speaks. *(Re-examined and
  upheld in L2 §3 against the evidence L's Verify pass raised.)*
- **Enter in the navbar box submits to `/search`.** The dropdown is a shortcut, not the main
  experience, so the full page is always one keystroke away.

---

## 4. The matcher

Lives in the new `search.py` (§8). Every constant below is a **module-level constant with a
warning comment**, following `scoring.py`'s convention — not in `config.py`, not environment-
tunable, for the same reason H §10 gives: a per-environment parameter would leave two
environments ranking under two different algorithms.

### 4.1 Normalization

Everything — query and names alike — goes through `normalize.base_string`: accent-fold,
lowercase, delete punctuation, collapse whitespace. This is what already makes "Jérôme Ducros"
pair with "Jerome Ducros" in artist detection, and it is why searching "beyonce" finds "Beyoncé"
for free.

The query is then split on whitespace into **query tokens**. A query shorter than
`MIN_QUERY_LEN` characters after normalizing yields no results at all.

### 4.2 The name index, and its cache

The scoring universe is the set of **distinct normalized names** across `track.name`,
`album.name`, `artist.name` and `snapshot.name` — 18,461 distinct forms out of 24,032 raw names
as measured (§10). A name is scored **once** and attributed to every entity bearing it, which is
what keeps a query like "love" (421 candidate names) cheap.

The cache holds two things:

- `names`: `{normalized_name: trigram_set}` — the universe and its stage-1 index.
- Per type, a list of `(id, own_normalized_name, [associated_normalized_names])`. A track's
  associated names are its album's name and each credited artist's name; an album's are its
  credited artists'; artists and playlists have none.

**It is cached in-process and staleness-checked on `PRAGMA data_version` against a dedicated
module-level connection**, the same shape `scoring.ensure_fresh()` already uses — and for the
same reason it gives: the pragma is only meaningful relative to one connection, so a per-request
one would compare unrelated numbers.

Caching is a measured decision, not a reflex. Building the index costs a fixed **108 ms** per
request (63 ms normalizing, 45 ms trigrams) against **6–44 ms** of actual query work, so
uncached the fixed cost is 70–95% of every keystroke. Both figures are §10's.

**Corrected at Verify (2026-08-29): "data that changes perhaps twice a day" was wrong**, and the
claim is struck rather than softened. The check is `PRAGMA data_version`, which moves on a commit
by *any other* connection — and `api_log.record()` writes an `api_request` row, on its own
connection, for **every outbound Spotify request**. Scrobbling alone is ~14 polls a day before
token refreshes, and a snapshot pull writes continuously. Each invalidation costs a **285 ms**
rebuild (measured, §10.2). The caching decision still stands — the cost is one rebuild on the
first search after a write, not per keystroke — but the frequency it was argued from does not.

In-process module state is safe here for exactly the reason `serve.py` records: waitress runs
single-process, and multiple worker processes are ruled out as a correctness constraint — the
job slot, the recompute worker and the `JobStatus` singletons already depend on it.

### 4.3 Stage 1 — the trigram prefilter

Trigrams of a normalized string `s` are the 3-grams of `f"  {s} "` (two leading spaces, one
trailing), so word boundaries participate.

A name is a **candidate** if, for *any* query token `qt`,

```
len(trigrams(qt) & trigrams(name)) / len(trigrams(qt))  >=  TRIGRAM_FLOOR
```

Note this is coverage of the *query token's* trigrams, not Jaccard: a short token must not be
penalized for appearing inside a long name.

The prefilter is doing most of the real work, and it is what makes the design viable at all —
the full scorer alone costs 270–840 ms over the whole universe, against 4–10 ms for this
(§10). It is also what makes typo tolerance cheap: "bohemian rapsody" reduces 18,461 names to
**5** candidates, with the right answer scoring 0.97.

### 4.4 Stage 2 — `own`, `assoc`, and relevance

**Superseded by `docs/specs/better-search-L2.md` §4.** `tsim`'s difflib branch and the
whole-string reading are now gated at `FUZZY_FLOOR`; `own`, `assoc` and `BUMP` are replaced by a
per-query-token coverage measure with `ASSOC_WEIGHT`; and the artist-only exclusion this section
argues from arithmetic is now an explicit rule (L2 §4.3). Kept as written for the record.

Only candidates are scored. For a candidate name, define the per-token similarity

```
tsim(qt, nt) = 1.0                            if qt == nt
             = 0.85 + 0.15 * len(qt)/len(nt)  if nt.startswith(qt)
             = SequenceMatcher(qt, nt).ratio() otherwise
```

The equality and prefix fast paths matter twice over: they keep most token pairs away from
`difflib`, and the prefix branch is what makes an as-you-type query behave sensibly ("boh"
against "bohemian" scores 0.91 rather than difflib's 0.55).

A name's score is the better of a whole-string reading and an order-free one:

```
name_score = max(
    SequenceMatcher(query_norm, name).ratio(),
    mean over query tokens qt of ( max over name tokens nt of tsim(qt, nt) )
)
```

The second term is what makes **word order irrelevant** — "rhapsody bohemian" finds *Bohemian
Rhapsody*. The first is what stops a long name from being matched by one of its many tokens.

Then, per entity:

- `own` = the score of its **own** name.
- `assoc` = the best score among its **associated** names (0.0 if it has none).

```
relevance = min(1.0,  own * (1 + BUMP * assoc))
```

**`own` multiplies, and that single fact is what fixes the complaint L was raised over.** A track
titled "Wait a Minute!" by the artist Willow scores `own = 0.40` on the query "willow" (measured,
§10.1). Nothing rescues it: it is **below `RELEVANCE_FLOOR` and dropped outright**, and even
without the floor `0.40 ** ALPHA = 0.064` against the artist's `1.0` would bury it. So the same
formula that keeps artist-only matches out of the Songs section keeps them out of the combined
list too — one rule, no per-surface gate, and no threshold to argue over.

`assoc` is what makes a two-part query work: on "radiohead creep", *Creep* by Radiohead takes
`own = 0.643` from token coverage plus a bump from `assoc = 0.750` → **0.884**, while *Creep* by
anyone else stays at **0.643** (measured, §10.1).

An entity with `relevance < RELEVANCE_FLOOR` is dropped outright.

### 4.5 The rank key

**`rank_key`'s shape, `ALPHA` and `SCORE_FLOOR` are unchanged by L2** — only the `relevance`
fed into it changed (L2 §4.2).

```
rank_key = max(score, SCORE_FLOOR) * relevance ** ALPHA
```

`score` is the entity's materialized `all_time` display-space score, via the existing
`scoring.scores_for_tier(conn, "version", …)` / `album_scores` / `artist_scores` /
`playlist_scores`.

**Cross-type comparison is legitimate for free.** H's whole premise is one score on one absolute
scale, with every collection type aggregating onto it through `combine()` — an artist's score and
a version's score are already the same number, so a mixed list needs no reconciliation term.

`ALPHA` is the one knob trading match quality against score, and `SCORE_FLOOR` closes a hole worth
naming: `scoring`'s lookups return nothing for an entity with no materialized score, and a plain
`score * …` would make that entity **rank zero on an exact name match** — invisible, silently, and
only for the newest rows. `SCORE_FLOOR` is the bottom of display space, so an unscored entity
ranks as a poor one rather than as no entity at all.

### 4.6 Constants

**Superseded by `docs/specs/better-search-L2.md` §4.4:** `BUMP` is deleted, `FUZZY_FLOOR` and
`ASSOC_WEIGHT` are added, and every other value below is unchanged but re-measured against L2's
formula.

| Constant | Value | What it does |
|---|---:|---|
| `MIN_QUERY_LEN` | 2 | Shorter queries return nothing |
| `TRIGRAM_FLOOR` | 0.5 | Stage-1 candidate admission |
| `RELEVANCE_FLOOR` | 0.5 | Below this an entity is dropped. **0.35 was measured and rejected** — it admits 4,395 names for "willow" and 7,619 for "the" (§10) |
| `BUMP` | 0.5 | How much `assoc` lifts `own` |
| `ALPHA` | 3.0 | Match quality vs score |
| `SCORE_FLOOR` | 10.0 | Bottom of display space, for unscored entities |
| `COMBINED_LIMIT` | 20 | Rows in Most Relevant |
| `SECTION_LIMIT` | 10 | Rows rendered per type on page load |
| `SECTION_MAX` | 200 | Rows a See more fetch may return |
| `DROPDOWN_LIMIT` | 5 | Rows in the navbar dropdown |

### 4.7 Two cost rules the implementation must keep

`/dev/canonical`'s budget note in `CLAUDE.md` applies here directly — **work proportional to what
is rendered, not to the library** — with one licensed exception and one trap:

- **Ranking must see every candidate before the cap**, exactly as `song_scores()` does for the
  canonical viewer: capping before ranking returns an arbitrary 20, not the best 20. This is the
  licensed exception, and it is cheap because it reads materialized scores.
- **Hydration touches only the rendered slice.** `canonical.representative()` and
  `canonical.track_display()` run for the rows actually emitted (20, or 10, or up to 200 on a See
  more), never for every candidate.
- **The version-group lookup is batched.** K's `entities.search` calls
  `canonical.groups_for_track` once per matching track; candidate sets here are larger, so it
  becomes one query over the candidate track ids.

Songs dedupe to **one row per version group**, keeping the member with the highest `relevance`
(ties broken by score).

---

## 5. Routes

| Route | Returns | Notes |
|---|---|---|
| `GET /search?q=` | The page, server-rendered | Unchanged URL; §6 |
| `GET /api/search?q=` | **Rendered HTML fragment** — the dropdown's 5 rows | §7 |
| `GET /api/search/more?q=&type=` | **Rendered HTML fragment** — one type's rows, up to `SECTION_MAX` | §6.2. `type` ∈ `songs`/`albums`/`artists`/`playlists`, whitelisted, never interpolated |

**Both async endpoints return HTML, not JSON**, following the precedent `/api/canonical/cross/listing`
already sets and states in its own docstring: rendering server-side is what keeps the entity links
in them the same `entity_link` macro as everywhere else, and the scores the same `score_display`.
JSON rows would mean building both in JS, in a second place, free to drift.

**Neither async endpoint calls `canonical.ensure_track_groups`.** The full `/search` page keeps
it, as today. The reasoning is the cross-listing endpoint's, verbatim: *a GET returning a listing
has no business taking a write lock* — and here it would be a write on every keystroke. The cost
is that a track ingested since the last full page load is not dropdown-findable until one happens,
which is a transient state the page itself closes.

---

## 6. The `/search` page

Layout, top to bottom. The existing sections keep their columns, cover art and `entity_link`s.

### 6.1 Most Relevant

A new box above the four sections: the top `COMBINED_LIMIT` entities by `rank_key`, **one mixed
list across all four types**, each row carrying a type label alongside the name, cover art where
the type has one, and a `score_display` badge.

**It does not dedupe against the sections below.** Repetition is fine — it is a shortcut, and
suppressing a row from its own type's section to avoid repeating it would make that section lie
about its own ranking.

### 6.2 The four type sections, and See more

Each section renders `SECTION_LIMIT` rows on load. Where more matched, it carries a **See more**
control which fetches `/api/search/more` for that type and swaps the returned rows in, after
which the box is scrollable.

**Page load renders 20 + 4×10 = 60 rows and issues no extra requests.** The remainder is neither
built nor sent until asked for — which is the whole point of the control, and the reason
`SECTION_MAX` can be as high as 200 without weighing on the common path. A See more re-runs the
search server-side (6–44 ms against the cached index) rather than holding state between requests.

All four tables carry a `score_display` badge per row.

---

## 7. The navbar dropdown

The navbar form stays a real `GET` form to `/search` and keeps working with JS disabled. A new
`static/js/search.js` (an IIFE, loaded site-wide beside `format.js`) attaches to it:

- Fires after `MIN_QUERY_LEN` characters, debounced `150 ms`.
- Fetches `/api/search?q=` and shows the returned fragment — `DROPDOWN_LIMIT` rows, one mixed
  list, same shape as Most Relevant.
- Up/Down move a highlight; Enter on a highlighted row navigates to it; **Enter with nothing
  highlighted submits the form** to `/search?q=`. Escape and click-outside close it.
- **Every response carries a sequence number, and a reply older than the latest request is
  discarded.** This is the bug this shape always has: a slow reply for "wil" arriving after a fast
  one for "willow" would otherwise overwrite the correct results with stale ones.

The dropdown appears wherever the navbar and its search box already appear — which includes the
three `immersive` pages, since `base.html` renders the navbar on all of them.

---

## 8. The review-queue Enter collision

Found during planning, and it is a live bug today rather than one L introduces.

`canonical_review.js`'s document keydown handler guards against typing in a field
(`canonical_review.js:669` returns for `input`/`textarea`) — but T's `exitState` branch
(`canonical_review.js:662`) returns **before** that guard. So on the review queue's done screen,
typing in the navbar search box and pressing Enter navigates to `/dev/canonical` instead of
searching.

**Fix: move the `input`/`textarea` guard above the `exitState` branch**, so a focused field wins
in every state. Finn's rule, stated in planning: if search is focused, Enter searches rather than
driving the review queue.

---

## 9. Module placement

**A new `search.py`.** `entities.py` is "the entity pages' read paths that belong to no existing
owner", and search is not an entity page — K put it there because it was ninety lines. L gives it
module-level tuning constants, cached state with a staleness check, a two-stage scorer and its own
result shape, which is `scoring.py`'s shape rather than a function's.

`entities.search` **moves** into it and is deleted from `entities.py`.

`search.py` imports `normalize`, `canonical`, `scoring` and `db`; nothing imports it but `app.py`.
No cycle: `normalize` imports nothing project-level by construction, and `scoring` does not reach
back here.

**`CLAUDE.md`'s codebase map gains a `search.py` entry and loses `entities.search` from the
`entities.py` one.** `tests/test_codebase_map.py` enforces the first half mechanically; the second
is prose and is not enforced, so it has to be done by hand.

---

## 10. Measured facts

All measured 2026-08-29 on the laptop, read-only against the real `symr.db`. Don't re-derive
these; don't trust a contradicting number without re-measuring. Note
`timings-contaminated-by-parallel-chats` — these are pure-CPU local measurements, so the risk is
low, but a wildly different figure is more likely another chat than a finding.

### 10.1 The matcher's worked numbers

**Superseded by `docs/specs/better-search-L2.md` §7.1.** The numbers below are L's and are no
longer what the matcher produces; L2 §7.1 gives the current values beside these for comparison.

Every illustrative figure in §4.4 is one of these, computed against the specced formula rather
than estimated. They double as the expected values for §12's matcher tests.

| case | query | vs | `name_score` |
|---|---|---|---:|
| exact | `willow` | "Willow" | **1.000** |
| word order | `rhapsody bohemian` | "Bohemian Rhapsody" | **1.000** |
| typo | `bohemian rapsody` | "Bohemian Rhapsody" | **0.970** |
| `assoc` source | `radiohead creep` | "Radiohead" | **0.750** |
| `own` source | `radiohead creep` | "Creep" | **0.643** |
| artist-only match | `willow` | "Wait a Minute!" | **0.400** — below `RELEVANCE_FLOOR` |

Prefix fast path: `tsim("boh", "bohemian")` = **0.906**, against difflib's 0.545.

**Corpus.** 13,244 tracks / 6,291 albums / 4,344 artists / 154 playlists = **24,032 raw names**,
**18,461 distinct after `base_string`**. (H §2's 9,949 tracks is superseded — R's scrobbling has
been adding rows.)

**Index build, per request if uncached:** `base_string` over all 24,032 names **63 ms**; trigram
sets over the 18,461 distinct **45 ms**. Total fixed cost **108 ms**.

**Scoring without a prefilter — the design that was rejected:**

| query | full scorer over all 18,461 |
|---|---:|
| `willow` | 366 ms |
| `love` | 324 ms |
| `the` | 273 ms |
| `radiohead creep` | 791 ms |
| `bohemian rapsody` | 841 ms |

**Scoring with the §4.3 prefilter — the design specced:**

| query | candidates | prefilter | score | total |
|---|---:|---:|---:|---:|
| `willow` | 37 | 5 ms | 1 ms | **6 ms** |
| `cardigan` | 9 | 5 ms | 0 ms | **6 ms** |
| `bohemian rapsody` | 5 | 10 ms | 0 ms | **10 ms** — top match 0.97 |
| `radiohead creep` | 28 | 10 ms | 1 ms | **11 ms** |
| `love` | 421 | 4 ms | 8 ms | **12 ms** |
| `the` | 2,331 | 4 ms | 40 ms | **44 ms** |

**Floor selectivity, names passing out of 18,461** — the measurement that set
`RELEVANCE_FLOOR = 0.5`:

| query | ≥ 0.35 | ≥ 0.5 | ≥ 0.6 |
|---|---:|---:|---:|
| `willow` | 4,395 | 675 | 327 |
| `love` | 6,516 | 2,900 | 977 |
| `the` | 7,619 | 4,561 | 2,465 |
| `radiohead creep` | 5,584 | 251 | 12 |
| `bohemian rapsody` | 6,761 | 165 | 8 |

Two conclusions the design rests on. **0.35 admits a quarter to 40% of the library** — it does not
bound anything. And **no floor can bound a short common query**, because thousands of names really
do match "the"; what bounds a result list is the rank and a cap, which is why `SECTION_MAX` exists
and why See more fetches rather than pre-rendering.

### 10.2 What the shipped path actually costs (measured at Verify, 2026-08-29)

**§10's 6–44 ms is the matcher, and only the matcher.** It is not what a request costs, and the
distinction was not drawn when those figures were taken. A real `/api/search` request against the
running app measures **185–340 ms**, and the gap is not in anything §4 describes.

| | measured |
|---|---:|
| `/api/search` end to end, any real query | **185–340 ms** |
| `rank()`, `'bohemian rapsody'` / `'willow'` / `'the'` | 109 / 138 / 220 ms |
| — of which `_rank_artists` | **85–99 ms, on every query** |
| — of which stage 1 + `_score_names` | 5–45 ms (this is §10's figure, and it is accurate) |
| index rebuild, when `data_version` has moved | 285 ms |

`_rank_artists` is `scoring.artist_scores`, which has a **fixed ~71 ms floor for a single
artist** — `_artist_role_rows` scans the `track_artist_role` *view* (60 ms at n=1, 86 ms at
n=500, i.e. essentially independent of how many artists matched) plus `_version_scores_maps` at
11 ms. This is a pre-existing `scoring.py` shape, not something L introduced: K's `/search` paid
it once per page load, and L pays it per keystroke.

It does, however, mean **§4.7's licensed exception is justified on a premise that does not hold
for artists**: "cheap because it reads materialized scores" is true of the version tier and false
of `artist_scores`, which aggregates. Finn's call at Verify was that **the latency is fine** and
this is out of scope; it is recorded here so it is not rediscovered as a finding.

---

## 11. Roadmap

- Mark **L ✅ DONE** in `docs/Planning/roadmap.md` at Verify, pointing at this spec.
- Add L to the **Spec index**.
- **Record that L was taken ahead of F/G and O**, which the Order block places before it. Finn's
  call, 2026-08-29. No technical dependency in either direction; O is still gated on the
  `api_request` log catching a real lockout, which is a wait for data rather than for work.
- The Order block's `F/G ──► L (search)` edge is updated to reflect that L landed first.

---

## 12. Tests

Two of these are here specifically because a test written from the *new* rule alone would pass
against the *old* implementation. §2 is the list of replaced rules; these are the ones that need
a test asserting the replacement actually happened.

**Replaced-rule assertions.**

- **A song matches on its own title only** (replaces K §10's "`track.name`, or any credited
  artist's name"). Fixture: a track titled "Wait a Minute!" credited to an artist named "Willow",
  and a track titled "Willow" by someone else. For `q=willow`, assert the first is **absent** from
  both the Songs section and the combined list, while the artist Willow **is** present in Artists.
  The negative is the whole test — K's implementation returns both songs and passes any assertion
  that only checks the second is there.
- **No LIKE wildcards** (replaces K §10's unescaped-`%` note). `q=%` finds nothing rather than
  everything.

**The matcher.**

- Typo tolerance: `bohemian rapsody` returns a track named "Bohemian Rhapsody".
- Word order: `rhapsody bohemian` returns it too.
- Accent folding: `beyonce` finds an artist named "Beyoncé".
- The `assoc` bump: two tracks with the **identical** title "Creep", one credited to Radiohead;
  for `q=radiohead creep` the Radiohead one ranks higher. Identical titles are what isolate
  `assoc` — with different titles, `own` could explain the ordering.
- `RELEVANCE_FLOOR`: a name scoring below it is absent from results.
- **`SCORE_FLOOR`: an entity with no row in `score` is still returned on an exact name match.**
  This is the hole §4.5 names — without the floor it ranks 0 and vanishes, and every fixture whose
  entities happen to be scored would pass regardless.

**Caching.**

- **A name inserted after the first search is findable by the second.** A cache with no staleness
  check passes every test built on a fixture DB stamped once before the run, so this is the only
  test that can fail for it.

**Result assembly.**

- Songs dedupe to one row per version group, keeping the highest-`relevance` member.
- Most Relevant holds at most `COMBINED_LIMIT` rows and mixes types.
- A section renders at most `SECTION_LIMIT` rows; `/api/search/more` returns at most `SECTION_MAX`.
- `/api/search/more?type=` rejects a type outside the whitelist.
- A query shorter than `MIN_QUERY_LEN` returns nothing.

**Routes.**

- **`/api/search` writes nothing.** Given a track with no `track_group` row, assert the row count
  is unchanged after the request — this is §5's decision, and nothing else in the suite would
  notice it being quietly "fixed" by adding an `ensure_track_groups` call.
- `tests/routes_catalog.py` gains the two new endpoints. Its existing `Case("search_page", "GET",
  "/search?q=a")` becomes the **short-query** variant (`q=a` is now below `MIN_QUERY_LEN`), and a
  second variant with a real ≥2-character query is added beside it — a variant case proves the
  branch responds, so per P2-010 the real query needs a semantic assertion in `test_routes.py`,
  not just a non-5xx.

**Not tested, verified by hand.** `static/js/search.js` (debounce, sequence number, keyboard
navigation) and §8's `canonical_review.js` guard reorder — both JS, and the suite has no browser.
§8 in particular is a two-line reorder whose symptom only appears on the review queue's done
screen with the navbar box focused; Verify should reproduce it.

---

## 13. Out of scope

- **FTS5, and any new dependency.** §10's numbers say a pure-Python two-stage matcher over
  `normalize.base_string` is fast enough; FTS5's trigram tokenizer is available (sqlite 3.50.4)
  but is substring acceleration rather than typo tolerance, and it is an index to keep in sync.
- The `recent` horizon (§3).
- Search history, saved searches, synonyms, or an alias/nickname dictionary.
- Any change to what the entity pages themselves render.
- **W**'s CSS-framework conversion — Most Relevant and the See more control reuse `panel`,
  `data-table` and the existing macros, and get restyled with everything else when W lands.
