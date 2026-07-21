import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth

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
    return spotipy.Spotify(auth_manager=auth_manager)
