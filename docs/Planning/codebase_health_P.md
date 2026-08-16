# P — Codebase health: pre-spec

**Status: not a spec.** This is a findings record, written 2026-08-15, to be picked up by its
own `/symr-plan` session. It is deliberately *not* implementation-ready: nothing here has been
decided with Finn, no scope has been agreed, and the "what a plan session must decide" list in
§8 is the real starting point.

**Why it exists.** These findings came out of a bird's-eye review at the end of J's planning
session. They would otherwise live only in that chat transcript and be lost. Every measurement
below is dated and was taken against the real repo — a plan session should trust it or
re-measure deliberately, not re-derive it by accident.

**Read §6 before proposing anything.** A cleanup step's biggest risk is destroying the things
that are already working, and several of them look like clutter to a fresh eye.

---

## 1. Measurements, 2026-08-15

**~15,400 lines of code, ~7,700 of docs.**

| | lines | share of code |
|---|---:|---:|
| Python (app modules) | 8,364 | 54% |
| Static — JS 3,260, CSS 961 | 4,221 | 27% |
| Templates | 2,147 | 14% |
| `scripts/` (spent one-offs) | 650 | 4% |
| **total** | **15,382** | |
| docs (specs 6,381 · roadmap 644 · rest 718) | 7,743 | — |

**Python by subsystem:**

| | lines |
|---|---:|
| Web layer — `app.py` | 1,628 |
| Spotify ingest — `snapshot` 769, `roundtrip` 904, `history_import` 413, `backfill` 286, `spotify_client` 62 | 2,434 |
| Canonical grouping — `canonical_detect` 975, `canonical` 555, `canonical_autogroup` 179 | 1,709 |
| Infra — `db` 736, `jobs` 195, `config` 32 | 963 |
| Scoring — `scoring` | 931 |
| Feature read paths — `generations` 218, `artists` 218, `entities` 159, `grouping` 104 | 699 |

**Shape of the Python:** 357 functions, **median 10 lines**. Only 11 exceed 78 lines. Non-blank
lines are **24% comment or docstring** (715 comment lines, 981 docstring lines, 5,495 code).
**One `TODO` in the entire tree** (`roundtrip.py:35`, a documented deferral with a trigger
condition) and zero `FIXME` / `HACK` / `XXX`.

**DB, for context on §7:** 93 MB on disk. `play` 22.7 MB, `track` 21.6 MB, `album` 8.5 MB,
`score` 2.0 MB, `artist` 1.4 MB, `membership` 0.9 MB. About 30 MB of that is `raw_json`.

---

## 2. Finding — no automated tests, at 15,400 lines

`CLAUDE.md` records "Test / lint: none yet. Record them here verbatim once they exist." That is
still true. Every check in this project is a human clicking through pages once, at the end of a
feature, and never again.

**What this does and does not cover.** The specs' Verification sections are a real substitute
for *acceptance* — they are specific, they get run, and they have caught real defects. They are
not a substitute for *regression*: nothing detects a break in a module the current feature did
not touch, and there is no signal at all between features.

**Why it matters more here than in a typical personal tool.** `symr.db` is not reconstructible.
It holds seven years of streaming history (93,063 plays), 461+ hand-reviewed grouping pairs, 37
generations of curation, and the manual alias decisions from the round-trip. Spotify cannot
re-supply any of it. A silent corruption is permanent.

**The obvious first target.** `snapshot._diff_playlist_tracks` (`snapshot.py:631`, 80 lines) is
the single function whose bugs would silently corrupt history — it decides which memberships
get `removed_at`, including the ambiguous case where copies of a track departed and its
fallback rule guesses which. It is pure, deterministic, takes a list and a connection, does no
I/O and makes no API calls. It is close to the ideal unit-test target and has nothing.

Other high-value pure targets, in rough order: `canonical_detect`'s suffix classification and
`normalize_title`/`normalize_suffix`; `scoring.combine()` and `_display`/`_undisplay`
round-tripping; `grouping.group_cards`; `generations.runs()`; `snapshot._parse_track_item`.

**The fair counter-argument, which a plan session should weigh rather than dismiss.** The
*catastrophic* risk — corrupting the real Spotify library — is handled structurally, not by
tests: read-only scopes everywhere but one module, a live name-and-owner guard before every
write, replace-never-append. That is the right design and tests would add little to it. But
"we cannot corrupt Spotify" and "we will not quietly corrupt our own DB" are different
guarantees, and only the first is designed for.

**KISS applies but does not settle it.** `CLAUDE.md` says the goal is code that is done,
understandable and works — not production-grade. A full test suite would violate that. A dozen
unit tests over the pure functions above probably would not, and that tension is exactly what
the plan session has to resolve rather than assume.

---

## 3. Finding — `create_app` is 1,572 lines

The clearest structural defect in the repo, and the one that compounds.

`app.py` is 1,628 lines, of which `create_app` (line 36) is **1,572** — roughly 10% of the
entire codebase inside one function. Measured 2026-08-15:

- **71 view functions** are defined as closures inside it.
- **34 page routes / hooks, 925 lines**; **37 `/api/*` endpoints, 435 lines**.
- **42 `conn.execute` calls** live directly in it.
- The five largest view functions account for ~514 lines, a third of the function:
  `album_page` (131), `artist_page` (107), `playlist_page` (96), `canonical_index` (92),
  `search_page` (88). All five are doing **read-path work**, not routing.

**The tell.** `CLAUDE.md`'s own codebase map states the rule that entity read paths belonging
to no existing owner live in `entities.py`. Four page routes kept theirs inline anyway. The
rule exists, is written down, and the code quietly does not follow it — which is how a
convention dies.

**Consequences that are already true**, not hypothetical: no route is reachable except through
a full app instance (which is also part of why §2 is hard to start); the file is navigable only
by search; and every future feature adds to the same function — J alone adds a third
`before_request` hook and a new endpoint.

Directions a plan session might weigh: Flask blueprints grouped by area (entity pages, dev
tools, `/api/*`); or the lighter option of extracting only the read queries into the modules
that already own that data (`entities.py`, `canonical.py`, `generations.py`) and leaving the
routes thin. The second is closer to KISS and fixes the stated-rule violation directly.

---

## 4. Finding — `CLAUDE.md` is a hand-maintained second source of truth

5,472 words, 87 lines. It is genuinely excellent working context and is the reason a cold
session can be productive immediately — that is not in question. The problem is structural:
it is a **manually synchronized index of a codebase that changes every session**, its accuracy
is load-bearing, and nothing enforces it.

**A live instance, found in this session:** it states `app.py` has "**two** app-wide
`before_request` hooks in order". J (`docs/specs/partial-pulls-J.md` §4.3) adds a third. The
drift is caught only because that spec explicitly says to update the map at Verify time — i.e.
by a human remembering, which is exactly the mechanism being relied on everywhere else.

This is not an argument for deleting it. It is an argument for a plan session deciding what the
map is *for*: if it is the durable architecture record, some of it wants to be generated or
checked; if it is orientation, some of the detail that drifts fastest (exact hook counts, exact
route lists) may belong in code comments instead, where it sits beside the thing it describes.

---

## 5. Finding — a circular import between `artists.py` and `canonical_detect.py`

Verified 2026-08-15, module level in both directions:

- `artists.py:10` → `import canonical_detect`
- `canonical_detect.py:10` → `import artists`

It works today because Python caches partially-initialized modules and neither module *uses*
the other's names at import time. It is fragile rather than broken: adding a single
module-level call to the other's functions in either file breaks the import, and the error will
point somewhere unhelpful.

Context for why it happened, both sides deliberate: `canonical_detect` reads
`artists.artist_sets` directly to stay off `_fetch_tracks`'s ~350ms whole-library path, and
`artists` uses `canonical_detect` for duplicate-candidate detection. Neither dependency is
wrong; the pairing is.

**The rest of the import graph is clean** and worth recording so it is not re-derived:
`canonical.py`, `grouping.py`, `jobs.py` and `config.py` have **zero** internal imports (true
leaves), `db.py` depends only on `config`, and `app.py` importing everything is expected of a
route layer. This is the only cycle.

---

## 6. What is healthy and must survive the cleanup

**A cleanup session's biggest risk is tidying these away.** Each looks like clutter to a fresh
eye and is not.

- **The 24% comment/docstring density.** These are overwhelmingly *why*, not *what*, and most
  record a failure that actually happened: `roundtrip.py`'s replace-never-append and
  read-as-a-bag invariants, `jobs.py`'s explanation of why one lock replaced three,
  `spotify_client.py`'s `respect_retry_after_header=False` note, `scoring.py`'s `_worker_alive`
  finally-block reasoning. **A refactor that strips comments to "clean up" destroys the most
  valuable thing in the repo.** Comment density should come out of this step level or higher.
- **The stated module invariants.** "`canonical.py` never touches `track` or `membership`",
  "`snapshot.py` is read-only w.r.t. Spotify", "`roundtrip.py` is the only module that writes
  to the library", "`track.artists` is write-only, never read", "none of `canonical.py`'s
  functions commit — callers own the transaction". These are load-bearing and mostly enforced
  by convention. Any restructuring must preserve them explicitly, and moving code between
  modules is exactly what would violate one by accident.
- **The one-TODO discipline.** One `TODO`, zero `FIXME`/`HACK`/`XXX`, no commented-out code, at
  15,400 lines. Whatever this step does, it should not open a debt ledger — and it should not
  leave "cleanup part 2" markers behind.
- **`entity_link` centralization.** Roadmap M1c flagged 11 latent `url_for` bypasses; a grep on
  2026-08-15 returns **zero**. Entity linking goes through one macro and should stay that way.
- **Median function length of 10 lines.** Outside `create_app` the decomposition is good. The
  problem is one function, not a pervasive style.

---

## 7. Deliberate decisions that look like problems and are not

Recorded so a cleanup session does not "fix" them:

- **~30 MB of `raw_json` in `track` and `album`** — deliberate, per `docs/specs/track-metadata-A.md`.
  The track object is the complete and final universe of Spotify metadata and there is no second
  pull to plan for. It is ~1/3 of the DB and read only as a fallback. Keep it.
- **`scripts/` — 650 lines of already-applied one-offs.** `migrate_track_metadata.py`,
  `reset_misgrouped_pairs.py`, `seed_generations.py` are kept as the *record of what happened*,
  not as runnable code, and `backfill_track_details.py` is explicitly superseded. This is not
  dead code to delete; `CLAUDE.md` says so for each.
- **No SPA framework, no bundler, one IIFE per page.** Deliberate. JS is 3,260 lines and three
  files carry 58% of it (`canvas.js` 759, `canonical_review.js` 649, `roundtrip.js` 497); the
  other eight average 91 lines.
- **Plain HTML with minimal styling.** `CLAUDE.md`: function over form, and no visual polish
  unless asked.

---

## 8. What a plan session still has to decide

Nothing below has been discussed with Finn. These are the questions, not answers.

1. **Scope and order.** All four findings, or a subset? §3 (`create_app`) and §5 (the cycle) are
   bounded and mechanical; §2 (tests) is open-ended and §4 (`CLAUDE.md`) is a judgement call
   about what the map is for. They may not belong in one step.
2. **Tests: how far.** Nothing, a dozen unit tests over the pure functions in §2, or a real
   suite? And if any: what runner, where the files live, and the `CLAUDE.md` "Commands" entry
   that must be recorded verbatim once it exists. A test DB fixture strategy is its own
   question — `symr.db` is the test environment today (see the `symr-testing` memory).
3. **`create_app`: blueprints or query extraction.** Both are defensible; they have very
   different blast radii.
4. **Is this a behaviour-preserving refactor only?** A cleanup step that also changes behaviour
   is very hard to verify without §2 existing first — which is an argument for ordering tests
   before the refactor, or for accepting a manual verification pass.
5. **Lint/format.** Not investigated here at all. There is no linter, no formatter, and no
   config for either. Whether that is wanted is untouched ground.
6. **How the step gets verified**, given that a refactor's whole point is that nothing observable
   changes.

---

## 9. Caveat on this assessment

Every line of this codebase was written by Claude, and most of the conventions it is being
graded against were written by Claude too — so "follows its own rules" is partly self-scoring,
and the four findings above are the ones a self-review was *able* to see. An outside reviewer
would likely find things this did not.

The more meaningful evidence in favour is indirect: this session picked the project up cold
from `CLAUDE.md` and the specs, and they held up well enough to find a real, unnoticed defect in
`snapshot.py` (`docs/specs/partial-pulls-J.md` §0.2) that the roadmap had missed.
