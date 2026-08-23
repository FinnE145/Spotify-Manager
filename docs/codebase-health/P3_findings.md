# P3 — Findings

Same convention as P1 and P2: every finding gets a `P3-###` id and a ruling from Finn before the
session that found it merges. Instructions: `docs/codebase-health/P3_refactor.md` §7.

**P3 is strictly behaviour-preserving** (§2), so a bug found while moving code is recorded here and
the code moves unchanged. Fixing it in the same diff would destroy the one thing that makes a
byte-exact diff meaningful: that every difference is a defect.

| id | session | subject | ruling |
|---|---|---|---|
| P3-001 | 1 | `SELECT *` in `_board_state` — **and a correction to what this finding first claimed about it** | **leave as is** (2026-08-22) |
| P3-002 | 1 | The golden baseline is blind to JSON **key order**, because `jsonify` sorts | **record and leave** (2026-08-22) |
| P3-003 | 1 (Verify) | `normalize.base_string`'s accent-stripping was unasserted by the whole suite *and* invisible to golden | **assertion fixed now; the class goes to the post-P sweep** (2026-08-22) |

---

## P3-001 — `SELECT *` in `_board_state`, and a correction to this finding's first version

**Found:** session 1, while doing §4.5. **Corrected the same session**, before the merge, by
checking a claim that had already been written down and committed. The correction is the more
useful half and is why this entry keeps both versions.

### What was first claimed, and why it was wrong

`_board_state` did `SELECT * FROM card` and `dict(row)`-ed the result straight into `/api/board`'s
JSON. `SELECT *` returns columns in the table's *physical* order, and `card.note` arrives by
`ALTER TABLE ... ADD COLUMN` (`db.py:660`) rather than from `SCHEMA`, so the physical order genuinely
does differ between databases:

| | physical order of `card` |
|---|---|
| a database that migrated into `note` (**`symr.db`**) | `… image_url, placement, x, y, note` |
| one built fresh from `db.py`'s `SCHEMA` (**every test DB**) | `… image_url, note, placement, x, y` |

Both verified empirically. From that, this finding originally concluded that "two installs of Symr
at the same commit served different bytes on `/api/board`", and that naming the columns in the
migrated order was therefore *the only choice* consistent with §2's behaviour-preserving rule and
§3.3's zero-diff gate.

**That conclusion is false.** Flask's `app.json.sort_keys` defaults to `True` (verified on Flask
3.1.3), so `jsonify` serializes object keys **alphabetically** and the dict's insertion order is
discarded before it reaches the response. The captured baseline shows it plainly — `get_board`'s
snapshot has `board_id, display_name, entity_id, entity_type, id, image_url, note, placement, x, y`,
which is neither of the two orders above. The physical-order difference is real; it is simply
invisible through the API, and always was.

So: either column order would have produced zero golden diffs, and the constraint this finding
claimed to be operating under did not exist.

### What is actually true

- **The change is still correct**, on §4.5's own stated grounds and unchanged by any of this: a
  column added to `card` would silently widen an API payload, and naming the columns is what stops
  that. The key *set* is what matters, and that is exactly what `SELECT *` left open.
- **The order chosen is immaterial** to the response. The migrated order was kept because it is
  what is written, not because it was forced.
- **Nothing was ever broken**, in either version of the story.

### The part worth carrying forward

This finding passed its own check for the wrong reason, which is the failure shape `P2_tests.md` §1
spent six sessions on, arriving here in a findings document rather than in a test. The golden
compare came back clean and was read as confirmation of the reasoning; it was nothing of the kind,
because the mechanism it was taken to confirm cannot reach the bytes it compares. The question that
found it is the same one P2 ends on — *would this have noticed if I were wrong?* — asked of a claim
rather than of an assertion. See P3-002 for the harness limit it exposed.

**Ruled 2026-08-22: leave as is** (the code; the finding's first version is corrected above).

---

## P3-002 — what the golden baseline is blind to

**Found:** session 1, out of P3-001. Not a defect in the harness; a limit worth stating, because a
clean compare is about to be the evidence for two sessions that move ~583 lines, and it is only as
good as what it actually observes.

`golden.compare` diffs `response.data` and nothing else. That is complete for HTML — every page in
the baseline is compared byte for byte, whitespace included — and it has two known gaps, both on
the JSON side:

**1. Object key order, because `jsonify` sorts.** `app.json.sort_keys` is `True`, so every `/api/*`
response comes out alphabetically keyed regardless of the order the view built the dict in. The
baseline therefore observes the key *set* and every value, but cannot observe key order. This is
not a hole to plug: the order is invisible to clients too, for the same reason. It is recorded
because P3-001 mistook a clean compare for confirmation of an order-related claim, and that mistake
is available to anyone reading a green compare in session 2 or 3.

**2. Status code and headers.** `capture()` records each case's status and the capture pass asserts
none is 5xx, but the snapshot on disk is the body alone, so `compare()` cannot check that a route
still returns the *same* status. A view that changed status while returning identical bytes would
pass. In practice this is covered from the other side and does not need fixing here: Symr's error
path renders `error.html`, whose bytes differ from any real page, and `test_routes.py`'s permanent
sweep independently asserts non-5xx on every route in the catalog.

**Not fixable inside P3 anyway**, which is the second reason to write it down rather than act on
it: storing statuses would change what a snapshot *is*, and the only way to get them into the
existing baseline is a re-capture — the one action §3.4 forbids outright, since it promotes any
regression already introduced into the new baseline.

**Ruled 2026-08-22: record and leave.** If a future site-wide refactor wants the status dimension, it
belongs in the capture format from the start, decided before that refactor's baseline is taken.

---

## P3-003 — a mutation that survived 770 tests *and* a clean golden compare

**Found:** session 1's Verify pass, by mutation against the code session 1 had just moved.

Deleting `strip_accents` from `normalize.base_string` — so artist names, album names and title bases
stop being accent-folded — left **all 770 tests passing and the golden compare clean**. Both halves
matter, because between them they are the entire safety net P3 is relying on for the ~583 lines
sessions 2 and 3 move.

It is not hypothetical. `symr.db` carries a merged artist pair **"Jerome Ducros" / "Jérôme Ducros"**,
which `artists.candidate_pairs` could only ever have bucketed together because of that call. Golden
cannot see it for a reason worth knowing: the pair is *already reviewed*, so it renders under
`/dev/artists`' **Merged** table rather than under **Duplicate candidates**, and the merged table
reads stored rows rather than re-deriving the buckets. A golden baseline over a curated library
observes the *outcome* of past curation, not the rule that produced it.

**This is not a P3 regression.** `main` has the identical hole: the assertion that moved
(`test_artists.py:465`) carried no accented string before the move either, and the identity check
retired beside it would not have caught this — it pinned that two *names* referred to one function,
which says nothing about what that function does.

### Fixed here

One accented assertion added to the test that moved. It kills the mutation. The test's own comment
had claimed the pipeline was "NFKD, strip combining marks" since before P3 — that claim is now
checked rather than merely stated, which is the narrow version of `P2_tests.md` §1's question asked
of a comment instead of an assertion.

### Left for the post-P sweep — the reason this is a finding and not just a fix

The *class* is out of P3's scope (§2: behaviour-preserving, and a bug found while moving code is
recorded, not fixed). What the one instance shows is a question nothing has systematically asked:
**which properties of the code P3 moves are unasserted by the suite and simultaneously unobservable
in the golden baseline?** That intersection is where a refactor can quietly change behaviour with
both nets reporting green, and it is exactly where P3-002's two known blind spots also live.

Two things make the intersection larger than it looks, and both are visible above:

- **A golden baseline over a curated database sees settled state, not live rules.** Anything whose
  effect has already been recorded — a merge, a review, a pin, an alias — renders from the stored
  row, so the rule that produced it is not re-run on the page being compared.
- **A moved function keeps its old tests, including their old blind spots.** The move is where the
  question gets asked; the tests come along unexamined unless someone asks it.

Neither is a defect to fix inside P3. Both belong on the post-P list, alongside anything else of
this shape found in sessions 2 and 3.

---

## Not findings — decisions taken in passing, recorded so they are not re-litigated

- **`docs/specs/canonical-fixes.md`'s archived blocks were left verbatim.** §4.4 asked for the two
  doc references to `all_candidate_groups` to be updated. §2.1 there is a dated measurement table
  and §2.2 records the cause as it stood in 2026-08-07; both sit under a P1-009 note that already
  declares them archived and already names `cross_artist_groups` as long gone without editing the
  blocks themselves. That established convention was followed — the prose note carries the
  correction, the dated measurement is not re-derived (`CLAUDE.md`'s rule for the roadmap's
  measurements, applied here for the same reason).
- **`normalize.py` was added to `CLAUDE.md`'s codebase map in session 1's Verify, not deferred to
  session 3.** The session had recorded the opposite, on §4.3's "do that by hand, in session 3, once
  the code has settled". Verify's finish-up requires the map to cover new modules, and the two
  reconcile cleanly: what §4.3 defers is the *restructuring* the nine extracted views will force,
  which genuinely has not settled — a brand-new module is not part of that and is stable now.
  Leaving it out would have merged a wrong map to `main` and left it wrong for two sessions, with
  §4.3's module-list check (still session 3's) not yet written to catch it. The `tests/` entry gained
  `test_golden_pass.py` at the same time and for the same reason.
- **`tests/test_artists.py`'s identity assertion was retired, not moved.** It asserted
  `detect.normalize_name is detect._normalize_base_string` — that artist-name and title-base
  normalization were one function. Both names are gone and there is exactly one function now, so
  the check would compare it against itself. The property is true by construction; the three
  behavioural assertions beside it were retargeted at `normalize.base_string` and kept.
