#!/usr/bin/env python3
"""Put a judge through each task's known answers and controls, from the frozen release (v1 step 4).

    scripts/admit_judge.py <release dir> --out <dir> [--passes 3] [--concurrency 8] [--limit N]

The judge is ERRATA_JUDGE_MODEL, called with your own key; it must be named.
For each of the release's tasks it reads the known-wrong and known-right
answers, in both orders (calibration.jsonl), and then, on each task it read
correctly, the controls, --passes times each (controls.jsonl). <out> is then
what `scripts/grade_harbor.py --admission` reads: a task is graded only by a
judge admitted to it. Needs neither the corpus nor GitHub
(`errata_bench.release.admission`). Paid: about 4 readings per task for the
known answers, and 2 per control and pass. Running again finishes what is left.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.release.admission import conversations_from  # noqa: E402
from errata_bench.spec import Task  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=10**9)
    args = ap.parse_args(argv)
    if not os.environ.get("ERRATA_JUDGE_MODEL"):
        print("refused: set ERRATA_JUDGE_MODEL to the judge to admit; nothing was read", file=sys.stderr)
        return 2

    from errata_bench.instrument.control import controlled
    from errata_bench.llm import judge_model
    from errata_bench.score.judge import can_be_scored
    from errata_bench.stages.building import stage_calibrate, stage_control

    paths = Paths(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    if not paths.tasks.exists():
        rows = [json.loads((d / "grading" / "task.json").read_text())
                for d in sorted((args.release / "tasks").iterdir()) if (d / "grading" / "task.json").is_file()]
        paths.tasks.write_text("".join(json.dumps(Task.from_json(r).to_json()) + "\n" for r in rows))
    with conversations_from(args.release):
        calibrated = asyncio.run(stage_calibrate(paths, args.limit, args.concurrency))
        print(calibrated.line().strip())
        controls = asyncio.run(stage_control(paths, args.limit, args.concurrency, passes=args.passes))
        print(controls.line().strip())
    grader = judge_model()
    sound = {r["task_id"] for r in load(paths.calibration) if r.get("judge_model") == grader and can_be_scored(r)}
    admitted = sound & controlled(paths)
    tasks = sum(1 for _ in load(paths.tasks))
    print(f"{grader}: {len(sound)} of {tasks} tasks' known answers read correctly, {len(admitted)} admitted "
          f"(their controls behaved)")
    return 0 if calibrated.failed == 0 and controls.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
