"""The shared `/api/*` error shape (P1-014, docs/specs/error-pages.md,
Audited 2026-08-17).

`P1_findings.md` P1-014's Test field, verbatim: "every /api/* error response,
from both the generic abort()/exception path and the hand-written
precondition checks, has exactly `error` and `detail` keys. An
unauthenticated /api/* request gets JSON 401, not a redirect. A request with
a query string that errors shows the full query string in the HTML error
page's request line."
"""

import jobs


def test_generic_abort_has_exactly_error_and_detail_keys(client, fake_spotify):
    # source: P1-014's Test field -- "the generic abort()/exception path
    # ... has exactly error and detail keys."
    resp = client.get("/api/canonical/queue?tracks=only-one-id")

    assert resp.status_code == 400
    assert set(resp.get_json().keys()) == {"error", "detail"}


def test_uncaught_exception_has_exactly_error_and_detail_keys(client, fake_spotify, monkeypatch):
    # source: same clause -- the generic Exception handler path (not an
    # abort()), which render_error's HTML-vs-JSON branch also covers.
    import canonical_detect

    def _boom(conn):
        raise RuntimeError("deliberate failure")

    monkeypatch.setattr(canonical_detect, "candidate_groups", _boom)

    resp = client.get("/api/canonical/queue")

    assert resp.status_code == 500
    assert set(resp.get_json().keys()) == {"error", "detail"}


def test_hand_written_already_running_has_exactly_error_and_detail_keys(client, fake_spotify):
    # source: P1-014's Test field -- "the hand-written precondition checks"
    # -- already_running is one of api_error's own hand-picked slugs
    # (app.py's pull_snapshot et al.).
    with jobs._lock:
        jobs._active = "snapshot"
    try:
        resp = client.post("/api/snapshot/pull")
    finally:
        with jobs._lock:
            jobs._active = None

    assert resp.status_code == 409
    body = resp.get_json()
    assert set(body.keys()) == {"error", "detail"}
    assert body["error"] == "already_running"


def test_unauthenticated_api_request_gets_json_401_not_a_redirect(client, monkeypatch):
    # source: P1-014's Test field -- "An unauthenticated /api/* request gets
    # JSON 401, not a redirect." This is the P1-014 fix itself: require_login
    # used to unconditionally redirect regardless of the /api/ prefix.
    import app as app_module

    monkeypatch.setattr(app_module, "get_spotify_client", lambda: None)

    resp = client.get("/api/canonical/queue")

    assert resp.status_code == 401
    assert resp.content_type.startswith("application/json")
    assert resp.get_json() == {"error": "not_authenticated", "detail": None}
    assert "Location" not in resp.headers


def test_unauthenticated_page_request_still_redirects(client, monkeypatch):
    # source: P1-014's Action note -- "unauthenticated page requests still
    # redirect, unchanged." The negative half: the /api/* fix must not have
    # broken ordinary page auth.
    import app as app_module

    monkeypatch.setattr(app_module, "get_spotify_client", lambda: None)

    resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_html_error_page_includes_the_query_string_in_the_request_line(client, fake_spotify):
    # source: P1-014's Test field -- "a request with a query string that
    # errors shows the full query string in the HTML error page's request
    # line." A bare request.path implementation would drop it.
    resp = client.get("/album/nope?foo=bar")

    assert resp.status_code == 404
    assert b"?foo=bar" in resp.data


def test_generic_slug_derives_from_the_http_status_name(client, fake_spotify):
    # source: error-pages.md's slug-derivation rule -- lowercased, spaces to
    # underscores -- distinct from the hand-written domain-specific slugs
    # like not_authenticated/already_running. Every abort() reachable under
    # /api/* today is a 400, so "bad_request" is the only generic slug this
    # can exercise there; not_found is covered on the HTML side by
    # test_html_error_page_includes_the_query_string_in_the_request_line.
    resp = client.get("/api/canonical/queue?tracks=only-one")

    assert resp.get_json()["error"] == "bad_request"


def test_public_endpoints_skip_the_login_guard(client, monkeypatch):
    # characterization -- _PUBLIC_ENDPOINTS = {"login", "callback", "static"}.
    # /login itself must render (or redirect into Spotify) without a client,
    # never bounce through the guard it is itself exempt from.
    import app as app_module

    monkeypatch.setattr(app_module, "get_spotify_client", lambda: None)

    resp = client.get("/login", follow_redirects=False)

    assert resp.status_code != 302 or "/login" not in resp.headers.get("Location", "")
