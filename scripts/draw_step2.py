"""Draw step 2's moments: every pushback no run has triaged yet, one per session.

    .venv/bin/python scripts/draw_step2.py [--dry-run]

Writes runs/step2-later/moments.jsonl and runs/step2-first/moments.jsonl, and
prints what each rule removed. The rules, fixed on 09-24 before any of these
moments was read:

- later: each session's earliest later pushback with new agent work before it
  (`run.py moments --later`), with no per-repository cap;
- first: each session's first pushback. 600 of them were drawn into
  runs/sweep2 and never triaged, which is why "not triaged" and not "not
  drawn" is the test;
- both: no moment any run has triaged; no session of the first grid; no
  session with a task built in any run; no session without a timestamp (the
  build cannot choose its commit, and `find_moments` now leaves them out);
- first: no session whose later moment runs/later-sample or runs/later-cap20
  already carries;
- a session can be in both lists. If both its moments become admitted tasks,
  the first pushback is kept: it is the earlier moment, in the conversation
  with less friction.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import run as run_mod  # noqa: E402
from errata_bench.store import append, load  # noqa: E402

RUNS = ROOT / "runs"
GRID = RUNS / "phaseA-grid1-DeepSeek-V4-Pro" / "tasks.jsonl"
IN_FLIGHT = [RUNS / "later-sample", RUNS / "later-cap20"]


def collect(later: bool) -> list[dict]:
    out = Path(tempfile.mkdtemp()) / "moments.jsonl"
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        run_mod.find_moments(10**6, out, later=later, max_per_repo=0)
    print(" ".join(said.getvalue().split())[:200])
    return load(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)

    triaged = {(r.get("session_id"), r.get("turn_number"))
               for f in RUNS.glob("**/triaged.jsonl") for r in load(f)}
    grid = {r["session_id"] for r in load(GRID)}
    built = {r.get("session_id") for f in RUNS.glob("**/tasks.jsonl") for r in load(f)}
    in_flight = {r["session_id"] for d in IN_FLIGHT for r in load(d / "moments.jsonl")}

    lists = {}
    for name, later in (("step2-later", True), ("step2-first", False)):
        moments = collect(later)
        steps = [("not triaged by any run", lambda m: (m["session_id"], m["turn_number"]) not in triaged),
                 ("not a first-grid session", lambda m: m["session_id"] not in grid),
                 ("no task built from the session", lambda m: m["session_id"] not in built)]
        if not later:
            steps.append(("its later moment not already in a batch", lambda m: m["session_id"] not in in_flight))
        print(f"{name}: {len(moments)} moments")
        for why, keep in steps:
            before = len(moments)
            moments = [m for m in moments if keep(m)]
            print(f"  {why}: -{before - len(moments)} -> {len(moments)}")
        lists[name] = moments
    both = {m["session_id"] for m in lists["step2-later"]} & {m["session_id"] for m in lists["step2-first"]}
    print(f"sessions in both lists: {len(both)} (if both become tasks, the first pushback is kept)")
    if args.dry_run:
        return 0
    for name, moments in lists.items():
        target = RUNS / name / "moments.jsonl"
        if target.exists():
            print(f"refused: {target} exists")
            return 1
        target.parent.mkdir(parents=True)
        for m in moments:
            append(target, m)
        print(f"wrote {len(moments)} to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
