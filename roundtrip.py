"""Foreign-track round-trip (see docs/specs/foreign-roundtrip-D.md): turns
played-but-unknown Spotify uris into real track rows with full metadata, by
pushing them through a private scratch playlist 100 at a time and reading the
full track objects back out of the playlist-items endpoint. ~2 requests per
100 uris instead of 1 per uri.

Each batch **replaces** the loader's contents rather than appending to them,
so the playlist holds exactly one batch and the read-back is always offset 0.
And the read-back is treated as a *bag*: every returned track identifies
itself by its own id, and a substituted one carries `linked_from` naming the
uri that was requested, so nothing depends on the k-th item being the k-th
uri sent. Both of those are deliberate -- the first version appended and
mapped positionally, and a read window that drifted 564 items silently wrote
1,250 bogus "relink" mappings before anything noticed.

**The one module in Symr that writes to the Spotify library.** Every write
goes to the <Play History Loader> playlist and nowhere else, and a run refuses
to start until a live guard has confirmed that playlist is the one it thinks
it is. Nothing here writes membership rows: these tracks are in no playlist of
Finn's, and the loader is a scratch buffer, not a library playlist."""

from concurrent.futures import ThreadPoolExecutor

import requests
from spotipy.exceptions import SpotifyException

import canonical_detect
import db
import jobs
import snapshot
from jobs import RateLimited
from spotify_client import get_spotify_client

# TODO: replace with a UI field. Deferred to after the next full pull, when the
# playlist shows up in `snapshot` and can be picked from a list. Id only -- the
# si/pt parameters on a Spotify share link are account-scoped tokens and must
# never enter the repo.
LOADER_ID = "3Sr9aUZKYT8DDmWdcRQh00"
LOADER_NAME = "<Play History Loader>"

# The API maximum for playlist_add_items, and the page size we read back.
BATCH_SIZE = 100

# Three consecutive failed batches stop the run. Without this a systemic fault
# -- a bad token, a revoked scope, a malformed request -- would fail all 61
# batches and fire 6,100 public probes for nothing. Any successful batch
# resets the count, so scattered dead uris never trip it.
_BREAKER_LIMIT = 3

# The public open.spotify.com probe (§5). Not the Web API: it costs no quota.
# 5 concurrent is roughly 10/s, so a bad batch of 100 costs ~10s rather than
# ~100s, and the short timeout stops one hanging probe stalling the pool.
_PROBE_WORKERS = 5
_PROBE_TIMEOUT = 5
_PROBE_UA = "Symr/1.0 (personal Spotify library manager; track-existence check)"

# Newly-failed uris shown live on the page. The durable record is
# roundtrip_failed_uri; this is capped only to keep the polled payload small.
_FAILURE_FEED_LIMIT = 100

# roundtrip_failed_uri.state is control flow, not a note: it decides which uris
# the reconciliation pass (§4.5) will spend requests re-requesting. A
# probe-confirmed 404 is dead and never retried; a uri that simply didn't come
# back may have been substituted, and is worth one more look. Slugs, never the
# text shown on the page -- see STATE_LABELS for that.
STATE_NOT_RETURNED = "not_returned"
STATE_NEEDS_REVIEW = "needs_review"
STATE_DEAD = "dead"
STATE_LOAD_FAILED = "load_failed"

# Display only. Changing any of these can never change behaviour.
STATE_LABELS = {
    STATE_NOT_RETURNED: "not returned by the read-back",
    STATE_NEEDS_REVIEW: "returned an unlabelled substitute — needs a manual alias",
    STATE_DEAD: "404 on open.spotify.com — track no longer exists",
    STATE_LOAD_FAILED: "the loader playlist rejected it twice",
}

_status = jobs.JobStatus(
    "roundtrip",
    phase=None,  # "guard" | "batches" | "clearing" | "done" | "stopped" | "error"
    current=None,
    batch_total=0,
    batch_done=0,
    uris_total=0,
    uris_stored=0,
    aliases_created=0,
    uris_failed=0,
    reconciled=0,
    needs_review=0,
    left_in_playlist=0,
    requests=0,
    started_at=None,
    finished_at=None,
    error=None,
    retry_at=None,
    # How the run ended, mirroring roundtrip_run.outcome. Survives into the
    # terminal state so a page reloaded after a run still renders it.
    outcome=None,
    failures=[],
)


def get_status():
    status = _status.get()
    # Derived, so a page loaded after the Stop button was pressed elsewhere
    # still shows the pending stop.
    status["stopping"] = status["running"] and jobs.stop_requested()
    return status


def start(reconcile_only=False):
    return jobs.try_start("roundtrip", _run, reconcile_only)


# -- Reads for the page -------------------------------------------------


def counts(conn):
    remaining = conn.execute(
        "SELECT COUNT(*) FROM (" + _WORK_LIST_SQL + ")"
    ).fetchone()[0]
    batches = -(-remaining // BATCH_SIZE)  # ceil
    return {
        "remaining_uris": remaining,
        "batches": batches,
        # 2 guard reads (the playlist and the current user) + 2 per batch
        # (add, read back) + 1 clear.
        "requests_estimate": 2 * batches + 3,
        # The round-trip's product: tracks Symr knows that sit in none of
        # Finn's playlists. Derived rather than flagged, like everything else
        # about "foreign" here.
        "resolved_tracks": conn.execute(
            "SELECT COUNT(*) FROM track t "
            "WHERE NOT EXISTS (SELECT 1 FROM membership m WHERE m.track_id = t.track_id)"
        ).fetchone()[0],
        "aliases": conn.execute("SELECT COUNT(*) FROM track_uri_alias").fetchone()[0],
        "failed_uris": conn.execute("SELECT COUNT(*) FROM roundtrip_failed_uri").fetchone()[0],
        # This run's share of remaining_uris that came from an album
        # tracklist rather than a play.
        "wanted_uris": conn.execute(_WANTED_REMAINING_SQL).fetchone()[0],
        # Worth one more look: recorded as not-returned and still unresolved.
        "reconcilable": len(_reconcile_list(conn)),
        "review_uris": conn.execute(
            "SELECT COUNT(*) FROM roundtrip_failed_uri WHERE state = ?", (STATE_NEEDS_REVIEW,)
        ).fetchone()[0],
    }


def failed_uri_rows(conn, limit=200):
    return conn.execute(
        "SELECT requested_uri, state, detail, failed_at FROM roundtrip_failed_uri "
        "ORDER BY failed_at DESC, requested_uri LIMIT ?",
        (limit,),
    ).fetchall()


def run_rows(conn):
    return conn.execute(
        "SELECT id, started_at, finished_at, uris_attempted, tracks_stored, aliases_created, "
        "uris_failed, requests, left_in_playlist, outcome, error "
        "FROM roundtrip_run ORDER BY id DESC"
    ).fetchall()


def manual_alias_rows(conn):
    """The uris the reconciliation pass wouldn't decide, each with what the
    export called it and the tracks a human can alias it to.

    Candidates are matched on the normalized title *base* -- looser than the
    automatic rule in §4.5, which also requires the suffix. That looseness is
    the whole point of the manual step: `Opalite` and `Opalite - BUNT. Remix`
    share a base and are genuinely different tracks, so a person decides."""
    rows = conn.execute(
        "SELECT f.requested_uri, MAX(p.reported_track_name) AS name, "
        "MAX(p.reported_artist_name) AS artist, COUNT(*) AS plays "
        "FROM roundtrip_failed_uri f JOIN play p ON p.spotify_track_uri = f.requested_uri "
        "WHERE f.state = ? GROUP BY f.requested_uri ORDER BY plays DESC",
        (STATE_NEEDS_REVIEW,),
    ).fetchall()
    if not rows:
        return []

    # One pass over track names, bucketed by base title. Only two columns, and
    # only when there is actually something to review.
    by_base = {}
    for track in conn.execute("SELECT track_id, name FROM track"):
        by_base.setdefault(_title_key(track["name"])[0], []).append(track["track_id"])

    # One details query covering every row's candidates, rather than one per
    # row: it joins the track_artists view, which SQLite fully materializes on
    # every call (~54ms whether you pass 2 ids or 400). Per-row, 200 review rows
    # took 13s to render; batched, ~110ms.
    wanted = {tid for row in rows for tid in by_base.get(_title_key(row["name"])[0], [])}
    details = {row["track_id"]: row for row in _candidate_details(conn, sorted(wanted))}

    return [
        {
            "requested_uri": row["requested_uri"],
            "name": row["name"],
            "artist": row["artist"],
            "plays": row["plays"],
            "candidates": [
                details[tid] for tid in by_base.get(_title_key(row["name"])[0], [])
            ],
        }
        for row in rows
    ]


def _candidate_details(conn, track_ids):
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    return conn.execute(
        "SELECT t.track_id, t.name, COALESCE(ta.artists, '') AS artists, "
        "       al.name AS album, al.release_year "
        "FROM track t "
        "LEFT JOIN track_artists ta ON ta.track_id = t.track_id "
        "LEFT JOIN album al ON al.album_id = t.album_id "
        f"WHERE t.track_id IN ({placeholders}) "
        "ORDER BY t.name COLLATE NOCASE",
        track_ids,
    ).fetchall()


def set_manual_aliases(conn, pairs):
    """Records a batch of human decisions that played uris resolve to tracks.

    Every pair is validated before any of them is written, so one stale row
    can't leave the rest half-applied. Restricted to uris actually awaiting
    review, so this only ever resolves a known-unresolved uri -- it is never a
    general "rewrite any mapping" lever. Uris left unchosen are simply absent
    from `pairs` and stay in the list."""
    pending = {
        row["requested_uri"]
        for row in conn.execute(
            "SELECT requested_uri FROM roundtrip_failed_uri WHERE state = ?",
            (STATE_NEEDS_REVIEW,),
        )
    }
    for requested_uri, track_id in pairs:
        if requested_uri not in pending:
            raise ValueError(f"{requested_uri} is not awaiting a manual alias")
        if conn.execute("SELECT 1 FROM track WHERE track_id = ?", (track_id,)).fetchone() is None:
            raise ValueError(f"no such track: {track_id}")

    for requested_uri, track_id in pairs:
        conn.execute(
            "INSERT OR REPLACE INTO track_uri_alias (requested_uri, track_id) VALUES (?, ?)",
            (requested_uri, track_id),
        )
        conn.execute(
            "DELETE FROM roundtrip_failed_uri WHERE requested_uri = ?", (requested_uri,)
        )
    conn.commit()
    return len(pairs)


def clear_failures(conn):
    """Empties roundtrip_failed_uri so a later run retries those uris. Only
    ever re-opens work, so it needs no confirmation."""
    conn.execute("DELETE FROM roundtrip_failed_uri")
    conn.commit()


# -- The work list -------------------------------------------------

# Ordered by play count descending, so a run that dies at 30% has done the
# most-played third rather than a random one; uri ascending is the tie-break
# that makes the order deterministic across runs. The playlist is filled in
# exactly this order, so its contents show how far a run got.
#
# This is also what makes a run resumable with no run-state to go stale:
# "done" is derived -- a uri is done when it resolves through
# played_uri_track, which covers both a direct track row and a relink alias.
# A re-run simply recomputes and finds less to do.
#
# The second arm folds in uris an album tracklist wanted but has no track row
# for (docs/specs/entity-pages-K.md §6) -- not a second queue, just more work
# in the same one. A wanted uri has no plays by definition, so plays=0 sorts
# it after every play-derived uri without a special case; the NOT IN against
# `play` keeps a uri that's both wanted and played from appearing twice.
_WORK_LIST_SQL = """
SELECT spotify_track_uri, plays FROM (
    SELECT p.spotify_track_uri AS spotify_track_uri, COUNT(*) AS plays
    FROM play p
    LEFT JOIN played_uri_track x ON x.uri = p.spotify_track_uri
    WHERE x.track_id IS NULL
      AND p.spotify_track_uri NOT IN (SELECT requested_uri FROM roundtrip_failed_uri)
    GROUP BY p.spotify_track_uri

    UNION ALL

    SELECT w.uri AS spotify_track_uri, 0 AS plays
    FROM wanted_uri w
    LEFT JOIN played_uri_track x ON x.uri = w.uri
    WHERE x.track_id IS NULL
      AND w.uri NOT IN (SELECT requested_uri FROM roundtrip_failed_uri)
      AND w.uri NOT IN (SELECT spotify_track_uri FROM play)
)
ORDER BY plays DESC, spotify_track_uri ASC
"""

# The wanted arm's own contribution, for counts() to report separately so
# /dev/roundtrip can show what a run is about to do.
_WANTED_REMAINING_SQL = """
SELECT COUNT(*) FROM wanted_uri w
LEFT JOIN played_uri_track x ON x.uri = w.uri
WHERE x.track_id IS NULL
  AND w.uri NOT IN (SELECT requested_uri FROM roundtrip_failed_uri)
  AND w.uri NOT IN (SELECT spotify_track_uri FROM play)
"""


def _work_list(conn):
    return [row["spotify_track_uri"] for row in conn.execute(_WORK_LIST_SQL)]


# -- The run -------------------------------------------------


def _run(reconcile_only=False):
    conn = db.connect()
    sp = get_spotify_client()
    run_id = None
    try:
        _status.reset(phase="guard", started_at=jobs.now_iso())
        run_id = conn.execute(
            "INSERT INTO roundtrip_run (outcome) VALUES ('running')"
        ).lastrowid
        conn.commit()
        if sp is None:
            raise RuntimeError("not_authenticated")

        leftovers = _guard(conn, sp)
        if leftovers:
            _status.log(
                f"{leftovers} item(s) left in the loader by an earlier run — "
                "the first batch replaces them."
            )

        uris = [] if reconcile_only else _work_list(conn)
        batches = [uris[i:i + BATCH_SIZE] for i in range(0, len(uris), BATCH_SIZE)]
        _status.set(uris_total=len(uris))
        if reconcile_only:
            _status.set(phase="reconciling")
            _status.log("Reconcile-only run — skipping the main pass.")
        else:
            _status.set(phase="batches", batch_total=len(batches))
            _status.log(
                f"Run started — {len(uris):,} uris in {len(batches)} batches, "
                f"~{2 * len(batches) + 3} requests."
            )

        outcome = "completed"
        consecutive_failures = 0
        for index, batch in enumerate(batches, start=1):
            if jobs.stop_requested():
                _status.log(f"Stop requested — stopping cleanly before batch {index}.")
                outcome = "stopped"
                break
            if _run_batch(conn, sp, index, len(batches), batch):
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                _status.log(
                    f"consecutive failed batches: {consecutive_failures}/{_BREAKER_LIMIT}"
                )
            _status.set(batch_done=index)
            _record_run(conn, run_id, "running")
            if consecutive_failures >= _BREAKER_LIMIT:
                _status.log(
                    "Circuit breaker — three consecutive failed batches, stopping the run."
                )
                outcome = "breaker"
                break

        if outcome == "completed":
            # Second pass over what the main one couldn't resolve, while the
            # guard is still fresh and the loader is already ours (§4.5).
            _reconcile(conn, sp)
            _record_run(conn, run_id, "running")
            # Tidiness only, and one request whatever is in there. Nothing
            # depends on it: each batch replaces the last, so the loader holds
            # at most one batch at any time.
            _status.set(phase="clearing", current="clearing the loader playlist")
            jobs.call(_status, sp.playlist_replace_items, LOADER_ID, [])
            _status.set(left_in_playlist=0)
            _status.log("Loader playlist cleared.")
        else:
            # A quota stop has no requests left to spend anyway, and the next
            # run's first batch replaces whatever is sitting there.
            _status.log(
                f"Not clearing — {_status.get()['left_in_playlist']} item(s) "
                "left in the loader playlist."
            )

        _status.set(
            phase="done" if outcome == "completed" else "stopped",
            current=None,
            outcome=outcome,
            finished_at=jobs.now_iso(),
        )
        _record_run(conn, run_id, outcome)
        _log_totals(outcome)
    except RateLimited as e:
        conn.rollback()
        _status.set(
            phase="error",
            current=None,
            outcome="rate_limited",
            error=str(e),
            retry_at=e.retry_at,
            finished_at=jobs.now_iso(),
        )
        _status.log(f"Rate limited — retry after {e.retry_at}. Run stopped.")
        _record_run(conn, run_id, "rate_limited", str(e))
        _log_totals("rate_limited")
    except Exception as e:
        conn.rollback()
        _status.set(
            phase="error", current=None, outcome="error", error=str(e),
            finished_at=jobs.now_iso(),
        )
        _status.log(f"Error — {e}")
        _record_run(conn, run_id, "error", str(e))
        _log_totals("error")
    finally:
        conn.close()


def _log_totals(outcome):
    s = _status.get()
    _status.log(
        f"Run {outcome} — {s['uris_stored']:,} tracks stored, "
        f"{s['aliases_created']} aliased ({s['reconciled']} by reconciliation), "
        f"{s['uris_failed']} uris failed, {s['needs_review']} awaiting a manual alias, "
        f"{s['requests']} requests spent, "
        f"{s['left_in_playlist']} item(s) left in the playlist."
    )


def _guard(conn, sp):
    """Two reads and zero writes, before anything is added. This is the whole
    insurance policy against the one thing that must never happen -- a bug
    pointing the clear-playlist call at a real playlist -- so it is not
    optional and it is not cached.

    Returns the playlist's current item count, which is leftovers from an
    earlier run -- reported for the log only. Nothing depends on it being
    right: the first batch replaces whatever is there."""
    playlist = jobs.call(_status, sp.playlist, LOADER_ID)
    me = jobs.call(_status, sp.current_user)

    name = playlist.get("name")
    owner = playlist.get("owner") or {}
    if name != LOADER_NAME:
        raise RuntimeError(
            f"guard failed: playlist {LOADER_ID} is named {name!r}, not {LOADER_NAME!r} — "
            "refusing to write to it"
        )
    if not owner.get("id") or owner["id"] != me.get("id"):
        raise RuntimeError(
            f"guard failed: playlist {LOADER_ID} is owned by {owner.get('id')!r}, "
            f"not by you ({me.get('id')!r}) — refusing to write to it"
        )
    _status.log(f"Guard passed — {name} owned by {owner['id']}.")

    # excluded = 1 so a later full pull never reads the loader's items and it
    # never contributes membership rows.
    conn.execute(
        """
        INSERT INTO snapshot (playlist_id, name, owner, pulled_at, excluded)
        VALUES (?, ?, ?, datetime('now'), 1)
        ON CONFLICT(playlist_id) DO UPDATE SET
            name = excluded.name,
            owner = excluded.owner,
            excluded = 1
        """,
        (LOADER_ID, name, owner.get("display_name") or owner["id"]),
    )
    conn.commit()

    return (playlist.get("tracks") or {}).get("total") or 0


def _run_batch(conn, sp, index, total, uris):
    """Loads one batch into the loader, reads it straight back, and stores
    what came back. Everything it wrote is committed before it returns, so a
    quota death costs nothing already earned. Returns True if the batch
    landed."""
    label = f"batch {index}/{total}"
    loaded, tracks = _load_and_read(conn, sp, label, uris)
    if not loaded:
        return False
    stored = len(tracks)

    # Anything asked for that still doesn't resolve simply didn't come back.
    # A set difference, computed after the fact -- no positions, no counting.
    missing = _unresolved(conn, loaded)
    aliased = sum(1 for track in tracks if _linked_from_uri(track))

    if len(missing) == len(loaded):
        # Not one uri we asked for came back. Either every one was substituted
        # (which the reconciliation pass can still resolve on title+artist), or
        # something systemic went wrong -- a wrong read, a scope problem, the
        # wrong playlist. A structurally sane read, one usable track back per
        # uri sent, points at the first; anything else at the second, and those
        # uris must not be recorded at all, since a systemic fault would
        # otherwise flag every uri in the run.
        if stored == len(loaded):
            _fail_uris(conn, loaded, STATE_NOT_RETURNED)
            _status.log(
                f"{label} — all {len(loaded)} substituted with unlabelled tracks; "
                "queued for reconciliation"
            )
        else:
            _status.log(
                f"{label} — loaded {len(loaded)} but none came back and the read "
                f"returned {stored} usable track(s); batch failed, nothing recorded"
            )
        # Either way the batch made no progress on what was asked for, so the
        # breaker still gets to stop a run that keeps doing this.
        return False

    if missing:
        _fail_uris(conn, missing, STATE_NOT_RETURNED)
    _status.log(
        f"{label} — loaded {len(loaded)}, stored {stored}, aliased {aliased}"
        + (f", {len(missing)} not returned" if missing else "")
    )
    # Success is measured against what we asked for, not what we stored: a
    # batch that stored 100 unrelated tracks made no progress.
    return True


# -- Reconciliation (§4.5) -------------------------------------------------


def _reconcile_list(conn):
    """Uris worth one more look: recorded as not-returned, and still
    unresolved. Probe-confirmed 404s are excluded -- they are dead, and
    re-requesting them spends quota for nothing."""
    return [
        row["requested_uri"]
        for row in conn.execute(
            "SELECT f.requested_uri FROM roundtrip_failed_uri f "
            "LEFT JOIN played_uri_track x ON x.uri = f.requested_uri "
            "WHERE f.state = ? AND x.track_id IS NULL "
            "ORDER BY f.requested_uri",
            (STATE_NOT_RETURNED,),
        )
    ]


def _title_key(name):
    """Normalized (base, suffix). The suffix has to stay in the key: without
    it `Opalite`, `Opalite - BUNT. Remix` and `Opalite - Chris Lake Remix`
    collapse onto one key and all three become ambiguous."""
    return canonical_detect.normalize_title(name or "")


def _album_artist_keys(track):
    """Normalized album-artist names off the returned track object. Album
    artists specifically, because that is what the export's
    reported_artist_name is -- it misses featured credits by design."""
    artists = (track.get("album") or {}).get("artists") or []
    return {canonical_detect.normalize_name(a.get("name") or "") for a in artists}


def _expected_labels(conn, uris):
    """What the export says each uri was called when it was played. The only
    independent evidence available about a uri Spotify won't serve."""
    placeholders = ",".join("?" for _ in uris)
    return {
        row["uri"]: (
            _title_key(row["name"]),
            canonical_detect.normalize_name(row["artist"] or ""),
        )
        for row in conn.execute(
            "SELECT spotify_track_uri AS uri, MAX(reported_track_name) AS name, "
            "MAX(reported_artist_name) AS artist FROM play "
            f"WHERE spotify_track_uri IN ({placeholders}) GROUP BY spotify_track_uri",
            list(uris),
        )
    }


def _match_substitutes(conn, unresolved, candidates):
    """Pairs each still-unresolved uri with a returned track nothing else
    accounts for, but *only* on evidence: normalized full title AND album
    artist must both match what the export recorded.

    Deliberately not positional. Spotify substitutes these ids without setting
    `linked_from`, so the pairing is real but unstated -- and an unstated
    pairing guessed from position is exactly what silently corrupted 1,250
    rows (§4.3). A match here is evidenced or it doesn't happen; the leftovers
    are flagged for a human instead of guessed at."""
    expected = _expected_labels(conn, unresolved) if unresolved else {}
    keyed = [(t, _title_key(t.get("name")), _album_artist_keys(t)) for t in candidates]

    claims = {}
    for uri in unresolved:
        want_title, want_artist = expected.get(uri, (None, None))
        if not want_title or not want_title[0] or not want_artist:
            continue
        hits = [t for t, title, artists in keyed if title == want_title and want_artist in artists]
        if len(hits) == 1:
            claims.setdefault(hits[0]["id"], []).append(uri)

    # A candidate claimed by two uris is ambiguous in the other direction, so
    # only 1:1 pairings are written.
    return {uris[0]: track_id for track_id, uris in claims.items() if len(uris) == 1}


def _reconcile(conn, sp):
    uris = _reconcile_list(conn)
    if not uris:
        return
    batches = [uris[i:i + BATCH_SIZE] for i in range(0, len(uris), BATCH_SIZE)]
    _status.set(phase="reconciling", batch_total=len(batches), batch_done=0)
    _status.log(
        f"Reconciling {len(uris)} unresolved uri(s) in {len(batches)} batch(es) — "
        "matching returned substitutes on title + album artist."
    )
    for index, batch in enumerate(batches, start=1):
        if jobs.stop_requested():
            _status.log("Stop requested — ending reconciliation.")
            return
        _reconcile_batch(conn, sp, index, len(batches), batch)
        _status.set(batch_done=index)


def _reconcile_batch(conn, sp, index, total, uris):
    label = f"reconcile {index}/{total}"
    loaded, returned = _load_and_read(conn, sp, label, uris)
    if not loaded:
        return

    loaded_set = set(loaded)
    unresolved = _unresolved(conn, loaded)

    # A uri the read-back resolved on its own this time is no longer failed --
    # Spotify served it where an earlier run got nothing. Drop its row rather
    # than leaving it listed as "not returned by the read-back" forever, which
    # is both wrong and permanent (a resolved uri never comes back here).
    unresolved_set = set(unresolved)
    recovered = [uri for uri in loaded if uri not in unresolved_set]
    for uri in recovered:
        conn.execute("DELETE FROM roundtrip_failed_uri WHERE requested_uri = ?", (uri,))

    # A returned track is a candidate only if nothing already accounts for it:
    # it isn't one of the uris we asked for, and it didn't name one via
    # linked_from.
    candidates = [
        t for t in returned if t.get("uri") not in loaded_set and not _linked_from_uri(t)
    ]

    matched = _match_substitutes(conn, unresolved, candidates)
    for uri, track_id in matched.items():
        _alias(conn, uri, track_id)
        conn.execute("DELETE FROM roundtrip_failed_uri WHERE requested_uri = ?", (uri,))
    unmatched = [u for u in unresolved if u not in matched]
    for uri in unmatched:
        conn.execute(
            "UPDATE roundtrip_failed_uri SET state = ? WHERE requested_uri = ?",
            (STATE_NEEDS_REVIEW, uri),
        )
    conn.commit()

    _status.add(
        reconciled=len(matched), aliases_created=len(matched), needs_review=len(unmatched)
    )
    _status.log(
        f"{label} — {len(matched)} matched on title + artist, "
        f"{len(unmatched)} left for manual review"
        + (f", {len(recovered)} came back on their own" if recovered else "")
    )


def _load_and_read(conn, sp, label, uris):
    """The load-and-read-back cycle both passes are built on: make the loader
    hold exactly `uris`, read it straight back, and store every usable track
    that came back. Returns `(loaded, tracks)` -- what actually went in, and
    the usable track objects that came out, in return order. Committed before
    it returns, so a quota death costs nothing already earned.

    This is the module's whole mechanism, so it lives in one place: its two
    invariants (replace-not-append, and reading the page as a *bag*) are the
    ones the positional version got wrong, and asserting them twice would be
    asserting them once too many."""
    _status.set(current=f"loading {label}")
    loaded = _load_with_repair(conn, sp, label, uris)
    if not loaded:
        return [], []
    _status.set(left_in_playlist=len(loaded))

    # The playlist now holds exactly this batch and nothing else, so offset 0
    # is right by construction -- there is no running offset to drift.
    _status.set(current=f"reading back {label}")
    page = jobs.call(_status, sp.playlist_items, LOADER_ID, offset=0, limit=BATCH_SIZE)

    # Read as a bag, never as a sequence. Each returned track identifies itself
    # by its own id, and a substituted one carries `linked_from` naming exactly
    # what was requested -- so nothing here depends on the k-th item being the
    # k-th uri sent. (It used to, and a drifted read window silently rewrote
    # 1,250 uri->track mappings as bogus "relinks".)
    tracks = []
    aliased = 0
    for entry in page.get("items") or []:
        # Playlist items key the track as "item", not "track" (the endpoint
        # can hold episodes too).
        track = entry.get("item")
        if not snapshot._usable_track(track):
            continue
        # The same upsert a pull uses, so track / album / artist /
        # track_artist / album_artist are filled exactly as a pull fills them.
        snapshot._upsert_track_full(conn, snapshot._parse_track_item(track, None, None))
        requested_uri = _linked_from_uri(track)
        if requested_uri:
            # Spotify substituted this track for one it wouldn't serve. The
            # requested id comes back only as a stub, so it can never become a
            # track row of its own -- the relationship lives here instead.
            _alias(conn, requested_uri, track["id"])
            aliased += 1
        tracks.append(track)
    conn.commit()
    # Counted here, where the writes happen, so both passes tally identically.
    _status.add(uris_stored=len(tracks), aliases_created=aliased)
    return loaded, tracks


def _alias(conn, requested_uri, track_id):
    conn.execute(
        "INSERT OR REPLACE INTO track_uri_alias (requested_uri, track_id) VALUES (?, ?)",
        (requested_uri, track_id),
    )


def _linked_from_uri(track):
    """The uri Spotify says was *requested* when it substituted this track,
    or None if it wasn't a substitution. This is the only trustworthy pairing
    between a requested uri and a returned track."""
    linked_from = track.get("linked_from") or {}
    if linked_from.get("uri"):
        return linked_from["uri"]
    if linked_from.get("id"):
        return f"spotify:track:{linked_from['id']}"
    return None


def _unresolved(conn, uris):
    placeholders = ",".join("?" for _ in uris)
    resolved = {
        row[0]
        for row in conn.execute(
            f"SELECT uri FROM played_uri_track WHERE uri IN ({placeholders})", uris
        )
    }
    return [uri for uri in uris if uri not in resolved]


def _load_with_repair(conn, sp, label, uris):
    """Makes the loader playlist hold exactly this batch; on a 400, narrows it
    once with the public probe and retries with the survivors. Returns the uris
    actually loaded, or an empty list if the batch is dead.

    Replace rather than append: it costs the same one request, and it means
    the playlist never accumulates, never nears the 10,000-item cap, and never
    needs an offset to read back."""
    try:
        jobs.call(_status, sp.playlist_replace_items, LOADER_ID, uris)
        return uris
    except SpotifyException as e:
        # One dead uri poisons the whole write. Skipping 100 tracks to lose 1
        # is wasteful, and bisecting via the API spends the quota the run
        # exists to protect -- so narrow it off-quota instead.
        if e.http_status != 400:
            raise
        _status.log(f"{label} — rejected (400); probing {len(uris)} uris on open.spotify.com")

    _status.set(current=f"probing {len(uris)} uris for {label}")
    dead = _probe_dead(uris)
    _status.log(f"{label} — probe found {len(dead)} definitely-dead uri(s)")
    if dead:
        _fail_uris(conn, [u for u in uris if u in dead], STATE_DEAD)

    survivors = [u for u in uris if u not in dead]
    if not survivors:
        return []

    _status.set(current=f"retrying {label} with {len(survivors)} uris")
    try:
        jobs.call(_status, sp.playlist_replace_items, LOADER_ID, survivors)
        _status.log(f"{label} — retry with {len(survivors)} survivors succeeded")
        return survivors
    except SpotifyException as e:
        # The probe is best-effort narrowing -- it was only ever verified
        # against fabricated ids, and a withdrawn track may still serve a
        # page. This is the real backstop.
        _fail_uris(conn, survivors, STATE_LOAD_FAILED, str(e))
        _status.log(f"{label} — retry failed too; {len(survivors)} uris recorded as failed")
        return []


def _probe_dead(uris):
    """404-checks each uri against the *public* open.spotify.com track page --
    the web frontend, not the Web API, so it costs no quota (robots.txt allows
    /track/ for everyone). Only a definite 404 drops a uri: a timeout, a 5xx
    or a 429 is inconclusive and keeps it."""
    session = requests.Session()
    session.headers["User-Agent"] = _PROBE_UA

    def probe(uri):
        track_id = uri.rsplit(":", 1)[-1]
        try:
            response = session.head(
                f"https://open.spotify.com/track/{track_id}",
                timeout=_PROBE_TIMEOUT,
                allow_redirects=True,
            )
            return uri if response.status_code == 404 else None
        except requests.RequestException:
            return None

    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
        return {uri for uri in pool.map(probe, uris) if uri}


def _fail_uris(conn, uris, state, detail=None):
    for uri in uris:
        conn.execute(
            "INSERT OR IGNORE INTO roundtrip_failed_uri (requested_uri, state, detail) "
            "VALUES (?, ?, ?)",
            (uri, state, detail),
        )
        _status.append(
            "failures",
            {"uri": uri, "reason": STATE_LABELS[state]},
            limit=_FAILURE_FEED_LIMIT,
        )
    conn.commit()
    _status.add(uris_failed=len(uris))


def _record_run(conn, run_id, outcome, error=None):
    if run_id is None:
        return
    s = _status.get()
    conn.execute(
        "UPDATE roundtrip_run SET finished_at = ?, uris_attempted = ?, tracks_stored = ?, "
        "aliases_created = ?, uris_failed = ?, requests = ?, left_in_playlist = ?, "
        "outcome = ?, error = ? WHERE id = ?",
        (
            None if outcome == "running" else jobs.now_iso(),
            s["uris_total"],
            s["uris_stored"],
            s["aliases_created"],
            s["uris_failed"],
            s["requests"],
            s["left_in_playlist"],
            outcome,
            error,
            run_id,
        ),
    )
    conn.commit()
