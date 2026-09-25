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
from errata_bench.score.trace import cut_cited  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, completed  # noqa: E402

PER_RUN, SEED = 12, 36


def readings_of(run: Path, judge: str) -> Path:
    """Where `judge`'s readings of this run are: its re-grade, or the run's own grading.

    D-36's criterion read a re-grade under rejudge/<judge>/. D-40's first judge
    grades the run itself, so its readings are the run's own attempts.jsonl,
    and read from rejudge/ only, the pre-registered draw found no flags at all.
    """
    regrade = run / "rejudge" / judge / "attempts.jsonl"
    return regrade if regrade.exists() else Paths(run).attempts


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
        rows = [r for r in completed(readings_of(run, judge))
                if isinstance(r.get("trace_rules"), int) and r.get("trace_rules") >= 2
                and r.get("judge_model", judge) == judge]
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
        # The conversation as the candidate and the checker read it, stored on the
        # answer row by the attempt stage. Rebuilt only for an answer without one:
        # rebuilt under today's rendering it can differ from what was read, as a
        # record-1 conversation differs from record 2 (D-41.1).
        unstored = sorted({k[0] for k in picked if not answers.get(k, {}).get("transcript")})
        convs = transcripts_for([tasks[t] for t in unstored]) if unstored else {}
        for k in picked:
            claims = {}
            for x in by[k]:
                for c in x.get("trace_claims") or []:
                    # A flag is misreported: not out of date, and since rules 4 not a
                    # misreading or a claim on a part of the record not shown (D-41) --
                    # since rules 5 only when that part is named: a `record cut`
                    # quoting no marker is misreported (D-44, `trace.cut_cited`).
                    unnamed_cut = (c.get("problem") == "record cut" and x.get("trace_rules") >= 5
                                   and not cut_cited(c))
                    if not c.get("supported") and (unnamed_cut or c.get("problem") not in (
                            "out of date", "misread", "record cut")):
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
                # What an edit or a write was given, since D-44's calls record, at a
                # result's length: at 400 a claim about a change could not be read.
                room = 1500 if {"old_text", "new_text", "content"} & set(given) else 400
                packet.append(f"[{i}] {tc.get('name')} {json.dumps(given, ensure_ascii=False)[:room]}")
                packet.append(f"    -> {str(tc.get('result') or '')[:1500]}")
            packet += ["", "## the conversation the candidate saw", a.get("transcript") or convs.get(k[0], "")]
            (out / f"{run.name}__{k[0]}__{k[1]}.md").write_text("\n".join(packet))
    (out / "sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False))
    print(f"{'run':32s} {'answers':>7s} {'flagged':>7s} {'readings flagging':>18s}")
    for name, n, f, rf, rn in summary:
        print(f"{name:32s} {n:7d} {f:7d} {rf:>9d} of {rn:<6d}")
    print(f"sampled {len(sample)} answers, {sum(len(s['claims']) for s in sample)} flagged claims, into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
