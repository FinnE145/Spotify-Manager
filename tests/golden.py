"""Byte-exact HTML golden snapshot capture/compare (P2_tests.md §4.6).

**The tooling is committed here; the snapshots are never committed.** A
permanently-maintained byte-exact suite fails on nearly every feature branch
for entirely legitimate reasons (a template edit, a copy change), and a test
that routinely fails legitimately gets regenerated reflexively -- at which
point it protects nothing. So this stays inert (no captured files, nothing
this module does runs automatically) until P3's refactor actually needs it:
capture immediately before the refactor, diff after, then delete.

Runs against `routes_catalog.golden_cases(conn)` -- GET routes only, since a
POST changes state and would make a snapshot depend on the order captures
ran in. `login`/`callback` are excluded there too (see `Case.golden` in
routes_catalog.py): both produce a response that legitimately differs run to
run (a fresh OAuth `state`, session-dependent branching), so a byte diff on
either would be meaningless noise, not a signal.

**P3 drives this from pytest, not from the `__main__` block below**
(`P3_refactor.md` §3.2). `tests/test_golden_pass.py` is that driver. The
reason is the guards: `conftest.py` supplies the frozen clock, the blocked
sockets and the connect guard, and the standalone CLI has none of them --
which is exactly how it reached the real `symr.db` on 2026-08-21 and wrote 9
`wanted_uri` rows. The `__main__` block stays as the last line of defence,
not as the path anyone uses.

Still usable as a script, run directly (matching the project's own
`venv/bin/python app.py` convention -- not `-m`, since `tests/` carries no
`__init__.py` and isn't a package):

    venv/bin/python tests/golden.py capture tests/golden_snapshots
    venv/bin/python tests/golden.py compare tests/golden_snapshots

`.gitignore` excludes `tests/golden_snapshots/` by convention -- P3 should
use that path unless there's a reason not to, so the capture step can't
accidentally get committed.

against whatever DB `SYMR_DB_PATH` (via conftest's guard, or a real temp
copy) points the app at -- P3 runs it against a plain copy of `symr.db`
(`make_pristine`/`restore` below); the ordinary suite's builders corpus works
too, for the tooling's own self-test.

**Run as a script it carries its own `symr.db` guard**, because nothing else
does: it is the only thing in `tests/` that runs outside pytest, so none of
`conftest.py`'s four layers apply. It refuses to start unless `SYMR_DB_PATH`
is set and resolves somewhere other than the real database. Neither the
capture nor the compare pass is read-only in practice -- `create_app()`
migrates, and a plain GET writes -- so this is a hard exit, not a warning.
"""

import os
import shutil


def make_pristine(source, dest):
    """Takes the one-off pristine copy of the golden database (§3.1).

    **Opens no SQLite connection to `source`.** That is the whole point of
    doing this with `copyfile`: the source is the real 93 MB library, and
    even a read-only connection creates a `-shm`, takes locks, and gives a
    future edit somewhere to go wrong. Copying bytes cannot.

    A bare byte copy is only a *complete* database if nothing is pending in
    the write-ahead log, so this refuses when `<source>-wal` is non-empty
    rather than producing a torn baseline -- which would not announce itself,
    and would render subtly wrong pages that P3 would then read as refactor
    damage. An empty `-wal` means the last connection checkpointed on close,
    which is the state the file is in whenever the app is not running.
    """
    wal = source + "-wal"
    if os.path.exists(wal) and os.path.getsize(wal) > 0:
        raise RuntimeError(
            f"refusing to copy {source}: its write-ahead log ({wal}) is not empty, "
            "so a byte copy would be incomplete. Stop the app and try again."
        )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(source, dest)
    return dest


def restore(pristine, target):
    """Restores the run copy from the pristine copy, before every pass (§3.1).

    **The restore is not optional, and it is what makes a byte diff mean
    anything.** In Symr a plain GET writes -- `ensure_track_groups` on
    `/dev/canonical`, `queue_wanted_uris` on every album page, and P1-016's
    "attempted" stamp, which lands even when the detail fetch *fails* -- so a
    capture pass leaves the database in a state its own first request never
    saw. Comparing against that state would diff on the second pass rendering
    the other branch, which is a fact about Symr's write-on-read design and
    not about the refactor. Both passes start from identical bytes instead.

    Clears `-wal`/`-shm` alongside: they belong to the file being replaced,
    and a stale pair against fresh bytes is a corrupt database, not an old one.
    """
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(target + suffix)
        except FileNotFoundError:
            pass
    shutil.copyfile(pristine, target)
    return target


def capture(client, conn, out_dir):
    """Writes one `<slug>.html` per golden case into `out_dir`.

    Returns a list of `(slug, status_code)`. The status is carried out rather
    than discarded so a caller can tell a captured *page* from a captured
    *error page*: a baseline of 60 identical 500s would compare clean forever
    and prove nothing, which is `P2_tests.md` §1's first question asked of the
    tooling instead of of a test.
    """
    import routes_catalog

    os.makedirs(out_dir, exist_ok=True)
    written = []
    for case in routes_catalog.golden_cases(conn):
        resp = routes_catalog.issue(client, case)
        path = os.path.join(out_dir, f"{case.slug}.html")
        with open(path, "wb") as f:
            f.write(resp.data)
        written.append((case.slug, resp.status_code))
    return written


def compare(client, conn, out_dir, actual_dir=None):
    """Re-issues every golden case and diffs it against what's in `out_dir`.

    Returns a list of `(slug, detail)` for every case that differs, plus one
    entry per snapshot file present on disk but no longer in the catalog
    (`detail="missing from current catalog"`) and one per catalog case with
    no snapshot on disk (`detail="no snapshot captured"`). An empty return
    means nothing changed since capture() ran.

    `actual_dir`, when given, receives a `<slug>.html` of what each differing
    case rendered *this* time, so the diff can be read with an ordinary
    `diff` rather than inferred from a byte count. Written here, at the
    moment of comparison, rather than reconstructed by a caller afterwards:
    re-issuing a third time would run against a database the two earlier
    passes have already written to, so it would not necessarily reproduce
    the bytes being reported.
    """
    import routes_catalog

    on_disk = {
        name[: -len(".html")]
        for name in os.listdir(out_dir)
        if name.endswith(".html")
    } if os.path.isdir(out_dir) else set()

    cases = routes_catalog.golden_cases(conn)
    catalog_slugs = {case.slug for case in cases}

    diffs = []
    for case in cases:
        path = os.path.join(out_dir, f"{case.slug}.html")
        if not os.path.exists(path):
            diffs.append((case.slug, "no snapshot captured"))
            continue
        with open(path, "rb") as f:
            before = f.read()
        after = routes_catalog.issue(client, case).data
        if before != after:
            diffs.append((case.slug, f"byte diff: {len(before)}B -> {len(after)}B"))
            if actual_dir is not None:
                os.makedirs(actual_dir, exist_ok=True)
                with open(os.path.join(actual_dir, f"{case.slug}.html"), "wb") as f:
                    f.write(after)

    for slug in on_disk - catalog_slugs:
        diffs.append((slug, "missing from current catalog"))

    return diffs


if __name__ == "__main__":
    import os as _os
    import sys

    # Run directly (`python tests/golden.py`), Python puts only this file's
    # own directory (tests/) on sys.path -- which is what makes the bare
    # `import routes_catalog` above resolve, but leaves the repo root (for
    # `import app`, `import db`) missing unless added explicitly. conftest.py
    # avoids this entirely by running under pytest, which pythonpath = .
    # (pytest.ini) already covers -- this block exists only for this
    # standalone script path.
    _REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    # The real-symr.db guard, and it runs BEFORE `import app` -- this script
    # is the one thing in tests/ that runs outside pytest, so conftest.py's
    # four guard layers (P2_tests.md §4.1) do not protect it at all, and
    # `create_app()` below calls `db.init_db()`, which runs _migrate() and
    # _ensure_views() against whatever DB_PATH resolves to. It is also not
    # read-only after that: a plain GET writes (ensure_track_groups on
    # /dev/canonical, queue_wanted_uris on every album page, ensure_fresh's
    # recompute) and can spend a real Spotify request on an album/artist
    # page's first view.
    #
    # Two checks rather than one, for §4.1's own reason -- the first is the
    # mechanism, the second catches the day the mechanism changes. The
    # unset-env case is not hypothetical: it is exactly how this script
    # reached the real symr.db on 2026-08-21 (an exported SYMR_DB_PATH did
    # not carry into a later shell invocation), writing 9 wanted_uri rows.
    import config

    _resolved = _os.path.abspath(config.DB_PATH)
    if not _os.environ.get("SYMR_DB_PATH"):
        print(
            "refusing to run: SYMR_DB_PATH is not set, so this would open the "
            f"real library database ({_resolved}). Point it at a copy first.",
            file=sys.stderr,
        )
        sys.exit(2)
    if _resolved == _os.path.join(_REPO_ROOT, "symr.db"):
        print(
            f"refusing to run: SYMR_DB_PATH resolves to the real library "
            f"database ({_resolved}).",
            file=sys.stderr,
        )
        sys.exit(2)

    # Deliberately not imported at module level: importing app/db/conn
    # machinery unconditionally would make `import golden` (as
    # test_golden.py does) drag in Flask app construction every time. As a
    # script, real construction is exactly what's wanted.
    import app as app_module
    import db

    if len(sys.argv) != 3 or sys.argv[1] not in ("capture", "compare"):
        print(f"usage: {sys.argv[0]} capture|compare <out_dir>", file=sys.stderr)
        sys.exit(2)

    action, out_dir = sys.argv[1], sys.argv[2]
    flask_app = app_module.create_app()
    conn = db.connect()
    with flask_app.test_client() as client:
        if action == "capture":
            written = capture(client, conn, out_dir)
            print(f"captured {len(written)} snapshots to {out_dir}")
        else:
            diffs = compare(client, conn, out_dir)
            if diffs:
                for slug, detail in diffs:
                    print(f"DIFF  {slug}: {detail}")
                sys.exit(1)
            print("no differences")
