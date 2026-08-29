"""Album backfill (docs/specs/grouping-fixes-backfill-M.md §4): a fourth
background job, alongside snapshot / history_import / roundtrip. It fetches
tracklists for unsettled albums in the most recent N generations and queues
their unowned uris into the round-trip's existing work list -- an ingest
route, nothing more. No chaining into the round-trip or auto-group.

Nothing here is checkpointed (§4.2): "settled" and "handled" are derived
fresh from track/wanted_uri/generation_presence every time, so clearing the
backfill queue is a free, complete undo, and re-clicking a button after a
partial run simply picks up wherever it left off."""

import json
from collections import defaultdict

import canonical
import db
import entities
import generations
import jobs
import roundtrip
from jobs import RateLimited
from spotify_client import get_spotify_client

_status = jobs.JobStatus(
    "backfill",
    phase=None,  # "working" | "done" | "stopped" | "error"
    generations=None,  # which button was clicked (2 or 7); survives into the terminal state
    ordinals=None,  # the ordinals actually chosen, for display
    albums_total=0,
    albums_done=0,
    current_album=None,
    uris_queued=0,
    requests=0,
    started_at=None,
    finished_at=None,
    error=None,
    retry_at=None,
    outcome=None,  # "completed" | "stopped" | "rate_limited" | "error"
)


def get_status():
    status = _status.get()
    status["stopping"] = status["running"] and jobs.stop_requested()
    return status


def start(n_generations):
    return jobs.try_start("backfill", _run, n_generations)


# -- The derived model (§4.2) -------------------------------------------


def _settled_map(conn):
    """album_id -> missing: total_tracks - owned(A) - queued(A), where
    queued(A) only counts wanted_uri rows for A that are still unresolved.
    Settled means missing <= 0 -- every caller derives that itself. The
    count, not just the boolean, is kept because it is precisely how many
    URIs an Add would queue (T2, docs/specs/small-fixes-T.md §2.2), at zero
    further request cost. Three cheap aggregates, combined in Python rather
    than a per-album correlated query."""
    owned = {
        r["album_id"]: r["n"]
        for r in conn.execute(
            "SELECT album_id, COUNT(*) AS n FROM track "
            "WHERE album_id IS NOT NULL GROUP BY album_id"
        )
    }
    queued = {
        r["album_id"]: r["n"]
        for r in conn.execute(
            "SELECT w.album_id AS album_id, COUNT(*) AS n FROM wanted_uri w "
            "LEFT JOIN played_uri_track x ON x.uri = w.uri "
            "WHERE w.album_id IS NOT NULL AND x.track_id IS NULL "
            "GROUP BY w.album_id"
        )
    }
    missing_map = {}
    for row in conn.execute("SELECT album_id, total_tracks FROM album"):
        missing_map[row["album_id"]] = (row["total_tracks"] or 0) - owned.get(
            row["album_id"], 0
        ) - queued.get(row["album_id"], 0)
    return missing_map


def _unhandled_ordinals_desc(conn, settled):
    """Generation ordinals, descending, that are NOT handled: at least one
    album with a track present in that generation is unsettled. An ordinal
    with no album-bearing tracks at all is vacuously handled and excluded."""
    by_ordinal = defaultdict(set)
    for row in conn.execute(
        "SELECT DISTINCT gp.ordinal, t.album_id FROM generation_presence gp "
        "JOIN track t ON t.track_id = gp.track_id WHERE t.album_id IS NOT NULL"
    ):
        by_ordinal[row["ordinal"]].add(row["album_id"])

    all_ordinals = [
        row["ordinal"]
        for row in conn.execute("SELECT ordinal FROM generation ORDER BY ordinal DESC")
    ]
    return [
        o
        for o in all_ordinals
        if not all(settled.get(aid, 1) <= 0 for aid in by_ordinal.get(o, ()))
    ]


def _albums_for_ordinals(conn, ordinals):
    if not ordinals:
        return set()
    placeholders = ",".join("?" for _ in ordinals)
    return {
        row["album_id"]
        for row in conn.execute(
            f"SELECT DISTINCT t.album_id FROM generation_presence gp "
            f"JOIN track t ON t.track_id = gp.track_id "
            f"WHERE gp.ordinal IN ({placeholders}) AND t.album_id IS NOT NULL",
            ordinals,
        )
    }


def _requests_estimate(conn, album_ids):
    """1 request per album that needs a first fetch (tracklist_pulled_at IS
    NULL), plus one more per extra 50 tracks past the first page (§0.3). An
    unsettled album that was already fetched -- its queue was cleared --
    costs nothing, since the tracklist is already stored."""
    if not album_ids:
        return 0
    placeholders = ",".join("?" for _ in album_ids)
    total = 0
    for row in conn.execute(
        f"SELECT total_tracks FROM album WHERE album_id IN ({placeholders}) "
        f"AND tracklist_pulled_at IS NULL",
        list(album_ids),
    ):
        total += max(1, -(-(row["total_tracks"] or 0) // 50))  # ceil(total_tracks / 50)
    return total


def _format_ordinal_range(ordinals):
    if not ordinals:
        return ""
    return ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in generations.runs(ordinals))


def _derive(conn, n_generations, settled, unhandled):
    """The work list itself, off an already-computed settled map and
    unhandled-ordinal list. Split out so previews() can derive both buttons
    from one pass over them (§4.6) -- the two differ only in how many
    ordinals they take."""
    chosen = unhandled[:n_generations]
    album_ids = _albums_for_ordinals(conn, chosen)
    unsettled = sorted(a for a in album_ids if settled.get(a, 1) > 0)
    # T2 (docs/specs/small-fixes-T.md §2.2): exactly how many URIs an Add
    # would queue, free arithmetic off the missing map above.
    queued_uris = sum(max(0, settled.get(a, 1)) for a in unsettled)

    return {
        "ordinals": sorted(chosen),
        "albums": unsettled,
        "requests_estimate": _requests_estimate(conn, unsettled),
        "queued_uris": queued_uris,
    }


def _refresh(conn):
    """The one write either entry point makes: every generation_presence
    reader needs track groups to be current first."""
    canonical.ensure_track_groups(conn)
    conn.commit()


def work_list(conn, n_generations):
    """Everything the Add-button preview and the job's real run both need,
    computed identically so the two can never disagree: the chosen
    generation ordinals (descending, skipping handled ones, capped at
    n_generations), the unsettled albums with a track present in any of
    them, and the request estimate. Pure -- no Spotify calls, nothing
    written but _refresh()'s track groups."""
    _refresh(conn)
    settled = _settled_map(conn)
    return _derive(conn, n_generations, settled, _unhandled_ordinals_desc(conn, settled))


def previews(conn, remaining_uris, counts=(7, 2)):
    """Display shape of work_list() for the Add buttons, one row per button,
    computed fresh on every /dev/roundtrip page load (spec M §4.6). Both
    buttons share a single _refresh() / _settled_map() /
    _unhandled_ordinals_desc() pass: they are identical for every button, so
    doing them per button was a second track-group check and a second commit
    on a plain page load.

    `remaining_uris` is the round-trip's own queue size (roundtrip.counts()'s
    `remaining_uris`) -- T2 (docs/specs/small-fixes-T.md §2.3) combines it
    with this button's own `queued_uris` through roundtrip.requests_estimate,
    the single formula the Status panel also uses, so the two figures can't
    drift apart."""
    _refresh(conn)
    settled = _settled_map(conn)
    unhandled = _unhandled_ordinals_desc(conn, settled)
    rows = []
    for n in counts:
        wl = _derive(conn, n, settled, unhandled)
        rows.append(
            {
                "generations": n,
                "album_count": len(wl["albums"]),
                "requests_estimate": wl["requests_estimate"],
                "range_label": _format_ordinal_range(wl["ordinals"]),
                "queued_uris": wl["queued_uris"],
                "roundtrip_estimate": roundtrip.requests_estimate(
                    remaining_uris + wl["queued_uris"]
                ),
            }
        )
    return rows


# -- The run -------------------------------------------------


def _fetch_full_tracklist(conn, sp, album_id):
    """The backfill's own tracklist fetch: unlike entities.fetch_album_tracklist,
    pages past Spotify's 50-item first page (§4.5), and every request counts
    against this job's own status via jobs.call. Failures propagate --
    _run's per-album try/except is what catches them."""
    album = jobs.call(_status, sp.album, album_id)
    page = album.get("tracks") or {}
    items = list(page.get("items") or [])
    while page.get("next"):
        page = jobs.call(_status, sp.next, page)
        items.extend(page.get("items") or [])
    conn.execute(
        "UPDATE album SET tracklist_json = ?, tracklist_pulled_at = ? WHERE album_id = ?",
        (json.dumps(items, separators=(",", ":")), jobs.now_iso(), album_id),
    )


def _run(n_generations):
    conn = db.connect()
    sp = get_spotify_client()
    try:
        _status.reset(phase="working", started_at=jobs.now_iso(), generations=n_generations)
        if sp is None:
            raise RuntimeError("not_authenticated")

        wl = work_list(conn, n_generations)
        albums = wl["albums"]
        _status.set(albums_total=len(albums), ordinals=wl["ordinals"])
        _status.log(
            f"Backfill started — {len(albums)} album(s) across generations "
            f"{_format_ordinal_range(wl['ordinals']) or 'none'}, ~{wl['requests_estimate']} requests."
        )

        stopped = False
        for i, album_id in enumerate(albums, start=1):
            if jobs.stop_requested():
                _status.log(f"Stop requested — stopping cleanly before album {i}/{len(albums)}.")
                stopped = True
                break
            _status.set(current_album=album_id)
            try:
                row = conn.execute(
                    "SELECT tracklist_pulled_at FROM album WHERE album_id = ?", (album_id,)
                ).fetchone()
                if row and row["tracklist_pulled_at"] is None:
                    _fetch_full_tracklist(conn, sp, album_id)
                queued = entities.queue_wanted_uris(conn, album_id, source="backfill")
                _status.add(uris_queued=queued)
            except RateLimited:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                _status.log(f"{album_id} — failed: {e}")
            _status.set(albums_done=i)

        _status.set(current_album=None)
        if stopped:
            _status.set(phase="stopped", outcome="stopped", finished_at=jobs.now_iso())
        else:
            _status.set(phase="done", outcome="completed", finished_at=jobs.now_iso())
        s = _status.get()
        _status.log(
            f"{'Stopped' if stopped else 'Finished'} — {s['albums_done']}/{len(albums)} album(s), "
            f"{s['uris_queued']} uri(s) queued, {s['requests']} requests spent."
        )
    except RateLimited as e:
        conn.rollback()
        _status.set(
            phase="error", current_album=None, outcome="rate_limited",
            error=str(e), retry_at=e.retry_at, finished_at=jobs.now_iso(),
        )
        _status.log(f"Rate limited — retry after {e.retry_at}. Run stopped.")
    except Exception as e:
        conn.rollback()
        _status.set(
            phase="error", current_album=None, outcome="error", error=str(e),
            finished_at=jobs.now_iso(),
        )
        _status.log(f"Error — {e}")
    finally:
        conn.close()
