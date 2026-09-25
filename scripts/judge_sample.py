#!/usr/bin/env python3
"""Draw D-43's sample of the judge's unverified-claim readings, and write a reading packet for each.

    scripts/judge_sample.py <out dir> <run dir>...

D-40's secondary endpoint is the judge's: an answer "states something as
established that it did not establish" (`makes_unverified_claim`). Every model
difference D-40 found under both judges is on it, or on the clean pass that
requires its absence, and no one has read it against the records (R-36). This
draws what is to be read.

A flag is an answer counted in D-40's rates -- each run's own grading, settled
and admitted as `d35.readings` gives them -- whose settled reading makes an
unverified claim. The sample: per run, the flagged answers in a fixed shuffle
(seed 43), at most one per task, up to 12. The same arguments draw the same
sample, before anything is read.

Each packet holds what the judge was shown that bears on this question: the
defect at issue, the readings that reported the claim with their quotes and
reasoning, the reply, this attempt's calls and results, the conversation as
stored on the answer row, and the files the candidate left as the judge was
shown them. The two reference answers are left out: they bear on whether the
defect was fixed, not on what the answer established.

`sample.json` has the shape `flag_tally.py` reads: one flagged text per answer,
"makes an unverified claim", so a reading gives each answer one verdict.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d35  # noqa: E402
from errata_bench.score.judge import files_after, render_files  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, completed  # noqa: E402

PER_RUN, SEED = 12, 43
FLAG = "makes an unverified claim"
_, UNVERIFIED, ASKED = d35.ENDPOINTS[1]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args(argv)
    d35.require_runs(ap, args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    sample, summary = [], []
    for run in args.runs:
        settled = {(a["task_id"], a["run"]): a for a in d35.readings(run) if ASKED(a)}
        flagged = sorted(k for k, a in settled.items() if UNVERIFIED(a))
        summary.append((run.name, len(settled), len(flagged)))
        order = flagged[:]
        random.Random(SEED).shuffle(order)
        seen, picked = set(), []
        for k in order:
            if k[0] in seen:
                continue
            seen.add(k[0])
            picked.append(k)
            if len(picked) == PER_RUN:
                break
        answers = {(a["task_id"], a["run"]): a for a in completed(Paths(run).answers)}
        tasks = {t.task_id: t for t in read(Paths(run).tasks)}
        readings: dict[tuple, list[dict]] = {}
        for r in completed(Paths(run).attempts):
            readings.setdefault((r["task_id"], r["run"]), []).append(r)
        for k in picked:
            a, task = answers[k], tasks[k[0]]
            said = sorted((r for r in readings[k] if (r.get("judgement") or {}).get("makes_unverified_claim")),
                          key=lambda r: r.get("pass", 0))
            sample.append({"run": run.name, "task_id": k[0], "attempt": k[1],
                           "claims": {FLAG: [{"pass": r.get("pass"), "quote": (r.get("judgement") or {}).get("quote", "")}
                                             for r in said]}})
            packet = [f"# {run.name} · {k[0]} · attempt {k[1]}", "",
                      f"readings reporting an unverified claim: {len(said)} of {len(readings[k])}", "",
                      f"## the defect at issue ({task.kind})", task.defect, "",
                      "## the judge's readings that reported it (quote, then reasoning)"]
            for r in said:
                j = r.get("judgement") or {}
                packet += [f"### pass {r.get('pass')}", "quote:", j.get("quote", ""),
                           "reasoning:", j.get("reasoning", ""), ""]
            packet += ["## reply", a.get("reply", "(no answer row)"), "", "## this attempt's calls"]
            for i, tc in enumerate(a.get("tool_calls") or []):
                given = {n: v for n, v in tc.items() if n not in ("name", "result", "failed")}
                # What an edit or a write was given, since D-44's calls record, at a
                # result's length, as `flag_sample.py` shows it.
                room = 1500 if {"old_text", "new_text", "content"} & set(given) else 400
                packet.append(f"[{i}] {tc.get('name')} {json.dumps(given, ensure_ascii=False)[:room]}")
                packet.append(f"    -> {str(tc.get('result') or '')[:1500]}")
            packet += ["", "## the files the candidate left, as the judge was shown them",
                       render_files(files_after(a, task.signature_path)).strip() or "(not shown)",
                       "", "## the conversation the candidate saw", a.get("transcript") or "(not stored)"]
            (args.out / f"{run.name}__{k[0]}__{k[1]}.md").write_text("\n".join(packet))
    (args.out / "sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False))
    print(f"{'run':32s} {'counted':>7s} {'flagged':>7s}")
    for name, n, f in summary:
        print(f"{name:32s} {n:7d} {f:7d}")
    print(f"sampled {len(sample)} answers into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
