# S sweep — the untriaged survivors

**Nothing is outstanding. All 252 survivors of the corrected sweep
(`S_sweep.md` §2.4) have a verdict and a reason.** This file is kept as the
record of what it tracked and how it was used, not as live work.

The verdicts themselves are in `S_sweep.md`: §3.1–§3.4 for the first 40, then
§3.5 (round 1, 71), §3.6 (round 2, 79), §3.7 (round 3, 29) and §3.8 (round 4,
33). §4.1 is the end-of-step re-run that confirms every "gap — fixed" mutant is
now caught and every non-fix verdict still survives.

---

## What this file was for

It was the work list, restricted to what was still owed, and it existed because
`mutation-sweep-S.md` §4 keeps `sweep_results.jsonl` out of git on the
assumption that the committed ledger would carry the whole story. At 252
survivors it could not, and the results file lives in `/private/tmp`, one
cleanup from gone.

Three things about its design were load-bearing, and a future sweep should
copy them:

- **`before` is the load-bearing column, not `line`.** Line numbers are
  relative to the measured tree; a rebase onto a moved `main` shifts them. The
  source text is what relocates a survivor afterwards.
- **`col` matters** wherever one line carries several mutants — `verify.py`
  refuses rather than guessing which you meant. `canonical_autogroup.py:122`
  and `:123` are the worked example: two mutants a line apart that look
  interchangeable and got **opposite verdicts** (`S_sweep.md` §3.8).
- **Rows were deleted only once their verdict was re-verified**, so the file's
  length was always the true remaining count.

## The partition rule it was read against

Survivors are listed **by module** but must be assigned **by feature domain** —
the test file that owns the *feature*, not one that owns the module.
`app.py`'s 50 survivors were nine feature clusters, and its section here went
50 → 18 → 7 → 0 across the rounds while its fixes landed in
`test_grouping_canvas.py`, `test_history_import.py`, `test_roundtrip.py`,
`test_artists.py`, `test_backfill.py` and `test_snapshot_page.py`.

`app.py:940` is the sharpest worked example. The round 3/4 partition first put
it with the round-trip lot and `app.py:952` with the backfill lot — but 940 is
the constant that the view returning 952 validates against, twelve lines apart
in one route. They were reassigned to one agent before either round ran.

## If the sweep is re-run

Rebuild this file by filtering `sweep_results.jsonl` for `status ==
"SURVIVED"`, keeping `file`, `line`, `col`, `op` and `before`. Do **not** carry
the old verdicts across: they were judgements about a tree that has since
gained 172 tests, and `S_sweep.md` §4.1's re-run is the measurement that says
which of them still hold.
