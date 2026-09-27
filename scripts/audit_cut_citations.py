#!/usr/bin/env python3
"""Every stored `record cut` citation, checked against what its grader was shown (#4).

    python scripts/audit_cut_citations.py runs/<run>...

A `record cut` claim is excused from `misreported` when it quotes the cut it
rests on. That was checked for the quote's form only, so a grader could quote a
marker it was never shown (#4). This reads each graded row's trace claims and
rebuilds what its grader was shown -- the answer's conversation and its record
-- to class each citation as `trace.cut_citation` does: shown, elsewhere,
invented or none. The rows do not say which view each reading had, so each
citation is classed against every view a grader could have been shown -- the
whole record (view 2), each fallback, and view 1's bound of 24,000 characters,
which is the smallest fallback -- and given the best of them. The renderer has
changed since view 1, so a view-1 marker can come out with another number
here and read as invented. No model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.trace import FALLBACKS, cut_citation, render  # noqa: E402

OUTCOMES = ("shown", "elsewhere", "invented", "none")
VIEWS = (None, *FALLBACKS)   # the whole record, then each fallback; the last is view 1's bound


def graded_files(run: Path) -> list[tuple[str, Path]]:
    """A run's graded rows: its own grader's, and each re-judge's."""
    out = [("(the run's own)", run / "attempts.jsonl")] if (run / "attempts.jsonl").is_file() else []
    for d in sorted((run / "rejudge").glob("*")) if (run / "rejudge").is_dir() else []:
        if (d / "attempts.jsonl").is_file():
            out.append((d.name, d / "attempts.jsonl"))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", type=Path, nargs="+")
    args = ap.parse_args(argv)
    counts: dict[tuple, Counter] = defaultdict(Counter)
    examples: dict[str, list] = defaultdict(list)
    for run in args.runs:
        answers = {}
        if (run / "answers.jsonl").is_file():
            for line in open(run / "answers.jsonl"):
                r = json.loads(line)
                if not r.get("error"):
                    answers[(r["task_id"], r.get("run"), r.get("model"))] = r
        for label, path in graded_files(run):
            for line in open(path):
                row = json.loads(line)
                a = (answers.get((row["task_id"], row.get("run"), row.get("model") or row.get("candidate_model")))
                     or next((v for k, v in answers.items() if k[:2] == (row["task_id"], row.get("run"))), None))
                if a is None:
                    continue
                records = None
                for c in row.get("trace_claims") or []:
                    if c.get("problem") != "record cut":
                        continue
                    if records is None:
                        records = [render(a.get("tool_calls") or [], budget=b) for b in VIEWS]
                    found = {cut_citation(c.get("evidence") or "", a.get("transcript") or "", r) for r in records}
                    outcome = next(o for o in OUTCOMES if o in found)
                    grader = row.get("judge_model") or label
                    counts[(run.name, grader)][outcome] += 1
                    if outcome in ("elsewhere", "invented") and len(examples[outcome]) < 5:
                        examples[outcome].append((run.name, grader, row["task_id"], (c.get("evidence") or "")[:160]))
    print(f"{'run':32} {'grader':14} " + " ".join(f"{o:>9}" for o in OUTCOMES))
    for (run, grader), c in sorted(counts.items()):
        print(f"{run:32} {grader:14} " + " ".join(f"{c[o]:9}" for o in OUTCOMES))
    total = sum(counts.values(), Counter())
    print(f"{'all':47} " + " ".join(f"{total[o]:9}" for o in OUTCOMES))
    for outcome, rows in examples.items():
        print(f"\n{outcome}, first {len(rows)}:")
        for r in rows:
            print(f"  {r[0]} {r[1]} {r[2]}: {r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
