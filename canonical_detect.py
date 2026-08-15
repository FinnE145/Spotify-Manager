"""Detection for canonical track grouping (see
docs/canonical-tracks/detection.md). Proposes candidate groups and
pre-filled tier labels; decides nothing and writes nothing. Pure
computation over track/membership — no Spotify calls."""

import re
import unicodedata
from collections import defaultdict

import artists
import canonical
import scoring

_SUFFIX_DELIMITERS = (" (", " [", " - ", " – ", " — ", " /")

# Suffix classes: base (no suffix) - version - recording - neutral. Keywords
# are written in normalize_suffix() form (punctuation is a space, so it's
# "taylor s version", not "taylor's version") and matched as whole token
# sequences, never as bare substrings: "feat don toliver" contains "live",
# and substring matching classified all 16 of those as version.

# Sounds different from the original.
_VERSION_KEYWORDS = (
    "acoustic", "live", "remix", "demo", "instrumental", "cover", "nightcore",
    "piano", "orchestral", "stripped", "sped up", "slowed", "reprise",
    # session / venue
    "long pond studio sessions", "recorded at spotify studios", "unplugged",
    "voice memo", "the voice performance",
)

# Sounds the same as the original; a different master or edition.
_RECORDING_KEYWORDS = (
    "remaster", "remastered", "taylor s version", "mono", "stereo", "clean",
    "explicit", "radio edit", "single version", "album version", "deluxe",
    "anniversary", "extended",
)

# Deliberately undecided: recognised, but carrying no reliable signal about
# whether the audio differs. Both lists produce `neutral`, the same class an
# unrecognised suffix gets; they are split by *precedence*, which is the only
# thing that distinguishes them.

# Checked BEFORE the generic "... version" / "... mix" catch-alls, and that
# placement is the whole point: these are structural markers, so
# "(Bonus Track Version)" and "(Arr. Jazz Version)" must not be swept up as
# version.
#
#   arr -- an arrangement usually *is* a different performance, but this
#     library's classical entries are inconsistent enough to be worth deciding
#     by hand rather than by prefill.
#   interlude / skit / bonus track / edit -- markers, not audio descriptions.
#     Bare "- edit" is only 3 tracks and isn't worth a rule of its own;
#     "radio edit" stays recording via its own keyword above.
_NEUTRAL_STRUCTURAL_KEYWORDS = ("arr", "interlude", "skit", "bonus track", "edit")

# Checked AFTER them, which makes this list a no-op by construction -- the
# fallback is `neutral` anyway. It is written out as the record that these
# were considered, and the ordering is the decision: a credit says nothing
# about the audio, so it must not *veto* something that does. Without this
# split, "(feat. …) [Vocal Up Mix]" classified neutral on the strength of the
# "feat" alone.
_NEUTRAL_CREDIT_KEYWORDS = ("feat", "ft", "featuring", "with")

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


def normalize_suffix(s):
    """The comparison form for suffixes, used by classify_suffix, by
    _same_recording's suffix equality, and by the auto-group rule.

    Punctuation becomes a *space* rather than being deleted -- NFKD doesn't
    fold U+2019, so "(taylor's version)" and "(taylor’s version)" only agree
    once both collapse to "taylor s version" (which is why all 14 of the
    latter used to fall through unclassified).

    Digits are kept: "1947 version", "remastered 1999" and "99 luftballons"
    all need them.
    """
    folded = _strip_accents(s or "").casefold()
    spaced = "".join(c if (c.isalnum() or c.isspace()) else " " for c in folded)
    return re.sub(r"\s+", " ", spaced).strip()


def _has_keyword(normalized, keywords):
    """Whole-token-sequence match, not substring: " live " is in " demo live "
    but not in " feat don toliver "."""
    padded = f" {normalized} "
    return any(f" {kw} " in padded for kw in keywords)


def _normalize_base_string(s):
    return _strip_punct_collapse(_strip_accents(s or "").lower())


# Artist names normalize through the same pipeline as titles, but only to
# spot duplicate-id candidates in artists.py -- detection itself never
# matches on names.
normalize_name = _normalize_base_string


def classify_suffix(suffix):
    """base | version | recording | neutral.

    Order matters, and every step earns its place:

      1. version keywords -- "- Live (Remastered)" sounds different, whatever
         the master, so version outranks recording.
      2. recording keywords -- "radio edit" stays recording despite containing
         "edit", and "40th Anniversary Mono Mix" stays recording despite
         containing "mix".
      3. structural neutral markers -- keeps "(Bonus Track Version)" and
         "(Arr. Jazz Version)" out of the two catch-alls below.
      4/5. the generic "... version" and "... mix" catch-alls.
      6. credit keywords, then the neutral fallback.
    """
    if not suffix:
        return "base"
    normalized = normalize_suffix(suffix)
    if _has_keyword(normalized, _VERSION_KEYWORDS):
        return "version"
    if _has_keyword(normalized, _RECORDING_KEYWORDS):
        return "recording"
    if _has_keyword(normalized, _NEUTRAL_STRUCTURAL_KEYWORDS):
        return "neutral"
    # Generic "<something> version" -- jazz, guitar, original, 1947 -- and
    # "<something> mix" -- country, pop, uk, radio, ultimate. A different mix
    # is a different balance, which sounds different; the named recording-tier
    # cases (taylor s / single / album version, mono mix, remastered mix) were
    # all caught above.
    if _has_keyword(normalized, ("version", "mix")):
        return "version"
    if _has_keyword(normalized, _NEUTRAL_CREDIT_KEYWORDS):
        return "neutral"
    return "neutral"


# -- Track data ---------------------------------------------------------


def _fetch_tracks(conn):
    rows = conn.execute(
        """
        SELECT t.track_id, t.name, ta.artists, a.name AS album_name, a.image_url AS album_image_url,
               t.duration_ms, t.explicit, t.isrc,
               -- SUM, not COUNT(CASE ...): on a LEFT JOIN miss m.removed_at is
               -- NULL, so the CASE yielded 1 and COUNT counted it -- every
               -- track with no membership at all reported one phantom
               -- membership. Latent until step D tripled the library with
               -- 6,070 membership-less round-tripped tracks.
               SUM(CASE WHEN m.track_id IS NOT NULL AND m.removed_at IS NULL THEN 1 ELSE 0 END) AS live_count
        FROM track t
        LEFT JOIN album a ON a.album_id = t.album_id
        LEFT JOIN track_artists ta ON ta.track_id = t.track_id
        LEFT JOIN membership m ON m.track_id = t.track_id
        GROUP BY t.track_id
        """
    ).fetchall()
    artist_sets = artists.artist_sets(conn)

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

    # Tracks pinned as their song group's representative, so an item can show
    # the ★ that's already saved rather than looking unpinned.
    pinned_ids = {
        row["representative_track_id"]
        for row in conn.execute(
            "SELECT representative_track_id FROM canonical_group "
            "WHERE tier = 'song' AND representative_track_id IS NOT NULL"
        )
    }

    empty = {"artist_ids": set(), "primary_ids": set(), "featured_ids": set()}
    tracks = {}
    for row in rows:
        base, suffix = normalize_title(row["name"])
        credits = artist_sets.get(row["track_id"], empty)
        tracks[row["track_id"]] = {
            "name": row["name"],
            "artists": row["artists"] or "",
            "album_name": row["album_name"],
            "album_image_url": row["album_image_url"],
            "duration_ms": row["duration_ms"],
            "explicit": row["explicit"],
            "isrc": row["isrc"],
            "live_count": row["live_count"],
            "base": base,
            "suffix": suffix,
            "suffix_norm": normalize_suffix(suffix),
            "suffix_class": classify_suffix(suffix),
            # Alias-resolved artist ids. artist_ids drives candidate
            # generation (permissive, so nothing is missed); primary_ids
            # drives the song prefill, so a shared *featured* credit alone
            # never merges two songs silently.
            "artist_ids": credits["artist_ids"],
            "primary_ids": credits["primary_ids"],
            "featured_ids": credits["featured_ids"],
            "album_norm": _normalize_base_string(row["album_name"] or ""),
            "real_groups": real_groups.get(row["track_id"]),
            "pinned": row["track_id"] in pinned_ids,
        }

    # Each track's own materialized score (docs/specs/scoring-H.md §11.1),
    # for ranking candidate/cross-artist groups -- see _make_candidate_group.
    track_scores = scoring.scores_for_tier(conn, "track", list(tracks))
    for tid, info in tracks.items():
        info["score"] = track_scores.get(tid, {}).get("all_time", 0.0)
    return tracks


def _pinned_track_id(track_ids, tracks):
    return next((tid for tid in track_ids if tracks[tid]["pinned"]), None)


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


def _same_recording_identity(tracks, a, b):
    """Recording identity: **same ISRC + same duration + same explicit flag.**
    Any of the three differing means a different recording.

    Recording means the tracks *are* the same; version means they *sound* the
    same. A clean edit has words taken out of it, so it is a different
    recording that sounds near-identical -- and it needs no rule to land
    there, because both sides still share a version group through
    shares_base_version. Note this holds even when Spotify reports the *same*
    ISRC for the clean and explicit rows, which it does for 15 groups here:
    the differing flag wins.

    (A step-E predecessor did the opposite -- it merged differing-explicit
    pairs *into* one recording. That was the wrong tier.)
    """
    ra, rb = tracks[a], tracks[b]
    return bool(
        ra["isrc"]
        and rb["isrc"]
        and ra["isrc"] == rb["isrc"]
        and ra["duration_ms"] is not None
        and rb["duration_ms"] is not None
        and abs(ra["duration_ms"] - rb["duration_ms"]) <= _DURATION_TOLERANCE_MS
        and bool(ra["explicit"]) == bool(rb["explicit"])
    )


def _same_recording(tracks, a, b):
    """One recording across two releases -- the AAA-on-four-releases case, and
    the workhorse rule. Same-album pairs are _same_release's job, and reach
    recording tier through the nesting branch in same_recording_group."""
    return _same_recording_identity(tracks, a, b) and (
        tracks[a]["album_norm"] != tracks[b]["album_norm"]
    )


def _same_release(tracks, a, b):
    """Same recording, same album -- the duplicate-album-upload case."""
    return _same_recording_identity(tracks, a, b) and (
        tracks[a]["album_norm"] == tracks[b]["album_norm"]
    )


def _clean_explicit_pair(tracks, a, b):
    """A clean edit and its explicit original: same base title, shared artist,
    near-identical length, differing `explicit`.

    A **version**-tier rule and never a recording one. They sound
    near-identical but are not the same recording -- which is exactly the
    distinction between the two tiers, and exactly what the deleted
    recording-tier version of this rule got wrong.

    It has to be stated rather than left to shares_base_version, because
    `neutral` no longer shares the base version: without it the pair splits
    the moment either side carries a feat. clause, or the "(Explicit Ver.)"
    marker that announces the very thing being matched on.

    Matched on the base title, not the full one, for the same reason -- the
    suffixes are often what differ. A version-classified side vetoes it: an
    instrumental or acoustic cut genuinely sounds different, whatever its
    explicit flag says.
    """
    ra, rb = tracks[a], tracks[b]
    if "version" in (ra["suffix_class"], rb["suffix_class"]):
        return False
    return bool(
        ra["base"] == rb["base"]
        and (ra["artist_ids"] & rb["artist_ids"])
        and ra["duration_ms"] is not None
        and rb["duration_ms"] is not None
        and abs(ra["duration_ms"] - rb["duration_ms"]) <= _DURATION_TOLERANCE_MS
        and bool(ra["explicit"]) != bool(rb["explicit"])
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


def _prefill_labels(track_ids, tracks):
    labels = {tid: {} for tid in track_ids}
    counter = _Counter()

    def same_song(a, b):
        # Song tier merges by default: a shared *primary* artist is the whole
        # rule, whatever the suffix class. A title match alone (covers,
        # Christmas songs, coincidental titles) is still never enough, and
        # neither is a shared featured credit -- "Song by B" and "Song by A
        # feat. B" are surfaced together but pre-filled apart. Those two are
        # what keeps covers separate.
        #
        # The prefill therefore now guesses where it used to abstain: an
        # unrecognised suffix lands in the same song group and gets its finer
        # tiers decided by ISRC, duration and album, rather than sitting
        # singleton at all four. That's the intended trade -- an
        # obviously-related track sitting alone was the more annoying failure,
        # and it's what takes wrong song-tier splits from 131 to 51.
        if _same_real(tracks, a, b, "song"):
            return True
        return bool(tracks[a]["primary_ids"] & tracks[b]["primary_ids"])

    def shares_base_version(tid):
        # base- and recording-classified tracks sound like the original (a
        # remaster sounds the same), so they share one version.
        #
        # version-classified ones (acoustic, live, remix) each stand alone --
        # two different live cuts are two different-sounding things.
        #
        # neutral ones also stand alone, and that is the point: a neutral
        # suffix is the one we understand *least*, so assuming "sounds the
        # same" is a guess exactly where there is no evidence. A neutral track
        # still joins a version group when it earns it -- same_version_group
        # merges on recording identity below -- so "Lemonade" and "Lemonade
        # (feat. NAV)", sharing an ISRC and a duration, stay together, while
        # "Speechless (Full)" and "Speechless (Part 2)" (208s vs 144s) split.
        return tracks[tid]["suffix_class"] in ("base", "recording")

    def same_version_group(a, b):
        # An existing real match always wins, including across the
        # base/version-classified boundary: if these two were once decided to
        # be the same version, that decision is never silently proposed as
        # undone just because one of them carries a "(Live)"-style suffix.
        if _same_real(tracks, a, b, "version"):
            return True
        # Nesting: same recording (or release) implies same version. Without
        # this, two rows of the same "(Live)" track -- same ISRC, both
        # version-classified -- land in different version components, and
        # assign_recording_release runs *scoped inside* a version component,
        # so they could then never merge at recording either.
        if _same_recording(tracks, a, b) or _same_release(tracks, a, b):
            return True
        # Sounds the same without being the same: a clean edit of the
        # explicit original. Version only -- recording never merges these.
        if _clean_explicit_pair(tracks, a, b):
            return True
        return shares_base_version(a) and shares_base_version(b)

    def same_recording_group(a, b):
        # A release-tier match (same ISRC + same album) must also merge at
        # recording tier, since release <= recording nesting requires it --
        # even when the recording-specific rule (which only fires on a
        # *different* album) doesn't independently agree.
        if _same_real(tracks, a, b, "recording"):
            return True
        return _same_recording(tracks, a, b) or _same_release(tracks, a, b)

    def same_release_group(a, b):
        if _same_real(tracks, a, b, "release"):
            return True
        return _same_release(tracks, a, b)

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


def cross_component_pairs(conn, track_ids):
    """Every unordered pair from track_ids whose two tracks fall in
    *different* artist-overlap components -- the exact set of pairs
    _cross_component_reviewed checks, and so the exact set that needs
    writing to settle a cross-artist bucket (spec M §1.2). Nothing else:
    within-component pairs are the main queue's job.

    Shares _group_by_rule with _bucket_components rather than
    re-implementing the union-find -- if the two ever disagreed about what a
    component is, a bucket could fail to settle and resurface forever.

    Reads artists.artist_sets(conn) directly instead of going through
    _fetch_tracks (~350ms, whole-library): that's where _fetch_tracks gets
    artist_ids from anyway, so this is cheaper and exactly equivalent.
    """
    ids = sorted(set(track_ids))
    artist_sets = artists.artist_sets(conn)
    empty = {"artist_ids": set(), "primary_ids": set(), "featured_ids": set()}

    def artist_ids(tid):
        return artist_sets.get(tid, empty)["artist_ids"]

    components = _group_by_rule(ids, lambda a, b: bool(artist_ids(a) & artist_ids(b)))
    component_of = {tid: i for i, comp in enumerate(components) for tid in comp}

    return [
        _pair_key(ids[i], ids[j])
        for i in range(len(ids))
        for j in range(i + 1, len(ids))
        if component_of[ids[i]] != component_of[ids[j]]
    ]


# -- Candidate groups ---------------------------------------------------


def _make_candidate_group(base, track_ids, tracks, reviewed, cross_artist):
    ids_sorted = sorted(track_ids)
    return {
        "key": f"{base}:{','.join(ids_sorted)}",
        "base": base,
        "track_ids": ids_sorted,
        "tracks": {tid: _display_fields(tracks[tid]) for tid in ids_sorted},
        "labels": _prefill_labels(ids_sorted, tracks),
        "score": scoring.group_score([tracks[tid]["score"] for tid in ids_sorted]),
        "pinned_track_id": _pinned_track_id(ids_sorted, tracks),
        "reviewed": reviewed,
        "cross_artist": cross_artist,
    }


def _order(groups):
    """The one ordering key for every candidate-group listing -- main queue,
    cross-artist buckets, and the /dev/canonical listings both read
    (docs/specs/scoring-H.md §11.1: `impact` retired in favour of score)."""
    return sorted(groups, key=lambda g: (-g["score"], -len(g["track_ids"]), g["base"]))


def _bucket_components(tracks):
    """Every normalized-base bucket, each split into artist-overlap connected
    components. The shared front half of candidate generation."""
    buckets = defaultdict(list)
    for tid, rec in tracks.items():
        buckets[rec["base"]].append(tid)
    for base, ids in buckets.items():
        yield base, _group_by_rule(
            ids, lambda a, b: bool(tracks[a]["artist_ids"] & tracks[b]["artist_ids"])
        )


def _build_all_groups(conn):
    tracks = _fetch_tracks(conn)
    reviewed_pairs = _load_reviewed_pairs(conn)

    main_groups, cross_groups = [], []
    for base, components in _bucket_components(tracks):
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


def all_candidate_groups(conn):
    main, cross = _build_all_groups(conn)
    return _order(main + cross)


def filter_groups(groups, query):
    """Candidate groups whose base title, track title or artists match `query`.

    Matched with SQL LIKE's `%` wildcard rather than a plain substring so this
    filter behaves like the LIKE-backed Groups filter beside it on
    `/dev/canonical` -- in particular, searching `%` lists everything, which is
    how you get past that listing's unfiltered cap."""
    if not query:
        return groups
    rx = re.compile(".*".join(re.escape(part) for part in query.split("%")), re.IGNORECASE)
    out = []
    for g in groups:
        values = [g["base"]]
        for t in g["tracks"].values():
            values += [t["title"], t["artists"]]
        if any(v and rx.search(v) for v in values):
            out.append(g)
    return out


def canonical_page_groups(conn):
    """The three results `/dev/canonical`'s cross-artist pane needs -- the
    unreviewed main queue, the unreviewed cross-artist bucket count, and every
    candidate for the listing -- derived from a single _build_all_groups()
    instead of the three separate rebuilds calling them one at a time would
    cost. Served by /api/canonical/cross/listing, not by the page itself."""
    main, cross = _build_all_groups(conn)
    unreviewed_main = _order([g for g in main if not g["reviewed"]])
    unreviewed_cross = _order([g for g in cross if not g["reviewed"]])
    all_groups = _order(main + cross)
    return unreviewed_main, unreviewed_cross, all_groups


# -- Cross-artist queue (spec E §4) -------------------------------------
#
# 0 of 292 reviewed cross-artist pairs have ever been merged: the queue's hit
# rate over its whole history is zero, because it is mostly `Home` by six
# unrelated artists and `Winter Wonderland` by five crooners. It isn't deleted
# -- the rare true positive is real -- but it is shaped so the overwhelmingly
# common answer, "no, none of these are related", costs one keypress.


def _row(tracks, tid, **extra):
    return {"track_id": tid, **_display_fields(tracks[tid]), **extra}


def _make_cross_item(conn, base, bucket, tracks, reviewed_pairs, song_members):
    bucket_set = set(bucket)

    # Established = already reviewed against something else in *this* bucket.
    # The asymmetry is the point: when a newcomer arrives in a settled bucket
    # only the newcomer is new, and the tracks it creates fresh unreviewed
    # pairs against stay established and stay collapsed. A track that was
    # reviewed and left ungrouped is still established -- having decided it
    # doesn't belong, you don't want to be asked again; you only need it
    # visible in case a future newcomer belongs with it.
    established = {
        tid
        for tid in bucket
        if any(_pair_key(tid, other) in reviewed_pairs for other in bucket if other != tid)
    }
    newcomers = [tid for tid in bucket if tid not in established]

    song_ids = []
    for tid in sorted(established, key=lambda t: (-tracks[t]["live_count"], t)):
        real = tracks[tid]["real_groups"]
        if real and real["song"] not in song_ids:
            song_ids.append(real["song"])

    groups = []
    nested_ids = set()
    for song_id in song_ids:
        members = sorted(song_members.get(song_id, []))
        here = [tid for tid in members if tid in established and tid in bucket_set]
        here_primary = set()
        for tid in here:
            here_primary |= tracks[tid]["primary_ids"]

        # A newcomer sharing a primary artist with this group already forms an
        # unreviewed *main*-queue candidate with it, by construction. Asking
        # about it here would be doing the main queue's job twice, so it is
        # rendered nested and unassignable, labelled with its actual state.
        nested = []
        for tid in newcomers:
            if not (tracks[tid]["primary_ids"] & here_primary):
                continue
            decided = all(_pair_key(tid, other) in reviewed_pairs for other in here)
            nested.append(_row(tracks, tid, state="decided" if decided else "queued"))
            nested_ids.add(tid)

        rep_id = canonical.representative(conn, song_id)
        # Count and albums cover the *whole* song group, including members
        # outside this bucket -- that's what tells you what you'd be joining.
        groups.append(
            {
                "song_id": song_id,
                "representative": _row(tracks, rep_id) if rep_id in tracks else None,
                "track_count": len(members),
                "artists": sorted({tracks[t]["artists"] for t in members if tracks[t]["artists"]}),
                "albums": sorted({tracks[t]["album_name"] for t in members if tracks[t]["album_name"]}),
                "nested": nested,
            }
        )

    new_rows = [
        _row(tracks, tid)
        for tid in sorted(newcomers, key=lambda t: (-tracks[t]["live_count"], tracks[t]["name"], t))
        if tid not in nested_ids
    ]
    ids_sorted = sorted(bucket)
    return {
        "key": f"{base}:{','.join(ids_sorted)}",
        "base": base,
        "track_ids": ids_sorted,
        "new_tracks": new_rows,
        "groups": groups,
        "score": scoring.group_score([tracks[tid]["score"] for tid in ids_sorted]),
    }


def cross_bucket_for(conn, track_ids):
    """One bucket by track id, whatever its reviewed status -- what Backspace
    needs. After a save every pair in the bucket is reviewed, so every track
    is established and the item renders as pure "here is what you just
    decided": no new songs, one row per song group."""
    tracks = _fetch_tracks(conn)
    ids = [tid for tid in track_ids if tid in tracks]
    if len(ids) < 2:
        return None
    song_members = defaultdict(list)
    for tid, rec in tracks.items():
        if rec["real_groups"]:
            song_members[rec["real_groups"]["song"]].append(tid)
    return _make_cross_item(
        conn, tracks[ids[0]]["base"], ids, tracks, _load_reviewed_pairs(conn), song_members
    )


def cross_buckets(conn):
    """One item per unreviewed cross-artist bucket, split into new songs and
    existing groups. Bucket membership and ordering are unchanged from the old
    queue -- only the shape of the question is different."""
    tracks = _fetch_tracks(conn)
    reviewed_pairs = _load_reviewed_pairs(conn)

    song_members = defaultdict(list)
    for tid, rec in tracks.items():
        if rec["real_groups"]:
            song_members[rec["real_groups"]["song"]].append(tid)

    items = []
    for base, components in _bucket_components(tracks):
        if len(components) < 2 or _cross_component_reviewed(reviewed_pairs, components):
            continue
        bucket = [tid for comp in components for tid in comp]
        items.append(_make_cross_item(conn, base, bucket, tracks, reviewed_pairs, song_members))
    return _order(items)


# -- Pending tier review (spec E §4.5/§4.6) -----------------------------


def pending_song_ids(conn):
    """The distinct song groups awaiting a tier pass. Deduped at read time:
    two newcomers landing in the same group are two rows resolving to one
    item.

    A group that has fallen back to a single member is skipped, because there
    is nothing left to review across -- an auto-group undo restores
    `track_group` wholesale and can detach an assigned newcomer, and a manual
    ungroup in the viewer does the same. This filter is the *only* one:
    pending_tier_items() serves exactly what this returns, so the count on
    /dev/canonical and the queue behind it can never disagree.

    The pending row itself is deliberately left in place rather than deleted.
    It still records that the track owes a tier pass, so if a later merge puts
    it back into a multi-member group the item correctly comes back.
    """
    song_ids = []
    for row in conn.execute("SELECT track_id FROM pending_tier_review"):
        groups = canonical.groups_for_track(conn, row["track_id"])
        if not groups or groups["song"] in song_ids:
            continue
        if len(canonical.group_members(conn, groups["song"])) < 2:
            continue
        song_ids.append(groups["song"])
    return song_ids


def pending_tier_items(conn):
    """Each pending song group's *current* members, for
    /dev/canonical/review?queue=pending.

    A full candidate group, **not** ad_hoc_group: the whole point of the
    pending queue is to assign the finer tiers, so the prefill has to run and
    fill them in. ad_hoc_group pre-fills nothing and would render four
    singleton chips per row -- the saved state, which is exactly the thing
    being reviewed.

    The song tier comes out shared regardless, because _prefill_labels'
    same_song consults _same_real first and every member is already in this
    one song group. So the assignment stands and the prefill only proposes
    below it.
    """
    tracks = _fetch_tracks(conn)
    reviewed_pairs = _load_reviewed_pairs(conn)
    items = []
    for song_id in pending_song_ids(conn):
        members = [tid for tid in canonical.group_members(conn, song_id) if tid in tracks]
        # Not the singleton check -- pending_song_ids already made that one, and
        # owns it so the count and this queue agree. This only catches a
        # track_group row pointing at a track _fetch_tracks no longer returns.
        if len(members) < 2:
            continue
        base = tracks[sorted(members)[0]]["base"]
        items.append(
            _make_candidate_group(
                base,
                members,
                tracks,
                _all_reviewed(reviewed_pairs, members),
                cross_artist=False,
            )
        )
    return items


# -- Stale decisions ----------------------------------------------------


def stale_recording_groups(conn):
    """Saved recording groups holding at least one pair the current rules
    would no longer merge -- decisions made under an older rule set.

    Worth reporting because they don't just sit there: _same_real re-asserts
    each one every time its group surfaces, and union-find then chains other
    tracks in through it. "Snow On The Beach (feat. More Lana Del Rey)" is
    26 s shorter than the original and carries its own ISRC, but rode into one
    recording with it on the strength of a stale merge.

    Pure: reports, changes nothing. They only clear by being re-decided.
    """
    tracks = _fetch_tracks(conn)
    by_recording = defaultdict(list)
    for tid, rec in tracks.items():
        if rec["real_groups"]:
            by_recording[rec["real_groups"]["recording"]].append(tid)

    stale = []
    for recording_id, members in by_recording.items():
        if len(members) < 2:
            continue
        ids = sorted(members)
        if any(
            not _same_recording_identity(tracks, ids[i], ids[j])
            for i in range(len(ids))
            for j in range(i + 1, len(ids))
        ):
            stale.append({"recording_id": recording_id, "track_ids": ids})
    return sorted(stale, key=lambda g: g["recording_id"])


# -- Deterministic auto-group ------------------------------------------


def _auto_group_pair(tracks, a, b):
    """Full recording identity (ISRC + duration + explicit) **plus** an
    identical normalized full title.

    Scored against the reviewed baseline at 114/114. Loosening it to bare ISRC
    equality produces 7 recording-tier disagreements -- the guards are what
    buy the accuracy, so don't drop them. It also deliberately does *not*
    strip a trailing feat. clause: the rule asserts certainty and stays
    maximally strict, and feature-neutrality belongs in the prefill, which
    only suggests.

    The explicit guard comes via _same_recording_identity, and it matters here
    more than anywhere: a run writes **one shared recording** per group, so a
    group whose rows disagree on `explicit` must not close at all. It stays in
    the queue whole rather than having certainty asserted over a visible
    contradiction.
    """
    ra, rb = tracks[a], tracks[b]
    return bool(
        _same_recording_identity(tracks, a, b)
        and ra["base"] == rb["base"]
        and ra["suffix_norm"] == rb["suffix_norm"]
    )


def auto_group_candidates(conn):
    """The unreviewed main-queue candidate groups the auto-group rule closes,
    and the size of the queue they came from.

    A group closes only when the rule matches on *every* pair in it -- a
    3-track group where it fires on two pairs of three stays in the queue
    whole. Cross-artist candidates are never considered; none of them close
    under this rule anyway.

    Pure, like everything else here: this decides which groups qualify and
    hands back what a writer needs. canonical_autogroup.py does the writing.
    """
    tracks = _fetch_tracks(conn)
    reviewed_pairs = _load_reviewed_pairs(conn)

    closable = []
    queue_total = 0
    for base, components in _bucket_components(tracks):
        for comp in components:
            if len(comp) < 2 or _all_reviewed(reviewed_pairs, comp):
                continue
            queue_total += 1
            ids = sorted(comp)
            if all(
                _auto_group_pair(tracks, ids[i], ids[j])
                for i in range(len(ids))
                for j in range(i + 1, len(ids))
            ):
                closable.append(
                    {
                        "base": base,
                        "track_ids": ids,
                        # Release is keyed on the album name, so tracks on the
                        # same album share a release and tracks on different
                        # albums don't.
                        "album_norms": {tid: tracks[tid]["album_norm"] for tid in ids},
                    }
                )
    return closable, queue_total


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
        "score": scoring.group_score([tracks[tid]["score"] for tid in ids_sorted]),
        "pinned_track_id": _pinned_track_id(ids_sorted, tracks),
        "reviewed": None,
        "cross_artist": False,
    }
