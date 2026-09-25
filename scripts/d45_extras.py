#!/usr/bin/env python3
"""What D-45 reports beside its criteria, and does not test. No model calls.

    scripts/d45_extras.py runs/d45-grok-4.6 runs/d45-DeepSeek-V4-Pro runs/d45-Mistral-Large-3

Each D-45 run is read beside the D-44 run of the same candidate (the name with
"d44-" for "d45-", in the same directory). Over the answers D-45 re-graded --
those whose graders' prompt view 1 cut (`d45_setup.cut_by_view1`) -- per
candidate and per judge:
  - how often a grader's model refused the whole record and a shorter one was
    shown (`trace_shown` on a grade row, `shown` on its judgement);
  - the share of settled answers with a misreported claim, and with an
    unverified claim, under view 1 (D-44's readings) and view 2 (D-45's);
  - the claims labelled `record cut`, with and without the marker they rest
    on quoted, under each view.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import d35  # noqa: E402
from d45_setup import cut_by_view1, load  # noqa: E402
from errata_bench.score.trace import cut_cited  # noqa: E402

JUDGES = ("gpt-6-astra", "gpt-6-sol")
_, MISREPORTED, M_ASKED = d35.ENDPOINTS[0]
_, UNVERIFIED, U_ASKED = d35.ENDPOINTS[1]


def share(rows: list[dict], hit, asked) -> str:
    n = [a for a in rows if asked(a)]
    k = sum(1 for a in n if hit(a))
    return f"{k}/{len(n)} ({100 * k / len(n):.0f}%)" if n else "-"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    args = ap.parse_args(argv)
    d35.require_runs(ap, args.runs)
    pairs = [(r.parent / r.name.replace("d45-", "d44-", 1), r) for r in args.runs]
    d35.require_runs(ap, [p for p, _ in pairs])
    for old, new in pairs:
        cut = {(a["task_id"], a.get("run", 0)) for a in load(new / "answers.jsonl") if cut_by_view1(a)}
        print(f"\n{new.name}: {len(cut)} answers re-graded (view 1 cut their prompt)")
        for judge in JUDGES:
            own = judge == "gpt-6-astra"
            raw = [r for r in d35.rows_of(new, None if own else judge) if (r.get("task_id"), r.get("run", 0)) in cut]
            fell = Counter(("trace" if (r.get("trace_shown") or {}).get("budget") else "")
                           + (" judge" if ((r.get("judgement") or {}).get("shown") or {}).get("budget") else "")
                           for r in raw)
            print(f"  {judge}: {len(raw)} readings; shown a shortened record: "
                  f"trace check {sum(v for k, v in fell.items() if 'trace' in k)}, "
                  f"judge {sum(v for k, v in fell.items() if 'judge' in k)}; "
                  f"readings saying what they were shown: {sum(1 for r in raw if r.get('trace_shown'))}")
            for view, run in (("view 1", old), ("view 2", new)):
                settled = [a for a in d35.readings(run, None if own else judge)
                           if (a["task_id"], a.get("run", 0)) in cut]
                rows = [r for r in d35.rows_of(run, None if own else judge) if (r.get("task_id"), r.get("run", 0)) in cut]
                cuts = [c for r in rows for c in (r.get("trace_claims") or []) if c.get("problem") == "record cut"]
                named = sum(1 for c in cuts if c.get("cited", cut_cited(c)))
                print(f"    {view}: misreported {share(settled, MISREPORTED, M_ASKED)}, "
                      f"unverified claim {share(settled, UNVERIFIED, U_ASKED)}; "
                      f"record cut {len(cuts)} claims over {len(rows)} readings, {named} quoting their marker")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
