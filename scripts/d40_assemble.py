"""Assemble D-40's run directories from the 55 admitted tasks.

    .venv/bin/python scripts/d40_assemble.py [--dry-run]

Reads the kept tasks of results/step2-tally.json and, from each task's own run
directory, its task row and its calibration, control and gate rows (those of
the task's current fingerprint). Checks that the attempt stage would run every
one -- sound in calibration and controlled -- and refuses otherwise. Writes:

- runs/d40-base/: tasks.jsonl, calibration.jsonl, controls.jsonl, gate.jsonl,
  and d40.json with the headline set and the smoke tasks;
- runs/d40smoke-<candidate>/: the two smoke tasks only, one directory per
  candidate, as the first grid had one per candidate.

The full run's directories are copied from d40-base once the smoke run passes.

Fixed by D-40 before any answer exists: the headline set keeps at most 8 tasks
per repository, cutting entireio/cli's 16 to the 8 with the lowest SHA-1 of
their task id; the smoke tasks are the TypeScript task and the Go task with the
lowest SHA-1 of their task id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from errata_bench.corpus.sessions import load_repos  # noqa: E402
from errata_bench.instrument.control import controlled  # noqa: E402
from errata_bench.score.judge import can_be_scored  # noqa: E402
from errata_bench.spec import fingerprint, read, write  # noqa: E402
from errata_bench.store import Paths, append, load  # noqa: E402

RUNS = ROOT / "runs"
CANDIDATES = ["grok-4.6", "Kimi-K2.7-Code", "DeepSeek-V4-Pro", "DeepSeek-V4-Flash",
              "Mistral-Large-3", "MAI-Thinking-1"]
CAP = 8


def sha(task_id: str) -> str:
    return hashlib.sha1(task_id.encode()).hexdigest()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    kept = json.load(open(ROOT / "results" / "step2-tally.json"))["kept"]
    tasks, rows = [], defaultdict(list)
    for k in kept:
        src = Paths(RUNS / k["run"])
        task = next(t for t in read(src.tasks) if t.task_id == k["task_id"])
        mark = fingerprint(task)
        tasks.append(task)
        for name in ("calibration", "controls", "gate"):
            rows[name] += [r for r in load(getattr(src, name)) if r.get("task_id") == task.task_id
                           and r.get("task_fingerprint") in (None, mark)]
    assert len({t.task_id for t in tasks}) == len(tasks) == 55, "55 tasks with distinct names"

    by_repo = defaultdict(list)
    for t in tasks:
        by_repo[t.repo_id].append(t.task_id)
    headline = sorted(tid for ids in by_repo.values() for tid in sorted(ids, key=sha)[:CAP])
    languages = {rid: repo.language for rid, repo in load_repos().items()}
    smoke = [min((t.task_id for t in tasks if languages.get(t.repo_id) == lang), key=sha)
             for lang in ("TypeScript", "Go")]
    print(f"tasks {len(tasks)} | headline (at most {CAP} per repository) {len(headline)} | smoke {smoke}")

    if args.dry_run:
        return 0
    base = Paths(RUNS / "d40-base")
    if base.tasks.exists():
        print(f"refused: {base.tasks} exists")
        return 1
    base.root.mkdir(parents=True, exist_ok=True)
    write(tasks, base.tasks)
    for name in ("calibration", "controls", "gate"):
        for r in rows[name]:
            append(getattr(base, name), r)
    # The attempt stage's own selection, asked of the assembled directory.
    sound = {r["task_id"] for r in load(base.calibration) if can_be_scored(r)}
    runnable = sound & controlled(base)
    missing = sorted({t.task_id for t in tasks} - runnable)
    if missing:
        print(f"refused: the attempt stage would skip {missing}")
        return 1
    (base.root / "d40.json").write_text(json.dumps({"headline": headline, "smoke": smoke,
                                                    "candidates": CANDIDATES}, indent=1))
    for cand in CANDIDATES:
        smoke_dir = Paths(RUNS / f"d40smoke-{cand}")
        smoke_dir.root.mkdir(parents=True, exist_ok=True)
        write([t for t in tasks if t.task_id in smoke], smoke_dir.tasks)
        for name in ("calibration", "controls", "gate"):
            for r in rows[name]:
                if r["task_id"] in smoke:
                    append(getattr(smoke_dir, name), r)
    print(f"wrote {base.root} and {len(CANDIDATES)} smoke directories; all 55 would be attempted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
