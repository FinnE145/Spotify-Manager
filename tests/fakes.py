"""The fake Spotify client (P2_tests.md §4.4).

Covers **only the endpoints the job loops actually call** -- ten of them -- not
spotipy in general. It earns its keep on roundtrip.py, whose replace-never-append
and read-as-a-bag-never-a-sequence invariants were both learned the hard way and
are the highest-corruption-risk logic in the tree.

Two things about it are structural rather than incidental:

**There is no `playlist_add_items`, and there never will be.** roundtrip.py must
*replace* the loader playlist's contents, never append, so that the read-back is
always `offset=0` and no running offset can drift. Code that tried to append
would fail here with AttributeError naming the method -- the invariant enforced
by absence rather than by an assertion someone has to remember to write.

**The loader playlist is not registered by default.** roundtrip's guard verifies
the playlist's name and owner live before it writes anything, and a fake that
satisfied that guard for free would let a test pass while the guard was broken.
A round-trip test registers it on purpose:

    sp.add_playlist(roundtrip.LOADER_ID, roundtrip.LOADER_NAME)

It can express every failure mode those loops exist to handle: a 429 carrying a
Retry-After, a 400 on a batch, a read-back of substituted tracks that carry
`linked_from`, and one of substitutes that carry nothing at all.
"""

from collections import defaultdict

from spotipy.exceptions import SpotifyException

# -- API-shaped objects ------------------------------------------------------
#
# These produce what Spotify actually returns, not what is convenient: every one
# of them is fed straight to snapshot._parse_track_item / _parse_album, so a
# field missing here is a field the parser will read as None.


def spotify_artist(artist_id, name=None, images=None):
    return {
        "id": artist_id,
        "name": name if name is not None else f"Artist {artist_id}",
        "type": "artist",
        "uri": f"spotify:artist:{artist_id}",
        "external_urls": {"spotify": f"https://open.spotify.com/artist/{artist_id}"},
        "images": images if images is not None else [],
    }


def spotify_album(album_id, name=None, artists=None, total_tracks=1, tracks=None, **overrides):
    """A simplified album object, as embedded in every track.

    `images` follows Spotify's real 640/300/64 ordering, because
    snapshot._album_image_url looks for the 300px entry specifically and falls
    back to the *middle* one -- a fake with a single image would exercise only
    the fallback.
    """
    album = {
        "id": album_id,
        "name": name if name is not None else f"Album {album_id}",
        "album_type": "album",
        "type": "album",
        "uri": f"spotify:album:{album_id}",
        "release_date": "2024-01-15",
        "release_date_precision": "day",
        "total_tracks": total_tracks,
        "external_urls": {"spotify": f"https://open.spotify.com/album/{album_id}"},
        "images": [
            {"url": f"https://i.scdn.co/image/{album_id}-640", "width": 640, "height": 640},
            {"url": f"https://i.scdn.co/image/{album_id}-300", "width": 300, "height": 300},
            {"url": f"https://i.scdn.co/image/{album_id}-64", "width": 64, "height": 64},
        ],
        "artists": artists if artists is not None else [spotify_artist(f"{album_id}-artist")],
    }
    if tracks is not None:
        album["tracks"] = tracks
    album.update(overrides)
    return album


def spotify_track(track_id, name=None, album=None, artists=None, linked_from=None, **overrides):
    track = {
        "id": track_id,
        "name": name if name is not None else f"Track {track_id}",
        "type": "track",
        "uri": f"spotify:track:{track_id}",
        "duration_ms": 210_000,
        "explicit": False,
        "track_number": 1,
        "disc_number": 1,
        "is_playable": True,
        "is_local": False,
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
        "external_ids": {"isrc": f"ISRC{track_id}"},
        "album": album if album is not None else spotify_album(f"{track_id}-album"),
        "artists": artists if artists is not None else [spotify_artist(f"{track_id}-artist")],
    }
    if linked_from is not None:
        track["linked_from"] = linked_from
    track.update(overrides)
    return track


def spotify_playlist(
    playlist_id, name=None, owner_id="finn", owner_name=None, total=0, snapshot_id=None
):
    """One playlist object.

    `display_name` follows `owner_id` rather than being a fixed string:
    snapshot._fetch_all_playlists stores the *display name* as `snapshot.owner`
    while roundtrip's guard compares the *id*, so a fake that pinned the name
    would quietly file a foreign-owned playlist under Finn's name.
    """
    return {
        "id": playlist_id,
        "name": name if name is not None else f"Playlist {playlist_id}",
        "type": "playlist",
        "uri": f"spotify:playlist:{playlist_id}",
        "description": "",
        "snapshot_id": snapshot_id or f"snap-{playlist_id}",
        "owner": {"id": owner_id, "display_name": owner_name or owner_id},
        "images": [{"url": f"https://i.scdn.co/image/{playlist_id}", "width": 640, "height": 640}],
        "tracks": {"total": total},
    }


def playlist_item(track, added_at=None):
    """One entry from the playlist-items endpoint, which keys the track as
    **"item"** -- the endpoint can hold episodes too (snapshot.py's comment)."""
    return {"item": track, "added_at": added_at, "is_local": False}


def saved_item(track, added_at=None):
    """One entry from the saved-tracks endpoint, which is track-only and so
    still keys it as "track"."""
    return {"track": track, "added_at": added_at}


# -- Failures ----------------------------------------------------------------


def rate_limited(retry_after=1):
    """A 429. jobs.call sleeps through a wait of <= 30s and retries once;
    anything longer raises jobs.RateLimited instead of blocking the thread."""
    return SpotifyException(429, -1, "rate limit exceeded", headers={"Retry-After": str(retry_after)})


def bad_request(message="invalid request"):
    """A 400 -- what Spotify returns for a batch containing a uri it will not
    accept, which is what roundtrip narrows down with its off-quota probe."""
    return SpotifyException(400, -1, message, headers={})


def not_found(message="not found"):
    return SpotifyException(404, -1, message, headers={})


# -- The client --------------------------------------------------------------


class FakeSpotify:
    def __init__(self, user_id="finn", user_name=None):
        # display_name follows the id for the same reason spotify_playlist's
        # does: _pull_liked_songs stores `display_name or id` as the owner.
        self.user = {"id": user_id, "display_name": user_name or user_id}
        self.playlists = []
        self.items = {}
        self.saved_tracks = []
        self.albums = {}
        self.artists = {}
        self.tracks = {}

        # Every call in order, as (method, args, kwargs). What "exactly one
        # request on first view" is asserted against.
        self.calls = []
        # Every playlist_replace_items call, as (playlist_id, uris). The write
        # log: nothing else in this fake can modify a playlist.
        self.replacements = []

        self._failures = defaultdict(list)
        self._pages = {}
        self._page_counter = 0

        # uri -> (track_id served instead, whether it carries linked_from)
        self.substitutions = {}
        # uris that simply do not come back in the read -- found by set
        # difference, which is the only way roundtrip can detect them
        self.dropped = set()

    # -- Setup -------------------------------------------------------

    def add_playlist(
        self, playlist_id, name=None, owner_id=None, owner_name=None, tracks=None, **overrides
    ):
        """Registers a playlist and, optionally, its contents.

        Owned by the current user unless `owner_id` says otherwise -- which is
        the state roundtrip's guard exists to refuse.
        """
        if owner_id is None:
            owner_id = self.user["id"]
            owner_name = owner_name or self.user["display_name"]
        playlist = spotify_playlist(
            playlist_id,
            name=name,
            owner_id=owner_id,
            owner_name=owner_name,
            total=len(tracks or []),
            **overrides,
        )
        self.playlists.append(playlist)
        self.items[playlist_id] = [playlist_item(t) for t in (tracks or [])]
        # Registered here too, so sp.track() and a loader read-back see the
        # same object a test handed in rather than a freshly built stand-in.
        for track in tracks or []:
            self.tracks[track["id"]] = track
        return playlist

    def add_saved_tracks(self, tracks):
        """Seeds Liked Songs, which snapshot pulls through its own endpoint
        (it is half an exception to every playlist rule -- see
        snapshot.LIKED_PLAYLIST_ID)."""
        for track in tracks:
            self.tracks[track["id"]] = track
            self.saved_tracks.append(saved_item(track))

    def paged(self, items, limit=50):
        """A multi-page collection to embed in another object -- specifically
        an album's `tracks`, which backfill._fetch_full_tracklist walks with
        sp.next() past Spotify's 50-item first page (§4.5).

        Without this a fake album could only ever be one page, so the one place
        the backfill deliberately differs from entities.fetch_album_tracklist
        would be untestable.
        """
        return self._paginate(items, limit)

    def add_track(self, track, **kwargs):
        """Registers a track object (or builds one from an id) so that it comes
        back from sp.track() and from a loader read-back."""
        if isinstance(track, str):
            track = spotify_track(track, **kwargs)
        self.tracks[track["id"]] = track
        return track

    def add_album(self, album):
        self.albums[album["id"]] = album
        return album

    def add_artist(self, artist):
        self.artists[artist["id"]] = artist
        return artist

    def substitute(self, requested_uri, served_track_id, linked_from=True):
        """Spotify serves a different track than the one asked for.

        `linked_from=True` is the honest case, where the served track names what
        was requested and roundtrip can write a real alias. `linked_from=False`
        is the case that made the reconciliation pass necessary: the
        substitution happens but nothing in the response says so.
        """
        self.substitutions[requested_uri] = (served_track_id, linked_from)

    def drop(self, requested_uri):
        """This uri comes back from nothing at all."""
        self.dropped.add(requested_uri)

    def fail(self, method, exception, times=1):
        """Queue `exception` for the next `times` calls to `method`."""
        self._failures[method].extend([exception] * times)

    # -- Endpoints ---------------------------------------------------

    def current_user(self):
        self._record("current_user")
        return dict(self.user)

    def current_user_playlists(self, limit=50, offset=0):
        self._record("current_user_playlists", limit=limit, offset=offset)
        return self._paginate(self.playlists, limit, offset)

    def current_user_saved_tracks(self, limit=50, offset=0):
        self._record("current_user_saved_tracks", limit=limit, offset=offset)
        return self._paginate(self.saved_tracks, limit, offset)

    def playlist(self, playlist_id):
        self._record("playlist", playlist_id)
        for playlist in self.playlists:
            if playlist["id"] == playlist_id:
                return playlist
        raise not_found(f"playlist {playlist_id}")

    def playlist_items(self, playlist_id, limit=100, offset=0):
        # 404 rather than an empty page, like the real endpoint and like
        # playlist()/track()/album()/artist() above. An empty page for a
        # playlist nobody registered would read as "every uri came back
        # missing", which is a real round-trip outcome and would look like one.
        self._record("playlist_items", playlist_id, limit=limit, offset=offset)
        if playlist_id not in self.items:
            raise not_found(f"playlist {playlist_id}")
        return self._paginate(self.items[playlist_id], limit, offset)

    def playlist_replace_items(self, playlist_id, uris):
        """The only write this fake has, and the only one roundtrip is allowed
        to make. Replaces -- the playlist afterwards holds exactly `uris`,
        resolved through substitutions and drops."""
        self._record("playlist_replace_items", playlist_id, uris)
        if playlist_id not in self.items:
            raise not_found(f"playlist {playlist_id}")
        self.replacements.append((playlist_id, list(uris)))
        self.items[playlist_id] = self._serve(uris)
        return {"snapshot_id": f"snap-{len(self.replacements)}"}

    def track(self, track_id):
        self._record("track", track_id)
        if track_id not in self.tracks:
            raise not_found(f"track {track_id}")
        return self.tracks[track_id]

    def album(self, album_id):
        self._record("album", album_id)
        if album_id not in self.albums:
            raise not_found(f"album {album_id}")
        return self.albums[album_id]

    def artist(self, artist_id):
        self._record("artist", artist_id)
        if artist_id not in self.artists:
            raise not_found(f"artist {artist_id}")
        return self.artists[artist_id]

    def next(self, page):
        self._record("next")
        # None past the end, matching spotipy: a loop that called next()
        # unconditionally would then fail here rather than quietly reading an
        # empty page forever.
        token = page.get("next")
        return self._pages[token] if token is not None else None

    # -- Plumbing ----------------------------------------------------

    def _record(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        queued = self._failures.get(method)
        if queued:
            raise queued.pop(0)

    def _serve(self, uris):
        """What the loader playlist holds after being asked for `uris`.

        Order is preserved here only because a list has one; nothing reading it
        may depend on that. A substituted track carries the *requested* uri in
        `linked_from`, which is the only trustworthy way to pair the two.
        """
        served = []
        for uri in uris:
            if uri in self.dropped:
                continue
            requested_id = uri.rsplit(":", 1)[-1]
            substitution = self.substitutions.get(uri)
            if substitution is None:
                served.append(playlist_item(self.tracks.get(requested_id) or spotify_track(requested_id)))
                continue
            served_id, carries_linked_from = substitution
            track = dict(self.tracks.get(served_id) or spotify_track(served_id))
            if carries_linked_from:
                track["linked_from"] = {
                    "id": requested_id,
                    "uri": uri,
                    "type": "track",
                    "external_urls": {"spotify": f"https://open.spotify.com/track/{requested_id}"},
                }
            served.append(playlist_item(track))
        return served

    def _paginate(self, items, limit, offset=0):
        """Splits into pages and returns the first.

        `next` holds an opaque token rather than a URL. Callers only ever test
        it for truth and hand it back to sp.next(), which is exactly what
        spotipy's real pages support -- so a token is indistinguishable to them
        and does not invite a test to parse a URL that would not be there.

        `total` is the whole collection, **not** what is left after `offset` --
        that is what Spotify returns, and a fake that shrank it with the offset
        would make a paging progress figure read low and correct-looking.
        """
        collection = list(items)
        window = collection[offset:]
        chunks = [window[i:i + limit] for i in range(0, len(window), limit)] or [[]]
        pages = [{"items": chunk, "next": None, "total": len(collection)} for chunk in chunks]
        for index, page in enumerate(pages[:-1]):
            token = f"page-{self._page_counter}"
            self._page_counter += 1
            self._pages[token] = pages[index + 1]
            page["next"] = token
        return pages[0]
