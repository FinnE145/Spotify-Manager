"""`api_log.py` -- one row per outbound Spotify request.

Hooked at the `requests.Session` level rather than around `jobs.call`, which is
what makes it catch *everything*: the entity pages' one-off detail fetches
never go through a job, and neither do SpotifyOAuth's token refreshes.

Two of its rules are security-shaped and are asserted here rather than trusted:
it stores **no headers, no bodies and no response content** -- only
`Retry-After` by name into its own column and `len(response.content)` as an
integer -- and a logging failure **must never break the request it is
logging**, which means `record()` swallows every exception and a lost row is
silent. That silence is why no job may hold an open write transaction across a
Spotify request (`snapshot._pull_liked_songs`).
"""

import pytest
import requests

import api_log
import builders
import db


class FakeResponse:
    """What `requests.Session.request` hands back.

    Carries the four things `LoggingSession` reads and nothing else, so a test
    cannot accidentally assert on a field the logger has no business seeing.
    """

    def __init__(self, url, status_code=200, content=b"{}", headers=None):
        self.request = type("PreparedRequest", (), {"url": url})()
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


@pytest.fixture
def transport(monkeypatch):
    """Answers at `requests.Session.request` -- the method `LoggingSession`
    delegates to with `super()`.

    conftest blocks the layer below this (`HTTPAdapter.send`), so a test that
    forgot this fixture would fail loudly rather than reach the network.
    """
    served = []

    def fake_request(self, method, url, *args, **kwargs):
        served.append((method, url))
        response = fake_request.response
        if isinstance(response, Exception):
            raise response
        return response

    fake_request.response = FakeResponse("https://api.spotify.com/v1/me")
    monkeypatch.setattr(requests.Session, "request", fake_request)
    return fake_request


def rows(conn):
    return conn.execute(
        "SELECT ts, host, method, path, query, status, duration_ms, response_bytes, "
        "retry_after, context, error FROM api_request ORDER BY id"
    ).fetchall()


# -- `record()` -------------------------------------------------------------


def test_a_request_is_recorded_with_its_context_label(conn):
    """The label is a contextvar, not a module global: a job's background
    thread and a page-load thread run concurrently, and a global would let one
    overwrite the other's."""
    # source: partial-pulls-J.md §4.3 -- the context label is set in
    # jobs.try_start's run() wrapper and in app.py's first before_request hook.
    api_log.api_context.set("snapshot")

    api_log.record(
        host="api.spotify.com", method="GET", path="/v1/me", query=None, status=200,
        duration_ms=120, response_bytes=512, retry_after=None, error=None,
    )

    row = rows(conn)[0]
    assert (row["host"], row["method"], row["path"], row["status"]) == (
        "api.spotify.com", "GET", "/v1/me", 200
    )
    assert row["context"] == "snapshot"
    assert row["ts"].endswith("Z")


def test_the_context_label_defaults_to_none(conn):
    # source: api_log.api_context -- declared with default=None, so an
    # unlabelled request still records rather than failing.
    api_log.record(
        host="api.spotify.com", method="GET", path="/v1/me", query=None, status=200,
        duration_ms=1, response_bytes=0, retry_after=None, error=None,
    )

    assert rows(conn)[0]["context"] is None


def test_a_logging_failure_never_breaks_the_request_it_is_logging(conn, monkeypatch):
    """The whole call is wrapped and swallows every exception. The cost is
    that a lost row is silent -- which is why no job may hold an open write
    transaction across a Spotify request."""
    # source: partial-pulls-J.md §4.4 -- "a logging failure must never break
    # the request it is logging".
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "connect", broken)

    # No raise, no return value, nothing for a caller to handle.
    assert api_log.record(
        host="api.spotify.com", method="GET", path="/v1/me", query=None, status=200,
        duration_ms=1, response_bytes=0, retry_after=None, error=None,
    ) is None


# -- `LoggingSession` -------------------------------------------------------


def test_a_successful_request_is_logged_and_returned(conn, transport):
    # source: partial-pulls-J.md §4.1 -- LoggingSession overrides request(),
    # the one method every requests verb routes through.
    transport.response = FakeResponse(
        "https://api.spotify.com/v1/tracks/abc", status_code=200, content=b"x" * 42
    )

    response = api_log.LoggingSession().request("GET", "https://api.spotify.com/v1/tracks/abc")

    assert response is transport.response
    row = rows(conn)[0]
    assert (row["host"], row["path"], row["status"]) == (
        "api.spotify.com", "/v1/tracks/abc", 200
    )
    assert row["response_bytes"] == 42
    assert row["error"] is None


def test_the_logged_url_is_the_one_actually_sent_not_the_one_passed_in(conn, transport):
    """Query params handed in via `params=` are not in the incoming `url`, so
    reading `response.request.url` is what makes the stored query right."""
    # source: api_log.LoggingSession -- "response.request.url, not the incoming
    # url ... this is the fully resolved request requests.Session actually
    # sent."
    transport.response = FakeResponse(
        "https://api.spotify.com/v1/playlists/p1/tracks?offset=0&limit=100"
    )

    api_log.LoggingSession().request(
        "GET", "https://api.spotify.com/v1/playlists/p1/tracks", params={"offset": 0}
    )

    row = rows(conn)[0]
    assert row["path"] == "/v1/playlists/p1/tracks"
    assert row["query"] == "offset=0&limit=100"


def test_a_failed_request_is_logged_and_re_raised(conn, transport):
    """A request that never got a response still gets a row -- with the error
    and a NULL status, which is how a network failure is distinguishable from
    a 500."""
    # source: partial-pulls-J.md §4.2 -- status is nullable and `error` holds
    # the exception text.
    transport.response = requests.ConnectionError("name resolution failed")

    with pytest.raises(requests.ConnectionError):
        api_log.LoggingSession().request("GET", "https://api.spotify.com/v1/me")

    row = rows(conn)[0]
    assert row["status"] is None
    assert row["response_bytes"] is None
    assert "name resolution failed" in row["error"]


def test_a_retry_after_header_is_stored_by_name(conn, transport):
    """The one header that is stored, and it goes into its own integer column
    -- not a headers blob."""
    # source: partial-pulls-J.md §4.6 -- no headers, no bodies, no response
    # content; only Retry-After by name.
    transport.response = FakeResponse(
        "https://api.spotify.com/v1/me", status_code=429, headers={"Retry-After": "37"}
    )

    api_log.LoggingSession().request("GET", "https://api.spotify.com/v1/me")

    row = rows(conn)[0]
    assert (row["status"], row["retry_after"]) == (429, 37)


def test_only_the_length_of_the_body_is_stored_never_the_body(conn, transport):
    """A response body can hold anything Spotify returns about the account, so
    the log records its size and nothing else."""
    # source: partial-pulls-J.md §4.6 -- "len(response.content) as an integer".
    secret = b'{"email": "someone@example.com", "country": "GB"}'
    transport.response = FakeResponse("https://api.spotify.com/v1/me", content=secret)

    api_log.LoggingSession().request("GET", "https://api.spotify.com/v1/me")

    row = rows(conn)[0]
    assert row["response_bytes"] == len(secret)
    assert all(
        secret.decode() not in str(value) for value in tuple(row) if value is not None
    )


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({}, None),
        ({"Retry-After": "5"}, 5),
        # Spotify may send an HTTP-date instead of seconds; an unparseable
        # value is dropped rather than stored wrong or raised.
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
    ],
)
def test_retry_after_parsing(headers, expected):
    # source: api_log._retry_after -- characterization of all three arms.
    assert api_log._retry_after(headers) == expected


# -- `request_counts` -------------------------------------------------------


def log_row(conn, host, days):
    conn.execute(
        "INSERT INTO api_request (ts, host, method, path, status) VALUES (?, ?, 'GET', '/v1/me', 200)",
        (builders.days_ago(days), host),
    )
    conn.commit()


def test_the_dev_counts_cover_the_two_rolling_windows(conn):
    # source: partial-pulls-J.md §5.2 -- the /dev row is "Requests: x in 24h ·
    # y in 7d".
    log_row(conn, "api.spotify.com", days=0)
    log_row(conn, "api.spotify.com", days=3)
    log_row(conn, "api.spotify.com", days=30)

    counts = api_log.request_counts(conn)

    assert counts == {"last_24h": 1, "last_7d": 2}


def test_token_refreshes_do_not_inflate_the_quota_picture(conn):
    """`accounts.spotify.com` is where SpotifyOAuth refreshes a token. Those
    requests are logged -- the session hook catches everything -- but they do
    not spend the API quota this row is about."""
    # source: partial-pulls-J.md §5.2 -- request_counts is "filtered to host =
    # 'api.spotify.com' so token refreshes don't inflate the quota picture".
    log_row(conn, "api.spotify.com", days=0)
    log_row(conn, "accounts.spotify.com", days=0)

    assert api_log.request_counts(conn) == {"last_24h": 1, "last_7d": 1}
