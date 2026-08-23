"""`golden.py`'s own tests -- proving the capture/compare tooling can
actually detect a difference, not just run without erroring.

`P2_tests.md` §1's first question, applied to the tooling itself rather than
to a test: a compare() that can never report a diff is worth nothing. No
snapshots are captured or committed here -- this only proves the mechanism
works, against a throwaway `tmp_path`.
"""

import os

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


# -- the P3 harness: make_pristine / restore / actual_dir ---------------
#
# These three exist only for P3's golden passes (P3_refactor.md §3.1/§3.2),
# but they are the layer everything else in that verification story stands
# on: a make_pristine that stopped refusing a torn copy would produce a
# baseline of subtly wrong pages, and session 2 would read the resulting
# diffs as damage done by its own refactor.
#
# Note the fixtures are not SQLite databases -- just bytes. That is
# deliberate, and it is what pins the "opens no connection" property below:
# an implementation reaching for sqlite3's backup API instead of copying
# bytes would fail on every one of these.


def test_make_pristine_refuses_a_source_whose_write_ahead_log_is_not_empty(tmp_path):
    # source: P3_refactor.md §3.1 -- the pristine copy is "a plain copy of
    # symr.db", and a byte copy is only a *complete* database when nothing is
    # pending in the WAL. Asserting the dest was not created matters as much
    # as the raise: a version that refused *after* copying would leave a torn
    # baseline on disk for the next run to find and trust.
    source = tmp_path / "src.db"
    source.write_bytes(b"main database contents")
    (tmp_path / "src.db-wal").write_bytes(b"pending frames")
    dest = tmp_path / "out" / "pristine.db"

    try:
        golden.make_pristine(str(source), str(dest))
    except RuntimeError as exc:
        assert "write-ahead log" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError for a non-empty -wal")

    assert not dest.exists()


def test_make_pristine_copies_an_empty_wal_source_without_opening_it(tmp_path):
    # source: P3_refactor.md §3.1 -- "An empty `-wal` means the last
    # connection checkpointed on close, which is the state the file is in
    # whenever the app is not running", so a present-but-empty -wal must
    # *proceed*. That is the discriminating half: a guard written as "refuse
    # if a -wal exists" rather than "if it is non-empty" would reject the
    # normal case and P3 could never take a baseline at all.
    #
    # The -shm assertion is golden.py's other stated claim -- it copies bytes
    # precisely so it never opens the real 93 MB library. A connection would
    # create a -shm beside the source; copyfile cannot.
    source = tmp_path / "src.db"
    source.write_bytes(b"main database contents")
    (tmp_path / "src.db-wal").write_bytes(b"")
    dest = tmp_path / "out" / "pristine.db"

    returned = golden.make_pristine(str(source), str(dest))

    assert returned == str(dest)
    assert dest.read_bytes() == b"main database contents"
    assert not (tmp_path / "src.db-shm").exists()


def test_restore_replaces_the_run_copy_and_clears_its_stale_sidecar_files(tmp_path):
    # source: P3_refactor.md §3.1 -- "The restore is not optional", because a
    # capture pass leaves the database in a state its own first request never
    # saw (write-on-read). Both halves are asserted: the bytes must come back
    # to the pristine ones, *and* the old -wal/-shm must go, since golden.py's
    # docstring notes a stale pair against fresh bytes is a corrupt database
    # rather than an out-of-date one. A plain copyfile passes the first half
    # and fails the second.
    pristine = tmp_path / "pristine.db"
    pristine.write_bytes(b"pristine contents")
    target = tmp_path / "run.db"
    target.write_bytes(b"contents written by the previous pass")
    (tmp_path / "run.db-wal").write_bytes(b"stale frames")
    (tmp_path / "run.db-shm").write_bytes(b"stale index")

    returned = golden.restore(str(pristine), str(target))

    assert returned == str(target)
    assert target.read_bytes() == b"pristine contents"
    assert not (tmp_path / "run.db-wal").exists()
    assert not (tmp_path / "run.db-shm").exists()
    assert pristine.read_bytes() == b"pristine contents"


def test_restore_works_when_the_target_does_not_exist_yet(tmp_path):
    # characterization -- the first pass of a session has no run copy to
    # replace, so the removal step must tolerate a missing file rather than
    # raising FileNotFoundError before it ever reaches the copy.
    pristine = tmp_path / "pristine.db"
    pristine.write_bytes(b"pristine contents")
    target = tmp_path / "run.db"

    golden.restore(str(pristine), str(target))

    assert target.read_bytes() == b"pristine contents"


def test_capture_reports_the_status_each_case_actually_returned(client, conn, tmp_path):
    # source: P3_refactor.md §3.3 + golden.py's capture() docstring -- the
    # status is carried out of capture() so a baseline of error pages cannot
    # pass for a baseline of real ones ("60 identical 500s would compare clean
    # forever"). test_golden_pass.py's capture assertion is only worth
    # anything if this number comes from the response, so the fake below
    # returns 500: an implementation reporting a hardcoded 200 -- which every
    # real case here would also return -- passes without it.
    _corpus(conn)

    import routes_catalog

    class _FakeResponse:
        data = b"<html>an error page</html>"
        status_code = 500

    real_issue = routes_catalog.issue
    routes_catalog.issue = lambda *args, **kwargs: _FakeResponse()
    try:
        written = golden.capture(client, conn, str(tmp_path))
    finally:
        routes_catalog.issue = real_issue

    assert written
    assert {status for _slug, status in written} == {500}


def test_compare_writes_what_a_differing_case_rendered_this_time(client, conn, tmp_path):
    # source: golden.py's compare() docstring -- actual_dir exists so a diff
    # can be read with an ordinary `diff` rather than inferred from a byte
    # count. Three things have to hold for that to be true, and only the
    # first is obvious: the file is written, it holds the *new* bytes rather
    # than a re-copy of the snapshot, and a case that did **not** differ gets
    # no file -- otherwise the directory is a dump of everything and says
    # nothing about which case to look at.
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    actual = tmp_path / "actual"

    t1, playlist = _corpus(conn)
    golden.capture(client, conn, str(snapshots))

    t2 = builders.make_track(conn, "t-golden-2", name="A New Track")
    builders.make_membership(conn, playlist_id=playlist, track_id=t2)
    conn.commit()

    diffs = golden.compare(client, conn, str(snapshots), actual_dir=str(actual))

    diff_slugs = {slug for slug, _ in diffs}
    assert "get_playlist_page" in diff_slugs

    written = (actual / "get_playlist_page.html").read_bytes()
    assert b"A New Track" in written
    assert written != (snapshots / "get_playlist_page.html").read_bytes()

    unchanged = {p.stem for p in actual.glob("*.html")}
    assert unchanged == diff_slugs


def test_compare_writes_nothing_when_no_actual_dir_is_given(client, conn, tmp_path):
    # characterization -- actual_dir is optional, and the ordinary
    # self-tests above call compare() without it. A version that always wrote
    # would need somewhere to write to, so this pins that the parameter is
    # genuinely opt-in rather than defaulted to a path beside the snapshots.
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()

    t1, playlist = _corpus(conn)
    golden.capture(client, conn, str(snapshots))

    builders.make_membership(
        conn, playlist_id=playlist, track_id=builders.make_track(conn, "t-golden-3", name="Third")
    )
    conn.commit()

    diffs = golden.compare(client, conn, str(snapshots))

    assert diffs
    assert sorted(os.listdir(str(tmp_path))) == ["snapshots"]
