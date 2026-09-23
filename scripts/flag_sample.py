#!/usr/bin/env python3
"""Draw D-36 criterion 3's sample of trace-check flags, and write a reading packet for each.

    scripts/flag_sample.py <judge> <out dir> <run dir>...

A flag is an answer whose settled reading by <judge> (a claim misreported on
any of its readings) is misreported, under the trace check's second rules.
The sample: per run, the flagged answers in a fixed shuffle (seed 36), at most
one per task, up to 12. It is drawn before anything is read, and the same
arguments draw the same sample. Each packet holds what a reader needs: the
flagged claims with the checker's source, problem and reasoning, the reply,
this attempt's calls and results, and the conversation the candidate saw.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.attempt import transcripts_for  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, completed  # noqa: E402

PER_RUN, SEED = 12, 36


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("judge")
    ap.add_argument("out", type=Path)
    ap.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args(argv)
    judge, out, runs = args.judge, args.out, args.runs
    for r in runs:
        if not (r / "tasks.jsonl").is_file():
            ap.error(f"not a run directory (no tasks.jsonl): {r}")
    out.mkdir(parents=True, exist_ok=True)
    sample, summary = [], []
    for run in runs:
        rows = [r for r in completed(run / "rejudge" / judge / "attempts.jsonl") if isinstance(r.get("trace_rules"), int) and r.get("trace_rules") >= 2]
        by = {}
        for r in rows:
            by.setdefault((r["task_id"], r["run"]), []).append(r)
        flagged = sorted(k for k, rs in by.items() if any(x.get("misreported") for x in rs))
        summary.append((run.name, len(by), len(flagged), sum(1 for r in rows if r.get("misreported")), len(rows)))
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
        convs = transcripts_for([tasks[k[0]] for k in picked])
        for k in picked:
            claims = {}
            for x in by[k]:
                for c in x.get("trace_claims") or []:
                    if not c.get("supported") and c.get("problem") != "out of date":
                        claims.setdefault(c["claim"], []).append({"pass": x["pass"], "source": c.get("source"),
                                                                  "problem": c.get("problem")})
            a = answers.get(k, {})
            sample.append({"run": run.name, "task_id": k[0], "attempt": k[1], "claims": claims})
            packet = [f"# {run.name} · {k[0]} · attempt {k[1]}", "",
                      f"readings flagging it: {sum(1 for x in by[k] if x.get('misreported'))} of {len(by[k])}", "",
                      "## flagged claims"]
            for c, where in claims.items():
                packet.append(f"- {c}  <- {where}")
            packet += ["", "## the checker's reasoning, per flagging reading"]
            for x in by[k]:
                if x.get("misreported"):
                    packet.append(f"- pass {x['pass']}: {x.get('trace_reasoning', '')}")
            packet += ["", "## reply", a.get("reply", "(no answer row)"), "", "## this attempt's calls"]
            for i, tc in enumerate(a.get("tool_calls") or []):
                given = {k: v for k, v in tc.items() if k not in ("name", "result", "failed")}
                packet.append(f"[{i}] {tc.get('name')} {json.dumps(given, ensure_ascii=False)[:400]}")
                packet.append(f"    -> {str(tc.get('result') or '')[:1500]}")
            packet += ["", "## the conversation the candidate saw", convs.get(k[0], "")]
            (out / f"{run.name}__{k[0]}__{k[1]}.md").write_text("\n".join(packet))
    (out / "sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False))
    print(f"{'run':32s} {'answers':>7s} {'flagged':>7s} {'readings flagging':>18s}")
    for name, n, f, rf, rn in summary:
        print(f"{name:32s} {n:7d} {f:7d} {rf:>9d} of {rn:<6d}")
    print(f"sampled {len(sample)} answers, {sum(len(s['claims']) for s in sample)} flagged claims, into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
