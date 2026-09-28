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
written: each model's own measures (`release.report.score`; a run holding two
models' trials scores each apart), which judge read them, whether every trial
ran its task as published (`grading.official`), and the tasks' version.

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


def graded_for(task: Task, folder: Path) -> tuple[str, str]:
    """The instruction a trial of this task was given, and the conversation its answer is graded against.

    The graders read the whole conversation, which the agent was given: in its
    instruction, or, for a conversation too long to pass as one argument, cut
    there and whole in its container (`harbor.fitted`, `harbor.FITTED`). The
    judge's admission read the same (`admitted_conversation`, #7): on those
    tasks the cut view had been graded, on a judge admitted on the whole.
    """
    meta = json.loads((folder / "task.json").read_text())
    whole = (folder / "conversation.txt").read_bytes().decode("utf-8")
    turns = json.loads((folder / "shown_turns.json").read_text())
    instruction, _, _ = harbor_mod.fitted(meta, whole, turns, task.cut_turn)
    return instruction, whole


def admitted_conversation(folder: Path) -> str | None:
    """The conversation the judge's admission read at the cut (`release.admission`), or None if absent."""
    try:
        return json.loads((folder / "grading" / "controls.json").read_text())["cut"]
    except (OSError, ValueError, KeyError):
        return None


def admitted_elsewhere(tasks: dict[str, tuple[Task, Path]]) -> list[str]:
    """The tasks whose admission read another conversation than grading reads: their grades would not be admitted."""
    return [task_id for task_id, (task, folder) in sorted(tasks.items())
            if admitted_conversation(folder) != graded_for(task, folder)[1]]


def release_problems(tasks: dict[str, tuple[Task, Path]], admission: Path, out: Path,
                     graded: dict[str, tuple[str, str]]) -> list[str]:
    """Whatever would make this run's grades not what they claim to be, found before anything is read or paid.

    A task the release holds incompletely; one whose admission read another
    conversation than grading reads, or none (#7); an admission made on another
    version of a task, by the fingerprint its rows carry; and rows already on
    record in ``out`` that were graded on another conversation -- a folder
    resumed across the fix of #7 would mix the two (09-27 review). ``graded``
    is filled with each task's instruction and conversation on the way.
    """
    from errata_bench.spec import fingerprint

    problems = []
    for task_id, (task, folder) in sorted(tasks.items()):
        try:
            graded[task_id] = graded_for(task, folder)
        except (OSError, ValueError, KeyError) as e:
            problems.append(f"{task_id}: the release holds this task incompletely ({type(e).__name__}: {e})")
            continue
        admitted = admitted_conversation(folder)
        if admitted is None:
            problems.append(f"{task_id}: the release has no conversation the judge's admission read "
                            f"(grading/controls.json)")
        elif admitted != graded[task_id][1]:
            problems.append(f"{task_id}: the judge's admission read another conversation than grading reads")
    stamps = {task_id: fingerprint(task) for task_id, (task, _) in tasks.items()}
    for name in ("calibration.jsonl", "controls.jsonl"):
        for r in load(admission / name) if (admission / name).is_file() else []:
            stamp = r.get("task_fingerprint")
            if r.get("task_id") in stamps and stamp and stamp != stamps[r["task_id"]]:
                problems.append(f"{r['task_id']}: --admission was made on another version of this task "
                                f"({name}: fingerprint {stamp}, the release's {stamps[r['task_id']]})")
                break
    # Read without `Paths`, which makes the folder: a refused run leaves nothing behind (09-28 review).
    stale = sorted({r["task_id"] for r in (load(out / "answers.jsonl") if (out / "answers.jsonl").is_file() else [])
                    if not r.get("error") and r.get("task_id") in graded
                    and r.get("transcript") is not None and r["transcript"] != graded[r["task_id"]][1]})
    if stale:
        problems.append(f"{out} holds rows graded on another conversation than grading now reads "
                        f"({', '.join(stale[:5])}): grade into a new --out")
    return problems


def dataset_version(release: Path) -> str:
    """The version of the tasks graded against: the one the release's Harbor tasks were exported as."""
    try:
        return str(json.loads((release / "harbor" / "export.json").read_text())["benchmark_version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


# Each published dataset release, by the version its Harbor tasks were exported
# as and the digest of its grading rows (`grading_data_of`). v1.0.2 changed only
# the grading rows, so its tasks still read 1.0.1 and `dataset_version` alone
# could not tell it from v1.0.1 (#11). A release built since writes its own
# number (release.json, `scripts/build_dataset.py`).
KNOWN_RELEASES = {
    ("1.0", "sha256:8bcdfc6b42eb6b152115751f060f31d3fc7ad94b7697015e88913618291276c9"): "1.0",
    ("1.0.1", "sha256:8bcdfc6b42eb6b152115751f060f31d3fc7ad94b7697015e88913618291276c9"): "1.0.1",
    ("1.0.1", "sha256:860ed377e941ef43f0406c900d71f22b2816911f7ab0a5aa46e02f63c07d0ca1"): "1.0.2",
}


def dataset_release(release: Path) -> str:
    """Which published release of the dataset this is: its own record, or recognised by its tasks and grading rows."""
    try:
        return str(json.loads((release / "release.json").read_text())["release"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return KNOWN_RELEASES.get((dataset_version(release), grading_data_of(release)), "unrecognised")


def sha256_of(*files: Path) -> str | None:
    """One digest over those of the files given that exist, by name and content; None if none does.

    The dataset ships its admission without `gate.jsonl`, so a digest that
    needed every file named none (09-27).
    """
    import hashlib

    present = [f for f in files if f.is_file()]
    if not present:
        return None
    h = hashlib.sha256()
    for f in present:
        h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def grading_data_of(release: Path) -> str | None:
    """One digest over every task's grading row (tasks/<id>/grading/task.json), each named by its task."""
    import hashlib

    rows = sorted((release / "tasks").glob("*/grading/task.json"))
    if not rows:
        return None
    h = hashlib.sha256()
    for f in rows:
        h.update(f.parent.parent.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def manifest_of(release: Path, run: Path) -> dict:
    """What a run's results were made with, named exactly and with no credential in it (#6)."""
    provider = ("azure" if os.environ.get("ERRATA_PROVIDER", "").lower() == "azure"
                else "openai-compatible" if os.environ.get("OPENAI_BASE_URL") else "openai")
    return {"code_version": code_version(), "dataset_version": dataset_version(release),
            "dataset_release": dataset_release(release),
            "task_digests": sha256_of(release / "harbor" / "digests.json"),
            # What grading reads of each task, by task: v1.0.2 changed it (the
            # defect labels, #8) and not the tasks, so the tasks' version alone
            # does not name it.
            "grading_data": grading_data_of(release),
            "admission": sha256_of(*(run / name for name in ADMISSION)),
            "requirements_lock": sha256_of(Path(__file__).resolve().parent.parent / "requirements-lock.txt"),
            "provider": provider,
            "api": os.environ.get("ERRATA_API") or ("chat_completions" if provider == "azure" else "responses")}


def served_by(rows: list[dict]) -> dict:
    """How many requests each model served, as the provider named it, per grader (#6)."""
    counts: dict[str, Counter] = {"judge": Counter(), "trace": Counter()}
    for r in rows:
        for grader in counts:
            counts[grader].update(r.get(f"{grader}_served") or ["not recorded"])
    return {g: dict(c) for g, c in counts.items()}


def served_note(served: dict) -> str | None:
    """A note when the judge's requests were served by more than one named model; None otherwise.

    Printed only, it was lost with the console, and it counted "unknown" -- a
    response that named no model -- as a second model (09-28 review, #6).
    """
    judges = sorted(m for m in served.get("judge", {}) if m not in ("not recorded", "unknown"))
    if len(judges) > 1:
        return f"the judge's requests were served by {len(judges)} models: {', '.join(judges)}"
    return None


def results_of(paths: Paths, judge: str, passes: int, version: str = "unknown", manifest: dict | None = None) -> dict:
    """The run's results: each model's own score (never two models' answers in one), and whether it is official."""
    from errata_bench.release.report import OFFICIAL_JUDGE, score
    from errata_bench.score.judge import can_be_scored
    from errata_bench.stages.scoring import controlled

    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    readings, answers = load(paths.attempts), [r for r in load(paths.answers) if not r.get("error")]
    common = ({f"the judge is {judge}, not {OFFICIAL_JUDGE}"} if judge != OFFICIAL_JUDGE else set()) | (
        {f"{passes} readings per answer, not 3"} if passes != 3 else set())
    models = {}
    for model in sorted({a.get("model") for a in answers}):
        mine = [a for a in answers if a.get("model") == model]
        s = score([r for r in readings if r.get("model") == model], tasks=admitted)
        why = sorted({w for a in mine for w in (a.get("harbor") or {}).get("why_not_official", [])} | common)
        s.update({"agents": sorted({(a.get("harbor") or {}).get("agent") for a in mine}),
                  "official": not why, "why_not_official": why})
        models[model] = s
    rows = load(paths.answers)
    served = served_by(readings)
    return {"benchmark": "errata-bench", "dataset_version": version,
            "dataset_release": (manifest or {}).get("dataset_release", "unrecognised"), "manifest": manifest or {},
            "served": served, "served_note": served_note(served),
            "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code_version": code_version(), "judge": judge, "passes": passes, "admitted_tasks": sorted(admitted),
            "trials": {"on_record": len(rows), "graded": len(answers), "not_gradable": len(rows) - len(answers)},
            "models": models}


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
    # Refused before anything is read or paid: a judge admitted on one
    # conversation and grading on another is not admitted to what it grades
    # (#7), and the same holds for an admission of another task version, or a
    # folder already holding rows graded on another conversation.
    graded_cache: dict[str, tuple[str, str]] = {}
    problems = release_problems(tasks, args.admission, args.out, graded_cache)
    # A folder of job folders holds no trial of its own: given one, grading
    # used to find nothing, write nothing and succeed (09-28 review).
    empty = [str(j) for j in args.jobs if not read_trials([j])]
    if empty:
        problems.append(f"no Harbor trial found in {', '.join(empty[:3])}: give each job folder "
                        f"(the folder holding the trials' folders), not a folder of jobs")
    if problems:
        print(f"refused: {len(problems)} problem(s), nothing was graded:"
              + "".join(f"\n  - {p}" for p in problems[:8]) + ("\n  ..." if len(problems) > 8 else ""),
              file=sys.stderr)
        return 2
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
    written = Counter()
    for trial in read_trials(args.jobs):
        if trial.name in on_record:
            continue
        if trial.task_id not in tasks:
            written["not a task of this release"] += 1
            continue
        task, folder = tasks[trial.task_id]
        if task.task_id not in graded_cache:
            graded_cache[task.task_id] = graded_for(task, folder)
        instruction, conversation = graded_cache[task.task_id]
        ok, why = official(trial, digests)
        row = answer_row(trial, task, runs[task.task_id], conversation, instruction, ok)
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
    from errata_bench.stages.scoring import stage_grade

    progress = asyncio.run(stage_grade(paths, args.limit, args.concurrency, passes=args.passes))
    print(progress.line().strip())
    result = results_of(paths, judge_model(), args.passes, dataset_version(args.release),
                        manifest_of(args.release, args.out))
    if result["served_note"]:
        print(f"note: {result['served_note']} (results.json, `served_note`)")
    (args.out / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    for model, s in result["models"].items():
        m = s["measures"]
        print(f"{model}: honest reports {m['honest_reports']['value']:.0%} [{m['honest_reports']['low']:.0%}-"
              f"{m['honest_reports']['high']:.0%}], fixed {m['fixed']['value']:.0%}, "
              f"fixed and honest {m['fixed_and_honest']['value']:.0%}, no answer {m['no_answer']['value']:.0%} "
              f"({s['answers']} answers on {s['tasks']} tasks); "
              f"{'official' if s['official'] else 'not official: ' + '; '.join(s['why_not_official'])}")
    return 0 if progress.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
