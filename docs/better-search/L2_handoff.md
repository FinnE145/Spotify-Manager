# L2 — better search, round two: handoff for `/symr-plan`

**Written 2026-08-29 by L's Verify pass.** L is verified and correct against
`docs/specs/better-search-L.md`; everything below is work that pass *found* and
deliberately did not do. Feed this file to `/symr-plan` — it is the brain-dump,
and it is meant to replace re-deriving any of it.

**Every number here is measured**, on 2026-08-29, read-only against the real
`symr.db`, through `search.rank()` itself. They are deterministic scores over a
fixed corpus, not timings — `timings-contaminated-by-parallel-chats` does not
apply, and a figure that disagrees means the library changed or the algorithm
did. Don't re-derive them; do re-measure after any change to the matcher.

Three pieces of work:

| § | what | size |
|---|---|---|
| §2 | **Fix B** — a name must not count as its own `assoc` | one line + a spec amendment |
| §3 | **Defect 2** — the difflib fallback's noise floor, and the mean that leans on it | real design work; the obvious fix breaks L §4.4 |
| §4 | **Defect 3** — `relevance` and `score` are not on comparable scales | design work, entangled with §3 |
| §5 | **Eight tests** L's suite is missing | mechanical, but write them *after* §2–§4 |

§2 is independent and could land alone. §3 and §4 are the same problem seen
from two sides and should be planned together. §5 depends on all three, because
three of the eight tests assert behaviour §2–§4 may change.

---

## 1. What set this off

Finn, searching `test` on the real library, got this top five:

```
1. Song      Testarossa                              score 88
2. Playlist  Indie Rock Mix (test)                   score 69
3. Album     Testarossa                              score 88
4. Song      Leaving, On a Jet Plane - "Greatest Hits" Version   score 67
5. Album     TESTING                                 score 55
```

His three complaints, all correct:

- Why are two entities with **the same name and the same score** (the two
  Testarossas) not in the same place?
- Why does the `test` inside **"greatest"** outrank things that literally say
  *test*?
- Positions 7 and 8 were **"The bEST"** and **"TESsa violeT"**.

The internals, with `own` / `assoc` / `relevance` exposed:

```
 # type      name                                    own assoc   rel  score   rank
 1 song      Testarossa                            0.910 0.910 1.000   88.1   88.1  assoc<-'testarossa'
 2 playlist  Indie Rock Mix (test)                 1.000 0.000 1.000   69.5   69.5
 3 album     Testarossa                            0.910 0.000 0.910   88.1   66.4
 4 song      Leaving...- "Greatest Hits" Version    0.667 0.667 0.889   66.9   47.0  assoc<-'john denvers greatest hits'
 5 album     TESTING                               0.936 0.000 0.936   55.5   45.4
 6 song      The greatest gift we've got            0.667 0.667 0.889   33.1   23.3  assoc<-'the greatest gift weve got'
 7 song      The Best                              0.750 0.750 1.000   22.1   22.1  assoc<-'the best'
 8 artist    Tessa Violet                          0.667 0.000 0.667   69.1   20.5
 9 album     TESTING                               0.936 0.000 0.936   21.5   17.6
10 song      Testarossa v2                         0.910 0.910 1.000   16.9   16.9  assoc<-'testarossa v2'
```

**Nothing here is a bug in the implementation.** L is a faithful build of its
spec; every one of these numbers is what L §4.4/§4.5 asks for. The defects are
in the formula.

---

## 2. Fix B — a name must not count as its own `assoc`

### What is wrong

L §4.4 defines `assoc` as "the best score among its **associated** names", and
§4.2 makes a track's associated names its album's name and each credited
artist's. Its stated job (§4.4) is to make a *two-part* query work: on
`radiohead creep`, `own` comes from the title and `assoc` from a different
piece of evidence, the artist.

When a track is on a self-titled album, `assoc` is **the same string as `own`**.
The formula reads one match twice and multiplies:

```
song  Testarossa   own 0.910, assoc 0.910 (its own album)  ->  rel 1.000  ->  88.1
album Testarossa   own 0.910, assoc 0.000                  ->  rel 0.910  ->  66.4
```

Same name, same score, 22 points apart. That is Finn's first complaint exactly,
and it is not a tuning artefact — it is one string counted as two.

Rows 7 and 10 above are the same pattern. So is `q=willow`, where **a song that
does not contain the query at all** reaches `relevance = 1.000`:

```
 6 song  When Will I See You Again   own 0.800  assoc 0.800 <- its own self-titled album  ->  rel 1.000  ->  62.6
```

### The change

In `search._relevance`, exclude an associated name identical to the own name:

```python
assoc_score = max((name_scores.get(a, 0.0) for a in assoc if a != own), default=0.0)
```

Both are already `normalize.base_string` forms, so the comparison is exact and
free.

### What it fixes, measured

| query | baseline | with B |
|---|---|---|
| `test` | song Testarossa **88.1**, album Testarossa **66.4** (split, ranks 1 and 3) | **both 66.4, adjacent** |
| `radiohead` | artist Radiohead **2nd**, behind a song called "Radio" (62.6 vs 58.1) | **artist Radiohead 1st** |
| `willow` | "When Will I See You Again" at rel 1.000, rank 62.6 | rel 0.800, drops below "Will Wood" (39.5) |
| `radiohead creep` (L §4.4's worked example) | Radiohead 24.5, Creep 22.0 @ rel 0.884 | **unchanged** |
| `bohemian rapsody` (L §10.1) | Bohemian Rhapsody 43.0 @ rel 0.970 | **unchanged** |

No regression in any spec-worked case. `q=willow`'s top five are unchanged
because they already had `own = 1.000`, which is the general shape: **B only
moves partial matches**, which is precisely where the double-count did damage.

### What it does not fix

Row 4 survives B untouched at 47.0: its `assoc` is `'john denvers greatest
hits'`, a genuinely *different* name, so B has nothing to strip. That case is
§3's.

### Decisions the plan session owes

- **Spec amendment.** L §4.2's "a track's associated names are its album's name
  and each credited artist's" and §4.4's `assoc` definition both need the
  exclusion written in, with the reason. L §10.1's table is unaffected (no row
  in it is a self-titled pair).
- **How strict is "the same name"?** B compares normalized strings exactly.
  `own = 'testarossa'` against `assoc = 'testarossa v2'` is two different
  strings carrying one piece of evidence, and B lets it through (row 10 keeps
  `assoc 0.910` — from a *different* album, so arguably legitimately). Decide
  whether "identical" is the right test or whether it should be something like
  "assoc must not itself be a near-match of own".
- **Is B a bug fix or a tuning change?** It reads as a bug fix — the formula's
  stated purpose is not served by counting one string twice — which matters for
  whether it needs its own roadmap letter or rides along.

---

## 3. Defect 2 — the fallback's noise floor, and the mean that leans on it

### What is wrong

`search._tsim`'s third branch is a raw `SequenceMatcher(...).ratio()`, which is
a **character-overlap** measure. It scores words that merely share letters
almost as highly as real matches:

```
NOISE (the fallback branch, all observed in real results)
  'test'     vs 'greatest'   = 0.667
  'test'     vs 'tessa'      = 0.667
  'test'     vs 'best'       = 0.750
  'beyonce'  vs 'beyond'     = 0.769
  'cardigan' vs 'cardiac'    = 0.800
  'willow'   vs 'will'       = 0.800
  'beyonce'  vs 'blink once' = 0.588

SIGNAL (what the fallback exists for — typos)
  'rapsody'          vs 'rhapsody'          = 0.933
  'bohmian'          vs 'bohemian'          = 0.933
  'bohemian rapsody' vs 'bohemian rhapsody' = 0.970
```

Against `RELEVANCE_FLOOR = 0.5` and `ALPHA = 3.0`, a 0.75 coincidence keeps 42%
of its score and a 0.94 typo keeps 83% — a factor of two, which any ordinary
score difference erases. That is why "Greatest Hits" (score 67) outranks
"TESTING" (score 55).

**How much of a result list this is.** For `q=test`, counting rows whose
normalized name does not contain the substring `test` at all:

| type | returned | actually contain "test" | noise |
|---|---:|---:|---:|
| songs | 30 | 14 | **16** |
| albums | 61 | 35 | **26** |
| artists | 5 | 2 | **3** |
| playlists | 1 | 1 | 0 |

**45 of 97 rows — 46% — are character coincidences.**

There is a clean separator in the observed data: **all noise ≤ 0.800, all
signal ≥ 0.933.** The gap 0.80–0.93 is empty. Substring containment separates
them too (`"test" in "greatest"` is False, `"test" in "testing"` is True) but
fails on typos, which are the fallback's whole reason to exist.

### Why the obvious fix breaks L §4.4

Sharpening the fallback (I simulated `ratio ** 3`) does clean up the noise —
`q=test` drops from 30/61/5 candidates to 15/10/1, and every remaining top row
literally contains the string. But it **destroys L §4.4's own worked example**:

```
radiohead creep:  Creep  rel 0.884 -> 0.704,  rank 22.0 -> 11.1
                  (and with RELEVANCE_FLOOR raised to 0.70: 1 song, 0 albums returned)
```

The reason is the structural half of this finding, and it is the important part:

> `_name_score`'s token term is a **mean over query tokens**, so a query token
> that matches nothing drags the whole score down — and the fallback's noise
> level is what props it back up.

Concretely, L §10.1's `own = 0.643` for *Creep* on `radiohead creep` is exactly

```
( tsim("creep","creep") + tsim("radiohead","creep") ) / 2  =  ( 1.000 + 0.286 ) / 2  =  0.643
```

**The spec's flagship number is carried by difflib's score for a deliberate
non-match.** Any change that makes non-matches score like non-matches also
makes multi-token queries score worse. The two cannot be separated inside
`tsim` alone.

### Directions for the plan session (none decided)

- Change the token term from a **mean over query tokens** to a coverage measure
  that is not dragged by an unmatched token — best-*k*, a weighted mean, or
  "sum of matched tokens / query length". This is the direction that addresses
  the root cause, and it is the biggest change.
- Sharpen at the `_name_score` level rather than per token, so the fallback's
  value is still available to the mean but the *name's* final score is
  penalized when nothing matched well.
- A threshold rather than a curve on the fallback (the measured 0.80/0.93 gap
  makes ~0.85 a defensible cut), combined with one of the above.
- Replace difflib entirely with an edit-distance rule that is length-aware.

Whichever is chosen, **re-measure the whole of L §10.1's table** — it is the
regression suite for this, and §10.1's `0.643` and `0.884` will move.

---

## 4. Defect 3 — `relevance` and `score` are not on comparable scales

Distinct from §3, and visible where the *noise* is not the problem:

```
q=beyonce
  1. song   Beyond                          rel 0.769  score 42.6  ->  32.8
  2. album  Blink Once                      rel 0.727  score 24.0  ->  14.2
  3. song   DELRESTO (ECHOES) (feat. Beyoncé)  rel 1.000  score 10.4  ->  10.4
  4. artist Beyoncé                         rel 1.000  score 10.4  ->  10.4

q=cardigan
  1. song   cardigan                        rel 1.000  score 74.1  ->  74.1
  2. song   Cardiac Arrest                  rel 0.800  score 53.1  ->  42.5
  3. song   Rocking A Cardigan in Atlanta   rel 1.000  score 27.1  ->  27.1
```

An **exact** match on the artist Beyoncé lands 4th, and an exact match on
"Rocking A Cardigan in Atlanta" lands behind "Cardiac Arrest". L §3 decided this
deliberately — *"An exact match does not automatically sort first. Score still
speaks."* — and it is worth asking in planning whether that decision survives
contact with use.

**ALPHA cannot fix any of it.** Measured at `ALPHA = 6.0`:

| | baseline (ALPHA 3) | ALPHA 6 |
|---|---|---|
| `test`: the two Testarossas | 88.1 / 66.4 | **88.1 / 50.0 — the gap widens** |
| `radiohead`: the artist | 2nd | **still 2nd** |

Raising ALPHA punishes partial matches harder, which is the right direction for
§3's noise and the *wrong* direction for §2's double-count and for a legitimate
partial match. It is a single knob against three problems. Fix §2 and §3 first,
then decide whether ALPHA still needs moving — with B alone, `q=radiohead` is
already correct at ALPHA 3.

Related sub-question worth settling: `q=willow` returns **"Under the Willow
Tree" four times**, four distinct album ids with identical names. L does not
dedupe albums by name (songs dedupe by version group, artists by
`artist_alias`; albums and playlists dedupe by nothing). Decide whether that is
fine or whether albums want a dedupe rule.

---

## 5. The eight tests L's suite is missing

Found by mutation during L's Verify pass: **40 hand-written mutants against
`search.py` and `app.py`, 23 killed, 17 survived.** Each row below is a mutant
that left the suite green. Coverage was run second and found nothing here —
`search.py` is at 98% — except that it independently confirms 1c: line 185, the
prefix fast path, is **never executed by the suite at all**.

| # | what survives | L clause | discriminating case |
|---|---|---|---|
| 1a | `combined` left unsorted; sorted **worst-first**; `ALPHA = 0.0` | §4.5, §6.1 | two entities of different types with known scores/relevance; assert order, and assert the right one survives `COMBINED_LIMIT = 1`. **Three mutants — the most valuable gap.** Cross-type ranking is L's headline and nothing asserts it |
| 1b | `_rank_artists` / `_rank_playlists` drop their `sort` | §4.7 | the artists/playlists twin of the existing songs and albums rank-before-cap tests |
| 1c | `_tsim`'s prefix fast path deleted | §4.4, §10.1 | unit: `_tsim("boh","bohemian") == 0.906` (§10.1's own number, against difflib's 0.545). **Unexecuted, not merely unasserted** |
| 1d | `TRIGRAM_FLOOR` 0.5 → 0.95 | §4.3 | the existing typo test is two-token, so `bohemian` admits the name by itself and the typo'd token never faces stage 1. A **single-token** `rapsody` query does |
| 1e | `_name_score` returns `token_term` only (the whole-string half deleted) | §4.4 | `bohemianrhapsody` (no space): `whole = 0.970` vs `token = 0.667`. Both clear the floor, so assert the **value**, not presence |
| 1f | `_trigrams` padding `f"  {s} "` → `s` | §4.3 ("so word boundaries participate") | unit test on `_trigrams`' literal output. Slightly brittle; there is no behavioural isolation |
| 1g | a track's **album name** dropped from `assoc`; an album's **credited artists** dropped from `assoc` | §4.2 | only the track→artist edge is covered today. **Write this after §2** — the rule it asserts is the one B changes |
| 1h | `type_label` hardcoded to `"Song"`; combined `image_url` hardcoded to `None` | §6.1 | Most Relevant and the dropdown render both and nothing reads either — the classic "produces a value nothing reads" |

Plus one minor: the songs dedupe tie-break on track-tier score (§4.7's
parenthetical "ties broken by that member's own track-tier score") is
unasserted — two members of one version group, identical relevance, different
track scores.

**Sequencing.** 1a, 1d, 1e and 1g assert behaviour §2–§4 may change; writing
them first means writing them twice. 1b, 1c, 1f and 1h are safe to write at any
point.

---

## 6. Out of scope, decided, or already handled

- **Latency.** Finn: *"the current latency is fine."* Recorded so a plan session
  does not rediscover it: a dropdown request costs **185–340 ms**, not L §10's
  6–44 ms. The matcher is fine (5–45 ms); `scoring.artist_scores` has a fixed
  **~71 ms floor for a single artist** (`_artist_role_rows` scans the
  `track_artist_role` view, 60–86 ms regardless of match count, plus
  `_version_scores_maps` at 11 ms), so `_rank_artists` is 85–99 ms of *every*
  query. Index rebuild is **285 ms**. This is a pre-existing `scoring.py` shape
  — K paid it once per page load, L pays it per keystroke. **Not L2's problem
  unless the latency stops being fine.**
- **Two spec corrections L's Verify pass is making**, so L2 does not redo them:
  L §10's timing claim is being corrected to the measured figures above, and
  §4.2's "data that changes perhaps twice a day" is being corrected — the cache
  invalidates on `PRAGMA data_version`, and `api_log.record()` writes an
  `api_request` row from its own connection for every outbound Spotify request.
- **`/search`'s write lock below `MIN_QUERY_LEN`** is also being fixed in L's
  Verify pass (`?q=a` currently takes `ensure_track_groups` + commit to return
  nothing).
- **Everything else in L works and was driven live**: typo tolerance, word
  order, accent folding, `%` finding nothing, the dropdown's keyboard
  navigation and sequence-number guard, See more, page load at exactly 20 + 4×10
  rows with zero extra requests, the dropdown over the immersive pages, and §8's
  `canonical_review.js` guard reorder (with T4's Enter-to-exit still intact).
  None of that needs re-verifying.

---

## 7. Questions planning should expect to answer

1. One step or two — does **§2 (fix B)** land on its own ahead of the matcher
   rework, or all together?
2. Is B's "identical normalized string" the right exclusion, or does `assoc`
   need to exclude *near*-matches of `own` too?
3. Does L §3's *"an exact match does not automatically sort first"* survive
   §4's evidence, or does an exact own-name match now get a guaranteed floor?
4. Which §3 direction — reshape the token term, sharpen at name level,
   threshold the fallback, or replace difflib?
5. Do albums (and playlists) get a name-dedupe rule?
6. `RELEVANCE_FLOOR` and `ALPHA` are almost certainly wrong once §3 lands.
   Re-tune both **after**, against a re-measured §10.1 table, not before.
