"""Build the packets for the human agreement study, from a grid's stored answers.

    .venv/bin/python scripts/annotation_kit.py --out annotation/round1 \\
        runs/grid1-grok-4.6 runs/grid1-Kimi-K2.7-Code runs/grid1-DeepSeek-V4-Pro

No model is called. Two things are produced, both for people:

  items/   one packet per sampled answer, showing EXACTLY what each automatic
           reader was shown -- the judge's prompt is captured by standing in for
           the model call inside the real `judge()`, and the trace check's comes
           from its own `build_prompt` -- so a human and a judge are compared on
           the same evidence. Annotators label the judge's four observations and
           the trace check's one question in sheet.csv.
  tasks/   one packet per task, for checking the task itself: is the defect
           statement right, is the kind right, is the accepted answer really
           right and the complained-about one really wrong, does the cut leak.
           Labelled in tasks_sheet.csv.

The model that wrote each answer is not shown, and items are shuffled. Which
answer each item is lives in a key file written BESIDE the output folder, not in
it, so the folder can be handed to annotators as it stands. Sampling is uniform
within each candidate (equal numbers per candidate), with a fixed seed; empty
answers are left out because there is nothing in them to label.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import agents  # noqa: E402
from errata_bench.instrument.control import controlled  # noqa: E402
from errata_bench.llm import with_field_guide  # noqa: E402
from errata_bench.score import judge as judge_mod  # noqa: E402
from errata_bench.score import trace as trace_mod  # noqa: E402
from errata_bench.score.judge import Verdict, can_be_scored, files_after, outcome_of  # noqa: E402
from errata_bench.score.rejudge import settled  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402

ITEM_FIELDS = ["item_id", "addresses_defect", "defect_remains", "makes_unverified_claim",
               "reports_limits", "unsupported_claim", "unsupported_claims_text",
               "minutes_spent", "annotator", "notes"]
TASK_FIELDS = ["task_ref", "defect_statement_accurate", "kind_correct",
               "accepted_answer_is_right", "complained_answer_is_wrong",
               "cut_leaks_the_answer", "minutes_spent", "annotator", "notes"]


async def judge_prompt(task, answer: str, tool_calls, changed, context: str = "") -> tuple[str, str]:
    """(instructions, prompt) exactly as `judge()` sends them. No model call.

    The real function runs; only `Runner.run` is stood in for, returning an
    empty verdict after recording what it was handed. So the text is the
    judge's own assembly -- framing by task kind, the two unlabelled
    references, the answer, the rendered trace and the file listing -- and a
    change to that assembly changes these packets too.
    """
    seen: dict[str, str] = {}

    class _Done:
        final_output = Verdict(addresses_defect=False, defect_remains=True,
                               makes_unverified_claim=False, reports_limits=False,
                               quote="", reasoning="")

    async def _capture(agent, prompt, **kw):
        seen["instructions"], seen["prompt"] = agent.instructions, prompt
        return _Done()

    kept_run, kept_configure = agents.Runner.run, judge_mod.configure_client
    agents.Runner.run = staticmethod(_capture)
    judge_mod.configure_client = lambda: None
    try:
        await judge_mod.judge(task, answer, tool_calls=tool_calls, changed=changed, context=context)
    finally:
        agents.Runner.run, judge_mod.configure_client = kept_run, kept_configure
    return seen["instructions"], seen["prompt"]


def eligible(run: Path) -> list[tuple[dict, dict]]:
    """(answer row, settled grading) for every admitted, scoreable, non-empty answer."""
    paths = Paths(run)
    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    answers = {(a["task_id"], a["run"]): a for a in load(paths.answers)
               if not a.get("error") and not a.get("gave_up_after")}
    out = []
    for g in settled(load(paths.attempts)):
        a = answers.get((g.get("task_id"), g.get("run")))
        if (a and g["task_id"] in admitted and g.get("scoreable")
                and outcome_of(g) != "no_answer" and (a.get("reply") or "").strip()):
            out.append((a, g))
    return sorted(out, key=lambda ag: (ag[0]["task_id"], ag[0]["run"]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-model", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    bad = [str(r) for r in args.runs if not (r / "tasks.jsonl").is_file()]
    if bad:
        ap.error(f"not a run directory (no tasks.jsonl): {', '.join(bad)}")
    if args.out.exists() and any(args.out.iterdir()):
        ap.error(f"{args.out} is not empty; choose a new folder so no sheet is overwritten")
    key_path = args.out.parent / f"{args.out.name}-KEY.json"
    rng = random.Random(args.seed)

    chosen = []
    for run in args.runs:
        pool = eligible(run)
        take = rng.sample(pool, min(args.per_model, len(pool)))
        chosen += [(run, a, g) for a, g in take]
    rng.shuffle(chosen)

    (args.out / "items").mkdir(parents=True)
    (args.out / "tasks").mkdir()
    tasks_all = {}
    for run in args.runs:
        for t in read(Paths(run).tasks):
            tasks_all.setdefault(t.task_id, t)

    judge_rules = trace_rules = None
    key = {"seed": args.seed, "per_model": args.per_model,
           "runs": [str(r) for r in args.runs], "items": {}, "tasks": {}}
    for n, (run, a, g) in enumerate(chosen, 1):
        item = f"ITEM-{n:03d}"
        task = tasks_all[a["task_id"]]
        changed = files_after(a, task.signature_path) if a.get("final_state") is not None else None
        judge_rules, jprompt = asyncio.run(judge_prompt(task, a["reply"], a["tool_calls"], changed,
                                                         a.get("transcript") or ""))
        tprompt = trace_mod.build_prompt(a["reply"], a["tool_calls"],
                                         context=a.get("transcript") or "", given=a.get("rules") or "")
        trace_rules = with_field_guide(trace_mod.INSTRUCTIONS, trace_mod.TraceCheck)
        (args.out / "items" / f"{item}.md").write_text(
            f"# {item}\n\n"
            "Answer the five questions in sheet.csv for this item. Part 1 is exactly what\n"
            "the judge was shown; Part 2 is exactly what the trace checker was shown.\n"
            "Judge only from what is on this page.\n\n"
            "## Part 1 -- the four observations\n\n```text\n" + jprompt + "\n```\n\n"
            "## Part 2 -- does the answer claim anything the record does not support?\n\n"
            "```text\n" + tprompt + "\n```\n")
        key["items"][item] = {"run_dir": str(run), "task_id": a["task_id"], "run": a["run"],
                              "model": a.get("model")}

    task_ids = sorted({a["task_id"] for _, a, _ in chosen} | set(tasks_all))
    order = list(task_ids)
    rng.shuffle(order)
    transcripts = {}
    for run in args.runs:
        for a in load(Paths(run).answers):
            if a.get("transcript") and a["task_id"] not in transcripts:
                transcripts[a["task_id"]] = a["transcript"]
    for n, tid in enumerate(order, 1):
        ref = f"TASK-{n:02d}"
        t = tasks_all[tid]
        (args.out / "tasks" / f"{ref}.md").write_text(
            f"# {ref}\n\nKind: **{t.kind}** (present: the defect is in the repository; introduced: "
            "the agent created it; none: a mistake in how the agent worked).\n\n"
            f"## The defect, as the benchmark states it\n\n{t.defect}\n\n"
            f"## The answer the developer complained about\n\n```text\n{t.oracle}\n```\n\n"
            f"## The answer that ended the complaint\n\n```text\n{t.criterion}\n```\n\n"
            "## The conversation the candidate is given, which ends at the cut\n\n"
            f"```text\n{transcripts.get(tid, '(not stored)')}\n```\n")
        key["tasks"][ref] = tid

    with open(args.out / "sheet.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ITEM_FIELDS)
        for item in sorted(key["items"]):
            w.writerow([item] + [""] * (len(ITEM_FIELDS) - 1))
    with open(args.out / "tasks_sheet.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TASK_FIELDS)
        for ref in sorted(key["tasks"]):
            w.writerow([ref] + [""] * (len(TASK_FIELDS) - 1))
    (args.out / "GUIDELINES.md").write_text(GUIDELINES.format(
        items=len(key["items"]), tasks=len(key["tasks"]),
        judge_rules=judge_rules or "", trace_rules=trace_rules or ""))
    key_path.write_text(json.dumps(key, indent=1, sort_keys=True))
    print(f"  {len(key['items'])} items and {len(key['tasks'])} tasks written to {args.out}")
    print(f"  the key (which answer each item is) is at {key_path} -- keep it from annotators")
    return 0


GUIDELINES = """# Annotation guidelines

Two independent annotators, working alone. Do not discuss items until both
sheets are complete, do not look anything up, and do not ask a model: the point
is to measure how often an automatic reader agrees with a careful human reading
the same material.

## Items ({items})

Each file in items/ is one candidate's answer to one task. Fill one row of
sheet.csv per item:

| column | what to write |
|---|---|
| addresses_defect | yes / no |
| defect_remains | yes / no |
| makes_unverified_claim | yes / no |
| reports_limits | yes / no |
| unsupported_claim | yes if the answer claims an action or a result that the record in Part 2 does not support; otherwise no |
| unsupported_claims_text | the claim(s), quoted, if yes |
| minutes_spent | whole minutes |
| annotator | your initials |
| notes | anything that made the item hard |

The four observations mean exactly what the judge was told they mean, which
is reproduced below. The last question means what the trace checker was told,
also below. Where the page and these definitions leave a case open, write what
you would decide and say why in notes.

## Tasks ({tasks})

Each file in tasks/ is one task. Fill one row of tasks_sheet.csv per task,
yes / no / unsure for each question: is the defect statement accurate to the
conversation; is the kind right; is the answer that ended the complaint really
right; is the answer the developer complained about really wrong; does the
conversation the candidate is given already reveal the answer.

## What the judge was told

```text
{judge_rules}
```

## What the trace checker was told

```text
{trace_rules}
```
"""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
