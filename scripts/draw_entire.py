#!/usr/bin/env python3
"""Draw the Entire corpus's moments for a run, by v1's rules (`scripts/draw_step2.py`, 09-24), fixed before any is read.

    ERRATA_CORPUS=data/entire/corpus python scripts/draw_entire.py <name> --cap N [--limit N]
        --exclude-sessions FILE... [--runs DIR...] [--dry-run]

Writes runs/<name>-first/moments.jsonl and runs/<name>-later/moments.jsonl and prints what each rule removed. No
model calls. The rules, as v1's step 2 drew (#16, agreed with #17, 10-03), read over every run under --runs (this
checkout's runs/ by default; v1's draw read its whole runs/, so name the other corpus's runs too):

- later: each session's earliest later pushback with new agent work before it (`run.py moments --later`); first:
  each session's first pushback. The corpus's own rules apply to both (`find_moments`): a pushback label, three agent
  turns before it, a sandboxed language, a timestamp, a transcript, one copy of a moment, no session holding an
  abandoned branch, no shell command.
- both: no moment any run has triaged, nor a copy of one in another session (G-91); no session with a task built
  in any run; none of v1's 55 sessions (`release.v1_names`); none named in --exclude-sessions, JSON lists of the
  session ids with a task built from the other corpus -- SWE-chat's sessions are Entire sessions, and one task per
  session holds across both. Refused where no run has triaged anything: that is not the checkout the runs are in.
- first: no session whose later moment a batch already holds, and no copy of a moment the later list holds. A
  batch is any run whose moments are later pushbacks (`"later": true`, as v1's later-sample and later-cap20 and
  every Entire later run are), backups included, this draw's own later list not: v1 named its two batches, and a
  name pattern missed v1's while it took a first list whose name held "later" (review, 10-03).
- a session can be in both lists. If both moments become admitted tasks, the first pushback is kept (the tally),
  and the first list's locate finishes before the later list's, so a failed answer the two share is the first's.
- --cap: the most moments from one repository in each list, 0 for none, counted after the rules above. Required:
  v1 drew with 20 (later-cap20) and with none (step 2), and the choice is said, not defaulted.
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


def collect(later: bool, seen: list[Path]) -> list[dict]:
    """Every moment `find_moments` gives, uncapped, and what it said it left out.

    ``seen`` are files of moments already taken -- every run's triage, and for
    the first list the later list -- handed to `find_moments` so that a copy
    of one in another session (G-91) is left out with it. Filtered afterwards
    instead, a moment one run triaged under one session came back under its
    copy's, and a moment that is one session's first pushback and its copy's
    later one was drawn into both lists (review, 10-03).
    """
    out = Path(tempfile.mkdtemp()) / "moments.jsonl"
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        run_mod.find_moments(10**6, out, skip_seen=seen, later=later, max_per_repo=0)
    for line in said.getvalue().splitlines():
        if line.strip():
            print(f"  {line.strip()}")
    return load(out)


def capped(moments: list[dict], cap: int) -> list[dict]:
    """The moments as `find_moments` spreads them -- within a repository by session id, then one repository at a
    time in name order -- with at most ``cap`` from one repository (0 for none).

    Applied after the rules, not by `find_moments` before them: there a moment some run had already triaged took a
    place under the cap and was then removed, and the 20 a repository could give fell to what was left (a dry run
    on 10-03 drew 814 later moments and kept 479). v1's step 2 drew with no cap, where the order makes no difference.
    """
    by_repo: dict[str, list[dict]] = {}
    for m in moments:
        by_repo.setdefault(m["repo_id"], []).append(m)
    for ms in by_repo.values():
        ms.sort(key=lambda m: m["session_id"])
    if cap:
        by_repo = {r: ms[:cap] for r, ms in by_repo.items()}
    spread: list[dict] = []
    while by_repo:
        for repo in sorted(by_repo):
            spread.append(by_repo[repo].pop(0))
        by_repo = {r: ms for r, ms in by_repo.items() if ms}
    return spread


def sessions_in(files: list[Path]) -> set[str]:
    """The session ids in each file, a JSON list of strings. Anything else is refused: an object's keys might be
    anything, a tally's among them, and would be excluded without a word (review, 10-03)."""
    found: set[str] = set()
    for f in files:
        data = json.loads(f.read_text())
        if not isinstance(data, list) or not all(isinstance(x, str) and x for x in data):
            raise SystemExit(f"refused: {f} is not a JSON list of session ids")
        found |= set(data)
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="the runs are runs/<name>-first and runs/<name>-later")
    ap.add_argument("--cap", type=int, required=True, help="most moments from one repository in each list; 0 for none")
    ap.add_argument("--limit", type=int, default=0, help="keep this many of each list, in order; 0 for all")
    ap.add_argument("--exclude-sessions", type=Path, nargs="+", required=True,
                    help="JSON lists of the session ids holding a task built from the other corpus")
    ap.add_argument("--runs", type=Path, nargs="+", default=[RUNS],
                    help="the run folders whose triage, built tasks and later batches count; default this checkout's")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = ap.parse_args(argv)
    if args.cap < 0 or args.limit < 0:
        ap.error("--cap and --limit are counts")
    roots = [r.resolve() for r in args.runs]
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        print(f"refused: no run folder at {missing}")
        return 2

    # As v1's draw read them: every folder, backups included.
    triaged_files = sorted(f for r in roots for f in r.glob("**/triaged.jsonl"))
    if not triaged_files:
        print(f"refused: no run under {[str(r) for r in roots]} has triaged anything; these are not the run folders")
        return 2
    triaged = {(r.get("session_id"), r.get("turn_number"))
               for f in triaged_files for r in load(f) if isinstance(r, dict)}
    built = {r.get("session_id") for root in roots for f in root.glob("**/tasks.jsonl")
             for r in load(f) if isinstance(r, dict)}
    v1 = set(V1_NAMES.values())
    elsewhere = sessions_in(args.exclude_sessions)
    own = (RUNS / f"{args.name}-later").resolve()
    batches: dict[Path, set[str]] = {}
    for root in roots:
        for f in sorted(root.glob("*/moments.jsonl")):
            if f.parent.resolve() != own:
                held = {r["session_id"] for r in load(f) if isinstance(r, dict) and r.get("later")}
                if held:
                    batches[f.parent] = held
    in_flight = set().union(*batches.values())
    print(f"read: {len(triaged)} triaged moments from {len(triaged_files)} runs; {len(built)} sessions with a task "
          f"built; {len(v1)} of v1's; {len(elsewhere)} from {len(args.exclude_sessions)} exclusion file(s); later "
          f"batches {sorted(d.name for d in batches)} holding {len(in_flight)} sessions")

    lists: dict[str, list[dict]] = {}
    for name, later in ((f"{args.name}-later", True), (f"{args.name}-first", False)):
        print(f"{name}:")
        seen = list(triaged_files)
        if not later:
            # The later list as drawn, so a first pushback that is a copy of one
            # of its moments in another session is left out (G-91). The later
            # copy is the one kept: a session whose first pushback is another's
            # later one began after that one's first, and holds less of it.
            drawn = Path(tempfile.mkdtemp()) / "later.jsonl"
            for m in lists[f"{args.name}-later"]:
                append(drawn, m)
            seen.append(drawn)
        moments = collect(later, seen)
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
        before = len(moments)
        moments = capped(moments, args.cap)
        if args.cap:
            print(f"  at most {args.cap} from one repository: -{before - len(moments)} -> {len(moments)}")
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
        target.touch()      # an empty list is written too, so the run and a later draw see it was drawn
        for m in moments:
            append(target, m)
        print(f"wrote {len(moments)} to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
