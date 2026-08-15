import json
import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

import artists
import canonical
import canonical_autogroup
import canonical_detect
import db
import entities
import generations
import history_import
import jobs
import roundtrip
import scoring
import snapshot
from config import APP_DEBUG, APP_PORT, MAX_CONTENT_LENGTH, SECRET_KEY
from grouping import render_export_text
from spotify_client import get_auth_manager, get_spotify_client


# /dev/canonical's two scrolling listings render every row they're given, which
# unfiltered was ~800 four-tier trees and ~600 buckets -- 4.3MB of HTML. Both
# are capped unless you've searched; a search is taken as asking for all of its
# matches, so `%` (a LIKE wildcard in both filters) still gets you everything.
_LISTING_CAP = 50


def _cap_listing(rows, query):
    return rows if query else rows[:_LISTING_CAP]


def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    db.init_db()

    app.teardown_appcontext(db.close_db)

    # -- Auth guard ---------------------------------------------------

    # Endpoints reachable without a Spotify login. Everything else is gated.
    _PUBLIC_ENDPOINTS = {"login", "callback", "static"}

    @app.before_request
    def require_login():
        if request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        if get_spotify_client() is None:
            return redirect(url_for("login"))
        return None

    # docs/specs/scoring-H.md §9.3's read-time backstop, run on every
    # authenticated request rather than scattered per-route like
    # canonical.ensure_track_groups(conn) -- a single hook is a write path
    # that can never be forgotten, which is the property the backstop exists
    # for. Cheap on the common case (a 0.03ms PRAGMA); see scoring.ensure_fresh.
    @app.before_request
    def refresh_scores():
        if request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        scoring.ensure_fresh(db.get_db())
        return None

    # -- Error handling -------------------------------------------------

    def render_error(code, name, detail=None, exc=None):
        if request.path.startswith("/api/"):
            slug = name.lower().replace(" ", "_")
            message = exc if exc is not None else detail
            return jsonify({"error": slug, "detail": message}), code

        try:
            return (
                render_template(
                    "error.html",
                    code=code,
                    name=name,
                    detail=detail,
                    exc=exc,
                    method=request.method,
                    path=request.path,
                ),
                code,
            )
        except Exception:
            return (
                f"<h1>Error {code}.</h1>"
                "<p>An error occurred, and the templated error page could not be rendered.</p>",
                code,
            )

    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        return render_error(e.code, e.name, detail=e.description)

    @app.errorhandler(Exception)
    def handle_exception(e):
        exc = f"{type(e).__name__}: {e}"
        return render_error(500, "Internal Server Error", exc=exc)

    # -- Pages --------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("home.html", active="home")

    @app.route("/canvas")
    def canvas():
        return render_template("canvas.html", active="canvas")

    @app.route("/audit")
    def audit():
        return render_template("coming_soon.html", active="audit", page_name="Audit")

    @app.route("/covers")
    def covers():
        return render_template(
            "coming_soon.html", active="covers", page_name="Cover Library"
        )

    @app.route("/folders")
    def folders():
        return render_template(
            "coming_soon.html", active="folders", page_name="Folder Structure"
        )

    @app.route("/analytics")
    def analytics():
        return render_template("coming_soon.html", active="analytics", page_name="Analytics")

    # -- Entity pages (docs/specs/entity-pages-K.md) ---------------------
    #
    # Top-level, not under /dev: the canonical place each entity is
    # displayed, which every other page links into.

    _GROUP_TIER_COLUMN = {"song": "song_id", "version": "version_id",
                           "recording": "recording_id", "release": "release_id"}

    @app.route("/song/<int:group_id>", endpoint="song_page", defaults={"tier": "song"})
    @app.route("/version/<int:group_id>", endpoint="version_page", defaults={"tier": "version"})
    @app.route("/recording/<int:group_id>", endpoint="recording_page", defaults={"tier": "recording"})
    @app.route("/release/<int:group_id>", endpoint="release_page", defaults={"tier": "release"})
    def group_page(group_id, tier):
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        row = conn.execute("SELECT tier FROM canonical_group WHERE id = ?", (group_id,)).fetchone()
        if row is None or row["tier"] != tier:
            abort(404, description="No such group.")

        tree = canonical.group_tree(conn, tier, group_id)
        track_ids = tree["track_ids"]
        if not track_ids:
            abort(404, description="Group has no members.")

        rep_id = tree["representative_track_id"]
        rep = canonical.track_display(conn, rep_id) if rep_id else None
        artist_credits = canonical.artist_credits_for_tracks(conn, track_ids)

        breadcrumb = conn.execute(
            "SELECT song_id, version_id, recording_id, release_id FROM track_group WHERE track_id = ?",
            (track_ids[0],),
        ).fetchone()

        track_scores = scoring.scores_for_tier(conn, "track", track_ids)
        member_tracks = sorted(
            (canonical.track_display(conn, tid) for tid in track_ids),
            key=lambda t: (
                -track_scores.get(t["track_id"], {}).get("all_time", 0.0),
                (t["name"] or "").casefold(),
            ),
        )
        tracks_by_id = {t["track_id"]: t for t in member_tracks}

        ordinals = generations.presence_for_tracks(conn, track_ids)
        runs = generations.runs(ordinals) if ordinals else []
        spans = generations.generation_spans(conn)

        return render_template(
            "entity_group.html",
            tier=tier,
            group_id=group_id,
            rep=rep,
            artist_credits=artist_credits,
            pinned=tree["pinned"],
            track_count=len(track_ids),
            breadcrumb=breadcrumb,
            stats=entities.play_stats(conn, track_ids),
            playlists=entities.playlists_for_tracks(conn, track_ids),
            tracks_by_id=tracks_by_id,
            track_scores=track_scores,
            # "song" isn't a materialized tier (§9.1) -- it aggregates at
            # query time from its member versions, same as album/artist/
            # playlist, so it needs song_scores() rather than a direct lookup.
            score=(
                scoring.song_scores(conn, [group_id]).get(group_id)
                if tier == "song"
                else scoring.get_both(conn, tier, group_id)
            ),
            spans=spans,
            ordinals=ordinals,
            tenure=max((end - start + 1 for start, end in runs), default=0),
            total_generations=len(ordinals),
            run_count=len(runs),
            tree=tree,
            member_tracks=member_tracks,
        )

    @app.route("/track/<track_id>", endpoint="track_page")
    def track_page(track_id):
        conn = db.get_db()
        if conn.execute("SELECT 1 FROM track WHERE track_id = ?", (track_id,)).fetchone() is None:
            abort(404, description="Track not found.")

        canonical.ensure_track_groups(conn)
        conn.commit()

        track = canonical.track_display(conn, track_id)
        track_artists = canonical.artist_credits_for_tracks(conn, [track_id]).get(track_id, [])
        groups = canonical.groups_for_track(conn, track_id)

        memberships = conn.execute(
            """
            SELECT m.playlist_id, s.name AS playlist_name, m.added_at, m.removed_at, m.position
            FROM membership m
            JOIN snapshot s ON s.playlist_id = m.playlist_id
            WHERE m.track_id = ?
            ORDER BY s.name COLLATE NOCASE, m.added_at
            """,
            (track_id,),
        ).fetchall()

        aliases = conn.execute(
            "SELECT requested_uri FROM track_uri_alias WHERE track_id = ? ORDER BY requested_uri",
            (track_id,),
        ).fetchall()

        return render_template(
            "entity_track.html",
            track=track,
            track_artists=track_artists,
            groups=groups,
            memberships=memberships,
            stats=entities.play_stats(conn, [track_id]),
            aliases=aliases,
            score=scoring.get_both(conn, "track", track_id),
        )

    @app.route("/album/<album_id>", endpoint="album_page")
    def album_page(album_id):
        conn = db.get_db()

        def _load():
            return conn.execute(
                "SELECT album_id, name, album_type, release_date, total_tracks, image_url, "
                "external_url, tracklist_json, tracklist_pulled_at FROM album WHERE album_id = ?",
                (album_id,),
            ).fetchone()

        album = _load()
        if album is None:
            abort(404, description="Album not found.")

        if album["tracklist_pulled_at"] is None:
            entities.fetch_album_tracklist(conn, album_id)
            album = _load()

        artist_rows = conn.execute(
            "SELECT COALESCE(aa.canonical_artist_id, ab.artist_id) AS artist_id, ar.name "
            "FROM album_artist ab "
            "LEFT JOIN artist_alias aa ON aa.artist_id = ab.artist_id "
            "JOIN artist ar ON ar.artist_id = COALESCE(aa.canonical_artist_id, ab.artist_id) "
            "WHERE ab.album_id = ? ORDER BY ab.position",
            (album_id,),
        ).fetchall()

        owned_ids = [
            r["track_id"]
            for r in conn.execute("SELECT track_id FROM track WHERE album_id = ?", (album_id,))
        ]
        # Owned tracks only: an unowned tracklist entry's artist ids come
        # straight from Spotify's simplified object, with no guarantee we've
        # ever ingested an `artist` row for them (fetch_album_tracklist never
        # upserts artists), so linking those would often 404.
        artist_credits = canonical.artist_credits_for_tracks(conn, owned_ids)

        canonical.ensure_track_groups(conn)
        conn.commit()

        # An owned row built from Symr's own `track` table. Used both when the
        # fetch never succeeded and for the tracks the fetched page didn't
        # reach, so there's one way to render a track Symr actually holds.
        def _owned_rows(track_ids):
            if not track_ids:
                return []
            placeholders = ",".join("?" for _ in track_ids)
            out = []
            for r in conn.execute(
                "SELECT t.track_id, t.name, t.track_number, t.disc_number, t.duration_ms, t.explicit, "
                "       COALESCE(ta.artists, '') AS artists "
                "FROM track t LEFT JOIN track_artists ta ON ta.track_id = t.track_id "
                f"WHERE t.track_id IN ({placeholders})",
                list(track_ids),
            ):
                g = canonical.groups_for_track(conn, r["track_id"])
                out.append(
                    {
                        "owned": True,
                        "track_id": r["track_id"],
                        "version_id": g["version"] if g else None,
                        "name": r["name"],
                        "artists": r["artists"],
                        "duration_ms": r["duration_ms"],
                        "explicit": r["explicit"],
                        "track_number": r["track_number"],
                        "disc_number": r["disc_number"],
                    }
                )
            return out

        rows = []
        tracklist = json.loads(album["tracklist_json"]) if album["tracklist_json"] else None
        fetched = tracklist is not None
        if fetched:
            owned_set = set(owned_ids)
            for item in tracklist:
                tid = item.get("id")
                is_owned = bool(tid and tid in owned_set)
                version_id = None
                if is_owned:
                    g = canonical.groups_for_track(conn, tid)
                    version_id = g["version"] if g else None
                rows.append(
                    {
                        "owned": is_owned,
                        "track_id": tid,
                        "version_id": version_id,
                        "name": item.get("name"),
                        "artists": ", ".join(a.get("name", "") for a in item.get("artists") or []),
                        "duration_ms": item.get("duration_ms"),
                        "explicit": item.get("explicit"),
                        "track_number": item.get("track_number"),
                        "disc_number": item.get("disc_number"),
                    }
                )
        else:
            # Never successfully fetched -- show only what's independently
            # known from Symr's own library rather than nothing at all.
            rows = _owned_rows(owned_ids)
        rows.sort(key=lambda r: (r["disc_number"] or 1, r["track_number"] or 0))

        # Owned tracks the fetched page didn't contain (§5.2). An album past 50
        # tracks can easily hold the one track Symr knows beyond the first page,
        # and without these the tracklist contradicts its own "N of M known"
        # header. Costs no request -- these come from the `track` table.
        shown = {r["track_id"] for r in rows if r["track_id"]}
        appended = _owned_rows([tid for tid in owned_ids if tid not in shown])
        appended.sort(key=lambda r: (r["disc_number"] or 1, r["track_number"] or 0))

        track_names = {r["track_id"]: r["name"] for r in rows + appended if r["owned"]}

        return render_template(
            "entity_album.html",
            album=album,
            artists=artist_rows,
            rows=rows,
            appended=appended,
            track_artist_credits=artist_credits,
            track_names=track_names,
            fetched=fetched,
            known_count=len(owned_ids),
            stats=entities.play_stats(conn, owned_ids),
            playlists=entities.playlists_for_tracks(conn, owned_ids),
            score=scoring.album_scores(conn, [album_id]).get(album_id, {"all_time": 0.0, "recent": 0.0}),
        )

    @app.route("/artist/<artist_id>", endpoint="artist_page")
    def artist_page(artist_id):
        conn = db.get_db()

        alias_row = conn.execute(
            "SELECT canonical_artist_id FROM artist_alias WHERE artist_id = ?", (artist_id,)
        ).fetchone()
        if alias_row is not None:
            return redirect(url_for("artist_page", artist_id=alias_row["canonical_artist_id"]))

        def _load():
            return conn.execute(
                "SELECT artist_id, name, external_url, image_url, detail_pulled_at "
                "FROM artist WHERE artist_id = ?",
                (artist_id,),
            ).fetchone()

        artist = _load()
        if artist is None:
            abort(404, description="Artist not found.")

        if artist["detail_pulled_at"] is None:
            entities.fetch_artist_image(conn, artist_id)
            artist = _load()

        merged_ids = [
            r["artist_id"]
            for r in conn.execute(
                "SELECT artist_id FROM artist_alias WHERE canonical_artist_id = ? ORDER BY artist_id",
                (artist_id,),
            )
        ]

        canonical.ensure_track_groups(conn)
        conn.commit()

        credit_rows = conn.execute(
            "SELECT tar.track_id, tar.role, tg.version_id FROM track_artist_role tar "
            "JOIN track_group tg ON tg.track_id = tar.track_id WHERE tar.artist_id = ?",
            (artist_id,),
        ).fetchall()
        all_track_ids = [r["track_id"] for r in credit_rows]

        primary_versions, featured_versions = set(), set()
        for r in credit_rows:
            (primary_versions if r["role"] == "primary" else featured_versions).add(r["version_id"])
        # A version already counted as primary (via some other member track)
        # doesn't also need a featured row -- primary is the more informative
        # badge for this artist.
        featured_versions -= primary_versions

        def _version_rows(version_ids):
            scores = scoring.scores_for_tier(conn, "version", list(version_ids))
            out = []
            for vid in version_ids:
                rid = canonical.representative(conn, vid)
                if rid is None:
                    continue
                out.append({"version_id": vid, **canonical.track_display(conn, rid)})
            out.sort(
                key=lambda t: (
                    -scores.get(t["version_id"], {}).get("all_time", 0.0),
                    (t["name"] or "").casefold(),
                )
            )
            return out

        album_rows = conn.execute(
            "SELECT a.album_id, a.name, a.release_date, a.image_url FROM resolved_album_artist raa "
            "JOIN album a ON a.album_id = raa.album_id "
            "WHERE raa.artist_id = ?",
            (artist_id,),
        ).fetchall()
        album_scores = scoring.album_scores(conn, [a["album_id"] for a in album_rows])
        album_rows = sorted(
            album_rows,
            key=lambda a: (
                -album_scores.get(a["album_id"], {}).get("all_time", 0.0),
                (a["name"] or "").casefold(),
            ),
        )

        playlists_seen = {}
        for row in entities.playlists_for_tracks(conn, all_track_ids):
            playlists_seen.setdefault(row["playlist_id"], row)

        # Counted off the rendered rows, not off credit_rows: the lists below
        # are one row per version group, and _version_rows drops a group whose
        # representative resolves to nothing. Counting anything else lets the
        # header disagree with the list directly under it.
        primary_rows = _version_rows(primary_versions)
        featured_rows = _version_rows(featured_versions)

        return render_template(
            "entity_artist.html",
            artist=artist,
            merged_ids=merged_ids,
            version_count=len(primary_rows) + len(featured_rows),
            album_count=len(album_rows),
            primary_tracks=primary_rows,
            featured_tracks=featured_rows,
            albums=album_rows,
            playlists=list(playlists_seen.values()),
            ordinals=generations.presence_for_tracks(conn, all_track_ids),
            spans=generations.generation_spans(conn),
            stats=entities.play_stats(conn, all_track_ids),
            score=scoring.artist_group_score(conn, [artist_id]),
        )

    @app.route("/playlist/<playlist_id>", endpoint="playlist_page")
    def playlist_page(playlist_id):
        conn = db.get_db()
        playlist = conn.execute(
            "SELECT * FROM snapshot WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()
        if playlist is None:
            abort(404, description="Playlist not found.")

        canonical.ensure_track_groups(conn)
        conn.commit()

        rows = conn.execute(
            """
            SELECT m.id, m.track_id, t.name, t.album_id, a.name AS album_name, t.duration_ms,
                   m.added_at, m.removed_at, m.position
            FROM membership m
            JOIN track t ON t.track_id = m.track_id
            LEFT JOIN album a ON a.album_id = t.album_id
            WHERE m.playlist_id = ?
            ORDER BY m.position
            """,
            (playlist_id,),
        ).fetchall()

        version_by_track = {}
        for r in rows:
            g = canonical.groups_for_track(conn, r["track_id"])
            version_by_track[r["track_id"]] = g["version"] if g else None
        artist_credits = canonical.artist_credits_for_tracks(conn, [r["track_id"] for r in rows])

        live_track_ids = [r["track_id"] for r in rows if r["removed_at"] is None]
        totals = conn.execute(
            "SELECT SUM(t.duration_ms) AS runtime, MIN(m.added_at) AS first_added, "
            "MAX(m.added_at) AS last_added FROM membership m JOIN track t ON t.track_id = m.track_id "
            "WHERE m.playlist_id = ? AND m.removed_at IS NULL",
            (playlist_id,),
        ).fetchone()

        generation = conn.execute(
            "SELECT ordinal FROM generation WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()

        gen_view = None
        if generation is not None and request.args.get("generation") == "1":
            tier = request.args.get("tier", "version")
            tier = tier if tier in ("version", "song") else "version"
            column = _GROUP_TIER_COLUMN[tier]

            spans = generations.generation_spans(conn)
            idx = next(i for i, s in enumerate(spans) if s["ordinal"] == generation["ordinal"])

            def _groups_at(ordinal):
                return {
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT {column} FROM generation_presence WHERE ordinal = ?",
                        (ordinal,),
                    )
                }

            this_groups = _groups_at(generation["ordinal"])
            prev_groups = _groups_at(spans[idx - 1]["ordinal"]) if idx > 0 else set()
            next_groups = _groups_at(spans[idx + 1]["ordinal"]) if idx + 1 < len(spans) else None

            def _summaries(ids):
                out = []
                for gid in sorted(ids):
                    rid = canonical.representative(conn, gid)
                    if rid is None:
                        continue
                    out.append({"group_id": gid, **canonical.track_display(conn, rid)})
                return out

            gen_view = {
                "ordinal": generation["ordinal"],
                "tier": tier,
                "span": spans[idx],
                "carried": _summaries(this_groups & prev_groups),
                "new": _summaries(this_groups - prev_groups),
                "survived_out": len(this_groups & next_groups) if next_groups is not None else None,
            }

        return render_template(
            "entity_playlist.html",
            playlist=playlist,
            rows=rows,
            version_by_track=version_by_track,
            artist_credits=artist_credits,
            totals=totals,
            stats=entities.play_stats(conn, live_track_ids),
            generation=generation,
            gen_view=gen_view,
            score=scoring.playlist_scores(conn, [playlist_id]).get(
                playlist_id, {"all_time": 0.0, "recent": 0.0}
            ),
        )

    @app.route("/search", endpoint="search_page")
    def search_page():
        conn = db.get_db()
        q = request.args.get("q", "").strip()

        songs, albums, artists_result, playlists_result = [], [], [], []
        if q:
            canonical.ensure_track_groups(conn)
            conn.commit()
            like = f"%{q}%"

            # All four groups here rank by score before capping at 50, not
            # after: a name-ordered cap returns the alphabetically-first 50
            # matches rather than the best 50 (docs/specs/scoring-H.md §11.1).
            # Every match is fetched (no SQL LIMIT) so the ranking sees the
            # whole result set, which searching bounds to a manageable size.
            track_rows = conn.execute(
                "SELECT t.track_id FROM track t WHERE t.name LIKE ? "
                "   OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id) "
                "              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "
                "ORDER BY t.name COLLATE NOCASE",
                (like, like),
            ).fetchall()
            seen_versions = {}
            for row in track_rows:
                groups = canonical.groups_for_track(conn, row["track_id"])
                if not groups or groups["version"] in seen_versions:
                    continue
                seen_versions[groups["version"]] = row["track_id"]
            version_scores = scoring.scores_for_tier(conn, "version", list(seen_versions))
            ranked_versions = sorted(
                seen_versions, key=lambda vid: -version_scores.get(vid, {}).get("all_time", 0.0)
            )[:50]
            songs = []
            for vid in ranked_versions:
                rep_id = canonical.representative(conn, vid) or seen_versions[vid]
                songs.append({"version_id": vid, **canonical.track_display(conn, rep_id)})

            album_rows = conn.execute(
                "SELECT album_id, name, image_url FROM album WHERE name LIKE ? "
                "ORDER BY name COLLATE NOCASE",
                (like,),
            ).fetchall()
            album_score_map = scoring.album_scores(conn, [a["album_id"] for a in album_rows])
            albums = sorted(
                album_rows, key=lambda a: -album_score_map.get(a["album_id"], {}).get("all_time", 0.0)
            )[:50]

            artist_rows = conn.execute(
                "SELECT ar.artist_id, ar.name, COALESCE(aa.canonical_artist_id, ar.artist_id) AS resolved_id "
                "FROM artist ar LEFT JOIN artist_alias aa ON aa.artist_id = ar.artist_id "
                "WHERE ar.name LIKE ?",
                (like,),
            ).fetchall()
            seen_artists = {}
            for row in artist_rows:
                if row["resolved_id"] not in seen_artists:
                    canonical_artist = conn.execute(
                        "SELECT artist_id, name FROM artist WHERE artist_id = ?", (row["resolved_id"],)
                    ).fetchone()
                    seen_artists[row["resolved_id"]] = dict(canonical_artist)
            artist_score_map = scoring.artist_scores(conn, list(seen_artists))
            artists_result = sorted(
                seen_artists.values(),
                key=lambda a: (
                    -artist_score_map.get(a["artist_id"], {}).get("all_time", 0.0),
                    (a["name"] or "").casefold(),
                ),
            )[:50]

            playlist_rows = conn.execute(
                "SELECT playlist_id, name, image_url FROM snapshot WHERE name LIKE ? "
                "ORDER BY name COLLATE NOCASE",
                (like,),
            ).fetchall()
            playlist_score_map = scoring.playlist_scores(conn, [p["playlist_id"] for p in playlist_rows])
            playlists_result = sorted(
                playlist_rows,
                key=lambda p: -playlist_score_map.get(p["playlist_id"], {}).get("all_time", 0.0),
            )[:50]

        return render_template(
            "search.html",
            q=q,
            songs=songs,
            albums=albums,
            artists=artists_result,
            playlists=playlists_result,
        )

    @app.route("/dev")
    def dev_index():
        return render_template("dev.html", active="dev")

    @app.route("/dev/scoring", endpoint="dev_scoring")
    def dev_scoring_index():
        conn = db.get_db()
        return render_template(
            "scoring.html",
            active="dev_scoring",
            tier_counts=scoring.tier_counts(conn),
            recompute_status=scoring.recompute_status(),
        )

    @app.route("/api/scoring/recompute", methods=["POST"])
    def api_scoring_recompute():
        # Synchronous (~1s, §2.11) -- no job slot, no background thread, same
        # as every other scoring.recompute() call site. A failure propagates
        # to the global exception handler, which renders it as the same
        # {"error", "detail"} JSON shape every other /api/* failure uses;
        # scoring.js reads either that or this success shape off the response.
        conn = db.get_db()
        scoring.recompute(conn)
        return jsonify({"tier_counts": scoring.tier_counts(conn), "status": scoring.recompute_status()})

    @app.route("/dev/artists", endpoint="dev_artists")
    def artists_index():
        conn = db.get_db()
        return render_template(
            "artists.html",
            active="dev_artists",
            pairs=artists.candidate_pairs(conn),
            merged=artists.merged_groups(conn),
        )

    @app.route("/dev/import", endpoint="dev_import")
    def history_import_index():
        conn = db.get_db()
        return render_template(
            "history_import.html",
            active="dev_import",
            coverage=history_import.coverage_counts(conn),
            imports=history_import.import_rows(conn),
            has_upload=history_import.latest_upload(conn) is not None,
        )

    @app.route("/dev/roundtrip", endpoint="dev_roundtrip")
    def roundtrip_index():
        conn = db.get_db()
        return render_template(
            "roundtrip.html",
            active="dev_roundtrip",
            counts=roundtrip.counts(conn),
            runs=roundtrip.run_rows(conn),
            failures=roundtrip.failed_uri_rows(conn),
            review_rows=roundtrip.manual_alias_rows(conn),
            state_labels=roundtrip.STATE_LABELS,
            loader_name=roundtrip.LOADER_NAME,
        )

    @app.route("/dev/canonical", endpoint="dev_canonical")
    def canonical_index():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        q = request.args.get("q", "").strip()
        show_singletons = request.args.get("singletons") == "1"
        cross_q = request.args.get("cross", "").strip()
        search_q = request.args.get("search", "").strip()
        expand_song_id = request.args.get("expand", type=int)

        reviewed_row = conn.execute(
            "SELECT COUNT(*) AS c, MAX(decided_at) AS latest FROM reviewed_pair"
        ).fetchone()

        all_song_groups = canonical.song_group_rows(
            conn, query=q, include_singletons=show_singletons
        )
        # Ranked here rather than in canonical.py: scoring is cheap (one read
        # of the materialized version tier plus a Python combine() per song),
        # so the listing still ranks before the expensive hydration step below
        # touches only the capped slice.
        song_scores = scoring.song_scores(conn, [g["song_id"] for g in all_song_groups])
        for g in all_song_groups:
            g["score"] = song_scores.get(g["song_id"], {}).get("all_time", 0.0)
        all_song_groups.sort(key=lambda g: (-g["score"], g["song_id"]))
        shown = _cap_listing(all_song_groups, q)
        # A deep link to a group past the cap would otherwise land on a page
        # that doesn't contain it.
        if expand_song_id and not any(g["song_id"] == expand_song_id for g in shown):
            shown = [g for g in all_song_groups if g["song_id"] == expand_song_id] + shown
        groups = canonical.hydrate_song_groups(conn, shown)

        # The listing's real cost: one four-tier tree per group rendered. Built
        # for the capped slice only, which is the point of the cap.
        trees = {g["song_id"]: canonical.song_tree(conn, g["song_id"]) for g in groups}

        # Detection is deliberately absent here: it cost ~350ms of this page's
        # ~500ms and feeds only the cross-artist pane and the two unreviewed
        # counts, so /api/canonical/cross/listing serves both after paint.

        search_results = []
        if search_q:
            like = f"%{search_q}%"
            rows = conn.execute(
                "SELECT t.track_id FROM track t WHERE t.name LIKE ? "
                "   OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id) "
                "              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "
                "ORDER BY t.name COLLATE NOCASE",
                (like, like),
            ).fetchall()
            row_scores = scoring.scores_for_tier(conn, "track", [r["track_id"] for r in rows])
            ranked_rows = sorted(
                rows, key=lambda r: -row_scores.get(r["track_id"], {}).get("all_time", 0.0)
            )[:100]
            for row in ranked_rows:
                info = canonical.track_display(conn, row["track_id"])
                info["groups"] = canonical.groups_for_track(conn, row["track_id"])
                search_results.append(info)

        # Every track id whose artist names this page might render as links,
        # gathered up front so artist_credits_for_tracks is one batched query
        # rather than one per track (see its own docstring for why that
        # matters on this page specifically).
        credit_track_ids = set()
        for g in groups:
            if g["representative_track_id"]:
                credit_track_ids.add(g["representative_track_id"])
            credit_track_ids.update(trees[g["song_id"]]["track_ids"])
        credit_track_ids.update(t["track_id"] for t in search_results)

        return render_template(
            "canonical.html",
            active="dev_canonical",
            total_tracks=conn.execute("SELECT COUNT(*) FROM track").fetchone()[0],
            tier_counts=canonical.tier_counts(conn),
            reviewed_count=reviewed_row["c"],
            reviewed_latest=reviewed_row["latest"],
            groups=groups,
            group_total=len(all_song_groups),
            trees=trees,
            show_singletons=show_singletons,
            q=q,
            cross_q=cross_q,
            search_q=search_q,
            search_results=search_results,
            expand_song_id=expand_song_id,
            last_auto_run=canonical_autogroup.last_run(conn),
            auto_grouped=canonical.auto_grouped_ids(conn),
            pending_tier_count=len(canonical_detect.pending_song_ids(conn)),
            artist_credits=canonical.artist_credits_for_tracks(conn, list(credit_track_ids)),
        )

    @app.route("/dev/canonical/group/<int:group_id>")
    def canonical_group_deep_link(group_id):
        conn = db.get_db()
        row = conn.execute(
            "SELECT tier FROM canonical_group WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            abort(404, description="No such canonical group.")

        members = canonical.group_members(conn, group_id)
        if not members:
            abort(404, description="Group has no members.")
        song_id = canonical.groups_for_track(conn, members[0])["song"]

        return redirect(url_for("dev_canonical", expand=song_id) + f"#group-{group_id}")

    @app.route("/dev/canonical/review")
    def canonical_review():
        tracks_param = request.args.get("tracks")
        if tracks_param is not None and len([t for t in tracks_param.split(",") if t]) < 2:
            abort(400, description="tracks= needs at least 2 track ids")
        return render_template("canonical_review.html", active="dev_canonical")

    @app.route("/dev/canonical/cross")
    def canonical_cross():
        return render_template("canonical_cross.html", active="dev_canonical")

    @app.route("/api/canonical/cross")
    def api_canonical_cross():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        tracks_param = request.args.get("tracks")
        if tracks_param is not None:
            item = canonical_detect.cross_bucket_for(
                conn, [t for t in tracks_param.split(",") if t]
            )
            if item is None:
                abort(400, description="tracks= needs at least 2 known track ids")
            return jsonify({"items": [item], "pending_count": 0})

        return jsonify(
            {
                "items": canonical_detect.cross_buckets(conn),
                # Drives the redirect when the queue empties: assignments made
                # in an earlier sitting would otherwise be unreachable until
                # some future session happened to finish the queue.
                "pending_count": len(canonical_detect.pending_song_ids(conn)),
            }
        )

    @app.route("/api/canonical/cross/listing")
    def api_canonical_cross_listing():
        """The /dev/canonical cross-artist pane, plus the two unreviewed counts
        that sit in its Stats panel. Split out of the page because all three
        need full detection (~350ms), which is most of what that page used to
        spend before painting anything.

        Returns rendered HTML rather than JSON rows so the entity links in it
        stay the same entity_link macro every other page uses.

        Assumes the page's own ensure_track_groups() has already run -- this is
        only ever fetched by that page, and a GET returning a listing has no
        business taking a write lock."""
        conn = db.get_db()
        cross_q = request.args.get("cross", "").strip()

        unreviewed_main, unreviewed_cross, all_groups = canonical_detect.canonical_page_groups(conn)
        matched = canonical_detect.filter_groups(
            [g for g in all_groups if g["cross_artist"]], cross_q
        )
        shown = _cap_listing(matched, cross_q)

        credit_track_ids = set()
        for g in shown:
            credit_track_ids.update(g["track_ids"])

        return jsonify(
            {
                "html": render_template(
                    "_canonical_cross.html",
                    groups=shown,
                    total=len(matched),
                    cross_q=cross_q,
                    artist_credits=canonical.artist_credits_for_tracks(
                        conn, list(credit_track_ids)
                    ),
                ),
                "total": len(matched),
                "unreviewed_main": len(unreviewed_main),
                "unreviewed_cross": len(unreviewed_cross),
            }
        )

    @app.route("/api/canonical/cross/apply", methods=["POST"])
    def api_canonical_cross_apply():
        body = request.get_json()
        track_ids = body.get("track_ids") or []
        assignments = body.get("assignments") or []
        if len(track_ids) < 2:
            abort(400, description="track_ids needs at least 2 track ids")

        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        bucket = set(track_ids)

        for i, assignment in enumerate(assignments):
            newcomers = [t for t in (assignment.get("track_ids") or [])]
            # The bucket is the only thing this page is allowed to touch.
            outside = [t for t in newcomers if t not in bucket]
            if outside:
                abort(400, description=f"tracks not in this bucket: {sorted(outside)}")

            song_id = assignment.get("song_id")
            members = canonical.group_members(conn, song_id) if song_id else []
            targets = list(dict.fromkeys(members + newcomers))
            if len(targets) < 2:
                continue

            # One shared song label; every track keeps its existing version,
            # recording and release group. Passing the newcomer's current ids
            # rather than fresh singletons is deliberate: a song-tier decision
            # must not silently detach it from a finer-tier group it is
            # already in (see spec E §4.4).
            labels = {}
            for track_id in targets:
                current = canonical.groups_for_track(conn, track_id)
                if current is None:
                    abort(400, description=f"no track_group row for {track_id}")
                labels[track_id] = {
                    "song": f"cross:{i}",
                    "version": str(current["version"]),
                    "recording": str(current["recording"]),
                    "release": str(current["release"]),
                }
            try:
                canonical.apply_partition(conn, labels)
            except ValueError as e:
                abort(400, description=str(e))

            for track_id in newcomers:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_tier_review (track_id) VALUES (?)",
                    (track_id,),
                )

        # Cross-component pairs only, not every pair in the bucket -- that's
        # the exact set _cross_component_reviewed checks, so it's what stops
        # the bucket resurfacing until another newcomer arrives. A
        # within-component pair (same-artist tracks) is the main queue's
        # job: marking it here would suppress it from ever being asked there
        # (spec M §1).
        canonical.mark_reviewed_pairs(conn, canonical_detect.cross_component_pairs(conn, track_ids))
        conn.commit()
        scoring.recompute(conn)
        return jsonify({"ok": True})

    @app.route("/api/canonical/queue")
    def api_canonical_queue():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        tracks_param = request.args.get("tracks")
        if tracks_param is not None:
            track_ids = [t for t in tracks_param.split(",") if t]
            if len(track_ids) < 2:
                abort(400, description="tracks= needs at least 2 track ids")
            placeholders = ",".join("?" for _ in track_ids)
            existing = {
                row["track_id"]
                for row in conn.execute(
                    f"SELECT track_id FROM track WHERE track_id IN ({placeholders})", track_ids
                )
            }
            missing = set(track_ids) - existing
            if missing:
                abort(400, description=f"unknown track ids: {sorted(missing)}")
            queue_name = "ad-hoc"
            items = [canonical_detect.ad_hoc_group(conn, track_ids)]
        elif request.args.get("queue") == "pending":
            queue_name = "pending"
            items = canonical_detect.pending_tier_items(conn)
        else:
            queue_name = "main"
            items = canonical_detect.candidate_groups(conn)

        return jsonify({"queue": queue_name, "items": items})

    @app.route("/api/canonical/apply", methods=["POST"])
    def api_canonical_apply():
        body = request.get_json()
        track_ids = body.get("track_ids") or []
        labels = body.get("labels") or {}
        pin = body.get("pin_representative")

        conn = db.get_db()
        try:
            result = canonical.apply_partition(conn, labels)
        except ValueError as e:
            abort(400, description=str(e))
        canonical.mark_reviewed(conn, track_ids)
        if pin:
            canonical.pin_representative(conn, pin)
        # A pending tier-review item is exactly the song group these tracks are
        # in, so committing it is what the pending row was waiting for.
        for track_id in track_ids:
            conn.execute("DELETE FROM pending_tier_review WHERE track_id = ?", (track_id,))
        conn.commit()
        scoring.recompute(conn)
        return jsonify(result)

    @app.route("/api/canonical/autogroup/preview")
    def api_canonical_autogroup_preview():
        return jsonify(canonical_autogroup.preview(db.get_db()))

    @app.route("/api/canonical/autogroup", methods=["POST"])
    def api_canonical_autogroup():
        # Synchronous: ~1.2s with apply_partition(cleanup=False), so it needs
        # neither the job slot nor a background thread.
        return jsonify(canonical_autogroup.run(db.get_db()))

    @app.route("/api/canonical/autogroup/undo", methods=["POST"])
    def api_canonical_autogroup_undo():
        try:
            return jsonify(canonical_autogroup.undo(db.get_db()))
        except ValueError as e:
            abort(400, description=str(e))

    @app.route("/api/canonical/pin", methods=["POST"])
    def api_canonical_pin():
        body = request.get_json()
        track_id = body.get("track_id")
        if not track_id:
            abort(400, description="track_id required")

        conn = db.get_db()
        try:
            canonical.pin_representative(conn, track_id)
        except ValueError as e:
            abort(400, description=str(e))
        conn.commit()
        scoring.recompute(conn)
        return jsonify({"ok": True})

    @app.route("/dev/snapshot", endpoint="dev_snapshot")
    def snapshot_index():
        conn = db.get_db()
        playlists = conn.execute("SELECT * FROM snapshot").fetchall()
        playlist_score_map = scoring.playlist_scores(conn, [p["playlist_id"] for p in playlists])
        playlists = sorted(
            playlists,
            key=lambda p: (
                -playlist_score_map.get(p["playlist_id"], {}).get("all_time", 0.0),
                (p["name"] or "").casefold(),
            ),
        )

        q = request.args.get("q", "").strip()
        track_matches = []
        if q:
            like = f"%{q}%"
            track_matches = conn.execute(
                """
                SELECT t.track_id, t.name, COALESCE(ta.artists, '') AS artists, COUNT(m.id) AS appearances
                FROM track t
                JOIN membership m ON m.track_id = t.track_id AND m.removed_at IS NULL
                LEFT JOIN track_artists ta ON ta.track_id = t.track_id
                WHERE t.name LIKE ?
                   OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id)
                              WHERE x.track_id = t.track_id AND ar.name LIKE ?)
                GROUP BY t.track_id
                ORDER BY t.name COLLATE NOCASE
                LIMIT 50
                """,
                (like, like),
            ).fetchall()

        changes = conn.execute(
            """
            SELECT m.playlist_id, s.name AS playlist_name, m.track_id, t.name AS track_name,
                   COALESCE(ta.artists, '') AS artists, m.added_at, m.removed_at,
                   COALESCE(m.removed_at, m.added_at) AS event_at,
                   CASE WHEN m.removed_at IS NOT NULL THEN 'removed' ELSE 'added' END AS kind
            FROM membership m
            JOIN snapshot s ON s.playlist_id = m.playlist_id
            JOIN track t ON t.track_id = m.track_id
            LEFT JOIN track_artists ta ON ta.track_id = t.track_id
            ORDER BY event_at DESC
            LIMIT 50
            """
        ).fetchall()

        return render_template(
            "snapshot.html",
            active="dev_snapshot",
            playlists=playlists,
            summary=snapshot.summary_counts(conn),
            query=q,
            track_matches=track_matches,
            changes=changes,
            liked_playlist_id=snapshot.LIKED_PLAYLIST_ID,
            pending_generation=generations.pending_new_generation(conn),
        )

    # -- Generations & tenure -------------------------------------------

    def _generations_tier_arg():
        tier = request.args.get("tier", "version")
        return tier if tier in ("version", "song") else "version"

    @app.route("/dev/generations", endpoint="dev_generations")
    def dev_generations_index():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        tier = _generations_tier_arg()
        return render_template(
            "generations.html",
            active="dev_generations",
            tier=tier,
            gens=generations.generations(conn, tier=tier),
            pending_generation=generations.pending_new_generation(conn),
        )

    _TENURE_SORT_KEYS = {
        "tenure": "tenure", "total": "total_generations", "runs": "run_count", "score": "score",
    }
    _TENURE_PAGE_SIZE = 100

    @app.route("/dev/generations/tenure", endpoint="dev_generations_tenure")
    def dev_generations_tenure():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        tier = _generations_tier_arg()
        sort = request.args.get("sort", "tenure")
        if sort not in _TENURE_SORT_KEYS:
            sort = "tenure"
        page = request.args.get("page", 1, type=int) or 1
        page = max(page, 1)

        spans = generations.generation_spans(conn)

        all_tenures = generations.tenures(conn, tier=tier)
        # Every row's score, computed up front: the sort below runs before
        # pagination, same as tenure/total/runs (docs/specs/scoring-H.md
        # §11.1). "song" aggregates at query time; "version" is a direct
        # materialized lookup.
        if tier == "version":
            score_map = scoring.scores_for_tier(conn, "version", [t["group_id"] for t in all_tenures])
        else:
            score_map = scoring.song_scores(conn, [t["group_id"] for t in all_tenures])
        for t in all_tenures:
            t["score"] = score_map.get(t["group_id"], {}).get("all_time", 0.0)

        sort_key = _TENURE_SORT_KEYS[sort]
        # group_id as the tiebreak keeps paging stable across requests.
        all_tenures.sort(key=lambda t: (-t[sort_key], t["group_id"]))

        total = len(all_tenures)
        total_pages = max(1, -(-total // _TENURE_PAGE_SIZE))
        page = min(page, total_pages)
        start = (page - 1) * _TENURE_PAGE_SIZE
        page_slice = all_tenures[start : start + _TENURE_PAGE_SIZE]

        rows = []
        for t in page_slice:
            rep_id = canonical.representative(conn, t["group_id"])
            present = {o for start_o, end_o in t["runs"] for o in range(start_o, end_o + 1)}
            rows.append(
                {
                    **t,
                    "representative": canonical.track_display(conn, rep_id) if rep_id else None,
                    "present_ordinals": present,
                }
            )

        return render_template(
            "generations_tenure.html",
            active="dev_generations",
            tier=tier,
            sort=sort,
            page=page,
            total_pages=total_pages,
            total=total,
            generation_count=len(spans),
            spans=spans,
            rows=rows,
        )

    @app.route("/dev/generations/confirm", methods=["POST"], endpoint="dev_generations_confirm")
    def dev_generations_confirm():
        playlist_id = request.form.get("playlist_id")
        decision = request.form.get("decision")
        # Closed set rather than trusting an arbitrary path back from the
        # form, even though it's this app's own hidden field.
        return_to = {"dev_snapshot": "dev_snapshot", "dev_generations": "dev_generations"}.get(
            request.form.get("return_to"), "dev_generations"
        )
        if not playlist_id or decision not in ("yes", "no"):
            abort(400, description="playlist_id and a yes/no decision are required")

        conn = db.get_db()
        try:
            if decision == "yes":
                generations.confirm_generation(conn, playlist_id)
            else:
                generations.decline_generation(conn, playlist_id)
        except ValueError as e:
            abort(400, description=str(e))
        conn.commit()
        # Only a new generation is a scoring input (tenure, §4.1) -- declining
        # just mutes the prompt and touches nothing scoring reads.
        if decision == "yes":
            scoring.recompute(conn)
        return redirect(url_for(return_to))

    # -- OAuth ----------------------------------------------------------

    @app.route("/login")
    def login():
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        auth_manager = get_auth_manager()
        return redirect(auth_manager.get_authorize_url(state=state))

    @app.route("/callback")
    def callback():
        error = request.args.get("error")
        if error:
            abort(400, description=f"Spotify authorization failed: {error}")

        expected = session.pop("oauth_state", None)
        if not expected or request.args.get("state") != expected:
            abort(400, description="Invalid OAuth state.")

        code = request.args.get("code")
        if not code:
            abort(400, description="Missing authorization code.")

        auth_manager = get_auth_manager()
        auth_manager.get_access_token(code, as_dict=False)
        return redirect(url_for("index"))

    # -- Snapshot ---------------------------------------------------------

    @app.route("/api/snapshot/pull", methods=["POST"])
    def pull_snapshot():
        if get_spotify_client() is None:
            return jsonify({"error": "not_authenticated"}), 401
        if not snapshot.start_full_pull():
            return jsonify({"error": "already_running"}), 409
        return jsonify({"started": True})

    @app.route("/api/snapshot/refresh", methods=["POST"])
    def refresh_snapshot():
        if get_spotify_client() is None:
            return jsonify({"error": "not_authenticated"}), 401
        if not snapshot.start_refresh():
            return jsonify({"error": "already_running"}), 409
        return jsonify({"started": True})

    @app.route("/api/snapshot/backfill", methods=["POST"])
    def backfill_snapshot():
        if get_spotify_client() is None:
            return jsonify({"error": "not_authenticated"}), 401
        if not snapshot.start_backfill():
            return jsonify({"error": "already_running"}), 409
        return jsonify({"started": True})

    @app.route("/api/snapshot/status")
    def snapshot_status():
        conn = db.get_db()
        status = snapshot.get_status()
        status.update(snapshot.summary_counts(conn))
        return jsonify(status)

    @app.route("/api/snapshot/exclude", methods=["POST"])
    def exclude_snapshot_playlists():
        body = request.get_json()
        playlist_ids = body.get("playlist_ids") or []
        if not playlist_ids:
            abort(400, description="playlist_ids required")
        conn = db.get_db()
        snapshot.set_excluded(conn, playlist_ids, bool(body.get("excluded")))
        return jsonify({"ok": True})

    # -- Play history -------------------------------------------------

    @app.route("/api/history/import", methods=["POST"])
    def import_history():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            abort(400, description="A .zip export file is required.")
        if not upload.filename.lower().endswith(".zip"):
            abort(400, description="Upload the export .zip itself, not its contents.")
        # Checked before the file is copied anywhere, so a rejected import
        # doesn't leave a ~66 MB orphan folder behind.
        if history_import.busy():
            return jsonify({"error": "already_running"}), 409
        folder = history_import.save_upload(upload)
        if not history_import.start_upload(folder, upload.filename):
            return jsonify({"error": "already_running"}), 409
        return jsonify({"started": True})

    @app.route("/api/history/reimport", methods=["POST"])
    def reimport_history():
        conn = db.get_db()
        latest = history_import.latest_upload(conn)
        if latest is None:
            abort(400, description="Nothing uploaded yet — there's no folder to re-import.")
        if not history_import.start_reimport(latest["folder"], latest["original_name"]):
            return jsonify({"error": "already_running"}), 409
        return jsonify({"started": True})

    @app.route("/api/history/status")
    def history_status():
        conn = db.get_db()
        status = history_import.get_status()
        status.update(history_import.coverage_counts(conn))
        # So the page can grey its buttons out while another job holds the
        # slot, rather than taking the whole upload and only then answering 409.
        status["active_job"] = jobs.active()
        return jsonify(status)

    # -- Foreign-track round-trip -------------------------------------

    @app.route("/api/roundtrip/start", methods=["POST"], defaults={"reconcile_only": False})
    @app.route("/api/roundtrip/reconcile", methods=["POST"], defaults={"reconcile_only": True})
    def start_roundtrip(reconcile_only):
        if get_spotify_client() is None:
            return jsonify({"error": "not_authenticated"}), 401
        if not roundtrip.start(reconcile_only=reconcile_only):
            return jsonify({"error": "already_running", "detail": jobs.active()}), 409
        return jsonify({"started": True})

    @app.route("/api/roundtrip/stop", methods=["POST"])
    def stop_roundtrip():
        # Cooperative: the run finishes its current batch, commits, skips the
        # clear, and ends in the stopped-early state.
        return jsonify({"stopping": jobs.request_stop("roundtrip")})

    @app.route("/api/roundtrip/status")
    def roundtrip_status():
        conn = db.get_db()
        status = roundtrip.get_status()
        status.update(roundtrip.counts(conn))
        status["active_job"] = jobs.active()
        return jsonify(status)

    @app.route("/api/roundtrip/alias", methods=["POST"])
    def alias_roundtrip_uris():
        body = request.get_json()
        pairs = [
            (entry.get("requested_uri"), entry.get("track_id"))
            for entry in (body.get("aliases") or [])
        ]
        if not pairs or not all(uri and track_id for uri, track_id in pairs):
            abort(400, description="aliases must be a non-empty list of {requested_uri, track_id}")
        conn = db.get_db()
        try:
            saved = roundtrip.set_manual_aliases(conn, pairs)
        except ValueError as e:
            abort(400, description=str(e))
        return jsonify({"ok": True, "saved": saved})

    @app.route("/api/roundtrip/clear-failures", methods=["POST"])
    def clear_roundtrip_failures():
        conn = db.get_db()
        roundtrip.clear_failures(conn)
        return jsonify({"ok": True})

    # -- Artists ------------------------------------------------------

    @app.route("/api/artists/alias", methods=["POST"])
    def alias_artists():
        body = request.get_json()
        artist_id_a = body.get("artist_id_a")
        artist_id_b = body.get("artist_id_b")
        if not artist_id_a or not artist_id_b:
            abort(400, description="artist_id_a and artist_id_b required")
        conn = db.get_db()
        if body.get("same"):
            artists.mark_same(conn, artist_id_a, artist_id_b)
        else:
            artists.mark_not_same(conn, artist_id_a, artist_id_b)
        return jsonify({"ok": True})

    @app.route("/api/artists/unmerge", methods=["POST"])
    def unmerge_artist():
        body = request.get_json()
        artist_id = body.get("artist_id")
        if not artist_id:
            abort(400, description="artist_id required")
        conn = db.get_db()
        artists.unmerge(conn, artist_id)
        return jsonify({"ok": True})

    # -- Board state -------------------------------------------------

    @app.route("/api/board")
    def get_board():
        return jsonify(_board_state(db.get_db()))

    @app.route("/api/card/<int:card_id>", methods=["POST"])
    def update_card(card_id):
        body = request.get_json()
        conn = db.get_db()
        conn.execute(
            "UPDATE card SET placement = ?, x = ?, y = ? WHERE id = ? AND board_id = ?",
            (body["placement"], body.get("x"), body.get("y"), card_id, db.DEFAULT_BOARD_ID),
        )
        conn.commit()
        return jsonify({"ok": True})

    @app.route("/api/card/<int:card_id>", methods=["PATCH"])
    def patch_card(card_id):
        body = request.get_json()
        conn = db.get_db()
        fields, params = [], []
        for key in ("note", "x", "y", "placement"):
            if key in body:
                fields.append(f"{key} = ?")
                params.append(body[key])
        if fields:
            params.extend([card_id, db.DEFAULT_BOARD_ID])
            conn.execute(
                f"UPDATE card SET {', '.join(fields)} WHERE id = ? AND board_id = ?", params
            )
            conn.commit()
        return jsonify({"ok": True})

    # -- Labels -------------------------------------------------------

    @app.route("/api/label", methods=["POST"])
    def create_label():
        body = request.get_json()
        conn = db.get_db()
        cur = conn.execute(
            "INSERT INTO label (board_id, text, x, y) VALUES (?, ?, ?, ?)",
            (db.DEFAULT_BOARD_ID, body.get("text", ""), body["x"], body["y"]),
        )
        conn.commit()
        return jsonify({"id": cur.lastrowid})

    @app.route("/api/label/<int:label_id>", methods=["PATCH"])
    def update_label(label_id):
        body = request.get_json()
        conn = db.get_db()
        fields, params = [], []
        for key in ("text", "x", "y"):
            if key in body:
                fields.append(f"{key} = ?")
                params.append(body[key])
        if fields:
            params.extend([label_id, db.DEFAULT_BOARD_ID])
            conn.execute(
                f"UPDATE label SET {', '.join(fields)} WHERE id = ? AND board_id = ?", params
            )
            conn.commit()
        return jsonify({"ok": True})

    @app.route("/api/label/<int:label_id>", methods=["DELETE"])
    def delete_label(label_id):
        conn = db.get_db()
        conn.execute(
            "DELETE FROM label WHERE id = ? AND board_id = ?", (label_id, db.DEFAULT_BOARD_ID)
        )
        conn.commit()
        return jsonify({"ok": True})

    # -- Export ---------------------------------------------------------

    @app.route("/api/export")
    def export():
        cutoff = float(request.args.get("cutoff", 300))
        conn = db.get_db()
        state = _board_state(conn)
        text = render_export_text(state["cards"], state["labels"], cutoff)
        return jsonify({"text": text})

    return app


def _board_state(conn):
    cards = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM card WHERE board_id = ? ORDER BY id", (db.DEFAULT_BOARD_ID,)
        )
    ]
    labels = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM label WHERE board_id = ? ORDER BY id", (db.DEFAULT_BOARD_ID,)
        )
    ]
    return {"cards": cards, "labels": labels}


if __name__ == "__main__":
    app = create_app()
    app.run(port=APP_PORT, debug=APP_DEBUG)
