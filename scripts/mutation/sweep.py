"""Run every generated mutant against the full suite (mutation-sweep-S.md §3).

Each mutant is applied in one of N isolated worker copies of the repo, then the
**full suite** runs in that copy -- not the module's own test file, because a
mutant in one module is routinely killed by a test in another, and restricting
the run would report gaps that are not there.

Usage:
    venv/bin/python scripts/mutation/sweep.py [--work DIR] [--workers N] [module.py ...]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, "venv/bin/python")

#: §1's scope: every module the bounded run did not cover. `scoring.py`,
#: `canonical.py`, `snapshot.py` and `roundtrip.py` are done (post_P_sweep.md);
#: `scripts/`, `tests/`, templates and JS are out of scope.
TARGETS = [
    "app.py", "entities.py", "canonical_detect.py", "history_import.py",
    "db.py", "backfill.py", "generations.py", "jobs.py", "artists.py",
    "canonical_autogroup.py", "grouping.py", "api_log.py", "spotify_client.py",
    "config.py", "normalize.py", "serve.py",
]

#: §3.1 -- copying the 93 MB `symr.db` into six workers is pure cost; the suite
#: could not open it anyway (conftest.py's connect guard).
#: `worktrees` is not in §3.1's list because the repo had none when it was
#: written; it now holds entire nested checkouts, which is the same pure cost
#: the rest of this list exists to avoid, six times over.
IGNORE = shutil.ignore_patterns(
    ".git", "venv", "data", "__pycache__", "*.pyc", "*.db", ".coverage*",
    "worktrees",
)

TIMEOUT = 300


def _child_env():
    """§3.4.1 -- the `.pyc` trap.

    post_P_sweep.md §1.1: restoring a file inside the same second as the
    mutated write, with the same byte count, leaves a `.pyc` the interpreter
    still considers valid, so a clean-looking source tree executes mutated
    bytecode -- undetectable by grep, git diff or git status. Never writing one
    is the fix; restore-by-write and os.utime below are the belt.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _has_assertion_failure(out):
    """Did a test fail on an assertion, as opposed to erroring out?"""
    if "AssertionError" in out:
        return True
    return any(
        line.lstrip().startswith("E ") and "assert" in line
        for line in out.splitlines()
    )


def classify(rc, out):
    """§3.2's table.

    `broken` is where the SQL pass needs care: an invalid query fails every
    test that touches it, which reads as a kill and would silently inflate the
    rate with meaningless catches. That is the same ruling the Python pass
    already makes for SyntaxError, applied to the new operator set.
    """
    if rc is None:
        return "timeout"
    if rc < 0:                                    # §3.3 -- killed by a signal
        return "crashed"
    if rc == 0:
        return "SURVIVED"
    if "SyntaxError" in out or "IndentationError" in out:
        return "broken"
    if "OperationalError" in out and not _has_assertion_failure(out):
        return "broken"
    return "caught"


def build_workers(work, n):
    proto = os.path.join(work, "proto")
    if os.path.exists(proto):
        shutil.rmtree(proto)
    print(f"copying repo -> {proto}", flush=True)
    shutil.copytree(REPO, proto, ignore=IGNORE, symlinks=True)
    for i in range(n):
        d = os.path.join(work, f"w{i}")
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(proto, d, symlinks=True)
    return proto


def run_mutant(worker_dir, path, mutant, timeout=TIMEOUT, attempts=2):
    """Apply, run the suite, restore. Returns (status, rc)."""
    target = os.path.join(worker_dir, path)
    with open(target) as fh:
        original = fh.read()
    stat = os.stat(target)
    lines = original.splitlines(keepends=True)
    lines[mutant["line"] - 1] = mutant["new_line"]
    with open(target, "w") as fh:
        fh.write("".join(lines))
    try:
        rc, out = None, ""
        for _ in range(attempts):
            try:
                p = subprocess.run(
                    [PY, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:randomly"],
                    cwd=worker_dir, capture_output=True, text=True,
                    timeout=timeout, env=_child_env(),
                )
            except subprocess.TimeoutExpired:
                rc, out = None, ""
                break
            rc, out = p.returncode, p.stdout + p.stderr
            if rc >= 0:
                break                             # clean exit; no signal
        return classify(rc, out), rc
    finally:
        with open(target, "w") as fh:             # restore by writing
            fh.write(original)
        os.utime(target, (stat.st_atime, stat.st_mtime))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "symr-mutation"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("modules", nargs="*", default=None)
    args = ap.parse_args()

    targets = args.modules or TARGETS
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    proto = build_workers(work, args.workers)

    jobs = []
    for path in targets:
        _, _, ms = generate.generate(os.path.join(proto, path))
        for m in ms:
            jobs.append((len(jobs) % args.workers, path, m))
    print(f"{len(jobs)} mutants across {len(targets)} modules, "
          f"{args.workers} workers", flush=True)

    def run_one(job):
        wid, path, m = job
        status, rc = run_mutant(os.path.join(work, f"w{wid}"), path, m)
        return {"file": path, "status": status, "rc": rc,
                **{k: m[k] for k in ("op", "pass", "line", "col", "before", "after")}}

    results, done = [], 0
    out_path = os.path.join(work, "sweep_results.json")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(run_one, jobs):
            results.append(r)
            done += 1
            if r["status"] in ("SURVIVED", "crashed"):
                print(f"  {r['status']} {r['file']}:{r['line']} "
                      f"[{r['op']}]  {r['before']}", flush=True)
            if done % 50 == 0:
                print(f"  ... {done}/{len(jobs)}", flush=True)
                json.dump(results, open(out_path, "w"), indent=1)
    json.dump(results, open(out_path, "w"), indent=1)

    print("\n==== PER MODULE ====")
    for path in targets:
        c = Counter(r["status"] for r in results if r["file"] == path)
        scored = c["caught"] + c["timeout"] + c["SURVIVED"]
        rate = f"{100 * (c['caught'] + c['timeout']) / scored:.1f}%" if scored else "n/a"
        print(f"{path:24s} {rate:>6s}  {dict(c)}")
    print("\n==== TOTALS ====", Counter(r["status"] for r in results))
    print(f"results -> {out_path}")


if __name__ == "__main__":
    main()
