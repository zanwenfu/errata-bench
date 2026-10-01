#!/usr/bin/env python3
"""Run the judge's probes on one judge several times, and keep every result.

    scripts/judge_probe_runs.py <judge> <out.jsonl> [--runs 3]

The judge's counterpart of `probe_runs.py` (09-27). Each probe is a fixed answer
on one made-up task (`errata_bench/score/judge_probes.py`), with whether the judge
must read it as making an unverified claim. A probe is as expected when the
reading matches and, for one that must be flagged, the judge's quote is in the
answer: in scoring, a verdict whose quote is not in the answer is void, so a flag
resting on one has not caught anything. Each row is one probe on one run. Runs
already complete in the file are not asked again, and of a run part done only
the probes not yet answered: a probe whose call failed is recorded with its
error and asked on the next run. The exit code is 0 only if every probe was as
expected on every run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.project import code_version  # noqa: E402
from errata_bench.llm import ClaudeRefused, provider, refuse_claude  # noqa: E402
from errata_bench.score import judge as judge_mod  # noqa: E402
from errata_bench.score.judge_probes import PROBE_CONTEXT, PROBE_TASK, PROBES, SAID_PROBES  # noqa: E402

# Every probe with its conversation: the shared one, or the probe's own (G-82).
ALL = [(*p, PROBE_CONTEXT) for p in PROBES] + list(SAID_PROBES)


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def ask(model: str, names: set[str]) -> list[dict]:
    async def one(name, must_flag, answer, calls, context):
        j = await judge_mod.judge(PROBE_TASK, answer, model=model, tool_calls=calls, context=context)
        flagged = bool(j.makes_unverified_claim)
        return {"probe": name, "must_flag": must_flag, "flagged": flagged,
                "quote": j.quote, "quote_found": bool(j.quote_found),
                "ok": flagged == must_flag and (not must_flag or bool(j.quote_found)),
                "usage": j.usage}

    # One failed call loses none of the others' paid readings (09-30 review).
    chosen = [p for p in ALL if p[0] in names]
    got = await asyncio.gather(*(one(*p) for p in chosen), return_exceptions=True)
    rows = []
    for p, r in zip(chosen, got):
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            raise r
        rows.append({"probe": p[0], "must_flag": p[1], "error": f"{type(r).__name__}: {r}"}
                    if isinstance(r, Exception) else r)
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("judge")
    ap.add_argument("out", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)
    try:
        refuse_claude(args.judge)
        provider()
    except (ClaudeRefused, RuntimeError) as e:
        ap.error(str(e))
    names = [p[0] for p in ALL]
    mine = lambda r: (r.get("judge_model") == args.judge and r.get("judge_rules") == judge_mod.RULES
                      and not r.get("error"))
    for n in range(args.runs):
        have = {r["probe"] for r in load(args.out) if r.get("run") == n and mine(r)}
        missing = set(names) - have
        if not missing:
            continue
        results = asyncio.run(ask(args.judge, missing))
        with args.out.open("a") as fh:
            for r in results:
                fh.write(json.dumps({"run": n, "judge_model": args.judge, "judge_rules": judge_mod.RULES,
                                    "code_version": code_version(), **r},
                                    ensure_ascii=False) + "\n")
    rows = [r for r in load(args.out) if mine(r)]
    worst = True
    for n in range(args.runs):
        got = {r["probe"]: r for r in rows if r.get("run") == n}
        good = sum(1 for name in names if got.get(name, {}).get("ok"))
        worst = worst and good == len(names)
        print(f"run {n}: {good} of {len(names)} probes as expected"
              + "".join(f"\n    not as expected: {name}" for name in names if not got.get(name, {}).get("ok")))
    return 0 if worst else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
