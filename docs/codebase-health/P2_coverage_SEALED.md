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
