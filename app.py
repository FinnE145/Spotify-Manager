import secrets

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import db
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

    @app.route("/snapshot")
    def snapshot():
        return render_template("coming_soon.html", active="snapshot", page_name="Snapshot")

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
            return f"Spotify authorization failed: {error}", 400

        expected = session.pop("oauth_state", None)
        if not expected or request.args.get("state") != expected:
            return "Invalid OAuth state.", 400

        code = request.args.get("code")
        if not code:
            return "Missing authorization code.", 400

        auth_manager = get_auth_manager()
        auth_manager.get_access_token(code, as_dict=False)
        return redirect(url_for("index"))

    # -- Snapshot ---------------------------------------------------------

    @app.route("/api/snapshot/pull", methods=["POST"])
    def pull_snapshot():
        sp = get_spotify_client()
        if sp is None:
            return jsonify({"error": "not_authenticated"}), 401

        conn = db.get_db()
        results = sp.current_user_playlists(limit=50)
        playlists = list(results["items"])
        while results["next"]:
            results = sp.next(results)
            playlists.extend(results["items"])

        for playlist in playlists:
            if playlist is None:
                # Spotify returns a null entry for playlists that were deleted
                # but are still in the user's followed list.
                continue

            images = playlist.get("images") or []
            image_url = images[0]["url"] if images else None
            owner = (playlist.get("owner") or {}).get("display_name")
            track_count = (playlist.get("tracks") or {}).get("total")

            conn.execute(
                """
                INSERT INTO snapshot (playlist_id, name, image_url, owner, track_count, pulled_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(playlist_id) DO UPDATE SET
                    name=excluded.name,
                    image_url=excluded.image_url,
                    owner=excluded.owner,
                    track_count=excluded.track_count,
                    pulled_at=excluded.pulled_at
                """,
                (
                    playlist["id"],
                    playlist["name"],
                    image_url,
                    owner,
                    track_count,
                ),
            )

            conn.execute(
                """
                INSERT INTO card (board_id, entity_type, entity_id, display_name, image_url, placement)
                VALUES (?, 'playlist', ?, ?, ?, 'tray')
                ON CONFLICT(board_id, entity_type, entity_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    image_url=excluded.image_url
                """,
                (db.DEFAULT_BOARD_ID, playlist["id"], playlist["name"], image_url),
            )
        conn.commit()

        return jsonify(_board_state(conn))

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
