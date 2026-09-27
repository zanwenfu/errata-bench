#!/usr/bin/env python3
"""Tally a re-reading of a criterion's unsettled items, and what the criterion would read with it.

    scripts/reread_tally.py <re-read dir> --flags <tally dir> --judge <tally dir> [--out <file.md>]

B-269 (09-27): the reading packets cut every output at 1,500 characters that the
graders read whole, and the rubric sends a flag resting on the cut part to
"unclear", which counts against the grader. The items a criterion's reading left
unclear are read again from whole packets, twice and blind, and each disagreement
settled against the record (<re-read dir>/items.json, readings/, adjudicated.json).

Exploratory: the registered result stands as it was read. This prints each
item's settled verdict and each criterion's share with the re-read verdicts in
place of the old ones, the flags per flag and per answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flag_tally import REAL, load_readings, tally  # noqa: E402


def settled(where: Path) -> tuple[list[dict], list[str]]:
    """Each item with both readings and its verdict; and the items left unsettled."""
    items = json.loads((where / "items.json").read_text())
    readings = [json.loads(f.read_text()) for f in sorted((where / "readings").glob("*.json"))]
    adjudicated = {a["item"]: a for a in json.loads((where / "adjudicated.json").read_text())} \
        if (where / "adjudicated.json").exists() else {}
    out, open_ = [], []
    for it in items:
        said = [next(r for r in rs if r["item"] == it["item"])["verdict"] for rs in readings]
        verdict = said[0] if len(set(said)) == 1 else (adjudicated.get(it["item"]) or {}).get("verdict")
        if verdict is None:
            open_.append(f"item {it['item']}: {it['packet']}")
        out.append({**it, "readings": said, "verdict": verdict,
                    "adjudicated": len(set(said)) > 1})
    return out, open_


def with_reread(res: Path, items: list[dict], kind: str) -> tuple[str, str, str, str]:
    """A criterion's share as read, and with the re-read verdicts in place."""
    sample = json.loads((res / "sample.json").read_text())
    first = load_readings(res / "first")
    second = load_readings(res / "second" / "readings")
    adj = [a for f in sorted((res / "second").glob("adjudicated*.json")) for a in json.loads(f.read_text())]
    flags = tally(sample, first, second, adj)["flags"]
    new = {(i["packet"], i["claim_id"]): i["verdict"] for i in items if i["kind"] == kind}
    shares = []
    for replaced in (False, True):
        verdicts = [(f["packet"], new.get((f["packet"], f["id"]), f["verdict"]) if replaced else f["verdict"])
                    for f in flags]
        real = sum(v in REAL for _, v in verdicts)
        answers = defaultdict(bool)
        for p, v in verdicts:
            answers[p] |= v in REAL
        shares.append((f"{real}/{len(verdicts)} ({100 * real / len(verdicts):.0f}%)",
                       f"{sum(answers.values())}/{len(answers)} ({100 * sum(answers.values()) / len(answers):.0f}%)"))
    return shares[0][0], shares[1][0], shares[0][1], shares[1][1]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reread", type=Path)
    ap.add_argument("--flags", type=Path, required=True, help="the trace check's tally directory")
    ap.add_argument("--judge", type=Path, required=True, help="the judge's tally directory")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    items, open_ = settled(args.reread)
    if open_:
        print("unsettled: two readings disagree and no adjudication is recorded:\n  " + "\n  ".join(open_))
        return 1
    lines = ["| item | kind | packet | readings | verdict |", "|---|---|---|---|---|"]
    for i in items:
        lines.append(f"| {i['item']} | {i['kind']} | {i['packet']} | {' / '.join(i['readings'])} | "
                     f"{i['verdict']}{' (adjudicated)' if i['adjudicated'] else ''} |")
    lines.append("")
    for kind, res, name in (("flag", args.flags, "trace check's flags"), ("judge", args.judge, "judge's calls")):
        before, after, before_a, after_a = with_reread(res, items, kind)
        lines.append(f"- The {name} real: {before} as read; {after} with the re-read. "
                     f"Answers with a real one: {before_a} as read; {after_a} with the re-read.")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
