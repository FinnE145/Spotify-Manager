"""Generations & tenure (docs/specs/generations-B.md). A generation is one
playlist in the chain of 36+ current-favs playlists; tenure is a version (or
song) group's presence across them, which is deliberately not the same thing
as membership (presence in any playlist).

generations() and tenures() are read-only and never commit, same as
canonical.py. Detection/confirm/decline own the generation table and
snapshot.generation_declined -- callers commit those.

Nothing here is materialized: tenure keys on version_id, which changes every
time a group is reviewed in the canonical queue, so this is all derived at
query time."""

import re
from collections import defaultdict
from datetime import datetime

import jobs

TIER_COLUMN = {"version": "version_id", "song": "song_id"}

# Only the modern vXX.Y.Z scheme -- every future generation uses it, and the
# historical naming schemes are handled by scripts/seed_generations.py.
_GENERATION_NAME_RE = re.compile(r"^v(\d+)\.\d+\.\d+$")


def _tier_column(tier):
    try:
        return TIER_COLUMN[tier]
    except KeyError:
        raise ValueError(f"invalid tier: {tier!r}") from None


def generation_spans(conn):
    """Ordered per-generation rows with the tiled span: started_at is the
    earliest live added_at in the generation's own playlist, ended_at is the
    next generation's started_at (None for the active one, whose span is
    still open). Tier-independent -- used directly by callers that just need
    the ordinal/name list (e.g. the tenure strip), and internally by
    generations() and tenures()."""
    rows = conn.execute(
        """
        SELECT g.ordinal, g.playlist_id, s.name,
               (SELECT MIN(m.added_at) FROM membership m
                WHERE m.playlist_id = g.playlist_id AND m.removed_at IS NULL) AS started_at
        FROM generation g
        JOIN snapshot s ON s.playlist_id = g.playlist_id
        ORDER BY g.ordinal
        """
    ).fetchall()
    spans = [dict(row) for row in rows]
    for i, span in enumerate(spans):
        span["ended_at"] = spans[i + 1]["started_at"] if i + 1 < len(spans) else None
    return spans


def _presence_by_ordinal(conn, column):
    """{ordinal: {group_id, ...}} at the given tier column."""
    presence = defaultdict(set)
    for row in conn.execute(
        f"SELECT DISTINCT ordinal, {column} AS group_id FROM generation_presence"
    ):
        presence[row["ordinal"]].add(row["group_id"])
    return presence


def generations(conn, tier="version"):
    """The generation list, ordered by ordinal, with per-generation counts
    at the given tier. Requires canonical.ensure_track_groups(conn) to have
    already run (and committed) in this request -- generation_presence joins
    track_group and silently drops anything without a row there."""
    column = _tier_column(tier)
    spans = generation_spans(conn)
    presence = _presence_by_ordinal(conn, column)

    result = []
    for i, span in enumerate(spans):
        groups = presence.get(span["ordinal"], set())
        prev_groups = presence.get(spans[i - 1]["ordinal"], set()) if i > 0 else set()
        next_span = spans[i + 1] if i + 1 < len(spans) else None
        next_groups = presence.get(next_span["ordinal"], set()) if next_span else None

        carried_in = len(groups & prev_groups)
        result.append(
            {
                "ordinal": span["ordinal"],
                "playlist_id": span["playlist_id"],
                "name": span["name"],
                "started_at": span["started_at"],
                "ended_at": span["ended_at"],
                "group_count": len(groups),
                "carried_in": carried_in,
                "new_in": len(groups) - carried_in,
                "survived_out": len(groups & next_groups) if next_groups is not None else None,
            }
        )
    return result


def runs(ordinals):
    """Collapse a set of ordinals into (start, end) runs of consecutive
    integers, e.g. {5, 6, 7, 10} -> [(5, 7), (10, 10)]."""
    ordinals = sorted(ordinals)
    result = []
    start = prev = ordinals[0]
    for o in ordinals[1:]:
        if o == prev + 1:
            prev = o
        else:
            result.append((start, prev))
            start = prev = o
    result.append((start, prev))
    return result


def presence_for_tracks(conn, track_ids):
    """The sorted ordinals any of `track_ids` was present in, from
    generation_presence. Same ensure_track_groups precondition as
    generations()/tenures()."""
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    rows = conn.execute(
        f"SELECT DISTINCT ordinal FROM generation_presence WHERE track_id IN ({placeholders})",
        list(track_ids),
    ).fetchall()
    return sorted(row["ordinal"] for row in rows)


def tenures(conn, tier="version"):
    """One entry per group ever present in a generation, at the given tier.
    Same ensure_track_groups precondition as generations().

    Ties for longest run (more than one run of the same max length -- common,
    since a group present in just two non-consecutive generations is two
    length-1 runs) favour the earliest run: its span becomes `days`."""
    column = _tier_column(tier)
    spans = {span["ordinal"]: span for span in generation_spans(conn)}

    group_ordinals = defaultdict(set)
    for row in conn.execute(
        f"SELECT DISTINCT ordinal, {column} AS group_id FROM generation_presence"
    ):
        group_ordinals[row["group_id"]].add(row["ordinal"])

    now = jobs.now_iso()
    result = []
    for group_id, ordinals in group_ordinals.items():
        group_runs = runs(ordinals)
        lengths = [end - start + 1 for start, end in group_runs]
        tenure = max(lengths)
        start_ordinal, end_ordinal = group_runs[lengths.index(tenure)]

        start_at = spans[start_ordinal]["started_at"]
        end_at = spans[end_ordinal]["ended_at"] or now
        days = (datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).days

        result.append(
            {
                "group_id": group_id,
                "runs": group_runs,
                "tenure": tenure,
                "total_generations": sum(lengths),
                "run_count": len(group_runs),
                "first_ordinal": min(ordinals),
                "last_ordinal": max(ordinals),
                "days": days,
            }
        )
    return result


def pending_new_generation(conn):
    """The lowest-major vX.Y.Z playlist that looks like an undeclared new
    generation, or None. Only one is ever surfaced at a time; if several
    exist, the rest come up in turn as each is resolved."""
    existing = {row["ordinal"] for row in conn.execute("SELECT ordinal FROM generation")}
    candidates = []
    for row in conn.execute(
        "SELECT playlist_id, name FROM snapshot WHERE generation_declined = 0"
    ):
        match = _GENERATION_NAME_RE.match(row["name"])
        if not match:
            continue
        major = int(match.group(1))
        if major in existing:
            continue
        candidates.append({"ordinal": major, "playlist_id": row["playlist_id"], "name": row["name"]})
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["ordinal"])
    return candidates[0]


def confirm_generation(conn, playlist_id):
    """Inserts the generation, deriving its ordinal from the playlist's own
    current name rather than trusting a client-submitted value. Caller
    commits."""
    row = conn.execute(
        "SELECT name FROM snapshot WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no snapshot row for playlist {playlist_id}")
    match = _GENERATION_NAME_RE.match(row["name"])
    if not match:
        raise ValueError(f"{row['name']!r} does not match the vX.Y.Z generation naming scheme")
    conn.execute(
        "INSERT OR IGNORE INTO generation (ordinal, playlist_id) VALUES (?, ?)",
        (int(match.group(1)), playlist_id),
    )


def decline_generation(conn, playlist_id):
    """Stops pending_new_generation from asking about this playlist again.
    Caller commits."""
    conn.execute(
        "UPDATE snapshot SET generation_declined = 1 WHERE playlist_id = ?", (playlist_id,)
    )
