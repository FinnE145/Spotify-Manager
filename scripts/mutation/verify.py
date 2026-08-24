"""Kill proofs and the crash-verification pass (mutation-sweep-S.md §6, §3.3).

`kill` is the gate that makes §7's delegation safe. A triage verdict is
otherwise a judgement to be trusted; with it, "this test kills that mutant" is
a re-runnable fact, so a cold agent's output can be *checked* rather than
believed. It also directly blocks the defect P found in every session -- a test
that passes, cites a real clause and could never fail -- because a test that
cannot fail cannot kill anything.

Usage:
    verify.py kill  --module entities.py --line 88 --op eq --test tests/test_x.py::test_y
    verify.py one   --module entities.py --line 88 --op eq
    verify.py caught --results DIR/sweep_results.json
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
import sweep  # noqa: E402

REPO = sweep.REPO
PY = sweep.PY


def _copy(work, name):
    d = os.path.join(work, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(REPO, d, ignore=sweep.IGNORE, symlinks=True)
    return d


def find_mutant(worker_dir, module, line, op, col=None):
    """The one mutant at (line, op) -- refusing rather than guessing if the
    line carries more than one, since picking the wrong one would prove
    nothing about the survivor being triaged."""
    _, _, ms = generate.generate(os.path.join(worker_dir, module))
    hits = [m for m in ms if m["line"] == line and m["op"] == op
            and (col is None or m["col"] == col)]
    if not hits:
        raise SystemExit(f"no {op} mutant at {module}:{line}")
    if len(hits) > 1:
        cols = [m["col"] for m in hits]
        raise SystemExit(f"{len(hits)} {op} mutants at {module}:{line}, "
                         f"cols {cols} -- pass --col")
    return hits[0]


def _run_suite(cwd, extra=()):
    p = subprocess.run(
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:randomly",
         "--tb=no", "-rf", *extra],
        cwd=cwd, capture_output=True, text=True, timeout=1800,
        env=sweep._child_env(),
    )
    return p.returncode, p.stdout + p.stderr


def _failed(out):
    return {ln.split()[1] for ln in out.splitlines()
            if ln.startswith(("FAILED ", "ERROR ")) and len(ln.split()) > 1}


def cmd_kill(args):
    """§6: the suite green with the test and no mutant, then the mutant
    applied with that test failing -- by name, not 'the suite fails'."""
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    d = _copy(work, "verify")
    m = find_mutant(d, args.module, args.line, args.op, args.col)
    print(f"mutant  {args.module}:{args.line} [{args.op}]")
    print(f"  before  {m['before']}")
    print(f"  after   {m['after']}")

    rc, out = _run_suite(d)
    clean_green = rc == 0
    print(f"\n1. suite without the mutant: {'GREEN' if clean_green else 'RED'}")
    if not clean_green:
        print(f"   failures: {sorted(_failed(out))}")

    target = os.path.join(d, args.module)
    original = open(target).read()
    lines = original.splitlines(keepends=True)
    lines[m["line"] - 1] = m["new_line"]
    open(target, "w").write("".join(lines))
    try:
        rc, out = _run_suite(d)
    finally:
        open(target, "w").write(original)
    failed = _failed(out)
    killed = any(f == args.test or f.endswith(args.test) for f in failed)
    print(f"2. suite with the mutant: {len(failed)} failing")
    for f in sorted(failed):
        print(f"   {'>>' if (f == args.test or f.endswith(args.test)) else '  '} {f}")

    ok = clean_green and killed
    print(f"\nKILL PROOF: {'PASS' if ok else 'FAIL'} -- {args.test}")
    if not killed:
        print("   that test did not fail under the mutant; it does not kill it.")
    return 0 if ok else 1


def cmd_one(args):
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    d = _copy(work, "verify")
    m = find_mutant(d, args.module, args.line, args.op, args.col)
    status, rc = sweep.run_mutant(d, args.module, m)
    print(f"{args.module}:{args.line} [{args.op}] -> {status} (rc={rc})")
    print(f"  before  {m['before']}")
    print(f"  after   {m['after']}")
    return 0


def cmd_caught(args):
    """§3.3 -- re-run every mutant the sweep called `caught`.

    A signal-killed child returns a negative return code, and a naive
    `if rc:` reads that as "caught". The bounded run took a SIGSEGV mid-run and
    only discovered the misclassification afterwards; this closes the sweep by
    confirming the totals.
    """
    prior = json.load(open(args.results))
    want = [r for r in prior if r["status"] == "caught"]
    work = os.path.dirname(os.path.abspath(args.results))
    print(f"re-running {len(want)} previously-'caught' mutants, "
          f"{args.workers} workers", flush=True)

    by_module = {}
    for r in want:
        by_module.setdefault(r["file"], None)
    for path in by_module:
        by_module[path] = {
            (m["line"], m["op"], m["col"]): m
            for m in generate.generate(os.path.join(work, "proto", path))[2]
        }

    jobs = []
    for r in want:
        m = by_module[r["file"]].get((r["line"], r["op"], r["col"]))
        if m is not None:
            jobs.append((len(jobs) % args.workers, r["file"], m))

    def run_one(job):
        wid, path, m = job
        status, rc = sweep.run_mutant(os.path.join(work, f"w{wid}"), path, m)
        return {"file": path, "line": m["line"], "op": m["op"],
                "status": status, "rc": rc, "before": m["before"]}

    out, anomalies = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(run_one, jobs):
            out.append(r)
            if r["status"] != "caught":
                anomalies += 1
                print(f"  !! {r['status']} {r['file']}:{r['line']} "
                      f"[{r['op']}] {r['before'][:70]}", flush=True)
    dest = os.path.join(work, "verify_caught.json")
    json.dump(out, open(dest, "w"), indent=1)
    print(f"\n{Counter(r['status'] for r in out)}")
    print(f"{len(out)}/{len(want)} re-run, {anomalies} anomalies -> {dest}")
    return 1 if anomalies else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "symr-mutation"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("kill", "one"):
        s = sub.add_parser(name)
        s.add_argument("--module", required=True)
        s.add_argument("--line", type=int, required=True)
        s.add_argument("--op", required=True)
        s.add_argument("--col", type=int, default=None)
        if name == "kill":
            s.add_argument("--test", required=True)
    s = sub.add_parser("caught")
    s.add_argument("--results", required=True)
    s.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    return {"kill": cmd_kill, "one": cmd_one, "caught": cmd_caught}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
