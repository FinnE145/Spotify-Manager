"""A small library for the v1 API's tests (docs/specs/api-Z1.md).

Built so the rules an implementation could fall back on all disagree with each
other, which is the only way a test of ordering or matching can fail:

  * the highest-scoring song ("Zebra") is alphabetically **last**, and the
    lowest-scoring ("Aardvark") alphabetically first, so a sort that ignored the
    score and fell back to name or id would be caught;
  * "Beta Alt" is an alias id of "Beta", and credits on one track use it;
  * the song "Zebra" has two versions, one on a 2019 album and one on a 2023
    album, so "earliest member" and "any member" differ;
  * "half•alive" is a punctuation-bearing name the normalizer folds.
"""

import builders
from builders import make_album, make_artist, make_group, make_playlist, make_score, make_track


def build(conn):
    for artist_id, name in (
        ("ar-alpha", "Alpha"), ("ar-beta", "Beta"), ("ar-beta2", "Beta Alt"), ("ar-half", "half•alive"),
    ):
        make_artist(conn, artist_id, name)
    conn.execute(
        "INSERT INTO artist_alias (artist_id, canonical_artist_id) VALUES ('ar-beta2', 'ar-beta')"
    )
    conn.commit()

    make_album(conn, "al-z", "Zed Album", artists=["ar-alpha"], release_year=2019,
               release_date="2019-05-01", release_date_sortable="2019-05-01", total_tracks=2)
    make_album(conn, "al-a", "Alpha Album", artists=["ar-beta"], release_year=2023,
               release_date="2023-02-01", release_date_sortable="2023-02-01", total_tracks=3)
    make_album(conn, "al-h", "Half Album", artists=["ar-half"], release_year=2021,
               release_date="2021-07-01", release_date_sortable="2021-07-01", total_tracks=1)

    make_track(conn, "t-aard", "Aardvark", album_id="al-a", artists=["ar-beta"],
               duration_ms=200_000, explicit=1)
    make_track(conn, "t-zeb", "Zebra", album_id="al-z", artists=["ar-alpha", "ar-beta2"],
               duration_ms=300_000)
    make_track(conn, "t-zeb-live", "Zebra (Live)", album_id="al-a", artists=["ar-beta"],
               duration_ms=250_000)
    make_track(conn, "t-half", "half•alive", album_id="al-h", artists=["ar-half"],
               duration_ms=180_000)

    g_aard = make_group(conn, ["t-aard"])
    g_zeb = make_group(conn, ["t-zeb"])
    g_live = make_group(conn, ["t-zeb-live"], song=g_zeb["song"])
    g_half = make_group(conn, ["t-half"])

    # Version scores: Zebra highest, Aardvark lowest -- alphabetical order
    # inverted. Track scores follow so the representative election has a score
    # to read too.
    for tier_id, track_id, score in (
        (g_zeb["version"], "t-zeb", 90.0), (g_live["version"], "t-zeb-live", 60.0),
        (g_half["version"], "t-half", 50.0), (g_aard["version"], "t-aard", 10.0),
    ):
        make_score(conn, "version", tier_id, all_time=score, recent=score - 5)
        make_score(conn, "track", track_id, all_time=score, recent=score - 5)

    drop_strays(conn)

    return {
        "groups": {"aard": g_aard, "zeb": g_zeb, "live": g_live, "half": g_half},
        "tracks": ["t-aard", "t-zeb", "t-zeb-live", "t-half"],
    }


def ids_for_sweep(conn, lib):
    """The placeholder values routes_catalog's v1 cases need, from this library."""
    pid = make_playlist(conn, "pl-mix", "Mix")
    builders.make_membership(conn, pid, "t-aard")
    ordinal = builders.make_generation(conn)
    g = lib["groups"]["zeb"]
    return {
        "track": "t-zeb", "track2": "t-aard", "album": "al-z", "artist": "ar-beta",
        "playlist": pid, "song": g["song"], "version": g["version"],
        "recording": g["recording"], "release": g["release"],
        "generation_ordinal": ordinal,
        "generation_playlist": conn.execute(
            "SELECT playlist_id FROM generation WHERE ordinal = ?", (ordinal,)
        ).fetchone()[0],
    }


def drop_strays(conn):
    """make_group / make_membership / make_play re-call make_track for a track
    that exists, which mints a stray album and artist before noticing; drop
    them so a count over the library is exactly what a test declared."""
    conn.execute(
        "DELETE FROM album_artist WHERE album_id NOT IN "
        "(SELECT album_id FROM track WHERE album_id IS NOT NULL)"
    )
    conn.execute("DELETE FROM album WHERE album_id NOT IN (SELECT album_id FROM track WHERE album_id IS NOT NULL)")
    conn.execute(
        "DELETE FROM artist WHERE artist_id NOT IN (SELECT artist_id FROM track_artist "
        "UNION SELECT artist_id FROM album_artist UNION SELECT artist_id FROM artist_alias "
        "UNION SELECT canonical_artist_id FROM artist_alias)"
    )
    conn.commit()
