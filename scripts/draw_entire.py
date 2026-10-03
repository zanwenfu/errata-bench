#!/usr/bin/env python3
"""Draw the Entire corpus's moments for a run, by v1's rules (`scripts/draw_step2.py`, 09-24), fixed before any is read.

    ERRATA_CORPUS=data/entire/corpus python scripts/draw_entire.py <name> --cap N [--limit N]
        [--exclude-sessions FILE...] [--dry-run]

Writes runs/<name>-first/moments.jsonl and runs/<name>-later/moments.jsonl and prints what each rule removed. No
model calls. The rules, as v1's step 2 drew (#16, agreed with #17, 10-03):

- later: each session's earliest later pushback with new agent work before it (`run.py moments --later`); first:
  each session's first pushback. The corpus's own rules apply to both (`find_moments`): a pushback label, three agent
  turns before it, a sandboxed language, a timestamp, a transcript, one copy of a moment, no session holding an
  abandoned branch, no shell command.
- both: no moment any run has triaged; no session with a task built in any run; none of v1's 55 sessions
  (`release.v1_names`); none named in --exclude-sessions, a JSON list or object of session ids, for the sessions
  with a task built from the other corpus -- SWE-chat's sessions are Entire sessions, and one task per session holds
  across both.
- first: no session whose later moment a batch already holds (the later-pushback runs under runs/, named below).
- a session can be in both lists. If both moments become admitted tasks, the first pushback is kept (the tally),
  and the first list's locate finishes before the later list's, so a failed answer the two share is the first's.
- --cap: the most moments from one repository in each list, 0 for none. Required: v1 drew with 20
  (later-cap20) and with none (step 2), and the choice is said, not defaulted.
- --limit: how many of each list to keep, in `find_moments`' order (a pilot); every moment otherwise.
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
from errata_bench.release.v1_names import V1_NAMES  # noqa: E402
from errata_bench.store import append, load  # noqa: E402

RUNS = ROOT / "runs"


def collect(later: bool, cap: int) -> list[dict]:
    """Every moment `find_moments` gives, in its order, and what it said it left out."""
    out = Path(tempfile.mkdtemp()) / "moments.jsonl"
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        run_mod.find_moments(10**6, out, later=later, max_per_repo=cap)
    for line in said.getvalue().splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    return load(out)


def sessions_in(files: list[Path]) -> set[str]:
    """The session ids in each file: a JSON list of ids, or an object keyed by them."""
    found: set[str] = set()
    for f in files:
        data = json.loads(f.read_text())
        found |= set(data if isinstance(data, list) else data.keys())
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="the runs are runs/<name>-first and runs/<name>-later")
    ap.add_argument("--cap", type=int, required=True, help="most moments from one repository in each list; 0 for none")
    ap.add_argument("--limit", type=int, default=0, help="keep this many of each list, in order; 0 for all")
    ap.add_argument("--exclude-sessions", type=Path, nargs="*", default=[],
                    help="JSON files of session ids holding a task built from the other corpus")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)
    if args.cap < 0 or args.limit < 0:
        ap.error("--cap and --limit are counts")

    # As v1's draw read them: every folder under runs/, backups included.
    triaged = {(r.get("session_id"), r.get("turn_number"))
               for f in RUNS.glob("**/triaged.jsonl") for r in load(f) if isinstance(r, dict)}
    built = {r.get("session_id") for f in RUNS.glob("**/tasks.jsonl") for r in load(f) if isinstance(r, dict)}
    v1 = set(V1_NAMES.values())
    elsewhere = sessions_in(args.exclude_sessions)
    later_runs = sorted(d for d in RUNS.glob("*later*") if (d / "moments.jsonl").exists() and ".pre-" not in d.name)
    in_flight = {r["session_id"] for d in later_runs for r in load(d / "moments.jsonl") if isinstance(r, dict)}
    print(f"read: {len(triaged)} triaged moments; {len(built)} sessions with a task built here; {len(v1)} of v1's; "
          f"{len(elsewhere)} from {len(args.exclude_sessions)} exclusion file(s); later batches "
          f"{[d.name for d in later_runs]} holding {len(in_flight)} sessions")

    lists: dict[str, list[dict]] = {}
    for name, later in ((f"{args.name}-later", True), (f"{args.name}-first", False)):
        print(f"{name}:")
        moments = collect(later, args.cap)
        steps = [("not triaged by any run", lambda m: (m["session_id"], m["turn_number"]) not in triaged),
                 ("no task built from the session in any run", lambda m: m["session_id"] not in built),
                 ("not one of v1's sessions", lambda m: m["session_id"] not in v1),
                 ("no task built from the session in the other corpus", lambda m: m["session_id"] not in elsewhere)]
        if not later:
            steps.append(("its later moment not already in a batch", lambda m: m["session_id"] not in in_flight))
        print(f"  {len(moments)} moments")
        for why, keep in steps:
            before = len(moments)
            moments = [m for m in moments if keep(m)]
            print(f"  {why}: -{before - len(moments)} -> {len(moments)}")
        if args.limit:
            moments = moments[:args.limit]
            print(f"  the first {args.limit} kept: {len(moments)}")
        lists[name] = moments
    both = ({m["session_id"] for m in lists[f"{args.name}-later"]}
            & {m["session_id"] for m in lists[f"{args.name}-first"]})
    print(f"sessions in both lists: {len(both)} (if both become tasks, the first pushback is kept)")
    if args.dry_run:
        return 0
    for name in lists:
        if (RUNS / name / "moments.jsonl").exists():
            print(f"refused: {RUNS / name / 'moments.jsonl'} exists; nothing was written")
            return 1
    for name, moments in lists.items():
        target = RUNS / name / "moments.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        for m in moments:
            append(target, m)
        print(f"wrote {len(moments)} to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
