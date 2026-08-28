# S sweep — the untriaged survivors

**33 survivors still owed a verdict**, from the corrected sweep of
2026-08-24 (`S_sweep.md` §2.4). Everything §3.1–§3.4 already ruled on is
excluded, so this file is exactly the remaining work.

**Why this is committed when `mutation-sweep-S.md` §4 says results are not.**
§4 keeps `sweep_results.jsonl` out of git because the committed ledger "is
what makes the work resumable across sessions when the scratchpad is not" — it
assumed that ledger would carry the whole story. At this scale it cannot, and
the results file lives in `/private/tmp`, one cleanup from gone. This *is* that
ledger, restricted to what is still owed.

**`before` is the load-bearing column, not `line`.** Line numbers are relative
to the measured tree; a rebase onto a moved `main` shifts them. The source text
is what relocates a survivor afterwards.

**`col` matters** wherever one line carries several mutants — pass it to
`verify.py --col`, or the tool refuses rather than guessing which you meant.

Regenerate with the snippet in `S_handoff.md` if the sweep is ever re-run.

---

**Rounds 1, 2 and 3 closed 179 of the original 212** (2026-08-24/28), and their
rows are gone from this file. Five sections went entirely — `history_import.py`
(27), `scrobble.py` (18), `jobs.py` (10), `artists.py` (8), `grouping.py` (2) —
and this file's `app.py` section went 50 → 18 → 7 with them, because `app.py`'s
survivors are **not one module's work**: they are nine feature clusters whose fixes land in the test
file that owns the *feature*, not one that owns `app.py`. See `S_sweep.md` §3.5.
The partition rule is therefore **test-file ownership by feature domain** (spec
§7.2's rule, applied to the domain rather than the module); what remains here is
still listed by module, so read it against §3.5's and §3.7's domain tables
before assigning anyone a batch.

**`app.py:940` is a worked example of why the domain, not the module, is the
unit.** The round 3/4 partition first put it with the round-trip lot and
`app.py:952` with the backfill lot — but 940 is the constant `start_backfill_job`
validates against and 952 is that same view's return, twelve lines apart in one
route. They are one job and are now both listed above, owed to whoever takes
backfill.

Every verdict from every round was re-verified by the master session: 67 of 67
in round 1, 79 of 79 in round 2, 29 of 29 in round 3 — kill proofs re-run to
`PASS`, non-fix verdicts re-run and still `SURVIVED`.


## `app.py` — 7 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 772 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 780 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 788 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 807 | 48 | `or` | `playlist_ids = body.get("playlist_ids") or []`<br>→ `playlist_ids = body.get("playlist_ids") and []` |
| 812 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 940 | 38 | `num` | `_BACKFILL_GENERATION_COUNTS = (2, 7)`<br>→ `_BACKFILL_GENERATION_COUNTS = (2, 8)` |
| 952 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |

## `backfill.py` — 7 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 43 | 43 | `and` | `status["stopping"] = status["running"] and jobs.stop_requested()`<br>→ `status["stopping"] = status["running"] or jobs.stop_requested()` |
| 90 | 16 | `sqlDISTINCT` | `"SELECT DISTINCT gp.ordinal, t.album_id FROM generation_presence gp "`<br>→ `"SELECT gp.ordinal, t.album_id FROM generation_presence gp "` |
| 100 | 60 | `false` | `o for o in all_ordinals if not all(settled.get(aid, False) for aid in by_ordinal.get(o, ()`<br>→ `o for o in all_ordinals if not all(settled.get(aid, True) for aid in by_ordinal.get(o, ())` |
| 111 | 21 | `sqlDISTINCT` | `f"SELECT DISTINCT t.album_id FROM generation_presence gp "`<br>→ `f"SELECT t.album_id FROM generation_presence gp "` |
| 113 | 51 | `sqlAND` | `f"WHERE gp.ordinal IN ({placeholders}) AND t.album_id IS NOT NULL",`<br>→ `f"WHERE gp.ordinal IN ({placeholders}) OR t.album_id IS NOT NULL",` |
| 150 | 66 | `false` | `unsettled = sorted(a for a in album_ids if not settled.get(a, False))`<br>→ `unsettled = sorted(a for a in album_ids if not settled.get(a, True))` |
| 235 | 53 | `or` | `f"{_format_ordinal_range(wl['ordinals']) or 'none'}, ~{wl['requests_estimate']} requests."`<br>→ `f"{_format_ordinal_range(wl['ordinals']) and 'none'}, ~{wl['requests_estimate']} requests.` |

## `api_log.py` — 7 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 53 | 36 | `or` | `host=parts.hostname or "",`<br>→ `host=parts.hostname and "",` |
| 56 | 34 | `or` | `query=parts.query or None,`<br>→ `query=parts.query and None,` |
| 58 | 61 | `num` | `duration_ms=int((time.monotonic() - start) * 1000),`<br>→ `duration_ms=int((time.monotonic() - start) * 1001),` |
| 75 | 57 | `num` | `duration_ms=int((time.monotonic() - start) * 1000),`<br>→ `duration_ms=int((time.monotonic() - start) * 1001),` |
| 112 | 61 | `num` | `"last_24h": _count_since(conn, now - timedelta(hours=24)),`<br>→ `"last_24h": _count_since(conn, now - timedelta(hours=25)),` |
| 113 | 59 | `num` | `"last_7d": _count_since(conn, now - timedelta(days=7)),`<br>→ `"last_7d": _count_since(conn, now - timedelta(days=8)),` |
| 119 | 80 | `sql>=` | `"SELECT COUNT(*) FROM api_request WHERE host = 'api.spotify.com' AND ts >= ?",`<br>→ `"SELECT COUNT(*) FROM api_request WHERE host = 'api.spotify.com' AND ts > ?",` |

## `generations.py` — 6 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 70 | 17 | `sqlDISTINCT` | `f"SELECT DISTINCT ordinal, {column} AS group_id FROM generation_presence"`<br>→ `f"SELECT ordinal, {column} AS group_id FROM generation_presence"` |
| 88 | 72 | `cmp>` | `prev_groups = presence.get(spans[i - 1]["ordinal"], set()) if i > 0 else set()`<br>→ `prev_groups = presence.get(spans[i - 1]["ordinal"], set()) if i >= 0 else set()` |
| 89 | 40 | `num` | `next_span = spans[i + 1] if i + 1 < len(spans) else None`<br>→ `next_span = spans[i + 1] if i + 2 < len(spans) else None` |
| 133 | 17 | `sqlDISTINCT` | `f"SELECT DISTINCT ordinal FROM generation_presence WHERE track_id IN ({placeholders})",`<br>→ `f"SELECT ordinal FROM generation_presence WHERE track_id IN ({placeholders})",` |
| 151 | 17 | `sqlDISTINCT` | `f"SELECT DISTINCT ordinal, {column} AS group_id FROM generation_presence"`<br>→ `f"SELECT ordinal, {column} AS group_id FROM generation_presence"` |
| 202 | 25 | `sqlDISTINCT` | `f"SELECT DISTINCT {column} FROM generation_presence WHERE ordinal = ?",`<br>→ `f"SELECT {column} FROM generation_presence WHERE ordinal = ?",` |

## `canonical_autogroup.py` — 6 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 105 | 66 | `false` | `applied = canonical.apply_partition(conn, labels, cleanup=False)`<br>→ `applied = canonical.apply_partition(conn, labels, cleanup=True)` |
| 122 | 23 | `num` | `for start in range(0, len(ids), 500):`<br>→ `for start in range(1, len(ids), 500):` |
| 122 | 36 | `num` | `for start in range(0, len(ids), 500):`<br>→ `for start in range(0, len(ids), 501):` |
| 123 | 36 | `num` | `chunk = ids[start : start + 500]`<br>→ `chunk = ids[start : start + 501]` |
| 146 | 41 | `sqlDESC` | `"FROM auto_group_run ORDER BY id DESC LIMIT 1"`<br>→ `"FROM auto_group_run ORDER BY id ASC LIMIT 1"` |
| 146 | 52 | `sqlnum` | `"FROM auto_group_run ORDER BY id DESC LIMIT 1"`<br>→ `"FROM auto_group_run ORDER BY id DESC LIMIT 2"` |

