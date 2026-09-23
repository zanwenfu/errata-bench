"""Paired comparisons between candidate run directories, as D-35 fixes them.

    .venv/bin/python scripts/paired_tests.py runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code \\
        runs/grid1-DeepSeek-V4-Pro [--judge claude-opus-5] [--runs 0,1,2]

Readings are folded by the harness's own `settled`; only scoreable attempts of
admitted tasks count, admission being the run directory's own (calibration and
controls, i.e. the benchmark judge's) whichever judge's grades are read, so the
two judges are compared on one task set. `--judge NAME` reads that judge's
grades from `<run>/rejudge/<NAME>/attempts.jsonl` instead of the run's own.

For every endpoint, each task gets a per-task rate over the attempts where the
question could be asked; two candidates are compared on the tasks both have a
rate for. The test is an exact two-sided paired sign-flip permutation test on
the per-task differences, the effect is their mean with a task-cluster
bootstrap 95% interval, and p-values are Holm-adjusted over the pairs within
each endpoint. The any-attempt sign test used for the first slice (R-34) is
printed beside it for continuity.
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.instrument.control import controlled  # noqa: E402
from errata_bench.score.judge import PASSING, can_be_scored, outcome_of  # noqa: E402
from errata_bench.score.rejudge import _passed, judge_paths, settled  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402

# (name, the property, whether the question could be asked of this answer)
ENDPOINTS = [
    ("PRIMARY  trace check: a claim the record does not support",
     lambda a: a.get("claims_match_trace") is False,
     lambda a: a.get("claims_match_trace") is not None),
    ("judge: makes an unverified claim",
     lambda a: bool((a.get("judgement") or {}).get("makes_unverified_claim")),
     lambda a: (a.get("judgement") or {}).get("makes_unverified_claim") is not None),
    ("clean pass",
     lambda a: _passed(a, PASSING),
     lambda a: True),
]


# ---- statistics, exact where it can be --------------------------------------

def sign_flip_p(diffs: list[Fraction]) -> float:
    """Exact two-sided p for the sum of paired differences under sign flips.

    Built as a distribution over the attainable sums rather than by enumerating
    2^n assignments: the differences are multiples of small fractions, so the
    number of distinct sums stays small. Zero differences carry no sign.
    """
    nz = [abs(d) for d in diffs if d != 0]
    if not nz:
        return 1.0
    dist: dict[Fraction, int] = {Fraction(0): 1}
    for d in nz:
        nxt: dict[Fraction, int] = defaultdict(int)
        for s, c in dist.items():
            nxt[s + d] += c
            nxt[s - d] += c
        dist = nxt
    observed = abs(sum(diffs))
    extreme = sum(c for s, c in dist.items() if abs(s) >= observed)
    return extreme / 2 ** len(nz)


def any_sign_p(b: int, c: int) -> float:
    """Exact two-sided sign test on discordant counts (the R-34 test)."""
    n = b + c
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def bootstrap_ci(diffs: list[float], resamples: int, seed: int) -> tuple[float, float]:
    """Percentile 95% interval for the mean difference, resampling tasks."""
    if not diffs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return means[int(0.025 * resamples)], means[min(resamples - 1, int(0.975 * resamples))]


def holm(ps: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order."""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adjusted = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adjusted[i] = running
    return adjusted


# ---- reading the rows ---------------------------------------------------------

def graded(run: Path, judge: str | None, runs: set[int] | None) -> list[dict]:
    paths = Paths(run)
    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    source = judge_paths(run, judge).attempts if judge else paths.attempts
    rows = [a for a in settled(load(source)) if a.get("task_id") in admitted and a.get("scoreable")]
    if runs is not None:
        rows = [a for a in rows if a.get("run") in runs]
    return rows


def per_task(rows: list[dict], has, asked) -> dict[str, Fraction]:
    hits: dict[str, list[bool]] = defaultdict(list)
    for a in rows:
        if asked(a):
            hits[a["task_id"]].append(bool(has(a)))
    return {t: Fraction(sum(v), len(v)) for t, v in hits.items() if v}


def any_attempt(rows: list[dict], has, asked) -> dict[str, bool]:
    out: dict[str, list[bool]] = defaultdict(list)
    for a in rows:
        if asked(a):
            out[a["task_id"]].append(bool(has(a)))
    return {t: any(v) for t, v in out.items()}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--judge", help="read this judge's re-grades instead of the run's own grading")
    ap.add_argument("--runs", dest="attempts", help="restrict to these attempt numbers, e.g. 0 or 0,1,2")
    ap.add_argument("--resamples", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    if len(args.runs) < 2:
        ap.error("give at least two run directories")
    which = {int(x) for x in args.attempts.split(",")} if args.attempts else None
    data = {r: graded(r, args.judge, which) for r in args.runs}

    print(f"judge: {args.judge or 'each run directory own grading'}; "
          f"attempts: {sorted(which) if which is not None else 'all'}")
    for r, rows in data.items():
        empty = sum(outcome_of(a) == "no_answer" for a in rows)
        print(f"  {r.name}: {len(rows)} scoreable answers over "
              f"{len({a['task_id'] for a in rows})} tasks; empty answers {empty}/{len(rows)}")
    print()

    for name, has, asked in ENDPOINTS:
        print(name)
        tables = {r: per_task(rows, has, asked) for r, rows in data.items()}
        anys = {r: any_attempt(rows, has, asked) for r, rows in data.items()}
        for r in args.runs:
            t = tables[r]
            mean = float(sum(t.values()) / len(t)) if t else float("nan")
            print(f"  {r.name}: mean per-task rate {mean:.3f} over {len(t)} tasks")
        pairs = list(itertools.combinations(args.runs, 2))
        results = []
        for x, y in pairs:
            shared = sorted(set(tables[x]) & set(tables[y]))
            diffs = [tables[x][t] - tables[y][t] for t in shared]
            p = sign_flip_p(diffs)
            fd = [float(d) for d in diffs]
            lo, hi = bootstrap_ci(fd, args.resamples, args.seed)
            ashared = sorted(set(anys[x]) & set(anys[y]))
            b = sum(anys[x][t] and not anys[y][t] for t in ashared)
            c = sum(anys[y][t] and not anys[x][t] for t in ashared)
            results.append((x, y, len(shared), sum(fd) / len(fd) if fd else float("nan"),
                            lo, hi, p, b, c, any_sign_p(b, c)))
        adjusted = holm([res[6] for res in results])
        for (x, y, n, mean, lo, hi, p, b, c, pa), padj in zip(results, adjusted):
            print(f"  {x.name} - {y.name}: {n} tasks, mean difference {mean:+.3f} "
                  f"[{lo:+.3f}, {hi:+.3f}], sign-flip p {p:.4f}, Holm {padj:.4f}; "
                  f"any-attempt {b} to {c}, p {pa:.3f}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
