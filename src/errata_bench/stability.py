"""How reliably a judge can read a task's known pair.

A task enters the benchmark only if the judge can tell the developer's rejected
answer from the one they accepted, in both orders. That admission is a single
yes/no decision, it was taken once per run, and it is not reproducible.

Measured on 09-20: the same judge, the same nine tasks, six separate
occasions. Seven tasks passed every time. Two did not --
`nuttycc-LuminTime-68` read the developer's own accepted answer as not
resolving the problem on two of the six, and only in one of the two orders it
is shown in. Because a task carries three attempts, one task coming or going
moves a model's score by up to three: Kimi's 9 of 24 became 6 of 20 on the
second grading, a third of its score, with the same stored answers graded both
times and nothing different about the model.

The judge's *grading* is steady by comparison -- it agreed with itself on 80 of
81 answers. It is this one admission decision that wobbles, and it wobbles
where it is most expensive.

So it is asked repeatedly instead of once. A task whose answer changes between
askings cannot be scored reliably by this judge, and admitting it puts a coin
flip worth three attempts into a published rate. Nothing here re-reads a
candidate's answer or runs anything: it is the known pair, which is two fixed
strings from the transcript, read again.

    python run.py gate --run <dir> --judge <model> --passes 10

Results go to `<run>/gate.jsonl`, one row per (task, pass), resumable and
retried on error like every other stage.
"""

from __future__ import annotations

import time
from pathlib import Path

from .judge import can_be_scored
from .pipeline import Paths, Progress, _gather, append, completed, load


def observations(run: Path, model: str, *, passing=None) -> dict[str, list[bool]]:
    """Every recorded reading of each task's known pair by this judge.

    Both the repeated readings from this tool and the single reading each
    regrade took on its way past. They are the same measurement -- the same
    two strings, the same question -- so a regrade's calibration row is another
    draw and is counted as one.
    """
    seen: dict[str, list[bool]] = {}
    paths = Paths(run)
    for row in load(paths.gate):
        if row.get("judge_model") == model and not row.get("error"):
            # Re-derived from the four stored readings rather than read off the
            # verdict recorded at the time. What counts as the known-right
            # answer reading correctly is a rule, and the rule changes; the
            # readings do not. So tightening it costs nothing and cannot
            # silently leave old verdicts in place beside new ones.
            seen.setdefault(row["task_id"], []).append(
                can_be_scored(row, **({"passing": passing} if passing else {}))
            )
    rejudged = run / "rejudge"
    if rejudged.exists():
        for d in sorted(p for p in rejudged.iterdir() if p.is_dir()):
            for row in load(Paths(d).calibration):
                if row.get("judge_model") == model and not row.get("error"):
                    seen.setdefault(row["task_id"], []).append(
                        can_be_scored(row, **({"passing": passing} if passing else {}))
                    )
    return seen


def stable(run: Path, model: str, *, least: int = 2, passing=None) -> tuple[set[str], dict]:
    """The tasks this judge admitted every time, and the full tally.

    ``least`` is how many readings a task needs before its steadiness means
    anything: one reading that happened to hold is not evidence of holding.
    """
    seen = observations(run, model, passing=passing)
    tally = {
        task: {"held": sum(1 for x in xs if x), "asked": len(xs)}
        for task, xs in sorted(seen.items())
    }
    keep = {
        task for task, t in tally.items()
        if t["asked"] >= least and t["held"] == t["asked"]
    }
    return keep, tally


async def measure(
    run: Path, model: str, *, passes: int = 10, concurrency: int = 6
) -> Progress:
    """Read every task's known pair `passes` times over, and record each answer."""
    from .judge import calibrate
    from .spec import read

    paths = Paths(run)
    p = Progress("gate")
    t0 = time.monotonic()
    tasks = read(paths.tasks)
    done = {
        (r["task_id"], r["pass"])
        for r in completed(paths.gate) if r.get("judge_model") == model
    }
    jobs = [(t, n) for t in tasks for n in range(passes) if (t.task_id, n) not in done]
    p.skipped = len(tasks) * passes - len(jobs)
    p.notes.append(f"{model} reading {len(tasks)} known pairs, {passes} times over")
    if not jobs:
        p.took_s = time.monotonic() - t0
        return p

    async def one(task, n):
        try:
            c = await calibrate(task, model=model)
        except Exception as e:  # noqa: BLE001 - dropped and retried, as elsewhere
            append(paths.gate, {
                "task_id": task.task_id, "pass": n, "judge_model": model,
                "error": f"{type(e).__name__}: {e}",
            })
            return False
        append(paths.gate, {
            "task_id": task.task_id,
            "pass": n,
            "judge_model": model,
            # The verdict this tool exists to measure.
            "holds": c.sound,
            "strict": c.strict,
            # The four readings behind it, so a wobble can be told apart as a
            # pass/fail flip or a side reading moving.
            "failed_solved": c.failed_solved,
            "resolution_solved": c.resolution_solved,
            "failed_solved_swapped": c.failed_solved_swapped,
            "resolution_solved_swapped": c.resolution_solved_swapped,
            "failed_outcome": c.failed_outcome,
            "resolution_outcome": c.resolution_outcome,
            "failed_outcome_swapped": c.failed_outcome_swapped,
            "resolution_outcome_swapped": c.resolution_outcome_swapped,
            "detail": c.detail,
        })
        return c.sound

    results = await _gather([one(t, n) for t, n in jobs], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


def report(run: Path, model: str, *, least: int = 2) -> str:
    """The tally, task by task, and what it costs to keep only the steady ones."""
    keep, tally = stable(run, model, least=least)
    if not tally:
        return f"  no readings of a known pair by {model} in {run}"
    lines = [f"\n  {model} reading each task's known pair, in {run}\n",
             f"  {'task':34s} {'held':>10s}   admitted every time?"]
    for task, t in sorted(tally.items(), key=lambda kv: (kv[1]["held"] / kv[1]["asked"], kv[0])):
        mark = "yes" if task in keep else ("no" if t["asked"] >= least else "too few readings")
        lines.append(f"  {task[:34]:34s} {t['held']:>4}/{t['asked']:<4}   {mark}")
    wobbled = [t for t in tally if t not in keep and tally[t]["asked"] >= least]
    lines.append(
        f"\n  steady: {len(keep)} of {len(tally)} tasks"
        + (f"; dropped for wobbling: {', '.join(sorted(wobbled))}" if wobbled else "")
    )
    return "\n".join(lines)
