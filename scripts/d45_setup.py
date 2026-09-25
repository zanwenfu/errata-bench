#!/usr/bin/env python3
"""Set up D-45: re-grade under view 2 only the D-44 answers that view 1 cut.

    scripts/d45_setup.py <d44 run dir> <d45 run dir> [--judge gpt-6-sol]

The graders' prompt for an answer differs between view 1 (the trace bounded at
24,000 characters, the answer at 12,000) and view 2 (the whole record, 09-25)
only when view 1 cut something: an output withheld or clipped, an edit's text
clipped, or an answer past 12,000 characters. For every other answer the prompt
is the same text, byte for byte, so its D-44 readings are what view 2 would ask,
and they are copied as they are. The cut answers get no readings, so the
grading stage, which resumes, reads exactly them.

The new run directory holds the task and admission files, all of D-44's
answers, and the copied readings: the run's own grading (attempts.jsonl) and
each re-grade named by --judge (rejudge/<judge>/attempts.jsonl, with the tests
the re-grade driver expects beside it). Nothing in D-44's directory is touched.
Prints which answers view 1 cut, and why.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.trace import ANSWER_CHARS, render  # noqa: E402

VIEW1_TRACE = 24_000
JUDGE_ANSWER = 12_000


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def cut_by_view1(answer: dict) -> list[str]:
    """What view 1 left out of this answer's grader prompts; empty when it left out nothing."""
    calls = answer.get("tool_calls") or []
    why = []
    if calls and render(calls, budget=VIEW1_TRACE) != render(calls):
        why.append("trace")
    if len(answer.get("reply") or "") > min(ANSWER_CHARS, JUDGE_ANSWER):
        why.append("answer")
    return why


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--judge", action="append", default=[])
    args = ap.parse_args(argv)
    if not (args.src / "tasks.jsonl").is_file():
        ap.error(f"not a run directory: {args.src}")
    if args.dst.exists():
        ap.error(f"{args.dst} exists; D-45 is set up once")
    answers = load(args.src / "answers.jsonl")
    cut = {(a["task_id"], a.get("run", 0)): cut_by_view1(a) for a in answers}
    keep = {k for k, why in cut.items() if not why}
    args.dst.mkdir(parents=True)
    for name in ("tasks", "calibration", "controls", "gate", "answers"):
        if (args.src / f"{name}.jsonl").exists():
            shutil.copy2(args.src / f"{name}.jsonl", args.dst / f"{name}.jsonl")

    def copy_readings(src: Path, dst: Path) -> int:
        rows = [r for r in load(src) if (r.get("task_id"), r.get("run", 0)) in keep]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return len(rows)

    copied = {"own grading": copy_readings(args.src / "attempts.jsonl", args.dst / "attempts.jsonl")}
    for judge in args.judge:
        src, dst = args.src / "rejudge" / judge, args.dst / "rejudge" / judge
        for name in ("calibration", "controls", "instrument"):
            if (src / f"{name}.jsonl").exists():
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / f"{name}.jsonl", dst / f"{name}.jsonl")
        copied[judge] = copy_readings(src / "attempts.jsonl", dst / "attempts.jsonl")
    n_cut = sum(1 for why in cut.values() if why)
    print(f"{args.src.name}: {len(answers)} answers; view 1 cut {n_cut} "
          f"(trace {sum('trace' in w for w in cut.values())}, answer {sum('answer' in w for w in cut.values())}); "
          f"readings copied for the other {len(keep)}: " + ", ".join(f"{k} {v}" for k, v in copied.items()))
    for k, why in sorted(cut.items()):
        if why:
            print(f"  re-graded: {k[0]} #{k[1]} ({', '.join(why)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
