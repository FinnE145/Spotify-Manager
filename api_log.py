"""The API request log (docs/specs/partial-pulls-J.md §4): every outbound
Spotify request gets one row -- what it was, when, and how it came back.
Headless for now; the only surface J ships is the /dev requests-per-day line.

Hooked at the requests.Session level via LoggingSession, not by wrapping
jobs.call -- that catches everything, including the entity pages' one-off
detail fetches, which never go through a job. spotify_client.py builds two
separate LoggingSession instances (the API client and SpotifyOAuth each keep
their own retry/adapter setup)."""

import contextvars
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

import db

# Set in two places: jobs.try_start's run() wrapper (the job name) and
# app.py's first before_request hook (request.endpoint). A contextvar, not a
# module global -- a pull's background thread and a page-load thread run
# concurrently, and a global would let one overwrite the other's label.
api_context = contextvars.ContextVar("api_context", default=None)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retry_after(headers):
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class LoggingSession(requests.Session):
    """Overrides request() -- the one method every requests.Session verb
    (get/post/...) routes through -- so every outbound call is timed and
    logged regardless of which spotipy code path issued it."""

    def request(self, method, url, *args, **kwargs):
        start = time.monotonic()
        try:
            response = super().request(method, url, *args, **kwargs)
        except Exception as e:
            parts = urlsplit(url)
            record(
                host=parts.hostname or "",
                method=method,
                path=parts.path,
                query=parts.query or None,
                status=None,
                duration_ms=int((time.monotonic() - start) * 1000),
                response_bytes=None,
                retry_after=None,
                error=str(e),
            )
            raise

        # response.request.url, not the incoming `url` -- query params passed
        # via the params= kwarg aren't in `url` itself, and this is the fully
        # resolved request requests.Session actually sent.
        parts = urlsplit(response.request.url)
        record(
            host=parts.hostname or "",
            method=method,
            path=parts.path,
            query=parts.query or None,
            status=response.status_code,
            duration_ms=int((time.monotonic() - start) * 1000),
            response_bytes=len(response.content),
            retry_after=_retry_after(response.headers),
            error=None,
        )
        return response


def record(*, host, method, path, query, status, duration_ms, response_bytes, retry_after, error):
    """A logging failure must never break the request it is logging (§4.4) --
    the whole call is wrapped and swallows every exception. A connection per
    write, not a shared one: no check_same_thread, no lock, no lifetime to
    manage, against an HTTP request that already costs 100ms+."""
    try:
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO api_request (ts, host, method, path, query, status, duration_ms, "
                "response_bytes, retry_after, context, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now_iso(), host, method, path, query, status, duration_ms,
                    response_bytes, retry_after, api_context.get(), error,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def request_counts(conn):
    """Quota-counting requests only (host = api.spotify.com), for the /dev
    row. No "remaining today" figure -- there's no known budget yet; that's
    roadmap step O, gated on this log actually catching a lockout first."""
    now = datetime.now(timezone.utc)
    return {
        "last_24h": _count_since(conn, now - timedelta(hours=24)),
        "last_7d": _count_since(conn, now - timedelta(days=7)),
    }


def _count_since(conn, cutoff):
    return conn.execute(
        "SELECT COUNT(*) FROM api_request WHERE host = 'api.spotify.com' AND ts >= ?",
        (cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchone()[0]
