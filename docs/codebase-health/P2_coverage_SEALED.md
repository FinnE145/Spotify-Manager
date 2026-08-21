# P2 — Coverage measurements — **SEALED**

> ## ⛔ DO NOT READ THIS FILE IF YOU ARE WRITING TESTS.
>
> **May open it:** a **Verify** session (any session), and **P2 session 5**.
> **Must not open it:** P2 sessions **2, 3 and 4** — the implement sessions that write tests —
> and any future implement session adding tests to `tests/`.
>
> If you are an implement session and something pointed you here, that pointer was telling you the
> file exists, not that you may read it. Close it and carry on. Nothing in here is needed to write
> a good test; that is the entire point.

**Why the seal, and why it is not theatre.** `P2_tests.md` §7 and `codebase-health-P.md` §2 both
turn on one fact: a coverage map in view while writing optimises for *executing lines*, and the
cheapest way to execute a line is the tautological characterization test §2 exists to prohibit.
Publishing the gap list would defeat the blind-writing discipline just as thoroughly as watching
the map live — the session would simply work the list instead. Two specific failures this prevents,
both of which a bare number invites:

- **anchoring up** — "session 1 got 76%, mine should beat that", which buys lines, not assertions;
- **anchoring down** — "session 1 got 76%, I'm probably around there, I can stop", which ends a
  session on a number rather than on §5's floor.

**Why a Verify session may read it.** Verify writes no tests. It reviews a diff that is already
finished, so a gap list cannot bend what got written — the work is done before the file is opened.
That is the whole distinction, and it is why the measurement cadence below is safe.

**Cadence (decided 2026-08-21).** Coverage is measured in **each session's Verify pass**, scoped to
that session's own modules, and recorded here. Session 5 does the consolidated whole-suite pass and
takes Finn's ruling on what is worth filling. Finn fills gaps during Verify passes as they come, and
amends the entries below when an earlier session's numbers move.

**No numeric threshold, ever.** A suite of `assert True` reaches 100%. Coverage here is a
gap-finder and nothing else.

**The number never leaves this file.** It does not go in `codebase-health-P.md` §10's status table,
`P2_tests.md`, `P2_findings.md`, `CLAUDE.md` or a commit message — all of which an implement
session reads as a matter of course.

---

## The invocation

```
venv/bin/python -m pytest -q --cov=<module> [--cov=<module> …] \
    --cov-report=term-missing --cov-branch
```

Scoped per session rather than whole-repo: while P2 is part-written, a whole-repo number is
dominated by modules nobody has reached yet and means nothing. `tests/conftest.py`'s `sqlite3`
guard carries a narrow basename exemption for coverage.py's own data file — without it the suite
passes and pytest then exits `INTERNALERROR` on the report.

---

## Session 1 — Ingest (measured 2026-08-21, at Verify)

`--cov=snapshot --cov=db --cov=roundtrip --cov=history_import --cov=api_log --cov=jobs`,
against 268 passing tests.

| module | statements | miss | branch | partial | cover |
|---|---|---|---|---|---|
| `api_log.py` | 43 | 0 | 2 | 0 | **100%** |
| `jobs.py` | 95 | 1 | 14 | 1 | **98%** |
| `db.py` | 89 | 5 | 40 | 7 | **91%** |
| `snapshot.py` | 360 | 69 | 112 | 12 | **81%** |
| `history_import.py` | 134 | 39 | 28 | 3 | **72%** |
| `roundtrip.py` | 317 | 119 | 88 | 12 | **59%** |
| **total** | **1038** | **233** | **284** | **35** | **76%** |

**The shape matters more than the number: the uncovered mass is job bodies and page-facing
read/write paths, not algorithms.** Everything session 1 was actually aimed at — `_diff_playlist_tracks`,
the refresh/full-pull/epoch rules, the shared track-ingest path, `_run_batch`'s recording rules,
the dedup hash, the queue partition — is at or near full coverage. What is missing is the code
*around* those: the `_run()` loops that call them and the page's own reads and writes.

### Gaps worth filling

- **`roundtrip._match_substitutes`** (15 lines) — the reconciliation rule deciding whether a
  substitute is auto-aliased or flagged `needs a manual alias`. A write decision with corruption
  stakes and a real spec clause behind it (`foreign-roundtrip-D.md` §4.5: normalized full title
  *and* album artist must both match). Would have been on `P2_tests.md` §5's floor had P1 flagged it.
- **`roundtrip.set_manual_aliases`** (23 lines) — the hand-driven write into `track_uri_alias` from
  the page's pick-a-track table. Untested write path; a wrong alias permanently mislabels a play.
- **`roundtrip._reconcile_batch`** (40 lines) — `_reconcile`'s stop/clear behaviour is covered, its
  batch body is not.

### Gaps of middling value

- **`history_import._run_import` + `_finish`** (59 lines) — the import job loop. Parsing, dedup,
  field handling and coverage counts are all covered; the loop that drives them is not.
- **`snapshot._run_backfill`** (55 lines) — the track-metadata refill job (one `GET /v1/tracks/{id}`
  per track, `raw_json` mop-up). Distinct from `backfill.py`, which is session 2's.
- **`roundtrip.probe` / `_probe_dead`** (17 lines) — the off-quota `open.spotify.com` probe. Needs a
  faked `requests.get`, since the suite blocks outbound HTTP by design.

### Deliberately not worth filling

- **`db._migrate`**, 5 lines — individual one-line `ALTER TABLE` arms the `legacy` fixture does not
  build (`card.note`, `canonical_group.auto_run_id`, `track.isrc`, `album.tracklist_json`,
  `artist.image_url`). The migration *pattern* is covered, and both interesting migrations
  (`capture_id` seeding, `wanted_uri.album_id` backfill) have their own tests.
- **`jobs.py:131`** — a literal `raise AssertionError("unreachable")`. It is unreachable.
- **The `get_status` / `start_*` / `busy` one-liners** across every module — thin route-facing
  wrappers that session 4's permanent route tests will execute for free. Filling them now would
  buy the same lines twice.

### Expected to move on its own

`roundtrip.py`'s 59% is the lowest number here and the least alarming: three of its six gaps are
page-facing reads and writes that **session 4** covers by definition. Re-measure it then before
treating it as a session-1 shortfall.

---

## Session 2 — Grouping (measured 2026-08-21, at Verify)

`--cov=canonical --cov=canonical_detect --cov=canonical_autogroup --cov=artists --cov=backfill`,
**after** Verify's gap fill, against 511 passing tests. (As handed over, at 510 tests:
`backfill.py` 96%, total 99% with 12 missed statements.)

| module | statements | miss | branch | partial | cover |
|---|---|---|---|---|---|
| `artists.py` | 82 | 0 | 24 | 0 | **100%** |
| `canonical_autogroup.py` | 62 | 0 | 18 | 0 | **100%** |
| `backfill.py` | 122 | 1 | 26 | 0 | **99%** |
| `canonical_detect.py` | 353 | 3 | 158 | 2 | **99%** |
| `canonical.py` | 223 | 3 | 108 | 2 | **98%** |
| **total** | **842** | **7** | **334** | **4** | **99%** |

**Read the difference from session 1 as a property of the code, not of the session.** Session 1's
modules are job loops that drive Spotify; session 2's are pure computation over SQLite, which the
builders reach directly. The one session-2 module shaped like session 1's — `backfill.py`, the
fourth job — carries this session's only real gap, and it is the same *kind* of gap session 1 has.
Nothing here should be used to rank the two sessions' thoroughness; the mutation results below are
the honest comparison, and both sessions look the same on those.

**Mutation check run at Verify (independent of the session's own 31).** 23 mutations across the
five modules plus the `app.py` call site — the P1-013 `neutral` exclusion, `_clean_explicit_pair`'s
version veto and explicit-flag term, P1-018's pair ordering, the `representative()` NULL-`added_at`
sentinel, `cross_component_pairs`' component filter (and its `app.py` caller reverted to
`mark_reviewed`), `pending_song_ids`' ≥2-member filter, `filter_groups`' `%` wildcard, the duration
tolerance, `_has_keyword`'s whole-token rule, `_order`'s score key, `_cleanup_tier`'s orphan sweep,
`apply_partition` committing and writing to `membership`, `undo`'s snapshot wipe, and four of
`backfill`'s arithmetic and paging rules. **22 killed, 1 survived** — the survivor is the P1-010
fixture recorded as P2-005.

### Gaps found, and filled at Verify

- **`backfill.py:254-255, 272-277`** — the `RateLimited` path, both arms, was never executed. The
  inner per-album `except RateLimited: conn.rollback(); raise` sits *above* a generic
  `except Exception` that logs the album and continues, so the ordering is load-bearing: swap them
  and a quota block silently degrades into a per-album failure while the job keeps spending
  requests against a quota already refusing them — and the page reports `completed`. The outer arm
  is what sets `phase="error"` / `outcome="rate_limited"` / `retry_at`. Session 1 covered exactly
  this shape for `snapshot.py` (`test_an_app_quota_block_aborts_the_whole_run`), so this was an
  inconsistency between the two sessions rather than a house style.

  **Filled** by `test_a_rate_limit_aborts_the_whole_run_rather_than_failing_one_album`. Its
  discriminating assertion is deliberately *not* the outcome string, which the outer arm would set
  either way — it is that the **second album is never attempted**, which is the only observation
  that separates the correct `except` ordering from the swapped one. Both mutations (removing the
  per-album arm; blanking the outer arm's `outcome`/`retry_at`) were confirmed to fail afterwards.

### Deliberately not worth filling

- **`canonical_detect.all_candidate_groups`** (`613-614`) — uncovered because it is dead. P1-009
  condemned it for removal on a full-codebase caller search; a test would only preserve it.
- **`canonical.py:177, 190` and `canonical_detect.py:830`** — `if x is None: continue` guards
  against states their own comments say the callers prevent (`830` says so explicitly: the
  singleton check belongs to `pending_song_ids`). Same class as P2-004's stale-pin branch: cheap,
  defensive, and not worth a fixture that has to fake an impossible state to reach.
- **`canonical.nested_tree`** (`364`) — a one-line `subtree(conn, "song", …)` wrapper; the viewer
  page that calls it is session 4's.
- **`backfill.start`** (`48`) — the `jobs.try_start` one-liner, and after the fill the only missed
  statement left in the module. `jobs.try_start` itself is covered by session 1, and session 4's
  route tests execute this for free.

### The one thing this measurement is worth remembering for

**`artists.py` scored 100% on lines *and* branches, and `artists.py` is where P2-005 lived.** Every
line of `_canonical_of` executed, both branches of its sort exercised, and the suite still could not
tell "highest score" from "lowest id". Coverage was structurally incapable of finding that defect;
one mutation found it immediately. Session 5 should read its own consolidated number in that light
— a high figure here bought exactly one item (the `backfill` gap above), while the mutation pass
bought the only real defect in the session. This is the concrete evidence for §7's "gap-finder, not
a gate", and it is stronger evidence than the argument §7 makes from first principles.

---

## Session 3 — Scoring (measured 2026-08-21, at Verify)

`--cov=scoring`, **after** Verify's gap fill, against 600 passing tests. (As handed over, at 596
tests: 2 missed statements, 2 partial branches, same 99%.)

| module | statements | miss | branch | partial | cover |
|---|---|---|---|---|---|
| `scoring.py` | 339 | 1 | 90 | 1 | **99%** |

One module, so there is no shape to read off the table. What is worth recording is that **coverage
found one of this session's five gaps and mutation found the other four** — the same split session
2 saw, and for the same reason: every one of the four was a *green test that could not fail*, which
by construction executes the code it fails to check.

**Mutation check run at Verify (the session ran none of its own).** 60 mutations across `scoring.py`
and `db.py`'s `track_artist_role` view — `_sat`'s half-value, `_raw`'s per-term weights, `combine`'s
exponent / membership weights / tail floor, `_display` and `_undisplay` both ways, exposure's floor
and its recent-horizon clamp (including inverted), the play-weight `CASE` (both the prototype's
NULLIF form and an uncapped one), `R`'s ×30, buckets from windowed inputs, `_recent_ordinals`'
began-within rule, the baseline's median (as mean, and as a median of output *scores*), shrinkage
uncapped and constant, the §7.1a blend, the subtier blend and `SUBTIER_W`, wholesale replace,
`_observe()`'s ordering, all four backstop deferrals and both `_failed_fingerprint` arms, five
worker behaviours, album padding, the playlist live filter, `artist_group_score`'s AND-merge,
`FEATURED_WEIGHT`, `group_score`'s space, and the role view's VA fallback. **52 killed, 8
survived** — four real (recorded as **P2-007**), four equivalent or invalid.

**Record the equivalent mutants so nobody re-hunts them.** Each looks like a survivor and is not:

- **`album_scores`' `max(pad, 0)`** — deleting the guard changes nothing, because `[0.0] * -1` is
  already `[]`. The test is *not* un-failable: `abs(...)` in place of `max(..., 0)` fails it, which
  was confirmed. A note now sits in the test saying so.
- **`str(vid)` on the `score` insert** — SQLite applies TEXT affinity to the column, so an int is
  stored as `'5'` either way and `scores_for_tier`'s `str(g)` lookup still matches.
- **Row order in `_own_tier_to_version`** — a recording/release group belongs to exactly one
  version, so the dict cannot depend on ordering.
- One mutation of mine was a no-op (a stray comment) and proves nothing.

### Gaps found, and filled at Verify

- **`_weighted_score:834`** — the `if vid not in maps[h]: continue` skip, for an artist credited on
  a version with no materialized `score` row: the state between a grouping change and the next
  recompute. Counting it as 0.0 instead would drag an artist down on evidence that does not exist
  yet, which is §4.6's rule. **Filled** by
  `test_a_version_with_no_materialized_score_drops_out_rather_than_counting_zero`; the mutation
  that appends `0.0` was confirmed to fail afterwards.
- The other four gaps this Verify filled were mutation-found, not coverage-found — see **P2-007**.

### Deliberately not worth filling

- **`scoring.py:118`** — `combine()`'s `if total == 0: return 0.0`. Unreachable at the shipped
  `TAIL_FLOOR = 1.0`: every tail weight is at least `1.0 × uᵢ`, and the empty case returns one line
  earlier. Reaching it needs `TAIL_FLOOR` monkeypatched to 0 *and* an all-zero collection, which
  tests a parameter combination §5.2 says should not be moved. Same class as session 2's defensive
  `if x is None: continue` guards.

### The one thing this measurement is worth remembering for

Session 2's lesson was that `artists.py` scored 100% and still hid P2-005. Session 3 sharpens it:
`scoring.py` reached 99% **while the `score` table's entire `recent` column went unasserted** — 8,950
versions' worth of the second horizon, materialized by covered lines, read by no test. Coverage
cannot see an unmade observation, because the code that produces it runs regardless. Session 5
should read the consolidated number knowing that the two highest-coverage sessions are also the two
where mutation found the most.

