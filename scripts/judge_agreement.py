"""Agreement between two judges, answer by answer (D-35, "also reported, not tested").

    .venv/bin/python scripts/judge_agreement.py --judge claude-opus-5 \\
        runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code runs/grid1-DeepSeek-V4-Pro

D-35 compares the benchmark judge and the second judge on three questions, answer
by answer: the trace check's (a claim the record does not support), the judge's
`makes_unverified_claim`, and the clean pass line -- the three endpoints, as
d35.py defines them. Each is asked over the answers both judges could score, taken
from the rows d35.py gives every D-35 script, so the admission and the exclusions
are the ones the results use. Printed for each: Cohen's kappa, raw agreement and
n, with a 95% interval from resampling tasks, because the answers to one task are
not independent -- and a task is one cluster across candidates, since the three
candidates answered the same tasks. Per candidate, and pooled.

Also printed, and not part of D-35: each judge against itself, reading against
reading, over every answer it read more than once. Agreement between two judges
means little without it. DeepSeek-V4-Pro, grading the same answers twice,
disagreed with itself nearly as often as with the judge it was compared to.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d35  # noqa: E402
from annotation_agreement import kappa  # noqa: E402
from errata_bench.score.rejudge import judge_paths  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402


def _label(has, asked):
    return lambda a: bool(has(a)) if asked(a) else None


# (name, the answer's label: True/False, or None where the question could not be asked)
QUESTIONS = [(name, _label(has, asked)) for name, has, asked in d35.ENDPOINTS]

Pairs = dict[str, dict[str, list[tuple[bool, bool]]]]   # question -> task -> pairs


def between(run: Path, judge: str, runs: set[int] | None, first_judge: str | None = None) -> Pairs:
    """(first judge, second judge) on every answer both could score, by task.

    The first judge is the run's own grading unless ``first_judge`` names a
    re-grade. D-36's criterion 2 compares two re-grades under the same trace
    rules; against the run's own grading, the first grid's second-rules
    re-grades would be compared with readings taken under the first rules.
    """
    first = {(a["task_id"], a["run"]): a for a in d35.readings(run, first_judge, runs)}
    second = {(a["task_id"], a["run"]): a for a in d35.readings(run, judge, runs)}
    out: Pairs = {name: defaultdict(list) for name, _ in QUESTIONS}
    for key in sorted(set(first) & set(second), key=str):
        for name, label in QUESTIONS:
            x, y = label(first[key]), label(second[key])
            if x is not None and y is not None:
                out[name][key[0]].append((x, y))
    return out


def within(run: Path, judge: str | None, runs: set[int] | None) -> Pairs:
    """Every pair of one judge's readings of one answer, over the answers it scored."""
    source = judge_paths(run, judge).attempts if judge else Paths(run).attempts
    counted = {(a["task_id"], a["run"]) for a in d35.readings(run, judge, runs)}
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in load(source):
        if not r.get("error") and (r.get("task_id"), r.get("run")) in counted:
            by[(r["task_id"], r["run"])].append(r)
    out: Pairs = {name: defaultdict(list) for name, _ in QUESTIONS}
    for key, rs in by.items():
        rs.sort(key=lambda r: r.get("pass", 0))
        for name, label in QUESTIONS:
            for r1, r2 in combinations(rs, 2):
                x, y = label(r1), label(r2)
                if x is not None and y is not None:
                    out[name][key[0]].append((x, y))
    return out


def merged(parts: list[dict[str, list]]) -> dict[str, list]:
    """Pool several candidates' pairs, one cluster per task across all of them."""
    out: dict[str, list] = defaultdict(list)
    for part in parts:
        for task, pairs in part.items():
            out[task] += pairs
    return out


def kappa_ci(by_task: dict[str, list], resamples: int, seed: int) -> tuple[float, float]:
    """Percentile 95% interval for kappa, resampling tasks with replacement."""
    tasks = sorted(by_task)
    if not tasks:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    ks = []
    for _ in range(resamples):
        k = kappa([p for _ in tasks for p in by_task[tasks[rng.randrange(len(tasks))]]])
        if k == k:
            ks.append(k)
    if not ks:
        return (float("nan"), float("nan"))
    ks.sort()
    return ks[int(0.025 * len(ks))], ks[min(len(ks) - 1, int(0.975 * len(ks)))]


def line(label: str, by_task: dict[str, list], resamples: int, seed: int) -> str:
    pairs = [p for ps in by_task.values() for p in ps]
    if not pairs:
        return f"    {label:28s} no answers to compare"
    agree = sum(a == b for a, b in pairs) / len(pairs)
    lo, hi = kappa_ci(by_task, resamples, seed)
    return (f"    {label:28s} n={len(pairs):4d} over {len(by_task):2d} tasks  agreement {agree:.2f}  "
            f"kappa {kappa(pairs):+.2f} [{lo:+.2f}, {hi:+.2f}]")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--judge", required=True, help="the second judge, whose re-grades are under <run>/rejudge/")
    ap.add_argument("--first-judge", help="the first judge's re-grade under <run>/rejudge/, "
                                          "instead of the run's own grading")
    ap.add_argument("--runs", dest="attempts", help="restrict to these attempt numbers, e.g. 0 or 0,1,2")
    ap.add_argument("--tasks", type=Path, help="only these tasks: a JSON list of task ids (D-40's task sets)")
    ap.add_argument("--resamples", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    d35.require_runs(ap, args.runs)
    d35.restrict(args.tasks)
    if args.tasks:
        print(f"tasks restricted to {args.tasks} ({len(d35.ONLY)} ids)")
    if args.first_judge and args.first_judge == args.judge:
        ap.error("--first-judge and --judge name the same judge; that is its self-agreement, "
                 "which this prints anyway")
    which = {int(x) for x in args.attempts.split(",")} if args.attempts else None

    first_names = Counter(r.get("judge_model") for run in args.runs
                          for r in d35.readings(run, args.first_judge, which))
    first = ", ".join(str(m) for m in first_names) or "(none)"
    whose = (f"its re-grade under rejudge/{args.first_judge}" if args.first_judge
             else "each run directory own grading")
    print(f"judge agreement: {first} ({whose}) against {args.judge}; "
          f"attempts: {sorted(which) if which is not None else 'all'}\n")

    pairs = {run: between(run, args.judge, which, args.first_judge) for run in args.runs}
    selfs = {j: {run: within(run, args.first_judge if j == "first" else args.judge, which)
                 for run in args.runs}
             for j in ("first", "second")}
    for name, _ in QUESTIONS:
        print(name)
        print("  between the judges")
        for run in args.runs:
            print(line(run.name, pairs[run][name], args.resamples, args.seed))
        if len(args.runs) > 1:
            print(line("pooled", merged([pairs[run][name] for run in args.runs]), args.resamples, args.seed))
        print("  each judge against itself, reading against reading (not in D-35)")
        for j, label in (("first", first), ("second", args.judge)):
            print(line(label, merged([selfs[j][run][name] for run in args.runs]), args.resamples, args.seed))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
