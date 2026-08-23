"""P3's golden capture/compare passes, driven from pytest (`P3_refactor.md` §3).

**Opt-in, and deliberately not part of the ordinary suite.** These are two
operations in a specific refactor's verification story, not permanent
regression tests: capture once at the branch's starting commit, compare at the
end of every session, delete the snapshots at the end of P3. Left unmarked
they would fail for everyone else the moment the snapshots are gone, which is
the reflexive-regeneration failure `codebase-health-P.md` §4 exists to avoid.

    venv/bin/python tests/golden.py ...      # NOT this -- see below
    SYMR_GOLDEN=capture venv/bin/python -m pytest tests/test_golden_pass.py
    SYMR_GOLDEN=compare venv/bin/python -m pytest tests/test_golden_pass.py

**Why pytest rather than `golden.py`'s `__main__`** (§3.2): everything that
makes a byte diff reproducible already lives in `conftest.py`, and the
standalone CLI has none of it. Specifically, four sources of non-determinism
have to be dead before "any diff at all is a bug" is literally true, and three
of them are already handled by fixtures this module inherits for free:

  1. **the async scoring worker** -- `ensure_fresh()` runs in a before_request
     hook on every page and enqueues a recompute; a background pass landing
     mid-capture would rescore every page rendered after it, at a different
     point each run. conftest's autouse `recompute_calls` fixture replaces
     `request_recompute` with a recorder, so no worker thread ever spawns.
     `test_the_async_recompute_worker_is_disarmed` asserts that rather than
     trusting it, because it is an assumption held by a *different* file.
  2. **the clock** -- scoring's 90-day `recent` horizon and `play_stats`'
     30d/7d windows are `now`-dependent, and so are `api_log`'s rolling
     counts on `/dev`. conftest's autouse `freezer` pins all of them.
  3. **outbound HTTP** -- blocked outright, so `fetch_album_tracklist` and
     `fetch_artist_image` fail identically every run instead of depending on
     what Spotify happened to return.
  4. **write-on-read** -- the one this module handles itself, in `golden_db`:
     those failed fetches still *stamp* "attempted" (P1-016), and
     `ensure_track_groups` / `queue_wanted_uris` write on a plain GET. See
     `golden.restore`.
"""

import os

import pytest

import app as app_module
import config
import db
import golden
import routes_catalog
import scoring

#: "capture" | "compare" | None. An env var rather than a pytest option so
#: nothing has to be added to conftest.py, whose ordering is load-bearing.
_ACTION = os.environ.get("SYMR_GOLDEN")

_HERE = os.path.dirname(os.path.abspath(__file__))

#: Gitignored (`.gitignore:20`), so a capture cannot be committed by accident.
SNAPSHOT_DIR = os.path.join(_HERE, "golden_snapshots")

#: The plain copy of `symr.db` both passes render against (§3.1). It lives
#: beside the snapshots because it has the same lifetime: made once, used by
#: all three sessions, deleted with them at the end of P3. `*.db` is
#: separately gitignored, so it is covered twice over.
PRISTINE = os.path.join(SNAPSHOT_DIR, "_pristine.db")

#: Where a failing compare dumps what each differing case rendered, for an
#: ordinary `diff` against the snapshot beside it.
ACTUAL_DIR = os.path.join(SNAPSHOT_DIR, "_actual")

pytestmark = pytest.mark.skipif(
    _ACTION not in ("capture", "compare"),
    reason="P3 golden pass; set SYMR_GOLDEN=capture or SYMR_GOLDEN=compare",
)


@pytest.fixture
def golden_db(_clean_slate):
    """Restores the run copy from the pristine copy, then opens it.

    Requests `_clean_slate` explicitly even though it is autouse, because the
    ordering is the point rather than a coincidence: `_clean_slate` wipes the
    database and stamps the *empty* schema template over it, and this has to
    land afterwards or the pass renders 60 pages of an empty library.

    The run copy is written to `config.DB_PATH` itself -- not to a path of its
    own -- because `db.py` and `scoring.py` both `from config import DB_PATH`,
    so the path is bound at import and cannot be changed afterwards. Being
    inside conftest's temp directory is also what satisfies the layer-3
    connect guard.
    """
    if not os.path.exists(PRISTINE):
        pytest.fail(
            f"No pristine golden database at {PRISTINE}.\n"
            "Make one first (a plain copy of symr.db, per P3_refactor.md §3.1):\n"
            "    venv/bin/python -c \"import sys; sys.path.insert(0, 'tests'); "
            "import golden; golden.make_pristine('symr.db', "
            f"'{PRISTINE}')\""
        )

    golden.restore(PRISTINE, config.DB_PATH)
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def golden_client(golden_db, fake_spotify):
    """A client on an app built *after* the restore.

    `create_app()` calls `db.init_db()` (app.py:41), which migrates and can
    rebuild the views -- so an app built before the restore would run that
    against the empty template and leave the real copy untouched. Depending on
    `golden_db` is what orders the two.
    """
    return app_module.create_app().test_client()


def test_the_async_recompute_worker_is_disarmed(golden_db):
    # source: P3_refactor.md §3.2 -- the async scoring worker is the first of
    # the four sources of non-determinism that "must be dead", since a
    # recompute landing mid-pass changes scores for every page rendered later
    # in that same pass. conftest supplies this, so what is asserted here is
    # that the assumption still holds -- it is owned by another file, and
    # nothing else in this module would notice if it stopped being true.
    # conftest captures the genuine function before any fixture stubs it, so
    # identity against that is the unambiguous check -- the real one spawns a
    # worker thread, the replacement appends to a list.
    import conftest

    assert scoring.request_recompute is not conftest.REAL_REQUEST_RECOMPUTE


@pytest.mark.skipif(_ACTION != "capture", reason="capture pass only")
def test_capture(golden_db, golden_client):
    # source: P3_refactor.md §3.4 -- "Capture once, at the branch's starting
    # commit, before session 1's first edit." The assertions are the
    # harness's own version of P2_tests.md §1's first question: a capture of
    # 60 error pages would compare clean forever and prove nothing, so the
    # statuses and the file sizes are checked rather than just the count.
    written = golden.capture(golden_client, golden_db, SNAPSHOT_DIR)

    expected = len(routes_catalog.golden_cases(golden_db))
    assert len(written) == expected

    failed = [(slug, status) for slug, status in written if status >= 500]
    assert failed == []

    empty = [
        slug
        for slug, _ in written
        if os.path.getsize(os.path.join(SNAPSHOT_DIR, f"{slug}.html")) == 0
    ]
    assert empty == []


@pytest.mark.skipif(_ACTION != "compare", reason="compare pass only")
def test_compare(golden_db, golden_client):
    # source: P3_refactor.md §1 -- P3's single acceptance criterion is that
    # "nothing observable changed", and §3.3 makes zero diffs a hard
    # precondition rather than a smoke test.
    diffs = golden.compare(golden_client, golden_db, SNAPSHOT_DIR, actual_dir=ACTUAL_DIR)

    report = "\n".join(f"  {slug}: {detail}" for slug, detail in diffs)
    assert diffs == [], (
        f"{len(diffs)} golden case(s) differ:\n{report}\n\n"
        f"What each rendered this time is in {ACTUAL_DIR}/ -- diff it against "
        f"{SNAPSHOT_DIR}/<slug>.html."
    )
