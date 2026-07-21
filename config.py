import os

from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.environ["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"

DB_PATH = os.environ.get("SYMR_DB_PATH", "symr.db")
SPOTIPY_CACHE_PATH = os.environ.get("SYMR_SPOTIPY_CACHE", ".spotipy_cache")

APP_PORT = int(os.environ.get("SYMR_PORT", "45660"))
