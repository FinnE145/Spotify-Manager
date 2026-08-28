"""Re-run a batch of kill proofs -- the master session's half of §6.

`verify.py kill` proves one mutant against one test. This runs a *list* of
them in parallel and reports which reproduced, which is what spec §7.1 step 4
asks the master session to do with every verdict an agent returns: "re-run
every returned kill proof itself. Cheap, mechanical, and the point of §6."

Two things this exists to stop being re-derived, both learned the hard way:

1. **The per-worker-directory race.** Jobs assigned round-robin and mapped
   across a pool of the same size do *not* stay one-per-directory, so two
   overlap in one copy and the restore writes a mutated file back as the
   original -- every later run in that worker is then red, which reads as a
   kill that isn't. `sweep.py` was fixed for this, `verify.py`'s crash pass
   repeated it, and the round-2 master driver repeated it a third time before
   it was caught pre-run. This module is the single home the third repeat
   argued for: one bucket per thread, assigned up front, never a pool.

2. **`--work` defaults to one shared path** (`$TMPDIR/symr-mutation`), so
   parallel callers silently corrupt each other. Every lane here gets its own.

Both failures are silent and both flatter the result, which is the standing
lesson about this tooling: a pleasing number is a prompt to check the
instrument.

Usage:
    recheck.py --jobs jobs.tsv [--workers 4] [--work DIR]

`jobs.tsv` is one job per line, tab-separated, `#` comments and blanks ignored:

    module<TAB>line<TAB>col<TAB>op<TAB>test     -- expect KILL PROOF: PASS
    module<TAB>line<TAB>col<TAB>op              -- expect the mutant to SURVIVE

The second form is for verdicts of `equivalent`, `cosmetic` and
`gap -- recorded, not fixed`: those claim *no* test kills the mutant, and a
claim that it still survives is just as checkable as a kill, so check it.

Exit status is 0 only when every job matched its expectation.
"""
import argparse
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweep  # noqa: E402

REPO = sweep.REPO
PY = sweep.PY
VERIFY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify.py")


def load_jobs(path):
    jobs = []
    for n, raw in enumerate(open(path), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) not in (4, 5):
            raise SystemExit(
                "%s:%d -- expected 4 or 5 tab-separated fields, got %d: %r"
                % (path, n, len(parts), line))
        mod, ln, col, op = parts[0], int(parts[1]), int(parts[2]), parts[3]
        jobs.append((mod, ln, col, op, parts[4] if len(parts) == 5 else None))
    return jobs


def run_one(work, job):
    mod, line, col, op, test = job
    cmd = [PY, VERIFY, "--work", work]
    cmd += (["kill"] if test else ["one"])
    cmd += ["--module", mod, "--line", str(line), "--col", str(col), "--op", op]
    if test:
        cmd += ["--test", test]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    out = p.stdout + p.stderr
    if test:
        ok = "KILL PROOF: PASS" in out
    else:
        ok = "SURVIVED" in out
    return ok, "%s:%d col%d [%s]" % (mod, line, col, op), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--work", default=os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "symr-recheck"))
    args = ap.parse_args()

    jobs = load_jobs(args.jobs)
    kills = sum(1 for j in jobs if j[4])
    print("%d jobs (%d kill proofs, %d survive-checks), %d workers\n"
          % (len(jobs), kills, len(jobs) - kills, args.workers), flush=True)

    # One bucket per thread, decided up front. See this module's docstring --
    # a pool with `idx % workers` does NOT keep a job on its own directory.
    buckets = [[] for _ in range(args.workers)]
    for i, j in enumerate(jobs):
        buckets[i % args.workers].append(j)

    results, lock = [], threading.Lock()

    def run_bucket(wid):
        work = os.path.join(args.work, "w%d" % wid)
        for job in buckets[wid]:
            ok, label, out = run_one(work, job)
            expected = "PASS" if job[4] else "SURVIVED"
            with lock:
                results.append((ok, label, out))
                print("%-9s %s" % (expected if ok else "MISMATCH", label),
                      flush=True)

    threads = [threading.Thread(target=run_bucket, args=(w,))
               for w in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    bad = [(l, o) for ok, l, o in results if not ok]
    print("\n=== %d/%d reproduced as claimed ==="
          % (len(results) - len(bad), len(results)))
    for label, out in bad:
        print("\n--- MISMATCH: %s ---" % label)
        print("\n".join(out.strip().splitlines()[-12:]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
