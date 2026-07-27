import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

import canonical
import canonical_detect
import db
import snapshot
from config import APP_DEBUG, APP_PORT, SECRET_KEY
from grouping import render_export_text
from spotify_client import get_auth_manager, get_spotify_client


def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
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

    @app.route("/dev/canonical", endpoint="dev_canonical")
    def canonical_index():
        conn = db.get_db()
        canonical.ensure_track_groups(conn)
        conn.commit()

        return render_template(
            "canonical.html",
            active="dev_canonical",
            main_groups=canonical_detect.candidate_groups(conn),
            cross_groups=canonical_detect.cross_artist_groups(conn),
            tier_counts=canonical.tier_counts(conn),
        )

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
                SELECT t.track_id, t.name, t.artists, COUNT(m.id) AS appearances
                FROM track t
                JOIN membership m ON m.track_id = t.track_id AND m.removed_at IS NULL
                WHERE t.name LIKE ? OR t.artists LIKE ?
                GROUP BY t.track_id
                ORDER BY t.name COLLATE NOCASE
                LIMIT 50
                """,
                (like, like),
            ).fetchall()

        changes = conn.execute(
            """
            SELECT m.playlist_id, s.name AS playlist_name, m.track_id, t.name AS track_name,
                   t.artists, m.added_at, m.removed_at,
                   COALESCE(m.removed_at, m.added_at) AS event_at,
                   CASE WHEN m.removed_at IS NOT NULL THEN 'removed' ELSE 'added' END AS kind
            FROM membership m
            JOIN snapshot s ON s.playlist_id = m.playlist_id
            JOIN track t ON t.track_id = m.track_id
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
            SELECT m.id, m.track_id, t.name, t.artists, t.album_name, m.added_at, m.removed_at,
                   m.position
            FROM membership m
            JOIN track t ON t.track_id = m.track_id
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
        track = conn.execute("SELECT * FROM track WHERE track_id = ?", (track_id,)).fetchone()
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

        return render_template("snapshot_track.html", active="dev_snapshot", track=track, rows=rows)

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

    @app.route("/api/snapshot/status")
    def snapshot_status():
        conn = db.get_db()
        status = snapshot.get_status()
        status.update(snapshot.summary_counts(conn))
        return jsonify(status)

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
