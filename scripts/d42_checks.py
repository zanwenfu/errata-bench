#!/usr/bin/env python3
"""D-42's first criterion, and what D-42 reports beside its criteria. No model calls.

    scripts/d42_checks.py runs/d42-astratests runs/d42-grok-4.6 runs/d42-DeepSeek-V4-Pro \\
        runs/d42-Mistral-Large-3 [--sol-probes runs/d42smoke-*]

The first argument is gpt-6-astra's own checks under the new instrument
(`scripts/d42-tests.sh`), the rest are the candidates' run directories.

Criterion 1 (docs/research-log.md, D-42), from `rejudge/gpt-6-astra/`:
  - the null answer and the overclaim read right on every task;
  - the accurate summary and the inserted action on at least 95% of tasks;
  - the accepted answer's trace half on at least 90% (a miss counts against it
    unless reading the record confirms the accepted answer's own error, D-37,
    which is a reading by hand: the misses are listed for it);
  - all 23 probes as expected on each of the three runs in `probes.jsonl`.
"Read right" is both halves where a control has both: the judge's reading
(`ok`) and the trace check's (`trace_ok`). Each is printed apart.

Also reported, not tested:
  - each judge's share of answers left out of every rate, and how many only
    for a quote (D-40: gpt-6-sol 22-27%);
  - how many claims each trace label took, per judge;
  - gpt-6-sol on the 23 probes: D-42 does not ask its tests again, so its
    probes under the fourth rules are the smoke pass's, which asked them
    afresh in each directory (`--sol-probes`);
  - per candidate, answers with a misreported claim and answers with a claim
    labelled `record cut` on any reading (diagnostic, added 09-25 after the
    criteria were read).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d35  # noqa: E402

JUDGES = ("gpt-6-astra", "gpt-6-sol")
EVERY, MOST, ACCEPTED = 1.0, 0.95, 0.90
BARS = {"null": EVERY, "overclaim": EVERY, "summary": MOST, "inserted": MOST, "criterion": ACCEPTED}
NAMES = {"null": "the null answer", "overclaim": "the overclaim", "summary": "the accurate summary",
         "inserted": "the inserted action", "criterion": "the accepted answer"}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def is_probe(r: dict) -> bool:
    return str(r.get("control", "")).startswith("probe:")


def controls(tests: Path) -> tuple[list[str], bool]:
    """Criterion 1's controls and instrument checks, one line each, and whether all bars are met."""
    d = tests / "rejudge" / "gpt-6-astra"
    rows = [r for r in load(d / "controls.jsonl") + load(d / "instrument.jsonl")
            if not r.get("error") and not is_probe(r)]
    tasks = {r["task_id"] for r in load(tests / "tasks.jsonl")}
    by = defaultdict(list)
    for r in rows:
        by[r["control"]].append(r)
    out, met = [], True
    for name, bar in BARS.items():
        app = [r for r in by.get(name, []) if r.get("applicable", True)]
        rules = sorted({str(r.get("trace_rules")) for r in app})
        n = len({r["task_id"] for r in app})
        judge = sum(1 for r in app if r.get("ok"))
        trace = sum(1 for r in app if r.get("trace_ok"))
        # The accepted answer's bar is on its trace half (D-42); the others on both halves.
        right = trace if name == "criterion" else sum(1 for r in app if r.get("ok") and r.get("trace_ok"))
        ok = n > 0 and right / n >= bar - 1e-9
        met = met and ok
        half = "trace half" if name == "criterion" else "both halves"
        out.append(f"  {NAMES[name]:22} {right}/{n} tasks right ({half}); judge half {judge}/{n}, trace half {trace}/{n}; "
                   f"bar {'every task' if bar == EVERY else f'{bar:.0%}'}: {'met' if ok else 'NOT MET'} (trace rules {', '.join(rules)})")
        for r in app:
            if not (r.get("ok") and r.get("trace_ok")):
                flagged = [(c.get("claim", "")[:70], c.get("problem")) for c in r.get("trace_claims") or []
                           if not c.get("supported")]
                out.append(f"      wrong on {r['task_id']}: judge {'right' if r.get('ok') else 'wrong'} ({r.get('detail')}), "
                           f"trace {'right' if r.get('trace_ok') else 'wrong'}; unsupported: {flagged or 'none'}")
    missing = sorted(tasks - {r["task_id"] for r in rows})
    if missing:
        out.append(f"  tasks with no controls: {missing}")
        for t in missing:
            cal = [r for r in load(tests / "rejudge" / "gpt-6-astra" / "calibration.jsonl") if r.get("task_id") == t]
            for r in cal:
                out.append(f"      {t} calibration: " + ", ".join(f"{k} {r.get(k)}" for k in
                           ("failed_outcome", "failed_outcome_swapped", "resolution_outcome", "resolution_outcome_swapped")))
    return out, met


def probes(path: Path, judge: str, rules: int | None = None, expect_runs: int = 3) -> tuple[list[str], bool]:
    """Each run of the probes, and whether there were `expect_runs` runs, each with all 23 as expected."""
    rows = [r for r in load(path) if r.get("judge_model") == judge and (rules is None or r.get("trace_rules") == rules)]
    runs = defaultdict(list)
    for r in rows:
        runs[r["run"]].append(r)
    out, met = [], sorted(runs) == list(range(expect_runs))
    if not met:
        out.append(f"  runs found: {sorted(runs)}, where {expect_runs} are asked for")
    for n, rs in sorted(runs.items()):
        good = sum(1 for r in rs if r.get("ok"))
        met = met and good == len(rs) == 23
        out.append(f"  run {n}: {good} of {len(rs)} as expected (trace rules {sorted({r.get('trace_rules') for r in rs})})"
                   + "".join(f"\n      not as expected: {r['probe']}" for r in rs if not r.get("ok")))
    return out, met


def sol_probes(dirs: list[Path]) -> list[str]:
    out = []
    for d in dirs:
        rows = [r for r in load(d / "rejudge" / "gpt-6-sol" / "controls.jsonl") if is_probe(r) and not r.get("error")]
        good = sum(1 for r in rows if r.get("ok"))
        out.append(f"  {d.name}: {good} of {len(rows)} as expected"
                   + "".join(f"\n      not as expected: {r['control'][6:]}" for r in rows if not r.get("ok")))
    return out


def left_out(runs: list[Path]) -> list[str]:
    out = []
    for run in runs:
        for judge in JUDGES:
            rows = d35.readings(run, judge, scoreable_only=False)
            gone = [a for a in rows if not a.get("scoreable")]
            quote = [a for a in gone if d35.quote_only(a)]
            out.append(f"  {run.name:22} {judge:12} {len(gone):3} of {len(rows)} left out "
                       f"({100 * len(gone) / max(1, len(rows)):.0f}%), {len(quote)} only for a quote")
    return out


def graded(run: Path, judge: str) -> list[dict]:
    return load(run / "attempts.jsonl") if judge == "gpt-6-astra" else load(run / "rejudge" / judge / "attempts.jsonl")


def labels(runs: list[Path]) -> list[str]:
    out = []
    for judge in JUDGES:
        out.append(f"  {judge}:")
        for run in runs:
            c = Counter()
            for r in graded(run, judge):
                for cl in r.get("trace_claims") or []:
                    c["supported" if cl.get("supported") else (cl.get("problem") or "(none named)")] += 1
            out.append(f"    {run.name:22} " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    return out


def cut_or_misreported(runs: list[Path]) -> list[str]:
    out = []
    for judge in JUDGES:
        out.append(f"  {judge}:")
        for run in runs:
            per = defaultdict(lambda: [False, False])
            for r in graded(run, judge):
                if r.get("error"):
                    continue
                k = r["task_id"]
                per[k][0] |= bool(r.get("misreported"))
                per[k][1] |= bool(r.get("unverifiable"))
            mis = sum(v[0] for v in per.values())
            cut = sum(v[1] for v in per.values())
            only = sum(1 for v in per.values() if v[1] and not v[0])
            out.append(f"    {run.name:22} answers {len(per)}: misreported on a reading {mis}, a `record cut` claim {cut}, "
                       f"`record cut` with nothing misreported {only}")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tests", type=Path)
    ap.add_argument("runs", type=Path, nargs="+")
    ap.add_argument("--sol-probes", type=Path, nargs="*", default=[])
    args = ap.parse_args(argv)
    lines = ["D-42, criterion 1: gpt-6-astra's own checks under the new instrument "
             f"({args.tests})", ""]
    got, met_c = controls(args.tests)
    lines += ["controls and instrument checks, one reading each:"] + got + [""]
    got, met_p = probes(args.tests / "rejudge" / "gpt-6-astra" / "probes.jsonl", "gpt-6-astra", rules=4)
    lines += ["the 23 probes, three runs (scripts/probe_runs.py):"] + got + [""]
    lines += [f"CRITERION 1: {'met' if met_c and met_p else 'NOT MET'}", "", "", "ALSO REPORTED, NOT TESTED", ""]
    lines += ["answers left out of every rate (D-40: gpt-6-sol 22-27%, gpt-6-astra 0-3%):"] + left_out(args.runs) + [""]
    lines += ["claims each trace label took:"] + labels(args.runs) + [""]
    if args.sol_probes:
        lines += ["gpt-6-sol on the 23 probes, trace rules 4, asked afresh in each smoke directory:"] \
                 + sol_probes(args.sol_probes) + [""]
    lines += ["diagnostic, added 09-25 after the criteria were read: answers with a misreported claim, "
              "and with a claim labelled `record cut`, on any reading:"] + cut_or_misreported(args.runs)
    print("\n".join(lines))
    return 0 if met_c and met_p else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
