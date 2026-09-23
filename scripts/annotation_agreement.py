"""Agreement between annotators, and between annotators and each judge.

    .venv/bin/python scripts/annotation_agreement.py --key annotation/round1-KEY.json \\
        annotation/round1/sheet-A.csv annotation/round1/sheet-B.csv [--judge claude-opus-5]

Reads the filled item sheets (one per annotator, in the kit's column layout) and
the key the kit wrote. For each labelled question it reports, with the number of
items both sides labelled: raw agreement and Cohen's kappa with a bootstrap 95%
interval over items -- annotator against annotator, each annotator against the
judge, and the annotators' consensus (items where they agree) against the judge.
The judge's labels are its settled verdict on that answer: the judge's own four
observations, and "unsupported claim" as the trace check finding a claim the
record does not support. `--judge NAME` compares against that judge's re-grades.
yes/y/true/1 and no/n/false/0 are read; anything else (blank, unsure) is left out.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.score.rejudge import judge_paths, settled  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402

QUESTIONS = ["addresses_defect", "defect_remains", "makes_unverified_claim", "reports_limits",
             "unsupported_claim"]
YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


def as_bool(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    return True if v in YES else False if v in NO else None


def kappa(pairs: list[tuple[bool, bool]]) -> float:
    """Cohen's kappa for two binary raters; nan when chance agreement is total."""
    n = len(pairs)
    if not n:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def kappa_ci(pairs: list[tuple[bool, bool]], resamples: int = 2000, seed: int = 0) -> tuple[float, float]:
    if not pairs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    ks = sorted(k for k in (kappa([pairs[rng.randrange(len(pairs))] for _ in pairs])
                            for _ in range(resamples)) if k == k)
    if not ks:
        return (float("nan"), float("nan"))
    return ks[int(0.025 * len(ks))], ks[min(len(ks) - 1, int(0.975 * len(ks)))]


def judge_labels(key: dict, judge: str | None) -> dict[str, dict[str, bool | None]]:
    """The judge's settled reading of every keyed item, per question."""
    cache: dict[str, dict] = {}
    out = {}
    for item, where in key["items"].items():
        run = Path(where["run_dir"])
        if str(run) not in cache:
            source = judge_paths(run, judge).attempts if judge else Paths(run).attempts
            cache[str(run)] = {(g["task_id"], g["run"]): g for g in settled(load(source))}
        g = cache[str(run)].get((where["task_id"], where["run"]))
        if g is None:
            out[item] = {q: None for q in QUESTIONS}
            continue
        j = g.get("judgement") or {}
        labels = {q: (bool(j[q]) if q in j else None) for q in QUESTIONS[:4]}
        cmt = g.get("claims_match_trace")
        labels["unsupported_claim"] = None if cmt is None else (cmt is False)
        out[item] = labels
    return out


def read_sheet(path: Path) -> dict[str, dict[str, bool | None]]:
    with open(path, newline="") as fh:
        return {row["item_id"]: {q: as_bool(row.get(q)) for q in QUESTIONS} for row in csv.DictReader(fh)}


def line(label: str, pairs: list[tuple[bool, bool]]) -> str:
    if not pairs:
        return f"    {label:28s} no items labelled by both"
    agree = sum(a == b for a, b in pairs) / len(pairs)
    k = kappa(pairs)
    lo, hi = kappa_ci(pairs)
    return f"    {label:28s} n={len(pairs):3d}  agreement {agree:.2f}  kappa {k:+.2f} [{lo:+.2f}, {hi:+.2f}]"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheets", nargs="+", type=Path, help="one filled sheet.csv per annotator")
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--judge", help="compare against this judge's re-grades instead of the run's own")
    args = ap.parse_args(argv)
    key = json.loads(args.key.read_text())
    sheets = {p.stem: read_sheet(p) for p in args.sheets}
    judged = judge_labels(key, args.judge)
    names = list(sheets)
    print(f"judge: {args.judge or 'each run directory own grading'}; items in key: {len(key['items'])}; "
          f"annotators: {', '.join(names)}\n")
    for q in QUESTIONS:
        print(q)
        if len(names) >= 2:
            a, b = sheets[names[0]], sheets[names[1]]
            both = [(a[i][q], b[i][q]) for i in key["items"]
                    if i in a and i in b and a[i][q] is not None and b[i][q] is not None]
            print(line(f"{names[0]} vs {names[1]}", both))
        for n in names:
            s = sheets[n]
            pairs = [(s[i][q], judged[i][q]) for i in key["items"]
                     if i in s and s[i][q] is not None and judged[i][q] is not None]
            print(line(f"{n} vs judge", pairs))
        if len(names) >= 2:
            a, b = sheets[names[0]], sheets[names[1]]
            consensus = [(a[i][q], judged[i][q]) for i in key["items"]
                         if i in a and i in b and a[i][q] is not None and a[i][q] == b[i][q]
                         and judged[i][q] is not None]
            print(line("consensus vs judge", consensus))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
