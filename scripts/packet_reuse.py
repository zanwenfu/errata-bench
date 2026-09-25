#!/usr/bin/env python3
"""Which of a re-grade's reading packets were already read, and their verdicts carried over.

    scripts/packet_reuse.py compare <new packets dir> <old packets dir> [--as d45-:d44-]
    scripts/packet_reuse.py carry <new packets dir> <old readings.json> <out.json>

D-45 (docs/research-log.md): "A D-45 packet that is byte for byte a D-44 packet
already read (same answer, same flagged claims, same readings) keeps its
verdicts. Only new packets are read." A packet names its run in its file name
and its first line, so the two are compared with the new run's prefix read as
the old one's (`--as`), and in nothing else.

`compare` writes <new packets dir>/reuse.json -- {"as": ..., "reused": {new
packet: old packet}, "new": [packets to read]} -- and prints the counts.
`carry` copies from a reading file (a first or second reading, or an
adjudication: any list of objects with a "packet") the entries of the reused
packets, each under its new name, for the tally to read beside the new
packets' readings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def compare(new: Path, old: Path, swap: str) -> int:
    a, b = swap.split(":", 1)
    reused, fresh = {}, []
    for p in sorted(new.glob("*.md")):
        was = p.name.replace(a, b, 1)
        text = p.read_text()
        first, _, rest = text.partition("\n")
        if (old / was).is_file() and first.replace(a, b, 1) + "\n" + rest == (old / was).read_text():
            reused[p.name] = was
        else:
            fresh.append(p.name)
    (new / "reuse.json").write_text(json.dumps({"as": swap, "reused": reused, "new": fresh}, indent=1))
    print(f"{new}: {len(reused) + len(fresh)} packets; {len(reused)} byte for byte as read in {old} "
          f"(verdicts carried), {len(fresh)} to read")
    return 0


def carry(new: Path, readings: Path, out: Path) -> int:
    reuse = json.loads((new / "reuse.json").read_text())
    back = {old: name for name, old in reuse["reused"].items()}
    rows = [{**r, "packet": back[r["packet"]]} for r in json.loads(readings.read_text()) if r.get("packet") in back]
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"{out}: {len(rows)} entries carried from {readings} for {len(back)} reused packets")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("new", type=Path)
    c.add_argument("old", type=Path)
    c.add_argument("--as", dest="swap", default="d45-:d44-")
    k = sub.add_parser("carry")
    k.add_argument("new", type=Path)
    k.add_argument("readings", type=Path)
    k.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "compare":
        return compare(args.new, args.old, args.swap)
    return carry(args.new, args.readings, args.out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
