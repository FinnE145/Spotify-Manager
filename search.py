"""Search (docs/specs/better-search-L.md, docs/specs/better-search-L2.md):
one matcher, used by three surfaces -- the /search page, the navbar dropdown
(/api/search) and each type section's See more (/api/search/more). There is
no separate "dropdown ranking" and "page ranking"; every surface slices the
same ranked lists `rank()` builds.

Two-stage fuzzy matcher: a trigram prefilter (L §4.3) narrows ~18,461
distinct normalized names down to a handful of candidates, then only those
are scored with a token-aware SequenceMatcher blend, gated at FUZZY_FLOOR
(L2 §4.1) -- a raw SequenceMatcher ratio has a high noise floor on short
strings ("test" vs "best" scores 0.750), so below the gate it counts as no
match at all. A name yields one relevance value per query token (L2 §4.2),
and an entity whose own name contributes nothing to any query token is
dropped outright regardless of what its associated names score (L2 §4.3) --
what makes an artist-only match on an unrelated song title not a result,
however high the artist's own score. Albums additionally dedupe on name +
artists + overlapping release groups after ranking (L2 §5).

Read-only w.r.t. Spotify and w.r.t. the database -- nothing here writes
anything, unlike `/search`'s own route, which still owns the
`ensure_track_groups()` + commit pairing (L §5: "a GET returning a listing
has no business taking a write lock")."""

import sqlite3
import threading
from collections import defaultdict
from difflib import SequenceMatcher

import canonical
import normalize
import scoring
from config import DB_PATH

# ============================================================================
# PARAMETERS -- docs/specs/better-search-L.md §4.6, docs/specs/
# better-search-L2.md §4.4. Tuned in planning against the real DB (both
# specs' measured figures); THESE ARE NOT LIVE DIALS. Not in config.py and
# not environment-tunable, for scoring.py's reason (H §10): two environments
# ranking under two different algorithms is worse than one algorithm
# everywhere.
# ============================================================================

MIN_QUERY_LEN = 2      # shorter (after normalizing) yields no results at all
TRIGRAM_FLOOR = 0.5    # stage-1 candidate admission, L §4.3
FUZZY_FLOOR = 0.85     # below this a SequenceMatcher ratio is 0.0, L2 §4.1
RELEVANCE_FLOOR = 0.5  # below this an entity is dropped outright, L2 §4.2 (re-measured)
ASSOC_WEIGHT = 0.5     # what an associated name's evidence is worth against `own`, L2 §4.2 -- replaces L's BUMP
ALPHA = 3.0            # match quality vs score, L §4.5 (re-measured against L2's formula)
SCORE_FLOOR = 10.0     # bottom of display space, for unscored entities, §4.5
COMBINED_LIMIT = 20    # rows in Most Relevant / the dropdown's own cap, §4.6
SECTION_LIMIT = 10     # rows rendered per type on page load
SECTION_MAX = 200      # rows a See more fetch may return
DROPDOWN_LIMIT = 5     # rows in the navbar dropdown

TYPES = ("songs", "albums", "artists", "playlists")

_TYPE_LABELS = {"songs": "Song", "albums": "Album", "artists": "Artist", "playlists": "Playlist"}


# ---------------------------------------------------------------- the name index & its cache (§4.2)

_cache_lock = threading.Lock()
_checker_conn = None
_cached_version = None
_cache = None


def _checker():
    """The dedicated connection the cache's staleness check reads
    PRAGMA data_version through -- scoring._checker's exact shape, and for
    the same reason: the pragma is only meaningful relative to the
    connection that reads it, so a per-request connection would compare
    unrelated numbers, and this connection must never be the one that
    writes (it never does -- this module is read-only)."""
    global _checker_conn
    if _checker_conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _checker_conn = conn
    return _checker_conn


def _trigrams(s):
    """3-grams of `f"  {s} "` (§4.3) -- two leading spaces, one trailing, so
    word boundaries participate in the coverage test."""
    padded = f"  {s} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _build_index(conn):
    """{"names": {normalized: trigram_set}, "track"/"album"/"artist"/
    "playlist": [(id, own_normalized, [assoc_normalized, ...]), ...]} --
    §4.2's whole scoring universe, built once and cached until the database
    changes underneath it.

    A track's associated names are its album's and each credited artist's;
    an album's are its credited artists'; artists and playlists have none."""
    track_rows = conn.execute("SELECT track_id, name, album_id FROM track").fetchall()
    album_rows = conn.execute("SELECT album_id, name FROM album").fetchall()
    artist_rows = conn.execute("SELECT artist_id, name FROM artist").fetchall()
    playlist_rows = conn.execute("SELECT playlist_id, name FROM snapshot").fetchall()

    album_names = {r["album_id"]: r["name"] for r in album_rows}
    artist_names = {r["artist_id"]: r["name"] for r in artist_rows}

    track_artists = defaultdict(list)
    for row in conn.execute("SELECT track_id, artist_id FROM track_artist ORDER BY track_id, position"):
        track_artists[row["track_id"]].append(row["artist_id"])

    album_artists = defaultdict(list)
    for row in conn.execute("SELECT album_id, artist_id FROM album_artist ORDER BY album_id, position"):
        album_artists[row["album_id"]].append(row["artist_id"])

    names = {}

    def norm(raw):
        n = normalize.base_string(raw)
        if n and n not in names:
            names[n] = _trigrams(n)
        return n

    tracks = []
    for row in track_rows:
        own = norm(row["name"] or "")
        assoc = []
        album_name = album_names.get(row["album_id"])
        if album_name:
            assoc.append(norm(album_name))
        for artist_id in track_artists.get(row["track_id"], ()):
            artist_name = artist_names.get(artist_id)
            if artist_name:
                assoc.append(norm(artist_name))
        tracks.append((row["track_id"], own, assoc))

    albums = []
    for row in album_rows:
        own = norm(row["name"] or "")
        assoc = [
            norm(artist_names[artist_id])
            for artist_id in album_artists.get(row["album_id"], ())
            if artist_id in artist_names
        ]
        albums.append((row["album_id"], own, assoc))

    artists = [(row["artist_id"], norm(row["name"] or ""), []) for row in artist_rows]
    playlists = [(row["playlist_id"], norm(row["name"] or ""), []) for row in playlist_rows]

    return {"names": names, "songs": tracks, "albums": albums, "artists": artists, "playlists": playlists}


def _get_index(conn):
    """The cached index, rebuilt only when `PRAGMA data_version` has moved
    since the last build (§4.2) -- fixed at 108ms per request uncached
    (measured §10.1), against 6-44ms of actual query work, for data that
    changes perhaps twice a day."""
    global _cache, _cached_version
    with _cache_lock:
        checker = _checker()
        version = checker.execute("PRAGMA data_version").fetchone()[0]
        if _cache is None or version != _cached_version:
            _cache = _build_index(conn)
            _cached_version = version
        return _cache


# ---------------------------------------------------------------- the matcher (§4.3, §4.4)


def _stage1_candidates(query_tokens, names_index):
    """Names admitted for stage-2 scoring: any name covering at least
    TRIGRAM_FLOOR of *some* query token's own trigrams (§4.3) -- coverage of
    the token, not Jaccard, so a short token isn't penalized for appearing
    inside a long name."""
    token_trigrams = [_trigrams(qt) for qt in query_tokens]
    candidates = set()
    for name, name_tri in names_index.items():
        for qt_tri in token_trigrams:
            if qt_tri and len(qt_tri & name_tri) / len(qt_tri) >= TRIGRAM_FLOOR:
                candidates.add(name)
                break
    return candidates


def _tsim(qt, nt):
    """Per-token similarity (L2 §4.1): exact and prefix fast paths keep most
    pairs away from difflib and are not gated -- the prefix branch is what
    makes an as-you-type query behave sensibly and it is not a fuzzy match.
    The fallback is difflib's ratio, gated: below FUZZY_FLOOR it is 0.0.
    A flat threshold, not a curve -- measured, one character substituted in
    a 5-letter word scores 0.800 regardless of which word, so no
    length-aware rule separates a short-word typo from a different short
    word (L2 §3)."""
    if qt == nt:
        return 1.0
    if nt.startswith(qt):
        return 0.85 + 0.15 * len(qt) / len(nt)
    r = SequenceMatcher(None, qt, nt).ratio()
    return r if r >= FUZZY_FLOOR else 0.0


def _whole_string(query_norm, name):
    """The whole-string reading, gated by the same FUZZY_FLOOR as `_tsim`'s
    fallback (L2 §4.1) -- gating only `_tsim` leaves the same noise in a
    second way: "beyonce" against "beyond" scores 0.769 both per-token and
    whole-string."""
    r = SequenceMatcher(None, query_norm, name).ratio()
    return r if r >= FUZZY_FLOOR else 0.0


def _name_token_scores(query_norm, query_tokens, name):
    """One similarity value per query token, not one score for the whole
    name (L2 §4.2): the better of the best per-word `_tsim` against `name`
    and the whole-string reading. Folding `whole` into every token is what
    carries a match across a token boundary the query doesn't share --
    "bohemianrhapsody" (no space) is one query token whose per-token reading
    against "bohemian rhapsody" is gated to 0, while `whole` = 0.970 carries
    it."""
    name_tokens = name.split()
    whole = _whole_string(query_norm, name)
    if not name_tokens:
        return tuple(whole for _ in query_tokens)
    return tuple(max(max(_tsim(qt, nt) for nt in name_tokens), whole) for qt in query_tokens)


def _score_names(query_norm, query_tokens, names_index):
    """{normalized_name: tuple_of_per_query_token_scores} for every stage-1
    candidate only -- scored once and shared by every entity bearing that
    name (§4.2)."""
    candidates = _stage1_candidates(query_tokens, names_index)
    return {name: _name_token_scores(query_norm, query_tokens, name) for name in candidates}


def _relevance(name_scores, query_tokens, own, assoc):
    """Mean over query tokens of max(own_i, ASSOC_WEIGHT * assoc_i) (L2
    §4.2) -- a coverage measure: an unmatched query token scores 0 rather
    than a difflib coincidence, so a query can no longer be dragged down by
    a token that matches nothing, and it can no longer be rescued by one
    either.

    L2 §4.3: an entity whose own name contributes nothing to *any* query
    token is dropped -- returns None -- before relevance is computed and
    regardless of what its associated names score. Stated as a rule rather
    than left to a threshold coincidence (own=0 forcing relevance below
    RELEVANCE_FLOOR only by chance), so ASSOC_WEIGHT stays free to be tuned
    without silently breaking the guarantee."""
    own_scores = name_scores.get(own)
    if own_scores is None or not any(own_scores):
        return None
    assoc_scores = [name_scores[a] for a in assoc if a in name_scores]
    total = sum(
        max(own_scores[i], ASSOC_WEIGHT * max((s[i] for s in assoc_scores), default=0.0))
        for i in range(len(query_tokens))
    )
    return total / len(query_tokens)


def _candidates(name_scores, query_tokens, items):
    """`items` is (id, own, assoc) triples from the index. Returns
    (id, relevance, own, assoc) for every entity clearing L2 §4.3's
    own-must-contribute rule and RELEVANCE_FLOOR -- the filter every
    `_rank_*` function applies before its own type-specific work."""
    out = []
    for id_, own, assoc in items:
        rel = _relevance(name_scores, query_tokens, own, assoc)
        if rel is not None and rel >= RELEVANCE_FLOOR:
            out.append((id_, rel, own, assoc))
    return out


# ---------------------------------------------------------------- ranking, per type (§4.5, §4.7)


def _rank_key(score, relevance):
    return max(score or 0.0, SCORE_FLOOR) * (relevance**ALPHA)


def _rank_songs(conn, name_scores, index, query_tokens):
    """Candidates are individual tracks (own = the track's own title, per
    L §2's replaced rule), but the ranked entity is the **version group**
    (§4.7): the version-group lookup is one batched query over the
    candidate track ids, and songs dedupe to one row per version group,
    keeping the highest-relevance member (ties broken by its own track-tier
    score)."""
    candidates = [
        (tid, rel) for tid, rel, _, _ in _candidates(name_scores, query_tokens, index["songs"])
    ]
    if not candidates:
        return []

    track_ids = [tid for tid, _ in candidates]
    placeholders = ",".join("?" for _ in track_ids)
    version_by_track = {
        row["track_id"]: row["version_id"]
        for row in conn.execute(
            f"SELECT track_id, version_id FROM track_group WHERE track_id IN ({placeholders})",
            track_ids,
        )
    }
    track_score_map = scoring.scores_for_tier(conn, "track", track_ids)

    best = {}
    for track_id, relevance in candidates:
        vid = version_by_track.get(track_id)
        if vid is None:
            continue
        track_score = track_score_map.get(track_id, {}).get("all_time", 0.0)
        key = (relevance, track_score)
        if vid not in best or key > best[vid][0]:
            best[vid] = (key, track_id)

    version_ids = list(best)
    version_score_map = scoring.scores_for_tier(conn, "version", version_ids)

    ranked = []
    for vid, ((relevance, _), track_id) in best.items():
        score = version_score_map.get(vid, {}).get("all_time", 0.0)
        ranked.append(
            {
                "type": "songs",
                "id": vid,
                "track_id": track_id,
                "relevance": relevance,
                "score": score,
                "rank_key": _rank_key(score, relevance),
            }
        )
    ranked.sort(key=lambda r: -r["rank_key"])
    return ranked


def _rank_albums(conn, name_scores, index, query_tokens):
    """Candidates score exactly like every other type; after ranking, §5's
    dedupe collapses rows that are almost certainly the same release under
    two different Spotify album ids."""
    items = _candidates(name_scores, query_tokens, index["albums"])
    if not items:
        return []
    score_map = scoring.album_scores(conn, [aid for aid, *_ in items])
    ranked = sorted(
        (
            (
                {
                    "type": "albums",
                    "id": aid,
                    "relevance": rel,
                    "score": (score := score_map.get(aid, {}).get("all_time", 0.0)),
                    "rank_key": _rank_key(score, rel),
                },
                own,
                tuple(assoc),
            )
            for aid, rel, own, assoc in items
        ),
        key=lambda t: -t[0]["rank_key"],
    )
    return _dedupe_albums(conn, ranked)


def _dedupe_albums(conn, ranked):
    """§5: two album rows collapse into one when their normalized names and
    normalized credited-artist lists are equal *and* their owned tracks'
    release-group ids intersect -- display-only, `canonical_group` /
    `track_group` are only ever read. `ranked` is (row, own, assoc) triples
    already sorted by `rank_key` descending, so a collision always attaches
    to the first (highest-ranked) already-kept row it matches, and the kept
    row is therefore always the best-ranked of its group.

    The release-id lookup is one query over the *candidate* album ids
    (L §4.7: work proportional to what is ranked, not to the library)."""
    ids = [row["id"] for row, _, _ in ranked]
    placeholders = ",".join("?" for _ in ids)
    release_sets = defaultdict(set)
    for r in conn.execute(
        f"""SELECT t.album_id AS album_id, tg.release_id AS release_id
            FROM track t
            JOIN track_group tg ON tg.track_id = t.track_id
            WHERE t.album_id IN ({placeholders})""",
        ids,
    ):
        release_sets[r["album_id"]].add(r["release_id"])

    kept = []  # (own, assoc, releases) for each row already in `result`
    result = []
    for row, own, assoc in ranked:
        releases = release_sets.get(row["id"], set())
        if any(
            k_own == own and k_assoc == assoc and k_releases & releases
            for k_own, k_assoc, k_releases in kept
        ):
            continue
        kept.append((own, assoc, releases))
        result.append(row)
    return result


def _rank_artists(conn, name_scores, index, query_tokens):
    """Every raw artist row is its own candidate (a duplicate Spotify id can
    carry its own spelling), then deduped onto its **resolved** id --
    `artists.py`'s convention, kept here since K's original search already
    resolved through `artist_alias` and L §2 does not name this as replaced."""
    candidates = [
        (aid, rel) for aid, rel, _, _ in _candidates(name_scores, query_tokens, index["artists"])
    ]
    if not candidates:
        return []

    alias_map = {
        row["artist_id"]: row["canonical_artist_id"]
        for row in conn.execute("SELECT artist_id, canonical_artist_id FROM artist_alias")
    }

    best = {}
    for artist_id, relevance in candidates:
        resolved = alias_map.get(artist_id, artist_id)
        if resolved not in best or relevance > best[resolved]:
            best[resolved] = relevance

    resolved_ids = list(best)
    score_map = scoring.artist_scores(conn, resolved_ids)
    ranked = [
        {
            "type": "artists",
            "id": aid,
            "relevance": rel,
            "score": (score := score_map.get(aid, {}).get("all_time", 0.0)),
            "rank_key": _rank_key(score, rel),
        }
        for aid, rel in best.items()
    ]
    ranked.sort(key=lambda r: -r["rank_key"])
    return ranked


def _rank_playlists(conn, name_scores, index, query_tokens):
    candidates = [
        (pid, rel) for pid, rel, _, _ in _candidates(name_scores, query_tokens, index["playlists"])
    ]
    if not candidates:
        return []
    score_map = scoring.playlist_scores(conn, [pid for pid, _ in candidates])
    ranked = [
        {
            "type": "playlists",
            "id": pid,
            "relevance": rel,
            "score": (score := score_map.get(pid, {}).get("all_time", 0.0)),
            "rank_key": _rank_key(score, rel),
        }
        for pid, rel in candidates
    ]
    ranked.sort(key=lambda r: -r["rank_key"])
    return ranked


_RANKERS = {
    "songs": _rank_songs,
    "albums": _rank_albums,
    "artists": _rank_artists,
    "playlists": _rank_playlists,
}


def is_searchable(q):
    """Whether `q` can match anything at all -- MIN_QUERY_LEN measured after
    normalizing (§4.1), which is why this is not `len(q) >= 2`: "%" and "a "
    are both empty once base_string is done with them.

    Public because the /search route asks it *before* taking
    ensure_track_groups' write lock: a query that is going to return nothing
    has no business taking one, and re-deriving the rule at the route would
    be two places encoding one threshold."""
    return len(normalize.base_string(q)) >= MIN_QUERY_LEN


def rank(conn, q):
    """The full ranked lists for all four types, unclipped, plus their
    mixed-type "combined" merge -- what every one of the three routes slices
    from. §4.7: ranking must see every candidate before any cap is applied,
    so nothing here is capped; only the hydration step below touches
    anything past what will actually render."""
    if not is_searchable(q):
        return {"songs": [], "albums": [], "artists": [], "playlists": [], "combined": []}

    query_norm = normalize.base_string(q)
    query_tokens = query_norm.split()
    index = _get_index(conn)
    name_scores = _score_names(query_norm, query_tokens, index["names"])

    by_type = {t: _RANKERS[t](conn, name_scores, index, query_tokens) for t in TYPES}
    combined = sorted(
        (row for rows in by_type.values() for row in rows), key=lambda r: -r["rank_key"]
    )
    return {**by_type, "combined": combined}


# ---------------------------------------------------------------- hydration (§4.7: only the rendered slice)


def _hydrate_songs(conn, ranked_slice):
    out = []
    for r in ranked_slice:
        vid = r["id"]
        rep_id = canonical.representative(conn, vid) or r["track_id"]
        display = canonical.track_display(conn, rep_id)
        out.append({"type": "songs", "id": vid, "version_id": vid, "score": r["score"], **display})
    return out


def _hydrate_albums(conn, ranked_slice):
    if not ranked_slice:
        return []
    ids = [r["id"] for r in ranked_slice]
    placeholders = ",".join("?" for _ in ids)
    rows = {
        row["album_id"]: row
        for row in conn.execute(
            f"SELECT album_id, name, image_url FROM album WHERE album_id IN ({placeholders})", ids
        )
    }
    out = []
    for r in ranked_slice:
        row = rows.get(r["id"])
        if row is None:
            continue
        out.append(
            {
                "type": "albums",
                "id": row["album_id"],
                "album_id": row["album_id"],
                "name": row["name"],
                "image_url": row["image_url"],
                "score": r["score"],
            }
        )
    return out


def _hydrate_artists(conn, ranked_slice):
    if not ranked_slice:
        return []
    ids = [r["id"] for r in ranked_slice]
    placeholders = ",".join("?" for _ in ids)
    rows = {
        row["artist_id"]: row
        for row in conn.execute(
            f"SELECT artist_id, name, image_url FROM artist WHERE artist_id IN ({placeholders})", ids
        )
    }
    out = []
    for r in ranked_slice:
        row = rows.get(r["id"])
        if row is None:
            continue
        out.append(
            {
                "type": "artists",
                "id": row["artist_id"],
                "artist_id": row["artist_id"],
                "name": row["name"],
                "image_url": row["image_url"],
                "score": r["score"],
            }
        )
    return out


def _hydrate_playlists(conn, ranked_slice):
    if not ranked_slice:
        return []
    ids = [r["id"] for r in ranked_slice]
    placeholders = ",".join("?" for _ in ids)
    rows = {
        row["playlist_id"]: row
        for row in conn.execute(
            f"SELECT playlist_id, name, image_url FROM snapshot WHERE playlist_id IN ({placeholders})",
            ids,
        )
    }
    out = []
    for r in ranked_slice:
        row = rows.get(r["id"])
        if row is None:
            continue
        out.append(
            {
                "type": "playlists",
                "id": row["playlist_id"],
                "playlist_id": row["playlist_id"],
                "name": row["name"],
                "image_url": row["image_url"],
                "score": r["score"],
            }
        )
    return out


_HYDRATORS = {
    "songs": _hydrate_songs,
    "albums": _hydrate_albums,
    "artists": _hydrate_artists,
    "playlists": _hydrate_playlists,
}

# combined/dropdown rows are one uniform shape regardless of type -- a type
# label, a link kind + id, a name, an optional cover, and a score. Built from
# the richer per-type hydrate dicts above rather than duplicating their
# queries.
_COMBINED_KIND = {"songs": "version", "albums": "album", "artists": "artist", "playlists": "playlist"}
_COMBINED_IMAGE_KEY = {
    "songs": "album_image_url",
    "albums": "image_url",
    "artists": "image_url",
    "playlists": "image_url",
}


def _hydrate_combined(conn, combined_slice):
    by_type = defaultdict(list)
    for r in combined_slice:
        by_type[r["type"]].append(r)

    lookup = {}
    for t, rows in by_type.items():
        for h in _HYDRATORS[t](conn, rows):
            lookup[(t, h["id"])] = h

    out = []
    for r in combined_slice:
        h = lookup.get((r["type"], r["id"]))
        if h is None:
            continue
        out.append(
            {
                "type": r["type"],
                "type_label": _TYPE_LABELS[r["type"]],
                "kind": _COMBINED_KIND[r["type"]],
                "id": r["id"],
                "name": h["name"],
                "image_url": h.get(_COMBINED_IMAGE_KEY[r["type"]]),
                "score": h["score"],
            }
        )
    return out


# ---------------------------------------------------------------- the three routes' entry points


def search_page(conn, q):
    """/search's whole kwargs (§6): `most_relevant` (COMBINED_LIMIT, mixed)
    plus each type's first SECTION_LIMIT rows and its total candidate count,
    which the template uses to decide whether to show a See more control."""
    ranked = rank(conn, q)
    result = {"most_relevant": _hydrate_combined(conn, ranked["combined"][:COMBINED_LIMIT])}
    for t in TYPES:
        result[t] = _HYDRATORS[t](conn, ranked[t][:SECTION_LIMIT])
        result[f"{t}_total"] = len(ranked[t])
    return result


def search_dropdown(conn, q):
    """/api/search's DROPDOWN_LIMIT mixed rows, same shape as Most
    Relevant (§7)."""
    ranked = rank(conn, q)
    return _hydrate_combined(conn, ranked["combined"][:DROPDOWN_LIMIT])


def search_more(conn, q, type_):
    """/api/search/more's up-to-SECTION_MAX rows for one type. `type_` is
    already whitelisted by the caller against TYPES."""
    ranked = rank(conn, q)
    return _HYDRATORS[type_](conn, ranked[type_][:SECTION_MAX])
