#!/usr/bin/env python3
"""A Harbor run's spend so far, from the token counts it recorded, at Azure list prices.

    python scripts/harbor_spend.py --jobs <job>... [--graded <grading out>...] [--admitted <run>...]
                                   [--ledger <file>] [--stop USD]

The candidates' spend is each trial's `agent/reference-agent.json` usage (the
reference agent's; another agent's is not counted here), priced by the model
it ran. The grading spend is each reading's judge and trace-check usage in
`attempts.jsonl`, priced by the judge's model. A reading that recorded no
usage is counted apart, not priced; an answer with nothing to read makes no
call and is not counted there. The spend before any Harbor job -- finding
tasks, screening them, the judge's admission, its gate and a re-judge's checks
-- is each row's own `usage` in a run folder given to --admitted, priced by the
model that read it, the failed rows a stage moved aside when it ran again
(`<stage>.dropped.jsonl`) included; rows written before 09-30 recorded none and
are counted apart. A model not on the price list is priced as gpt-6-astra, the dearest, and
named. Exits 3 when the total reaches --stop, so a guard loop can stop the run
(`scripts/harbor-guard.sh`). Exits 4, pricing nothing, unless each --jobs
folder is a Harbor job (its `config.json`), each --graded folder a grading run
(its `tasks.jsonl`) or a re-judge's (`<run>/rejudge/<judge>`), and each
--admitted folder a run folder (its input, or rows of a stage that calls a
model): a folder of jobs, a pattern that matched nothing, or the wrong folder
priced $0 for good and said so quietly (09-28 reviews).

--ledger keeps each price once it is seen, a trial's by its folder and record
and a reading's by its folder and row, so one ledger carries the whole run:
Harbor deletes a failed trial's folder when it runs it again, and a later
phase's guard may not be given an earlier grading folder. What a pass never
saw is still missed: a failed attempt Harbor ran again between two passes
(about 9% of grok-4.6's spend on the v1 subset). No model calls, no network.

Prices as `scripts/d40_spend.py` gives them (09-24), with gpt-6's uncached input
at its cache-write price, which Azure bills (the "Cd Wr" meter) on tokens the
usage does not tell apart: an upper bound there. Not modelled: the long-context
tiers of gpt-6-astra and grok-4.6, which bill at up to twice the price past a
length; no such meter was billed through 09-28, so read the run's meters after
it (`az consumption usage list`).
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
# USD per 1M tokens written to gpt-6's prompt cache (Global Standard, 09-28; gpt-6-sol's as listed 09-30).
CACHE_WRITE = {"gpt-6-astra": 12.50, "gpt-6-sol": 2.50}
# Rows of an answer with nothing to read: no judge was called for them.
NO_READING = ("no_answer", "gave_up", "no_context")


def priced(model: str, usage: dict | None, unpriced: set[str]) -> float:
    bare = str(model or "").split("/")[-1]
    if bare not in PRICE:
        unpriced.add(bare)
        bare = "gpt-6-astra"
    usd = _d40.usd(bare, usage, reasoning_apart=bare in REASONING_APART)
    if bare in CACHE_WRITE and usage:
        uncached = (usage.get("input_tokens") or 0) - (usage.get("cached_tokens") or 0)
        usd += uncached * (CACHE_WRITE[bare] - PRICE[bare][0]) / 1e6
    return usd


def _ledger(ledger: Path | None, kind: str) -> dict[str, float]:
    """The prices of this kind the ledger holds; rows from before kinds were written are the agents'."""
    if ledger is None or not ledger.is_file():
        return {}
    return {row["key"]: float(row["usd"]) for row in _d40.rows(ledger) if row.get("kind", "candidate") == kind}


def _keep(ledger: Path | None, kind: str, new: list[dict]) -> None:
    if ledger is not None and new:
        with open(ledger, "a") as fh:
            fh.writelines(json.dumps({**r, "kind": kind}) + "\n" for r in new)


def candidates(jobs: list[Path], unpriced: set[str], ledger: Path | None = None) -> tuple[float, int]:
    """The agents' spend: every trial's record now on disk, and every one the ledger saw before."""
    import hashlib

    seen = _ledger(ledger, "candidate")
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
    _keep(ledger, "candidate", new)
    return sum(seen.values()), len(seen)


def grading(outs: list[Path], unpriced: set[str], ledger: Path | None = None) -> tuple[float, int, int]:
    """The judge's spend: every reading now on disk, and every one the ledger saw before, in any folder."""
    import hashlib

    seen = _ledger(ledger, "grading")
    new, missing = [], 0
    for out in outs:
        for row in _d40.rows(out / "attempts.jsonl"):
            if row.get("error") or row.get("outcome") in NO_READING:
                continue
            key = f"{out.resolve()}:{hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]}"
            if key in seen:
                continue
            judge = row.get("judge_model") or ""
            usd = 0.0
            for part in ("judge_usage", "trace_usage"):
                if row.get(part):
                    usd += priced(judge, row[part], unpriced)
                else:
                    missing += 1
            seen[key] = usd
            new.append({"key": key, "usd": round(usd, 6)})
    _keep(ledger, "grading", new)
    return sum(seen.values()), len(seen), missing


# The rows the steps before a pilot write, each with its own model calls' usage
# (`store.append_used`): finding tasks, screening, the admission, the gate, a
# re-judge's instrument checks. Each priced by the model that read it.
ADMITTED_FILES = ("triaged.jsonl", "readings.jsonl", "trajectories.jsonl", "signatures.jsonl", "screened.jsonl",
                  "calibration.jsonl", "controls.jsonl", "gate.jsonl", "instrument.jsonl")
# A run folder holds one of these from its start, before any call is paid: the
# moments a build reads, the tasks an admission reads, a re-judge's served record.
RUN_FILES = ("moments.jsonl", "tasks.jsonl", "served.jsonl") + ADMITTED_FILES


def dropped(name: str) -> str:
    """Where a stage keeps the failed rows it drops when it runs again (`store.rows.dropped_rows`).

    Their usage is spend: deleted with them, a run started again before the
    guard's next tally lost it (10-01 review). Priced under the stage's own file,
    so a row the ledger saw there before it moved is not priced twice.
    """
    return name.removesuffix(".jsonl") + ".dropped.jsonl"


def admitted(folders: list[Path], unpriced: set[str], ledger: Path | None = None) -> tuple[float, int, int]:
    """Screening's and the admission's spend: every row now on disk, and every one the ledger saw before."""
    import hashlib

    seen = _ledger(ledger, "admitted")
    new, missing = [], 0
    for folder in folders:
        for name in ADMITTED_FILES:
            for row in [*_d40.rows(folder / name), *_d40.rows(folder / dropped(name))]:
                key = (f"{folder.resolve()}/{name}:"
                       f"{hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]}")
                if key in seen:
                    continue
                read_by = row.get("screen_model") or row.get("judge_model") or row.get("find_model") or ""
                used = row.get("usage")
                if used is None:
                    # Unmetered: a row a model read before metering (it names the
                    # model), or one written outside a meter. A row naming no model
                    # and carrying no usage is input, read by none (a re-screen's
                    # signatures), and costs nothing; nor is a failure from before
                    # metering counted, which is asked again (10-01 review).
                    missing += "usage" in row or (bool(read_by) and not row.get("error"))
                    continue
                # Metered with no call finished ({}): nothing to price, and nothing missing.
                usd = priced(read_by, used, unpriced) if used else 0.0
                seen[key] = usd
                new.append({"key": key, "usd": round(usd, 6)})
    _keep(ledger, "admitted", new)
    return sum(seen.values()), len(seen), missing


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", type=Path, nargs="*", default=[])
    ap.add_argument("--graded", type=Path, nargs="*", default=[])
    ap.add_argument("--admitted", type=Path, nargs="*", default=[],
                    help="run folders of the stages before Harbor (finding, screening, admission, gate) or a "
                         "re-judge's, priced by the usage each row records")
    ap.add_argument("--ledger", type=Path)
    ap.add_argument("--stop", type=float, default=float("inf"))
    args = ap.parse_args(argv)
    # A job folder holds Harbor's config.json from its start, a grading folder
    # its tasks.jsonl (a re-judge's is <run>/rejudge/<judge>), and a run folder
    # its input; any other folder would price $0 for good.
    wrong = ([f"{j} (not a Harbor job: no config.json)" for j in args.jobs if not (j / "config.json").is_file()]
             + [f"{g} (not a grading run: no tasks.jsonl, and not a re-judge's folder)" for g in args.graded
                if not ((g / "tasks.jsonl").is_file() or (g.is_dir() and g.resolve().parent.name == "rejudge"))]
             + [f"{a} (not a run folder: none of {', '.join(RUN_FILES)})" for a in args.admitted
                if not any((a / name).is_file() for name in RUN_FILES)])
    # Screening and an admission are paid without a Harbor job (09-30 review).
    if wrong or not (args.jobs or args.graded or args.admitted):
        print(f"refused: {'; '.join(wrong) or 'no folder given'}; nothing priced", file=sys.stderr)
        return 4
    unpriced: set[str] = set()
    cand, trials = candidates(args.jobs, unpriced, args.ledger)
    grade, readings, halves = grading(args.graded, unpriced, args.ledger)
    admit, admit_rows, unmetered = admitted(args.admitted, unpriced, args.ledger)
    total = cand + grade + admit
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{now} candidates ${cand:,.2f} over {trials} trials; grading ${grade:,.2f} over {readings} readings"
          + (f" ({halves} halves with no usage recorded, not priced)" if halves else "")
          + (f"; finding, screening and admission ${admit:,.2f} over {admit_rows} rows"
             + (f" ({unmetered} with no usage recorded, not priced)" if unmetered else "") if args.admitted else "")
          + f"; total ${total:,.2f} of a ${args.stop:,.0f} stop line"
          + (f"; priced as gpt-6-astra, not on the list: {', '.join(sorted(unpriced))}" if unpriced else ""))
    return 3 if total >= args.stop else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
