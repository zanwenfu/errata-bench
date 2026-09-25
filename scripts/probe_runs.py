#!/usr/bin/env python3
"""Run the trace check's probes on one judge several times, and keep every result.

    scripts/probe_runs.py <judge> <out.jsonl> [--runs 3]

The controls stage asks the probes once per judge. D-42 asks for all of them to
come out as expected on each of three runs, because one run of a reader that
samples can pass by luck. Each row is one probe on one run: whether it had to be
flagged, whether it was, whether that is as expected, and under which rules.
Runs already complete in the file are not asked again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.llm import ClaudeRefused, refuse_claude  # noqa: E402
from errata_bench.score import trace  # noqa: E402
from errata_bench.score.attempt import INSTRUCTIONS as CANDIDATE_RULES, environment_note  # noqa: E402


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("judge")
    ap.add_argument("out", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)
    try:
        refuse_claude(args.judge)
    except ClaudeRefused as e:
        ap.error(str(e))
    names = [p[0] for p in trace.PROBES]
    given = f"{CANDIDATE_RULES}\n\n{environment_note('host')}"
    for n in range(args.runs):
        have = {r["probe"] for r in load(args.out)
                if r.get("run") == n and r.get("judge_model") == args.judge and r.get("trace_rules") == trace.RULES}
        if all(name in have for name in names):
            continue
        results = asyncio.run(trace.verify(model=args.judge, given=given))
        with args.out.open("a") as fh:
            for r in results:
                fh.write(json.dumps({"run": n, "judge_model": args.judge, "trace_rules": trace.RULES, **r},
                                    ensure_ascii=False) + "\n")
    rows = [r for r in load(args.out) if r.get("judge_model") == args.judge and r.get("trace_rules") == trace.RULES]
    worst = True
    for n in range(args.runs):
        mine = {r["probe"]: r for r in rows if r.get("run") == n}
        good = sum(1 for name in names if mine.get(name, {}).get("ok"))
        worst = worst and good == len(names)
        print(f"run {n}: {good} of {len(names)} probes as expected"
              + "".join(f"\n    not as expected: {name}" for name in names if not mine.get(name, {}).get("ok")))
    return 0 if worst else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
