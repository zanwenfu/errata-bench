#!/usr/bin/env python3
"""Draw a sample of the answers the judge did NOT flag, and write a reading packet for each.

    scripts/judge_misses_sample.py <out dir> <run dir>... [--per-run 8] [--rule majority]

Every reading of the judge so far (D-43, D-44, D-45) read only answers it had
flagged as making an unverified claim, and asked whether the flag was right.
That measures how often a flag is wrong, not how often an unverified claim goes
unflagged: an answer the judge passed was never read. With the judge's reading
to be the headline (09-27), what it misses is the other half of its accuracy.

A miss is an answer counted in the run's rates -- settled by `rule` over the
run's own grading and admitted, as `d35.readings` gives them -- whose settled
reading makes no unverified claim, and which states as established something
the record does not establish. The sample: per run, the unflagged answers in a
fixed shuffle (seed 47), at most one per task, up to `--per-run`. The same
arguments draw the same sample, before anything is read.

Each packet holds what `judge_sample.py` shows (the defect, the reply, the calls
as the judge is shown them, the files and the conversation), with every
reading's own reasoning, since none of them reported a claim. `sample.json` has
the shape `flag_tally.py` reads: one text per answer, so a reading gives each
answer one verdict, and "real" is a miss.
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
from errata_bench.score.rejudge import SETTLE_RULES  # noqa: E402
from errata_bench.score.trace import render  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, completed  # noqa: E402

SEED = 47
MISS = "states as established something it did not establish"
_, UNVERIFIED, ASKED = d35.ENDPOINTS[1]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--per-run", type=int, default=8)
    ap.add_argument("--rule", choices=SETTLE_RULES, default="majority")
    # Scored only when every reading could be checked, as the D-45 misses screen
    # was drawn and tallied (09-27); v1's own grading scores by the readings that
    # can be (09-28). Named, so the stored screen reproduces.
    ap.add_argument("--supported", choices=("all", "majority"), default="all")
    args = ap.parse_args(argv)
    d35.require_runs(ap, args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    sample, summary = [], []
    for run in args.runs:
        settled = {(a["task_id"], a["run"]): a for a in d35.readings(run, rule=args.rule, supported=args.supported) if ASKED(a)}
        unflagged = sorted(k for k, a in settled.items() if not UNVERIFIED(a))
        summary.append((run.name, len(settled), len(unflagged)))
        order = unflagged[:]
        random.Random(SEED).shuffle(order)
        seen, picked = set(), []
        for k in order:
            if k[0] in seen:
                continue
            seen.add(k[0])
            picked.append(k)
            if len(picked) == args.per_run:
                break
        answers = {(a["task_id"], a["run"]): a for a in completed(Paths(run).answers)}
        tasks = {t.task_id: t for t in read(Paths(run).tasks)}
        readings: dict[tuple, list[dict]] = {}
        for r in completed(Paths(run).attempts):
            readings.setdefault((r["task_id"], r["run"]), []).append(r)
        for k in picked:
            a, task = answers[k], tasks[k[0]]
            mine = sorted(readings[k], key=lambda r: r.get("pass", 0))
            said = [r for r in mine if (r.get("judgement") or {}).get("makes_unverified_claim")]
            sample.append({"run": run.name, "task_id": k[0], "attempt": k[1],
                           "claims": {MISS: [{"pass": r.get("pass")} for r in mine]}})
            packet = [f"# {run.name} · {k[0]} · attempt {k[1]}", "",
                      f"readings reporting an unverified claim: {len(said)} of {len(mine)} "
                      f"(settled: none, by the {args.rule} rule)", "",
                      f"## the defect at issue ({task.kind})", task.defect, "",
                      "## the judge's readings (reasoning)"]
            for r in mine:
                j = r.get("judgement") or {}
                packet += [f"### pass {r.get('pass')}: unverified claim "
                           f"{'reported' if j.get('makes_unverified_claim') else 'not reported'}",
                           j.get("reasoning", ""), ""]
            packet += ["## reply", a.get("reply", "(no answer row)"), "",
                       "## this attempt's calls, as the judge is shown them", render(a.get("tool_calls") or []),
                       "", "## the files the candidate left, as the judge was shown them",
                       render_files(files_after(a, task.signature_path)).strip() or "(not shown)",
                       "", "## the conversation the candidate saw", a.get("transcript") or "(not stored)"]
            (args.out / f"{run.name}__{k[0]}__{k[1]}.md").write_text("\n".join(packet))
    (args.out / "sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False))
    print(f"{'run':32s} {'counted':>7s} {'unflagged':>9s}")
    for name, n, u in summary:
        print(f"{name:32s} {n:7d} {u:9d}")
    print(f"sampled {len(sample)} answers into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
