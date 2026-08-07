import requests
import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth
from urllib3.util import Retry

from config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES,
    SPOTIPY_CACHE_PATH,
)


def get_auth_manager():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPES,
        cache_handler=CacheFileHandler(cache_path=SPOTIPY_CACHE_PATH),
    )


def get_spotify_client():
    """Returns an authenticated Spotipy client, or None if there's no valid cached token."""
    auth_manager = get_auth_manager()
    token_info = auth_manager.cache_handler.get_cached_token()
    if not auth_manager.validate_token(token_info):
        return None

    # Spotipy's default retry setup still auto-retries 429s and blocks for
    # however long Retry-After says (urllib3 treats 429 specially via its
    # hardcoded RETRY_AFTER_STATUS_CODES, independent of status_forcelist).
    # An app-level quota block can carry an hours-long Retry-After, so we
    # build our own session with respect_retry_after_header=False: retry
    # transient 5xx a few times, but let a 429 raise immediately so
    # snapshot.py's own _call() can fail fast on a long wait.
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=None,
        read=False,
        # GET only. urllib3 cannot know whether a write that 5xx'd was applied
        # before the error, so replaying one can duplicate it -- and a silently
        # duplicated playlist write is exactly the kind of thing that desyncs
        # the round-trip from what it thinks it wrote. Nothing outside
        # roundtrip.py issues a non-GET anyway, so this costs the reads
        # nothing. (Spotipy's token refresh POSTs through SpotifyOAuth's own
        # session, not this one, so it is unaffected.)
        allowed_methods=frozenset(["GET"]),
        status=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
        respect_retry_after_header=False,
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return spotipy.Spotify(auth_manager=auth_manager, requests_session=session)
