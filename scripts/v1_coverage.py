#!/usr/bin/env python3
"""How completely each v1 task was rebuilt, and what of its record was ever cut (#5).

    python scripts/v1_coverage.py <release dir> [--admission <dir>] [--runs runs/<run>...] > results/v1-coverage.md

From the frozen release alone, no model calls and no network. Per task:
  - the defect check: what it established (`construct.presence`, #8);
  - the edits replayed onto the starting commit, and how many of them were
    checked against the conversation;
  - the consistency check re-run on the frozen tree (`construct.consistency`):
    files compared, files not found -- a repository's own or a dependency's --
    and the commands before the cut that changed state no replay reproduces;
  - the view the agent's instruction shows: the conversation whole, or long
    tool traffic cut to fit (`tool_cap`), and how many parts were cut;
  - whether the official judge is admitted to it.
Then, apart, the three kinds of cut: in the conversation, in a stored tool
output, and in a grader's view. With --runs, the stored-output cuts and the
shortened grader views of those runs, by candidate. "Consistent" is not
"completely rebuilt": a task can compare nothing and pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.construct.consistency import check as consistency  # noqa: E402

CUT_IN_CONVERSATION = re.compile(r"\[[\d,]+ more characters not shown\]")
STORED_CUT = re.compile(r"\[cut: [\d,]+ characters\]")
HOME = re.compile(r"^(?:/(?:Users|home)/[^/]+|[A-Za-z]:[\\/]Users[\\/][^\\/]+)")


def where(path: str, checkout: str) -> tuple[str, str]:
    """A path the conversation read, as ("inside"/"outside" the checkout, the path shown without a home folder).

    The developer's home folder names them, and the dataset's terms forbid
    identifying them: a path inside the checkout is shown from there, any other
    with its home folder as ~.
    """
    norm, root = path.replace("\\", "/"), checkout.replace("\\", "/").rstrip("/")
    if root and norm.startswith(root + "/"):
        return "inside", norm[len(root) + 1:]
    return "outside", HOME.sub("~", norm)


def admitted(folder: Path | None) -> set[str] | None:
    """The tasks the official judge is admitted to, as grading decides it (`grade_harbor.results_of`)."""
    if folder is None or not (folder / "calibration.jsonl").is_file():
        return None
    from errata_bench.instrument.control import controlled
    from errata_bench.score.judge import can_be_scored
    from errata_bench.store import Paths

    return ({r["task_id"] for r in map(json.loads, open(folder / "calibration.jsonl")) if can_be_scored(r)}
            & controlled(Paths(folder)))


def task_rows(release: Path, tasks_dir: Path, admission: Path | None) -> list[dict]:
    exported = {}
    listing = release / "harbor" / "export.json"
    if listing.is_file():
        exported = {r["task_id"]: r for r in json.loads(listing.read_text())["tasks"]}
    official = admitted(admission)
    rows = []
    for d in sorted(tasks_dir.iterdir()):
        if not (d / "grading" / "task.json").is_file():
            continue
        g = json.loads((d / "grading" / "task.json").read_text())
        meta = json.loads((d / "task.json").read_text())
        turns = json.loads((d / "grading" / "turns.json").read_text())
        archive = d / "workspace.tar.gz"
        if not archive.is_file():
            archive = release / "harbor" / d.name / "environment" / "workspace.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter="fully_trusted")
            c = consistency(Path(tmp) / "workspace", turns, g["cut_turn"], meta.get("sha") or "")
        missing = [where(f["path"], meta.get("session_workdir") or meta.get("workdir") or "")
                   for f in c["files"] if not f.get("found")]
        conversation = (d / "conversation.txt").read_bytes().decode("utf-8")
        instruction_path = release / "harbor" / d.name / "instruction.md"
        instruction = instruction_path.read_bytes().decode("utf-8") if instruction_path.is_file() else ""
        e = exported.get(d.name, {})
        rows.append({
            "task": d.name, "kind": g["kind"], "defect": g.get("strength"),
            "edits": (g.get("edits_replayed") or 0, g.get("edits_verified") or 0),
            "compared": c["files_compared"], "differing": c["files_differing"],
            "missing_inside": [m for side, m in missing if side == "inside"],
            "missing_outside": [m for side, m in missing if side == "outside"],
            "state_changing": len(c.get("mutating_commands") or []),
            "consistent": c["consistent"],
            "conversation_cuts": len(CUT_IN_CONVERSATION.findall(conversation)),
            "tool_cap": e.get("tool_cap"),
            "instruction_cuts": len(CUT_IN_CONVERSATION.findall(instruction)),
            "admitted": None if official is None else d.name in official,
        })
    return rows


def run_cuts(run: Path) -> dict:
    """One graded run's stored-output cuts, and how many of its readings recorded a shortened view."""
    out = {"answers": 0, "cut_answers": 0, "cuts": 0, "readings": 0, "view_recorded": 0, "shortened": 0}
    if (run / "answers.jsonl").is_file():
        for line in open(run / "answers.jsonl"):
            r = json.loads(line)
            if r.get("error"):
                continue
            out["answers"] += 1
            n = len(STORED_CUT.findall(json.dumps(r.get("tool_calls") or [])))
            out["cut_answers"] += bool(n)
            out["cuts"] += n
    for f in [run / "attempts.jsonl", *sorted((run / "rejudge").glob("*/attempts.jsonl"))]:
        if not f.is_file():
            continue
        for line in open(f):
            r = json.loads(line)
            out["readings"] += 1
            views = [v for v in ((r.get("judgement") or {}).get("shown"), r.get("trace_shown")) if v]
            out["view_recorded"] += bool(views)
            out["shortened"] += any(v.get("budget") is not None for v in views)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--runs", type=Path, nargs="*", default=[])
    ap.add_argument("--admission", type=Path, help="the official judge's admission (default: the release's own)")
    args = ap.parse_args(argv)
    admission = args.admission or args.release / "admission" / "gpt-6-astra"
    rows = task_rows(args.release, args.release / "tasks", admission)
    out = ["# v1: how completely each task was rebuilt (#5)", "",
           f"From `{args.release}`, by `scripts/v1_coverage.py`: no model calls. \"Consistent\" is not \"completely "
           "rebuilt\": a task that compares no file passes.", "",
           "| task | kind | defect check | edits replayed (checked) | files compared | not found: inside / "
           "outside the checkout | state-changing commands before the cut | instruction: tool cap (parts cut) | "
           "official |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cap = "whole" if r["tool_cap"] is None else f"{r['tool_cap']:,} ({r['instruction_cuts']})"
        out.append(f"| {r['task']} | {r['kind']} | {r['defect']} | {r['edits'][0]} ({r['edits'][1]}) | "
                   f"{r['compared']}{' (' + str(r['differing']) + ' differ)' if r['differing'] else ''} | "
                   f"{len(r['missing_inside'])} / {len(r['missing_outside'])} | {r['state_changing']} | {cap} | "
                   f"{'-' if r['admitted'] is None else ('yes' if r['admitted'] else 'no')} |")
    n = len(rows)
    out += ["", "## Totals", "",
            f"- Tasks: {n}. Defect checks: " + ", ".join(f"{v} {k}" for k, v in
                                                         sorted(Counter(r['defect'] for r in rows).items())) + ".",
            f"- Edits replayed: {sum(r['edits'][0] for r in rows)} on {sum(1 for r in rows if r['edits'][0])} tasks, "
            f"{sum(r['edits'][1] for r in rows)} checked against the conversation.",
            f"- Files compared: {sum(r['compared'] for r in rows)} ({sum(r['differing'] for r in rows)} differing); "
            f"not found: {sum(len(r['missing_inside']) for r in rows)} inside the developer's checkout (a file "
            f"never committed, or made by a command), {sum(len(r['missing_outside']) for r in rows)} outside it. "
            f"{sum(1 for r in rows if not r['compared'])} tasks compared no file.",
            f"- Commands before the cut that changed state no replay reproduces: "
            f"{sum(r['state_changing'] for r in rows)} on {sum(1 for r in rows if r['state_changing'])} tasks.",
            "", "## Files the conversation read before the cut and the tree does not hold", "",
            "Inside the checkout: a file the developer never committed, or one a command made. Outside it: another "
            "folder of the developer's machine (home folders shown as ~). Whether each limits the task, or only a "
            "conclusion about it, is not yet reviewed (#5).", ""]
    for r in rows:
        if r["missing_inside"] or r["missing_outside"]:
            parts = [f"inside: {', '.join(f'`{m}`' for m in r['missing_inside'])}" if r["missing_inside"] else "",
                     f"outside: {', '.join(f'`{m}`' for m in r['missing_outside'])}" if r["missing_outside"] else ""]
            out.append(f"- **{r['task']}**: " + "; ".join(x for x in parts if x))
    out += ["", "## The three kinds of cut", "",
            f"- **In the conversation.** v1 renders each conversation whole (record 3): "
            f"{sum(r['conversation_cuts'] for r in rows)} cut marks in the 55 conversations. "
            f"The instruction handed to the agent cuts long tool traffic on "
            f"{sum(1 for r in rows if r['tool_cap'] is not None)} tasks to fit one argument "
            f"({sum(r['instruction_cuts'] for r in rows)} parts in all), with the whole conversation in the "
            f"container; the graders read it whole (#7).",
            "- **In a stored tool output.** v1's verifier keeps every output whole."]
    runs = {run.name: run_cuts(run) for run in args.runs}
    for name, c in runs.items():
        out.append(f"  - {name}: {c['cut_answers']} of {c['answers']} stored answers have cut outputs "
                   f"({c['cuts']:,} cuts).")
    out.append("- **In a grader's view.** View 1 bounded every reading's record at 24,000 characters. Since view "
               "2 a grader is shown the whole record, shortened only when its model refuses the length, to a "
               "marked fallback, and each reading records which (`shown`).")
    for name, c in runs.items():
        recorded = (f"{c['shortened']} of the {c['view_recorded']} that record their view were shortened"
                    if c["view_recorded"] else "none records its view (view 1)")
        out.append(f"  - {name}: {c['readings']} graded readings; {recorded}.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
