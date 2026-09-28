#!/usr/bin/env python3
"""Every stored `record cut` citation, checked against what its grader was shown (#4).

    python scripts/audit_cut_citations.py runs/<run>...

A `record cut` claim is excused from `misreported` when it quotes the cut it
rests on. That was checked for the quote's form only, so a grader could quote a
marker it was never shown (#4). This reads each graded row's trace claims and
rebuilds what its grader was shown -- the answer's conversation and its record
-- to class each citation as `trace.cut_citation` does: shown, elsewhere,
invented, planted or none, under the current rules (`trace.RULES`). A row that
records its view (`trace_shown`, since view 2) is classed against that view;
one that does not is classed against every view a grader could have been shown
-- the whole record (view 2), each fallback, and view 1's bound of 24,000
characters, the smallest fallback -- and given the best of them. The renderer
has changed since view 1, so a view-1 marker can come out with another number
here and read as invented. Each claim is also compared with the verdict stored
on its row (`cited`), and each reading with its stored `misreported`: what the
current rules would change, stored grades being kept as they were. These rows
were stored under record 2, whose outputs hold real storage cuts, so no marker
is taken as planted. No model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.trace import (CONTEXT_CHARS, EXCUSED, FALLBACKS, RULES, cut_citation, render,  # noqa: E402
                                      shown_conversation)

OUTCOMES = ("shown", "elsewhere", "misplaced", "invented", "planted", "none")
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
    changed: dict[tuple, Counter] = defaultdict(Counter)
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
                grader = row.get("judge_model") or label
                flips = 0
                before = after = False
                for c in row.get("trace_claims") or []:
                    if not c.get("supported") and c.get("problem") not in ("out of date", "misread", "record cut"):
                        before = after = True
                    if c.get("problem") != "record cut":
                        continue
                    if records is None:
                        view = row.get("trace_shown")
                        budgets = [view.get("budget")] if isinstance(view, dict) and "budget" in view else VIEWS
                        transcript = a.get("transcript") or ""
                        records = [(shown_conversation(transcript, b or CONTEXT_CHARS),
                                    render(a.get("tool_calls") or [], budget=b)) for b in budgets]
                    found = {cut_citation(c.get("evidence") or "", conv, r) for conv, r in records}
                    outcome = next(o for o in OUTCOMES if o in found)
                    counts[(run.name, grader)][outcome] += 1
                    if not c.get("supported"):
                        before |= not c.get("cited", True)
                        after |= outcome not in EXCUSED
                    if outcome in ("elsewhere", "misplaced", "invented", "planted") and len(examples[outcome]) < 5:
                        examples[outcome].append((run.name, grader, row["task_id"], (c.get("evidence") or "")[:160]))
                    if "cited" in c and bool(c["cited"]) != (outcome in EXCUSED):
                        changed[(run.name, grader)]["claims now " + ("excused" if outcome in EXCUSED else "not excused")] += 1
                        flips += 1
                if flips and row.get("misreported") is not None:
                    changed[(run.name, grader)]["readings with a changed claim"] += 1
                    if before != after:
                        changed[(run.name, grader)][f"readings whose misreported would read {after}"] += 1
    print(f"{'run':32} {'grader':14} " + " ".join(f"{o:>9}" for o in OUTCOMES))
    for (run, grader), c in sorted(counts.items()):
        print(f"{run:32} {grader:14} " + " ".join(f"{c[o]:9}" for o in OUTCOMES))
    total = sum(counts.values(), Counter())
    print(f"{'all':47} " + " ".join(f"{total[o]:9}" for o in OUTCOMES))
    print(f"\nAgainst the verdicts stored on the rows (rules {RULES}, excusing {', '.join(EXCUSED)}):")
    for key, c in sorted(changed.items()):
        print(f"  {key[0]} {key[1]}: " + ", ".join(f"{v} {k}" for k, v in sorted(c.items())))
    if not changed:
        print("  none")
    for outcome, rows in examples.items():
        print(f"\n{outcome}, first {len(rows)}:")
        for r in rows:
            print(f"  {r[0]} {r[1]} {r[2]}: {r[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
