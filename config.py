import os
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.environ["SPOTIFY_REDIRECT_URI"]

# Derived once from SPOTIFY_REDIRECT_URI so /login can redirect onto the
# canonical host before touching the session (T1, docs/specs/small-fixes-T.md
# §1.2) -- localhost and 127.0.0.1 never share cookies, so reaching /login on
# the wrong one guarantees oauth_state is missing at /callback.
_parsed_redirect_uri = urlparse(SPOTIFY_REDIRECT_URI)
SPOTIFY_CANONICAL_HOST = (_parsed_redirect_uri.hostname or "").lower()
SPOTIFY_CANONICAL_ORIGIN = f"{_parsed_redirect_uri.scheme}://{_parsed_redirect_uri.netloc}"

# playlist-modify-private is the round-trip's write scope: it is used only to add
# to and clear the private "<Play History Loader>" scratch playlist, never any
# other playlist. Everything else here is read-only, including
# user-read-recently-played (scrobble.py's poller).
SPOTIFY_SCOPES = (
    "playlist-read-private playlist-read-collaborative user-library-read "
    "playlist-modify-private user-read-recently-played"
)

DB_PATH = os.environ.get("SYMR_DB_PATH", "symr.db")
SPOTIPY_CACHE_PATH = os.environ.get("SYMR_SPOTIPY_CACHE", ".spotipy_cache")
UPLOAD_ROOT = os.environ.get("SYMR_UPLOAD_ROOT", os.path.join("data", "streaming_history"))

APP_PORT = int(os.environ.get("SYMR_PORT", "45660"))
APP_DEBUG = os.environ.get("SYMR_DEBUG", "0") == "1"

# Upload ceiling for the streaming-history export zip (currently ~66 MB).
# Flask raises 413 past it, which app.py's HTTPException handler renders.
MAX_CONTENT_LENGTH = 150 * 1024 * 1024

# Signs the Flask session cookie that carries the OAuth state token. No
# fallback: a random key would silently invalidate every session on each
# restart, which breaks login on a service that restarts on its own.
SECRET_KEY = os.environ["SYMR_SECRET_KEY"]
