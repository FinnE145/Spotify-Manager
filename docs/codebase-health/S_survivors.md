# S sweep — the untriaged survivors

**141 survivors still owed a verdict**, from the corrected sweep of
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

**Round 1 closed 71 of the original 212** (2026-08-24/26), and their rows are
gone from this file. Three sections went entirely — `history_import.py` (27),
`scrobble.py` (18), `grouping.py` (2) — and 24 of `app.py`'s 50 went with them,
because `app.py`'s survivors are **not one module's work**: they are nine
feature clusters whose fixes land in the test file that owns the *feature*, not
one that owns `app.py`. See `S_sweep.md` §3.5. The partition rule is therefore
**test-file ownership by feature domain** (spec §7.2's rule, applied to the
domain rather than the module); what remains here is still listed by module,
so read it against §3.5's domain table before assigning anyone a batch.

Every verdict from round 1 was re-verified by the master session: 67 of 67
reproduced — 56 kill proofs re-run to `PASS`, 11 claimed equivalents re-run
and still `SURVIVED`.


## `app.py` — 26 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 31 | 15 | `num` | `_LISTING_CAP = 50`<br>→ `_LISTING_CAP = 51` |
| 419 | 59 | `num` | `song_id = canonical.groups_for_track(conn, members[0])["song"]`<br>→ `song_id = canonical.groups_for_track(conn, members[1])["song"]` |
| 426 | 87 | `cmp<` | `if tracks_param is not None and len([t for t in tracks_param.split(",") if t]) < 2:`<br>→ `if tracks_param is not None and len([t for t in tracks_param.split(",") if t]) <= 2:` |
| 426 | 89 | `num` | `if tracks_param is not None and len([t for t in tracks_param.split(",") if t]) < 2:`<br>→ `if tracks_param is not None and len([t for t in tracks_param.split(",") if t]) < 3:` |
| 447 | 62 | `num` | `return jsonify({"items": [item], "pending_count": 0})`<br>→ `return jsonify({"items": [item], "pending_count": 1})` |
| 563 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 615 | 73 | `sql=` | `conn.execute("DELETE FROM pending_tier_review WHERE track_id = ?", (track_id,))`<br>→ `conn.execute("DELETE FROM pending_tier_review WHERE track_id <> ?", (track_id,))` |
| 655 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 772 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 780 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 788 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 807 | 48 | `or` | `playlist_ids = body.get("playlist_ids") or []`<br>→ `playlist_ids = body.get("playlist_ids") and []` |
| 812 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 861 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 882 | 46 | `or` | `for entry in (body.get("aliases") or [])`<br>→ `for entry in (body.get("aliases") and [])` |
| 884 | 36 | `and` | `if not pairs or not all(uri and track_id for uri, track_id in pairs):`<br>→ `if not pairs or not all(uri or track_id for uri, track_id in pairs):` |
| 891 | 30 | `true` | `return jsonify({"ok": True, "saved": saved})`<br>→ `return jsonify({"ok": False, "saved": saved})` |
| 897 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 914 | 58 | `sql=` | `conn.execute("DELETE FROM wanted_uri WHERE source = ?", (source,))`<br>→ `conn.execute("DELETE FROM wanted_uri WHERE source <> ?", (source,))` |
| 916 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 927 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 936 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 940 | 38 | `num` | `_BACKFILL_GENERATION_COUNTS = (2, 7)`<br>→ `_BACKFILL_GENERATION_COUNTS = (2, 8)` |
| 952 | 35 | `true` | `return jsonify({"started": True})`<br>→ `return jsonify({"started": False})` |
| 980 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |
| 990 | 30 | `true` | `return jsonify({"ok": True})`<br>→ `return jsonify({"ok": False})` |

## `canonical_detect.py` — 32 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 80 | 50 | `cmp<` | `if idx != -1 and (best_idx is None or idx < best_idx):`<br>→ `if idx != -1 and (best_idx is None or idx <= best_idx):` |
| 166 | 52 | `sqlAND` | `SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_c`<br>→ `SUM(CASE WHEN m.track_id IS NOT NULL OR m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_co` |
| 166 | 82 | `sqlnum` | `SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_c`<br>→ `SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 2 ELSE 0 END) AS live_c` |
| 166 | 89 | `sqlnum` | `SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_c`<br>→ `SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 1 END) AS live_c` |
| 168 | 8 | `sqlLEFTJOIN` | `LEFT JOIN album a ON a.album_id = t.album_id`<br>→ `JOIN album a ON a.album_id = t.album_id` |
| 169 | 8 | `sqlLEFTJOIN` | `LEFT JOIN track_artists ta ON ta.track_id = t.track_id`<br>→ `JOIN track_artists ta ON ta.track_id = t.track_id` |
| 194 | 24 | `sql=` | `"WHERE tier = 'song' AND representative_track_id IS NOT NULL"`<br>→ `"WHERE tier <> 'song' AND representative_track_id IS NOT NULL"` |
| 194 | 33 | `sqlAND` | `"WHERE tier = 'song' AND representative_track_id IS NOT NULL"`<br>→ `"WHERE tier = 'song' OR representative_track_id IS NOT NULL"` |
| 194 | 61 | `sqlISNOTNULL` | `"WHERE tier = 'song' AND representative_track_id IS NOT NULL"`<br>→ `"WHERE tier = 'song' AND representative_track_id IS NULL"` |
| 225 | 38 | `in` | `"pinned": row["track_id"] in pinned_ids,`<br>→ `"pinned": row["track_id"] not in pinned_ids,` |
| 353 | 55 | `cmp<=` | `and abs(ra["duration_ms"] - rb["duration_ms"]) <= _DURATION_TOLERANCE_MS`<br>→ `and abs(ra["duration_ms"] - rb["duration_ms"]) < _DURATION_TOLERANCE_MS` |
| 370 | 17 | `num` | `self.n = 0`<br>→ `self.n = 1` |
| 373 | 18 | `num` | `self.n += 1`<br>→ `self.n += 2` |
| 441 | 19 | `true` | `return True`<br>→ `return False` |
| 446 | 19 | `true` | `return True`<br>→ `return False` |
| 484 | 23 | `cmp<` | `return (a, b) if a < b else (b, a)`<br>→ `return (a, b) if a <= b else (b, a)` |
| 673 | 22 | `and` | `if expand_song_id and not any(g["song_id"] == expand_song_id for g in shown):`<br>→ `if expand_song_id or not any(g["song_id"] == expand_song_id for g in shown):` |
| 687 | 44 | `sql=` | `"              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "`<br>→ `"              WHERE x.track_id <> t.track_id AND ar.name LIKE ?) "` |
| 687 | 57 | `sqlAND` | `"              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "`<br>→ `"              WHERE x.track_id = t.track_id OR ar.name LIKE ?) "` |
| 694 | 11 | `num` | `)[:100]`<br>→ `)[:101]` |
| 765 | 61 | `and` | `here = [tid for tid in members if tid in established and tid in bucket_set]`<br>→ `here = [tid for tid in members if tid in established or tid in bucket_set]` |
| 778 | 48 | `in` | `decided = all(_pair_key(tid, other) in reviewed_pairs for other in here)`<br>→ `decided = all(_pair_key(tid, other) not in reviewed_pairs for other in here)` |
| 788 | 65 | `in` | `"representative": _row(tracks, rep_id) if rep_id in tracks else None,`<br>→ `"representative": _row(tracks, rep_id) if rep_id not in tracks else None,` |
| 819 | 16 | `cmp<` | `if len(ids) < 2:`<br>→ `if len(ids) <= 2:` |
| 819 | 18 | `num` | `if len(ids) < 2:`<br>→ `if len(ids) < 3:` |
| 826 | 25 | `num` | `conn, tracks[ids[0]]["base"], ids, tracks, _load_reviewed_pairs(conn), song_members`<br>→ `conn, tracks[ids[1]]["base"], ids, tracks, _load_reviewed_pairs(conn), song_members` |
| 844 | 27 | `cmp<` | `if len(components) < 2 or _cross_component_reviewed(reviewed_pairs, components):`<br>→ `if len(components) <= 2 or _cross_component_reviewed(reviewed_pairs, components):` |
| 844 | 29 | `num` | `if len(components) < 2 or _cross_component_reviewed(reviewed_pairs, components):`<br>→ `if len(components) < 3 or _cross_component_reviewed(reviewed_pairs, components):` |
| 906 | 38 | `num` | `base = tracks[sorted(members)[0]]["base"]`<br>→ `base = tracks[sorted(members)[1]]["base"]` |
| 913 | 29 | `false` | `cross_artist=False,`<br>→ `cross_artist=True,` |
| 1039 | 29 | `num` | `base = tracks[ids_sorted[0]]["base"] if ids_sorted else ""`<br>→ `base = tracks[ids_sorted[1]]["base"] if ids_sorted else ""` |
| 1049 | 24 | `false` | `"cross_artist": False,`<br>→ `"cross_artist": True,` |

## `entities.py` — 27 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 56 | 34 | `sql>=` | `SUM(CASE WHEN p.ts >= ? THEN 1 ELSE 0 END) AS month,`<br>→ `SUM(CASE WHEN p.ts > ? THEN 1 ELSE 0 END) AS month,` |
| 67 | 37 | `cmp<` | `if data_through and data_through < month_start:`<br>→ `if data_through and data_through <= month_start:` |
| 132 | 19 | `sqlOR` | `(§0.5). INSERT OR IGNORE, so a uri already queued (by either source)`<br>→ `(§0.5). INSERT AND IGNORE, so a uri already queued (by either source)` |
| 235 | 94 | `sql=` | `"SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id = ?"`<br>→ `"SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id <> ?` |
| 244 | 23 | `or` | `(t["name"] or "").casefold(),`<br>→ `(t["name"] and "").casefold(),` |
| 274 | 72 | `num` | `"tenure": max((end - start + 1 for start, end in runs), default=0),`<br>→ `"tenure": max((end - start + 1 for start, end in runs), default=1),` |
| 296 | 41 | `sql=` | `JOIN snapshot s ON s.playlist_id = m.playlist_id`<br>→ `JOIN snapshot s ON s.playlist_id <> m.playlist_id` |
| 348 | 8 | `sqlLEFTJOIN` | `LEFT JOIN album a ON a.album_id = t.album_id`<br>→ `JOIN album a ON a.album_id = t.album_id` |
| 363 | 47 | `sqlMIN` | `"SELECT SUM(t.duration_ms) AS runtime, MIN(m.added_at) AS first_added, "`<br>→ `"SELECT SUM(t.duration_ms) AS runtime, MAX(m.added_at) AS first_added, "` |
| 364 | 9 | `sqlMAX` | `"MAX(m.added_at) AS last_added FROM membership m JOIN track t ON t.track_id = m.track_id "`<br>→ `"MIN(m.added_at) AS last_added FROM membership m JOIN track t ON t.track_id = m.track_id "` |
| 424 | 51 | `sql=` | `"LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id "`<br>→ `"LEFT JOIN artist_alias aa ON aa.artist_id <> ab.artist_id "` |
| 428 | 18 | `sqlMIN` | `"ORDER BY MIN(ab.position)",`<br>→ `"ORDER BY MAX(ab.position)",` |
| 453 | 26 | `sqlLEFTJOIN` | `"FROM track t LEFT JOIN track_artists ta ON ta.track_id = t.track_id "`<br>→ `"FROM track t JOIN track_artists ta ON ta.track_id = t.track_id "` |
| 453 | 68 | `sql=` | `"FROM track t LEFT JOIN track_artists ta ON ta.track_id = t.track_id "`<br>→ `"FROM track t LEFT JOIN track_artists ta ON ta.track_id <> t.track_id "` |
| 480 | 32 | `and` | `is_owned = bool(tid and tid in owned_set)`<br>→ `is_owned = bool(tid or tid in owned_set)` |
| 480 | 40 | `in` | `is_owned = bool(tid and tid in owned_set)`<br>→ `is_owned = bool(tid and tid not in owned_set)` |
| 491 | 88 | `or` | `"artists": ", ".join(a.get("name", "") for a in item.get("artists") or []),`<br>→ `"artists": ", ".join(a.get("name", "") for a in item.get("artists") and []),` |
| 578 | 39 | `eq` | `(primary_versions if r["role"] == "primary" else featured_versions).add(r["version_id"])`<br>→ `(primary_versions if r["role"] != "primary" else featured_versions).add(r["version_id"])` |
| 595 | 27 | `or` | `(t["name"] or "").casefold(),`<br>→ `(t["name"] and "").casefold(),` |
| 611 | 23 | `or` | `(a["name"] or "").casefold(),`<br>→ `(a["name"] and "").casefold(),` |
| 667 | 22 | `or` | `if not groups or groups["version"] in seen_versions:`<br>→ `if not groups and groups["version"] in seen_versions:` |
| 676 | 53 | `or` | `rep_id = canonical.representative(conn, vid) or seen_versions[vid]`<br>→ `rep_id = canonical.representative(conn, vid) and seen_versions[vid]` |
| 691 | 24 | `sqlLEFTJOIN` | `"FROM artist ar LEFT JOIN artist_alias aa ON aa.artist_id = ar.artist_id "`<br>→ `"FROM artist ar JOIN artist_alias aa ON aa.artist_id = ar.artist_id "` |
| 707 | 23 | `or` | `(a["name"] or "").casefold(),`<br>→ `(a["name"] and "").casefold(),` |
| 709 | 7 | `num` | `)[:50]`<br>→ `)[:51]` |
| 719 | 8 | `neg` | `key=lambda p: -playlist_score_map.get(p["playlist_id"], {}).get("all_time", 0.0),`<br>→ `key=lambda p: +playlist_score_map.get(p["playlist_id"], {}).get("all_time", 0.0),` |
| 720 | 7 | `num` | `)[:50]`<br>→ `)[:51]` |

## `db.py` — 12 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 519 | 42 | `sql=` | `LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id;`<br>→ `LEFT JOIN artist_alias aa ON aa.artist_id <> ab.artist_id;` |
| 525 | 7 | `sqlMIN` | `MIN(rta.position) AS position,`<br>→ `MAX(rta.position) AS position,` |
| 528 | 7 | `sqlMAX` | `MAX(CASE WHEN raa.artist_id IS NOT NULL THEN 1 ELSE 0 END) AS is_album_artist`<br>→ `MIN(CASE WHEN raa.artist_id IS NOT NULL THEN 1 ELSE 0 END) AS is_album_artist` |
| 531 | 0 | `sqlLEFTJOIN` | `LEFT JOIN artist ar ON ar.artist_id = rta.artist_id`<br>→ `JOIN artist ar ON ar.artist_id = rta.artist_id` |
| 573 | 21 | `sqlMAX` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MIN(is_album_artist) = 1` |
| 573 | 42 | `sql=` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MAX(is_album_artist) <> 1` |
| 573 | 44 | `sqlnum` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MAX(is_album_artist) = 2` |
| 577 | 21 | `sqlMAX` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MIN(is_album_artist) = 1` |
| 577 | 42 | `sql=` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MAX(is_album_artist) <> 1` |
| 577 | 44 | `sqlnum` | `CASE WHEN MAX(is_album_artist) = 1`<br>→ `CASE WHEN MAX(is_album_artist) = 2` |
| 592 | 7 | `sqlDISTINCT` | `SELECT DISTINCT g.ordinal, m.track_id, tg.version_id, tg.song_id`<br>→ `SELECT g.ordinal, m.track_id, tg.version_id, tg.song_id` |
| 642 | 24 | `num` | `_BUSY_TIMEOUT_SECONDS = 30`<br>→ `_BUSY_TIMEOUT_SECONDS = 31` |

## `jobs.py` — 10 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 23 | 18 | `false` | `_stop_requested = False`<br>→ `_stop_requested = True` |
| 27 | 13 | `num` | `_LOG_LIMIT = 200`<br>→ `_LOG_LIMIT = 201` |
| 32 | 28 | `num` | `_SHORT_WAIT_LIMIT_SECONDS = 30`<br>→ `_SHORT_WAIT_LIMIT_SECONDS = 31` |
| 73 | 40 | `true` | `threading.Thread(target=run, daemon=True).start()`<br>→ `threading.Thread(target=run, daemon=False).start()` |
| 104 | 18 | `num` | `def drain(timeout=40):`<br>→ `def drain(timeout=41):` |
| 124 | 15 | `max` | `attempts = max(1, int(timeout / _DRAIN_POLL_SECONDS))`<br>→ `attempts = min(1, int(timeout / _DRAIN_POLL_SECONDS))` |
| 124 | 19 | `num` | `attempts = max(1, int(timeout / _DRAIN_POLL_SECONDS))`<br>→ `attempts = max(2, int(timeout / _DRAIN_POLL_SECONDS))` |
| 151 | 25 | `num` | `for attempt in range(2):`<br>→ `for attempt in range(3):` |
| 159 | 27 | `cmp>` | `if retry_after > _SHORT_WAIT_LIMIT_SECONDS or attempt == 1:`<br>→ `if retry_after >= _SHORT_WAIT_LIMIT_SECONDS or attempt == 1:` |
| 222 | 58 | `num` | `self._fields[key] = self._fields.get(key, 0) + delta`<br>→ `self._fields[key] = self._fields.get(key, 1) + delta` |

## `artists.py` — 8 survivors

| line | col | op | before → after |
|---:|---:|---|---|
| 15 | 23 | `cmp<` | `return (a, b) if a < b else (b, a)`<br>→ `return (a, b) if a <= b else (b, a)` |
| 66 | 50 | `sqlIN` | `"DELETE FROM artist_alias WHERE artist_id IN ({}) OR canonical_artist_id IN ({})".format(`<br>→ `"DELETE FROM artist_alias WHERE artist_id NOT IN ({}) OR canonical_artist_id IN ({})".form` |
| 66 | 58 | `sqlOR` | `"DELETE FROM artist_alias WHERE artist_id IN ({}) OR canonical_artist_id IN ({})".format(`<br>→ `"DELETE FROM artist_alias WHERE artist_id IN ({}) AND canonical_artist_id IN ({})".format(` |
| 66 | 81 | `sqlIN` | `"DELETE FROM artist_alias WHERE artist_id IN ({}) OR canonical_artist_id IN ({})".format(`<br>→ `"DELETE FROM artist_alias WHERE artist_id IN ({}) OR canonical_artist_id NOT IN ({})".form` |
| 137 | 56 | `num` | `"track_count": counts_t.get(r["artist_id"], 0),`<br>→ `"track_count": counts_t.get(r["artist_id"], 1),` |
| 138 | 56 | `num` | `"album_count": counts_a.get(r["artist_id"], 0),`<br>→ `"album_count": counts_a.get(r["artist_id"], 1),` |
| 143 | 42 | `num` | `def _sample_tracks(conn, artist_id, limit=4):`<br>→ `def _sample_tracks(conn, artist_id, limit=5):` |
| 209 | 29 | `sqlDESC` | `"ORDER BY decided_at DESC"`<br>→ `"ORDER BY decided_at ASC"` |

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

