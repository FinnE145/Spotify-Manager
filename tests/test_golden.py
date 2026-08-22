"""`golden.py`'s own tests -- proving the capture/compare tooling can
actually detect a difference, not just run without erroring.

`P2_tests.md` §1's first question, applied to the tooling itself rather than
to a test: a compare() that can never report a diff is worth nothing. No
snapshots are captured or committed here -- this only proves the mechanism
works, against a throwaway `tmp_path`.
"""

import builders
import canonical
import golden


def _corpus(conn):
    """Enough for routes_catalog.discover() to resolve every placeholder --
    same shape as test_routes.py's corpus fixture, kept separate since this
    module only needs the golden-relevant subset.
    """
    t1 = builders.make_track(conn, "t-golden-1", name="Golden Track")
    t2 = builders.make_track(conn, "t-golden-2b", name="Golden Track Two")
    builders.make_group(conn, [t1, t2])
    artist = builders.make_artist(conn, "ar-golden", name="Golden Artist")
    album = builders.make_album(conn, "al-golden", name="Golden Album", artists=[artist])
    playlist = builders.make_playlist(conn, "p-golden", name="Golden Playlist")
    builders.make_membership(conn, playlist_id=playlist, track_id=t1)
    gen_playlist = builders.make_playlist(conn, "p-golden-gen", name="v1.0.0")
    builders.make_generation(conn, ordinal=1, playlist_id=gen_playlist)
    builders.make_membership(conn, playlist_id=gen_playlist, track_id=t1)
    builders.make_card(conn, x=0, y=0)
    builders.make_label(conn, x=0, y=0)
    canonical.ensure_track_groups(conn)
    conn.commit()
    return t1, playlist


def test_compare_reports_no_diff_immediately_after_capture(client, conn, tmp_path):
    # source: P2_tests.md §4.6 -- the ephemeral layer is "captured immediately
    # before P3, diffed after"; a capture must compare clean against itself.
    _corpus(conn)

    written = golden.capture(client, conn, str(tmp_path))
    diffs = golden.compare(client, conn, str(tmp_path))

    assert written  # sanity: it actually captured something
    assert diffs == []


def test_compare_detects_a_real_content_change(client, conn, tmp_path):
    # This is the test that matters: compare() must be able to fail. Capture
    # first, then change something that alters rendered output, then compare
    # again and confirm the affected case is named.
    # source: P2_tests.md §4.6 -- byte-exact snapshots; the whole point is that
    # a changed page is reported.
    t1, playlist = _corpus(conn)

    golden.capture(client, conn, str(tmp_path))

    t2 = builders.make_track(conn, "t-golden-2", name="A New Track")
    builders.make_membership(conn, playlist_id=playlist, track_id=t2)
    conn.commit()

    diffs = golden.compare(client, conn, str(tmp_path))

    diff_slugs = {slug for slug, _ in diffs}
    assert "get_playlist_page" in diff_slugs


def test_compare_reports_a_case_missing_its_snapshot(client, conn, tmp_path):
    # characterization -- a route added between capture and compare has nothing
    # to diff against, and must be reported rather than silently skipped.
    _corpus(conn)

    diffs = golden.compare(client, conn, str(tmp_path))

    assert any(detail == "no snapshot captured" for _, detail in diffs)


def test_compare_reports_a_stale_snapshot_no_longer_in_the_catalog(client, conn, tmp_path):
    # characterization -- the other direction: a snapshot whose route is gone.
    _corpus(conn)
    golden.capture(client, conn, str(tmp_path))

    import os

    with open(os.path.join(str(tmp_path), "get_a_route_that_no_longer_exists.html"), "w") as f:
        f.write("stale")

    diffs = golden.compare(client, conn, str(tmp_path))

    assert ("get_a_route_that_no_longer_exists", "missing from current catalog") in diffs


def test_golden_cases_excludes_post_routes(conn):
    # source: golden.py's module docstring -- "GET routes only, since a POST
    # changes state and would make a snapshot depend on the order captures
    # ran in."
    import routes_catalog

    _corpus(conn)
    cases = routes_catalog.golden_cases(conn)

    assert all(case.method == "GET" for case in cases)


def test_golden_cases_excludes_login_and_callback(conn):
    # source: golden.py's module docstring -- both produce a response that
    # legitimately differs run to run (fresh OAuth state, session-dependent
    # branching), so a byte diff on either is noise, not signal.
    import routes_catalog

    _corpus(conn)
    cases = routes_catalog.golden_cases(conn)

    slugs = {case.slug for case in cases}
    assert "get_login" not in slugs
    assert "get_callback" not in slugs
