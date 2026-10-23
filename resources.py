"""The v1 JSON API's builders (docs/specs/api-Z1.md §2.2).

Every function that produces a v1 representation lives here: the batched
summary builders (§6.1), the detail builders (§6.2), the list query (§7), search
(§8), plays and the timeline (§9), generations (§9.3), status (§10) and the
three writes (§11). api.py is route plumbing over these -- it parses and
validates params, calls one of them, and jsonifies.

**No Flask request machinery in here** -- no `request`, no `abort`, no
`jsonify` -- so every builder is callable against a fixture database with no
request context. The one Flask touch is `entity_url`, which needs an
*application* context (not a request) to build a URL from the same endpoints
`entity_link` uses, so a URL can never drift from a page's. A refusal is an
`ApiError`, which api.py turns into the {"error", "detail"} response.

**Nothing here imports `roundtrip`, and nothing may** (§3.2): roundtrip.py is the
only module that writes to the Spotify library, and v1 is the surface AB will
expose beyond the tailnet. A test reads this file's imports for it.

It reuses what exists rather than re-deriving it -- scores through scoring's
batched lookups, generations through generations.py, the matcher through
search.rank, the guarded fetches through entities -- and writes a set-based
query once where an existing helper is per-entity (`group_representatives`,
the per-track metric maps), with the per-entity case calling it with one id.
"""

import bisect
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

import api_log
import canonical
import db
import entities
import generations
import jobs
import normalize
import scoring
import scrobble
import search
import snapshot
from spotify_client import get_spotify_client

# §5.3. A module constant rather than config.py, for the reason scoring.py
# gives for its own: one meaning everywhere, not a per-environment setting.
DEFAULT_TZ = "America/Toronto"
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200

GROUP_TIERS = ("song", "version", "recording", "release")
TYPES = ("track", "album", "artist", "playlist") + GROUP_TIERS
PLURAL = {t: t + "s" for t in TYPES}
SINGULAR = {v: k for k, v in PLURAL.items()}

# More ids than this and a query runs unrestricted, then filters in Python:
# SQLite's host-parameter limit is far above it, but a 12k-item IN list is
# slower than the scan it replaces.
_IN_LIMIT = 900


class ApiError(Exception):
    """A refusal, as the {"error": slug, "detail": ...} body and status api.py
    returns for it (§5.2)."""

    def __init__(self, slug, status, detail=None):
        super().__init__(f"{slug}: {detail}")
        self.slug = slug
        self.status = status
        self.detail = detail


# -- Values -------------------------------------------------------------------


def _bool(value):
    return None if value is None else bool(value)


def _in(column, ids):
    """("col IN (?, ...)", params) for a small id list, ("1=1", []) for a big
    one -- the caller filters the rows by id either way."""
    ids = list(ids)
    if len(ids) > _IN_LIMIT:
        return "1=1", []
    return f"{column} IN ({','.join('?' for _ in ids)})", ids


_ENDPOINT = {
    "track": ("track_page", "track_id"),
    "album": ("album_page", "album_id"),
    "artist": ("artist_page", "artist_id"),
    "playlist": ("playlist_page", "playlist_id"),
    "song": ("song_page", "group_id"),
    "version": ("version_page", "group_id"),
    "recording": ("recording_page", "group_id"),
    "release": ("release_page", "group_id"),
}


def entity_url(type_, entity_id):
    """The site path of an entity's page (§5.1), built from the endpoints
    `entity_link` uses, never by hand. Relative, so it is right on both
    machines."""
    endpoint, arg = _ENDPOINT[type_]
    return current_app.url_map.bind("").build(endpoint, {arg: entity_id})


def _spotify_playlist_url(playlist_id):
    # snapshot stores no external_url for playlists (unlike track/album/
    # artist), so this one is derived. Liked Songs has no public page.
    if playlist_id == snapshot.LIKED_PLAYLIST_ID:
        return None
    return f"https://open.spotify.com/playlist/{playlist_id}"


# -- Dates, zones and the play window (§5.3) ------------------------------------


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc_str(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Window:
    """The resolved §5.3 params. `lo` / `hi` are UTC strings in the stored
    format (`hi` exclusive), or None; `has_range` is whether `from` or `to` was
    given, which is what decides whether `window` is reported at all."""

    __slots__ = ("tz_name", "tz", "lo", "hi", "min_played_ms")

    def __init__(self, tz_name, tz, lo, hi, min_played_ms):
        self.tz_name = tz_name
        self.tz = tz
        self.lo = lo
        self.hi = hi
        self.min_played_ms = min_played_ms

    @property
    def has_range(self):
        return self.lo is not None or self.hi is not None


def _parse_bound(text, tz, name, *, upper):
    """A bare date is a day in `tz` (an upper bound runs to the end of that
    day, so it returns the *next* midnight); a datetime must carry an offset."""
    try:
        if _DATE_RE.match(text):
            day = date.fromisoformat(text)
            if upper:
                day = day + timedelta(days=1)
            return _utc_str(datetime(day.year, day.month, day.day, tzinfo=tz))
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ApiError("invalid_parameter", 400, f"{name}: not a date or ISO-8601 datetime") from None
    if moment.tzinfo is None:
        raise ApiError(
            "invalid_parameter", 400,
            f"{name}: a datetime needs an offset (e.g. +00:00); pass a bare date for a day in tz",
        )
    return _utc_str(moment)


def make_window(tz=None, from_=None, to=None, min_played_ms=None):
    tz_name = tz or DEFAULT_TZ
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ApiError("invalid_parameter", 400, f"tz: unknown time zone {tz_name!r}") from None
    lo = _parse_bound(from_, zone, "from", upper=False) if from_ else None
    hi = _parse_bound(to, zone, "to", upper=True) if to else None
    if lo is not None and hi is not None:
        # A bare `to` already runs to the end of its day, so a `from` at or past
        # that instant is after the `to` date; a datetime `to` is exclusive, so
        # only a strictly later `from` is.
        to_is_date = bool(_DATE_RE.match(to))
        if lo > hi or (to_is_date and lo == hi):
            raise ApiError("invalid_parameter", 400, "from is after to")
    if min_played_ms is not None and min_played_ms < 0:
        raise ApiError("invalid_parameter", 400, "min_played_ms: must not be negative")
    return Window(tz_name, zone, lo, hi, min_played_ms)


def _play_bounds_sql(window, alias="p"):
    """(" AND ..." clause, params) for the window's range and min_played_ms --
    the filter every play listing and the timeline share."""
    clause, params = "", []
    if window.lo is not None:
        clause += f" AND {alias}.ts >= ?"
        params.append(window.lo)
    if window.hi is not None:
        clause += f" AND {alias}.ts < ?"
        params.append(window.hi)
    if window.min_played_ms is not None:
        clause += f" AND {alias}.ms_played >= ?"
        params.append(window.min_played_ms)
    return clause, params


# -- Per-track metric maps -------------------------------------------------------
#
# Every metric is built per track and rolled up to an entity, so a list's sort
# (which needs it for every entity) and a detail (one entity) are the same
# query; the entity's tracks are its coverage (§7.1).


class _Metrics:
    """Plays, live memberships and generation presence per track, loaded once
    per request. A method given a small track list queries just those; a big
    one loads the whole map and keeps it."""

    def __init__(self, conn, window):
        self.conn = conn
        self.window = window
        now = datetime.now(timezone.utc)
        self.month_start = (now - timedelta(days=entities._MONTH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.week_start = (now - timedelta(days=entities._WEEK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.data_through = conn.execute("SELECT MAX(ts) FROM play").fetchone()[0]
        self._full = {}

    def _load(self, name, loader, track_ids):
        if name in self._full:
            return self._full[name]
        if track_ids is not None and len(track_ids) <= _IN_LIMIT:
            return loader(list(track_ids))
        self._full[name] = loader(None)
        return self._full[name]

    # -- plays ---------------------------------------------------------------

    def _load_plays(self, track_ids):
        w = self.window
        cols = "COUNT(*) AS total, SUM(p.ts >= ?) AS m, SUM(p.ts >= ?) AS w"
        params = [self.month_start, self.week_start]
        if w.has_range:
            conds = []
            if w.lo is not None:
                conds.append("p.ts >= ?")
                params.append(w.lo)
            if w.hi is not None:
                conds.append("p.ts < ?")
                params.append(w.hi)
            cols += f", SUM({' AND '.join(conds)}) AS win"
        else:
            cols += ", 0 AS win"
        where, wparams = "1=1", []
        if w.min_played_ms is not None:
            where, wparams = "p.ms_played >= ?", [w.min_played_ms]
        if track_ids is not None:
            clause, cparams = _in("x.track_id", track_ids)
            where += f" AND {clause}"
            wparams += cparams
        rows = self.conn.execute(
            f"SELECT x.track_id, {cols} FROM play p "
            f"JOIN played_uri_track x ON x.uri = p.spotify_track_uri "
            f"WHERE {where} GROUP BY x.track_id",
            params + wparams,
        )
        return {r[0]: (r[1], r[2] or 0, r[3] or 0, r[4] or 0) for r in rows}

    def plays_by_track(self, track_ids=None):
        return self._load("plays", self._load_plays, track_ids)

    def plays(self, track_ids, by_track=None):
        """§5.1's plays shape over a track set. The rolling windows keep
        entities.play_stats' semantics: None when the play data ends before the
        window starts, so a stale export reads differently from a genuine zero."""
        by_track = by_track if by_track is not None else self.plays_by_track(track_ids)
        total = month = week = win = 0
        for tid in set(track_ids):
            t = by_track.get(tid)
            if t:
                total += t[0]
                month += t[1]
                week += t[2]
                win += t[3]
        dt = self.data_through
        return {
            "total": total,
            "past_30d": None if dt and dt < self.month_start else month,
            "past_7d": None if dt and dt < self.week_start else week,
            "window": win if self.window.has_range else None,
            "data_through": dt,
        }

    def play_sort_value(self, track_ids, by_track):
        idx = 3 if self.window.has_range else 0
        return sum(by_track[t][idx] for t in set(track_ids) if t in by_track)

    # -- live memberships --------------------------------------------------------

    def _load_memberships(self, track_ids):
        where, params = "1=1", []
        if track_ids is not None:
            where, params = _in("m.track_id", track_ids)
        rows = self.conn.execute(
            f"SELECT m.track_id, COUNT(*) FROM membership m "
            f"JOIN snapshot s ON s.playlist_id = m.playlist_id "
            f"WHERE m.removed_at IS NULL AND s.unfollowed_at IS NULL AND {where} "
            f"GROUP BY m.track_id",
            params,
        )
        return {r[0]: r[1] for r in rows}

    def memberships_by_track(self, track_ids=None):
        return self._load("memberships", self._load_memberships, track_ids)

    def memberships(self, track_ids, by_track=None):
        by_track = by_track if by_track is not None else self.memberships_by_track(track_ids)
        return sum(by_track.get(t, 0) for t in set(track_ids))

    # -- generation presence -----------------------------------------------------

    def _load_ordinals(self, track_ids):
        where, params = "1=1", []
        if track_ids is not None:
            where, params = _in("track_id", track_ids)
        out = defaultdict(set)
        for r in self.conn.execute(
            f"SELECT track_id, ordinal FROM generation_presence WHERE {where}", params
        ):
            out[r[0]].add(r[1])
        return out

    def ordinals_by_track(self, track_ids=None):
        return self._load("ordinals", self._load_ordinals, track_ids)

    def ordinals(self, track_ids, by_track=None):
        by_track = by_track if by_track is not None else self.ordinals_by_track(track_ids)
        found = set()
        for t in set(track_ids):
            found |= by_track.get(t, set())
        return found


def generation_facts(ordinals):
    """§6.2's `generations` section from a set of ordinals -- the same
    generations.runs rule the tenure page and the entity pages' strip use."""
    ords = sorted(ordinals)
    if not ords:
        return {"ordinals": [], "total": 0, "tenure": 0, "run_count": 0,
                "first_ordinal": None, "last_ordinal": None}
    runs = generations.runs(ords)
    return {
        "ordinals": ords,
        "total": len(ords),
        "tenure": max(end - start + 1 for start, end in runs),
        "run_count": len(runs),
        "first_ordinal": ords[0],
        "last_ordinal": ords[-1],
    }


# -- Coverage: the tracks an entity stands for (§7.1) -------------------------------


def coverage(conn, type_, ids):
    """{entity_id: [track_id, ...]} (each track once). A group covers its
    members, an album its owned tracks, an artist every track credited to it in
    any role (alias-resolved), a playlist its live tracks."""
    ids = list(ids)
    if type_ == "track":
        return {i: [i] for i in ids}
    if type_ in GROUP_TIERS:
        column = canonical.TIER_COLUMN[type_]
        clause, params = _in(f"{column}", ids)
        sql = f"SELECT {column}, track_id FROM track_group WHERE {clause}"
    elif type_ == "album":
        clause, params = _in("album_id", ids)
        sql = f"SELECT album_id, track_id FROM track WHERE album_id IS NOT NULL AND {clause}"
    elif type_ == "artist":
        clause, params = _in("artist_id", ids)
        sql = f"SELECT DISTINCT artist_id, track_id FROM resolved_track_artist WHERE {clause}"
    else:
        clause, params = _in("m.playlist_id", ids)
        sql = (
            "SELECT DISTINCT m.playlist_id, m.track_id FROM membership m "
            "JOIN snapshot s ON s.playlist_id = m.playlist_id "
            f"WHERE m.removed_at IS NULL AND s.unfollowed_at IS NULL AND {clause}"
        )
    wanted = set(ids)
    out = {i: [] for i in ids}
    for row in conn.execute(sql, params):
        if row[0] in wanted:
            out[row[0]].append(row[1])
    return out


# -- Group representatives (§6.1) ---------------------------------------------------


def group_representatives(conn, tier, ids=None):
    """{group_id: representative track id} for a whole tier, or just `ids`.

    The set-based form of canonical.representative -- the same election, so it
    is tested for parity with it: the pinned track if there is one, else the
    highest track score, then the oldest added_at, then the lowest track_id."""
    column = canonical.TIER_COLUMN[tier]
    where, params = "1=1", []
    if ids is not None:
        where, params = _in(f"tg.{column}", ids)
    pinned_where, pinned_params = ("1=1", [])
    if ids is not None:
        pinned_where, pinned_params = _in("id", ids)
    pinned = {
        r["id"]: r["representative_track_id"]
        for r in conn.execute(
            f"SELECT id, representative_track_id FROM canonical_group "
            f"WHERE tier = ? AND representative_track_id IS NOT NULL AND {pinned_where}",
            [tier, *pinned_params],
        )
    }
    best = {}
    for r in conn.execute(
        f"""
        SELECT tg.{column} AS gid, tg.track_id,
               COALESCE(sc.all_time, 0.0) AS score,
               (SELECT MIN(added_at) FROM membership m WHERE m.track_id = tg.track_id) AS oldest
        FROM track_group tg
        LEFT JOIN score sc ON sc.tier = 'track' AND sc.group_id = tg.track_id
        WHERE {where}
        """,
        params,
    ):
        key = (-r["score"], r["oldest"] or "9999", r["track_id"])
        if r["gid"] not in best or key < best[r["gid"]][0]:
            best[r["gid"]] = (key, r["track_id"])
    wanted = None if ids is None else set(ids)
    reps = {g: v[1] for g, v in best.items() if wanted is None or g in wanted}
    reps.update({g: t for g, t in pinned.items() if wanted is None or g in wanted})
    return reps


# -- Scores ---------------------------------------------------------------------------


def _scored_version_ids(conn):
    return {int(r[0]) for r in conn.execute("SELECT group_id FROM score WHERE tier = 'version'")}


def _entity_versions(conn, type_, ids):
    """{entity_id: {version_id, ...}} for the query-time types, so a score can
    be null when none of an entity's versions has a materialized one."""
    ids = list(ids)
    if type_ == "song":
        clause, params = _in("song_id", ids)
        sql = f"SELECT DISTINCT song_id, version_id FROM track_group WHERE {clause}"
    elif type_ == "album":
        clause, params = _in("t.album_id", ids)
        sql = ("SELECT DISTINCT t.album_id, tg.version_id FROM track t "
               f"JOIN track_group tg ON tg.track_id = t.track_id WHERE {clause}")
    elif type_ == "artist":
        clause, params = _in("r.artist_id", ids)
        sql = ("SELECT DISTINCT r.artist_id, tg.version_id FROM resolved_track_artist r "
               f"JOIN track_group tg ON tg.track_id = r.track_id WHERE {clause}")
    else:
        clause, params = _in("m.playlist_id", ids)
        sql = ("SELECT DISTINCT m.playlist_id, tg.version_id FROM membership m "
               "JOIN track_group tg ON tg.track_id = m.track_id "
               f"WHERE m.removed_at IS NULL AND {clause}")
    out = defaultdict(set)
    for r in conn.execute(sql, params):
        out[r[0]].add(r[1])
    return out


def scores(conn, type_, ids):
    """{id: {"all_time", "recent"} | None}. **None, not a zero, when nothing is
    materialized for the entity** -- zero is a score (a real, computed one) and
    "no score" is not. For the query-time types (song, album, artist, playlist)
    that means none of the entity's versions has a score row."""
    ids = list(ids)
    if not ids:
        return {}
    if type_ in ("track", "version", "recording", "release"):
        found = scoring.scores_for_tier(conn, type_, ids)
        return {i: found.get(i) for i in ids}
    computed = {
        "song": scoring.song_scores,
        "album": scoring.album_scores,
        "artist": scoring.artist_scores,
        "playlist": scoring.playlist_scores,
    }[type_](conn, ids)
    scored = _scored_version_ids(conn)
    if not scored:
        return {i: None for i in ids}
    versions = _entity_versions(conn, type_, ids)
    return {
        i: computed.get(i) if versions.get(i, set()) & scored else None
        for i in ids
    }


# -- Summaries (§6.1) ------------------------------------------------------------------
#
# One shape per type, built by one batched builder per type: one query per type
# per call, never one call per row. Wherever an entity appears -- a list, a
# search result, nested inside another -- it is this shape, by construction.


def _track_core(conn, track_ids):
    """{track_id: fields every track-bearing summary shares}."""
    track_ids = list(dict.fromkeys(track_ids))
    if not track_ids:
        return {}
    clause, params = _in("t.track_id", track_ids)
    wanted = set(track_ids)
    rows = conn.execute(
        "SELECT t.track_id, t.name, t.album_id, t.duration_ms, t.explicit, t.external_url, "
        "a.name AS album_name, a.image_url AS album_image_url "
        f"FROM track t LEFT JOIN album a ON a.album_id = t.album_id WHERE {clause}",
        params,
    ).fetchall()
    rows = [r for r in rows if r["track_id"] in wanted]
    credits = canonical.artist_credits_for_tracks(conn, [r["track_id"] for r in rows])
    out = {}
    for r in rows:
        out[r["track_id"]] = {
            "name": r["name"],
            "spotify_url": r["external_url"],
            "artists": [
                {"id": c["artist_id"], "name": c["name"]} for c in credits.get(r["track_id"], [])
            ],
            "album": (
                {"id": r["album_id"], "name": r["album_name"], "url": entity_url("album", r["album_id"])}
                if r["album_id"] else None
            ),
            "duration_ms": r["duration_ms"],
            "explicit": _bool(r["explicit"]),
            "cover_url": r["album_image_url"],
        }
    return out


def track_summaries(conn, ids):
    """A track: `type`, `id`, `name`, `url`, `spotify_url`, `artists` ([{id,
    name}] in credit order, alias-resolved), `album` ({id, name, url} or null),
    `duration_ms`, `explicit`, `cover_url` (the album's) and `score`
    ({all_time, recent}, or null when none is materialized)."""
    core = _track_core(conn, ids)
    score = scores(conn, "track", core)
    return {
        tid: {
            "type": "track", "id": tid, "name": c["name"], "url": entity_url("track", tid),
            "spotify_url": c["spotify_url"], "artists": c["artists"], "album": c["album"],
            "duration_ms": c["duration_ms"], "explicit": c["explicit"],
            "cover_url": c["cover_url"], "score": score[tid],
        }
        for tid, c in core.items()
    }


def group_summaries(conn, tier, ids):
    """A song / version / recording / release group: `type`, `id` (the
    canonical_group id), `name` (its representative track's), `url`, and from
    that representative `artists`, `album`, `duration_ms` and `cover_url`;
    `representative_track_id`, `member_count` and `score` (null when none is
    materialized). The representative is the one the pages elect."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    reps = group_representatives(conn, tier, ids)
    core = _track_core(conn, reps.values())
    column = canonical.TIER_COLUMN[tier]
    clause, params = _in(column, ids)
    wanted = set(ids)
    counts = {
        r[0]: r[1]
        for r in conn.execute(
            f"SELECT {column}, COUNT(*) FROM track_group WHERE {clause} GROUP BY {column}", params
        )
        if r[0] in wanted
    }
    score = scores(conn, tier, ids)
    out = {}
    for gid in ids:
        if gid not in counts:
            continue
        rep = reps.get(gid)
        c = core.get(rep) or {}
        out[gid] = {
            "type": tier, "id": gid, "name": c.get("name"), "url": entity_url(tier, gid),
            "artists": c.get("artists", []), "album": c.get("album"),
            "duration_ms": c.get("duration_ms"), "cover_url": c.get("cover_url"),
            "representative_track_id": rep, "member_count": counts[gid], "score": score[gid],
        }
    return out


def _album_artists(conn, album_ids):
    clause, params = _in("ab.album_id", album_ids)
    wanted = set(album_ids)
    out = defaultdict(list)
    # Grouped so a credit under both an alias id and its canonical id collapses
    # to one (same as entities.album_detail).
    for r in conn.execute(
        "SELECT ab.album_id, COALESCE(aa.canonical_artist_id, ab.artist_id) AS artist_id, ar.name "
        "FROM album_artist ab LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id "
        "JOIN artist ar ON ar.artist_id = COALESCE(aa.canonical_artist_id, ab.artist_id) "
        f"WHERE {clause} "
        "GROUP BY ab.album_id, COALESCE(aa.canonical_artist_id, ab.artist_id), ar.name "
        "ORDER BY ab.album_id, MIN(ab.position)",
        params,
    ):
        if r["album_id"] in wanted:
            out[r["album_id"]].append({"id": r["artist_id"], "name": r["name"]})
    return out


def album_summaries(conn, ids):
    """An album: `type`, `id`, `name`, `url`, `spotify_url`, `artists` ([{id,
    name}], alias-resolved), `album_type`, `release_date`,
    `release_date_precision`, `total_tracks`, `owned_count` (how many Symr
    holds), `cover_url` and `score` (null when none is materialized)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    clause, params = _in("album_id", ids)
    wanted = set(ids)
    rows = [
        r for r in conn.execute(
            "SELECT album_id, name, album_type, release_date, release_date_precision, "
            f"total_tracks, image_url, external_url FROM album WHERE {clause}",
            params,
        ) if r["album_id"] in wanted
    ]
    found = [r["album_id"] for r in rows]
    credits = _album_artists(conn, found)
    oclause, oparams = _in("album_id", found)
    owned = {
        r[0]: r[1]
        for r in conn.execute(
            f"SELECT album_id, COUNT(*) FROM track WHERE {oclause} GROUP BY album_id", oparams
        )
    }
    score = scores(conn, "album", found)
    return {
        r["album_id"]: {
            "type": "album", "id": r["album_id"], "name": r["name"],
            "url": entity_url("album", r["album_id"]), "spotify_url": r["external_url"],
            "artists": credits.get(r["album_id"], []), "album_type": r["album_type"],
            "release_date": r["release_date"],
            "release_date_precision": r["release_date_precision"],
            "total_tracks": r["total_tracks"], "owned_count": owned.get(r["album_id"], 0),
            "cover_url": r["image_url"], "score": score[r["album_id"]],
        }
        for r in rows
    }


def artist_summaries(conn, ids):
    """An artist: `type`, `id`, `name`, `url`, `spotify_url`, `image_url` (null
    until fetched) and `score` (null when none is materialized). Ids are
    canonical -- see resolve_artist."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    clause, params = _in("artist_id", ids)
    wanted = set(ids)
    rows = [
        r for r in conn.execute(
            f"SELECT artist_id, name, external_url, image_url FROM artist WHERE {clause}", params
        ) if r["artist_id"] in wanted
    ]
    score = scores(conn, "artist", [r["artist_id"] for r in rows])
    return {
        r["artist_id"]: {
            "type": "artist", "id": r["artist_id"], "name": r["name"],
            "url": entity_url("artist", r["artist_id"]), "spotify_url": r["external_url"],
            "image_url": r["image_url"], "score": score[r["artist_id"]],
        }
        for r in rows
    }


def playlist_summaries(conn, ids):
    """A playlist: `type`, `id`, `name`, `url`, `spotify_url` (derived from the
    id; null for Liked Songs), `owner`, `cover_url`, `live_count` (live
    membership rows), `excluded`, `is_liked` (Liked Songs, which is pulled
    outside the main loop and has no snapshot_id), `generation` (its ordinal, or
    null) and `score` (null when none is materialized)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    clause, params = _in("playlist_id", ids)
    wanted = set(ids)
    rows = [
        r for r in conn.execute(
            f"SELECT playlist_id, name, image_url, owner, excluded FROM snapshot WHERE {clause}",
            params,
        ) if r["playlist_id"] in wanted
    ]
    found = [r["playlist_id"] for r in rows]
    mclause, mparams = _in("playlist_id", found)
    live = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT playlist_id, COUNT(*) FROM membership "
            f"WHERE removed_at IS NULL AND {mclause} GROUP BY playlist_id",
            mparams,
        )
    }
    gclause, gparams = _in("playlist_id", found)
    gen = {
        r[0]: r[1]
        for r in conn.execute(
            f"SELECT playlist_id, ordinal FROM generation WHERE {gclause}", gparams
        )
    }
    score = scores(conn, "playlist", found)
    return {
        r["playlist_id"]: {
            "type": "playlist", "id": r["playlist_id"], "name": r["name"],
            "url": entity_url("playlist", r["playlist_id"]),
            "spotify_url": _spotify_playlist_url(r["playlist_id"]), "owner": r["owner"],
            "cover_url": r["image_url"], "live_count": live.get(r["playlist_id"], 0),
            "excluded": bool(r["excluded"]),
            "is_liked": r["playlist_id"] == snapshot.LIKED_PLAYLIST_ID,
            "generation": gen.get(r["playlist_id"]), "score": score[r["playlist_id"]],
        }
        for r in rows
    }


def summaries(conn, type_, ids):
    """{id: summary} for any of the eight types."""
    if type_ == "track":
        return track_summaries(conn, ids)
    if type_ == "album":
        return album_summaries(conn, ids)
    if type_ == "artist":
        return artist_summaries(conn, ids)
    if type_ == "playlist":
        return playlist_summaries(conn, ids)
    return group_summaries(conn, type_, ids)


# Documented once at the index (§5.4), from the builders' own docstrings.
SHAPE_DOCS = {
    "track": track_summaries.__doc__,
    "song": group_summaries.__doc__,
    "version": group_summaries.__doc__,
    "recording": group_summaries.__doc__,
    "release": group_summaries.__doc__,
    "album": album_summaries.__doc__,
    "artist": artist_summaries.__doc__,
    "playlist": playlist_summaries.__doc__,
}


def resolve_artist(conn, artist_id):
    """An alias id's canonical artist id; any other id unchanged (§5.1)."""
    row = conn.execute(
        "SELECT canonical_artist_id FROM artist_alias WHERE artist_id = ?", (artist_id,)
    ).fetchone()
    return row[0] if row else artist_id


# -- Details (§6.2) --------------------------------------------------------------------


def _playlists_section(conn, track_ids):
    """Summaries of the playlists holding any of `track_ids` live, each with the
    earliest live added_at among those tracks. Unfollowed playlists are excluded
    entirely (W §9.14b), as entities.playlists_for_tracks does."""
    track_ids = list(dict.fromkeys(track_ids))
    if not track_ids:
        return []
    clause, params = _in("m.track_id", track_ids)
    wanted = set(track_ids)
    added = {}
    for r in conn.execute(
        "SELECT m.playlist_id, m.track_id, m.added_at FROM membership m "
        "JOIN snapshot s ON s.playlist_id = m.playlist_id "
        f"WHERE m.removed_at IS NULL AND s.unfollowed_at IS NULL AND {clause}",
        params,
    ):
        if r["track_id"] not in wanted:
            continue
        cur = added.get(r["playlist_id"], "")
        a = r["added_at"]
        if r["playlist_id"] not in added or (a is not None and (cur is None or a < cur)):
            added[r["playlist_id"]] = a
    summ = playlist_summaries(conn, added)
    items = [{**summ[p], "added_at": added[p]} for p in added if p in summ]
    items.sort(key=lambda s: ((s["name"] or "").casefold(), s["id"]))
    return items


def _entity_sections(conn, track_ids, window, *, with_playlists=True, with_generations=True):
    metrics = _Metrics(conn, window)
    out = {"plays": metrics.plays(track_ids)}
    if with_playlists:
        out["playlists"] = _playlists_section(conn, track_ids)
    if with_generations:
        out["generations"] = generation_facts(metrics.ordinals(track_ids))
    return out


def track_detail(conn, track_id, window):
    row = conn.execute(
        "SELECT track_id, isrc, uri, track_number, disc_number FROM track WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    summary = track_summaries(conn, [track_id]).get(track_id)
    if row is None or summary is None:
        return None

    groups = canonical.groups_for_track(conn, track_id) or {}
    group_out = {}
    for tier in GROUP_TIERS:
        gid = groups.get(tier)
        group_out[tier] = group_summaries(conn, tier, [gid]).get(gid) if gid is not None else None

    # Unfollowed playlists excluded, removed rows included -- the same rows
    # entities.track_detail reads, and for the same reason.
    mem_rows = conn.execute(
        "SELECT m.playlist_id, m.position, m.added_at, m.removed_at FROM membership m "
        "JOIN snapshot s ON s.playlist_id = m.playlist_id "
        "WHERE m.track_id = ? AND s.unfollowed_at IS NULL "
        "ORDER BY s.name COLLATE NOCASE, m.added_at, m.id",
        (track_id,),
    ).fetchall()
    pl = playlist_summaries(conn, [r["playlist_id"] for r in mem_rows])
    aliases = [
        r[0]
        for r in conn.execute(
            "SELECT requested_uri FROM track_uri_alias WHERE track_id = ? ORDER BY requested_uri",
            (track_id,),
        )
    ]
    return {
        **summary,
        "isrc": row["isrc"], "uri": row["uri"],
        "track_number": row["track_number"], "disc_number": row["disc_number"],
        "groups": group_out,
        "memberships": [
            {"playlist": pl[r["playlist_id"]], "position": r["position"],
             "added_at": r["added_at"], "removed_at": r["removed_at"]}
            for r in mem_rows if r["playlist_id"] in pl
        ],
        "aliases": aliases,
        **_entity_sections(conn, [track_id], window),
    }


_FINER = {"song": "version", "version": "recording", "recording": "release"}


def group_detail(conn, tier, group_id, window):
    summary = group_summaries(conn, tier, [group_id]).get(group_id)
    if summary is None:
        return None
    column = canonical.TIER_COLUMN[tier]
    track_ids = [
        r[0] for r in conn.execute(f"SELECT track_id FROM track_group WHERE {column} = ?", (group_id,))
    ]

    # Every member shares each tier above this one, so one member's row names
    # them all.
    parents = {}
    above = GROUP_TIERS[: GROUP_TIERS.index(tier)]
    if above:
        row = conn.execute(
            "SELECT song_id, version_id, recording_id, release_id FROM track_group "
            "WHERE track_id = ?", (track_ids[0],),
        ).fetchone()
        for t in above:
            gid = row[f"{t}_id"]
            parents[t] = group_summaries(conn, t, [gid]).get(gid)

    children = None
    if tier in _FINER:
        finer = _FINER[tier]
        fcol = canonical.TIER_COLUMN[finer]
        child_ids = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT {fcol} FROM track_group WHERE {column} = ?", (group_id,)
            )
        ]
        kids = group_summaries(conn, finer, child_ids)
        children = sorted(
            kids.values(),
            key=lambda s: (s["score"] is None, -(s["score"] or {}).get("all_time", 0.0), s["id"]),
        )

    track_scores = scores(conn, "track", track_ids)
    members = track_summaries(conn, track_ids)
    ordered = sorted(
        members.values(),
        key=lambda t: (-(track_scores[t["id"]] or {}).get("all_time", 0.0),
                       (t["name"] or "").casefold(), t["id"]),
    )
    pinned = None
    if tier == "song":
        pinned = bool(conn.execute(
            "SELECT representative_track_id FROM canonical_group WHERE id = ?", (group_id,)
        ).fetchone()[0])
    return {
        **summary,
        "pinned": pinned,
        "parents": parents,
        "children": children,
        "members": ordered,
        **_entity_sections(conn, track_ids, window),
    }


def _tracklist_view(conn, album_row):
    """(tracklist items | None, appended owned tracks, total_duration_ms) from
    the stored tracklist, with no request."""
    album_id = album_row["album_id"]
    owned_ids = [r[0] for r in conn.execute("SELECT track_id FROM track WHERE album_id = ?", (album_id,))]
    owned = track_summaries(conn, owned_ids)
    raw = album_row["tracklist_json"]
    if raw is None:
        appended = sorted(owned.values(), key=lambda t: t["id"])
        return None, appended, None, owned_ids

    items = json.loads(raw)
    items.sort(key=lambda i: (i.get("disc_number") or 1, i.get("track_number") or 0))
    shown = {i["id"] for i in items if i.get("id")}
    tracklist = [
        {
            "disc_number": i.get("disc_number"), "track_number": i.get("track_number"),
            "name": i.get("name"), "uri": i.get("uri"), "duration_ms": i.get("duration_ms"),
            "track": owned.get(i.get("id")),
        }
        for i in items
    ]
    appended = [owned[t] for t in owned_ids if t not in shown and t in owned]
    appended.sort(key=lambda t: t["id"])
    durations = [i.get("duration_ms") for i in items] + [t["duration_ms"] for t in appended]
    complete = (
        album_row["total_tracks"] is not None
        and len(durations) == album_row["total_tracks"]
        and all(d is not None for d in durations)
    )
    return tracklist, appended, (sum(durations) if complete else None), owned_ids


def _outcome_error(outcome, what):
    if outcome == "no_client":
        raise ApiError(
            "spotify_not_authenticated", 503,
            "The server's Spotify sign-in has lapsed; it needs a browser sign-in at /login.",
        )
    if outcome == "failed":
        raise ApiError("spotify_request_failed", 502, f"{what}: {outcome.error}")


def album_detail(conn, album_id, window, *, spotify_request=False, queue_unowned=False):
    if conn.execute("SELECT 1 FROM album WHERE album_id = ?", (album_id,)).fetchone() is None:
        return None
    if spotify_request:
        # Always fetches, even if fetched before (§4.4) -- the page's first-view
        # rule is the page's, not this parameter's.
        _outcome_error(entities.fetch_album_tracklist(conn, album_id), "album tracklist")
    if queue_unowned:
        entities.queue_wanted_uris(conn, album_id, source="album")

    row = conn.execute(
        "SELECT album_id, total_tracks, tracklist_json, tracklist_pulled_at "
        "FROM album WHERE album_id = ?", (album_id,),
    ).fetchone()
    tracklist, appended, total_duration, owned_ids = _tracklist_view(conn, row)
    summary = album_summaries(conn, [album_id])[album_id]
    return {
        **summary,
        "tracklist": tracklist,
        "tracklist_fetched_at": row["tracklist_pulled_at"],
        "tracklist_count": None if tracklist is None else len(tracklist),
        "appended": appended,
        "total_duration_ms": total_duration,
        **_entity_sections(conn, owned_ids, window),
    }


def artist_detail(conn, artist_id, window, *, spotify_request=False):
    artist_id = resolve_artist(conn, artist_id)
    if conn.execute("SELECT 1 FROM artist WHERE artist_id = ?", (artist_id,)).fetchone() is None:
        return None
    if spotify_request:
        _outcome_error(entities.fetch_artist_image(conn, artist_id), "artist image")

    summary = artist_summaries(conn, [artist_id])[artist_id]
    row = conn.execute(
        "SELECT detail_pulled_at FROM artist WHERE artist_id = ?", (artist_id,)
    ).fetchone()
    merged = [
        r[0] for r in conn.execute(
            "SELECT artist_id FROM artist_alias WHERE canonical_artist_id = ? ORDER BY artist_id",
            (artist_id,),
        )
    ]
    track_ids = coverage(conn, "artist", [artist_id])[artist_id]
    version_count = conn.execute(
        "SELECT COUNT(DISTINCT tg.version_id) FROM resolved_track_artist r "
        "JOIN track_group tg ON tg.track_id = r.track_id WHERE r.artist_id = ?", (artist_id,),
    ).fetchone()[0]
    album_ids = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT album_id FROM resolved_album_artist WHERE artist_id = ?", (artist_id,)
        )
    ]
    albums = sorted(
        album_summaries(conn, album_ids).values(),
        key=lambda a: (-(a["score"] or {}).get("all_time", 0.0), (a["name"] or "").casefold(), a["id"]),
    )
    return {
        **summary,
        "image_fetched_at": row["detail_pulled_at"],
        "merged_ids": merged,
        "version_count": version_count,
        "album_count": len(albums),
        "albums": albums,
        **_entity_sections(conn, track_ids, window),
    }


def playlist_detail(conn, playlist_id, window):
    row = conn.execute(
        "SELECT playlist_id, description, last_changed_at, tracks_pulled_at, unfollowed_at "
        "FROM snapshot WHERE playlist_id = ?", (playlist_id,),
    ).fetchone()
    if row is None:
        return None
    summary = playlist_summaries(conn, [playlist_id])[playlist_id]
    span = None
    if summary["generation"] is not None:
        span = next(
            (s for s in generations.generation_spans(conn) if s["ordinal"] == summary["generation"]),
            None,
        )
    track_ids = coverage(conn, "playlist", [playlist_id])[playlist_id]
    # A playlist has no `playlists` or `generations` section (§6.2).
    sections = _entity_sections(conn, track_ids, window, with_playlists=False, with_generations=False)
    return {
        **summary,
        "description": row["description"],
        "last_changed_at": row["last_changed_at"],
        "tracks_pulled_at": row["tracks_pulled_at"],
        "unfollowed_at": row["unfollowed_at"],
        "generation": (
            {"ordinal": summary["generation"], "started_at": span["started_at"],
             "ended_at": span["ended_at"]}
            if span is not None else None
        ),
        **sections,
    }


def detail(conn, type_, entity_id, window, *, spotify_request=False, queue_unowned=False):
    """The bare detail object for any of the eight types, or None on a miss."""
    if type_ == "track":
        return track_detail(conn, entity_id, window)
    if type_ == "album":
        return album_detail(conn, entity_id, window,
                            spotify_request=spotify_request, queue_unowned=queue_unowned)
    if type_ == "artist":
        return artist_detail(conn, entity_id, window, spotify_request=spotify_request)
    if type_ == "playlist":
        return playlist_detail(conn, entity_id, window)
    try:
        group_id = int(entity_id)
    except (TypeError, ValueError):
        return None
    return group_detail(conn, type_, group_id, window)


def playlist_items(conn, playlist_id, page, per_page, include_removed=False):
    """§6.3: a playlist's items in position order, paginated. Live rows only
    unless `include_removed`."""
    if conn.execute("SELECT 1 FROM snapshot WHERE playlist_id = ?", (playlist_id,)).fetchone() is None:
        return None
    where = "playlist_id = ?" + ("" if include_removed else " AND removed_at IS NULL")
    total = conn.execute(f"SELECT COUNT(*) FROM membership WHERE {where}", (playlist_id,)).fetchone()[0]
    rows = conn.execute(
        f"SELECT position, added_at, removed_at, track_id FROM membership WHERE {where} "
        "ORDER BY position, id LIMIT ? OFFSET ?",
        (playlist_id, per_page, (page - 1) * per_page),
    ).fetchall()
    tracks = track_summaries(conn, [r["track_id"] for r in rows])
    return {
        "items": [
            {"position": r["position"], "added_at": r["added_at"], "removed_at": r["removed_at"],
             "track": tracks.get(r["track_id"])}
            for r in rows
        ],
        "total": total, "page": page, "per_page": per_page,
    }


# -- Lists (§7) --------------------------------------------------------------------------

TRACKLIKE = ("track",) + GROUP_TIERS
_NO_PLAYLIST = tuple(t for t in TYPES if t != "playlist")

# Which types accept each filter / sort (§7.3, §7.4). api.py rejects a real one
# on a type it does not apply to as unsupported_filter / unsupported_sort.
FILTER_TYPES = {
    "q": TYPES, "q_contains": TYPES, "q_full": TYPES,
    "artist": TRACKLIKE + ("album",),
    "artist_role": TRACKLIKE,
    "album": TRACKLIKE,
    "playlist": TRACKLIKE,
    "generation": TRACKLIKE + ("album", "artist"),
    "min_ms": TRACKLIKE, "max_ms": TRACKLIKE,
    "year_from": TRACKLIKE + ("album",), "year_to": TRACKLIKE + ("album",),
    "explicit": TRACKLIKE,
    "min_memberships": _NO_PLAYLIST,
    "min_plays": TYPES,
}
SORT_TYPES = {
    "score": TYPES, "recent_score": TYPES, "plays": TYPES, "name": TYPES,
    "memberships": _NO_PLAYLIST, "generations": _NO_PLAYLIST, "tenure": _NO_PLAYLIST,
    "duration": TRACKLIKE,
    "release_date": TRACKLIKE + ("album",),
}


def _norm(text):
    # base_string deletes punctuation without spacing it ("half•alive" ->
    # "halfalive"), so a query with a space in it ("half alive") would never
    # match; dropping spaces from both sides is what makes it (Finn's call).
    return normalize.base_string(text or "").replace(" ", "")


def _name_cond(expr, mode, needle):
    if mode == "q":
        return f"substr(symr_norm({expr}), 1, {len(needle)}) = ?", [needle]
    if mode == "q_contains":
        return f"instr(symr_norm({expr}), ?) > 0", [needle]
    return f"symr_norm({expr}) = ?", [needle]


def _name_filter(f):
    for mode in ("q", "q_contains", "q_full"):
        if f.get(mode) is not None:
            return mode, _norm(f[mode])
    return None, None


def _candidate_ids(conn, type_, f):
    """The ids passing every filter that can run in SQL, before any sort."""
    conn.create_function("symr_norm", 1, _norm, deterministic=True)
    mode, needle = _name_filter(f)
    if mode is not None and needle == "":
        return []  # nothing searchable after normalizing -- matches nothing

    if type_ in TRACKLIKE:
        return _tracklike_ids(conn, type_, f, mode, needle)

    conds, params = ["1=1"], []
    if type_ == "album":
        if mode:
            c, p = _name_cond("a.name", mode, needle)
            conds.append(c); params += p
        if f.get("artist") is not None:
            conds.append("EXISTS (SELECT 1 FROM resolved_album_artist ra "
                         "WHERE ra.album_id = a.album_id AND ra.artist_id = ?)")
            params.append(resolve_artist(conn, f["artist"]))
        if f.get("generation") is not None:
            conds.append("EXISTS (SELECT 1 FROM track t JOIN generation_presence gp "
                         "ON gp.track_id = t.track_id WHERE t.album_id = a.album_id AND gp.ordinal = ?)")
            params.append(f["generation"])
        if f.get("year_from") is not None:
            conds.append("a.release_year >= ?"); params.append(f["year_from"])
        if f.get("year_to") is not None:
            conds.append("a.release_year <= ?"); params.append(f["year_to"])
        return [r[0] for r in conn.execute(
            f"SELECT a.album_id FROM album a WHERE {' AND '.join(conds)}", params)]

    if type_ == "artist":
        if mode:
            # The canonical artist or any alias id's name.
            c, p = _name_cond("x.name", mode, needle)
            conds.append(
                "ar.artist_id IN (SELECT COALESCE(aa.canonical_artist_id, x.artist_id) FROM artist x "
                f"LEFT JOIN artist_alias aa ON aa.artist_id = x.artist_id WHERE {c})"
            )
            params += p
        if f.get("generation") is not None:
            conds.append("EXISTS (SELECT 1 FROM resolved_track_artist r JOIN generation_presence gp "
                         "ON gp.track_id = r.track_id WHERE r.artist_id = ar.artist_id AND gp.ordinal = ?)")
            params.append(f["generation"])
        return [r[0] for r in conn.execute(
            "SELECT ar.artist_id FROM artist ar "
            "WHERE ar.artist_id NOT IN (SELECT artist_id FROM artist_alias) "
            f"AND {' AND '.join(conds)}", params)]

    # playlist: unfollowed never listed (W §9.14b)
    if mode:
        c, p = _name_cond("s.name", mode, needle)
        conds.append(c); params += p
    return [r[0] for r in conn.execute(
        f"SELECT s.playlist_id FROM snapshot s WHERE s.unfollowed_at IS NULL AND {' AND '.join(conds)}",
        params)]


def _tracklike_ids(conn, type_, f, mode, needle):
    """A group passes when any one member track passes every member-level
    filter (§7.3); min_ms/max_ms (the representative's) and year_* (the
    earliest member's) are group-level and applied after."""
    conds, params = ["1=1"], []
    if mode:
        c, p = _name_cond("t.name", mode, needle)
        conds.append(c); params += p
    if f.get("artist") is not None:
        artist = resolve_artist(conn, f["artist"])
        if f.get("artist_role") is not None:
            conds.append("EXISTS (SELECT 1 FROM track_artist_role r WHERE r.track_id = t.track_id "
                         "AND r.artist_id = ? AND r.role = ?)")
            params += [artist, f["artist_role"]]
        else:
            conds.append("EXISTS (SELECT 1 FROM resolved_track_artist r "
                         "WHERE r.track_id = t.track_id AND r.artist_id = ?)")
            params.append(artist)
    if f.get("album") is not None:
        conds.append("t.album_id = ?"); params.append(f["album"])
    if f.get("playlist") is not None:
        conds.append("EXISTS (SELECT 1 FROM membership m JOIN snapshot s ON s.playlist_id = m.playlist_id "
                     "WHERE m.track_id = t.track_id AND m.playlist_id = ? "
                     "AND m.removed_at IS NULL AND s.unfollowed_at IS NULL)")
        params.append(f["playlist"])
    if f.get("generation") is not None:
        conds.append("EXISTS (SELECT 1 FROM generation_presence gp "
                     "WHERE gp.track_id = t.track_id AND gp.ordinal = ?)")
        params.append(f["generation"])
    if f.get("explicit") is not None:
        conds.append("COALESCE(t.explicit, 0) = ?"); params.append(1 if f["explicit"] else 0)

    if type_ == "track":
        # A track's own duration and its own album's year.
        if f.get("min_ms") is not None:
            conds.append("t.duration_ms >= ?"); params.append(f["min_ms"])
        if f.get("max_ms") is not None:
            conds.append("t.duration_ms <= ?"); params.append(f["max_ms"])
        if f.get("year_from") is not None:
            conds.append("(SELECT release_year FROM album a WHERE a.album_id = t.album_id) >= ?")
            params.append(f["year_from"])
        if f.get("year_to") is not None:
            conds.append("(SELECT release_year FROM album a WHERE a.album_id = t.album_id) <= ?")
            params.append(f["year_to"])
        return [r[0] for r in conn.execute(
            f"SELECT t.track_id FROM track t WHERE {' AND '.join(conds)}", params)]

    column = canonical.TIER_COLUMN[type_]
    ids = {r[0] for r in conn.execute(
        f"SELECT DISTINCT tg.{column} FROM track t JOIN track_group tg ON tg.track_id = t.track_id "
        f"WHERE {' AND '.join(conds)}", params)}

    if f.get("year_from") is not None or f.get("year_to") is not None:
        having, hparams = [], []
        if f.get("year_from") is not None:
            having.append("MIN(a.release_year) >= ?"); hparams.append(f["year_from"])
        if f.get("year_to") is not None:
            having.append("MIN(a.release_year) <= ?"); hparams.append(f["year_to"])
        ids &= {r[0] for r in conn.execute(
            f"SELECT tg.{column} FROM track_group tg JOIN track t ON t.track_id = tg.track_id "
            f"JOIN album a ON a.album_id = t.album_id GROUP BY tg.{column} "
            f"HAVING {' AND '.join(having)}", hparams)}

    if f.get("min_ms") is not None or f.get("max_ms") is not None:
        reps = group_representatives(conn, type_, ids)
        dur = {r["track_id"]: r["duration_ms"] for r in conn.execute(
            "SELECT track_id, duration_ms FROM track")}
        keep = set()
        for gid in ids:
            d = dur.get(reps.get(gid))
            if d is None:
                continue
            if f.get("min_ms") is not None and d < f["min_ms"]:
                continue
            if f.get("max_ms") is not None and d > f["max_ms"]:
                continue
            keep.add(gid)
        ids = keep
    return list(ids)


class _SortKeys:
    """Sort values per entity, each computed once for the whole candidate set
    (a sort needs every entity's value before the page can be cut)."""

    def __init__(self, conn, type_, ids, window):
        self.conn, self.type_, self.ids, self.window = conn, type_, ids, window
        self.metrics = _Metrics(conn, window)
        self._cov = None
        self._cache = {}

    @property
    def cov(self):
        if self._cov is None:
            self._cov = coverage(self.conn, self.type_, self.ids)
        return self._cov

    def _all_tracks(self):
        return None if len(self.ids) > _IN_LIMIT else {t for ts in self.cov.values() for t in ts}

    def values(self, key):
        if key not in self._cache:
            self._cache[key] = getattr(self, "_v_" + key)()
        return self._cache[key]

    def _scores(self, horizon):
        s = scores(self.conn, self.type_, self.ids)
        return {i: (s[i][horizon] if s[i] is not None else float("-inf")) for i in self.ids}

    def _v_score(self):
        return self._scores("all_time")

    def _v_recent_score(self):
        return self._scores("recent")

    def _v_plays(self):
        by_track = self.metrics.plays_by_track(self._all_tracks())
        return {i: self.metrics.play_sort_value(self.cov[i], by_track) for i in self.ids}

    def _v_memberships(self):
        by_track = self.metrics.memberships_by_track(self._all_tracks())
        return {i: self.metrics.memberships(self.cov[i], by_track) for i in self.ids}

    def _facts(self):
        by_track = self.metrics.ordinals_by_track(self._all_tracks())
        return {i: generation_facts(self.metrics.ordinals(self.cov[i], by_track)) for i in self.ids}

    def _v_generations(self):
        return {i: f["total"] for i, f in self._facts().items()}

    def _v_tenure(self):
        return {i: f["tenure"] for i, f in self._facts().items()}

    def _reps(self):
        return group_representatives(self.conn, self.type_, self.ids)

    def _v_name(self):
        t = self.type_
        conn = self.conn
        if t == "track":
            table, col, idc = "track", "name", "track_id"
        elif t == "album":
            table, col, idc = "album", "name", "album_id"
        elif t == "artist":
            table, col, idc = "artist", "name", "artist_id"
        elif t == "playlist":
            table, col, idc = "snapshot", "name", "playlist_id"
        else:
            reps = self._reps()
            names = {r[0]: r[1] for r in conn.execute("SELECT track_id, name FROM track")}
            return {i: (names.get(reps.get(i)) or "").casefold() for i in self.ids}
        names = {r[0]: r[1] for r in conn.execute(f"SELECT {idc}, {col} FROM {table}")}
        return {i: (names.get(i) or "").casefold() for i in self.ids}

    def _v_duration(self):
        dur = {r[0]: r[1] for r in self.conn.execute("SELECT track_id, duration_ms FROM track")}
        if self.type_ == "track":
            return {i: dur.get(i) if dur.get(i) is not None else -1 for i in self.ids}
        reps = self._reps()
        return {i: dur.get(reps.get(i)) if dur.get(reps.get(i)) is not None else -1 for i in self.ids}

    def _v_release_date(self):
        if self.type_ == "album":
            dates = {r[0]: r[1] for r in self.conn.execute(
                "SELECT album_id, release_date_sortable FROM album")}
            return {i: dates.get(i) or "" for i in self.ids}
        if self.type_ == "track":
            rows = self.conn.execute(
                "SELECT t.track_id, a.release_date_sortable FROM track t "
                "LEFT JOIN album a ON a.album_id = t.album_id")
            dates = {r[0]: r[1] for r in rows}
            return {i: dates.get(i) or "" for i in self.ids}
        column = canonical.TIER_COLUMN[self.type_]
        dates = {}
        for r in self.conn.execute(
            f"SELECT tg.{column}, MIN(a.release_date_sortable) FROM track_group tg "
            "JOIN track t ON t.track_id = tg.track_id JOIN album a ON a.album_id = t.album_id "
            f"GROUP BY tg.{column}"
        ):
            dates[r[0]] = r[1]
        return {i: dates.get(i) or "" for i in self.ids}


def entity_metrics(conn, type_, ids, window):
    """{id: the §7.1 metrics object} for `ids` -- the page slice only."""
    ids = list(ids)
    metrics = _Metrics(conn, window)
    cov = coverage(conn, type_, ids)
    all_tracks = list({t for ts in cov.values() for t in ts})
    plays = metrics.plays_by_track(all_tracks)
    mem = metrics.memberships_by_track(all_tracks) if type_ != "playlist" else None
    ords = metrics.ordinals_by_track(all_tracks) if type_ != "playlist" else None
    out = {}
    for i in ids:
        m = {"plays": metrics.plays(cov[i], plays)}
        if type_ != "playlist":
            facts = generation_facts(metrics.ordinals(cov[i], ords))
            m["memberships"] = metrics.memberships(cov[i], mem)
            m["generations"] = facts["total"]
            m["tenure"] = facts["tenure"]
        out[i] = m
    return out


def list_entities(conn, type_, filters, sort, window, page, per_page):
    """§7: one type's filtered, sorted, paginated list. `filters` is the
    already-validated {name: value} dict; `sort` a list of (key, descending)."""
    ids = _candidate_ids(conn, type_, filters)

    # Metric filters run on the SQL-filtered set, computed set-based for it.
    if filters.get("min_plays") is not None or filters.get("min_memberships") is not None:
        keys = _SortKeys(conn, type_, ids, window)
        if filters.get("min_plays") is not None:
            plays = keys.values("plays")
            ids = [i for i in ids if plays[i] >= filters["min_plays"]]
            keys = _SortKeys(conn, type_, ids, window)
        if filters.get("min_memberships") is not None:
            mem = keys.values("memberships")
            ids = [i for i in ids if mem[i] >= filters["min_memberships"]]

    keys = _SortKeys(conn, type_, ids, window)
    ordered = sorted(ids)  # the §5.5 tie-breaker: id ascending, last in line
    # One stable pass per key, last key first -- reverse=True keeps equal
    # elements in their previous order, so each earlier key re-sorts over it.
    for key, descending in reversed(sort):
        values = keys.values(key)
        ordered.sort(key=values.__getitem__, reverse=descending)

    start = (page - 1) * per_page
    page_ids = ordered[start:start + per_page]
    summ = summaries(conn, type_, page_ids)
    metrics = entity_metrics(conn, type_, [i for i in page_ids if i in summ], window)
    return {
        "items": [{**summ[i], "metrics": metrics[i]} for i in page_ids if i in summ],
        "total": len(ordered), "page": page, "per_page": per_page,
    }


# -- Search (§8) ----------------------------------------------------------------------------

_SEARCH_TYPE = {"songs": "version", "albums": "album", "artists": "artist", "playlists": "playlist"}


def _search_items(conn, *ranked_lists):
    """One list of summaries per ranked list, each in its own order, for rows of
    {"type": plural, "id": ...}. Built in a single pass per type across all the
    lists given: the sections and Most Relevant overlap, and a summary costs a
    score lookup."""
    by_type = defaultdict(list)
    for ranked in ranked_lists:
        for r in ranked:
            by_type[_SEARCH_TYPE[r["type"]]].append(r["id"])
    built = {t: summaries(conn, t, ids) for t, ids in by_type.items()}
    return [
        [s for s in (built[_SEARCH_TYPE[r["type"]]].get(r["id"]) for r in ranked) if s is not None]
        for ranked in ranked_lists
    ]


def search_results(conn, q, type_=None, page=1, per_page=DEFAULT_PER_PAGE):
    """The same matcher and ranking as /search (search.rank); the JSON
    replaces the HTML fragments. Songs rank at the version tier."""
    ranked = search.rank(conn, q)
    if type_ is not None:
        rows = ranked[type_]
        start = (page - 1) * per_page
        (items,) = _search_items(conn, rows[start:start + per_page])
        return {"items": items, "total": len(rows), "page": page, "per_page": per_page}
    slices = [ranked["combined"][:search.COMBINED_LIMIT]] + [
        ranked[t][:search.SECTION_LIMIT] for t in search.TYPES
    ]
    built = _search_items(conn, *slices)
    out = {"q": q, "most_relevant": built[0]}
    for t, items in zip(search.TYPES, built[1:]):
        out[t] = {"items": items, "total": len(ranked[t])}
    return out


# -- Plays and the timeline (§9.1, §9.2) ----------------------------------------------------

PLAY_ENTITY_FILTERS = ("track", "song", "version", "recording", "release", "album", "artist", "playlist")


def _plays_from(conn, entity, window, source):
    """(FROM/WHERE sql, params) for the play filters. Resolution goes through
    played_uri_track, so relinked uris count and an unresolved play cannot
    match an entity filter."""
    joins = "LEFT JOIN played_uri_track x ON x.uri = p.spotify_track_uri"
    where, params = "1=1", []
    if entity is not None:
        kind, value = entity
        joins = "JOIN played_uri_track x ON x.uri = p.spotify_track_uri"
        if kind == "track":
            where += " AND x.track_id = ?"; params.append(value)
        elif kind in GROUP_TIERS:
            joins += " JOIN track_group tg ON tg.track_id = x.track_id"
            where += f" AND tg.{canonical.TIER_COLUMN[kind]} = ?"; params.append(value)
        elif kind == "album":
            joins += " JOIN track t ON t.track_id = x.track_id"
            where += " AND t.album_id = ?"; params.append(value)
        elif kind == "artist":
            where += (" AND EXISTS (SELECT 1 FROM resolved_track_artist r "
                      "WHERE r.track_id = x.track_id AND r.artist_id = ?)")
            params.append(resolve_artist(conn, value))
        else:
            where += (" AND EXISTS (SELECT 1 FROM membership m JOIN snapshot s "
                      "ON s.playlist_id = m.playlist_id WHERE m.track_id = x.track_id "
                      "AND m.playlist_id = ? AND m.removed_at IS NULL AND s.unfollowed_at IS NULL)")
            params.append(value)
    if source is not None:
        where += " AND p.source = ?"; params.append(source)
    bounds, bparams = _play_bounds_sql(window)
    return f"FROM play p {joins} WHERE {where}{bounds}", params + bparams


def plays_list(conn, entity, source, window, ascending, page, per_page):
    """§9.1: individual plays, newest first unless `ascending`. `ip_addr` is
    never selected."""
    from_sql, params = _plays_from(conn, entity, window, source)
    total = conn.execute(f"SELECT COUNT(*) {from_sql}", params).fetchone()[0]
    order = "ASC" if ascending else "DESC"
    rows = conn.execute(
        "SELECT p.id, p.ts, p.ms_played, p.source, p.spotify_track_uri, p.reported_track_name, "
        "p.reported_artist_name, p.reported_album_name, p.reason_start, p.reason_end, p.shuffle, "
        "p.skipped, p.platform, p.conn_country, p.offline, p.offline_ts, p.incognito_mode, "
        f"x.track_id {from_sql} ORDER BY p.ts {order}, p.id {order} LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    tracks = track_summaries(conn, [r["track_id"] for r in rows if r["track_id"]])
    return {
        "items": [
            {
                "id": r["id"], "ts": r["ts"], "ms_played": r["ms_played"], "source": r["source"],
                "track": tracks.get(r["track_id"]),
                "spotify_track_uri": r["spotify_track_uri"],
                "reported": {
                    "track_name": r["reported_track_name"],
                    "artist_name": r["reported_artist_name"],
                    "album_name": r["reported_album_name"],
                },
                "reason_start": r["reason_start"], "reason_end": r["reason_end"],
                "shuffle": _bool(r["shuffle"]), "skipped": _bool(r["skipped"]),
                "platform": r["platform"], "conn_country": r["conn_country"],
                "offline": _bool(r["offline"]), "offline_ts": r["offline_ts"],
                "incognito_mode": _bool(r["incognito_mode"]),
            }
            for r in rows
        ],
        "total": total, "page": page, "per_page": per_page,
    }


def _bucket_start(day, bucket):
    if bucket == "day":
        return day
    if bucket == "week":
        return day - timedelta(days=day.weekday())  # Mondays
    return day.replace(day=1)


def _next_bucket(start, bucket):
    if bucket == "day":
        return start + timedelta(days=1)
    if bucket == "week":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def plays_timeline(conn, entity, source, window, bucket):
    """§9.2: play and ms_played totals per day/week/month, empty buckets
    included as zeros. A bucket's boundaries are real local midnights converted
    to UTC, so a DST change inside a week cannot shift a play across them."""
    from_sql, params = _plays_from(conn, entity, window, source)
    plays = conn.execute(f"SELECT p.ts, p.ms_played {from_sql} ORDER BY p.ts", params).fetchall()
    data_through = conn.execute("SELECT MAX(ts) FROM play").fetchone()[0]
    tz = window.tz

    def local_day(ts_utc):
        moment = datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return moment.astimezone(tz).date()

    # The range: `from` or the first matching play, to just before `to` or
    # the end of the play data (an exclusive upper bound's last instant belongs
    # to the day before it). Zero-filled even when nothing matched in it.
    first = local_day(window.lo) if window.lo else (local_day(plays[0]["ts"]) if plays else None)
    if window.hi:
        last = local_day(_utc_str(
            datetime.strptime(window.hi, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            - timedelta(seconds=1)))
    else:
        last = local_day(data_through) if data_through else None
    buckets = []
    if first is not None and last is not None:
        start = _bucket_start(first, bucket)
        while start <= last:
            buckets.append(start)
            start = _next_bucket(start, bucket)

    # Each boundary is the UTC instant of that bucket's first local midnight,
    # plus one past the last bucket; a play's bucket is where it falls between.
    bounds = [
        _utc_str(datetime(b.year, b.month, b.day, tzinfo=tz)) for b in buckets
    ]
    if buckets:
        end = _next_bucket(buckets[-1], bucket)
        bounds.append(_utc_str(datetime(end.year, end.month, end.day, tzinfo=tz)))
    counts = [0] * len(buckets)
    ms = [0] * len(buckets)
    for p in plays:
        idx = bisect.bisect_right(bounds, p["ts"]) - 1
        if 0 <= idx < len(buckets):
            counts[idx] += 1
            ms[idx] += p["ms_played"]
    return {
        "bucket": bucket, "tz": window.tz_name, "from": window.lo, "to": window.hi,
        "buckets": [
            {"start": b.isoformat(), "plays": counts[i], "ms_played": ms[i]}
            for i, b in enumerate(buckets)
        ],
    }


# -- Generations (§9.3) -------------------------------------------------------------------------


def generations_list(conn, tier="version"):
    """Every generation in ordinal order, with the counts generations.py
    derives. carried/new are at `tier`; version_count/song_count are fixed."""
    at_tier = generations.generations(conn, tier=tier)
    by_version = {g["ordinal"]: g for g in generations.generations(conn, tier="version")}
    by_song = {g["ordinal"]: g for g in generations.generations(conn, tier="song")}
    playlists = playlist_summaries(conn, [g["playlist_id"] for g in at_tier])
    return [
        {
            "ordinal": g["ordinal"], "name": g["name"], "playlist": playlists.get(g["playlist_id"]),
            "started_at": g["started_at"], "ended_at": g["ended_at"],
            "version_count": by_version[g["ordinal"]]["group_count"],
            "song_count": by_song[g["ordinal"]]["group_count"],
            "carried": g["carried_in"], "new": g["new_in"],
        }
        for g in at_tier
    ]


def generation_detail(conn, ordinal, tier="version"):
    """One generation's row plus every group present in it, labelled carried or
    new by the rule generations.generation_view uses, ordered by score."""
    row = next((g for g in generations_list(conn, tier) if g["ordinal"] == ordinal), None)
    if row is None:
        return None
    column = generations.TIER_COLUMN[tier]
    spans = generations.generation_spans(conn)
    idx = next(i for i, s in enumerate(spans) if s["ordinal"] == ordinal)

    def groups_at(o):
        return {r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM generation_presence WHERE ordinal = ?", (o,))}

    this_groups = groups_at(ordinal)
    prev_groups = groups_at(spans[idx - 1]["ordinal"]) if idx > 0 else set()
    next_groups = groups_at(spans[idx + 1]["ordinal"]) if idx + 1 < len(spans) else None
    summ = group_summaries(conn, tier, this_groups)
    items = [
        {"group": summ[g], "label": "carried" if g in prev_groups else "new"}
        for g in this_groups if g in summ
    ]
    items.sort(key=lambda i: (
        i["group"]["score"] is None, -(i["group"]["score"] or {}).get("all_time", 0.0), i["group"]["id"]))
    return {
        **row,
        "tier": tier,
        "survived_out": len(this_groups & next_groups) if next_groups is not None else None,
        "items": items,
    }


def pending_generation(conn):
    """§9.3: the pending new-generation prompt as {playlist, name}, or None."""
    pending = generations.pending_new_generation(conn)
    if pending is None:
        return None
    playlist = playlist_summaries(conn, [pending["playlist_id"]]).get(pending["playlist_id"])
    return {"playlist": playlist, "name": pending["name"]}


# -- Status (§10) ---------------------------------------------------------------------------------


def status(conn):
    """Data freshness: none of it sensitive -- no tokens, no error text."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    recompute = scoring.recompute_status()
    last_poll = conn.execute(
        "SELECT started_at FROM scrobble_poll ORDER BY id DESC LIMIT 1").fetchone()
    counts = api_log.request_counts(conn)
    tiers = canonical.tier_counts(conn)
    return {
        "spotify_authenticated": get_spotify_client() is not None,
        "active_job": jobs.active(),
        "last_pull_at": one("SELECT MAX(tracks_pulled_at) FROM snapshot"),
        "scrobble": {
            "enabled": db.get_meta(conn, "scrobble_enabled") != "0",
            "last_poll_at": last_poll[0] if last_poll else None,
            "next_poll_at": scrobble.next_wake_at(),
        },
        "plays": {
            "data_through": one("SELECT MAX(ts) FROM play"),
            "export_through": one("SELECT MAX(ts) FROM play WHERE source = 'export'"),
            "last_import_at": one("SELECT MAX(uploaded_at) FROM play_import WHERE error IS NULL"),
        },
        "scoring": {"last_recompute_at": recompute["finished_at"], "outcome": recompute["outcome"]},
        "pending_generation": pending_generation(conn),
        "spotify_requests": {"past_24h": counts["last_24h"], "past_7d": counts["last_7d"]},
        "library": {
            "tracks": one("SELECT COUNT(*) FROM track"),
            "albums": one("SELECT COUNT(*) FROM album"),
            "artists": one("SELECT COUNT(*) FROM artist "
                           "WHERE artist_id NOT IN (SELECT artist_id FROM artist_alias)"),
            "playlists": one("SELECT COUNT(*) FROM snapshot WHERE unfollowed_at IS NULL"),
            **{PLURAL[t]: tiers[t]["total"] for t in GROUP_TIERS},
            "plays": one("SELECT COUNT(*) FROM play"),
            "generations": one("SELECT COUNT(*) FROM generation"),
        },
    }


# -- The three writes (§11) -------------------------------------------------------------------------


def set_playlist_excluded(conn, playlist_id, excluded):
    """Flips snapshot.excluded (exclusion affects pulls, not scores, so no
    recompute). set_excluded commits."""
    if conn.execute("SELECT 1 FROM snapshot WHERE playlist_id = ?", (playlist_id,)).fetchone() is None:
        return None
    snapshot.set_excluded(conn, [playlist_id], excluded)
    return playlist_summaries(conn, [playlist_id])[playlist_id]


def _require_pending(conn, playlist_id):
    pending = generations.pending_new_generation(conn)
    if pending is None:
        raise ApiError("invalid_parameter", 400, "no generation is pending")
    if pending["playlist_id"] != playlist_id:
        raise ApiError("invalid_parameter", 400, "playlist_id is not the pending generation's playlist")


def confirm_pending_generation(conn, playlist_id, tier="version"):
    """Confirms the pending generation. Only a new generation is a scoring
    input, so this recomputes inline (the caller clicked once and is waiting
    for the outcome) -- commit first, then recompute."""
    _require_pending(conn, playlist_id)
    generations.confirm_generation(conn, playlist_id)
    conn.commit()
    scoring.recompute(conn)
    return generations_list(conn, tier)


def decline_pending_generation(conn, playlist_id, tier="version"):
    """Mutes the prompt for that playlist; touches nothing scoring reads, so no
    recompute."""
    _require_pending(conn, playlist_id)
    generations.decline_generation(conn, playlist_id)
    conn.commit()
    return generations_list(conn, tier)


def pin_song_representative(conn, song_id, track_id):
    """Pins `track_id` as the song's representative. pin_representative derives
    the song from the track, so membership is checked here -- or a pin aimed at
    one song would silently land on another. Async recompute (working a queue),
    requested after the commit."""
    if conn.execute(
        "SELECT 1 FROM canonical_group WHERE id = ? AND tier = 'song'", (song_id,)
    ).fetchone() is None:
        return None
    member = conn.execute(
        "SELECT 1 FROM track_group WHERE track_id = ? AND song_id = ?", (track_id, song_id)
    ).fetchone()
    if member is None:
        raise ApiError("invalid_parameter", 400, "track_id is not a member of this song")
    canonical.pin_representative(conn, track_id)
    conn.commit()
    scoring.request_recompute()
    return group_summaries(conn, "song", [song_id])[song_id]
