# Phase 3 — Grouping engine

Sub-spec of `docs/specs/canonical-tracks.md`. Read the tier model and data model there first.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`), finding P1-018.

This phase builds the server-side module (`canonical.py`) that owns the four-tier group ids. It has no UI of its own — phases 5 and 6 drive it. Its semantics are the intricate part of the feature, so they're pinned down exactly here.

## Invariants

1. **Nesting.** If two tracks share a `release_id` they share `recording_id`, `version_id`, and `song_id`. If they share a `recording_id` they share `version_id` and `song_id`. And so on. The engine enforces this on every write; no operation may leave the DB violating it.
2. **Totality.** Every row in `track` has exactly one `track_group` row, with four non-NULL ids.
3. **Id permanence.** A `canonical_group.id` is never reused for a different group (`AUTOINCREMENT` guarantees this even after deletes).
4. **No orphans.** A `canonical_group` row with zero members is deleted.

## Bootstrapping

`ensure_track_groups(conn)` — for every `track` row lacking a `track_group` row, allocate four fresh singleton groups (one per tier) and insert the row. Idempotent and cheap.

Call it at the top of every `/dev/canonical*` request and at the end of a snapshot pull, so newly-pulled tracks always have ids.

## The one write operation

Everything the UI does — merge, detach, ungroup, clear — is expressed as **one call**:

```python
apply_partition(conn, labels, cleanup=True) -> dict
```

**`cleanup` documented 2026-08-17 (P1-018)** — not in the original signature above.
`cleanup=False` skips the four `_cleanup_tier` passes (a full `canonical_group` × `track_group`
join each, which dominate the cost of a batch of calls). Only a caller that runs
`cleanup_all_tiers()` itself once its own batch is done may pass it — `grouping-catch-up-E.md`'s
auto-group run is the one caller today.

`labels` maps each track in the queue item to a label per tier:

```python
{
  "4uLU6h...": {"song": "s1", "version": "v1", "recording": "r1", "release": "x1"},
  "1301Wl...": {"song": "s1", "version": "v1", "recording": "r1", "release": "x2"},
  "7ouMYW...": {"song": "s1", "version": "v2", "recording": "r2", "release": "x3"},
}
```

Labels are **arbitrary local strings**, meaningful only within the call — the client never sees or invents real group ids. Two tracks sharing a label at a tier are in the same group at that tier; different labels mean different groups. The engine reconciles the DB to match.

This contract keeps all grouping logic server-side. The client only assigns labels (see `review-ui.md`).

**Validation** (reject with a 400 on failure — the UI should never produce these):
- Labels must be nested-consistent: two tracks sharing a release label must share their recording, version, and song labels; and so on up.
- Every track id must exist in `track`.

## Reconciliation algorithm

Process tiers **finest → coarsest**: release, recording, version, song. For tier *t*:

1. **Build parts.** Group the item's tracks by their tier-*t* label. This is the only *hard* partition: two item tracks with different labels can never share a part.
2. **Downward closure** (nesting-mandatory). Expand each part with every track — in the item or not — that shares a member's *just-assigned* finer-tier group, unless the item already claims that track for a different part. For release (the finest tier) this is empty; for recording it's the release groups assigned earlier in this same run; and so on. This is what nesting forces: a release group can never straddle two recording groups.
3. **Upward closure** (preservation). Expand each part with the existing tier-*t* group-mates of its members — every track already sharing that group, whether or not it's in the item — **except** any track already claimed by another part in step 1 or 2. An item can therefore only split a group it *actively disagrees* about; tracks it never mentions come along unchanged.

   This is the rule that makes a no-op commit a genuine no-op. Without it, an item holding only part of a group (an ad-hoc selection, or a candidate group narrower than a hand-built song group) reads as "everyone else is excluded" and silently cuts the rest loose.
4. **Choose the group id for each part.** Let *candidates* be the existing tier-*t* ids held by the part's members **whose full current membership is a subset of the part**. If any, reuse `min(candidates)`; otherwise allocate a fresh `canonical_group` row. So a group that only *gains* members keeps its id; a group the item genuinely **splits** yields new ids for both halves and the old row is deleted.
5. **Write** `track_group.<tier>_id` for every track in every part.
6. **Clean up.** Delete any `canonical_group` row at this tier that now has zero members. For a surviving group whose membership changed, clear `representative_track_id` to NULL if the pinned track is no longer a member.

Tracks *outside* the item are pulled into a part only by steps 2 and 3, and step 3 preserves their ids rather than changing them. The one way an outside track genuinely moves is when the item merges two existing groups: `A~B` already, and the item says `A~C`, so `B` joins `C`'s group by transitivity. That is reported in `dragged_in`.

**The trade-off this rule buys:** an item can no longer split apart tracks it doesn't show you. Selecting one track and pressing `0` detaches it from everything *in the item*, but not from a group-mate that isn't on screen. That's deliberate — a decision shouldn't reach tracks the reviewer can't see — and it rarely bites, since a group-mate almost always shares the title that built the candidate bucket in the first place.

### What the primitives look like in this model

The three actions the UI describes all fall out of the same reconciliation — the client just sends different labels:

- **Merge** *S* at tier *t*: give every track in *S* the same tier-*t* label (and unify their labels at all coarser tiers, since nesting requires it). Tracks outside *S* sharing a finer group, or an existing tier-*t* group, with a member come along via closure.
- **Detach** *S* (a strict subset of one group) at tier *t*: give *S* a fresh label, and keep the rest of the group **in the item** on its old label. The rest of the group has to be present — a track the item omits is preserved, not detached (step 3), so detaching from it is impossible without showing it.
- **Ungroup** an entire tier-*t* group: give each of its constituent **finer** groups its own label — so a song group splits into its version groups rather than shattering into singletons. Coarser tiers untouched.

### Return value

```python
{"tracks": {track_id: {"song": id, "version": id, "recording": id, "release": id}, ...},
 "dragged_in": [track_id, ...]}
```

`tracks` covers the item's tracks **plus** anything pulled in by either closure. `dragged_in` lists only those outside tracks whose ids **actually changed** — i.e. the item merged two existing groups and the absent members of one followed. Tracks merely *preserved* by step 3 keep their ids and are not reported, so the note stays quiet unless something genuinely moved.

## Marking review

```python
mark_reviewed(conn, track_ids)
```

Inserts every unordered pair from `track_ids` into `reviewed_pair` (always `a < b` lexicographically), refreshing `decided_at` on conflict. Called on commit alongside `apply_partition`, never separately — reviewing *is* deciding, even when the decision is "leave them all apart."

A candidate group counts as **unreviewed** if any pair among its tracks is missing from `reviewed_pair`.

## Representatives

```python
representative(conn, group_id) -> track_id
```

Returns `canonical_group.representative_track_id` when set; otherwise computes it over the group's members: **most live memberships** (`membership` rows with `removed_at IS NULL`) → **oldest `added_at`** → **lowest `track_id`**.

```python
pin_representative(conn, track_id)
```

Sets that track as the pinned representative for its **song** group only (the "★" action in the review UI, and the clickable star on `/dev/canonical`). Song is the only tier anything displays or consumes a representative for — pinning at the finer tiers too was tried and dropped: a version-classified track's own singleton version/recording/release groups would trivially "self-pin" alongside a genuinely different track's own trivial self-pin, showing two "representatives" for what's really one decision.

`track.popularity` is NULL library-wide (Spotify stopped returning it on playlist-item track objects), so it is not part of the tie-break.

## Consumer helpers

Thin read helpers for phases 5–6 and future features:

- `group_members(conn, group_id)` → track ids in a group.
- `groups_for_track(conn, track_id)` → its four ids.
- `nested_tree(conn, song_id)` → the song's version → recording → release → track nesting, for the viewer page.
- `tier_counts(conn)` → distinct group counts per tier, and how many are non-singleton.

Consumers outside this feature always join through `track_group`; they never re-derive grouping. For example, version-level playlist dedup is:

```sql
SELECT m.playlist_id, tg.version_id, COUNT(*) AS copies
FROM membership m
JOIN track_group tg ON tg.track_id = m.track_id
WHERE m.removed_at IS NULL
GROUP BY m.playlist_id, tg.version_id
HAVING copies > 1
```

## Testing this phase

No UI yet, so verify from a Python shell against the real DB: bootstrap, then run merge/detach/ungroup sequences and assert the invariants hold (nesting, no orphans, totality) and that ids behave as specified — reused on pure growth, fresh on split. Phase 6's stats block is the ongoing check that nothing drifts.
