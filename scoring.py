"""Scoring (docs/specs/scoring-H.md): a general score ranking version groups,
and by aggregation songs, albums, artists, playlists and any arbitrary
collection of tracks, on one absolute scale.

The version group is the atom (§3.2): `recompute()` computes weighted plays,
play rate, membership count and tenure per version, saturates and weights
them, then shrinks toward a bucket baseline -- both horizons -- and
materializes the result into the `score` table, along with recording/
release/track's own lightly-blended variants (§6). Song, album, artist,
playlist and arbitrary collections are **not** materialized; they aggregate
at query time from the stored version scores via `combine()`, the one
power-mean function every collection type shares (§5).

docs/scoring/tuning_prototype.py is the executable reference this module
must reproduce (§0.2, §12) -- run it, and compare its printed validation-set
order and version-score percentiles against this module's output. If the two
disagree, one of them is wrong and the difference must be resolved
deliberately, never by re-tuning until the numbers look nice.

Read-only w.r.t. Spotify -- no calls here, like everything except
roundtrip.py."""

import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import canonical
import generations
from config import DB_PATH

# ============================================================================
# PARAMETERS -- docs/specs/scoring-H.md §10.1. Every value here was tuned in
# the planning session against the real DB; docs/scoring/tuning_prototype.py
# is the executable record of *why*. THESE ARE NOT LIVE DIALS: changing any
# one of them invalidates every row already in the `score` table, because two
# rows would then reflect two different algorithms. After editing a value,
# call recompute() immediately (or use the "Recompute scores" button on
# /dev/scoring) -- if you forget, the §9.3 read-time backstop will eventually
# catch the drift and recompute on its own, but only after paying its
# fingerprint check on some later request.
# ============================================================================

MIN_EXPOSURE_DAYS = 14
K_RATE = 0.5          # half-value for R, plays per 30 days
K_MEM = 4.0            # half-value for M, live membership count
K_TEN = 2.0            # half-value for T, distinct generations present in
W_RATE = 0.40          # structural, not fitted -- see spec §4.4
W_MEM = 0.35
W_TEN = 0.25
K_SHRINK = 3.0
SHRINK_MAX = 0.5
P_AGG = 2.5            # combine()'s power-mean exponent -- a ceiling, §5.2
TAIL_FLOOR = 1.0        # deliberately inert -- see combine()'s docstring
FEATURED_WEIGHT = 0.6   # collection-membership weight for a featured-only credit
SUBTIER_W = 0.05        # recording/release/track's own-score blend, §6
RECENT_WINDOW_DAYS = 90
RECENT_ALLTIME_BLEND = 0.15
SCALE = 100.0
GAMMA = 0.5

# The four materialized tiers (§9.1). "song" is deliberately absent -- it
# aggregates at query time like album/artist/playlist, it is not stored.
_SUBTIER_COLUMN = {"recording": "recording_id", "release": "release_id", "track": "track_id"}


# ---------------------------------------------------------------- core math


def _sat(x, k):
    """x/(x+k) -- the saturating transform (§4.4). Bounded by construction
    (no reference maximum needed), k is the interpretable half-value, and it
    never fully saturates so ordering survives in the tail."""
    return x / (x + k)


def _raw(inputs):
    """§4.4's weighted sum of the three saturated terms -- shared by version
    scoring (feeding §4.5's shrinkage) and by the subtier own-score (used
    unshrunk, see _fetch_own_inputs's docstring)."""
    return (
        W_RATE * _sat(inputs["R"], K_RATE)
        + W_MEM * _sat(inputs["M"], K_MEM)
        + W_TEN * _sat(inputs["T"], K_TEN)
    )


def combine(scores, weights=None):
    """The weighted power mean (§5.1), over NORMALIZED [0,1) scores -- the
    one aggregator every collection (song, album, artist, playlist, an
    ad-hoc bag of tracks) shares. `weights` is u_i, the collection-membership
    weight (§5.3, e.g. FEATURED_WEIGHT); the tail weight w_i is derived from
    the scores themselves (§5.2).

    TAIL_FLOOR=1.0 makes that derivation inert by design -- tuning found
    lowering it makes every ranking worse, since every real collection has a
    tail. It stays a real parameter rather than being simplified away because
    a future case may need it, but it is not a live dial: don't move it
    without evidence the specific failure it addresses is actually
    happening.

    Callers holding materialized (display-space) scores must _undisplay()
    them first -- this function only ever operates in normalized space
    (§8)."""
    if not scores:
        return 0.0
    peak = max(scores) or 1.0
    tail_weights = [
        max(s / peak, TAIL_FLOOR) * (weights[i] if weights else 1.0)
        for i, s in enumerate(scores)
    ]
    total = sum(tail_weights)
    if total == 0:
        return 0.0
    return (
        sum(tail_weights[i] * scores[i] ** P_AGG for i in range(len(scores))) / total
    ) ** (1 / P_AGG)


def _display(x):
    """The one monotonic transform to the displayed number (§8), applied
    exactly once at the very end of whatever computation produced x."""
    return SCALE * (max(x, 0.0) ** GAMMA)


def _undisplay(y):
    """Inverts _display(). Materialized rows store display-space numbers
    (§9.1), but combine() must run in normalized space (§8) -- aggregation
    therefore undisplays each materialized input, combines, and redisplays
    once. Monotonic, so this round-trip changes no ordering; it only
    recovers the space the power mean is defined in."""
    return (max(y, 0.0) / SCALE) ** (1 / GAMMA)


# ---------------------------------------------------------------- version-tier inputs


def _recent_ordinals(conn, now):
    """Generation ordinals whose span *began* within the recency window
    (§7.1 -- "began within", not "overlaps"). generations.generation_spans()
    is the one place a generation's start is defined, so this reads through
    it rather than re-deriving "started_at" a second time."""
    win = (now - timedelta(days=RECENT_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    spans = generations.generation_spans(conn)
    return [s["ordinal"] for s in spans if s["started_at"] and s["started_at"] >= win]


# §4.2: a play's contribution is min(ms_played/duration_ms, 1.0); a track
# with no/zero duration contributes its raw play at weight 1.0 rather than
# dividing by zero. Written as an explicit CASE rather than
# MIN(x/NULLIF(duration,0), 1.0): SQLite's scalar MIN returns NULL if *any*
# argument is NULL, and SUM() silently drops NULL rows -- that reads as
# "contributes nothing", the opposite of the spec's rule.
_PLAY_WEIGHT_SQL = (
    "CASE WHEN t.duration_ms IS NULL OR t.duration_ms = 0 THEN 1.0 "
    "ELSE MIN(p.ms_played * 1.0 / t.duration_ms, 1.0) END"
)


def _first_opportunity_days(now, recent, win, fo):
    """§4.3's exposure E, in days: MIN_EXPOSURE_DAYS when there's no first
    opportunity at all, otherwise days since it (clamped to the window's
    start on the recent horizon, per §7.1's "Exposure E: ... clamped to the
    window")."""
    if recent and fo != "9999" and fo < win:
        fo = win
    if fo == "9999":
        return MIN_EXPOSURE_DAYS
    return max((now - datetime.fromisoformat(fo)).days, MIN_EXPOSURE_DAYS)


def _fetch_version_inputs(conn, recent, now, recent_ordinals):
    """Per-version-group raw inputs {version_id: {W, M, T, R, bucket}} for one
    horizon. Mirrors docs/scoring/tuning_prototype.py's fetch() -- that
    script is the executable reference (§0.2, §12); an implementation that
    disagrees with it is wrong, or the prototype is."""
    win = (now - timedelta(days=RECENT_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    play_filter = "AND p.ts >= ?" if recent else ""
    added_filter = "AND m.added_at >= ?" if recent else ""
    if recent:
        ord_placeholders = ",".join("?" for _ in recent_ordinals) or "NULL"
        gen_filter = f"AND gp.ordinal IN ({ord_placeholders})"
    else:
        gen_filter = ""

    query = f"""
    WITH mem AS (
        SELECT tg.version_id, COUNT(*) AS n FROM membership m
        JOIN track_group tg ON tg.track_id = m.track_id
        WHERE m.removed_at IS NULL {added_filter}
        GROUP BY tg.version_id
    ), ten AS (
        SELECT gp.version_id, COUNT(DISTINCT gp.ordinal) AS n FROM generation_presence gp
        WHERE 1 = 1 {gen_filter}
        GROUP BY gp.version_id
    ), pl AS (
        SELECT tg.version_id, SUM({_PLAY_WEIGHT_SQL}) AS w, MIN(p.ts) AS fp FROM play p
        JOIN played_uri_track x ON x.uri = p.spotify_track_uri
        JOIN track t ON t.track_id = x.track_id
        JOIN track_group tg ON tg.track_id = t.track_id
        WHERE 1 = 1 {play_filter}
        GROUP BY tg.version_id
    ), ad AS (
        SELECT tg.version_id, MIN(m.added_at) AS a FROM membership m
        JOIN track_group tg ON tg.track_id = m.track_id
        GROUP BY tg.version_id
    ), mem_all AS (
        SELECT tg.version_id, COUNT(*) AS n FROM membership m
        JOIN track_group tg ON tg.track_id = m.track_id
        WHERE m.removed_at IS NULL
        GROUP BY tg.version_id
    ), ten_all AS (
        SELECT version_id, COUNT(DISTINCT ordinal) AS n FROM generation_presence
        GROUP BY version_id
    )
    SELECT DISTINCT tg.version_id AS group_id,
           COALESCE(mem.n, 0) AS m, COALESCE(ten.n, 0) AS t, COALESCE(pl.w, 0.0) AS w,
           COALESCE(mem_all.n, 0) AS m_all, COALESCE(ten_all.n, 0) AS t_all,
           MIN(COALESCE(pl.fp, '9999'), COALESCE(ad.a, '9999')) AS fo
    FROM track_group tg
    LEFT JOIN mem ON mem.version_id = tg.version_id
    LEFT JOIN ten ON ten.version_id = tg.version_id
    LEFT JOIN pl ON pl.version_id = tg.version_id
    LEFT JOIN ad ON ad.version_id = tg.version_id
    LEFT JOIN mem_all ON mem_all.version_id = tg.version_id
    LEFT JOIN ten_all ON ten_all.version_id = tg.version_id
    """
    params = []
    if recent:
        params.append(win)  # mem's added_filter
        params.extend(recent_ordinals)  # ten's gen_filter
        params.append(win)  # pl's play_filter

    out = {}
    for row in conn.execute(query, params):
        exposure_days = _first_opportunity_days(now, recent, win, row["fo"])
        w = row["w"]
        out[row["group_id"]] = {
            "W": w,
            "M": row["m"],
            "T": row["t"],
            "R": w / exposure_days * 30,
            "bucket": "C" if row["t_all"] > 0 else ("B" if row["m_all"] > 0 else "A"),
        }
    return out


def _score_all(rows):
    """raw (§4.4) -> bucket baselines (median inputs, scored once, §4.5) ->
    bounded shrinkage. Returns (scores, baselines), both normalized [0,1)."""
    for v in rows.values():
        v["raw"] = _raw(v)

    baselines = {}
    for bucket in "ABC":
        members = [v for v in rows.values() if v["bucket"] == bucket]
        if not members:
            baselines[bucket] = 0.0
            continue
        synthetic = {key: statistics.median([v[key] for v in members]) for key in ("R", "M", "T")}
        baselines[bucket] = _raw(synthetic)

    scores = {}
    for group_id, v in rows.items():
        pull = min(K_SHRINK / (v["W"] + K_SHRINK), SHRINK_MAX)
        scores[group_id] = v["raw"] + pull * (baselines[v["bucket"]] - v["raw"])
    return scores, baselines


def _version_horizons(conn, now, recent_ordinals):
    """Both horizons' normalized version-tier scores, with recent blended
    toward all_time (§7.1a) so the 86% of the library with zero recent
    activity gets a sensible internal order instead of one flat tie."""
    all_rows = _fetch_version_inputs(conn, False, now, recent_ordinals)
    recent_rows = _fetch_version_inputs(conn, True, now, recent_ordinals)
    all_scores, _all_baselines = _score_all(all_rows)
    recent_windowed, _recent_baselines = _score_all(recent_rows)

    recent_scores = {
        vid: (1 - RECENT_ALLTIME_BLEND) * recent_windowed.get(vid, 0.0) + RECENT_ALLTIME_BLEND * s
        for vid, s in all_scores.items()
    }
    return all_scores, recent_scores


# ---------------------------------------------------------------- subtier (§6)


def _fetch_own_inputs(conn, tier, recent, now, recent_ordinals):
    """§6's "own track set" inputs at a finer tier than version -- same shape
    as _fetch_version_inputs but grouped by recording_id/release_id/track_id,
    and without the bucket/M_all/T_all machinery: resolved during
    implementation (spec was silent on how much of §4 "own" re-runs) that
    the own-score stays raw only (§4.4), never independently shrunk. §10.1
    already records SUBTIER_W as unvalidated with a blast radius limited to
    representative-track tie-breaking (§11.3), so a second full
    bucket-baseline system per tier isn't worth the cost for that.
    Returns {group_id: {R, M, T}}."""
    column = _SUBTIER_COLUMN[tier]
    win = (now - timedelta(days=RECENT_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    play_filter = "AND p.ts >= ?" if recent else ""
    added_filter = "AND m.added_at >= ?" if recent else ""
    if recent:
        ord_placeholders = ",".join("?" for _ in recent_ordinals) or "NULL"
        gen_filter = f"AND gp.ordinal IN ({ord_placeholders})"
    else:
        gen_filter = ""

    query = f"""
    WITH mem AS (
        SELECT tg.{column} AS group_id, COUNT(*) AS n FROM membership m
        JOIN track_group tg ON tg.track_id = m.track_id
        WHERE m.removed_at IS NULL {added_filter}
        GROUP BY tg.{column}
    ), ten AS (
        SELECT tg.{column} AS group_id, COUNT(DISTINCT gp.ordinal) AS n
        FROM generation_presence gp
        JOIN track_group tg ON tg.track_id = gp.track_id
        WHERE 1 = 1 {gen_filter}
        GROUP BY tg.{column}
    ), pl AS (
        SELECT tg.{column} AS group_id, SUM({_PLAY_WEIGHT_SQL}) AS w, MIN(p.ts) AS fp FROM play p
        JOIN played_uri_track x ON x.uri = p.spotify_track_uri
        JOIN track t ON t.track_id = x.track_id
        JOIN track_group tg ON tg.track_id = t.track_id
        WHERE 1 = 1 {play_filter}
        GROUP BY tg.{column}
    ), ad AS (
        SELECT tg.{column} AS group_id, MIN(m.added_at) AS a FROM membership m
        JOIN track_group tg ON tg.track_id = m.track_id
        GROUP BY tg.{column}
    )
    SELECT DISTINCT tg.{column} AS group_id,
           COALESCE(mem.n, 0) AS m, COALESCE(ten.n, 0) AS t, COALESCE(pl.w, 0.0) AS w,
           MIN(COALESCE(pl.fp, '9999'), COALESCE(ad.a, '9999')) AS fo
    FROM track_group tg
    LEFT JOIN mem ON mem.group_id = tg.{column}
    LEFT JOIN ten ON ten.group_id = tg.{column}
    LEFT JOIN pl ON pl.group_id = tg.{column}
    LEFT JOIN ad ON ad.group_id = tg.{column}
    """
    params = []
    if recent:
        params.append(win)
        params.extend(recent_ordinals)
        params.append(win)

    out = {}
    for row in conn.execute(query, params):
        exposure_days = _first_opportunity_days(now, recent, win, row["fo"])
        out[row["group_id"]] = {"R": row["w"] / exposure_days * 30, "M": row["m"], "T": row["t"]}
    return out


def _own_tier_to_version(conn, tier):
    """{group_id: version_id} for a finer tier -- which version's score each
    recording/release/track blends toward (§6)."""
    column = _SUBTIER_COLUMN[tier]
    return {
        row["group_id"]: row["version_id"]
        for row in conn.execute(f"SELECT DISTINCT {column} AS group_id, version_id FROM track_group")
    }


def _subtier_scores(conn, tier, now, recent_ordinals, version_all, version_recent):
    """§6: score(x) = (1-SUBTIER_W)*score(version(x)) + SUBTIER_W*score_own(x),
    both horizons, in normalized space. Returns (all_time_scores,
    recent_scores) for the given finer tier."""
    version_of = _own_tier_to_version(conn, tier)

    def blend(own_rows, version_scores):
        out = {}
        for group_id, inputs in own_rows.items():
            vid = version_of.get(group_id)
            v_score = version_scores.get(vid, 0.0) if vid is not None else 0.0
            out[group_id] = (1 - SUBTIER_W) * v_score + SUBTIER_W * _raw(inputs)
        return out

    own_all = _fetch_own_inputs(conn, tier, False, now, recent_ordinals)
    own_recent = _fetch_own_inputs(conn, tier, True, now, recent_ordinals)
    return blend(own_all, version_all), blend(own_recent, version_recent)


# ---------------------------------------------------------------- recompute (§9)


def recompute(conn):
    """Whole-library recompute (§9.2): clear `score` and re-insert every
    version/recording/release/track row, both horizons, in one transaction.
    ~1s (§2.11). Idempotent -- a second run with no intervening writes
    produces the identical table.

    Called explicitly at the end of every job/write path that touches a
    scoring input, and as a catch-all by the read-time backstop
    (ensure_fresh()) below for any path that isn't. Records its own outcome
    for /dev/scoring via recompute_status() either way -- a failure is
    re-raised afterward, so the status is an extra receipt, never the only
    place the error surfaces."""
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.monotonic()
    try:
        canonical.ensure_track_groups(conn)
        conn.commit()

        now = datetime.now(timezone.utc)
        recent_ordinals = _recent_ordinals(conn, now)
        version_all, version_recent = _version_horizons(conn, now, recent_ordinals)

        rows = [
            ("version", str(vid), _display(version_all[vid]), _display(version_recent.get(vid, 0.0)))
            for vid in version_all
        ]
        for tier in ("recording", "release", "track"):
            sub_all, sub_recent = _subtier_scores(
                conn, tier, now, recent_ordinals, version_all, version_recent
            )
            rows.extend(
                (tier, str(gid), _display(sub_all[gid]), _display(sub_recent.get(gid, 0.0)))
                for gid in sub_all
            )

        conn.execute("DELETE FROM score")
        conn.executemany(
            "INSERT INTO score (tier, group_id, all_time, recent) VALUES (?, ?, ?, ?)", rows
        )
        conn.commit()
    except Exception as e:
        _record_recompute(started_at, t0, "error", str(e), None)
        raise
    else:
        _record_recompute(started_at, t0, "ok", None, tier_counts(conn))


def tier_counts(conn):
    """{tier: count} of materialized scores, for each of the four stored
    tiers (§9.1) -- what /dev/scoring shows."""
    counts = {
        row["tier"]: row["c"]
        for row in conn.execute("SELECT tier, COUNT(*) AS c FROM score GROUP BY tier")
    }
    return {tier: counts.get(tier, 0) for tier in ("version", "recording", "release", "track")}


_status_lock = threading.Lock()
_recompute_status = {
    "started_at": None,
    "finished_at": None,
    "duration_seconds": None,
    "outcome": None,  # "ok" | "error"
    "error": None,
    "counts": None,
}


def recompute_status():
    """The most recent recompute() call's outcome, in this process --
    purely in-memory, like jobs.JobStatus, so it reads as "no run yet" right
    after a restart. That's brief in practice: the §9.3 backstop recomputes
    on the very first request after any restart, so by the time anyone loads
    /dev/scoring there has almost always already been one."""
    with _status_lock:
        return dict(_recompute_status)


def _record_recompute(started_at, t0, outcome, error, counts):
    global _recompute_status
    with _status_lock:
        _recompute_status = {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_seconds": round(time.monotonic() - t0, 2),
            "outcome": outcome,
            "error": error,
            "counts": counts,
        }


# ---------------------------------------------------------------- backstop (§9.3)

# The union of every table a trigger above writes. reviewed_pair can't move a
# score on its own (it records that a pair was judged, not how tracks group)
# but stays in the list because it's written in the same breath as
# track_group, and an occasional redundant recompute costs a second.
_FINGERPRINT_TABLES = (
    "play", "membership", "track", "track_uri_alias", "track_group",
    "canonical_group", "reviewed_pair", "artist_alias", "generation",
)

_backstop_lock = threading.Lock()
_checker_conn = None
_last_data_version = None
_last_fingerprint = None


def _checker():
    """The dedicated long-lived connection the backstop reads PRAGMA
    data_version through, opened once per process. data_version is only
    meaningful relative to the connection that reads it, and never moves for
    that connection's own writes -- so this connection must never be the one
    that recomputes. Left in autocommit (isolation_level=None) so it never
    pins the WAL; it issues nothing but SELECT and PRAGMA, ever."""
    global _checker_conn
    if _checker_conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        _checker_conn = conn
    return _checker_conn


def ensure_fresh(conn):
    """The read-time backstop (§9.3). Cheap on every call (a ~0.03ms PRAGMA)
    unless some other connection has committed anything at all since the
    last check, in which case it pays an ~87ms fingerprint read, and only
    recomputes if the fingerprint actually moved.

    This exists for write paths nobody remembered to instrument explicitly
    -- canonical.ensure_track_groups() inserting track_group rows on a plain
    GET is the one it's mandatory for, not belt-and-braces politeness (see
    the module map's note on this). `conn` is used for the recompute itself
    if one is needed; it is never the checker connection."""
    global _last_data_version, _last_fingerprint
    needs_recompute = False
    with _backstop_lock:
        checker = _checker()
        version = checker.execute("PRAGMA data_version").fetchone()[0]
        if version != _last_data_version:
            _last_data_version = version
            fingerprint = tuple(
                checker.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in _FINGERPRINT_TABLES
            )
            if fingerprint != _last_fingerprint:
                _last_fingerprint = fingerprint
                needs_recompute = True

    if needs_recompute:
        recompute(conn)
    return needs_recompute


# ---------------------------------------------------------------- query-time aggregation (§5, §9.1)


def _version_scores_maps(conn):
    """{"all_time": {version_id: normalized}, "recent": {version_id: normalized}}
    -- the whole materialized version tier, undisplayed back to normalized
    space (see _undisplay's docstring for why aggregation needs that)."""
    all_time, recent = {}, {}
    for row in conn.execute("SELECT group_id, all_time, recent FROM score WHERE tier = 'version'"):
        vid = int(row["group_id"])
        all_time[vid] = _undisplay(row["all_time"])
        recent[vid] = _undisplay(row["recent"])
    return {"all_time": all_time, "recent": recent}


def song_scores(conn, song_ids):
    """{song_id: {"all_time": display, "recent": display}} -- combine() over
    each song's member version scores (§5: "Song groups aggregate their
    constituent version scores, using the same combiner as albums, artists
    and playlists ... A song is not special.")."""
    if not song_ids:
        return {}
    maps = _version_scores_maps(conn)
    placeholders = ",".join("?" for _ in song_ids)
    by_song = defaultdict(set)
    for row in conn.execute(
        f"SELECT DISTINCT song_id, version_id FROM track_group WHERE song_id IN ({placeholders})",
        list(song_ids),
    ):
        by_song[row["song_id"]].add(row["version_id"])
    return {
        sid: {
            h: _display(combine([maps[h][v] for v in versions if v in maps[h]]))
            for h in ("all_time", "recent")
        }
        for sid, versions in by_song.items()
    }


def album_scores(conn, album_ids):
    """{album_id: {"all_time": display, "recent": display}} -- combine() over
    each album's member version scores, padded with (total_tracks - known)
    zero-scoring members (§5.4)."""
    if not album_ids:
        return {}
    maps = _version_scores_maps(conn)
    placeholders = ",".join("?" for _ in album_ids)
    by_album = defaultdict(set)
    for row in conn.execute(
        f"SELECT DISTINCT t.album_id, tg.version_id FROM track t "
        f"JOIN track_group tg ON tg.track_id = t.track_id WHERE t.album_id IN ({placeholders})",
        list(album_ids),
    ):
        by_album[row["album_id"]].add(row["version_id"])
    have = {
        row["album_id"]: row["n"]
        for row in conn.execute(
            f"SELECT album_id, COUNT(*) AS n FROM track WHERE album_id IN ({placeholders}) GROUP BY album_id",
            list(album_ids),
        )
    }
    totals = {
        row["album_id"]: row["total_tracks"]
        for row in conn.execute(
            f"SELECT album_id, total_tracks FROM album WHERE album_id IN ({placeholders})",
            list(album_ids),
        )
    }
    out = {}
    for aid in album_ids:
        versions = by_album.get(aid, set())
        pad = max((totals.get(aid) or 0) - have.get(aid, 0), 0)
        out[aid] = {
            h: _display(combine([maps[h][v] for v in versions if v in maps[h]] + [0.0] * pad))
            for h in ("all_time", "recent")
        }
    return out


def _artist_role_rows(conn, artist_ids):
    """{artist_id: {version_id: featured_only}} for a batch of artists in one
    query -- featured_only is true iff every credit tying this version to
    this artist is a featured one (§5.3)."""
    placeholders = ",".join("?" for _ in artist_ids)
    out = defaultdict(dict)
    for row in conn.execute(
        f"SELECT r.artist_id, tg.version_id, "
        f"       MIN(CASE WHEN r.role = 'primary' THEN 0 ELSE 1 END) AS featured_only "
        f"FROM track_group tg JOIN track_artist_role r ON r.track_id = tg.track_id "
        f"WHERE r.artist_id IN ({placeholders}) GROUP BY r.artist_id, tg.version_id",
        list(artist_ids),
    ):
        out[row["artist_id"]][row["version_id"]] = bool(row["featured_only"])
    return out


def _weighted_score(maps, member_roles):
    """member_roles: {version_id: featured_only}. Builds what combine() needs
    from a set of version/role pairs and displays the result, both
    horizons."""
    out = {}
    for h in ("all_time", "recent"):
        scores, weights = [], []
        for vid, featured_only in member_roles.items():
            if vid not in maps[h]:
                continue
            scores.append(maps[h][vid])
            weights.append(FEATURED_WEIGHT if featured_only else 1.0)
        out[h] = _display(combine(scores, weights))
    return out


def artist_scores(conn, artist_ids):
    """{artist_id: {"all_time": display, "recent": display}}, one entry per
    artist -- batched over many artists at once (e.g. /search)."""
    if not artist_ids:
        return {}
    maps = _version_scores_maps(conn)
    roles = _artist_role_rows(conn, artist_ids)
    return {aid: _weighted_score(maps, roles.get(aid, {})) for aid in artist_ids}


def artist_group_score(conn, artist_ids):
    """{"all_time": display, "recent": display} for the UNION of one or more
    artists' credited versions, scored as one collection. A single artist_id
    is the ordinary per-artist score; several score a *group* of artists --
    e.g. a candidate duplicate-artist pair on /dev/artists, where "the
    pair's score" is the score of everything either credits, not a
    comparison of two separately-computed numbers. The combiner does not
    know what it is combining (§5)."""
    if not artist_ids:
        return {"all_time": 0.0, "recent": 0.0}
    maps = _version_scores_maps(conn)
    roles = _artist_role_rows(conn, artist_ids)
    merged = {}
    for per_artist in roles.values():
        for vid, featured_only in per_artist.items():
            merged[vid] = merged.get(vid, True) and featured_only
    return _weighted_score(maps, merged)


def playlist_scores(conn, playlist_ids):
    """{playlist_id: {"all_time": display, "recent": display}} -- combine()
    over each playlist's live member version scores."""
    if not playlist_ids:
        return {}
    maps = _version_scores_maps(conn)
    placeholders = ",".join("?" for _ in playlist_ids)
    by_playlist = defaultdict(set)
    for row in conn.execute(
        f"SELECT DISTINCT m.playlist_id, tg.version_id FROM membership m "
        f"JOIN track_group tg ON tg.track_id = m.track_id "
        f"WHERE m.playlist_id IN ({placeholders}) AND m.removed_at IS NULL",
        list(playlist_ids),
    ):
        by_playlist[row["playlist_id"]].add(row["version_id"])
    out = {}
    for pid in playlist_ids:
        versions = by_playlist.get(pid, set())
        out[pid] = {
            h: _display(combine([maps[h][v] for v in versions if v in maps[h]]))
            for h in ("all_time", "recent")
        }
    return out


def scores_for_tier(conn, tier, group_ids):
    """{group_id: {"all_time": display, "recent": display}} -- direct,
    already-materialized lookups at one of the four stored tiers, batched.
    Keyed by whatever type the entries of group_ids already are (int for
    version/recording/release, the track_id string for track)."""
    if not group_ids:
        return {}
    id_list = list(group_ids)
    placeholders = ",".join("?" for _ in id_list)
    str_ids = [str(g) for g in id_list]
    rows = {
        row["group_id"]: {"all_time": row["all_time"], "recent": row["recent"]}
        for row in conn.execute(
            f"SELECT group_id, all_time, recent FROM score "
            f"WHERE tier = ? AND group_id IN ({placeholders})",
            (tier, *str_ids),
        )
    }
    return {g: rows[str(g)] for g in id_list if str(g) in rows}


def get_both(conn, tier, group_id):
    """A single materialized {"all_time", "recent"} score, or None if it
    doesn't exist yet (e.g. before the first recompute)."""
    row = conn.execute(
        "SELECT all_time, recent FROM score WHERE tier = ? AND group_id = ?",
        (tier, str(group_id)),
    ).fetchone()
    return {"all_time": row["all_time"], "recent": row["recent"]} if row else None


def group_score(display_scores):
    """combine() over a set of already-materialized (display-space) scores
    -- for an ad-hoc bag of tracks that doesn't (yet) form one of the four
    stored tiers, e.g. a canonical-grouping candidate in canonical_detect.py
    whose tracks aren't necessarily one version group yet. Undisplays each
    input, combines in normalized space (§8), redisplays once."""
    if not display_scores:
        return 0.0
    return _display(combine([_undisplay(s) for s in display_scores]))
