"""The results table for a grid of candidate run directories.

    .venv/bin/python scripts/grid_table.py runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code ...

Every number comes from the harness's own functions -- `settled` folds the
repeated readings conservatively, `_passed` prices the pass line, admission is
calibration plus controls plus the gate as the run directory holds them -- so
the table cannot use a rule the pipeline does not. Rates carry Wilson 95%
intervals and their denominators. Attempts on one task are correlated, so with
more than one attempt per task the interval here is optimistic; the per-task
columns are the ones to compare models on.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.instrument.control import controlled  # noqa: E402
from errata_bench.project import code_version  # noqa: E402
from errata_bench.score.judge import PASSING, PASSING_WITH_HEDGE, can_be_scored, outcome_of  # noqa: E402
from errata_bench.score.rejudge import _passed, settled  # noqa: E402
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


def one(run: Path) -> dict:
    paths = Paths(run)
    tasks = {t.task_id: t for t in read(paths.tasks)}
    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    answers = [a for a in load(paths.answers) if not a.get("error")]
    graded = [a for a in settled(load(paths.attempts)) if a.get("task_id") in admitted]
    counted = [a for a in graded if a.get("scoreable")]
    asked = [a for a in counted if a.get("claims_match_trace") is not None]
    claimed = [a for a in counted if (a.get("judgement") or {}).get("makes_unverified_claim") is not None]
    models = Counter(a.get("model") for a in answers)
    judges = Counter(a.get("judge_model") for a in load(paths.attempts) if not a.get("error"))
    per_kind = defaultdict(lambda: [0, 0])
    for a in counted:
        k = tasks[a["task_id"]].kind if a["task_id"] in tasks else "?"
        per_kind[k][1] += 1
        per_kind[k][0] += _passed(a, PASSING)
    return {
        "run": run.name,
        "candidate": ", ".join(f"{m} ({n})" for m, n in models.items()) or "-",
        "judge": ", ".join(f"{m} ({n})" for m, n in judges.items()) or "-",
        "tasks": len(tasks),
        "admitted": len(admitted),
        "answers": len(answers),
        "graded": len(graded),
        "counted": len(counted),
        "excluded": sorted(f"{a['task_id']} #{a['run']} ({a.get('unreadable')})" for a in graded if not a.get("scoreable")),
        "clean_pass": wilson(sum(_passed(a, PASSING) for a in counted), len(counted)),
        "hedged_pass": wilson(sum(_passed(a, PASSING_WITH_HEDGE) for a in counted), len(counted)),
        "judge_unverified_claim": wilson(
            sum(bool((a.get("judgement") or {}).get("makes_unverified_claim")) for a in claimed), len(claimed)),
        "claims_not_in_trace": wilson(sum(a.get("claims_match_trace") is False for a in asked), len(asked)),
        "used_a_tool": wilson(sum(bool(a.get("tool_calls") or a.get("calls")) for a in counted), len(counted)),
        "outcomes": dict(Counter(outcome_of(a) for a in counted).most_common()),
        "clean_pass_by_kind": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_kind.items())},
        "unanimous_readings": f"{sum(bool(a.get('unanimous')) for a in counted)}/{len(counted)}",
    }


def main(argv: list[str]) -> int:
    runs = [Path(a) for a in argv]
    if not runs:
        print(__doc__)
        return 2
    rows = [one(r) for r in runs]
    print(f"code version: {code_version()}\n")
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
