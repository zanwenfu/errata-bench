#!/usr/bin/env python3
"""Tally a reading of the answers the judge did NOT flag, for what it missed.

    scripts/misses_tally.py <sample.json> <first dir> --second <dir> [--adjudicated <file>]
                            [--runs <run dir>... --rule majority --flag-precision 33/36] [--out <file.md>]

`judge_misses_sample.py` draws the answers and `flag_tally.py`'s format carries the
readings, one verdict per answer: "real" is a miss (the answer states as
established something the record does not establish), "false" a correct pass,
"unclear" what the record cannot settle.

With `--runs`, the answers the judge flagged and passed are counted in those runs
(settled by `--rule`, as the sample was drawn), and with `--flag-precision` (the
share of its flags read as right) the share of unverified claims the judge
catches is estimated: right flags / (right flags + missed ones), where the
missed ones are the miss share of the passed answers. It is an estimate from two
samples, and says so.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from annotation_agreement import kappa  # noqa: E402
from flag_tally import load_readings, model_of, tally  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a share, sound at small counts and at zero."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sample", type=Path)
    ap.add_argument("first", type=Path)
    ap.add_argument("--second", type=Path, required=True)
    ap.add_argument("--adjudicated", type=Path)
    ap.add_argument("--runs", type=Path, nargs="*", default=[])
    ap.add_argument("--rule", default="majority")
    ap.add_argument("--flag-precision", help="the share of the judge's flags read as right, as k/n")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sample = json.loads(args.sample.read_text())
    first, second = load_readings(args.first), load_readings(args.second)
    adjudicated = json.loads(args.adjudicated.read_text()) if args.adjudicated else []
    t = tally(sample, first, second, adjudicated)
    if t["unsettled"]:
        print(f"unsettled: {t['unsettled']}: two readings disagree and no adjudication is recorded")
        return 1
    flags = t["flags"]
    by_model: dict[str, Counter] = defaultdict(Counter)
    for f in flags:
        by_model[f["model"]][f["verdict"]] += 1
    n = len(flags)
    missed = sum(f["verdict"] == "real" for f in flags)
    unclear = sum(f["verdict"] == "unclear" for f in flags)
    lo, hi = wilson(missed, n)
    lines = [f"Of {n} answers the judge passed, {missed} state as established something the record does not "
             f"establish (a miss): {100 * missed / n:.0f}%, 95% interval {100 * lo:.0f}% to {100 * hi:.0f}%. "
             f"{unclear} cannot be settled from the record; counted as misses, {missed + unclear} of {n}.", "",
             "| model | answers | missed | unclear | right to pass |", "|---|---|---|---|---|"]
    for m, c in sorted(by_model.items()):
        lines.append(f"| {m} | {sum(c.values())} | {c['real']} | {c['unclear']} | {c['false']} |")
    same = sum(1 for f in flags if f["second"] == f["first"])
    k = kappa([(f["first"] == "real", f["second"] == "real") for f in flags])
    lines += ["", f"The two readings, blind to each other: the same verdict on {same} of {n}; on miss-or-not, "
              f"kappa {k:+.2f}. Adjudicated: {sum(1 for f in flags if f.get('adjudicated'))}."]
    for f in flags:
        if f["verdict"] != "false":
            lines.append(f"- {f['packet']}: {f['verdict']} -- {f['why'][:300]}")
    if args.runs:
        import d35

        _, unverified, asked = d35.ENDPOINTS[1]
        flagged = passed = 0
        for run in args.runs:
            rows = [a for a in d35.readings(run, rule=args.rule) if asked(a)]
            flagged += sum(1 for a in rows if unverified(a))
            passed += sum(1 for a in rows if not unverified(a))
        lines += ["", f"In those runs, settled by {args.rule}: {flagged} answers flagged, {passed} passed."]
        if args.flag_precision:
            right, of = (int(x) for x in args.flag_precision.split("/"))
            caught = flagged * right / of
            for label, rate in (("as read", missed / n), ("with the unclear counted as misses", (missed + unclear) / n)):
                lost = passed * rate
                lines.append(f"- Estimated share of unverified claims the judge catches, {label}: "
                             f"{caught:.0f} / ({caught:.0f} + {lost:.0f}) = {100 * caught / (caught + lost):.0f}% "
                             f"(flags {right}/{of} right; an estimate from two samples).")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
