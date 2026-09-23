"""Paired comparisons between candidate run directories, task by task.

    .venv/bin/python scripts/paired_tests.py runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code ...

Attempts on one task are correlated, so models are compared on the tasks they
share: for each pair, count the tasks where only one of the two shows the
property, and apply an exact two-sided sign test to those discordant tasks.
Settled readings only, scoreable attempts only, and with more than one attempt
per task a task counts as showing the property if any of its attempts does.
"""

from __future__ import annotations

import itertools
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.rejudge import settled  # noqa: E402
from errata_bench.store import load  # noqa: E402

READINGS = [
    ("trace check: a claim the record does not support",
     lambda a: a.get("claims_match_trace") is False, lambda a: a.get("claims_match_trace") is not None),
    ("judge: an unverified claim",
     lambda a: bool((a.get("judgement") or {}).get("makes_unverified_claim")),
     lambda a: (a.get("judgement") or {}).get("makes_unverified_claim") is not None),
    ("clean pass", lambda a: bool(a.get("passed")), lambda a: True),
]


def sign_p(b: int, c: int) -> float:
    n = b + c
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def per_task(run: Path, has, asked) -> dict[str, bool]:
    out: dict[str, list[bool]] = defaultdict(list)
    for a in settled(load(Path(run) / "attempts.jsonl")):
        if a.get("scoreable") and asked(a):
            out[a["task_id"]].append(has(a))
    return {t: any(v) for t, v in out.items()}


def main(argv: list[str]) -> int:
    runs = [Path(a) for a in argv]
    if len(runs) < 2:
        print(__doc__)
        return 2
    for name, has, asked in READINGS:
        print(name)
        tables = {r: per_task(r, has, asked) for r in runs}
        for x, y in itertools.combinations(runs, 2):
            shared = sorted(set(tables[x]) & set(tables[y]))
            b = sum(tables[x][t] and not tables[y][t] for t in shared)
            c = sum(tables[y][t] and not tables[x][t] for t in shared)
            print(f"  {x.name} vs {y.name}: {len(shared)} shared tasks; only the first {b}, "
                  f"only the second {c}; exact sign test p = {sign_p(b, c):.3f}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
