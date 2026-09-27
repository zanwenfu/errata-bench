#!/usr/bin/env python3
"""Grade a Harbor job's trials of errata-bench's tasks (v1 step 4).

    scripts/grade_harbor.py <release dir> <job dir>... --out <run dir> --admission <dir>
                            [--passes 3] [--concurrency 8] [--limit N] [--rows-only]

Each trial (`errata_bench.release.grading`) becomes an answer row, in a run
directory the grading stage reads: tasks.jsonl, the release's frozen tasks;
the judge's admission of each task (calibration.jsonl, controls.jsonl,
gate.jsonl), copied from --admission, a run directory where that judge was put
through the known answers; and answers.jsonl, from the trials. Then each answer
is read --passes times (`stages.scoring.stage_grade`) and <run dir>/results.json
written: the measures (`release.report.score`), which judge read them, and
whether every trial ran its task as published (`grading.official`).

The judge is ERRATA_JUDGE_MODEL, called with your own key (ERRATA_PROVIDER=azure
with AZURE_OPENAI_BASE_URL and AZURE_OPENAI_API_KEY, or OPENAI_API_KEY). Grading
calls that model three times per answer and is paid. With --rows-only nothing
is graded: the rows are written and counted, to check a job before paying.
Running again adds trials not yet on record and grades what is left.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.project import code_version  # noqa: E402
from errata_bench.release import harbor as harbor_mod  # noqa: E402
from errata_bench.release.grading import answer_row, official, read_trials  # noqa: E402
from errata_bench.spec import Task  # noqa: E402
from errata_bench.store import Paths, append, load  # noqa: E402

ADMISSION = ("calibration.jsonl", "controls.jsonl", "gate.jsonl")


def release_tasks(release: Path) -> dict[str, tuple[Task, Path]]:
    """The release's frozen tasks, by id, with each one's folder."""
    out = {}
    for d in sorted((release / "tasks").iterdir()):
        if (d / "grading" / "task.json").is_file():
            task = Task.from_json(json.loads((d / "grading" / "task.json").read_text()))
            out[task.task_id] = (task, d)
    return out


def shown_for(task: Task, folder: Path) -> tuple[str, str]:
    """The instruction a trial of this task was given, and the conversation it showed (`harbor.fitted`)."""
    meta = json.loads((folder / "task.json").read_text())
    whole = (folder / "conversation.txt").read_bytes().decode("utf-8")
    turns = json.loads((folder / "shown_turns.json").read_text())
    instruction, shown, _ = harbor_mod.fitted(meta, whole, turns, task.cut_turn)
    return instruction, shown


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("jobs", type=Path, nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--admission", type=Path, required=True)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--rows-only", action="store_true")
    args = ap.parse_args(argv)

    tasks = release_tasks(args.release)
    digest_file = args.release / "harbor" / "digests.json"
    digests = json.loads(digest_file.read_text()) if digest_file.is_file() else {}
    paths = Paths(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    if not paths.tasks.exists():
        paths.tasks.write_text("".join(json.dumps(t.to_json()) + "\n" for t, _ in tasks.values()))
    for name in ADMISSION:
        source, target = args.admission / name, args.out / name
        if source.is_file() and not target.exists():
            target.write_text("".join(json.dumps(r) + "\n" for r in load(source) if r.get("task_id") in tasks))

    on_record = {(r.get("harbor") or {}).get("trial") for r in load(paths.answers)}
    runs = Counter()
    for r in load(paths.answers):
        runs[r["task_id"]] = max(runs[r["task_id"]], int(r.get("run", -1)) + 1)
    shown_cache: dict[str, tuple[str, str]] = {}
    written = Counter()
    for trial in read_trials(args.jobs):
        if trial.name in on_record:
            continue
        if trial.task_id not in tasks:
            written["not a task of this release"] += 1
            continue
        task, folder = tasks[trial.task_id]
        if task.task_id not in shown_cache:
            shown_cache[task.task_id] = shown_for(task, folder)
        instruction, shown = shown_cache[task.task_id]
        ok, why = official(trial, digests)
        row = answer_row(trial, task, runs[task.task_id], shown, instruction, ok)
        if why:
            row["harbor"]["why_not_official"] = why
        runs[task.task_id] += 1
        append(paths.answers, row)
        written["error" if row.get("error") else "answer"] += 1
        written["official" if ok else "not official"] += 1
    rows = load(paths.answers)
    print(f"{len(rows)} trials on record in {paths.answers}: "
          f"{sum(1 for r in rows if not r.get('error'))} answers to grade, "
          f"{sum(1 for r in rows if r.get('error'))} not gradable; this run added {dict(written)}")
    for r in rows:
        if r.get("error"):
            print(f"  not gradable: {r['task_id']} #{r['run']} ({r['harbor']['trial']}): {r['error']}")
    if args.rows_only:
        return 0
    if not os.environ.get("ERRATA_JUDGE_MODEL"):
        # Never a default: grading is paid, with the user's key, by a model they chose.
        print("refused: set ERRATA_JUDGE_MODEL to the judge that grades these answers "
              "(the official judge is gpt-6-astra); nothing was graded", file=sys.stderr)
        return 2

    from errata_bench.llm import judge_model
    from errata_bench.release.report import OFFICIAL_JUDGE, score
    from errata_bench.score.judge import can_be_scored
    from errata_bench.stages.scoring import controlled, stage_grade

    progress = asyncio.run(stage_grade(paths, args.limit, args.concurrency, passes=args.passes))
    print(progress.line().strip())
    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    result = score(load(paths.attempts), tasks=admitted)
    graded = [r for r in rows if not r.get("error")]
    every_official = bool(graded) and all((r.get("harbor") or {}).get("official") for r in graded)
    result.update({
        "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_version": code_version(),
        "judge": judge_model(), "passes": args.passes,
        "official": every_official and judge_model() == OFFICIAL_JUDGE and args.passes == 3,
        "why_not_official": sorted({w for r in graded for w in (r.get("harbor") or {}).get("why_not_official", [])}
                                   | ({f"the judge is {judge_model()}, not {OFFICIAL_JUDGE}"}
                                      if judge_model() != OFFICIAL_JUDGE else set())
                                   | ({f"{args.passes} readings per answer, not 3"} if args.passes != 3 else set())),
        "agents": sorted({f"{(r.get('harbor') or {}).get('agent')} / {r.get('model')}" for r in graded}),
        "trials": {"on_record": len(rows), "graded": len(graded), "not_gradable": len(rows) - len(graded)},
        "admitted_tasks": len(admitted),
    })
    (args.out / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    m = result["measures"]
    print(f"honest reports {m['honest_reports']['value']:.0%} [{m['honest_reports']['low']:.0%}-"
          f"{m['honest_reports']['high']:.0%}], fixed {m['fixed']['value']:.0%}, "
          f"fixed and honest {m['fixed_and_honest']['value']:.0%}, no answer {m['no_answer']['value']:.0%}; "
          f"{'official' if result['official'] else 'not official: ' + '; '.join(result['why_not_official'])}")
    return 0 if progress.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
