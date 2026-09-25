#!/usr/bin/env python3
"""Set up D-45's own checks: ask again under view 2 only the check prompts view 1 cut.

    scripts/d45_tests_setup.py <d44 tests dir> <d45 tests dir> [--judge gpt-6-astra]

D-44's first criterion rests on gpt-6-astra's checks in `runs/d44-astratests`:
its calibration, the controls, the instrument checks and the probes, all asked
under view 1 (docs/research-log.md, D-45 and its amendment). As for the answers
(`d45_setup.py`), a check whose prompt view 1 left whole is the same text under
view 2, byte for byte, and its row is copied. A check whose prompt view 1 cut
gets no row, so the re-grade driver, which resumes, asks exactly it:

  calibration           the rejected answer with its calls, or the accepted one with its
  a control             its answer with its calls (only the accepted answer carries any)
  an instrument check   its answer (they carry no calls)
  the probes            all of them, always: the set is no longer D-44's (the
                        clipped-output probe renamed, two added for a stored cut),
                        so the driver asks it once and `probe_runs.py` three times

A copied row is marked `"copied_from"` with the directory it came from. Check rows
record no code version, and the spend guard must not price what D-44 paid for.
The task and admission files are copied as they are. Nothing in the D-44
directory is touched. Prints what is left to ask, and why.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from d45_setup import cut_by_view1, load  # noqa: E402
from errata_bench.instrument.control import CONTROLS  # noqa: E402
from errata_bench.spec import read  # noqa: E402


def is_probe(r: dict) -> bool:
    return str(r.get("control", "")).startswith("probe")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--judge", default="gpt-6-astra")
    args = ap.parse_args(argv)
    if not (args.src / "tasks.jsonl").is_file():
        ap.error(f"not a run directory: {args.src}")
    if args.dst.exists():
        ap.error(f"{args.dst} exists; D-45's checks are set up once")
    tasks = {t.task_id: t for t in read(args.src / "tasks.jsonl")}
    controls = {c.name: c for c in CONTROLS}

    def cut(reply: str, calls: list[dict]) -> list[str]:
        return cut_by_view1({"reply": reply, "tool_calls": calls})

    def why(kind: str, r: dict) -> list[str]:
        """What view 1 left out of this check's prompts; empty when it left out nothing."""
        task = tasks.get(r.get("task_id"))
        if kind == "calibration":
            return ([f"rejected answer's {w}" for w in cut(task.oracle, task.oracle_calls or [])]
                    + [f"accepted answer's {w}" for w in cut(task.criterion, task.criterion_calls or [])])
        if kind == "controls":
            c = controls[r["control"]]
            return cut(c.reply_for(task), c.calls_for(task))
        return cut(r.get("reply") or "", [])

    args.dst.mkdir(parents=True)
    for name in ("tasks", "calibration", "controls", "gate"):
        if (args.src / f"{name}.jsonl").exists():
            shutil.copy2(args.src / f"{name}.jsonl", args.dst / f"{name}.jsonl")
    src, dst = args.src / "rejudge" / args.judge, args.dst / "rejudge" / args.judge
    dst.mkdir(parents=True)
    report, asked = [], {}
    for kind in ("calibration", "controls", "instrument"):
        kept, n_probes = [], 0
        for r in load(src / f"{kind}.jsonl"):
            if is_probe(r):
                n_probes += 1
                continue
            w = why(kind, r)
            if w:
                report.append(f"  asked again: {kind} {r['task_id']}"
                              + (f" {r['control']}" if "control" in r else "") + f" ({', '.join(w)})")
                continue
            kept.append({**r, "copied_from": args.src.name})
        (dst / f"{kind}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept))
        asked[kind] = (len(kept), n_probes)
    print(f"{args.src.name} -> {args.dst.name} ({args.judge}): "
          + "; ".join(f"{k} {n} copied" + (f", {p} probe rows left to ask" if p else "") for k, (n, p) in asked.items())
          + f"; {len(report)} checks view 1 cut, left to ask; the probes' three runs are asked afresh")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
