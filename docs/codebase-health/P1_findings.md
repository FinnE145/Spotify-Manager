# P1 — Findings

One file for all of P1 (`docs/codebase-health/P1_spec_audit.md` §4). Stable ids, never reused,
never renumbered — P2's tests and `xfail` markers cite them.

**Status: complete. 21 findings, every spec independently audited at least once.** All 17 specs
read against their code; the 11 original findings each went through two independent Opus review
rounds; every spec that had never produced a finding, plus the core scoring/grouping specs, got
a full from-scratch blind audit on top of that (`org-canvas.md`, `grouping-catch-up-E.md`,
`error-pages.md`, `generations-B.md`, `entity-pages-K.md`, `grouping-fixes-backfill-M.md`,
`canonical-tracks.md`, `async-recompute-N.md`, `detection-artist-model.md`, `scoring-H.md` —
P1-012 through P1-021, the last two on Sonnet after Opus repeatedly hit the account's monthly
spend limit). **Every one of the 17 specs now has real, independent audit coverage — nothing
left un-reviewed.** `scoring-H.md`'s blind audit is worth naming specifically: of the whole
1265-line spec, only 2 narrow `underspecified` gaps turned up, strong confirmation this is the
best-maintained spec in the project. **Rulings complete as of 2026-08-17.** All 21 findings below
carry a classification, a ruling and an action from Finn, per `P1_spec_audit.md` §2. Every ruling
resolved to either **amend the spec** or **fix now** — none were queued for the P2 fix session, so
P2's `xfail(strict=True)` backlog is empty. All 17 specs in `docs/specs/` (plus the touched
`docs/canonical-tracks/` sub-specs) now carry the `**Audited 2026-08-17**` header line; two
sub-specs read during the blind audit but never producing a finding of their own
(`docs/canonical-tracks/detection.md`, `review-ui.md`) deliberately remain unstamped — flagged as
unverified, not assumed clean.

**The blind audit overturned this file's own earlier claim that those 6 specs "checked out
clean."** They didn't — every one of the 6 produced real material, from routine drift up to a
spec (`org-canvas.md`) that turns out to have never been touched at all since implementation (17
differences) and an inverted boolean condition in a headline grouping rule
(`grouping-catch-up-E.md`, §2.2's `neutral`-class handling).

**What the review rounds actually caught, beyond routine documentation drift** (so this isn't
ceremony) — the highlights, grouped by what kind of thing they are:

- **Real behavioral/correctness gaps, not just stale prose:** P1-004 (the undocumented
  failing-playlist epoch discount actively defeats J's resumability guarantee once the only
  unfinished work is one stuck playlist — silently turns a targeted retry into a full
  ~230-request re-pull); P1-007 (a probe-confirmed-dead uri discovered during reconciliation
  never actually transitions state and gets re-probed forever, plus a stop mid-reconciliation
  gets mis-recorded as a completed run); P1-013 (an inverted boolean on `shares_base_version`'s
  `neutral` handling — the single largest divergence found in the whole audit, a real fork in
  prefill behavior, not a doc issue); P1-015 (a generation playlist with zero live memberships
  crashes `tenures()` on a `NULL` timestamp — worth checking against the real DB); P1-016 (four
  small independent code bugs: a misleading "first 50 tracks" note, a mislinked Edit button,
  artist image picking first-not-largest, a failed detail fetch retrying forever instead of once).
- **Stale security/access-control claims** (same shape as each other, each worth fixing on its
  own regardless of the rest): P1-003 (`snapshot.md` still says "no write scopes" — false since
  D); P1-012 (`org-canvas.md` says "no write scopes… no library scopes" — both now false — and
  "no auth/login," also false).
- **A spec directly contradicting what a later step shipped, not just failing to mention it:**
  P1-008 (`canonical-tracks.md`'s own "Out of scope" section explicitly disclaims automatic
  grouping without review, a decision log, and cross-session undo — step E's auto-group feature
  does all three).
- **Two real overclaims in the findings themselves, corrected on review:** P1-009 (a "wasn't
  what shipped" claim that was wrong — it shipped, just at a different call site) and P1-003 (a
  claimed field-name rename that never happened — git history shows the "old" names were never
  implemented at all).
- **Citation errors, scope-broadenings, and other bookkeeping fixes:** P1-001, P1-006, P1-010,
  P1-011 all had wrong line/section numbers or undercounted scope corrected on review; P1-013
  found two modules' docstrings citing contradictory validation numbers for the same rule.

**Index** (file order is discovery order, not id order):

| id | one-line | class | severity |
|---|---|---|---|
| P1-001 | track-metadata-A: exclude-toggle UI names a page K already removed — 2nd-model-reviewed | spec-stale | trivial |
| P1-002 | `_diff_playlist_tracks`'s real algorithm vs `snapshot.md` — 2nd-model-reviewed | spec-stale/underspecified | **high** |
| P1-003 | `snapshot.md` comprehensively superseded + a stale security claim — corrected on review | spec-stale | whole-file |
| P1-004 | `_resolve_force_epoch`'s discount actively defeats J's resumability guarantee | underspecified | **high** |
| P1-005 | End-of-run wording bug ("Resume after in 14 mins") found on review | spec-stale | trivial→bug |
| P1-006 | play-history-C's Status/Concurrency sections predate `jobs.py` — citations fixed on review | spec-stale | low |
| P1-007 | D §4.3 step 6 contradicts §4.5; reconciliation state-tracking bug found on review | spec-stale | moderate |
| P1-008 | canonical-tracks' scope claims directly contradict what step E shipped | spec-stale | moderate |
| P1-009 | canonical-fixes §1 accurate; §2's fix *relocated* not replaced — corrected on review | spec-stale | low |
| P1-010 | detection-artist-model's canonical-artist tiebreak also superseded by H §11.3 | spec-stale | low |
| P1-011 | site-shell's `/snapshot` stale in 4 places, not 1 — broadened on review | spec-stale | trivial |
| P1-012 | org-canvas.md never touched since implementation — 17 differences, blind audit | spec-stale/code-wrong | moderate |
| P1-013 | grouping-catch-up-E: inverted `neutral` boolean + disagreeing docstrings — blind audit | spec-stale/code-wrong | **high** |
| P1-014 | error-pages.md: 6 differences incl. a debugger claim disproven empirically — blind audit | mixed | low |
| P1-015 | generations-B.md: 10 differences incl. a `NULL`-timestamp crash — blind audit | mixed | moderate |
| P1-016 | entity-pages-K.md: 13 differences incl. 4 small real bugs — blind audit | mixed | moderate |
| P1-017 | grouping-fixes-backfill-M.md: 8 differences in M2's exact guarantees — blind audit | underspecified | low |
| P1-018 | canonical-tracks.md: 16 differences, 2 more Out-of-scope contradictions — blind audit | mixed | moderate |
| P1-019 | async-recompute-N.md: 7 differences incl. lost transient self-heal — blind audit | mixed | moderate |
| P1-020 | detection-artist-model.md: 3 differences, all contained — blind audit (Sonnet) | mixed | low |
| P1-021 | scoring-H.md: only 2 differences in the whole 1265-line spec — blind audit (Sonnet) | underspecified | low |
| P1-018 | canonical-tracks.md: 16 differences, 2 more Out-of-scope contradictions — blind audit | mixed | moderate |
| P1-019 | async-recompute-N.md: 7 differences incl. lost transient self-heal — blind audit | mixed | moderate |

---

### P1-001 — `track-metadata-A.md`'s exclude-toggle UI section names a page that no longer exists

- **Spec:** `track-metadata-A.md` §UI — "a control per row in the playlist table on
  `/dev/snapshot`, and one in the header of `/dev/snapshot/playlist/<id>`." Also the "Read
  first" list cites `templates/snapshot_playlist.html`.
- **Code:** `/dev/snapshot/playlist/<id>` and `templates/snapshot_playlist.html` don't exist.
  The per-playlist exclude toggle now lives on the unified `/playlist/<id>` entity page
  (`templates/entity_playlist.html:22`).
- **Difference:** Route and template renamed out from under the spec.
- **Classification:** `spec-stale`
- **Cross-reference:** Already fully documented elsewhere — `entity-pages-K.md` §12.1's removal
  table explicitly records `/dev/snapshot/playlist/<id>` + `snapshot_playlist.html` →
  `/playlist/<playlist_id>`. Not a gap; `track-metadata-A.md` itself was just never touched
  after K landed.
- **Second-model review completed** (Opus). Confirmed exactly. One scope note: only the
  **second half** of the spec bullet is stale — the *per-row* toggle on `/dev/snapshot` itself
  (`templates/snapshot.html:83`) still exists and matches; the "Read first" list's other cites
  (`snapshot.html`, `snapshot.js`) are still valid too. An amendment should replace only the
  `/dev/snapshot/playlist/<id>` / `snapshot_playlist.html` clause, not the whole bullet.
- **Dedicated re-review (Opus, solo pass) — found a second dead route the finding never
  mentioned.** Confirmed everything above (including via `git show`), then read the rest of
  `track-metadata-A.md` fresh and found its §"Query-site changes" section (lines 182-183) cites
  two more now-dead sites, both K-removed, neither previously flagged:
  - **`app.py:306` ("playlist detail rows")** — that line, at the time, was inside
    `def snapshot_playlist()`, the same deleted `/dev/snapshot/playlist/<id>` view P1-001
    already covers. Same root cause, just a second citation of it.
  - **`app.py:323` ("`SELECT * FROM track`")** — that line was inside `def snapshot_track()`, a
    **third dead route** — `/dev/snapshot/track/<id>` — that P1-001 as originally filed never
    mentioned at all. It's now `/track/<id>` / `templates/entity_track.html`, per K's same §12.1
    table.
  So an amendment scoped to just the §UI bullet (as originally proposed) would leave two more
  stale pointers behind, one of them naming a dead route nowhere else in this finding names.
- **Ruling:** Classification confirmed (`spec-stale`).
- **Action:** **Amended 2026-08-17**, widened per the recommendation — `track-metadata-A.md`:
  the exclude-toggle UI bullet, the "Read first" template citation, and both `app.py`
  Query-site-changes line citations now point at their current locations / note their removal
  via `entity-pages-K.md` §12.1. Spec stamped Audited.
- **Test:** None — the toggle's current behaviour is K's territory, exercised by route tests
  written from `entity-pages-K.md` in batch 4.

---

### P1-002 — `_diff_playlist_tracks`'s real algorithm is materially more specific than `snapshot.md` describes

- **Spec:** `snapshot.md` §"Change detection & diffing" — "Which copy departed: best-effort —
  pick the copy whose position/neighbouring tracks no longer line up. Fallback when ambiguous:
  stamp the copy with the latest `added_at`."
- **Code:** `snapshot.py:738-818`. The actual algorithm, in order: (1) an **exact `added_at`
  match** between a current item and a stored row is treated as the same copy regardless of
  where it now sits — not mentioned in the spec at all; (2) items left over after that pass are
  paired off by **position order** (current sorted by position, stored sorted by position) —
  not "whose position/neighbouring tracks no longer line up," which describes a different,
  vaguer comparison than what's implemented; (3) only on a net decrease, the leftover *stored*
  copies are sorted by `added_at` ascending and the **oldest** `n_survive` are kept (paired to
  the remaining current items by position), the rest marked `removed_at = now`.
- **Difference:** The spec's fallback intent ("newest presumed departed") is directionally
  right, but the mechanism — exact-`added_at` identity pass first, then position-order pairing,
  then oldest-survive/newest-departs on the true leftover set — isn't described anywhere. The
  spec's strongest, most citable contradiction: `snapshot.md`'s own §"Why copies get their own
  rows" states position is "**never used as copy identity**" (line 42) — but
  `snapshot.py:780-791`'s position-order pairing directly does that, for whichever copies the
  exact-`added_at` pass didn't already resolve.
- **Classification:** split — the position-pairing mechanism is `spec-stale` (contradicts the
  quoted line above); the exact-`added_at` identity pass is closer to `underspecified` (the spec
  doesn't mention it existing at all, so there's nothing for it to contradict).
- **Second-model review completed** (Opus, independent read of `snapshot.py:738-818` against
  this finding and against `snapshot.md`). **Confirmed accurate as far as it went** — all three
  passes, no off-by-one — but **surfaced real, testable gaps** the first pass missed:
  - **The identity pass can invert the stated fallback.** If stored has copies at `added_at` A
    (old) and B (new), and current now has only one copy at `added_at` B, pass 1 matches B↔B
    directly — so the *newest* copy survives and the *oldest* departs, the **opposite** of
    "newest presumed departed." The fallback logic only ever governs whichever copies the
    identity pass left unmatched; it does not govern the whole set. This is the single most
    important interaction and wasn't stated in the original write-up.
  - **Survivors' `added_at` is rewritten, not just position** (`snapshot.py:789-790,813-814`) —
    in the ambiguous case, the surviving stored row is restamped with the paired current copy's
    `added_at`. That's a history mutation beyond the "position-reassignment" side effect
    originally noted.
  - **Tie-break on equal `added_at`:** the ascending sort at line 804 is stable over an
    already position-sorted list, so among leftover copies sharing one `added_at`, the
    **lowest-position one survives**. Unstated anywhere.
  - **NULL `added_at` handling:** `None` is a valid dict key, so two NULL-`added_at` rows
    exact-match each other in pass 1; and `s["added_at"] or ""` at line 804 sorts a NULL stored
    row as the *oldest* — meaning a NULL-`added_at` row always survives the fallback pass, never
    departs by it.
  - **Asymmetric `None`-guarding on position:** stored positions are guarded (`or 0`, lines 781,
    808) but `current_remaining.sort` (line 780) is not — a current item with `position=None`
    would raise `TypeError`. Not currently reachable (`_parse_track_item`'s `position` is always
    an int), but worth knowing so P2 fixtures don't accidentally construct one.
  - **`stored_rows`'s query has no `ORDER BY`** (`snapshot.py:742-746`) — which of two rows
    sharing an exact `added_at` gets popped by pass 1 vs. left as a leftover is unspecified.
    Tests must assert set-level outcomes, not depend on row insertion order.
  - Positions are Symr's own dense per-pull index (`_fetch_playlist_items` skips locals/episodes
    without incrementing), not Spotify's raw index — worth stating explicitly for anyone
    building fixtures.
- **Ruling:** Classification confirmed (split stands — position-pairing `spec-stale`, identity
  pass `underspecified`). Nothing else in the codebase documents these mechanics, so the fix is
  a full rewrite, not a light patch.
- **Action:** **Amended 2026-08-17** — `snapshot.md`'s "Change detection & diffing" section
  fully rewritten to describe the real three-pass algorithm (identity pass, position-order
  pairing, oldest-survives fallback with the `added_at` rewrite), including the NULL-handling,
  tie-break, and missing-`ORDER BY` caveats. Spec stamped Audited (jointly with P1-003).
- **Test:** This is P2's primary characterization + specification target for the ingest tier.
  Case list, incorporating the second pass's findings: exact-`added_at` match surviving a
  position change; the identity-pass-inverts-fallback interaction above (newest survives via
  exact match while an older unmatched copy departs); ambiguous same-`added_at` duplicates and
  their position tie-break; NULL `added_at` on one or more stored rows; net increase (pure
  inserts); net decrease with the oldest-survives/newest-departs fallback; the `added_at`
  rewrite on ambiguous survivors, not just position; a full-departure (track leaves the
  playlist entirely) and a brand-new track; and a no-op re-pull (identical `added_at` set,
  positions unchanged, zero removals) as the baseline case.

---

### P1-003 — `snapshot.md` is comprehensively superseded; disposition needed

- **Spec:** `snapshot.md` (125 lines, predates the lettered roadmap — never referenced by it).
- **Code:** Cross-checked section by section:

  | `snapshot.md` section | current reality | covered by |
  |---|---|---|
  | Routes (`/snapshot`, `/snapshot/playlist/<id>`, `/snapshot/track/<id>`) | gone; `/dev/snapshot` + K's unified `/playlist/<id>` / `/track/<id>` | **two-hop**: `canonical-tracks.md` Phase 1 (`/snapshot*` → `/dev/snapshot*`), then `entity-pages-K.md` §12.1 (`/dev/snapshot/{track,playlist}/<id>` → entity pages) |
  | `track` table shape (`album_name`, no `isrc`/artists model/etc.) | fully replaced | `track-metadata-A.md` (documented) |
  | Spotify integration field list (`track.album.{id,name}`, no artist ids) | fully replaced by the artist/album model | `track-metadata-A.md` (documented) |
  | Pull & refresh flow (always-full-reread semantics, no exclude, no resume) | replaced by the derived resumable work list | `partial-pulls-J.md` (documented) |
  | Pull progress (module-level status object: `running/phase/playlists_total/playlists_done/...`) | replaced by `jobs.JobStatus`; job-slot architecture traces to D §2, field names were **never actually those** — see below | resolved twice over, see below |
  | Rate-limit handling ("rely on Spotipy's built-in 429/`Retry-After` handling") | **reversed**: fail-fast (`RateLimited`) on a long wait rather than blind-sleeping | `spotify_client.py`, documented in `docs/spotify_constraints.md` and `partial-pulls-J.md` — **missing from the original table entirely** |
  | Core model (append-only membership log, why-copies-get-own-rows) | still conceptually accurate | n/a — not stale, **except** the "Change detection & diffing" sub-section, which is P1-002's territory, not this row's |
  | "Still **no write scopes**" (`snapshot.md:72`) | **false** — `playlist-modify-private` has existed since D | `foreign-roundtrip-D.md` — **missing from the original table, and the most consequential line in the whole file** |
  | Liked Songs "always re-pulled and diffed on every refresh" (`snapshot.md:65`) | now gated on the `excluded` flag, which didn't exist when written | `track-metadata-A.md` — **missing from the original table** |

- **Difference / open question — dedicated re-review (Opus, solo pass), the "unresolved gap"
  framing itself was wrong.** Git-archaeology (`git log -S'playlists_done' --all`) shows
  `playlists_done` has **never existed in any code file, in any commit** — it appears exactly
  once, in `snapshot.md` itself, in the commit that added the spec. `snapshot.py` was *born in
  that same commit* already using `run_total`/`run_done`. **There was no rename to attribute** —
  the spec's field names were never implemented as written, from day one. `foreign-roundtrip-D.md`
  §2's "keeps the same keys" disclaimer is therefore accurate and consistent (D didn't rename
  anything, because nothing D touched ever had the old names), not evidence of a gap. Separately,
  `detection-artist-model.md:171` (step I, which predates D) already documents `run_total`/
  `run_done` explicitly — so even reading it as documentation-owed, a spec names them. The
  `playlists_total` **name collision** still stands as observed (`snapshot.py:65`, a different
  field with the same name, all-time count vs this-run progress) — but `snapshot.py:20-23`
  already carries a code comment drawing that exact distinction, so it's a documented collision,
  not a silent one. **Net: this row is fully resolved, not partially.** The genuinely new item is
  the missing "no write scopes" row above, which is a stale *security* claim, not a mechanical
  one — worth calling out to Finn separately from the rest of this file's routine drift.
  Also confirmed: the "Core model" row's scope-narrowing note (exclude "Change detection &
  diffing," P1-002's territory) still holds and should carry into the amendment.
- **Classification:** `spec-stale` (whole-file)
- **Ruling:** Classification confirmed (`spec-stale`, whole file).
- **Action:** **Amended 2026-08-17** — header marked superseded with a pointers table to
  `track-metadata-A.md` / `partial-pulls-J.md` / `entity-pages-K.md` / `canonical-tracks.md` /
  `foreign-roundtrip-D.md`; the "no write scopes" line and the Liked Songs "always re-pulled"
  line fixed inline as well, not just referenced from the table. Spec stamped Audited (jointly
  with P1-002).
- **Test:** None directly from this spec — its living content is tested via the specs listed in
  the table above.

---

### P1-004 — `_resolve_force_epoch`'s failing-playlist discount is undocumented in `partial-pulls-J.md`

- **Spec:** `partial-pulls-J.md` §2.3–2.4 describes the epoch mechanism (a forced pull resumes
  its current epoch "if it still has unfinished targets," else starts a new one) but never
  mentions excluding *failing* playlists from what counts as "unfinished."
- **Code:** `snapshot.py:301-323`, `_resolve_force_epoch` — a playlist is only counted toward
  "epoch still has unfinished targets" when `stored["last_pull_error"] is None`. The function's
  own docstring explains why: without the discount, one permanently broken (but not yet
  excluded) playlist would keep the epoch alive forever, silently downgrading every future Full
  pull into "retry just that one."
- **Difference:** Real, deliberate behaviour with no spec clause behind it. (`CLAUDE.md`'s
  `snapshot.py` map entry does describe it — "discounts playlists that are failing" — so it was
  recorded somewhere, just not folded back into the spec.)
- **Classification:** `underspecified`
- **Second-model review completed** (Opus). Confirmed exactly, plus two refinements:
  - **The guard has a second arm the original write-up omitted.** It's
    `stored is None or stored["last_pull_error"] is None` — a playlist **never seen before**
    also counts as unfinished (correctly), not only ones carrying a recorded error. Worth
    stating explicitly so a spec amendment and P2's tests cover both arms.
  - **Scope is narrower than "the epoch mechanism":** the discount affects only whether a
    failing playlist keeps `pull_force_epoch` alive — it does **not** remove that playlist from
    the pull's target list (`snapshot.py:384` still includes it in `targets`). A failing
    playlist keeps getting retried every run; it just stops being the reason a new epoch won't
    start. J's nearest text, §2.6 (a failed playlist stays in the work list), is what the
    docstring implicitly leans on and is arguably in tension with the epoch discount rather than
    a clean citation for it — worth deciding during amendment whether §2.3–2.4 or §2.6 is the
    more natural home for the new clause.
- **Dedicated re-review (Opus, solo pass) — a real behavioral consequence, not just a
  documentation gap.** Confirmed everything above precisely, then found the discount's *cost* is
  undocumented and actually **contradicts §2.6's own reasoning**, not merely sits in tension
  with it:
  - **§2.6 argues a persistent failure "costs nothing in practice — it just gets retried."**
    With the discount in place, once the *only* remaining unfinished work in an epoch is one or
    more permanently-failing playlists, `_resolve_force_epoch` concludes the epoch is done and
    mints a **fresh** one. A fresh epoch makes `chosen` match **every** candidate playlist
    (`tracks_pulled_at < new_epoch` is true for all of them) — so the next Full pull click
    force-re-reads the **entire library** (~230 requests), not just the one broken playlist.
    This directly voids §2.4's explicit promise: *"while a forced pull is incomplete, clicking
    Full pull will **not** force-re-read the playlists it already captured in that epoch."* One
    stuck playlist, left un-excluded, silently turns every subsequent Full pull into a full
    library re-read — a real quota-spend risk given how central "don't accidentally burn the
    app-level quota" is to this codebase's design.
  - **Same mechanism bites on a transient failure too**, more subtly: one playlist failing on
    the very last item read of an otherwise-complete forced pull forfeits that epoch, so the
    next click re-reads all ~145 playlists instead of retrying just the one.
  - **`last_pull_error` is cleared only by a successful item read** (`snapshot.py:851`) — neither
    `_upsert_snapshot_playlist` (which does clear `unfollowed_at`) nor `set_excluded` clears it.
    So exclude→un-exclude or unfollow→re-follow carries a stale error forward and stays
    discounted from epoch resolution until its next successful read.
  - Two more minor timing notes: the epoch check reads `stored` rows captured *before* the
    current run's failures are recorded, so the discount always reflects the *previous* run's
    error state, never the one in progress; and `_resolve_force_epoch` commits a new epoch
    before any item read, so an immediately-aborted Full pull still burns an epoch (harmless,
    since everything is then unfinished and the next click resumes normally).
- **Ruling:** Design decision made — completing an epoch while a failing playlist is the only
  unfinished target now requires **explicit exclusion**, not an automatic discount. Verified
  (via git archaeology, `eac3cac`) this isn't a Liked Songs issue — the discount was added to
  fix a genuine "permanently-failing playlist pins the epoch forever" bug from a prior J
  verify-phase session, in the same commit as an unrelated Liked Songs transaction-timing fix.
  But the pre-discount code already had a clean escape the discount bypassed: an *excluded*
  playlist drops out of `candidates` upstream of this check, so exclusion alone already lets the
  epoch complete correctly with no silent re-read — confirmed by reasoning through
  `_sync_playlists_and_get_targets`'s `excluded_ids` filter. Also ruled: `last_pull_error`
  should clear on un-exclude and unfollow→re-follow.
- **Action:** **Fixed now, 2026-08-17.** `snapshot.py`'s `_resolve_force_epoch` discount
  (`and (stored is None or stored["last_pull_error"] is None)`) removed entirely — a failing,
  not-yet-excluded playlist now keeps pinning the epoch, matching §2.6's own "costs nothing in
  practice, retried until excluded by hand" reasoning. `set_excluded(conn, ids, False)` and
  `_upsert_snapshot_playlist`'s re-follow upsert (via a `CASE WHEN unfollowed_at IS NOT NULL`
  guard, so an ordinary still-failing pull doesn't clear it) now clear `last_pull_error`.
  Verified against a temp DB via a standalone script (not the live app/server, per the
  reloader-vs-live-DB risk): un-exclude clears the error, re-follow clears the error, an
  ordinary re-pull of a still-failing-not-unfollowed playlist keeps it. Documented in
  `partial-pulls-J.md` §2.4 (new paragraph) and §2.6 (cross-reference), and in
  `track-metadata-A.md`'s exclude-flag section. `partial-pulls-J.md` stamped Audited (jointly
  with P1-005). Not yet verified live in the browser — no rate-limited/failing-playlist state
  was triggered against the running app this session.
- **Test:** Specification test: a candidate set with one `last_pull_error`-carrying,
  not-excluded playlist that would otherwise satisfy `_is_full_pull_target` must keep
  `pull_force_epoch` alive (not start a fresh one) while every other target is done; excluding
  that playlist must then let the epoch complete on the next forced pull with no other
  previously-captured playlist re-entering the work list. Also cover the never-seen-before
  (`stored is None`) arm, and `last_pull_error` clearing on un-exclude and on a
  previously-unfollowed playlist reappearing (but *not* clearing on an ordinary re-pull of a
  still-followed, still-failing playlist).

---

### P1-008 — `canonical-tracks.md`'s representative-track tiebreak is superseded by `scoring-H.md`

- **Spec:** `canonical-tracks.md` §"Representative track" — "When `representative_track_id` is
  NULL, compute it: **most live memberships** (`membership` rows with `removed_at IS NULL`) →
  **oldest `added_at`** → **lowest `track_id`**."
- **Code:** `canonical.py:251-279`, `representative()`. Actual rule: **highest `score.all_time`**
  (defaulting to 0.0 when unscored) → oldest `added_at` → lowest `track_id`. The function's own
  docstring cites `scoring-H.md §11.3` as the source of the current rule, so this looks like a
  deliberate change that landed with H rather than accidental drift — but `canonical-tracks.md`
  itself still states the pre-H rule.
- **Difference:** "Most live memberships" and "highest score" will usually agree but can
  diverge — a track with fewer live memberships but a stronger score (heavier/more recent plays)
  now wins the representative slot, where the original rule would have picked the
  more-playlists track instead.
- **Classification:** `spec-stale` — **confirmed** by batch 3: `scoring-H.md` §11.3
  ("Behavioural, not display") explicitly documents this exact change — *"`canonical.representative()`
  (`canonical.py:244`) — currently most live memberships → oldest `added_at` → lowest track id.
  Becomes highest score."* So H's own text is accurate; `canonical-tracks.md` is the one that
  was never updated after H landed. Not accidental drift — H made the change and said so, just
  not back in the spec that originally stated the rule.
- **Second-model review completed** (Opus). Confirmed exactly, plus two nuances missed the
  first time:
  - **The election runs on the track tier's own score, not the group's tier** — the join is
    `sc.tier = 'track'` (`canonical.py`'s query inside `representative()`). A version group's
    representative is picked by its member tracks' *track*-tier scores, not by any
    version-tier number.
  - **`oldest_added` no longer filters to live memberships either.** It's `MIN(added_at)` over
    *all* membership rows for the track, `removed_at` or not — the original spec's rule was
    specifically "most **live** memberships," so the drift compounds: not just the primary
    sort key changed, the secondary one quietly stopped respecting `removed_at IS NULL` too.
    Separately, `r["oldest_added"] or "9999"` sorts a track with *no* membership rows **last**,
    not first — worth stating explicitly for test fixtures.
  - Minor: `scoring-H.md`'s own citation (`canonical.py:244`) is one line off the current
    `representative()` definition (`:251`) — cosmetic, not worth a separate finding.
- **Dedicated re-review (Opus, solo pass) — everything above confirmed exactly, plus a real
  behavioral consequence and a direct contradiction elsewhere in `canonical-tracks.md`:**
  - **Callers checked (8 total, all display-only)** — none assumes the old live-membership rule;
    every one ranks explicitly via its own `scoring.scores_for_tier` call and uses
    `representative()` only for a title/cover. `pin_representative` is unaffected (an explicit
    pin short-circuits before the election ever runs).
  - **The representative is now a moving target, undocumented anywhere.** It reads the `score`
    table, which `recompute()` wholesale-replaces — and since N, that fires *asynchronously*
    after every review-queue keypress, pin, and artist merge. Under the old rule the
    representative moved only when memberships changed (a pull). Now group titles/covers on
    `/dev/canonical`, the cross queue, tenure, search, and the entity pages can shift between two
    page loads with no user action in between. Nothing breaks, but it's a real, silent UX
    consequence of the H change that no spec states.
  - **Degraded-score edge case:** if `score` is ever empty (e.g. before the first recompute) or a
    recompute is currently failing, every candidate coalesces to `0.0` and the election silently
    collapses to the *old* rule's tail — oldest `added_at` → lowest `track_id` — with no live-
    membership filter, since that part of the old rule was already dropped.
  - **`/api/canonical/pin` triggers a wasted async recompute** (`app.py:1119`, per
    `async-recompute-N.md` §4.2) — pinning changes no scoring input, and `scoring.py` never calls
    `representative()`, so the dependency only runs one direction. Harmless (coalesced away) but
    worth knowing; not itself a P1 finding since it's code-vs-code, not spec-vs-code.
  - **`canonical-tracks.md` directly contradicts itself elsewhere, via step E.** Its own §"Data
    model"/"Out of scope" text says: no decision log ("undo is in-session only"), "automatic
    grouping without review — every merge is confirmed by hand," and "cross-session undo history"
    is out of scope. `canonical_autogroup.py` (built by `grouping-catch-up-E.md`, landed well
    after this spec) does all three: it auto-groups without any human review pass, keeps an
    `auto_group_run` log, and its undo is a **server-side snapshot that survives the session**.
    This isn't a nuance — it's three flat contradictions of stated scope, worth flagging as
    clearly as P1-003's "no write scopes" line, even though it's really "this spec predates a
    later one that deliberately reversed its own stated non-goals."
  - **Also confirmed stale, not previously flagged:** `track.album_image_url` (§Data model,
    referenced twice) no longer exists — that column moved to `album.image_url` in step A;
    `track.popularity` is also gone, not just always-NULL as the spec says.
- **Ruling:** Confirmed, amend as recommended (bundled with P1-010, P1-018, P1-020 — all the
  same H §11.3 fallout).
- **Action:** **Amended 2026-08-17** — `canonical-tracks.md`'s Representative track section
  rewritten to point at `scoring-H.md` §11.3, with the tier-of-score, live-membership-filter,
  degraded-score-fallback, moving-target-UX, and broader-than-song-tier-consumption nuances all
  folded in. Out-of-scope / Data-model contradictions (auto-grouping without review,
  cross-session undo) struck-through with pointers to `grouping-catch-up-E.md`, not deleted, so
  the original intent stays legible. `scoring-H.md` §11.3's own citation fixed too (see P1-010).
  Spec stamped Audited (jointly with P1-018).
- **Test:** Specification test (once H's §11.3 is confirmed as the source of truth): a group
  with a lower-live-membership-count, higher-score track and a higher-live-membership-count,
  lower-score track should elect the higher-score one as representative. Also cover the
  degraded-score fallback (empty `score` table collapses to the old tail-only rule).

---

### P1-009 — `canonical-fixes.md` §1 still accurate; §2's fix relocated, not replaced — corrected after second-model review

- **Spec:** `canonical-fixes.md` §2 — the fix for `/dev/canonical`'s ~1.2s load time is to build
  `canonical_detect._build_all_groups` **once per request** instead of the three separate calls
  (`candidate_groups`/`cross_artist_groups`/`all_candidate_groups`) the route made at the time,
  expecting ~1.2s → ~0.6s. §2.3 names two implementation options and states a preference: *"one
  new function returning all three, with the route calling that."*
- **Code:** `app.py:781-820`, `canonical_index()` (the `/dev/canonical` route) calls none of the
  three named detection functions directly.
- **Original difference claim — corrected by second-model review (Opus):** the first pass's
  headline — *"§2's proposed fix isn't what shipped"* — **is wrong.** §2.3's preferred option
  shipped **verbatim**: `canonical_detect.canonical_page_groups()` (`canonical_detect.py:637-647`)
  is exactly the "one new function returning all three" fix, docstring restating §2.2's own
  rationale. **What actually changed is the caller, not the mechanism** — the function is now
  invoked from `/api/canonical/cross/listing` (`app.py:942`), an async endpoint the page fetches
  after paint, rather than from the page route synchronously. §2 is superseded in *placement*
  (sync page load → async post-paint fetch), which is a real and separately-worth-recording
  change, but the fix itself is intact and correctly attributed.
- **Two more corrections from the same review:**
  - `canonical_index()` is not wholly detection-free as the first pass implied — it still calls
    `canonical_detect.pending_song_ids(conn)` (`app.py:870`), cheap SQL over
    `pending_tier_review`, not the expensive `_fetch_tracks` path. The "detection absent" claim
    only holds for the three named functions specifically.
  - **Dead code found in passing:** `cross_artist_groups` no longer exists anywhere in the
    codebase; `all_candidate_groups` (`canonical_detect.py:612`) still exists but has **zero
    callers**. Neither is a P1 finding on its own (P1 audits specs against code, not code against
    itself) but worth flagging separately — `all_candidate_groups` looks like a real cleanup
    candidate for P3.
- **Dedicated re-review (Opus, solo pass) — fully confirmed, with git history behind it.**
  Traced the actual commits: `4078acc` ("Build canonical detection once per /dev/canonical
  request") introduced `canonical_page_groups()` inside `canonical_index()`, replacing the
  three separate calls — §2 exactly as specced. `5b76c8c` ("Make /dev/canonical paint in 112ms
  instead of 1.18s") is the *later* commit that lifted the call out to the new async route. So
  the sequence really was: §2 shipped, then got relocated — not two competing designs.
  `cross_artist_groups`'s `def` was removed by `12a8865`; `all_candidate_groups` still has zero
  callers anywhere, confirmed by full-codebase search, only mentioned in
  `docs/canonical-tracks/detection.md:166` and the spec itself.
  **New, worth folding into the amendment:** §2.1's whole measurement table is more thoroughly
  dead than "the fix moved" implies — of its seven named calls, `cross_artist_groups` names a
  deleted function, `all_candidate_groups` names dead code, and `song_groups` was itself split
  into `song_group_rows`/`hydrate_song_groups` by the same `5b76c8c` commit (`song_tree × 142`
  is now capped at 50, not 142). Only `candidate_groups`/`ensure_track_groups`/`tier_counts`
  still name live functions, and none of them run on `/dev/canonical`'s current page-load path.
  The recommended action below should treat §2.1's table as archived history, not just annotate
  the one bullet about the fix's placement.
- **Blind audit completed** (Opus, independent, no visibility into this finding) — reached the
  same conclusion as the dedicated re-review above by a completely independent path (git
  archaeology vs. cold code reading), strong corroboration. §1 reconfirmed exactly, including
  the `0 None`/level-1 edge case. New, small items: the §1.1 line citation for `applyLevel()` is
  stale (now line 153, not 111); **§1.5's larger acceptance test case (the 12-track "willow"
  cross-artist example) is no longer runnable as written** — the cross-artist UI it exercises
  (`0 None` tier buttons on a cross-artist queue) no longer exists; that queue is now
  `/dev/canonical/cross`'s assign-to-group model with no tier buttons at all, per M's rework.
  Also: §1.5's "Inwood Hill Park" case is no longer unreviewed in `symr.db` (already resolved to
  the spec's exact target state) — a dated acceptance-test precondition, not a code issue.
- **Classification:** `spec-stale` (§2, on placement not mechanism — §1 remains accurate,
  verified against `static/js/canonical_review.js:153-178`, which implements §1.2/§1.3's
  coarser-left-alone rule and its agreement-fallback exactly as specified).
- **Ruling:** Confirmed, amend as recommended.
- **Action:** **Amended 2026-08-17** — `canonical-fixes.md` §2 rewritten: §2.1's measurement
  table marked archived history (only 3 of 7 named calls still name live functions, and none run
  on the current page-load path), §2.3 documents the shipped `canonical_page_groups()` fix and
  its later relocation to the async `/api/canonical/cross/listing` endpoint (previously
  undocumented anywhere), §1.1's stale line citation fixed, §1.5's now-unrunnable `willow`
  cross-artist case and already-resolved Inwood Hill Park precondition both noted as stale
  (not re-derived — flagged as no longer usable to re-verify §1 by hand). `all_candidate_groups`
  noted as a dead-code cleanup candidate for P3. Spec stamped Audited.
- **Test:** Specification test: `canonical_page_groups()` should return results consistent with
  building `candidate_groups`/`all_candidate_groups`/etc. individually (if still meaningful) —
  or, more usefully, a route test asserting `/api/canonical/cross/listing` returns what the page
  needs and the synchronous page load no longer pays detection's cost.

---

### P1-010 — `detection-artist-model.md`'s canonical-artist tiebreak is also superseded by `scoring-H.md`

- **Spec:** `detection-artist-model.md` §1 — "`canonical_artist_id` is the id with the **most
  `track_artist` rows** (ties broken by id ascending)."
- **Code:** `artists.py:51-56`, `_canonical_of()` — actual rule is **highest
  `scoring.artist_scores(...)["all_time"]`**, ties broken by id ascending. Docstring cites
  `scoring-H.md §11.3` explicitly, same as P1-008's finding on `canonical.representative()`.
- **Difference:** Same shape as P1-008, different table — this is the artist-identity analogue
  of the group-representative tiebreak, and both cite the same spec section.
- **Classification:** `spec-stale` — **confirmed** by batch 3: `scoring-H.md` §11.3 explicitly
  lists this as its second behavioural change — *"`artists._canonical_choice()` (`artists.py:59`)
  — picks which artist id wins a merge, currently by raw track count. Becomes score-weighted."*
  Both P1-008 and this finding trace to the same spec section, confirming H §11.3 deliberately
  introduced one rule (highest `all_time` score, then id ascending) to replace two earlier
  per-feature tiebreaks. **Minor additional drift:** H's own text names the function
  `artists._canonical_choice()`; the shipped name is `_canonical_of()` (`artists.py:51`) — a
  harmless rename H's text never picked up, worth folding into the same amendment.
- **Second-model review completed** (Opus). Confirmed exactly, including the name drift — and
  it's a **double** citation error in `scoring-H.md`, not just the name: `artists.py:59` (H's
  cited line) is now the `mark_same()` function, nowhere near `_canonical_of()` at line 51.
  Also noted: `_canonical_of`'s defensive `scores.get(a, {}).get("all_time", 0.0)` is
  unreachable in practice — `scoring.artist_scores()` returns one entry per requested id
  unconditionally, so the `.get(a, {})` fallback never fires. Not a bug, just dead defensive
  code, not worth its own finding. `_canonical_of` has exactly one caller (`mark_same`); `unmerge`
  never re-elects a canonical id.
- **Dedicated re-review (Opus, solo pass) — everything above confirmed exactly (both `.get`s
  traced and confirmed unreachable, `combine([])` returns `0.0` so an unscored artist can't
  raise), plus one real algorithmic nuance and three more stale spots in the spec:**
  - **The tiebreak runs on alias-resolved credits, which changes its practical behavior in a way
    neither spec states.** `_canonical_of`'s scores come from `track_artist_role`, built on
    `resolved_track_artist` (`COALESCE(artist_alias.canonical_artist_id, ...)`). An artist id
    **already merged** into a group therefore returns *zero* credit rows of its own and scores
    at the display floor — its credits already count toward its canonical. Practical
    consequence: folding a third id into an existing alias group will almost always keep the
    incumbent canonical (the newcomer starts with real credits, the existing aliases don't), and
    several already-merged members can tie at the floor and fall through to id-ascending. This
    matters for P2: the specification test proposed below must use two **unmerged** ids, or the
    fixture won't actually exercise the score comparison at all.
  - **§2 (line 122) — "Unchanged" list is wrong.** It lists "ordering by playlist impact" as
    unchanged by this spec, but `impact` ordering was retired site-wide by H — `canonical_detect._order()`
    now sorts by `scoring.group_score` and says so in its own docstring. Not this spec's fault
    (H landed after), but stale regardless.
  - **§3 (line 157) — call-site list names two more dead templates.** `snapshot_track.html` and
    `snapshot_playlist.html`, both deleted by K — the same pattern as P1-001, a third instance of
    it in a third spec.
  - **`/dev/artists`'s queue ordering is unstated and its score is invisible.** The queue is now
    ordered by `scoring.artist_group_score` (`artists.py:191-199`), which §1 doesn't mention at
    all; and "the smaller side pointing at the canonical id" (§1's own UI description) now means
    score-smaller, not track-count-smaller, consistent with the tiebreak change — but the score
    driving that sort is computed and never rendered anywhere in `templates/artists.html`, so
    it's an invisible sort key today. Not necessarily a spec problem, but worth Finn knowing.
- **Ruling:** Confirmed, amend alongside P1-008 (bundled with P1-020 too — same overlapping
  "Unchanged ordering" and dead-template items independently found by both).
- **Action:** **Amended 2026-08-17** — `detection-artist-model.md`'s canonical-artist tiebreak
  section rewritten to point at `scoring-H.md` §11.3, with the alias-resolved-credits nuance
  folded in; `/dev/artists`'s queue ordering documented (`artist_group_score` descending,
  previously invisible); §2's "Unchanged" list's stale impact-ordering claim fixed; §3's call-site
  inventory annotated as historically-accurate-but-drifted (2 of 6 named templates dead, read
  counts and search-predicate counts both changed) rather than re-counted precisely, since
  P1-020 flagged it isn't worth maintaining exactly. `scoring-H.md` §11.3's own citation fixed
  (function name `_canonical_choice` → `_canonical_of`, line `artists.py:59` → `:51`). Spec
  stamped Audited (jointly with P1-020).
- **Test:** Specification test: an alias group where the highest-`track_artist`-count id and the
  highest-`all_time`-score id differ should resolve to the score winner — **built from two
  unmerged ids**, per the alias-resolution nuance above, or the fixture won't test what it means
  to.

---

### P1-011 — `site-shell.md`'s `/snapshot` references are stale in four places, not one — broadened after second-model review

- **Spec:** `site-shell.md` §Navbar — "**Right (utility slot):** ... Holds the **Snapshot** link,
  shown as a small icon." Also §Routes (line 41): `/snapshot` listed as "Stub (utility)". Also
  §Stub pages (line 68): `/snapshot` listed among the routes rendering `coming_soon.html`. Also
  §Scope (line 28).
- **Code:** `templates/base.html:21-28` (search form 21-24, gear anchor 25-26) — the utility slot
  holds a search form (`entity-pages-K.md` §10) and a **gear icon linking to `/dev`** (`⚙`,
  `title="Dev"`), not a direct Snapshot link. `/snapshot` itself no longer exists as a stub
  route at all — it's `/dev/snapshot` (`app.py:1124`), and it's a **real, fully-built page**, not
  a `coming_soon.html` placeholder.
- **Difference:** Already cross-referenced for the navbar-icon part — `canonical-tracks.md`
  Phase 1 explicitly changes this: *"the existing snapshot icon in `.nav-utility` becomes a
  **gear**, icon only... linking to `/dev`."* So that piece is the same documented-elsewhere
  pattern as P1-001, not a gap.
- **Second-model review completed** (Opus). Confirmed the navbar piece exactly (with a minor
  line-range correction, later itself corrected again — see below), but found the original
  finding **understated the scope**: `/snapshot` is named as a utility-slot stub in three further
  places in `site-shell.md` beyond the navbar bullet — the routes table, the stub-pages list, and
  the scope section — and the drift is bigger than a rename. The spec describes `/snapshot` as a
  **planned stub** rendering `coming_soon.html`; today it's `/dev/snapshot`, a real, fully-built
  page (pull/refresh/backfill controls, live status). "Moved and renamed" understates it — it
  went from placeholder to shipped feature, and canonical-tracks.md's Phase 1 (the reorg that
  moved it under `/dev`) is the right pointer for all four references, not just the navbar one.
- **Dedicated re-review (Opus, solo pass) — line range corrected back, whole spec re-read fresh.**
  All four `/snapshot` references confirmed exactly at their cited lines (28, 41, 56, 68); the
  line 28 scope exclusion is "the most misleading of the four," since read on its own it implies
  Snapshot is *still unbuilt* rather than already shipped and relocated. **Line-range correction:**
  `.nav-utility` is `base.html:21-28` — the second-model review's `:20-27` was one line short at
  both ends (line 20 is the closing `</div>` of `.nav-primary`, not part of the utility slot).
  Read the rest of `site-shell.md` fresh (auth guard, Home page, stub pages, CSS layout, files
  touched) and found everything else accurate, with one soft note: **the CSS section's `height:
  calc(100% - 45px)` no longer exists anywhere in `style.css`** — the canvas is now a flex column
  (`body.immersive`, `#main{flex:1}`) — but the spec phrases this as *pre-implementation* state
  ("are currently global"), so it reads as historical framing rather than a false current claim.
  Worth a light touch-up, not a full finding on its own. Also: the auth guard is unchanged in
  shape but is now the *middle* of three `before_request` hooks (documented already in
  `partial-pulls-J.md` §4.3 and `scoring-H.md` §9.3 — not a gap).
- **Classification:** `spec-stale` (broadened from "one bullet" — four references, same root
  cause)
- **Ruling:** Confirmed, amend as recommended.
- **Action:** **Amended 2026-08-17.** `site-shell.md`: all four `/snapshot` references (navbar
  bullet, routes table, stub-pages list, scope section) now note the stale claim inline and point
  at `canonical-tracks.md` Phase 1 as where `/snapshot` left this spec's scope entirely — moved to
  `/dev/snapshot` and became a real, fully-built page rather than a `coming_soon.html` stub. CSS
  section's `calc(100% - 45px)` line also touched up (noted as pre-implementation state; the value
  no longer appears anywhere in `style.css`). Spec stamped Audited.
- **Test:** None needed beyond K's/canonical-tracks' own coverage.

---

### P1-005 — End-of-run wording doesn't match `partial-pulls-J.md` §5.1's example text

- **Spec:** `partial-pulls-J.md` §5.1 — "Rate limited: 'Rate limited — 89 of 145 captured, 56
  **still stale**. Resume after 14:20.' Stopped: 'Stopped — 89 of 145 captured, 56 **still
  stale**.'"
- **Code:** `static/js/snapshot.js:168-172` renders `"${run_done} of ${run_total} captured,
  ${run_total - run_done} remaining"` — "remaining," not "still stale." (The DB-derived "stale"
  count is a separate figure, shown persistently in the status header per §5.1's first bullet,
  not in this end-of-run line.)
- **Difference:** Minor wording mismatch between the spec's literal example strings and the
  shipped copy. Arguably the implementation is more correct — "remaining" describes this run's
  leftover work list, while "stale" is a DB-wide count that can differ from it — but the spec's
  exact words don't match what renders.
- **Classification:** `spec-stale`
- **Second-model review completed** (Opus). Confirmed verbatim, plus two unstated nuances worth
  keeping for a characterization test: `captureNote` is null-guarded when `run_total` is
  undefined or 0 (a run that died before its work list existed, e.g. rate-limited during the
  playlist-list fetch), and in that case the message falls back to a **third** wording —
  `"Pull failed: Rate limited by Spotify — retry <date>"` — which §5.1 doesn't describe at all;
  and the rate-limited line has no trailing period after the date span, a small copy detail if
  P2 ever pins this text exactly.
- **Dedicated re-review (Opus, solo pass) — found a real copy bug, not just a wording mismatch.**
  Confirmed everything above, then found: line 178 builds the rate-limited message as
  `"Resume after "` + `makeDateSpan(status.retry_at)`. `retry_at` is a **future** ISO timestamp
  and `format.js` phrases future times as `"in 14 mins"` — so the line actually renders
  **"Resume after in 14 mins."**, doubled-up and grammatically broken. `static/js/roundtrip.js`
  gets the equivalent line right (`"retry "` + the date span, no "after"). Also: the distinction
  between "stale" (Refresh's work list) and "remaining" (this run's own leftover count) is more
  load-bearing than the finding said — they're genuinely different sets on a **Full pull**
  (`_is_full_pull_target` is a strict superset of the refresh-stale rule), so on a full pull of
  an up-to-date library `stale` can read 0 while `remaining` reads 145; printing the spec's
  literal "stale" wording on a full-pull run would visibly contradict the header's live stale
  count polled in the same tick. Separately, on a **stopped** run `run_done` counts a
  just-failed-and-recorded playlist as done, so "remaining" slightly undercounts true leftover
  work when a run stops right after a failure — the rate-limited path doesn't have this quirk.
- **Ruling:** Classification confirmed (`spec-stale`). Amend spec to match the shipped
  "remaining" wording (judged more correct than "stale" — see the amendment for why). Fix the
  JS bug now, separately.
- **Action:** **Amended 2026-08-17** — `partial-pulls-J.md` §5.1 rewritten to the actual
  "remaining" wording, including the no-work-list-yet fallback message and the stale-vs-remaining
  divergence on a full pull. **Fixed now** — `static/js/snapshot.js:178` no longer says "Resume
  after " (which doubled up with `format.js`'s "in 14 mins" future-time phrasing into "Resume
  after in 14 mins."); it now reads "Resume " so the rendered line is "Resume in 14 mins.",
  matching `roundtrip.js`'s equivalent line's style. Verified by inspection and cross-checking
  `roundtrip.js`'s pattern, not live in the browser — reaching a real rate-limited state isn't
  safely triggerable mid-session (see P1-004's same caveat). Spec stamped Audited (jointly with
  P1-004).
- **Test:** Route/JS-adjacent behaviour, low value to pin byte-exact; if desired, a
  characterization test on the string-building logic in isolation, including the
  no-work-list-yet fallback message above and the stale-vs-remaining divergence on a full pull.

---

### P1-006 — `play-history-C.md`'s Status and Concurrency sections describe the pre-`jobs.py` design

- **Spec:** `play-history-C.md` §Status — "Module-global `_status` + `_status_lock` +
  `_set_status` + `get_status`, exactly as in `snapshot.py`," with a literal dict shape. §Concurrency —
  "`history_import.start_*` returns `False` when `snapshot.get_status()["running"]` is true.
  `snapshot._start` returns `False` when `history_import.get_status()["running"]` is true."
- **Code:** `history_import.py:57` uses `jobs.JobStatus`, and `_start` (`history_import.py:102`)
  calls `jobs.try_start("history_import", ...)` — the shared single-lock design, not the
  pairwise check described above.
- **Difference:** This is the exact design `foreign-roundtrip-D.md` §2 replaced, and says so
  explicitly: *"`snapshot._start` and `history_import._start` each check the other module's
  status with no lock held, then take their own lock"* — reproducing almost verbatim what
  `play-history-C.md` specifies — *"two locks cannot enforce one shared invariant"* — followed
  by the `jobs.py` design actually in place today. So this isn't independent drift; it's the
  direct, documented consequence of D §2's port, which `play-history-C.md` itself was never
  updated to reflect.
- **Classification:** `spec-stale`
- **Second-model review completed** (Opus). Confirmed both quotes and both code cites exactly.
  Two corrections to the original write-up's own citations:
  - **Wrong section number.** D's list of required doc annotations is **§9, numbered list item
    3** — D has no "§9.3" (no such subsection exists). Cite it as "D §9 item 3."
  - **Undercounted.** `play-history-C.md` already carries **three** such superseded-by-D
    annotations, not two — at lines 132, 276, and 286 (all pointing at
    `foreign-roundtrip-D.md §8` for the foreign-uri redefinition) — none of which cover the
    Status/Concurrency sections this finding is about, so the "wasn't on that list" framing
    still holds, just with the right count.
  - **Concurrency is only two-thirds stale.** Its third bullet — both API routes return `409
    {"error": "already_running"}` — is still accurate, at `app.py:1388,1391,1401` (corrected
    below — the original citation was one call off). Only the first two bullets (the pairwise
    status-polling description) are stale.
- **Dedicated re-review (Opus, solo pass).** Confirmed every claim above precisely, plus:
  **line-citation fix** — the 409 returns are `app.py:1388,1391,1401`, not `:1387,1390,1402`
  (that last line is the success-path `return jsonify({"started": True})`, not a 409). D §9's
  own list is internally misnumbered (items run 1,2,3,4,6,5), but item "3" is still unambiguous.
  **Four more stale spots in `play-history-C.md`, none previously flagged:**
  - **"Read first" (line 10)** names the same dead machinery a third time: *"Existing code to
    mirror: `snapshot.py` (`_status` / `_status_lock` / `_set_status` / ...)"* — `snapshot.py`
    has no `_status_lock`/`_set_status` either; it's `jobs.JobStatus` (`snapshot.py:17`). Same
    root cause, belongs in the same amendment as Status/Concurrency.
  - **The status dict shape is missing a key.** `jobs.JobStatus.get()` injects `log` on every
    call (`jobs.py:168-177`); the spec's literal dict (lines 229-243) doesn't list it.
  - **The endpoints table omits `active_job`.** `/api/history/status` (`app.py:1404`) returns
    `get_status()` plus coverage plus `status["active_job"]`, which `history_import.js:119`
    uses to grey the controls — not in the spec.
  - **"Nothing is rolled back" (line 221) is literally false**, though the intent it's gesturing
    at is right: `_run_import` does call `conn.rollback()` on failure (`history_import.py:159`).
    What survives is what already *committed* in 5,000-row chunks — the rollback only discards
    the uncommitted tail. Worth a precise rewording, not just a pointer.
  - **The import pipeline steps (1-5) omit `scoring.recompute(conn)`**, called at
    `history_import.py:153` and `:162` — an expected post-H addition, low severity, but absent
    from the spec's numbered steps.
  - Also, the D §8 coverage-table annotation (line 276) misidentifies *which* rows changed —
    it says "the first and last rows," but row 1 (Total plays) is unchanged; the real changes are
    rows 3-6 (the known-to-Symr/in-library split, two new rows) and row 8. Minor, but worth
    fixing while touching this area.
- **Ruling:** Classification confirmed (`spec-stale`). Amend everything in one pass; leave
  Concurrency's third bullet (409 responses) as-is.
- **Action:** **Amended 2026-08-17** — `play-history-C.md`: Status section rewritten to
  `jobs.JobStatus` (including the `log` key), Concurrency's first two bullets rewritten to the
  shared-job-slot design (`jobs.try_start`, per `foreign-roundtrip-D.md` §2), the "Read first"
  pointer fixed, the endpoints table's `/api/history/status` row now mentions `active_job`, and
  the "nothing is rolled back" sentence corrected (only the uncommitted tail is discarded; 5,000
  -row chunks that already committed survive). Also added the missing `scoring.recompute(conn)`
  step to the import pipeline. Spec stamped Audited.
- **Test:** None from this spec directly — the job-slot behavior is `jobs.py`'s own contract,
  more naturally tested against `foreign-roundtrip-D.md` §2's description in batch 1b's own
  scope (see P1-007 area) or as pure-function tests on `jobs.try_start`/`request_stop`.

---

### P1-007 — `foreign-roundtrip-D.md` §4.3 step 6 says "record nothing" on an all-missing batch; the code sometimes does record

- **Spec:** `foreign-roundtrip-D.md` §4.3 step 6 — "**All missing** → systemic (wrong read,
  wrong playlist, scope revoked), not 100 individually dead tracks. Record **nothing** —
  poisoning 100 good uris is the worse error — log it loudly and fail the batch."
- **Code:** `roundtrip.py:540-561`, `_run_batch`. When every requested uri is missing, the code
  branches on whether the batch nonetheless returned a full page of *usable* tracks
  (`stored == len(loaded)`): if so, it calls `_fail_uris(conn, loaded, STATE_NOT_RETURNED)` —
  i.e. it **does** record them, specifically so `_reconcile` (§4.5) has something to work with.
  Only the genuinely empty/short-read case ("loaded N but the read returned M < N usable
  tracks") matches the spec's literal "record nothing."
- **Difference:** §4.3 step 6, read literally, contradicts §4.5's own precondition — the
  reconciliation pass operates on `roundtrip_failed_uri` rows in state `not_returned`, which
  step 6 as written would never create in the all-missing case. The code's actual rule (record
  as `not_returned` when the read looks structurally sane — a full page of *something* came
  back — vs. record nothing when the read itself looks broken) is real and precisely as
  described. This reads like a refinement that shipped with §4.5 (itself flagged "added during
  implementation") but was never folded back into step 6's own text.
- **Classification:** `spec-stale`
- **Second-model review completed** (Opus). Confirmed precisely — line numbers and branch logic
  exact. Two corrections:
  - **Overstated causal claim.** The original write-up said the `stored == len(loaded)` branch
    "makes §4.5 possible at all." Not quite: the **partial**-missing path (some but not all
    requested uris come back, `roundtrip.py:563-564`) *also* writes `not_returned` rows, and is
    the likelier source of most of §4.5's measured 29 unresolved uris in practice. The scoped
    claim — this branch is what keeps the all-missing case from being silently dropped — is
    correct and is what the finding should say instead.
  - **Test description gap.** Both the `stored == loaded` and `stored != loaded` branches
    `return False` (`roundtrip.py:561`), so the circuit breaker counts either as a failed batch.
    The proposed test list read as if only the short-read case fails the batch; both must, and
    the assertion is specifically about what gets *recorded*, not about pass/fail.
  - Noted for P2 fixtures: `loaded` is post-400-narrowing (already excludes probe-confirmed
    dead uris), `stored` counts only entries passing `_usable_track`, and `_fail_uris` is
    `INSERT OR IGNORE` — a uri already marked `dead` keeps that state rather than being
    overwritten to `not_returned`.
- **Dedicated re-review (Opus, solo pass) — confirmed the above precisely, and found three more
  things, one a real bug with a live consequence for correctness, not just documentation:**
  - **(A) The `INSERT OR IGNORE` note has the harmful direction backwards.** The earlier note
    framed it as benign ("an already-`dead` uri keeps its state"). The actual live case runs the
    other way: a uri already stored as `not_returned` that the **reconciliation** pass later
    400s on, and the public-web probe confirms dead, calls `_fail_uris(..., STATE_DEAD)`
    (`roundtrip.py:823`) — but `INSERT OR IGNORE` means that write is silently dropped, and the
    row **stays `not_returned`**. `_reconcile_list` selects exactly `state = 'not_returned'`
    (`:581-590`), so that uri comes back into *every future* reconciliation run and gets
    re-probed forever. This directly contradicts §4.5's own stated intent — *"A probe-confirmed
    `dead` … never is [worth spending requests on]"*. Same mechanism affects `STATE_LOAD_FAILED`
    (`:838`). Bounded (only triggers on a 400 during reconcile specifically) but real, and worth
    its own line in the amendment, separate from the step-6 wording fix.
  - **(B) A stop during reconciliation is mis-recorded.** `_reconcile` returns on
    `jobs.stop_requested()` (`:664-666`) without signalling the stop upward; `_run`'s `outcome`
    stays `"completed"`, so the run still spends the clear-playlist request (§4.4) and writes
    `roundtrip_run.outcome = 'completed'` — but §6.1 explicitly says a stop should "skip the
    clear (§4.4), and end in the stopped-early state." This is **code-wrong** relative to the
    spec, not spec-stale — a real behavior gap, not a documentation one.
  - **(C) §4.1's "Guard (1 request)" heading is stale, and the spec already half-corrects
    itself.** `_guard` makes two calls (`sp.playlist` + `sp.current_user`, `:489-490`), and the
    spec's own measurements table (near the top) already says *"The guard is two reads, not
    one"* — only §4.1's heading and its body text ("It costs one request per run") were never
    updated to match. Same shape as this finding itself: a correction landed in one place and
    not another.
- **Ruling:** Step 6 wording confirmed `spec-stale`, amend. (A) and (B) both ruled: fix now
  rather than queue.
- **Action:** **Amended and fixed now, 2026-08-17.** `foreign-roundtrip-D.md` §4.3 step 6
  rewritten to the real rule (record `not_returned` on a structurally-sane all-missing read,
  record nothing on a genuinely broken one); §4.1's stale "1 request" heading/body corrected to
  "2 requests" (bundled in, trivial, matches the file's own measurements table which already
  said so). **(A) fixed** — `roundtrip.py`'s `_fail_uris` now upserts
  (`INSERT ... ON CONFLICT DO UPDATE`) instead of `INSERT OR IGNORE`, so a `not_returned` uri a
  reconciliation probe confirms `dead` actually transitions instead of getting silently dropped
  and re-probed forever; verified safe in the other direction (a `dead` row can never be
  reached by a `not_returned` write, since `_work_list` excludes any uri already in the table)
  and verified against a temp DB. **(B) fixed** — `_reconcile` now returns whether a stop cut it
  short; `_run` sets `outcome = "stopped"` and skips the clear when it did, instead of silently
  recording a completed run. Verified against a temp DB with `jobs.stop_requested` monkeypatched
  (not the live app). Both fixes and the wording amendment documented in
  `foreign-roundtrip-D.md` §4.5 and §6.1. Spec stamped Audited.
- **Test:** Specification test on `_run_batch`: an all-missing batch where the read-back
  returns a full page of unlabelled substitutes must record every uri as `not_returned` (not
  silently drop them) **and** the batch still counts as failed toward the circuit breaker; an
  all-missing batch with a genuinely short/empty read-back must record nothing and also fail.
  Separately, a partial-missing batch (some but not all uris resolve) should be covered too,
  since it's the more common path into `not_returned`. **Add:** a uri stored as `not_returned`
  that a later reconcile-pass probe confirms dead must actually transition to `dead` (catching
  (A)); a stop requested mid-reconciliation must land in the stopped-early state and skip the
  clear (catching (B)).

---

### P1-013 — `grouping-catch-up-E.md`: blind audit found 11 differences, including an inverted headline rule and two modules disagreeing with each other

- **Spec:** `docs/specs/grouping-catch-up-E.md`. Extensively self-annotated with "corrected
  during implementation" notes already — the blind audit found real drift anyway, mostly from
  *later* specs (M, H) changing things E was never revisited for.
- **Blind audit completed** (Opus, no visibility into other P1 findings). Confirmed the large
  majority of E's content matches exactly (listed in full at the end). 11 real differences,
  three worth calling out individually:
  - **`shares_base_version` excludes `neutral` — the opposite of what §2.2 states, and it
    inverts a headline prefill decision.** Spec: *"`shares_base_version` is true for `base`,
    `recording` and `neutral`, false for `version`… `neutral` simply joins the set that `base`
    and `recording` were already in."* Code (`canonical_detect.py:426`):
    `suffix_class in ("base", "recording")` — `neutral` is explicitly excluded, and the code's
    own comment argues the *opposite* position from the spec ("neutral ones also stand alone,
    and that is the point"). This is the single largest divergence found in the whole batch —
    not a rename or a moved function, a **reversed boolean condition** on the rule §2.2 calls
    out as its main change. `spec-stale` or `code-wrong` depending on which side Finn judges
    correct; either way it needs a ruling, not just an annotation.
  - **Two modules disagree with each other on a factual number, and both disagree with the
    spec's own correction.** `canonical_autogroup.py`'s docstring says the rule "Scored 116/116
    against the 503-pair reviewed baseline" — the *pre-correction* figures E's own §0 amendment
    explicitly retracted (corrected to 491 baseline, 114/114). `canonical_detect._auto_group_pair`'s
    docstring already says 114/114. So the two files that implement one rule cite two different
    validation results, and neither one is a case a reader would think to double-check.
    `code-wrong` (documentation-as-code, but still wrong).
  - **E's cross-queue write behavior was narrowed by M and E was never updated to say so.** E
    §4.4.3 says the cross-queue save "marks reviewed **every pair in the bucket**"; the actual
    write site (`app.py:1025`) calls `canonical.mark_reviewed_pairs(conn,
    canonical_detect.cross_component_pairs(conn, track_ids))` — cross-component pairs only, per
    `grouping-fixes-backfill-M.md` §1's M1 fix (already a P1 finding candidate in its own
    right — if M1 is confirmed on the M audit, this row of E should point at it). Whichever
    spec ends up "owning" this behavior, E's own text is now wrong about what its own feature
    does.
  - **Eight more, real but lower-stakes** (full detail on request; summarized): a whole
    version-tier merge rule (`_clean_explicit_pair`, clean/explicit pairs) exists with no
    counterpart in §2.2's three-item change list — its own docstring says it exists *because of*
    the `neutral`-exclusion bug above, so these two findings are linked; §2.2's instruction to
    make `_same_recording` compare normalized suffixes describes a code change that was never
    made — `_same_recording` has no suffix comparison at any tier; a generic `"… mix"` catch-all
    (`Vocal Up Mix`, `Country Mix`) classifies as `version` under a rule §2.1's table never
    states; the `neutral`-keyword list (`feat.`/`ft.`/`with`) is checked *after* the version/mix
    catch-all in `classify_suffix`'s precedence order, making it a no-op for any suffix also
    matching a version keyword — the five-step precedence order is entirely unspecified; §3.1's
    stated auto-group rule (three conditions) omits the `explicit` guard that's actually
    required — restated correctly in §0's amendment but never fixed in §3.1 itself, so the spec
    states its own headline rule two different, incompatible ways; `auto_group_run`'s schema in
    the spec omits `undone_at`, which is what the Undo button's availability is actually gated
    on; the auto-group rule's title comparison uses two *different* normalizers across its two
    halves (`normalize_title`'s base strips punctuation, `normalize_suffix` spaces it) though
    §1 describes one shared `normalize_suffix`; and §4's "ordering unchanged" claim is stale
    since H retired `impact`-based ordering everywhere, cross-queue included.
- **Classification:** mixed — see above; the `shares_base_version` item is the one needing a
  real ruling (spec-stale vs code-wrong), the auto-group docstring mismatch is `code-wrong`,
  the rest are `spec-stale`/`underspecified`.
- **Ruling:** Code is right — `neutral` stays excluded from `shares_base_version`. Auto-group
  docstrings: fix now, both to 114/114. Remaining ~8 items: bulk-amend to match code.
- **Action:** **Amended and fixed now, 2026-08-17.** `canonical_autogroup.py`'s docstring
  corrected to 114/114 against the 491-pair baseline (matching `canonical_detect._auto_group_pair`,
  and matching `grouping-catch-up-E.md`'s own post-implementation amendment — the 116/116
  figure was the pre-correction prediction, not a competing current truth). `grouping-catch-up-E.md`
  §2.2 rewritten: the `shares_base_version`/`neutral` rule now states and explains the actual
  (ruled-correct) code behavior, `_clean_explicit_pair` documented, the never-implemented
  `_same_recording`-suffix-comparison line corrected. Also amended: §2.1's table (the
  undocumented "mix" catch-all, the credit-keyword precedence-order note), §1 (the
  two-different-normalizers correction), §3.1 (the missing `explicit` guard),
  `auto_group_run`'s schema (`undone_at`), §4 (the stale `impact`→score ordering note), and
  §4.4's cross-queue write-behavior narrowing (M1, cross-referenced to
  `grouping-fixes-backfill-M.md`). Spec stamped Audited.
- **Test:** The `shares_base_version`/`neutral` behavior and the `_clean_explicit_pair` rule
  together are P2's clearest specification-test target in this spec — write the test from
  whichever rule Finn confirms, and let it double as the record of the decision.

---

### P1-014 — `error-pages.md`: blind audit found 6 differences, including a reasoning claim that empirically doesn't hold

- **Spec:** `docs/specs/error-pages.md`. Core design (the `render_error` helper, `/api/*` JSON
  negotiation, the two error handlers, the hardened inline fallback) all confirmed matching
  exactly.
- **Blind audit completed** (Opus, no visibility into other P1 findings). Six differences:
  - **The spec's stated reason for not rendering a traceback doesn't hold, verified
    empirically.** §Diagnostics says: *"when `APP_DEBUG` is on, Flask's built-in interactive
    debugger already intercepts uncaught exceptions with a full traceback before our handler
    runs."* The reviewer actually ran the app with `debug=True` to check: with
    `@app.errorhandler(Exception)` registered, Flask's `handle_user_exception` dispatches
    straight to that handler and `handle_exception`/`log_exception` — the debugger's own
    entry point — is never reached. So the custom 500 page renders with no traceback **whether
    or not debug is on**, not because the debugger already showed one, but because the
    registered handler pre-empts it entirely. The practical behavior (no traceback dump in the
    template) is unaffected, but the spec's stated *reason* for that behavior is wrong —
    `unclear`, since it needs someone to decide whether that matters enough to fix the prose.
  - **"Convert the five inline errors" is now ~40 sites.** The five original `abort()`
    conversions (two dead-route names — see below — plus the three `/callback` OAuth failures)
    all still work correctly, but the app now has roughly 40 `abort()` call sites, most on
    `/api/*` routes the spec's closing parenthetical explicitly says don't apply ("none of these
    are API"). Not wrong, just radically incomplete as a description of current error-handling
    surface area.
  - **Named routes are dead**, same pattern as P1-001/P1-011: `snapshot_playlist`/
    `snapshot_track` (spec's conversion list) and the `/snapshot/track/xyz` example URL no
    longer exist — now `track_page`/`playlist_page` at `/track/<id>`/`/playlist/<id>`. The
    `abort(404, description=...)` conversions themselves survive verbatim on the new routes;
    only the names are stale.
  - **The JSON error slug is unpinned and doesn't match what the spec implies.** §Content
    negotiation writes `{"error": <machine_slug>, ...}` and says "match the existing shape" —
    but the code derives the slug from the HTTP status name (`bad_request`, `not_found`), while
    the hand-written API errors elsewhere use domain-specific slugs (`not_authenticated`,
    `already_running`). Nothing in the spec lets a test assert which one applies where.
    `underspecified`.
  - **The request line drops the query string.** §Diagnostics says the page shows "the request
    method + path that errored" — but the code passes `request.path` only, so the spec's own
    Verify item 3 example (`/callback?error=access_denied`) would render as bare `GET /callback`,
    with the query parameter that actually caused the failure invisible. `underspecified`
    (arguably `code-wrong` against the spec's own worked example).
  - **A logged-out `/api/*` request gets a 302 redirect to the HTML login page, not JSON** — the
    auth guard runs before any error handler and doesn't know about the `/api/*` convention.
    §Auth-guard interaction says "no change needed, but verify" without considering this case.
    Cross-spec (the guard itself belongs to `site-shell.md`), `underspecified`.
- **Classification:** mixed, see above.
- **Ruling:** Traceback reasoning: amend (was simply wrong). ~40 abort() sites / dead route
  names: bulk amend. JSON error slug: **standardize** — ratify domain-specific slugs for expected
  preconditions, unify the response *shape* (a shared helper), but check nothing depends on exact
  slug strings first. Request line drops query string: fix now. Logged-out `/api/*` gets a 302:
  fix now.
- **Action:** **Amended and fixed now, 2026-08-17.** Checked first: no JS or Python anywhere
  compares an error slug by exact string (only `data.detail || data.error`-style truthy checks
  and display), so no existing consumer could break. **Fixed** — `app.py` gained a shared
  `api_error(slug, code, detail=None)` helper next to `render_error`; all 13 hand-written
  `jsonify({"error": ...})` call sites (`not_authenticated`/`already_running`, previously
  inconsistent about the `detail` key) now go through it, so every `/api/*` error response has the
  same `{"error": <slug>, "detail": <string-or-null>}` shape — no slug renamed. `render_error`'s
  HTML path now includes the query string in the rendered request line
  (`request.full_path if request.query_string else request.path`) instead of dropping it.
  `require_login` now returns `api_error("not_authenticated", 401)` for an unauthenticated
  `/api/*` request instead of an HTML redirect (unauthenticated page requests still redirect,
  unchanged). All three verified via a Flask test-client script against a temp DB and an isolated
  spotipy cache path (never the live app or `symr.db`/`.spotipy_cache`) — logged-out `/api/*` →
  JSON 401 with the standardized shape; logged-out page → unchanged 302; a query-string request
  (`/callback?error=access_denied`) → the string now appears in the rendered error page.
  **Amended** — the traceback-reasoning paragraph rewritten to the empirically-verified real
  reason (the registered `Exception` handler pre-empts Flask's debugger entirely, regardless of
  `APP_DEBUG` — not "the debugger already showed one first"); dead route names
  (`snapshot_playlist`/`snapshot_track`) and the Verify section's stale example both corrected to
  point at their current locations; "the five" corrected to the measured current count (35
  `abort()` sites) with a note that most are `/api/*` and out of this spec's HTML-page scope.
  Spec stamped Audited.
- **Test:** Specification test: every `/api/*` error response (both the generic `abort()`/
  exception path and the hand-written precondition checks) has exactly `error` and `detail` keys
  in its JSON body. Also: an unauthenticated `/api/*` request gets JSON 401, not a redirect; a
  request with a query string that errors shows the full query string in the HTML error page's
  request line.

---

### P1-015 — `generations-B.md`: blind audit found 10 differences, all low-stakes but real

- **Spec:** `docs/specs/generations-B.md`. The blind audit confirmed the overwhelming majority
  matches exactly — schema, tenure semantics, `runs()`, the confirm/decline flow, the seed
  script, the strip rendering — all verbatim or behaviorally identical. 10 differences, none
  individually severe, several worth test coverage:
  - **The stub route named in the routes table doesn't exist** — `GET
    /dev/generations/<int:ordinal>` (`dev_generation`) is gone, absorbed into the playlist page's
    `?generation=1` view exactly as the spec's own adjacent prose predicts. Same pattern as
    P1-001/P1-011/P1-014.
  - **"No new JS file" is no longer true** — `static/js/generation_confirm.js` exists (added by
    `async-recompute-N.md` §7.2 for click feedback; the underlying form-POST mechanism is
    unchanged).
  - **The tenure table gained a Score column and a fourth sort mode** the spec doesn't mention —
    `scoring.song_scores()`/`scores_for_tier()` computed over *every* row before sorting, which
    is real whole-library work the spec's own performance-note section didn't budget for
    (expected addition from H, never folded back).
  - **The module's public surface is three functions larger** than spec — `generation_spans()`,
    `runs()`, `presence_for_tracks()` all now exist and are consumed by K's entity pages, none
    mentioned in the spec's module description.
  - **"All of this lives under `/dev`" is no longer true** — tenure numbers and the 36-cell
    strip now render on the public entity pages and the playlist generation view, per K.
  - **Multiple simultaneous pending generations are unspecified** — `pending_new_generation()`
    collects every candidate but surfaces only the lowest-major one at a time; a real,
    undocumented policy.
  - **Ties for longest tenure run are unspecified** — the code picks the **earliest** max-length
    run when there's a tie (documented in its own docstring as a known case), so the reported
    days/first/last silently favor the oldest tie over the most recent. Worth stating explicitly.
  - **Confirm-a-new-generation uses `INSERT OR IGNORE`**, so a conflicting ordinal or playlist id
    is silently swallowed and the user is redirected as if it succeeded — no spec clause covers
    the failure mode.
  - **The span-tiling assumption has undefined behavior when it breaks** — `generation_spans()`
    sets `ended_at` from the next row *by ordinal order*, not literal ordinal+1, so a gap in
    ordinals desyncs `runs()`'s tiling assumption from the span arithmetic; separately, a
    generation playlist with zero live memberships produces `started_at = NULL`, which crashes
    `tenures()` with a `TypeError` on `datetime.fromisoformat(None)`. This is the one item with
    real correctness stakes — worth a specification test regardless of what else happens here.
  - **Minor:** the spec's own `generations(conn)` signature omits the `tier` parameter its
    adjacent prose says exists; code has `generations(conn, tier="version")`.
- **Classification:** mostly `spec-stale`, three `underspecified` (ties, multiple-pending,
  `INSERT OR IGNORE` failure mode). The claimed `code-wrong` crash **did not survive verification**
  — see below.
- **Ruling:** **The claimed `NULL started_at` crash doesn't reproduce.** Checked the live DB first
  (zero generations currently have zero live memberships — not a live landmine), then traced the
  actual guarantee and verified empirically against a temp DB: `generation_presence` and
  `generation_spans()`'s `started_at` subquery share the identical `removed_at IS NULL` filter on
  the same playlist, so any ordinal a group's `tenures()` run could ever reference is guaranteed a
  non-null `started_at`; the other place `None` could reach `fromisoformat` (`ended_at`) is already
  guarded by `or now`. The blind audit's claim was wrong as stated. **A real, narrower quirk turned
  up in the process instead**: a mid-sequence generation with zero live members desyncs the
  *preceding* populated generation's `ended_at` (falls back to `now`/"still open" via the same `or
  now` guard, even though a later generation already superseded it) — would inflate that
  generation's `days` for any group whose longest run ends there. Assessed as low-risk, fully
  contained to `generation_spans()`, and provably a no-op on all current data (the scenario has zero
  real occurrences) — **fixed now** rather than just documented, per that assessment. Everything
  else: document/bulk-amend as recommended.
- **Action:** **Fixed and amended, 2026-08-17.** `generations.py`'s `generation_spans()` now scans
  forward past any intervening NULL-`started_at` (empty) generation to find the real next
  `started_at` for `ended_at`, instead of taking `spans[i+1]` unconditionally. Verified against a
  temp DB: the new edge case (gen 1 populated → gen 2 empty → gen 3 populated) now correctly gives
  gen 1 an `ended_at` of gen 3's `started_at` instead of `None`/`now`, and a regression check
  confirmed the ordinary no-empty-generations case is byte-identical to before. `generations-B.md`
  amended throughout: the corrected (non-)crash story and the real quirk documented in §Tenure in
  days; tie-break-picks-earliest documented (already the code's own deliberate choice); multiple-
  pending-generations policy ratified as-is; `INSERT OR IGNORE`'s silent-swallow documented as a
  known, low-likelihood limitation; dead stub route struck in the routes table; `generation_confirm.js`'s
  existence noted (mechanism — plain form POST — unchanged); the tenure table's new Score
  column/sort documented; "all under `/dev`" corrected (K's entity pages render this data too);
  `generations()`'s missing `tier` parameter added to its signature; the three-function-larger
  public surface (`generation_spans`, `runs`, `presence_for_tracks`) documented. Spec stamped
  Audited.
- **Test:** Specification test: a mid-sequence generation with zero live memberships must not
  desync the *preceding* generation's `ended_at` from the next real generation's `started_at` (the
  actual bug fixed here — replaces the originally-proposed "must not crash" test, which would have
  asserted a scenario that was never reachable). Also worth covering: the tie-break-picks-earliest-
  run behavior, and `generations(conn, tier=...)`'s tier parameter.

---

### P1-016 — `entity-pages-K.md`: blind audit found 13 differences, mostly minor, a few real small bugs

- **Spec:** `docs/specs/entity-pages-K.md`, the largest spec in the batch. Blind audit confirmed
  the great majority matches exactly — every route, all nine endpoints, the four-tier group page
  decorator pattern, the track page's field list (including deliberately withholding
  `linked_from`), `play_stats` semantics, the generation view, K's own §12 deletion list, schema
  columns, CSS/JS/navbar. 13 differences, all individually low-severity; several are genuine
  small bugs worth a queued fix rather than just a doc update:
  - **"No ordering… nothing is ranked" (§7/§10/§16) is comprehensively false** — every list on
    every entity page now sorts by materialized score descending, and every header renders
    `score_display`. Fully expected (H's job, exactly as K itself said it would be), but the
    spec's negative claims should flip to describe H's actual ordering rule now that it exists.
  - **`wanted_uri` gained a fourth column** (`album_id`) and a second live `source` value
    (`'backfill'`) — both from M, undocumented in K.
  - **Fetching and queuing were split, and queuing now runs on every album-page view, not just
    first view** — M §4.4's change, K's §6 still describes them as one combined step.
  - **Real bug: the "first 50 of N" note can be actively wrong.** The backfill job pages *past*
    50 tracks into the same `tracklist_json` the album page reads, but the page's "first 50 of
    N fetched" note fires purely on `total_tracks > 50` — so a fully-backfilled album (all N
    tracks stored) still renders the note claiming only 50 were fetched, directly under a table
    that in fact shows all of them.
  - **Real bug: the "Edit" link doesn't go where the spec (and the link's own text) say it
    does.** Both K's prose and the rendered link text promise "the review queue"; the actual
    redirect lands on the canonical *viewer*, not the review queue.
  - **Small bug: artist image picks the first entry in Spotify's response, not the largest
    one**, though the spec explicitly says "largest" — relies on undocumented API ordering
    rather than computing it.
  - **Small bug: a failed detail fetch (album tracklist or artist image) retries on every
    subsequent page view forever**, not just once — the spec says "first view only, cached
    forever"; the code only stamps the "don't re-fetch" marker on success, so a transient
    failure never gets remembered as attempted.
  - **Small bug: album artists render via an inline query instead of the shared
    `resolved_album_artist` view**, and lack the view's `DISTINCT` — an album crediting both an
    alias id and its already-canonical id would render that artist's name twice.
  - **Small bug: the playlist generation banner omits the span** the spec calls for ("Generation
    31" plus its date range) — only the ordinal and a link render; the span only appears one
    click deeper, inside the generation view itself.
  - **Minor:** `entity_link`'s real signature has two more optional params (`params`,
    `css_class`) than the spec documents — added by M, functionally harmless.
  - **Minor:** search's `LIKE` wildcards are unescaped, so a query containing a literal `%` or
    `_` behaves as a wildcard — deliberate and documented on `/dev/canonical`'s equivalent
    search, unstated here.
  - **Minor:** the artist page's playlist list includes playlists where every one of the
    artist's tracks has since been *removed*, with no visual distinction from a playlist that
    still carries them — the spec says "playlists their tracks appear in," ambiguous on tense.
  - **Minor:** `entities.py`'s actual scope is narrower than described — only `play_stats` and
    `playlists_for_tracks` live there; the album/artist rollups the spec attributes to it are
    inline in `app.py` instead.
- **Classification:** mostly `spec-stale` (H/M catching up); the "first 50" note, the Edit link
  destination, and the artist-image/failed-fetch-retry items are small `code-wrong` bugs worth a
  queued fix independent of any spec amendment.
- **Ruling:** All four small bugs: fix now. Edit link: the *destination* (the canonical viewer)
  turned out to already be correct — a group with its own entity page is by definition already
  settled, so "the review queue" (unreviewed candidates only) was never the right place to send
  it; only the link's own label text was wrong, and that's what got fixed. Rest: bulk amend.
- **Action:** **Fixed now and amended, 2026-08-17.** `entities.py`: `fetch_album_tracklist` and
  `fetch_artist_image` now stamp their `_pulled_at` column on a failed attempt too (previously
  only on success), so a transient failure stops retrying on every subsequent page view instead of
  forever; `fetch_artist_image` now picks the image with the largest `width` instead of
  `images[0]`. `app.py`: `album_page` now passes the actual stored tracklist count to the
  template, and `entity_album.html`'s "first N of total" note fires only when that count is truly
  less than `total_tracks` (a fully-backfilled album no longer shows a stale partial-fetch note);
  the album-artist query gained a `GROUP BY` so a credit under both an alias id and its
  already-canonical id no longer renders twice. `templates/entity_group.html`'s Edit link label
  changed from "in the review queue" to "in the canonical viewer," matching its actual (and
  correct) destination. All four verified via a Flask test-client + temp-DB script (dedup query,
  partial-vs-full "first N" note, failure stamping on both fetches, largest-image selection) —
  not the live app. **Amended:** the ordering/ranking language across §7/§10 rewritten (H's
  score-descending sort, comprehensively, not the piecemeal "stale in a few spots" framing);
  `wanted_uri`'s `album_id` column and `'backfill'` source; the fetch/queue split (M §4.4); the
  fetch failure-stamping fix documented in prose; `entity_link`'s two extra params; unescaped
  search-wildcard behavior (deliberate, cross-referenced to `/dev/canonical`'s equivalent); the
  generation banner's missing span; the artist-page playlist-list tense ambiguity;
  `entities.py`'s narrower real scope (only `play_stats`/`playlists_for_tracks`, rollups stayed
  inline in `app.py` — flagged for P3's query-extraction step, not fixed here). Spec stamped
  Audited.
- **Test:** The four small bugs above are each a clean, cheap specification test, now written
  against the fixed behavior: a fully-backfilled album must not render the "first 50" note; a
  credit under both an alias and its canonical id renders once, not twice; artist image selection
  picks max-width; a failed detail fetch does not retry on the very next page view. Also: the Edit
  link resolves to the canonical viewer, deep-linked to the correct group.

---

### P1-017 — `grouping-fixes-backfill-M.md`: blind audit found 8 differences, mostly about the backfill job's exact guarantees

- **Spec:** `docs/specs/grouping-fixes-backfill-M.md`. Blind audit confirmed M1's fix
  (`cross_component_pairs`/`mark_reviewed_pairs`), M1b's sessionStorage clearing, and M1c's
  `entity_link` sweep (including the "no `url_for` bypass survives outside `_macros.html`"
  acceptance grep) all match exactly. 8 differences, all in M2 (the album backfill), mostly
  about exactly what the job's "derived, checkpoint-free" guarantees actually cover:
  - **§4.4's "the backfill job calls the same two functions [as the album page]" is only half
    true.** Only `queue_wanted_uris` is actually shared; the job has its **own** tracklist-fetch
    function (`backfill._fetch_full_tracklist`), never `entities.fetch_album_tracklist` — and
    has to, since §4.5 requires paging past 50 items, which the entity page's fetch deliberately
    never does. §4.4 and §4.5 contradict each other on this point, and `CLAUDE.md`'s `entities.py`
    map entry repeats §4.4's incomplete version verbatim.
  - **`queue_wanted_uris`'s signature is missing a parameter in the spec** — it's bolded as
    `queue_wanted_uris(conn, album_id)` but the very next sentence requires "the caller's
    `source`"; the real signature is `queue_wanted_uris(conn, album_id, source)`, a required
    positional arg.
  - **`entity_link` gained a second optional parameter beyond what §3.2 documents** —
    `css_class`, added because the playlist page's generation-view toggle needs an `active`
    class that `params` alone can't express (an empty value emits no attribute at all, so
    existing call sites are unaffected). Also a small misattribution: §3.2 says the `tier=`
    parameter usage is on `generations.html:40`; it's actually on `entity_playlist.html`.
  - **The fourth job (backfill) has no closing `scoring.recompute()` call**, unlike the other
    eleven job call sites — `backfill.py` doesn't import `scoring` at all, even though its
    `_refresh()` does commit a scoring input (`canonical.ensure_track_groups`). Likely benign —
    N's `ensure_fresh()` backstop should catch it on the next request — but `CLAUDE.md`'s "all
    eleven job call sites" phrasing implies backfill is one of them when it's actually a
    documented exception nowhere written down. (This landed after M, when N introduced the
    backstop pattern — not M's fault, just never folded back.)
  - **A plain page load writes to the database.** §4.2/§4.6 both describe the derived model as
    checkpoint-free and the cost display as "computed with no Spotify calls" — true, but
    `GET /dev/roundtrip`'s `previews()` call chain runs `canonical.ensure_track_groups()` **and
    commits**, on every ordinary page view. Not a Spotify-request cost, but it is a write the
    spec never flags.
  - **"The three counts partition the queue exactly" breaks while the listening arm is muted** —
    the mute filter lives inside `_WORK_LIST_SQL` (so `remaining_uris` drops it) but is
    deliberately absent from `_LISTENING_REMAINING_SQL` (so the muted count still displays what
    it *would* contribute) — meaning `listening + album_page + album_backfill ≠ remaining` in
    exactly the muted state. The code comments this; the spec's "partitions exactly" sentence
    doesn't carve out the exception. Matters for P2 — a naive partition-sum test would fail
    while muted.
  - **The "0 albums with NULL total_tracks, no degenerate case to guard" claim has a live guard
    anyway, and it does something specific.** `backfill.py`'s missing-count arithmetic treats a
    NULL `total_tracks` as `0`, which makes that album compute as **permanently settled** —
    silently excluded from every future backfill run. Harmless today (0 such albums exist per
    the spec's own measurement) but a real, undocumented policy the moment one shows up.
  - **"Commit per album" is real but not where the spec implies.** `_run`'s per-album loop has
    no `conn.commit()` of its own — durability comes from `queue_wanted_uris`'s trailing commit,
    which is *skipped* by an early return when `tracklist_json` is empty. So an album whose
    tracklist fetch succeeds but has nothing to queue can leave that fetch's write uncommitted.
    Narrow edge case, but the "commits per album" guarantee is a side effect of a different
    module's function, not the job's own code.
- **Classification:** one `spec-stale` (the shared-functions claim), the rest `underspecified` —
  real, undocumented behaviors and edge cases rather than wrong ones.
- **Ruling:** Muted-partition: document the exception, no code change — current display
  (showing what muting would exclude) stays. NULL `total_tracks`: document as current policy,
  no code change — zero real occurrences, not worth guarding. Remainder: bulk-amend to match
  code.
- **Action:** **Amended 2026-08-17.** `grouping-fixes-backfill-M.md`: §4.2 documents the
  NULL-`total_tracks`-auto-settles policy explicitly; §4.6 documents the muted-partition
  exception explicitly (unmuted case is the invariant to test); §4.4 corrected (only
  `queue_wanted_uris` is shared, backfill has its own tracklist-fetch function) and its missing
  `source` parameter added to the documented signature; §3.2 fixed (`tier=` call-site
  misattribution, `css_class` param documented); §4.5 corrected ("commit per album" is
  `queue_wanted_uris`'s side effect, skippable on an empty-tracklist early return) and the
  missing `scoring.recompute()` call documented as a known exception; §4.6 documents the
  `previews()` DB-write-on-page-load. Spec stamped Audited.
- **Test:** Specification test: the three round-trip queue counts must sum to `remaining_uris`
  in the **unmuted** case (and the spec should say explicitly that the muted case is the
  exception); a NULL-`total_tracks` album should behave however Finn decides (currently:
  silently and permanently settled); the tracklist-fetch-then-nothing-to-queue commit path is
  worth a coverage note even if not urgent.

---

### P1-012 — `org-canvas.md`: blind audit found the spec never touched since implementation, 17 differences, three consequential

- **Spec:** `docs/specs/org-canvas.md`, Symr's first-built feature. Never edited since its
  implementing commit.
- **Blind audit completed** (Opus, no visibility into other P1 findings, full fresh read). Found
  17 differences. Most are routine drift from a spec that predates the entire rest of the app
  and was simply never revisited — same shape as P1-003/P1-011, and listed compactly below. Three
  are worth Finn's direct attention:
  - **Security-relevant claim, false, same pattern as P1-003's "no write scopes" line.** §Spotify
    integration states: *"Scopes needed: `playlist-read-private`, `playlist-read-collaborative`.
    (No write scopes, no library/liked scopes for this feature.)"* `config.py:14-17` requests
    **`user-library-read` and `playlist-modify-private`** — both now present, neither disclosed
    here. (They're real and intentional — `user-library-read` for Liked Songs, `playlist-modify-
    private` for the round-trip — just never reflected back into this spec.)
  - **The "no auth/login" non-goal is false.** §Non-goals: *"No auth/login (local/Tailnet
    single-user for now)."* The app-wide login guard (site-shell.md) gates `/canvas` like every
    other page today.
  - **A real algorithmic divergence in the chain-grouping fallback, `code-wrong` or `unclear`
    depending on intent.** §Export (Phase 1.5) says: *"Chains that never reach a label →
    Ungrouped."* `grouping.py:52-63` doesn't implement that — it iterates *all* candidates within
    the cutoff, nearest-first, and takes the **first whose chain reaches a label**, so a card
    whose immediate-nearest neighbor dead-ends can still attach via a longer alternate path
    instead of falling to Ungrouped. This changes real export output, not just prose. Worth
    deciding whether the fallback-search behavior is the intended (better) design and the spec
    should catch up, or whether "Ungrouped on first-neighbor dead-end" was actually wanted.
  - **Scope creep on the Pull button, undocumented.** §Spotify integration describes "Pull all of
    Finn's playlists into the snapshot table" as a lightweight metadata-only pull. The button now
    triggers `/api/snapshot/pull` → the **entire** snapshot engine (`snapshot.start_full_pull()`)
    — every playlist's full track contents, Liked Songs, artist/album records, the works. This is
    already correctly owned by `snapshot.md`/`track-metadata-A.md`/`partial-pulls-J.md`
    (documented elsewhere), but org-canvas.md's own description of what its Pull button does is
    now wrong by an order of magnitude.
  - **Fourteen more, routine drift** (spec-stale unless noted): Phase 1's nearest-label export
    algorithm no longer exists at all, only Phase 1.5's chained version (spec-stale, but a P2
    test-writer reading only §Export Phase 1 would test dead code); tie-breaking and cycle
    handling in the chain algorithm are implemented (`grouping.py:17,31-33`) but the spec still
    lists them as an *open question* (underspecified); `card` gained a `note` field
    (`db.py:142`), editable and PATCHable, entirely undocumented and **not included in the
    export** either (underspecified); a "Download .md" button shipped despite being explicitly
    listed as a later-tier "don't build" item (spec-stale); the proximity cutoff has a UI slider
    (default 300) and a "show grouping radius" overlay, neither specced (underspecified); cards
    and labels snap to a `GRID = 17.5` lattice that scales with the intrinsic-size slider — a
    whole unspecified subsystem (underspecified); Delete *and* Backspace both remove a label, and
    dropping a card on the tray (or pressing either key) unplaces rather than deletes it, wider
    behavior than "Delete key removes it" (underspecified); multi-select accepts ctrl in addition
    to shift/⌘, and marquee selection is midpoint-containment not intersection (underspecified);
    export always emits an `## Ungrouped` header even when empty, and sorts tray cards
    alphabetically — neither ordering rule is specced (underspecified); every item in the "Open
    implementation questions" list at the bottom was resolved during implementation and never
    recorded back (save-per-move cadence, the actual route/endpoint shapes, DB location, zoom
    0.25-2 / card-scale 0.4-1.5 ranges, cutoff default 300, the tie rule) (underspecified);
    unfollowed playlists (`snapshot.unfollowed_at`) are never removed or flagged on their canvas
    card, so a card for a playlist Finn no longer follows sits indistinguishable from a live one
    — the spec's own "removed → decide" open question, still undecided (underspecified); the
    `card` table carries a `UNIQUE(board_id, entity_type, entity_id)` constraint the data-model
    section doesn't mention, which the snapshot-pull upsert path relies on (underspecified); the
    status/branch header ("ready to implement", pinned to a since-merged branch) is stale process
    metadata, same as several other pre-lettering specs.
- **Classification:** `spec-stale` (bulk) / `code-wrong` or `unclear` (the chain-fallback item) /
  `underspecified` (most of the rest, since these are real undocumented behaviors, not wrong ones)
- **Ruling:** Scopes/auth: fix regardless. Chain-fallback: code is right, keep it, amend the spec.
  Pull-button scope creep: doc-only fix. The other 13: broad update, but every item must point to
  the concrete current behavior (function/file, actual constants/values) rather than a vague
  "this changed" note, so a P2 test-writer has something real to derive a test from.
- **Action:** **Amended 2026-08-17.** Rather than scattering edits across 17 places in a doc this
  comprehensively stale, added one consolidated "Corrections to current behavior" section
  immediately after the header (before the original body, which stays intact as historical design
  intent) covering all 17 items with concrete specifics: the two security claims; the
  chain-fallback algorithm's real nearest-first-search behavior (`grouping.py`'s
  `_sorted_candidates`/`resolve()`, line-cited) ratified over the spec's literal
  first-dead-end-only reading; the Pull button's real scope; Phase 1's dead nearest-label
  algorithm; the tie-break/cycle-handling rule now implemented; `## Ungrouped`'s unconditional
  render and the tray's alphabetical sort; `card.note` (undocumented, PATCHable, absent from
  export); the shipped Download-`.md` button; the cutoff input + radius-overlay UI; the
  `GRID = 17.5` snap subsystem; Delete/Backspace/drop-on-tray's real unplace-vs-delete behavior;
  ctrl-as-a-multi-select-modifier and marquee's midpoint-containment rule; every "Open
  implementation question" resolved with its actual answer (save-on-move-completion via
  `persistPosition()`, the seven real routes, DB location, zoom `0.25`–`2` / scale `0.4`–`1.5`,
  cutoff default `300`, the tie rule); the still-genuinely-unresolved unfollowed-playlist display
  gap (called out separately from the resolved ones, since it's still an open question, not a
  silently-answered one); and the `UNIQUE(board_id, entity_type, entity_id)` constraint the
  snapshot-pull upsert relies on. Status/branch header struck as stale process metadata. Spec
  stamped Audited.
- **Test:** The chain-grouping fallback-search behavior (does a dead-ending nearest-neighbor
  really search further via the next-nearest candidate, or fall straight to Ungrouped) is the one
  item with real behavioral stakes worth a characterization test; `## Ungrouped`'s unconditional
  render and the tray's alphabetical sort are cheap to pin too. The rest is mostly UI/export-format
  detail, lower value to test.

---

### P1-018 — `canonical-tracks.md`: blind audit found 16 differences, three overlapping P1-008, several contradicting the spec's own "Out of scope" section

- **Spec:** `docs/specs/canonical-tracks.md` and its four sub-specs in `docs/canonical-tracks/`.
- **Blind audit completed** (Opus, no visibility into other findings). Three items confirm
  P1-008 independently (the representative-track tiebreak, `track.popularity`/`album_image_url`
  no longer existing) — good corroboration, not new. 13 genuinely new:
  - **Two more flat contradictions of the spec's own "Out of scope" section**, same shape as
    P1-008's third bullet: §Out of scope explicitly says "Automatic grouping without review —
    every merge is confirmed by hand… nothing is written until Enter" — `canonical_autogroup.py`
    does exactly that, no review, via the `/api/canonical/autogroup/*` endpoints. Separately,
    §Data model says "no decision log. Undo is in-session only (client-side)" and §Out of scope
    lists "cross-session undo history" as excluded — `auto_group_run` plus three
    `auto_group_snapshot_*` tables are exactly that, a server-side, cross-session decision log
    with restore.
  - **`canonical_group`'s schema is missing the `auto_run_id` column** (same root cause as the
    above — the auto-group feature's own tagging column).
  - **A latent ordering bug, leaning `code-wrong`.** The spec's invariant that `reviewed_pair`
    always stores `track_id_a < track_id_b` is enforced inside `mark_reviewed`, but
    `mark_reviewed_pairs` — the newer pair-level writer P1's own findings already flag as the
    real write path — inserts whatever order it's handed with no ordering check of its own.
    Today's only caller happens to pre-sort, so nothing has broken yet, but the invariant is no
    longer enforced at the layer that's supposed to own it.
  - **Three more dead-route references**, all the K-supersession pattern seen repeatedly
    elsewhere in this audit: Phase 1's route inventory and `viewer-page.md` (twice) still send
    links to `/dev/snapshot/track/<id>` / `/dev/snapshot/playlist/<id>`.
  - **`viewer-page.md`'s ordering claim is stale** — it says group browsing sorts by "playlist
    impact (total live memberships)"; actual ordering is by score (`impact` retired site-wide by
    H, same pattern P1-013 found in `canonical_detect._order()`).
  - **`viewer-page.md`'s "no pagination" claim is stale** — the listing is capped at 50 unless a
    filter is set (already documented behavior, just never folded back into this spec).
  - **The cross-artist entry point named in `viewer-page.md` no longer exists** —
    `?queue=cross-artist` is gone, replaced by the dedicated `/dev/canonical/cross` route (M's
    rework, already covered elsewhere — this spec just never got the pointer).
  - **The "unreviewed if any pair is missing" rule is narrower for cross-artist buckets** than
    stated — they settle on cross-component pairs only (M1's fix, same underlying fact P1-013
    already surfaces from E's side).
  - **Representative-track consumption is broader than "song tier only."** The spec and its
    grouping-engine sub-spec both say song is the only tier anything consumes a representative
    for; `representative()`/`group_tree()` are actually called on **version**-tier groups too
    (album/artist/search pages), though pinning stays song-only. Worth a decision, not just a
    doc fix — should pinning follow, or is version-tier representative-without-pin intentional?
  - **`apply_partition`'s real signature carries a `cleanup=True` default** the sub-spec doesn't
    document (already independently known from `grouping-catch-up-E.md`'s own §0.2, cross-spec
    confirmation).
  - **Two of the four "Notes for future consumers, not decided design" items are actually
    built** — listening history's open question is answered by `roundtrip.py`, and the rollup
    toggle exists as `generations.py`'s `tier="version"|"song"` parameter. (The other two — dedup
    report, cover audits — remain unbuilt stubs, consistent with the spec.)
- **Classification:** mixed, mostly `spec-stale`; the `mark_reviewed_pairs` ordering gap leans
  `code-wrong` (or `underspecified` if the ordering was never meant to be enforced there).
- **Ruling:** Out-of-scope contradictions folded into P1-008's amendment. `mark_reviewed_pairs`
  ruled: fix now, sort inside the function.
- **Action:** **Amended and fixed now, 2026-08-17.** `canonical-tracks.md`: Out-of-scope
  contradictions, `canonical_group.auto_run_id` column, three dead-route references (Phase 1 +
  `viewer-page.md` ×2), and the "2 of 4 notes for future consumers already built" items all
  amended. `grouping-engine.md`: `apply_partition`'s `cleanup=True` default documented.
  `viewer-page.md`: ordering (impact → score), no-pagination claim, dead cross-artist entry
  point, dead track-page route, and the cross-artist-narrower-unreviewed-rule all amended; both
  sub-specs stamped Audited alongside the main file. **`mark_reviewed_pairs` fixed** —
  `canonical.py` now normalizes `(min(a,b), max(a,b))` inside the function itself rather than
  trusting callers to pre-sort; verified against a temp DB. `detection.md` and `review-ui.md`
  were in the blind audit's read scope but produced no findings of their own and are **not**
  stamped Audited — flagged as unverified rather than assumed clean. Spec stamped Audited
  (jointly with P1-008).
- **Test:** Specification test: `mark_reviewed_pairs` given an unsorted pair should still store
  it as `(min, max)` — or the spec should say explicitly that callers own the ordering.

---

### P1-019 — `async-recompute-N.md`: blind audit found 7 differences, mostly about what actually still self-heals

- **Spec:** `docs/specs/async-recompute-N.md`. Blind audit confirmed the core worker
  implementation, all 20 call sites (5 async / 4 sync / 11 job), and the click-feedback claims
  match exactly. 7 differences, two worth flagging directly:
  - **§6.2's central claim — "transient failures still self-heal" — is no longer true.** The
    spec says a transient failure's fingerprint stays behind and the next backstop check retries
    automatically, with only *repeatable* failures suppressed via `_failed_fingerprint`. The
    code arms `_failed_fingerprint` on **every** failure, transient or not, so recovery now
    requires either a fresh commit moving one of the nine tracked `COUNT(*)`s, or the manual
    button — there's no longer a distinct self-healing path for the transient case the spec
    describes. Worth a decision: is losing transient self-heal acceptable, or was that a
    regression during implementation?
  - **§3.6's premise ("every job ends with recompute on success and both failure paths") is
    false for `history_import.py`**, which has one `except` block whose recompute is
    conditional on `import_id is not None`. Likely benign (nothing durable happens when it's
    `None`), but §3.6's guarantee — which §5's deferral logic explicitly leans on — doesn't
    universally hold.
  - **Five smaller items:** §5.2's stated reason for deferring while the worker is alive doesn't
    match the code's own comment (the real reason is a ~5ms fingerprint-read saving, not
    preventing a synchronous recompute — since §5.1 already removed the only path that could
    fire one); `/dev/generations/confirm` only recomputes on "yes," not on every submission, as
    §4.3 implies; `generation_confirm.js` is a bare `DOMContentLoaded` listener, not the IIFE the
    spec describes (behavior matches, structure doesn't); the failure banner doesn't actually
    reuse `.error` styling as §7.1 says (a separate rule was added because `.error` turned out to
    be more narrowly scoped than expected); and the banner fires for *any* recompute failure,
    including the manual button's (which already surfaces its own error to the clicker), not
    just the "silent background failure" case §7.1 frames it around.
- **Classification:** mostly `spec-stale`; the transient-self-heal item settled as `spec-stale`
  too — see ruling.
- **Ruling:** Code is right, ratified rather than changed — `recompute()` never talks to the
  network or Spotify (local SQLite + in-process Python only between `_observe()` and the
  `INSERT`s), so a caught failure is overwhelmingly likely to be a deterministic bug against the
  current data, not a genuine one-off flake. The spec's original transient/repeatable distinction
  can't actually be implemented anyway — `recompute()` has no way to know in advance which kind of
  failure it just caught short of retrying, and unbounded retry is exactly the spin §6.2 exists to
  prevent. Chosen over adding a time-based retry as the lower-risk option, per the instruction to
  prefer whatever doesn't add new, untested code this early. Remaining 6 items: bulk-amend to
  match code, as recommended.
- **Action:** **Amended 2026-08-17.** `async-recompute-N.md` §6.2 rewritten to drop the
  transient/repeatable distinction and state plainly that every failure suppresses auto-retry
  until a fresh commit moves the fingerprint or the manual button is clicked, with the
  no-network/no-Spotify reasoning folded in. §3.6 corrected (`history_import.py`'s recompute is
  conditional on `import_id is not None`, not unconditional on both failure paths). §4.3's table
  corrected (`/dev/generations/confirm` recomputes only on "yes"). §5.2 corrected (the real reason
  for deferring while the worker is alive is the ~5ms fingerprint-read saving during its ~1.8s
  window, not preventing a synchronous recompute — §5.1 already removed the only path that could
  fire one). §7.1 corrected (banner is its own `.scoring-banner` rule, not a reuse of `.error`;
  documented that it fires for any recorded failure, including the manual button's own). §7.2
  corrected (`generation_confirm.js` is a bare `DOMContentLoaded` listener, not an IIFE). Spec
  stamped Audited.
- **Test:** Specification test: a recompute failure must suppress auto-retry on that exact
  fingerprint until either a fresh commit moves it or the manual button is called directly —
  covering both a "transient" and a "repeatable" failure identically, since the spec no longer
  distinguishes them. Also worth covering: `/dev/generations/confirm` does not call
  `scoring.recompute()` on a "no" decision.

---

### P1-020 — `detection-artist-model.md`: blind audit (Sonnet) found 3 differences, all contained

- **Spec:** `docs/specs/detection-artist-model.md` and its living companion
  `docs/canonical-tracks/detection.md`. Blind audit confirmed the great majority matches exactly
  — the `artist_alias`/`reviewed_artist_pair` schema, `mark_same`/`mark_not_same`/`unmerge`'s
  exact semantics, the three id-set definitions and the compilation-album fallback, the full
  view chain (`resolved_track_artist` → … → `track_artists`) including the specific performance
  numbers (17s/19s/44ms) baked into its own comments, `normalize_artists`'s confirmed complete
  deletion, and §4's backfill/request-counter machinery. (Skipped re-deriving the canonical-id
  tiebreak change already covered by P1-010, per the audit's instructions.) Three real gaps:
  - **`/dev/artists`'s queue ordering is undocumented.** `candidate_pairs()` sorts by
    `scoring.artist_group_score` descending — real, deliberate logic with no spec clause behind
    it. `underspecified`.
  - **§2's "Unchanged" list is wrong about ordering** — it lists "ordering by playlist impact" as
    unchanged; `canonical_detect._order()`'s own docstring says `impact` was retired in favor of
    score, citing `scoring-H.md` §11.1. Same root cause as the ordering staleness P1-013/P1-018
    already found elsewhere in the grouping specs — H retired `impact` everywhere, and every
    earlier spec that mentioned it never got the memo. The companion doc
    `docs/canonical-tracks/detection.md` has the identical stale claim. `spec-stale`.
  - **§3's itemized call-site inventory is stale and was internally miscounted even when
    written** (says "six template spots" while its own enumeration lists seven). Two of the six
    named templates (`snapshot_track.html`, `snapshot_playlist.html`) were deleted by K 10 days
    after this spec landed — the K-supersession pattern seen repeatedly elsewhere in this audit.
    The `app.py` SQL-read and search-predicate counts have also drifted (3 reads not 4, 4 search
    predicates not 2) as later steps added more call sites. Reads as an accurate snapshot at
    landing time that nobody updated since, not an implementation error. `spec-stale`.
- **Classification:** as above — one `underspecified`, two `spec-stale`.
- **Ruling:** Confirmed, bundled into the same amendment as P1-010 (this finding's first two
  items are the same facts P1-010 independently surfaced). §3's inventory: point at the view
  chain as the source of truth rather than re-counting precisely.
- **Action:** **Amended 2026-08-17**, jointly with P1-010 — see that entry for what changed.
  Spec stamped Audited (jointly with P1-010).
- **Test:** None with real stakes; if anything, `candidate_pairs()`'s score-based ordering is
  worth a specification test once §1's ordering rule is written down somewhere.

---

### P1-021 — `scoring-H.md`: blind audit (Sonnet) found only 2 differences, both narrow — this spec holds up exceptionally well

- **Spec:** `docs/specs/scoring-H.md`, the largest and most architecturally central spec in the
  project. Blind audit (told to exclude what P1-008/P1-010/P1-019 already cover) confirmed
  essentially everything else formula-for-formula against `scoring.py` and
  `docs/scoring/tuning_prototype.py`: the version-score math (inputs, play weight, exposure/rate,
  saturation, shrinkage, buckets), the combiner and album padding, both horizons and their blend,
  the display transform, materialization and wholesale recompute, the schema, and every §11
  consumer site — `impact` confirmed **fully retired**, zero references anywhere in `.py`,
  templates, or JS. This corroborates the earlier manual spot-check (§10.1's 17-constant
  parameter table matched byte-for-byte) with a much deeper independent pass and the same
  conclusion: H is the best-maintained spec in the codebase, likely because
  `tuning_prototype.py` keeps it honest as an executable reference. Two real gaps, both
  `underspecified` rather than wrong:
  - **§6's subtier blend formula calls for "§4" over the subtier's own track set — but §4
    includes shrinkage, and the code's subtier path deliberately skips it.** The code's own
    docstring admits this was a judgment call made during implementation because the spec was
    silent on how much of §4 "own" re-running was supposed to mean; the executable reference
    (`tuning_prototype.py`) doesn't resolve it either — its `subtier_score()` takes a
    pre-computed `own_score` as a bare parameter with no derivation, and §0.2 itself already
    flags that function as "defined and never called." Low blast radius (`SUBTIER_W` only
    affects representative-track tie-breaking, already known to be unvalidated) but a genuine
    unresolved spec question, not just a documentation gap.
  - **§5.3's `FEATURED_WEIGHT` trigger is glossed simpler than what's actually built.** The spec
    describes it as "track_artist minus album_artist"; the real `track_artist_role` view falls
    back to treating every credit as primary whenever *none* of the track's artists match an
    album artist — a deliberate fix (its own comment explains it exists so Various Artists
    compilations don't misclassify the real artist as featured) that materially changes which
    credits get discounted, and the spec's plain-English description doesn't mention it.
- **Classification:** both `underspecified`.
- **Ruling:** Amend spec, both items. §6: code's no-shrinkage-on-subtier-own-score design ratified
  — a second full bucket-baseline system per finer tier isn't worth building for a term whose only
  job is breaking a representative-track tie, already flagged unvalidated. §5.3: amend to describe
  the real per-track fallback.
- **Action:** **Amended 2026-08-17.** `scoring-H.md` §6 gained a new paragraph settling the
  question explicitly (raw only, never independently shrunk, and why — including that
  `tuning_prototype.py`'s `subtier_score()` never resolves it either, since it's never called).
  §5.3 rewritten to describe `track_artist_role`'s real per-track fallback (promotes every credit
  on a track to `primary` when none of them match an album artist) rather than the simpler
  per-credit "track_artist minus album_artist" gloss. Spec stamped Audited.
- **Test:** Specification test: the subtier blend's own-score component must not be shrunk toward
  a bucket baseline (raw §4.4 output only) — the negative case, since the positive ("no shrinkage
  applied") is what's easy to accidentally reintroduce later. Also: `track_artist_role`'s
  all-credits-primary fallback on a track with zero album-artist-matching credits.

---

## Batch-by-batch account

What each batch covered, and how the original pass's "checked out clean" claims held up once
every spec got a real, independent from-scratch audit rather than a spot-check. **Batches 2 and
4 were wrong outright; batch 3 held up almost entirely** — the one batch where the original
manual read turned out to be reliable.

**Batch 2 (Grouping) — `grouping-catch-up-E.md`, `grouping-fixes-backfill-M.md`.** The original
spot-check (a handful of highest-risk claims each) matched cleanly, but a full blind audit found
11 real differences in E (P1-013, including an inverted boolean on a headline rule and two
modules citing contradictory validation figures) and 8 in M2's backfill job (P1-017, mostly
about the exact edges of its "derived, checkpoint-free" guarantees). Read those findings, not
this paragraph.

**Batch 3 (Scoring) — `scoring-H.md`, `async-recompute-N.md`.** The original manual pass held up
well and both specs later got a full blind audit anyway, on top of it. `scoring-H.md` (P1-021)
came back with only **2** narrow `underspecified` gaps out of 1265 lines — its entire §10.1
parameter table (17 constants) already matched `scoring.py` byte-for-byte from the original
pass, and the blind audit corroborated everything else formula-for-formula. This is the
best-verified spec in the project, by a wide margin. `async-recompute-N.md` (P1-019) came back
with 7 differences, one with real stakes (a stated transient-failure self-heal guarantee that no
longer holds in the code). §11.3's two "becomes score-weighted" changes are what P1-008 and
P1-010 trace to, confirmed accurate on H's side both times — the drift is entirely in the two
earlier specs that never got the memo.

**Batch 4 (Read paths & UI) — `entity-pages-K.md`, `generations-B.md`, `error-pages.md`,
`org-canvas.md`.** Same story as batch 2: the original spot-check found nothing, a full blind
audit found real material in all four — 13 differences in K (P1-016, a few small genuine bugs: a
misleading "first 50 tracks" note, a mislinked Edit button, artist images not picking the
largest), 10 in B (P1-015, including a NULL-`started_at` crash worth checking against the real
DB), 6 in error-pages (P1-014, including an empirically-verified-wrong claim about Flask's
debugger), and 17 in org-canvas (P1-012, above) — the most, since that spec turned out to have
never been touched at all since implementation. Read those findings, not this paragraph.

**The two core grouping specs and `detection-artist-model.md`** — `canonical-tracks.md` (P1-018,
16 differences, mostly corroborating and extending P1-008) and `detection-artist-model.md`
(P1-020, 3 contained differences, corroborating and extending P1-010) both got a full blind audit
on top of their original clustered/solo review, closing out the last real gaps in this file's
coverage.
