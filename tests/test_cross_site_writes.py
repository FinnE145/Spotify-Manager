"""The cross-origin write check (docs/specs/cross-site-writes.md).

Every rejection asserts the side effect did not happen, and every pass asserts
the handler actually ran -- a status code alone cannot tell a working check
from a missing one (spec, ## Tests). The spy on `roundtrip.start` is the one
that matters most: that endpoint starts the only job that writes to the real
Spotify library.
"""

import pytest

import app as app_module
import generations
import roundtrip
import scoring

ROUNDTRIP_START = "/api/roundtrip/start"
REFUSED_POST = {
    "error": "cross_origin_request_rejected",
    "detail": "Cross-origin POST refused: this request did not come from a Symr page.",
}


@pytest.fixture
def starts(monkeypatch):
    """Records every roundtrip.start() call instead of starting the job."""
    calls = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(roundtrip, "start", _spy)
    return calls


@pytest.fixture
def confirms(monkeypatch):
    """Records every generations.confirm_generation() call, and keeps the
    route's inline recompute out of it."""
    calls = []
    monkeypatch.setattr(
        generations, "confirm_generation", lambda conn, playlist_id: calls.append(playlist_id)
    )
    monkeypatch.setattr(scoring, "recompute", lambda conn: None)
    return calls


def _refused(resp, body=REFUSED_POST):
    return resp.status_code == 403 and resp.get_json() == body


# -- §2.2: Sec-Fetch-Site ---------------------------------------------------


def test_cross_site_post_is_refused_and_starts_nothing(client, starts):
    # source: docs/specs/cross-site-writes.md §2.2 + §2.5 -- cross-site is
    # refused with the one JSON error shape, and the job is never started.
    resp = client.post(ROUNDTRIP_START, headers={"Sec-Fetch-Site": "cross-site"})

    assert _refused(resp)
    assert starts == []


def test_same_site_post_is_refused_too(client, starts):
    # source: docs/specs/cross-site-writes.md §2.2 -- "and same-site": another
    # *.fmje.dev host is a different origin. A "reject only cross-site"
    # implementation passes this one.
    resp = client.post(ROUNDTRIP_START, headers={"Sec-Fetch-Site": "same-site"})

    assert _refused(resp)
    assert starts == []


@pytest.mark.parametrize("value", ["same-origin", "none"])
def test_same_origin_and_user_initiated_posts_pass(client, starts, value):
    # source: docs/specs/cross-site-writes.md §2.2 -- only same-origin and none
    # pass; the handler must actually run.
    resp = client.post(ROUNDTRIP_START, headers={"Sec-Fetch-Site": value})

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


def test_sec_fetch_site_decides_alone_even_beside_a_foreign_origin(client, starts):
    # source: docs/specs/cross-site-writes.md §2.2 -- "when present, decides
    # alone". An implementation that consults Origin whenever it is present
    # refuses this.
    resp = client.post(
        ROUNDTRIP_START,
        base_url="http://symr.fmje.dev",
        headers={"Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example"},
    )

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


def test_a_cross_site_delete_leaves_the_row_in_place(client, conn):
    # source: docs/specs/cross-site-writes.md §2.1 -- everything but GET/HEAD/
    # OPTIONS is checked, not just POST. An implementation guarding only POST
    # lets this DELETE through.
    label_id = client.post("/api/label", json={"text": "keep me", "x": 1, "y": 2}).get_json()["id"]

    resp = client.delete(f"/api/label/{label_id}", headers={"Sec-Fetch-Site": "cross-site"})

    assert _refused(resp, {
        "error": "cross_origin_request_rejected",
        "detail": "Cross-origin DELETE refused: this request did not come from a Symr page.",
    })
    assert conn.execute("SELECT text FROM label WHERE id = ?", (label_id,)).fetchone()[0] == "keep me"


# -- §2.3: the Origin fallback ------------------------------------------------


@pytest.mark.parametrize(
    "base_url, origin",
    [
        # Caddy terminates TLS: the browser says https, this process sees http.
        ("http://symr.fmje.dev", "https://symr.fmje.dev"),
        # The laptop dev loop.
        ("http://127.0.0.1:45660", "http://127.0.0.1:45660"),
    ],
)
def test_an_origin_matching_host_and_port_passes_whatever_its_scheme(
    client, starts, base_url, origin
):
    # source: docs/specs/cross-site-writes.md §2.3 -- host-and-port against
    # Host, "the scheme is never compared". A scheme-sensitive comparison
    # refuses the first case, i.e. every write in production.
    resp = client.post(ROUNDTRIP_START, base_url=base_url, headers={"Origin": origin})

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


@pytest.mark.parametrize(
    "base_url, origin",
    [
        ("http://symr.fmje.dev", "https://evil.example"),
        # A sibling under the same registrable domain is still another origin.
        ("http://symr.fmje.dev", "https://other.fmje.dev"),
    ],
)
def test_an_origin_on_another_host_is_refused(client, starts, base_url, origin):
    # source: docs/specs/cross-site-writes.md §2.3 -- "a different host" is
    # refused.
    resp = client.post(ROUNDTRIP_START, base_url=base_url, headers={"Origin": origin})

    assert _refused(resp)
    assert starts == []


def test_an_origin_on_the_same_host_but_another_port_is_refused(client, starts):
    # source: docs/specs/cross-site-writes.md §2.3 -- "the same host on a
    # different port". A hostname-only comparison passes this one.
    resp = client.post(
        ROUNDTRIP_START,
        base_url="http://127.0.0.1:45660",
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert _refused(resp)
    assert starts == []


def test_a_null_origin_is_refused(client, starts):
    # source: docs/specs/cross-site-writes.md §2.3 -- `Origin: null` (a
    # sandboxed iframe, a data: URL) is refused.
    resp = client.post(ROUNDTRIP_START, headers={"Origin": "null"})

    assert _refused(resp)
    assert starts == []


def test_a_null_origin_is_refused_even_when_werkzeug_reports_no_host(client, starts):
    # source: docs/specs/cross-site-writes.md §2.3 -- `Origin: null` is
    # refused. Werkzeug reports a Host with invalid characters (or none at
    # all) as "", and null's netloc is "" too, so a bare equality test passes
    # this one.
    resp = client.post(ROUNDTRIP_START, base_url="http://bad host", headers={"Origin": "null"})

    assert _refused(resp)
    assert starts == []


def test_the_origin_host_comparison_ignores_case(client, starts):
    # source: docs/specs/cross-site-writes.md §2.3 -- netloc against Host,
    # "compared case-insensitively". A case-sensitive comparison refuses this.
    resp = client.post(
        ROUNDTRIP_START,
        base_url="http://symr.fmje.dev",
        headers={"Origin": "https://SYMR.fmje.dev"},
    )

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


def test_an_empty_sec_fetch_site_falls_through_to_origin(client, starts):
    # source: docs/specs/cross-site-writes.md §2.2 -- "a present-but-empty
    # header counts as absent and falls through to §2.3". Testing presence
    # rather than emptiness refuses this matching-Origin request.
    resp = client.post(
        ROUNDTRIP_START,
        base_url="http://symr.fmje.dev",
        headers={"Sec-Fetch-Site": "", "Origin": "https://symr.fmje.dev"},
    )

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


def test_an_unparseable_origin_is_refused_not_a_500(client, starts):
    # source: docs/specs/cross-site-writes.md §2.3 -- "an Origin that does not
    # parse at all (a 403, not a 500)". urlsplit raises on an unclosed IPv6
    # bracket, which unguarded would reach the generic 500 handler.
    resp = client.post(ROUNDTRIP_START, headers={"Origin": "http://[::1"})

    assert _refused(resp)
    assert starts == []


# -- §2.4 / §2.1: what passes unchecked --------------------------------------


def test_a_post_with_neither_header_passes(client, starts):
    # source: docs/specs/cross-site-writes.md §2.4 -- no Sec-Fetch-Site and no
    # Origin is not a browser acting for another page. Also what keeps
    # test_routes.py's sweep green, since the test client sends neither.
    resp = client.post(ROUNDTRIP_START)

    assert resp.get_json() == {"started": True}
    assert starts == [{"reconcile_only": False}]


def test_a_cross_site_get_passes(client):
    # source: docs/specs/cross-site-writes.md §2.1 -- safe methods always pass.
    # An implementation checking every method refuses this with the error body,
    # which has no status fields in it.
    resp = client.get("/api/roundtrip/status", headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 200
    assert resp.get_json()["active_job"] is None


def test_cross_site_head_and_options_pass(client, starts):
    # source: docs/specs/cross-site-writes.md §2.1 -- HEAD and OPTIONS are safe
    # methods too. OPTIONS is Flask's automatic answer, whose Allow header an
    # error response would not carry; neither may reach the handler.
    head = client.head("/api/roundtrip/status", headers={"Sec-Fetch-Site": "cross-site"})
    options = client.options(ROUNDTRIP_START, headers={"Sec-Fetch-Site": "cross-site"})

    assert head.status_code == 200
    assert options.status_code == 200
    assert "POST" in options.headers["Allow"]
    assert starts == []


# -- §2.5: the page shape -----------------------------------------------------


def test_a_cross_site_form_post_gets_the_html_error_page(client, confirms):
    # source: docs/specs/cross-site-writes.md §2.5 -- a non-/api/ path gets
    # error.html, not JSON, and the generation is never confirmed.
    resp = client.post(
        "/dev/generations/confirm",
        data={"playlist_id": "p1", "decision": "yes", "return_to": "dev_generations"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    html = resp.get_data(as_text=True)
    assert resp.status_code == 403
    assert resp.mimetype == "text/html"
    assert "403 Forbidden" in html
    assert "Cross-origin POST refused: this request did not come from a Symr page." in html
    assert confirms == []


def test_a_same_origin_form_post_still_confirms(client, confirms):
    # source: docs/specs/cross-site-writes.md §2.2 -- the plain HTML form POST
    # from Symr's own page must keep working.
    resp = client.post(
        "/dev/generations/confirm",
        data={"playlist_id": "p1", "decision": "yes", "return_to": "dev_generations"},
        headers={"Sec-Fetch-Site": "same-origin"},
    )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dev/generations")
    assert confirms == ["p1"]


# -- §4: where the hook sits --------------------------------------------------


def test_a_refused_request_never_reaches_the_login_guard_or_the_score_backstop(
    client, starts, monkeypatch
):
    # source: docs/specs/cross-site-writes.md §4 -- the check runs before
    # require_login (whose get_spotify_client() can fire a token-refresh POST)
    # and before refresh_scores (ensure_fresh can enqueue a recompute). With
    # the hook placed after either, the matching spy records a call -- and
    # with no token, the login guard would answer 401 instead of 403.
    client_calls, fresh_calls = [], []

    def _no_token():
        client_calls.append(True)
        return None

    monkeypatch.setattr(app_module, "get_spotify_client", _no_token)
    monkeypatch.setattr(scoring, "ensure_fresh", lambda: fresh_calls.append(True))

    resp = client.post(ROUNDTRIP_START, headers={"Sec-Fetch-Site": "cross-site"})

    assert _refused(resp)
    assert client_calls == []
    assert fresh_calls == []
    assert starts == []


@pytest.mark.parametrize("path", ["/api/no-such-route", "/api/roundtrip/status"])
def test_a_cross_site_write_to_no_route_or_a_get_only_route_is_403(client, path):
    # source: docs/specs/cross-site-writes.md §4 -- the hook runs before URL
    # dispatch raises its 404/405, so a cross-site write is a 403 whatever it
    # targets. An implementation skipping requests with no matched endpoint
    # answers 404 / 405 here.
    resp = client.post(path, headers={"Sec-Fetch-Site": "cross-site"})

    assert _refused(resp)
