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
| P3-004 | 2 | Eleven payload keys on the six extracted entity pages were observable **only** by golden — a suite that is deleted at the end of P3 | **fixed now** (2026-08-22) |
| P3-005 | 2 (Verify) | The same class, swept exhaustively rather than sampled: **twelve more** payload keys only golden observed, three route-side guards nothing observed at all, and one dead payload key | **the fifteen assertions fixed now; the dead key goes to the post-P sweep** (2026-08-22) |
| P3-006 | 3 | §4.1's named home for `tenure_page` — `generations.py` — cannot have it: the function needs `scoring`, and `scoring.py` imports `generations` | **goes to `entities.py` instead** (2026-08-22) |
| P3-007 | 3 | The class enumerated a third time, over the three dev pages: **sixteen** of 38 mutations held by golden alone, four by nothing — and two of those four are a dead payload key and a rule the catalog cannot express | **the tests fixed now; the dead key goes to the post-P sweep** (2026-08-22) |
| P3-008 | 3 (Verify) | Mutating the *rules inside* three payload keys rather than the keys themselves: two §11.1 rank-before-cap clauses and `tenure_page`'s tier echo, **none of them observable by the permanent suite** and one of them by nothing at all | **the three assertions fixed now** (2026-08-22) |

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

**Discharged 2026-08-23 → `docs/codebase-health/post_P_sweep.md`.** The class was answered by a
bounded mutation sweep over the four highest-risk modules: 372 mutants, 364 killed, three of the
four modules at 100%, and every survivor in `scoring.py`. Five tests followed. The whole-codebase
version is roadmap step **S**, placed a few steps out so it also measures the feature work in
between. The half of this finding about golden is now moot — no baseline exists to be blind.

---

## P3-004 — the return values only the golden baseline was reading

**Found:** session 2, by mutating the six functions §4.1 had just moved, and asking P3-003's
question systematically rather than opportunistically: *which properties of the code P3 moves are
unasserted by the suite?* P3-003 left that to "the post-P sweep" as a class. It turns out to be
answerable cheaply for the code actually in flight, and the answer was large.

### The measurement

Eleven single-line mutations to `entities.py`, each emptying one key of a payload the template
renders. Every one of them left `venv/bin/python -m pytest` **green**:

| mutation | suite before | golden | suite after |
|---|---|---|---|
| `artist_detail` — generation strip emptied | passes | **passes** | fails |
| `artist_detail` — playlist dedup keeps the last row, not the first | passes | **passes** | fails |
| `album_detail` — `stats` / `playlists` rollups emptied | passes | fails | fails |
| `group_detail` — `stats` / `playlists` rollups emptied | passes | fails | fails |
| `track_detail` — `stats` emptied | passes | fails | fails |
| `playlist_detail` — `version_by_track` emptied | passes | fails | fails |
| `playlist_detail` — `artist_credits` emptied | passes | fails | fails |
| `album_detail` — `track_artist_credits` emptied | passes | fails | fails |
| `album_detail` — album-artist credits emptied | passes | fails | fails |
| `search` — playlist results dropped | passes | fails | fails |
| `track_detail` — canonical group links dropped | **fails** | fails | fails |

Two facts in that table matter more than the count.

**Nine were held up by the golden baseline alone.** That is a suite `P3_refactor.md` §3.4 requires
to be **deleted in session 3**. So on the day P3 finishes, nine mutations that are currently caught
would stop being caught — the refactor would end by *removing* the only net under the code it had
just spent three sessions moving, and nothing would say so.

**Two were caught by nothing at all.** `presence_for_tracks` on the artist page returns `[]` for the
artist the golden catalog happens to render, and `setdefault` versus plain assignment is invisible
whenever the first and last membership row for a playlist render alike. Both are instances of
P3-003's first mechanism, now with a second illustration: **a curated database renders one arm of a
branch, and a baseline over it observes that arm only.**

**This is not a P3 regression.** Every one of these lines is unchanged; only its address changed.
`main` has the identical gaps, because the moved code brought its tests — and its blind spots — with
it (P3-003's second mechanism, exactly).

### Fixed here

Eight new tests in `tests/test_entities.py`, under a section header naming the mutation each was
written against. **Ten of the eleven** now die against `tests/test_entities.py` alone; the
eleventh — `track_detail`'s canonical group links — dies against two route tests in
`tests/test_routes.py` instead, which is why the table above already recorded it as caught
before this session started. Both are permanent, so the load-bearing half of the claim holds:
nothing in the table depends on the suite that is about to be deleted. (The original wording
here said all eleven died against `test_entities.py` alone. Corrected in P3's Verify, which
re-ran the table rather than reading it — P3-005.)

Doing it in session 2 rather than deferring it is the point: these are the *seams the session
built*. §6 asks both of P2's questions "of every new test", and the second one — *is there a return
value or code path here that nothing reads?* — is only answerable while the payload's keys are in
front of you.

### The part worth carrying forward

The mutation that found the first nine took four minutes to write and needed no cleverness: empty
one key of a returned dict, run the suite, see green. What made it *worth* running was noticing that
a green golden compare and a green suite are not two independent nets here — during P3 they overlap
almost completely on this code, and after P3 one of them is gone.

So the generalization for session 3, whose three views have exactly the same shape: **when a
temporary net is doing the catching, the finish line is not "the compare is clean" but "the compare
is redundant."** Check which of the two is actually failing before treating a green pair as
confirmation — which is P3-001's lesson, arrived at from the opposite direction.

---

## P3-005 — the same class again, and what sampling missed

**Found:** session 2's Verify, by re-running P3-004's measurement instead of reading it, and then
doing exhaustively what P3-004 did by hand: mutate **every** key of all seven extracted payloads —
62 in `entities.py` plus `generation_view`'s six — running the permanent suite and the golden
compare *separately* for each, so "green" could be attributed to one net or the other.

P3-004 was right about the mechanism and right to fix it. What it could not know, having sampled
eleven mutations rather than enumerated them, is how much of the class was left.

### The measurement

**Twelve more keys the permanent suite could not see and only golden caught:**

| function | keys |
|---|---|
| `group_detail` | `tier`, `rep`, `pinned`, `ordinals`, `tree` |
| `track_detail` / `playlist_detail` / `album_detail` / `artist_detail` | `score` — all four |
| `artist_detail` | `merged_ids` |
| `generation_view` | `ordinal`, `span` |

**Three route-side guards caught by nothing at all** — not the suite, not golden. Each was verified
by deleting the line and running both:

- `group_page`'s `if not data["track_count"]: abort(404, "Group has no members.")`. This one is
  new structure: session 2 split the rule across a module boundary, `entities.group_detail`
  returns `{"track_count": 0}` and the route turns that into the 404. The return shape was pinned;
  the conversion was not. Without it an empty group is a **500**.
- `album_page`'s `canonical.ensure_track_groups(conn); conn.commit()`.
- `search_page`'s `if q:` — the guard whose comment session 2 *added* ("an empty `/search` writes
  nothing"). It is unobservable for a catalog reason: `routes_catalog` carries `/search?q=a` but
  not the bare path, and the url_map completeness check keys on `(endpoint, method)`, so the
  empty-q branch is swept by nothing. That is P2 session 5's gap in mirror image — there a bare
  path existed and its variants did not; here the variant exists and the bare path does not.

**One key nothing reads at all:** `group_detail["track_scores"]`. No template in the tree
references it. The value is genuinely live *inside* the function, where it ranks `member_tracks`;
only handing it to `entity_group.html` is dead. Pre-existing on `main`, and §2 forbids P3 fixing
it — see the ruling below.

### Fixed here

Fifteen assertions: ten in `tests/test_entities.py`, two in `tests/test_generations.py`, three in
`tests/test_routes.py` under a section naming the seam each covers. **Every one was verified by
re-running its mutation and confirming that exactly the new test fails** — writing the test and
watching it pass proves nothing about this class, which is the whole lesson of P2 and of P3-004.

Four of the twelve were the **`score`** on four of the six entity pages: step H's entire output,
and the number those pages lead with. `group_detail`'s score *was* asserted, which is exactly what
made the gap easy to miss — the tier that had a test was the one anybody would check first.

### Ruling on the dead key

`track_scores` is **recorded, not removed.** §2 is unambiguous — a thing found while moving code is
recorded and the code moves unchanged — and the argument that dropping an unused template kwarg is
"byte-identical anyway" is the argument that would justify any small tidy-up in a refactor whose
single acceptance criterion is that no difference exists to explain. It goes to the post-P sweep
with P3-003's class, as a one-line deletion in `entities.group_detail`'s return.

### The part worth carrying forward

P3-004's own generalization was right and is worth restating as the standard it set: *"when a
temporary net is doing the catching, the finish line is not 'the compare is clean' but 'the compare
is redundant.'"* This finding is what happens when that standard is applied by sampling. Eleven
mutations came to mind, eleven were fixed, and the write-up read as complete — while twice as many
instances of the identical class sat one key over.

So: **when the finding is "a whole category of thing is unobserved", the fix has to enumerate the
category, not sample it.** Here that was cheap and mechanical — walk the return dict with `ast`,
null one key, run two suites, attribute the green — and it is worth doing in session 3 for
`canonical_index`, `snapshot_index` and `dev_generations_tenure` before their snapshots are deleted,
because after deletion the measurement is no longer possible: there will be nothing left to
attribute a catch *to*.

---

## P3-006 — the one row of §4.1's table that names a home the import graph forbids

**Found:** session 3, on the first line of extracting `dev_generations_tenure`.

§4.1's table sends that view to `generations.tenure_page(conn, tier, sort, page)`. It cannot go
there. The page ranks every tenure row by score *before* paginating (`docs/specs/scoring-H.md`
§11.1), so the function needs `scoring.scores_for_tier` / `scoring.song_scores` — and
`scoring.py:32` already imports `generations`, for `generation_spans` at `scoring.py:148`. Adding
`generations → scoring` closes a cycle, in the step whose §8 criterion 3 is "the import graph has
no cycle" and whose §4.2 removed the only one that existed.

A function-local `import scoring` inside `tenure_page` would dodge the import-time failure, and was
not treated as an option: it is the same edge, drawn where a reader of the module header cannot see
it, and `CLAUDE.md`'s "never layer hacky fixes" applies exactly here.

### Ruled 2026-08-22: it goes to `entities.py`

`entities.py` already imports both `generations` and `scoring`, so `entities.tenure_page` costs
**no new edge at all** and needs no indirection. The cost is that a dev page's read path now sits
in a module whose docstring says "entity viewing pages" — paid down with a section comment there,
a `CLAUDE.md` map note in both `entities.py`'s and `generations.py`'s entries (a reader looking for
tenure will look in `generations.py` first, and now finds a pointer), and this finding.

The two alternatives, for the record. **Injecting scoring into `generations.tenure_page`** as a
`score_lookup` callable keeps §4.1's named home, but puts the tier→scoring-function branch in
`app.py`, which is a scoring concern in a routing file, and adds precisely the indirection §4.1.1's
own criterion disfavours. **Leaving the view in `app.py`** and recording it costs nothing against
§8's `conn.execute` target — this view has none — but loses the unit-testability of the
rank-before-paginate rule, which is the extraction's stated point (§5 reason 2).

### The part worth carrying forward

§4.1's table was built on one rule — *the module that already owns that data* — and that rule is
right. What this row shows is that it is not sufficient on its own: **ownership and the import
graph are two constraints, and only one of them was checked for eight of the nine views.**

§4.1.1 is the proof that the check works, and it is the same check. `canonical_index` got a whole
subsection precisely because its natural owner (`canonical.py`) would have needed `canonical →
scoring`, and every candidate there was "checked against the actual import graph" by name. That
scrutiny was spent on the view that visibly had no clean home; the eight that looked obvious got
the ownership rule alone, and one of them was wrong for the identical reason. **The view that looks
like it has an obvious home is the one whose home nobody verifies.**

---

## P3-007 — the class enumerated a third time, and what enumeration finds that test-writing doesn't

**Found:** session 3, doing exactly what P3-005's carry-forward assigned it — "worth doing in
session 3 for `canonical_index`, `snapshot_index` and `dev_generations_tenure` before their
snapshots are deleted, because after deletion the measurement is no longer possible: there will be
nothing left to attribute a catch *to*."

### The measurement

**38 single-line mutations**: every key of all three extracted payloads (24), plus the 14 route-side
wirings the extraction created — the cap decision, the five echoed query args, the two autogroup
status values, the pending-generation prompt, and each parsed argument. For every one, the
permanent suite and the golden compare were run **separately**, so "green" could be attributed to
one net or the other rather than to the pair.

| | caught by the suite | golden only | nothing |
|---|---|---|---|
| `canonical_detect.index_data` (10 keys) | 4 | 5 | 1 |
| `snapshot.index_data` (6 keys) | 3 | 2 | 1 |
| `entities.tenure_page` (8 keys) | 5 | 3 | 0 |
| route-side wiring (14) | 6 | 6 | 2 |
| **total** | **18** | **16** | **4** |

**Sixteen of 38 were held up by the golden baseline alone** — the suite §3.4 deletes at the end of
this session, which is to say at the end of P3.

The ratio is worth recording next to P3-005's. Ten of these three payloads' 24 keys were
golden-only (42%), against 12 of the entity pages' 62 (19%). That is not a coincidence and not a
regression: P2's sessions 4 and 5 were "Read paths & UI" and the route sweep, aimed at the entity
pages and the route layer. **The dev pages were the part of the read surface P2 never pointed at,
and the measurement shows it.**

### Two of the four "nothing" results are not missing tests

This is the part that is new, and it is the argument for enumerating rather than writing tests from
a reading of the code.

- **`snapshot.index_data`'s `liked_playlist_id` is dead.** Nothing in `templates/`, `static/` or
  `app.py` reads it; a repo-wide search returns exactly one hit, the line that produces it.
  Pre-existing on `main` (`app.py:749` before the move), so not a P3 regression. Same shape and the
  same ruling as P3-005's `track_scores`: **recorded, not removed** — §2 is unambiguous, and it goes
  to the post-P sweep as a one-line deletion.
- **`/dev/canonical`'s "a search lifts the cap" is unobservable for a *catalog* reason.** Every
  `?q=` case in `routes_catalog.py` and `test_routes.py` matches far fewer rows than the cap of 50,
  so capping a search and not capping it render identically. Nothing about the fixtures is wrong;
  no case in the catalog can express the rule. That is P3-005's `search_page` `if q:` in mirror
  image once more, and the fix needed a monkeypatched cap rather than another case.

A pass that asked "what should I test here?" would have written a test for each and moved on. The
mutation is what says one of the two has no observable behaviour to test at all, and the other
cannot be observed until a fixture is built that crosses the cap.

### Fixed here

**26 tests.** Eight in a new `tests/test_canonical_page.py`, four in a new
`tests/test_snapshot_page.py`, five in `tests/test_generations.py` (`tenure_page`'s, which live
beside the tenure fixtures they are built from even though the function is in `entities.py`, per
P3-006), six in `tests/test_routes.py` under a section naming the seam each covers, and three in
`tests/test_codebase_map.py` — that last file is §4.3's module-list check rather than part of this
finding.

**Every one was verified by re-running its mutation against the new suite with golden disabled**,
which is the only way to establish the thing that actually matters: 19 of the 20 previously
uncaught mutations now fail the permanent suite. The twentieth is `liked_playlist_id`, and it stays
uncaught because it is dead.

### The part worth carrying forward

P3-004 sampled the class and fixed what it sampled. P3-005 enumerated it and found twice as much
again. This one enumerated it on the code the same session had just written, and the new content is
the distinction that only mutation surfaces: **an unobserved return value is not always a missing
test.** Sometimes it is a key that should not exist, and sometimes it is a rule the test *catalog*
has no case capable of expressing. Both look identical from inside a coverage report and from
inside a green suite.

And the standing one, now discharged rather than restated: this was **the last session in which the
measurement was possible at all**. After the snapshots are deleted there is no second net, so a
green suite can no longer be attributed to anything. That is the argument for doing this before the
deletion rather than after it, and it does not come round again.

---

## P3-008 — the unit of mutation, and the three rules that fell between the keys

**Found:** P3's Verify pass, extending P3-007's enumeration after re-running 22 of its mutations
(all 22 caught, so its central claim holds) and its golden compare (zero diffs).

P3-007 mutated **every key** of the three extracted payloads and every route-side wiring the
extraction created. That is the right unit for "does anything read this value at all", and it is
the wrong unit for "does anything check the value is *right*". A key can be observed by name and
unobserved by rule: something asserts the list is non-empty, or has the expected members, while
nothing asserts the order the spec requires it to be in.

Three such rules survived the permanent suite. Each was then run against the golden baseline
separately, which is what attributes them:

| mutation | suite | golden |
|---|---|---|
| `entities.tenure_page` returns `"tier": "version"` whatever it was passed | survives | **catches** (318B, `get_dev_generations_tenure_song`) |
| `/dev/canonical?search=`'s `sorted(...)` → `list(...)` before `[:100]` | survives | **blind** |
| `/search`'s version listing, same mutation before `[:50]` | survives | **catches** |

The middle one is caught by **nothing**, and the reason is P3-007's own second "nothing" result
repeating exactly: `routes_catalog.py:267` is `/dev/canonical?search=zzz`, which matches almost
nothing, so no ordering inside `search_results` is observable through it. P3-007 identified that
shape for the *cap* and fixed it with a monkeypatched cap; the cap of 100 on this listing is a
literal in the middle of an expression, so the fix here is to assert the **order** of two results
rather than the membership of a hundred.

The other two are the class P3-004, P3-005 and P3-007 have each found once already, at a finer
grain. `tier` is the sharper of them, because it is session 3's own code and it was tested: five
new `tenure_page` tests landed in `test_generations.py`, and **every one of them passes
`tier="version"`**, so none can tell an echoed argument from a constant. And the echo is not
cosmetic — `generations_tenure.html:52` renders `entity_link(tier, r.group_id, …)`, so tier and
group id are one pair: a wrong tier links every row of the song-tier page to a *different* group
at the version tier, flips the tier toggle, and puts `tier=version` in every sort and pager link.
`/search`'s two halves are the same sentence of `scoring-H.md` §11.1 split across two listings,
with the album half asserted since session 2 and the song half not.

### Fixed here

Three tests, each verified by re-running its mutation against the full suite:
`test_the_track_search_ranks_by_score_before_its_hundred_row_cap`,
`test_tenure_page_reports_the_tier_it_was_asked_for_alongside_that_tier_s_ids` (which asserts the
tier *and* the ids together, since either alone is satisfiable by the mutation), and
`test_search_ranks_songs_by_score_before_capping_at_fifty`.

### The part worth carrying forward

The measurement P3-004 → P3-005 → P3-007 refined three times converged on a unit — one mutation
per payload key — and that unit has a floor. **A key-level sweep answers "is this read?", never
"is this right?"**, and the second question is where an ordering rule lives. Every one of P3's
findings after the first two has been an unobserved *something*; this is the first where the thing
unobserved was inside a key the sweep had already ticked off as covered.

Which is also the note to end P3's findings on, since the golden baseline is deleted with this
pass: **the four sweeps between them never stopped finding the same class, they only kept changing
what granularity they looked at it from.** `codebase-health-P.md` §2's question — *would this
notice a wrong answer?* — has to be asked of the assertion, not of the coverage report and not of
the key list.

---

## Not findings — decisions taken in passing, recorded so they are not re-litigated

- **The golden snapshots outlive session 3 by one pass.** §3.4, §5 and §8.8 all assign the deletion
  to session 3; Finn ruled 2026-08-22 that **P3's Verify pass deletes them instead**, and the
  reasoning is what the two previous Verify passes actually did with them. Session 1's re-ran the
  compare independently rather than trusting the session's word for it; session 2's re-ran P3-004's
  measurement rather than reading it, which is the entire reason P3-005 exists. Deleting at the end
  of session 3 would leave P3's own Verify with no way to re-derive either, and §3.4 forbids
  re-capturing — so the one action that cannot be undone would land exactly one pass before the
  pass most likely to need it. Nothing about the *net* is at stake: P3-007 already established that
  19 of the 20 mutations golden was solely catching now fail the permanent suite, so what survives
  for one more pass is the ability to check that claim, not coverage. `tests/golden_snapshots/` is
  gitignored (`.gitignore:20`) and `*.db` separately so (`.gitignore:5`), so nothing can reach a
  commit in the meantime.

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
- **`canonical.ensure_track_groups(conn)` + `conn.commit()` was hoisted above the 404 guard** in
  `track_page`, `album_page`, `artist_page` and `playlist_page`. §4.1 says flatly that the pairing
  stays in the route, and in four of the six views it sat *after* the row lookup that 404s — so
  keeping it in the route and having one extracted call means it now runs before that lookup. The
  visible consequence is that `/album/<unknown>` and friends run one idempotent
  "allocate groups for track-less tracks" SELECT before returning 404; in practice it writes
  nothing, since a library whose tracks all have groups selects zero rows. `search_page` is the
  exception and keeps its `if q:` around the pairing, so an empty `/search` still writes nothing at
  all. Zero golden diffs across all six.
- **`entities.group_detail` signals its two 404s by two return shapes**, not an exception or a
  sentinel: `None` means no group with that id at that tier, and a dict holding `track_count: 0`
  and nothing else means the group exists but is empty. The second check cannot move to the route
  — every line after it indexes `track_ids[0]` — and both descriptions ("No such group." / "Group
  has no members.") had to survive, since the 404 page renders them.
- **`app.py`'s `_GROUP_TIER_COLUMN` was deleted rather than moved.** Its only consumer was
  `playlist_page`'s generation view, which now calls `generations._tier_column` — the whitelist
  that module already had. That narrows the accepted set from four tiers to two, which is
  behaviour-preserving because the route normalizes `?tier=` to `version`/`song` before the lookup
  and always has; the other two were never reachable.
- **`tests/test_artists.py`'s identity assertion was retired, not moved.** It asserted
  `detect.normalize_name is detect._normalize_base_string` — that artist-name and title-base
  normalization were one function. Both names are gone and there is exactly one function now, so
  the check would compare it against itself. The property is true by construction; the three
  behavioural assertions beside it were retargeted at `normalize.base_string` and kept.
