"""spotify_client.py's retry policy.

The module scored **0%** in the S sweep (`S_sweep.md` §3.2). Most of its
survivors are tuning constants that no behaviour hinges on and that §3.2
records as "not fixed" -- deliberately not asserted here, since pinning them
would only make the next deliberate tweak fail.

`respect_retry_after_header=False` is the exception, and it is why this file
exists.
"""

import spotify_client


class _FakeCache:
    def get_cached_token(self):
        return {"access_token": "t", "expires_at": 4102444800}


class _FakeAuthManager:
    cache_handler = _FakeCache()

    def validate_token(self, token_info):
        return True


def _built_retry(monkeypatch):
    """The Retry the real get_spotify_client() mounts, with auth stubbed out.

    The suite patches get_spotify_client() everywhere (conftest.py), so the
    session this function builds had never been observed by any test -- which
    is exactly why every constant in it survived.
    """
    monkeypatch.setattr(spotify_client, "get_auth_manager", lambda: _FakeAuthManager())
    sp = spotify_client.get_spotify_client()
    assert sp is not None
    adapter = sp._session.get_adapter("https://api.spotify.com/v1/me")
    return adapter.max_retries


def test_a_429_is_not_slept_through(monkeypatch):
    # source: CLAUDE.md's spotify_client.py entry, and the comment on the Retry
    # itself -- respect_retry_after_header=False exists so a 429 raises
    # immediately instead of blocking for an hours-long app-quota Retry-After,
    # leaving snapshot.py's _call() to handle the wait. urllib3 treats 429
    # specially via its hardcoded RETRY_AFTER_STATUS_CODES, independent of
    # status_forcelist, so this flag is the only thing standing between the app
    # and a multi-hour block inside a single request.
    assert _built_retry(monkeypatch).respect_retry_after_header is False


def test_only_GETs_are_ever_retried(monkeypatch):
    # source: the Retry's own comment -- urllib3 cannot know whether a write
    # that 5xx'd was applied before the error, so replaying one can duplicate
    # it, and a duplicated playlist write desyncs the round-trip from what it
    # thinks it wrote. This is the never-corrupt-the-library rule expressed as
    # a retry policy.
    assert set(_built_retry(monkeypatch).allowed_methods) == {"GET"}
