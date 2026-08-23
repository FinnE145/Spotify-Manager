"""`snapshot.index_data` -- `/dev/snapshot`'s read path
(docs/codebase-health/P3_refactor.md §4.1).

Extracted out of `app.py`'s `snapshot_index` in P3 session 3. Session 3's
mutation sweep over its six payload keys found `playlists` and `changes` held
up by the golden baseline alone -- the suite §3.4 deletes at the end of P3 --
so those two are what the assertions here exist for. `summary`, `query` and
`track_matches` already die against the permanent suite and are not re-tested
from this side.

The page's pending-generation prompt is deliberately not part of this payload
(it stays in the route, so `snapshot.py` gains no dependency on
`generations.py`), so there is nothing to assert about it here.
"""

import builders
import snapshot


def test_index_data_selects_every_snapshot_column_and_no_others(conn):
    # source: P3_refactor.md §4.5 -- this was the second of the two
    # `SELECT * FROM snapshot` sites P3 named, and the accepted cost of a
    # named list is that "a named list needs updating when a column is
    # added". Compared against PRAGMA table_info for the same reason its
    # sibling on /playlist/<id> is: a migration adding a column the template
    # might read fails here rather than as a Jinja UndefinedError.
    builders.make_playlist(conn, "p-columns", name="Columns")

    data = snapshot.index_data(conn, "")

    assert set(data["playlists"][0].keys()) == {
        "playlist_id", "name", "image_url", "owner", "track_count", "pulled_at",
        "snapshot_id", "last_changed_at", "tracks_pulled_at", "unfollowed_at",
        "description", "last_pull_error", "excluded", "generation_declined",
        "tracks_pulled_snapshot_id",
    }
    assert set(data["playlists"][0].keys()) == {
        r["name"] for r in conn.execute("PRAGMA table_info(snapshot)")
    }


def test_playlists_rank_by_score_and_fall_back_to_name(conn):
    # source: docs/specs/scoring-H.md §11.1 -- "/dev/snapshot playlist list"
    # moves from name to score. Both rules are exercised at once and they
    # disagree: the scored playlist is last alphabetically, so a name-only
    # implementation puts it third, while the two unscored ones are inserted
    # in reverse alphabetical order, so an insertion-order implementation
    # gets those two backwards.
    scored = builders.make_playlist(conn, "p-zebra", name="Zebra")
    group = builders.make_group(conn, ["ta", "tb"])
    builders.make_score(conn, "version", group["version"], all_time=90.0)
    builders.make_membership(conn, playlist_id=scored, track_id="ta")
    builders.make_playlist(conn, "p-beta", name="Beta")
    builders.make_playlist(conn, "p-alpha", name="Alpha")

    data = snapshot.index_data(conn, "")

    assert [p["name"] for p in data["playlists"]] == ["Zebra", "Alpha", "Beta"]


def test_every_playlist_is_listed_including_excluded_and_unfollowed_ones(conn):
    # source: characterization of snapshot.html, which renders the excluded
    # ones with their toggle in place rather than hiding them -- the page is
    # where you go to un-exclude. A WHERE excluded = 0 would make the row
    # unreachable from the only UI that can change it.
    builders.make_playlist(conn, "p-live", name="Live")
    builders.make_playlist(conn, "p-excluded", name="Excluded", excluded=1)
    builders.make_playlist(conn, "p-gone", name="Gone", unfollowed_at=builders.days_ago(2))

    data = snapshot.index_data(conn, "")

    assert sorted(p["name"] for p in data["playlists"]) == ["Excluded", "Gone", "Live"]


def test_changes_are_the_newest_membership_events_first_and_carry_their_kind(conn):
    # source: characterization of snapshot.html's "Recent changes" table.
    # event_at is COALESCE(removed_at, added_at), so the removal -- added
    # long ago, removed yesterday -- has to sort *first*. Ordering on
    # added_at instead puts it second, which is the mutation this catches.
    builders.make_membership(
        conn, playlist_id="p-1", track_id="ta", added_at=builders.days_ago(30)
    )
    builders.make_membership(
        conn,
        playlist_id="p-2",
        track_id="tb",
        added_at=builders.days_ago(20),
        removed_at=builders.days_ago(1),
    )
    builders.make_membership(
        conn, playlist_id="p-3", track_id="tc", added_at=builders.days_ago(5)
    )

    changes = snapshot.index_data(conn, "")["changes"]

    assert [(c["track_id"], c["kind"]) for c in changes] == [
        ("tb", "removed"),
        ("tc", "added"),
        ("ta", "added"),
    ]
