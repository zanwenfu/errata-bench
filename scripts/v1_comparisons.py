"""The v1 baseline run's registered comparisons (docs/v1-baseline-run.md, "The comparison rule").

    .venv/bin/python scripts/v1_comparisons.py results/v1-baseline/results.json release/v1.0.2-dataset \\
        --out results/v1-baseline/comparisons

Each model's per-task rates come from `results.json` (the report's own
`per_task`), and each task's repository from the dataset (`tasks/<id>/task.json`,
`repo_id`). For each measure, every pair of models is compared on the tasks both
have a rate for:
- the effect: the mean per-task difference, with a 95% interval resampling whole
  repositories;
- the test: exact and two-sided, flipping the sign of each repository's summed
  difference, since tasks from one repository are not independent (entireio/cli
  holds 14 of the 51);
- Holm's correction over the pairs within the measure.

A difference is claimed when the adjusted p is below 0.05. From the claims come
letter groups (models sharing a letter are not shown to differ) and rank ranges;
each model's mean rank within a task is given beside them, and no test rests on
it. The task-level test and interval of D-35 are printed beside the repository
ones, for comparison only.

Writes <out>.json and <out>.md. No model calls, no network.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import string
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

_spec = importlib.util.spec_from_file_location("paired_tests", Path(__file__).resolve().parent / "paired_tests.py")
_pt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pt)
sign_flip_p, bootstrap_ci, cluster_bootstrap_ci, holm = (_pt.sign_flip_p, _pt.bootstrap_ci,
                                                         _pt.cluster_bootstrap_ci, _pt.holm)

MEASURES = ("honest_reports", "fixed", "fixed_and_honest")
ALPHA = 0.05
RESAMPLES = 10_000
SEED = 0


def rates_of(results: dict) -> dict[str, dict[str, dict[str, Fraction]]]:
    """{measure: {model: {task: rate}}}, each rate exact: its value times its answers is a count."""
    out: dict[str, dict[str, dict[str, Fraction]]] = {m: {} for m in MEASURES}
    for model, s in results["models"].items():
        for task, per in (s.get("per_task") or {}).items():
            for m in MEASURES:
                if m in per and per[m].get("answers"):
                    n = int(per[m]["answers"])
                    out[m].setdefault(model, {})[task] = Fraction(round(per[m]["value"] * n), n)
    return out


def repositories(dataset: Path, tasks: set[str]) -> dict[str, str]:
    repo = {}
    for t in sorted(tasks):
        repo[t] = json.loads((dataset / "tasks" / t / "task.json").read_text())["repo_id"]
    return repo


def compare_pair(a: dict[str, Fraction], b: dict[str, Fraction], repo_of: dict[str, str]) -> dict:
    shared = sorted(set(a) & set(b))
    diffs = {t: a[t] - b[t] for t in shared}
    by_repo: dict[str, Fraction] = defaultdict(Fraction)
    for t, d in diffs.items():
        by_repo[repo_of[t]] += d
    floats = {t: float(d) for t, d in diffs.items()}
    lo, hi = cluster_bootstrap_ci(floats, repo_of, RESAMPLES, SEED)
    tlo, thi = bootstrap_ci([floats[t] for t in shared], RESAMPLES, SEED)
    return {"tasks": len(shared), "repositories": len(by_repo),
            "difference": float(sum(diffs.values()) / len(shared)) if shared else float("nan"),
            "interval": [lo, hi], "p": sign_flip_p(list(by_repo.values())),
            "task_interval": [tlo, thi], "task_p": sign_flip_p(list(diffs.values()))}


def letter_groups(order: list[str], differ: set[frozenset]) -> dict[str, str]:
    """A compact letter display (Piepho 2004, insert and absorb), letters assigned best first.

    `order` lists the models best first; `differ` holds the pairs claimed to differ.
    Two models share a letter exactly when they are not claimed to differ.
    """
    groups: list[set[str]] = [set(order)]
    for pair in sorted(differ, key=lambda p: sorted(order.index(m) for m in p)):
        i, j = sorted(pair, key=order.index)
        split: list[set[str]] = []
        for g in groups:
            split.extend([g - {j}, g - {i}] if i in g and j in g else [g])
        unique: list[set[str]] = []
        for g in split:
            if g and g not in unique:
                unique.append(g)
        groups = [g for g in unique if not any(g < h for h in unique)]
    groups.sort(key=lambda g: sorted(order.index(m) for m in g))
    if len(groups) > len(string.ascii_lowercase):
        raise ValueError("more groups than letters")
    letters = {m: "".join(string.ascii_lowercase[k] for k, g in enumerate(groups) if m in g) for m in order}
    for x, y in itertools.combinations(order, 2):
        share = bool(set(letters[x]) & set(letters[y]))
        if share == (frozenset((x, y)) in differ):
            raise AssertionError(f"letters wrong for {x} and {y}: {letters[x]}, {letters[y]}")
    return letters


def rank_ranges(order: list[str], better: set[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """From 1 + the models claimed better, to K - the models claimed worse; `better` holds (winner, loser)."""
    k = len(order)
    return {m: (1 + sum(1 for w, l in better if l == m), k - sum(1 for w, l in better if w == m)) for m in order}


def mean_ranks(rates: dict[str, dict[str, Fraction]]) -> dict[str, float]:
    """Each model's rank within each task (1 is highest; ties share the mean of their ranks), averaged."""
    models = sorted(rates)
    tasks = sorted(set.intersection(*(set(rates[m]) for m in models))) if models else []
    total = {m: 0.0 for m in models}
    for t in tasks:
        values = sorted({rates[m][t] for m in models}, reverse=True)
        position = 1
        for v in values:
            tied = [m for m in models if rates[m][t] == v]
            for m in tied:
                total[m] += position + (len(tied) - 1) / 2
            position += len(tied)
    return {m: total[m] / len(tasks) for m in models} if tasks else {}


def compare(rates: dict[str, dict[str, Fraction]], repo_of: dict[str, str], measured: dict[str, float]) -> dict:
    """One measure: every pair, Holm over them, the claims, and what the claims give each model."""
    order = sorted(rates, key=lambda m: (-measured[m], m))
    pairs = []
    for a, b in itertools.combinations(order, 2):
        pairs.append({"a": a, "b": b, **compare_pair(rates[a], rates[b], repo_of)})
    for pair, adj in zip(pairs, holm([p["p"] for p in pairs])):
        pair["holm_p"] = adj
        pair["claimed"] = adj < ALPHA
    differ = {frozenset((p["a"], p["b"])) for p in pairs if p["claimed"]}
    better = {((p["a"], p["b"]) if p["difference"] > 0 else (p["b"], p["a"])) for p in pairs if p["claimed"]}
    letters = letter_groups(order, differ)
    ranges = rank_ranges(order, better)
    ranks = mean_ranks(rates)
    return {"order": order, "pairs": pairs,
            "models": {m: {"value": measured[m], "letters": letters[m], "rank_range": list(ranges[m]),
                           "mean_rank": ranks.get(m)} for m in order}}


def markdown(out: dict) -> str:
    lines = ["# The v1 baseline run: comparisons", "",
             "As registered in `docs/v1-baseline-run.md` (\"The comparison rule\"), written by",
             "`scripts/v1_comparisons.py`. Models sharing a letter are not shown to differ. A difference",
             f"is claimed when its Holm-adjusted p, over the {len(out['measures'][MEASURES[0]]['pairs'])} pairs of a",
             "measure, is below 0.05. The test flips signs by repository; the task-level test is beside it,",
             "for comparison only.", ""]
    for m in MEASURES:
        res = out["measures"][m]
        lines += [f"## {m.replace('_', ' ')}", "",
                  "| model | rate | group | rank range | mean rank within a task |", "|---|---|---|---|---|"]
        for model in res["order"]:
            s = res["models"][model]
            lo, hi = s["rank_range"]
            lines.append(f"| {model} | {s['value']:.1%} | {s['letters']} | {lo}–{hi} | {s['mean_rank']:.2f} |")
        lines += ["", "| pair | difference (points) | 95% interval, by repository | p | Holm p | claimed "
                  "| task-level p (not for claims) |", "|---|---|---|---|---|---|---|"]
        for p in res["pairs"]:
            lines.append(f"| {p['a']} − {p['b']} | {100 * p['difference']:+.1f} | "
                         f"[{100 * p['interval'][0]:+.1f}, {100 * p['interval'][1]:+.1f}] | {p['p']:.4f} | "
                         f"{p['holm_p']:.4f} | {'yes' if p['claimed'] else 'no'} | {p['task_p']:.4f} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    results = json.loads(args.results.read_text())
    rates = rates_of(results)
    tasks = {t for by_model in rates.values() for per in by_model.values() for t in per}
    repo_of = repositories(args.dataset, tasks)
    out = {"results": str(args.results), "code_version": results.get("code_version"),
           "dataset_release": results.get("dataset_release"), "judge": results.get("judge"),
           "alpha": ALPHA, "resamples": RESAMPLES, "seed": SEED, "measures": {}}
    for m in MEASURES:
        measured = {model: s["measures"][m]["value"] for model, s in results["models"].items()}
        out["measures"][m] = compare(rates[m], repo_of, measured)
    args.out.with_suffix(".json").write_text(json.dumps(out, indent=1) + "\n")
    args.out.with_suffix(".md").write_text(markdown(out) + "\n")
    head = out["measures"][MEASURES[0]]
    for model in head["order"]:
        s = head["models"][model]
        print(f"{model}: {s['value']:.1%}, group {s['letters']}, rank {s['rank_range'][0]}-{s['rank_range'][1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
