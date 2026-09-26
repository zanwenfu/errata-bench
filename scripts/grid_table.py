"""The results table for a grid of candidate run directories.

    .venv/bin/python scripts/grid_table.py runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code ...

Every number comes from the harness's own functions -- `settled` folds the
repeated readings conservatively, `_passed` prices the pass line, admission is
calibration plus controls as the run directory holds them -- and the rows and
endpoints are chosen by d35.py, the one place every D-35 script takes them from,
so the table cannot use a rule the pipeline or the tests do not. The gate is not
re-read here: the grid directories hold only tasks that already held it 7 of 7
(R-33). Rates carry Wilson 95% intervals and their denominators. Attempts on one
task are correlated, so with more than one attempt per task the interval here is
optimistic; the per-task columns are the ones to compare models on.

Every endpoint is also given by task kind, as D-35 asks ("also reported, not
tested"). `--judge NAME` reads a second judge's re-grades; `--admit-also NAME`
keeps only the tasks that judge also admits on its own tests.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d35  # noqa: E402
from errata_bench.project import code_version  # noqa: E402
from errata_bench.score.judge import PASSING, PASSING_WITH_HEDGE, outcome_of  # noqa: E402
from errata_bench.score.rejudge import _passed  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402


def wilson(k: int, n: int) -> str:
    if not n:
        return "  -  "
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{k}/{n} = {100 * p:.0f}% [{100 * max(0, c - h):.0f}-{100 * min(1, c + h):.0f}]"


def by_kind(rows: list[dict], kinds: dict[str, str], has, asked) -> dict[str, str]:
    """k/n per task kind, over the answers where the question could be asked."""
    per = defaultdict(lambda: [0, 0])
    for a in rows:
        if asked(a):
            k = kinds.get(a["task_id"], "?")
            per[k][1] += 1
            per[k][0] += bool(has(a))
    return {k: f"{v[0]}/{v[1]}" for k, v in sorted(per.items())}


def one(run: Path, judge: str | None = None, runs: set[int] | None = None,
        also: str | None = None, keep_quotes: bool = False) -> dict:
    """One candidate's table. Admission is always the run directory's own, so a
    second judge's grades are read over the same task set as the first's."""
    paths = Paths(run)
    tasks = {t.task_id: t for t in read(paths.tasks)}
    admitted = d35.admission(run, also)
    answers = [a for a in load(paths.answers) if not a.get("error")]
    graded = d35.readings(run, judge, runs, also, scoreable_only=False)
    if runs is not None:
        answers = [a for a in answers if a.get("run") in runs]
    counted = [a for a in graded if a.get("scoreable") or (keep_quotes and d35.quote_only(a))]
    (_, lies, lie_asked), (_, claims, claim_asked), (_, clean, _) = d35.ENDPOINTS
    # Under the trace rules each row was read by (D-35's `claims_match_trace`,
    # D-36's `misreported`): read by the first field only, a re-grade under the
    # second printed 0/0 for the primary endpoint.
    asked = [a for a in counted if lie_asked(a)]
    claimed = [a for a in counted if (a.get("judgement") or {}).get("makes_unverified_claim") is not None]
    models = Counter(a.get("model") for a in answers)
    judges = Counter(a.get("judge_model") for a in d35.rows_of(run, judge) if not a.get("error")
                     and (runs is None or a.get("run") in runs))
    # An answer row's `calls` is not a count of calls: since D-44 it names how
    # the attempt's calls were recorded ("calls": 2, now 3), and it is set on
    # every row, so read as tool use it counted every answer as using a tool
    # (B-268). Only a graded row's `calls` counts calls; the fallback below
    # reads that.
    used = {(a.get("task_id"), a.get("run")): bool(a.get("tool_calls")) for a in answers}
    kinds = {tid: t.kind for tid, t in tasks.items()}
    return {
        "run": run.name,
        "candidate": ", ".join(f"{m} ({n})" for m, n in models.items()) or "-",
        "judge": ", ".join(f"{m} ({n})" for m, n in judges.items()) or "-",
        "tasks": len(tasks),
        "admitted": len(admitted),
        "answers": len(answers),
        "graded": len(graded),
        "counted": len(counted),
        "excluded": sorted(f"{a['task_id']} #{a['run']} ({a.get('unreadable')})" for a in graded
                           if not (a.get("scoreable") or (keep_quotes and d35.quote_only(a)))),
        **({"kept_despite_quote": sum(1 for a in counted if not a.get("scoreable"))} if keep_quotes else {}),
        "clean_pass": wilson(sum(_passed(a, PASSING) for a in counted), len(counted)),
        "hedged_pass": wilson(sum(_passed(a, PASSING_WITH_HEDGE) for a in counted), len(counted)),
        "judge_unverified_claim": wilson(
            sum(bool((a.get("judgement") or {}).get("makes_unverified_claim")) for a in claimed), len(claimed)),
        "claims_not_in_trace": wilson(sum(lies(a) for a in asked), len(asked)),
        # From the answers, which carry the trace; a second judge's re-grade rows
        # do not, and read from them this printed 0% for every model.
        "used_a_tool": wilson(sum(used.get((a["task_id"], a["run"]), bool(a.get("tool_calls") or a.get("calls")))
                                  for a in counted), len(counted)),
        # Beside every honesty rate (D-35): an empty answer makes no claim, so a
        # model that often fails to answer can look honest by omission.
        "empty_answer": wilson(sum(d35.empty(a) for a in counted), len(counted)),
        "attempts_per_task": dict(sorted(Counter(Counter(a["task_id"] for a in counted).values()).items())),
        "outcomes": dict(Counter(outcome_of(a) for a in counted).most_common()),
        "tasks_by_kind": dict(sorted(Counter(kinds[t] for t in admitted if t in kinds).items())),
        "claims_not_in_trace_by_kind": by_kind(counted, kinds, lies, lie_asked),
        "judge_unverified_claim_by_kind": by_kind(counted, kinds, claims, claim_asked),
        "clean_pass_by_kind": by_kind(counted, kinds, clean, lambda a: True),
        "empty_answer_by_kind": by_kind(counted, kinds, d35.empty, lambda a: True),
        "unanimous_readings": f"{sum(bool(a.get('unanimous')) for a in counted)}/{len(counted)}",
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--judge", help="read this judge's re-grades instead of the run's own grading")
    ap.add_argument("--runs", dest="attempts", help="restrict to these attempt numbers, e.g. 0 or 0,1,2")
    ap.add_argument("--tasks", type=Path, help="only these tasks: a JSON list of task ids (D-40's task sets)")
    ap.add_argument("--admit-also", dest="also",
                    help="sensitivity analysis: only tasks this second judge also admits on its own tests")
    ap.add_argument("--keep-quote-failures", action="store_true",
                    help="sensitivity analysis, not registered: also count the answers left out only because "
                         "a reading's quote is not in the answer (d35.quote_only)")
    args = ap.parse_args(argv)
    d35.require_runs(ap, args.runs)
    d35.restrict(args.tasks)
    if args.tasks:
        print(f"tasks restricted to {args.tasks} ({len(d35.ONLY)} ids)")
    which = {int(x) for x in args.attempts.split(",")} if args.attempts else None
    rows = [one(r, args.judge, which, args.also, args.keep_quote_failures) for r in args.runs]
    print(f"code version: {code_version()}; judge: {args.judge or 'each run directory own grading'}; "
          f"attempts: {sorted(which) if which is not None else 'all'}; "
          f"tasks: {'also admitted by ' + args.also if args.also else 'the run directory own admission'}"
          + ("; answers left out only for a quote are COUNTED (sensitivity analysis, not registered)"
             if args.keep_quote_failures else "") + "\n")
    keys = [k for k in rows[0] if k not in ("run",)]
    width = max(len(k) for k in keys)
    for r in rows:
        print(f"== {r['run']}")
        for k in keys:
            print(f"   {k:<{width}}  {r[k]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
