import os
import secrets

from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.environ["SPOTIFY_REDIRECT_URI"]
# playlist-modify-private is the round-trip's write scope: it is used only to add
# to and clear the private "<Play History Loader>" scratch playlist, never any
# other playlist. Everything else here is read-only.
SPOTIFY_SCOPES = (
    "playlist-read-private playlist-read-collaborative user-library-read "
    "playlist-modify-private"
)

DB_PATH = os.environ.get("SYMR_DB_PATH", "symr.db")
SPOTIPY_CACHE_PATH = os.environ.get("SYMR_SPOTIPY_CACHE", ".spotipy_cache")

APP_PORT = int(os.environ.get("SYMR_PORT", "45660"))
APP_DEBUG = os.environ.get("SYMR_DEBUG", "0") == "1"

# Upload ceiling for the streaming-history export zip (currently ~66 MB).
# Flask raises 413 past it, which app.py's HTTPException handler renders.
MAX_CONTENT_LENGTH = 150 * 1024 * 1024

# Signs the Flask session cookie that carries the OAuth state token. A random
# fallback works for local single-user use; set SYMR_SECRET_KEY to keep sessions
# valid across restarts (e.g. an auth flow spanning a server restart).
SECRET_KEY = os.environ.get("SYMR_SECRET_KEY", secrets.token_hex(32))
