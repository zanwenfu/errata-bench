#!/usr/bin/env python3
"""Assemble the release as the dataset users download (v1 step 7).

    python scripts/build_dataset.py <release dir> --admission <run dir> --out <dataset dir> --release <number>

The dataset keeps the release's own layout, so every tool reads it as it reads
the release:

  harbor/<task>/          the Harbor tasks (`scripts/export_harbor.py`), with
                          their digests (digests.json) and export.json: what
                          `harbor run -p <dataset>/harbor` runs;
  tasks/<task>/           what grading reads of each task: task.json, the
                          conversation and the turns it is rendered from, and
                          grading/ (references, controls, the task row, the
                          turns to the resolution). The working copy is not
                          repeated here: it is in harbor/<task>/environment/;
  admission/gpt-6-astra/  the official judge's admission to each task
                          (`scripts/admit_judge.py`), rows for these tasks only:
                          what `scripts/grade_harbor.py --admission` reads;
  manifest.json           what the freeze made of each task;
  release.json            the release's own number (--release), which grading
                          records in every results.json;
  README.md               the dataset card (docs/dataset-card.md);
  SHA256SUMS              every file's digest.

No model calls, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

JUDGE = "gpt-6-astra"
ADMISSION = ("tasks.jsonl", "calibration.jsonl", "controls.jsonl")
TASK_FILES = ("task.json", "conversation.txt", "shown_turns.json")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--admission", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--release", dest="number", required=True,
                    help="the dataset release this build is published as, e.g. 1.0.3")
    args = ap.parse_args(argv)
    harbor = args.release / "harbor"
    if not (harbor / "digests.json").is_file():
        ap.error(f"{harbor} has no digests.json: export the tasks and record their digests first")
    if args.out.exists():
        ap.error(f"{args.out} exists: the dataset is built into a new folder")
    tasks = sorted(d.name for d in harbor.iterdir() if (d / "task.toml").is_file())
    digests = json.loads((harbor / "digests.json").read_text())
    missing = [t for t in tasks if t not in digests]
    if missing:
        ap.error(f"tasks with no digest: {missing[:5]}")
    out = args.out
    shutil.copytree(harbor, out / "harbor", ignore=shutil.ignore_patterns("__pycache__"))
    for t in tasks:
        src, dst = args.release / "tasks" / t, out / "tasks" / t
        dst.mkdir(parents=True)
        for name in TASK_FILES:
            shutil.copyfile(src / name, dst / name)
        shutil.copytree(src / "grading", dst / "grading")
    kept = set(tasks)
    (out / "admission" / JUDGE).mkdir(parents=True)
    for name in ADMISSION:
        rows = [json.loads(l) for l in (args.admission / name).read_text().splitlines() if l.strip()]
        rows = [r for r in rows if r.get("task_id") in kept and (name == "tasks.jsonl" or r.get("judge_model") == JUDGE)]
        (out / "admission" / JUDGE / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    shutil.copyfile(args.release / "manifest.json", out / "manifest.json")
    (out / "release.json").write_text(json.dumps({"release": args.number}) + "\n")
    card = Path(__file__).resolve().parent.parent / "docs" / "dataset-card.md"
    shutil.copyfile(card, out / "README.md")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out).as_posix()}\n" for p in files))
    size = sum(p.stat().st_size for p in files)
    print(f"{len(tasks)} tasks, {len(files)} files, {size / 2**20:,.0f} MB in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
