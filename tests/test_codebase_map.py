"""CLAUDE.md's codebase map, checked against the tree (P3_refactor.md §4.3).

**Deliberately narrow: a list against a list, with no numbers parsed out of
prose.** §4.3 considered and rejected the more ambitious version -- a test
checking the map's numeric claims ("three app-wide `before_request` hooks",
"four background jobs") against `app.url_map` or an AST scan. That is the
drift that actually happened (P2-001 found "three jobs" where there are four;
the pre-spec found "two hooks" after J added a third), so the motivation was
real. But it greps English that gets rewritten constantly: rephrase "three
hooks" to "a trio of hooks" and it fails legitimately, which
`codebase-health-P.md` §4 identifies as precisely the shape that gets
regenerated reflexively until it protects nothing.

What is left cannot fail for a rewording, and catches the drift class that
matters most -- **a module added and never documented**, which P3 itself would
have committed twice over (`normalize.py` in session 1, and session 3's
`tenure_page` landing in a module whose map entry says "entity pages") -- plus
its mirror, a module the map still names after it is gone.

Scope is deliberate too. The forward direction covers the repo's own modules
(root and `scripts/`), which the map documents one bullet each. It does *not*
cover `tests/`: the map names the six files there that carry design decisions
and not the thirty-odd test modules, and demanding all of them would fail
legitimately the first time anyone adds a test file.
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MAP = os.path.join(ROOT, "CLAUDE.md")

#: Any `foo.py` or `dir/foo.py` inside backticks. The map writes every file
#: reference that way, and restricting to backticks is what keeps ordinary
#: prose about "the app.py routes" from being read as a path.
_PY_IN_BACKTICKS = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")


def _map_text():
    with open(MAP) as fh:
        return fh.read()


def _named_py_files():
    return sorted(set(_PY_IN_BACKTICKS.findall(_map_text())))


def _repo_modules():
    """Every module the map is expected to document, one bullet each.

    `scripts/` is walked **recursively**: it grew a subfolder in step S
    (`scripts/mutation/`), and a top-level-only listdir would have let three
    files slip out of the map's guarantee entirely -- silently, since the
    forward check can only report what the scan hands it.
    """
    out = [f for f in os.listdir(ROOT) if f.endswith(".py")]
    scripts = os.path.join(ROOT, "scripts")
    for dirpath, dirnames, filenames in os.walk(scripts):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py"):
                full = os.path.join(dirpath, f)
                out.append(os.path.relpath(full, ROOT))
    return sorted(out)


def _exists(named):
    """The map names files both by path (`tests/golden.py`) and by bare name
    inside the bullet for their directory (`conftest.py`), so a bare name is
    resolved against the four directories that have such a bullet."""
    candidates = [named] + [
        os.path.join(d, named)
        for d in ("tests", "scripts", "scripts/mutation", "docs/scoring")
    ]
    return any(os.path.exists(os.path.join(ROOT, c)) for c in candidates)


def test_every_module_in_the_repo_appears_in_the_codebase_map():
    # source: P3_refactor.md §4.3 -- "a test asserting that every module in
    # the repo appears in CLAUDE.md's map and every module the map names
    # exists. List against list, no numbers parsed out of prose."
    text = _map_text()
    missing = [m for m in _repo_modules() if f"`{m}`" not in text and f"`{os.path.basename(m)}`" not in text]
    assert missing == [], f"modules absent from CLAUDE.md's codebase map: {missing}"


def test_every_module_the_codebase_map_names_exists():
    # source: P3_refactor.md §4.3 -- the reverse half of the same check. A
    # map entry outliving its module is the drift this catches; §4.4's
    # deletion of all_candidate_groups is the same class one level down.
    gone = [named for named in _named_py_files() if not _exists(named)]
    assert gone == [], f"CLAUDE.md names files that do not exist: {gone}"


def test_the_scan_actually_sees_the_map_and_the_tree():
    # source: P2_tests.md §1 via P2 session 5's convention -- a scan whose
    # assertion is "this list is empty" is the shape likeliest to silently
    # stop testing anything, so both halves are checked to be looking at
    # something. The floors are far below the real counts (19 root modules,
    # 4 scripts, ~30 named files) so ordinary growth or pruning cannot trip
    # them; only a scan that has broken can.
    assert len(_repo_modules()) > 15
    assert len(_named_py_files()) > 15
    # The map must name the module this very test reads, or the regex has
    # stopped matching the way the map writes paths.
    assert "app.py" in _repo_modules()
    assert "app.py" in _named_py_files()
    # And the `scripts/` walk must actually descend: a module in a subfolder
    # is exactly what a top-level listdir would drop, and dropping it makes
    # the forward check pass vacuously rather than fail.
    assert "scripts/mutation/generate.py" in _repo_modules()
