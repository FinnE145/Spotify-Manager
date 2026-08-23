# P3 — Findings

Same convention as P1 and P2: every finding gets a `P3-###` id and a ruling from Finn before the
session that found it merges. Instructions: `docs/codebase-health/P3_refactor.md` §7.

**P3 is strictly behaviour-preserving** (§2), so a bug found while moving code is recorded here and
the code moves unchanged. Fixing it in the same diff would destroy the one thing that makes a
byte-exact diff meaningful: that every difference is a defect.

| id | session | subject | ruling |
|---|---|---|---|
| P3-001 | 1 | `SELECT *` made `/api/board`'s JSON key order depend on migration history | **leave as is** (2026-08-22) |

---

## P3-001 — `SELECT *` made an API payload's key order depend on the database's migration history

**Found:** session 1, while doing §4.5. **Status:** not a bug that ever bit; a real instance of the
class §4.5 exists to prevent, and it decided which column order the fix had to use.

`_board_state` did `SELECT * FROM card` and `dict(row)`-ed the result straight into `/api/board`'s
JSON. `SELECT *` returns columns in the table's *physical* order, and `card.note` arrives by
`ALTER TABLE ... ADD COLUMN` (`db.py:660`) rather than from `SCHEMA`. So the two orders differ:

| | order of `card` |
|---|---|
| a database that migrated into `note` (**`symr.db`**) | `… image_url, placement, x, y, note` |
| one built fresh from `db.py`'s `SCHEMA` (**every test DB**) | `… image_url, note, placement, x, y` |

Verified empirically both ways on 2026-08-22. `snapshot.tracks_pulled_snapshot_id` has the same
split, though it only feeds templates that read by name, so nothing observable rides on it.

**Nothing was ever broken by this.** `canvas.js` reads the payload by property name, and JSON
objects are unordered by specification. What it means is narrower and still worth writing down:
before this change, the byte content of an API response was a function of *how the database got to
its current schema*, not of the code — two installs of Symr at the same commit served different
bytes on `/api/board`. That is the silent-widening failure mode §4.5 names, one step further along
than the version it describes.

**What the fix did.** The named list uses the **migrated** order — the one every existing database
actually has — so `/api/board` and `/api/export` are byte-identical on `symr.db` and the §3 golden
compare stays clean. Naming the columns also removes the dependency itself: from here on every
install serves the same key order regardless of its migration history.

**The one consequence to be aware of:** on a *fresh* database the payload's key order changes
(`note` moves from seventh to last). That is invisible to `canvas.js` and to the suite, which is
green, but it is the one respect in which this change is not literally a no-op everywhere.

**Why it is recorded rather than just done.** The choice was between the migrated order (zero
golden diffs, matches reality) and `SCHEMA`'s logical order (two deliberate golden diffs). §1 and
§3.3 make "any diff at all is a bug" load-bearing for the whole of P3, and §2 makes the session
behaviour-preserving, so the migrated order was the only option consistent with both. But it does
pin an accident of migration history as the canonical order, and that is Finn's call to confirm
rather than mine to make silently.

**Ruled 2026-08-22: leave as is.** If the logical order is ever preferred, it belongs in its own
later change with its own justification, not inside a refactor whose acceptance criterion is that
nothing observable moved.

---

## Not findings — decisions taken in passing, recorded so they are not re-litigated

- **`docs/specs/canonical-fixes.md`'s archived blocks were left verbatim.** §4.4 asked for the two
  doc references to `all_candidate_groups` to be updated. §2.1 there is a dated measurement table
  and §2.2 records the cause as it stood in 2026-08-07; both sit under a P1-009 note that already
  declares them archived and already names `cross_artist_groups` as long gone without editing the
  blocks themselves. That established convention was followed — the prose note carries the
  correction, the dated measurement is not re-derived (`CLAUDE.md`'s rule for the roadmap's
  measurements, applied here for the same reason).
- **`normalize.py` is new and is not yet in `CLAUDE.md`'s codebase map.** §4.3 puts the map update
  in session 3 deliberately, once the code has settled. §4.3's module-list check is the backstop
  that makes forgetting it impossible, and it is the exact drift class that check was designed for
  ("a module added and never documented, which P3 itself would commit if 4.2 landed unrecorded").
- **`tests/test_artists.py`'s identity assertion was retired, not moved.** It asserted
  `detect.normalize_name is detect._normalize_base_string` — that artist-name and title-base
  normalization were one function. Both names are gone and there is exactly one function now, so
  the check would compare it against itself. The property is true by construction; the three
  behavioural assertions beside it were retargeted at `normalize.base_string` and kept.
