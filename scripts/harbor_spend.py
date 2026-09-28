#!/usr/bin/env python3
"""A Harbor run's spend so far, from the token counts it recorded, at Azure list prices.

    python scripts/harbor_spend.py --jobs <job>... [--graded <grading out>...] [--ledger <file>] [--stop USD]

The candidates' spend is each trial's `agent/reference-agent.json` usage (the
reference agent's; another agent's is not counted here), priced by the model
it ran. The grading spend is each reading's judge and trace-check usage in
`attempts.jsonl`, priced by the judge's model. A reading that recorded no
usage is counted apart, not priced. A model not on the price list is priced as
gpt-6-astra, the dearest, and named. Exits 3 when the total reaches --stop, so
a guard loop can stop the run (`scripts/harbor-guard.sh`); exits 4, pricing
nothing, when a job folder given does not exist -- a pattern that matched
nothing priced $0 and said so quietly (09-28 review).

--ledger keeps each trial's price once it is seen, by its folder and record:
Harbor deletes a failed trial's folder when it runs it again, and the spend of
the failed attempt went with it. A trial retried between two passes is still
missed; the stop line keeps room for that. No model calls, no network. Prices
as `scripts/d40_spend.py` gives them (09-24).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("d40_spend", Path(__file__).resolve().parent / "d40_spend.py")
_d40 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_d40)
PRICE = _d40.PRICE
# grok reports its reasoning tokens apart from its output; they are billed as output.
REASONING_APART = {"grok-4.6"}


def priced(model: str, usage: dict | None, unpriced: set[str]) -> float:
    bare = str(model or "").split("/")[-1]
    if bare not in PRICE:
        unpriced.add(bare)
        bare = "gpt-6-astra"
    return _d40.usd(bare, usage, reasoning_apart=bare in REASONING_APART)


def candidates(jobs: list[Path], unpriced: set[str], ledger: Path | None = None) -> tuple[float, int]:
    """The agents' spend: every trial's record now on disk, and every one the ledger saw before."""
    import hashlib

    seen: dict[str, float] = {}
    if ledger is not None and ledger.is_file():
        for row in _d40.rows(ledger):
            seen[row["key"]] = float(row["usd"])
    new = []
    for job in jobs:
        for record in sorted(job.glob("*/agent/reference-agent.json")):
            try:
                raw = record.read_bytes()
                ref = json.loads(raw)
            except (OSError, ValueError):
                continue
            # The folder as the file system names it, however the job was spelt.
            key = f"{record.parent.parent.resolve()}:{hashlib.sha256(raw).hexdigest()[:16]}"
            if key not in seen:
                seen[key] = priced(ref.get("model") or "", ref.get("usage"), unpriced)
                new.append({"key": key, "usd": round(seen[key], 6)})
    if ledger is not None and new:
        with open(ledger, "a") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in new)
    return sum(seen.values()), len(seen)


def grading(outs: list[Path], unpriced: set[str]) -> tuple[float, int, int]:
    total, readings, missing = 0.0, 0, 0
    for out in outs:
        # Grading may not have begun; its folder is made when it does.
        for row in _d40.rows(out / "attempts.jsonl"):
            if row.get("error"):
                continue
            readings += 1
            judge = row.get("judge_model") or ""
            for key in ("judge_usage", "trace_usage"):
                if row.get(key):
                    total += priced(judge, row[key], unpriced)
                else:
                    missing += 1
    return total, readings, missing


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=Path, nargs="*", default=[])
    ap.add_argument("--graded", type=Path, nargs="*", default=[])
    ap.add_argument("--ledger", type=Path)
    ap.add_argument("--stop", type=float, default=float("inf"))
    args = ap.parse_args(argv)
    missing = [str(j) for j in args.jobs if not j.is_dir()]
    if missing or not args.jobs:
        print(f"refused: no job folder at {', '.join(missing) or '(none given)'}; nothing priced", file=sys.stderr)
        return 4
    unpriced: set[str] = set()
    cand, trials = candidates(args.jobs, unpriced, args.ledger)
    grade, readings, halves = grading(args.graded, unpriced)
    total = cand + grade
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{now} candidates ${cand:,.2f} over {trials} trials; grading ${grade:,.2f} over {readings} readings"
          + (f" ({halves} halves with no usage recorded, not priced)" if halves else "")
          + f"; total ${total:,.2f} of a ${args.stop:,.0f} stop line"
          + (f"; priced as gpt-6-astra, not on the list: {', '.join(sorted(unpriced))}" if unpriced else ""))
    return 3 if total >= args.stop else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
