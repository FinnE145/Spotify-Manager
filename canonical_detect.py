"""Detection for canonical track grouping (see
docs/canonical-tracks/detection.md). Proposes candidate groups and
pre-filled tier labels; decides nothing and writes nothing. Pure
computation over track/membership — no Spotify calls."""

import re
import unicodedata
from collections import defaultdict

import canonical

_SUFFIX_DELIMITERS = (" (", " [", " - ", " – ", " — ", " /")

_UNDECIDED_KEYWORDS = ("instrumental",)
_VERSION_KEYWORDS = (
    "acoustic", "live", "remix", "demo", "sped up", "slowed", "nightcore",
    "piano", "orchestral", "reprise", "stripped",
)
_RECORDING_KEYWORDS = (
    "remaster", "remastered", "taylor's version", "deluxe", "anniversary",
    "mono", "stereo", "clean", "explicit", "radio edit", "single version",
    "album version", "extended",
)

_DURATION_TOLERANCE_MS = 2000


# -- Normalization ----------------------------------------------------


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _strip_punct_collapse(s):
    s = "".join(c for c in s if c.isalnum() or c.isspace())
    return re.sub(r"\s+", " ", s).strip()


def _split_suffix(s):
    best_idx = None
    for delim in _SUFFIX_DELIMITERS:
        idx = s.find(delim)
        if idx != -1 and (best_idx is None or idx < best_idx):
            best_idx = idx
    if best_idx is None:
        return s, ""
    return s[:best_idx], s[best_idx + 1 :]


def normalize_title(title):
    folded = _strip_accents(title or "").lower()
    base_raw, suffix_raw = _split_suffix(folded)
    return _strip_punct_collapse(base_raw), suffix_raw.strip()


def _normalize_base_string(s):
    return _strip_punct_collapse(_strip_accents(s or "").lower())


def normalize_artists(artists_field):
    if not artists_field:
        return set()
    return {_normalize_base_string(a) for a in artists_field.split(", ") if a.strip()}


def classify_suffix(suffix):
    if not suffix:
        return "base"
    folded = suffix.casefold()
    if any(kw in folded for kw in _UNDECIDED_KEYWORDS):
        return "undecided"
    if any(kw in folded for kw in _VERSION_KEYWORDS):
        return "version"
    if any(kw in folded for kw in _RECORDING_KEYWORDS):
        return "recording"
    return "unknown"


# -- Track data ---------------------------------------------------------


def _fetch_tracks(conn):
    rows = conn.execute(
        """
        SELECT t.track_id, t.name, t.artists, t.album_name, t.album_image_url,
               t.duration_ms, t.explicit, t.isrc,
               COUNT(CASE WHEN m.removed_at IS NULL THEN 1 END) AS live_count
        FROM track t
        LEFT JOIN membership m ON m.track_id = t.track_id
        GROUP BY t.track_id
        """
    ).fetchall()

    real_groups = {
        row["track_id"]: {
            "song": row["song_id"],
            "version": row["version_id"],
            "recording": row["recording_id"],
            "release": row["release_id"],
        }
        for row in conn.execute(
            "SELECT track_id, song_id, version_id, recording_id, release_id FROM track_group"
        )
    }

    tracks = {}
    for row in rows:
        base, suffix = normalize_title(row["name"])
        tracks[row["track_id"]] = {
            "name": row["name"],
            "artists": row["artists"],
            "album_name": row["album_name"],
            "album_image_url": row["album_image_url"],
            "duration_ms": row["duration_ms"],
            "explicit": row["explicit"],
            "isrc": row["isrc"],
            "live_count": row["live_count"],
            "base": base,
            "suffix": suffix,
            "suffix_class": classify_suffix(suffix),
            "artist_set": normalize_artists(row["artists"]),
            "album_norm": _normalize_base_string(row["album_name"] or ""),
            "real_groups": real_groups.get(row["track_id"]),
        }
    return tracks


def _display_fields(rec):
    return {
        "title": rec["name"],
        "artists": rec["artists"],
        "album": rec["album_name"],
        "album_image_url": rec["album_image_url"],
        "duration_ms": rec["duration_ms"],
        "explicit": rec["explicit"],
        "isrc": rec["isrc"],
        "live_count": rec["live_count"],
        "suffix_class": rec["suffix_class"],
        "suffix": rec["suffix"],
    }


# -- Grouping by an arbitrary "same" predicate (union-find) -----------------


def _group_by_rule(ids, same_fn):
    parent = {tid: tid for tid in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if same_fn(ids[i], ids[j]):
                union(ids[i], ids[j])

    groups = defaultdict(list)
    for tid in ids:
        groups[find(tid)].append(tid)
    return list(groups.values())


def _same_recording(tracks, a, b):
    ra, rb = tracks[a], tracks[b]
    if ra["isrc"] and rb["isrc"] and ra["isrc"] == rb["isrc"] and ra["album_norm"] != rb["album_norm"]:
        return True
    if (
        ra["base"] == rb["base"]
        and ra["suffix"] == rb["suffix"]
        and (ra["artist_set"] & rb["artist_set"])
        and ra["duration_ms"] is not None
        and rb["duration_ms"] is not None
        and abs(ra["duration_ms"] - rb["duration_ms"]) <= _DURATION_TOLERANCE_MS
        and bool(ra["explicit"]) != bool(rb["explicit"])
    ):
        return True
    return False


def _same_release(tracks, a, b):
    ra, rb = tracks[a], tracks[b]
    return bool(
        ra["isrc"]
        and rb["isrc"]
        and ra["isrc"] == rb["isrc"]
        and ra["album_norm"] == rb["album_norm"]
        and ra["duration_ms"] is not None
        and rb["duration_ms"] is not None
        and abs(ra["duration_ms"] - rb["duration_ms"]) <= _DURATION_TOLERANCE_MS
    )


def _same_real(tracks, a, b, tier):
    """Already sharing this tier for real, per the last-saved grouping --
    an existing decision always outranks the heuristics below."""
    ra, rb = tracks[a]["real_groups"], tracks[b]["real_groups"]
    return bool(ra and rb and ra[tier] == rb[tier])


# -- Pre-fill -----------------------------------------------------------


class _Counter:
    def __init__(self):
        self.n = 0

    def label(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"


def _eligible(tracks, tid):
    return tracks[tid]["suffix_class"] in ("base", "version", "recording")


def _prefill_labels(track_ids, tracks):
    labels = {tid: {} for tid in track_ids}
    counter = _Counter()

    def same_song(a, b):
        # Same song requires some artist overlap -- a title match alone
        # (covers, Christmas songs, coincidental titles) is never enough.
        # An undecided/unknown-suffixed track (a or b) never merges by
        # heuristic at all -- guessing there is worse than a click -- but
        # an existing real match always wins regardless of eligibility or
        # artist, since it's a fact, not a guess.
        if _same_real(tracks, a, b, "song"):
            return True
        return _eligible(tracks, a) and _eligible(tracks, b) and bool(tracks[a]["artist_set"] & tracks[b]["artist_set"])

    def shares_base_version(tid):
        # base- and recording-classified tracks all sound like the original
        # (a remaster sounds the same), so they share one version.
        # version-classified ones (acoustic, live, remix) each stand alone --
        # two different live cuts are two different-sounding things -- and
        # undecided/unknown ones never merge by heuristic at all.
        return _eligible(tracks, tid) and tracks[tid]["suffix_class"] != "version"

    def same_version_group(a, b):
        # An existing real match always wins, including across the
        # base/version-classified boundary: if these two were once decided to
        # be the same version, that decision is never silently proposed as
        # undone just because one of them carries a "(Live)"-style suffix.
        if _same_real(tracks, a, b, "version"):
            return True
        return shares_base_version(a) and shares_base_version(b)

    def same_recording_group(a, b):
        # A release-tier match (same ISRC + same album) must also merge at
        # recording tier, since release <= recording nesting requires it --
        # even when the recording-specific rule (which only fires on a
        # *different* album) doesn't independently agree.
        if _same_real(tracks, a, b, "recording"):
            return True
        return _eligible(tracks, a) and _eligible(tracks, b) and (
            _same_recording(tracks, a, b) or _same_release(tracks, a, b)
        )

    def same_release_group(a, b):
        if _same_real(tracks, a, b, "release"):
            return True
        return _eligible(tracks, a) and _eligible(tracks, b) and _same_release(tracks, a, b)

    def assign_recording_release(members):
        for comp in _group_by_rule(members, same_recording_group):
            recording_label = counter.label("recording")
            for tid in comp:
                labels[tid]["recording"] = recording_label
            for rel_comp in _group_by_rule(comp, same_release_group):
                release_label = counter.label("release")
                for tid in rel_comp:
                    labels[tid]["release"] = release_label

    for song_component in _group_by_rule(track_ids, same_song):
        song_label = counter.label("song")
        for tid in song_component:
            labels[tid]["song"] = song_label

        for comp in _group_by_rule(song_component, same_version_group):
            version_label = counter.label("version")
            for tid in comp:
                labels[tid]["version"] = version_label
            assign_recording_release(comp)

    return labels


# -- Reviewed status ---------------------------------------------------


def _load_reviewed_pairs(conn):
    return {
        (row["track_id_a"], row["track_id_b"])
        for row in conn.execute("SELECT track_id_a, track_id_b FROM reviewed_pair")
    }


def _pair_key(a, b):
    return (a, b) if a < b else (b, a)


def _all_reviewed(reviewed_pairs, track_ids):
    ids = sorted(track_ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if _pair_key(ids[i], ids[j]) not in reviewed_pairs:
                return False
    return True


def _cross_component_reviewed(reviewed_pairs, components):
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            for a in components[i]:
                for b in components[j]:
                    if _pair_key(a, b) not in reviewed_pairs:
                        return False
    return True


# -- Candidate groups ---------------------------------------------------


def _make_candidate_group(base, track_ids, tracks, reviewed, cross_artist):
    ids_sorted = sorted(track_ids)
    return {
        "key": f"{base}:{','.join(ids_sorted)}",
        "base": base,
        "track_ids": ids_sorted,
        "tracks": {tid: _display_fields(tracks[tid]) for tid in ids_sorted},
        "labels": _prefill_labels(ids_sorted, tracks),
        "impact": sum(tracks[tid]["live_count"] for tid in ids_sorted),
        "reviewed": reviewed,
        "cross_artist": cross_artist,
    }


def _order(groups):
    return sorted(groups, key=lambda g: (-g["impact"], -len(g["track_ids"]), g["base"]))


def _build_all_groups(conn):
    tracks = _fetch_tracks(conn)
    reviewed_pairs = _load_reviewed_pairs(conn)

    buckets = defaultdict(list)
    for tid, rec in tracks.items():
        buckets[rec["base"]].append(tid)

    main_groups, cross_groups = [], []
    for base, ids in buckets.items():
        components = _group_by_rule(ids, lambda a, b: bool(tracks[a]["artist_set"] & tracks[b]["artist_set"]))
        for comp in components:
            if len(comp) >= 2:
                reviewed = _all_reviewed(reviewed_pairs, comp)
                main_groups.append(_make_candidate_group(base, comp, tracks, reviewed, cross_artist=False))
        if len(components) >= 2:
            whole = [tid for comp in components for tid in comp]
            reviewed = _cross_component_reviewed(reviewed_pairs, components)
            cross_groups.append(_make_candidate_group(base, whole, tracks, reviewed, cross_artist=True))

    return main_groups, cross_groups


def candidate_groups(conn):
    main, _cross = _build_all_groups(conn)
    return _order([g for g in main if not g["reviewed"]])


def cross_artist_groups(conn):
    _main, cross = _build_all_groups(conn)
    return _order([g for g in cross if not g["reviewed"]])


def all_candidate_groups(conn):
    main, cross = _build_all_groups(conn)
    return _order(main + cross)


def ad_hoc_group(conn, track_ids):
    """Skips detection: pre-fills nothing, renders the tracks' current
    saved grouping instead."""
    tracks = _fetch_tracks(conn)
    ids_sorted = sorted(track_ids)
    labels = {}
    for tid in ids_sorted:
        current = canonical.groups_for_track(conn, tid)
        if current is None:
            # No track_group row yet. ensure_track_groups() runs ahead of every
            # /dev/canonical* request so this shouldn't happen -- but fall back
            # to labels unique to this track rather than one shared sentinel,
            # which would silently merge every ungrouped track in the item.
            labels[tid] = {tier: f"ungrouped:{tid}:{tier}" for tier in canonical.TIER_ORDER}
        else:
            labels[tid] = {tier: str(current[tier]) for tier in canonical.TIER_ORDER}
    base = tracks[ids_sorted[0]]["base"] if ids_sorted else ""
    return {
        "key": f"{base}:{','.join(ids_sorted)}",
        "base": base,
        "track_ids": ids_sorted,
        "tracks": {tid: _display_fields(tracks[tid]) for tid in ids_sorted},
        "labels": labels,
        "impact": sum(tracks[tid]["live_count"] for tid in ids_sorted),
        "reviewed": None,
        "cross_artist": False,
    }
