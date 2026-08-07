import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

import artists
import canonical
import canonical_detect
import db
import history_import
import jobs
import roundtrip
import snapshot
from config import APP_DEBUG, APP_PORT, MAX_CONTENT_LENGTH, SECRET_KEY
from grouping import render_export_text
from spotify_client import get_auth_manager, get_spotify_client


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

    @app.route("/dev")
    def dev_index():
        return render_template("dev.html", active="dev")

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
        search_q = request.args.get("search", "").strip()
        expand_song_id = request.args.get("expand", type=int)

        reviewed_row = conn.execute(
            "SELECT COUNT(*) AS c, MAX(decided_at) AS latest FROM reviewed_pair"
        ).fetchone()

        groups = canonical.song_groups(conn, query=q, include_singletons=show_singletons)
        trees = {g["song_id"]: canonical.song_tree(conn, g["song_id"]) for g in groups}

        unreviewed_main_groups, unreviewed_cross_groups, all_groups = (
            canonical_detect.canonical_page_groups(conn)
        )
        cross_artist_groups = [g for g in all_groups if g["cross_artist"]]

        search_results = []
        if search_q:
            like = f"%{search_q}%"
            rows = conn.execute(
                "SELECT t.track_id FROM track t WHERE t.name LIKE ? "
                "   OR EXISTS (SELECT 1 FROM track_artist x JOIN artist ar USING(artist_id) "
                "              WHERE x.track_id = t.track_id AND ar.name LIKE ?) "
                "ORDER BY t.name COLLATE NOCASE LIMIT 100",
                (like, like),
            ).fetchall()
            for row in rows:
                info = canonical.track_display(conn, row["track_id"])
                info["groups"] = canonical.groups_for_track(conn, row["track_id"])
                search_results.append(info)

        return render_template(
            "canonical.html",
            active="dev_canonical",
            total_tracks=conn.execute("SELECT COUNT(*) FROM track").fetchone()[0],
            tier_counts=canonical.tier_counts(conn),
            unreviewed_main=len(unreviewed_main_groups),
            unreviewed_cross=len(unreviewed_cross_groups),
            reviewed_count=reviewed_row["c"],
            reviewed_latest=reviewed_row["latest"],
            groups=groups,
            trees=trees,
            show_singletons=show_singletons,
            q=q,
            cross_artist_groups=cross_artist_groups,
            search_q=search_q,
            search_results=search_results,
            expand_song_id=expand_song_id,
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
        elif request.args.get("queue") == "cross-artist":
            queue_name = "cross-artist"
            items = canonical_detect.cross_artist_groups(conn)
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
        conn.commit()
        return jsonify(result)

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
        return jsonify({"ok": True})

    @app.route("/dev/snapshot", endpoint="dev_snapshot")
    def snapshot_index():
        conn = db.get_db()
        playlists = conn.execute("SELECT * FROM snapshot ORDER BY name COLLATE NOCASE").fetchall()

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
        )

    @app.route("/dev/snapshot/playlist/<playlist_id>", endpoint="dev_snapshot_playlist")
    def snapshot_playlist(playlist_id):
        conn = db.get_db()
        playlist = conn.execute(
            "SELECT * FROM snapshot WHERE playlist_id = ?", (playlist_id,)
        ).fetchone()
        if playlist is None:
            abort(404, description="Playlist not found.")

        rows = conn.execute(
            """
            SELECT m.id, m.track_id, t.name, COALESCE(ta.artists, '') AS artists, a.name AS album_name, m.added_at,
                   m.removed_at, m.position
            FROM membership m
            JOIN track t ON t.track_id = m.track_id
            LEFT JOIN album a ON a.album_id = t.album_id
            LEFT JOIN track_artists ta ON ta.track_id = t.track_id
            WHERE m.playlist_id = ?
            ORDER BY m.position
            """,
            (playlist_id,),
        ).fetchall()

        return render_template(
            "snapshot_playlist.html", active="dev_snapshot", playlist=playlist, rows=rows
        )

    @app.route("/dev/snapshot/track/<track_id>", endpoint="dev_snapshot_track")
    def snapshot_track(track_id):
        conn = db.get_db()
        track = conn.execute(
            """
            SELECT t.track_id, t.name, COALESCE(ta.artists, '') AS artists, t.album_id, t.duration_ms, t.explicit,
                   t.external_url, t.uri, t.isrc, t.track_number, t.disc_number, t.is_playable,
                   t.linked_from, t.linked_from_id,
                   a.name AS album_name, a.image_url AS album_image_url
            FROM track t
            LEFT JOIN album a ON a.album_id = t.album_id
            LEFT JOIN track_artists ta ON ta.track_id = t.track_id
            WHERE t.track_id = ?
            """,
            (track_id,),
        ).fetchone()
        if track is None:
            abort(404, description="Track not found.")

        rows = conn.execute(
            """
            SELECT m.id, m.playlist_id, s.name AS playlist_name, m.added_at, m.removed_at,
                   m.position
            FROM membership m
            JOIN snapshot s ON s.playlist_id = m.playlist_id
            WHERE m.track_id = ?
            ORDER BY s.name COLLATE NOCASE, m.added_at
            """,
            (track_id,),
        ).fetchall()

        canonical.ensure_track_groups(conn)
        conn.commit()
        canonical_groups = canonical.groups_for_track(conn, track_id)
        canonical_siblings = {}
        for tier, group_id in canonical_groups.items():
            sibling_ids = [
                tid for tid in canonical.group_members(conn, group_id) if tid != track_id
            ]
            if not sibling_ids:
                canonical_siblings[tier] = []
                continue
            placeholders = ",".join("?" for _ in sibling_ids)
            canonical_siblings[tier] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT track_id, name, artists FROM track WHERE track_id IN ({placeholders})",
                    sibling_ids,
                )
            ]

        return render_template(
            "snapshot_track.html",
            active="dev_snapshot",
            track=track,
            rows=rows,
            canonical_groups=canonical_groups,
            canonical_siblings=canonical_siblings,
        )

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
