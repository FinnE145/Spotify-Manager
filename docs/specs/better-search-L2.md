# L2 — Better search, round two

**Step L2 of `docs/Planning/roadmap.md`.** Its brain-dump is
`docs/better-search/L2_handoff.md`, written by L's Verify pass on 2026-08-29; this spec is the
decided form of the three defects that file found. L2 stays on `feat/better-search-L` — a
sub-letter continues its letter's branch rather than opening a new one.

**This spec supersedes parts of `docs/specs/better-search-L.md`.** L §3's exact-match decision,
§4.4 (`own`/`assoc`/relevance), §4.5's rank key, §4.6's constants and §10.1's worked numbers are
replaced by §3–§4 and §7 here. Everything else in L stands and is still authoritative: §4.1's
normalization, §4.2's index and cache, §4.3's trigram prefilter, §4.7's cost rules, §5's routes,
§6's page, §7's dropdown and §9's module placement. L §2's table of what K's search lost still
describes what shipped.

---

## 1. What this is

L built a working two-stage matcher and it does what its spec says. Using it on the real library
exposed three defects **in the formula, not the implementation** — all three are one symptom of
the same thing: `SequenceMatcher.ratio()` is a character-overlap measure with a high noise floor
on short strings, and L's relevance formula was tuned around that floor rather than against it.

The complaint that raised L2, `q=test`, returned 97 rows of which **45 contained no "test" at
all**, ranked the song *Testarossa* and the album of the same name and the same score 22 points
apart, and put the `test` inside "greatest" above things that literally say *test*.

L2 replaces the second half of the matcher. Stage 1 is untouched.

---

## 2. What changes from L, clause by clause

| L's rule | L2's rule |
|---|---|
| §4.4: `tsim`'s third branch is a raw `SequenceMatcher(qt, nt).ratio()` | The same ratio, **gated: below `FUZZY_FLOOR` it is 0.0** (§4.1) |
| §4.4: `name_score = max(whole_string_ratio, mean of per-token tsim)` | The whole-string ratio is **gated by the same floor**, and a name yields a **per-query-token vector**, not one number (§4.2) |
| §4.4: `own` = its own name's score; `assoc` = the best associated name's score | Both retire. A name contributes **per query token**, and own and associated names are read **token by token** (§4.2) |
| §4.4: `relevance = min(1.0, own * (1 + BUMP * assoc))` | `relevance = mean over query tokens of max(own_i, ASSOC_WEIGHT * assoc_i)` (§4.2). The `min(1.0, …)` cap retires — the new form cannot exceed 1.0 |
| §4.4: an artist-only match is excluded **because `own` multiplies to a value below the floor** | It is excluded **by rule**: an entity whose own name contributes nothing is dropped before relevance is computed (§4.3) |
| §4.6: `BUMP = 0.5` | Retired, replaced by `ASSOC_WEIGHT = 0.5` (§4.4) |
| §4.7 / K §10: albums dedupe by nothing | Albums dedupe on **name + artists + overlapping release groups** (§5) |

**Unchanged and still load-bearing, named here because a test could otherwise be written against
the wrong one:** songs still rank at the **version tier** and dedupe to one row per version group,
keeping the highest-relevance member with ties broken by that member's track-tier score (L §4.7);
artists still dedupe onto their `artist_alias`-resolved id; playlists dedupe by nothing;
`rank_key = max(score, SCORE_FLOOR) * relevance ** ALPHA` keeps its shape and both its constants
(L §4.5); ranking still sees every candidate before any cap (L §4.7).

---

## 3. Decisions taken in planning, with their reasons

- **The gate is a flat threshold, not a curve or a length-aware edit rule.** Measured, one
  character substituted in a 5-letter word scores 0.800 — and `test`/`best` **is** one character
  substituted in a 5-letter word. No threshold, and no edit-distance rule, separates a short-word
  typo from a different short word. The gate declines to guess rather than guessing badly; the
  cost is named in §7 and accepted.
- **`FUZZY_FLOOR = 0.85`, and 0.88+ was measured and rejected.** Across 11 typo queries 0.85 keeps
  9 and improves 2; 0.90 additionally loses `cardigen`→cardigan and `beyonse`→Beyoncé. The
  handoff's "all noise ≤ 0.800, all signal ≥ 0.933, the gap is empty" reading is **corrected here**:
  real typos land as low as 0.857, so the gap is narrower than that sample suggested and 0.85 is
  the only defensible cut in it.
- **Gating alone was measured and rejected.** Keeping `own * (1 + BUMP * assoc)` and gating only
  the fallback makes multi-token queries *worse*: on `taylor swift cardigan` the track *cardigan*
  takes `own` = 1 of 3 tokens = 0.333, and `0.333 * (1 + 0.5 * 0.667) = 0.444` is below the floor,
  so **the right answer disappears entirely**. L's mean over query tokens is survivable today only
  because the noise floor props it up; removing the floor without reshaping the mean is not an
  option, which is the handoff §3's structural finding reproduced.
- **The self-titled double-count needs no separate fix.** Under per-token coverage a name
  appearing as both `own` and an associated name contributes `max(own_i, 0.5 * own_i) = own_i`.
  One string can no longer be read twice, so the handoff's "fix B" and its "how strict is
  identical?" question both dissolve. There is no exclusion rule to write.
- **L §3's *"an exact match does not automatically sort first"* survives.** The evidence against it
  reduces, once the noise is gone, to one contestable case: on `q=test` the playlist "Indie Rock
  Mix (test)" (relevance 1.000, score 69.5) outranks *Testarossa* (relevance 0.910, score 88.1).
  Which of those should come first is a genuine judgement call, so it is not grounds for changing
  the rule.
- **Searching an artist's name still does not return that artist's songs.** Under coverage this
  became a one-constant change rather than a consequence of the formula; it is deliberately not
  taken. §4.3 is what holds the line.
- **Playlists get no dedupe.** Exactly one pair of playlists shares a name in the whole library.
- **Album cover equality was measured and rejected as a dedupe key.** It collapses **0 of 294**
  duplicate name+artist groups: Spotify issues every duplicate album id its own artwork URL, so
  the four ids behind "Under the Willow Tree" carry four different covers. §5's rule is the one
  that works.
- **`ALPHA` and `RELEVANCE_FLOOR` keep L's values.** The handoff expected both to need re-tuning
  after the rework; measured against the new formula they do not. `ALPHA = 2.0` lets a partial
  match retake `q=test`; `4.0` changed no ordering found.

---

## 4. The matcher

Every constant stays a **module-level constant with a warning comment** in `search.py`, following
`scoring.py`'s convention and for H §10's reason — not in `config.py`, not environment-tunable.

### 4.1 The gate

```
tsim(qt, nt) = 1.0                            if qt == nt
             = 0.85 + 0.15 * len(qt)/len(nt)  if nt.startswith(qt)
             = r if r >= FUZZY_FLOOR else 0.0 otherwise, r = SequenceMatcher(qt, nt).ratio()
```

The equality and prefix branches are unchanged and are **not** gated — the prefix branch is what
makes an as-you-type query work (`tsim("boh", "bohemian")` = 0.906) and it is not a fuzzy match.

**The same gate applies to the whole-string reading** used in §4.2. Gating only `tsim` leaves the
noise a second way in: `beyonce` against `beyond` scores 0.769 *both* per-token and whole-string,
so a one-sided gate changes nothing for it.

### 4.2 Per-token coverage

A name no longer yields a score. It yields one value **per query token**:

```
whole    = SequenceMatcher(query_norm, name).ratio(), gated by FUZZY_FLOOR
tok(name)[i] = max( max over name tokens nt of tsim(query_token[i], nt),  whole )
```

Folding `whole` into every token is what preserves a whole-string match across token boundaries:
`bohemianrhapsody` (no space) is one query token whose per-token reading against "bohemian
rhapsody" is 0.667 and therefore gated to 0, while `whole` is 0.970 and carries it.

Then, per entity:

```
own_i   = tok(own name)[i]
assoc_i = max over associated names a of tok(a)[i]        (0.0 if it has none)

relevance = mean over query tokens i of  max(own_i, ASSOC_WEIGHT * assoc_i)
```

Associated names are unchanged from L §4.2: a track's are its album's name and each credited
artist's; an album's are its credited artists'; artists and playlists have none.

**This is a coverage measure, and that is the point.** L's mean over query tokens already *was*
coverage; it only failed because an unmatched token scored 0.3–0.8 instead of 0. Gating supplies
the zero, and reading own and associated names token-by-token instead of name-by-name is what
lets a query spread its evidence across them — `taylor swift cardigan` covers two tokens from the
artist and one from the title, for `(0.5 + 0.5 + 1.0) / 3 = 0.667`.

An entity with `relevance < RELEVANCE_FLOOR` is dropped outright, as in L.

### 4.3 Own must contribute

**An entity whose own name contributes nothing to any query token is dropped**, before relevance
is computed and regardless of what its associated names score.

This is L §4.4's headline decision — the one L was raised to fix, that an artist-only match on an
unrelated song title must not be a result — restated as a rule. L got it from arithmetic: `own`
multiplied, so `own = 0` forced relevance to 0. Coverage has no such multiply, and at
`ASSOC_WEIGHT = 0.5` an artist-only match lands at exactly 0.500, which is exactly
`RELEVANCE_FLOOR` and therefore **survives**. Stating the rule directly is better than choosing
`ASSOC_WEIGHT` to make a threshold coincidence come out right: the constant stays free to be
tuned, and the guarantee stops depending on two unrelated numbers being equal.

The worked case is L §10.1's: "Wait a Minute!" by the artist Willow, on `q=willow`, is absent.

### 4.4 Constants

| Constant | Value | What it does |
|---|---:|---|
| `FUZZY_FLOOR` | 0.85 | **New.** Below this a `SequenceMatcher` ratio is 0.0, both per-token and whole-string. 0.88+ measured and rejected (§3) |
| `ASSOC_WEIGHT` | 0.5 | **New, replaces `BUMP`.** What an associated name's evidence is worth against the entity's own |
| `MIN_QUERY_LEN` | 2 | Unchanged |
| `TRIGRAM_FLOOR` | 0.5 | Unchanged — stage 1 is untouched |
| `RELEVANCE_FLOOR` | 0.5 | Unchanged, re-measured against the new formula (§3) |
| `ALPHA` | 3.0 | Unchanged, re-measured against the new formula (§3) |
| `SCORE_FLOOR` | 10.0 | Unchanged |
| `COMBINED_LIMIT` / `SECTION_LIMIT` / `SECTION_MAX` / `DROPDOWN_LIMIT` | 20 / 10 / 200 / 5 | Unchanged |

`BUMP` is deleted.

### 4.5 What this costs, and the shape it forces

`_score_names` returns `{normalized_name: tuple_of_per_token_values}` instead of
`{normalized_name: score}`. A name is still scored **once** and shared by every entity bearing it
(L §4.2), and `max(per_token, whole)` is folded in at that point rather than per entity, so the
per-entity step stays a few `max` calls over a tuple. Measured, `rank()` costs 125–236 ms — the
same as L and dominated by the same `scoring.py` lookups L §10.2 identified, with fewer candidates
to score. **Latency is out of scope** (handoff §6: "the current latency is fine").

---

## 5. Album dedupe

Two album rows collapse into one search result when **all three** hold:

1. their normalized names are equal, and
2. their normalized credited-artist lists are equal, and
3. their owned tracks' `track_group.release_id` sets **intersect**.

Order is defined so the result is deterministic: candidates are sorted by `rank_key` descending,
and each album attaches to the first already-kept album it collapses with, or is kept itself. The
kept row is therefore always the highest-ranked of its group.

Conditions 1 and 2 alone are not enough — 294 name+artist groups cover 624 album rows, and 75 of
those groups are genuinely different releases (deluxe editions, a single against the album that
reissued it). Condition 3 is what tells them apart, and it is the canonical engine already
answering a question it exists to answer: the four ids behind "Under the Willow Tree" all hold one
track sitting in release group 4617.

**This is display-only.** Nothing is written, no `album_alias` table is introduced, and
`canonical_group` / `track_group` are read, never touched. The release-id lookup is one query over
the **candidate** album ids, keeping L §4.7's rule that work is proportional to what is ranked and
not to the library.

Measured: 247 rows collapse away across 219 of the 294 groups; `q=willow`'s Albums section goes
from 8 rows to 5.

---

## 6. What does not change

Everything in L not named in §2. Specifically, and because each is a place a reimplementation
could drift: stage 1's trigram prefilter and `TRIGRAM_FLOOR`; the index and its
`PRAGMA data_version` cache on a dedicated module-level connection; the three surfaces slicing one
set of ranked lists; hydration touching only the rendered slice; `/search`'s `is_searchable` gate
on the `ensure_track_groups` write lock; the dropdown's debounce, sequence numbers and keyboard
handling; See more; and `canonical_review.js`'s guard order from L §8.

No template, route, JS or schema change is required by L2. The work is `search.py` and its tests.

---

## 7. Measured facts

Measured 2026-08-30, read-only against the real `symr.db`, through a reference implementation of
§4. Deterministic scores over a fixed corpus, not timings — a figure that disagrees means the
library changed or the implementation does not match this spec. The corpus is L §10.1's
(18,461 distinct normalized names).

### 7.1 The matcher's worked numbers

These replace L §10.1's table and are §8's expected values.

| case | query | vs | relevance |
|---|---|---|---:|
| exact | `willow` | "Willow" | **1.000** |
| word order | `rhapsody bohemian` | "Bohemian Rhapsody" | **1.000** |
| typo | `bohemian rapsody` | "Bohemian Rhapsody" | **0.985** (L: 0.970) |
| own + assoc | `radiohead creep` | "Creep" *by Radiohead* | **0.750** (L: 0.884) |
| own only | `radiohead creep` | "Creep" by anyone else | **0.500** (L: 0.643) |
| partial own | `radiohead creep` | the artist "Radiohead" | **0.500** (L: 0.750) |
| three tokens | `taylor swift cardigan` | "cardigan" *by Taylor Swift* | **0.667** (L: dropped) |
| artist-only match | `willow` | "Wait a Minute!" by Willow | **dropped by §4.3** |

Per-token similarities:

| pair | value |
|---|---:|
| `tsim("boh", "bohemian")` — prefix branch, ungated | **0.906** |
| `tsim("rapsody", "rhapsody")` — signal, passes the gate | **0.933** |
| `tsim("test", "best")` — 0.750 raw | **0.0** |
| `tsim("test", "greatest")` — 0.667 raw | **0.0** |
| `tsim("beyonce", "beyond")` — 0.769 raw | **0.0** |
| `tsim("cardigan", "cardiac")` — 0.800 raw | **0.0** |

### 7.2 What it does to result lists

| query | L: rows / rows containing the query | L2: rows / containing |
|---|---:|---:|
| `test` | 97 / 52 | **13 / 13** |
| `willow` | 48 / 16 | **23 / 23** |
| `cardigan` | 11 / 4 | **6 / 5** |
| `beyonce` | 9 / 2 | **2 / 2** |
| `the` | — | 2,683 / 2,683 |
| `love` | — | 537 / 537 |

`cardigan`'s one non-containing row is the artist "Charlotte Cardin" at 0.857, just above the
gate; it ranks last. Multi-token queries are omitted from this table because "contains the whole
query string" is not a meaningful test for them.

Ordering, on the four cases the handoff raised:

| query | L | L2 |
|---|---|---|
| `test` | *Testarossa* (song) 88.1 at #1, the identically-named album 66.4 at #3 | **both 66.4, adjacent** |
| `radiohead creep` | Radiohead 24.5, *Creep* 22.0, and 19 further rows | ***Creep* first**, 3 rows total |
| `beyonce` | "Beyond" (relevance 0.769) first, the artist Beyoncé fourth | **only the two exact matches** |
| `cardigan` | "Cardiac Arrest" second | **absent** |

### 7.3 The gate's cost to typo tolerance

11 typo queries, asking where the intended target lands in the combined list:

| | L | `FUZZY_FLOOR` 0.85 | 0.90 |
|---|---|---|---|
| found, and 8 of them at #1 | 9 | **9** | 7 |
| improved by the gate | — | `beyonse` #2→#1, `taylor swfit` not-found→#1 | same |
| lost | — | `creap`→creep (0.800) | + `cardigen`, `beyonse` |
| never found in any variant | `tets`→test | same | same |

`creap`→*creep* is the accepted loss and the reason §3 states the gate as a refusal to guess.

---

## 8. Tests

The eight gaps the handoff §5 itemises are **all in scope for this step**, written after the §4–§5
implementation lands, plus the new behaviour below. Every test carries its one-line source comment
naming the clause it derives from, per `docs/specs/codebase-health-P.md` §2.

**New behaviour to pin:**

- **The gate, at the value.** `_tsim("test", "best")` and `_tsim("test", "greatest")` are `0.0`,
  and `_tsim("rapsody", "rhapsody")` is `0.933`. Assert the values, not presence: a mutant that
  gates at 0.95 also returns 0.0 for the first two.
- **The gate does not touch the prefix branch.** `_tsim("boh", "bohemian") == 0.906`. This is also
  handoff §5's 1c, whose line L's suite never executed at all.
- **The whole-string reading is gated too.** A query and name scoring in the 0.75–0.80 band
  whole-string with no token match — `beyonce` against `beyond` — yields relevance 0. A test that
  only gates `_tsim` passes against a half-fixed implementation.
- **`whole` still carries a cross-boundary match.** `bohemianrhapsody` against "Bohemian Rhapsody"
  scores 0.970, not the 0.667 its per-token reading gives. This subsumes handoff §5's 1e, and its
  point stands: **assert the value**, since both readings clear the floor in L's version.
- **Coverage spreads across own and associated names.** `taylor swift cardigan` gives the track
  *cardigan* by Taylor Swift relevance `0.667`, and it is the top song. **This is the test that
  fails against L's formula, where it is dropped**, and it is the discriminating case for the
  whole step.
- **A self-titled album is not counted twice.** A track whose album shares its name, and a second
  entity with the same name and score, rank equally. This is handoff §5's 1g, and §2 above names
  what it replaces: 1g's rule is L §4.2's `assoc` definition, so the fixture must assert
  *equality of the two rank keys*, not merely that both appear.
- **§4.3's rule, by rule.** An entity whose own name contributes nothing is absent even when an
  associated name matches exactly. Construct it at `ASSOC_WEIGHT * 1.0 == RELEVANCE_FLOOR`
  exactly — the artist-only case — so a missing §4.3 guard is a failure rather than a rounding
  question.
- **Album dedupe, all three conditions.** Two same-name same-artist albums whose tracks share a
  release group collapse to the highest-ranked; two whose release groups are disjoint do not; two
  with the same name and *different* artists do not. Covers must not be part of any fixture's
  reasoning — §3 rejected them, and a fixture that gives the two rows one cover would pass against
  an implementation that dedupes on covers.
- **`ALPHA` and `RELEVANCE_FLOOR` still bite.** L's suite has no test that fails when either moves;
  handoff §5's 1a (three surviving mutants on `combined`'s ordering) is the same gap and is the
  most valuable one on the list.

**Sequencing.** Handoff §5's 1a, 1d, 1e and 1g assert behaviour this step changes and are written
against §4's formula, not L's. 1b, 1c, 1f and 1h are unaffected by L2 and can be written at any
point.

---

## 9. Roadmap

L2 is **next**, ahead of W and V. `docs/Planning/roadmap.md`'s order block and its L2 section are
updated in this branch's second planning commit, along with the supersession pointers added to
`docs/specs/better-search-L.md`.

---

## 10. Out of scope

- **Latency**, per handoff §6 and Finn's *"the current latency is fine."* The 185–340 ms a dropdown
  request costs is `scoring.artist_scores`' ~71 ms floor for a single artist plus a 285 ms index
  rebuild after any write — a pre-existing `scoring.py` shape, not L's and not L2's.
- **An exact-match ranking floor** (§3).
- **Returning an artist's songs when their name is searched** (§3).
- **Playlist name dedupe** (§3).
- **Any change to stage 1**, the index, the cache, the routes, the templates or the JS (§6).
