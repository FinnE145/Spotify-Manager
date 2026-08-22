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

Usable as a script for P3, run directly (matching the project's own
`venv/bin/python app.py` convention -- not `-m`, since `tests/` carries no
`__init__.py` and isn't a package):

    venv/bin/python tests/golden.py capture tests/golden_snapshots
    venv/bin/python tests/golden.py compare tests/golden_snapshots

`.gitignore` excludes `tests/golden_snapshots/` by convention -- P3 should
use that path unless there's a reason not to, so the capture step can't
accidentally get committed.

against whatever DB `SYMR_DB_PATH` (via conftest's guard, or a real temp
copy) points the app at -- P3 builds a sampled DB for this; the ordinary
suite's builders corpus works too, for the tooling's own self-test.
"""

import os


def capture(client, conn, out_dir):
    """Writes one `<slug>.html` per golden case into `out_dir`.

    Returns the list of slugs written.
    """
    import routes_catalog

    os.makedirs(out_dir, exist_ok=True)
    written = []
    for case in routes_catalog.golden_cases(conn):
        resp = routes_catalog.issue(client, case)
        path = os.path.join(out_dir, f"{case.slug}.html")
        with open(path, "wb") as f:
            f.write(resp.data)
        written.append(case.slug)
    return written


def compare(client, conn, out_dir):
    """Re-issues every golden case and diffs it against what's in `out_dir`.

    Returns a list of `(slug, detail)` for every case that differs, plus one
    entry per snapshot file present on disk but no longer in the catalog
    (`detail="missing from current catalog"`) and one per catalog case with
    no snapshot on disk (`detail="no snapshot captured"`). An empty return
    means nothing changed since capture() ran.
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
