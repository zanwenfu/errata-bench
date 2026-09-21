"""The eleven stages, in order, and the driver that runs them.

Each stage reads the previous stage's file and writes its own. A stage that
finds its output already present skips the rows it has, so an interrupted run
resumes instead of restarting, and a stage can be re-run alone after its code
changes without redoing the ones before it.

    moments      pushback moments worth reading      -> moments.jsonl
    triage       does the agent have work to object to-> triaged.jsonl
    read         which are genuine agent error       -> readings.jsonl
    locate       the four turns that define a task   -> trajectories.jsonl
    signature    what the defect looks like in a tree-> signatures.jsonl
    screen       answerable, leaking, repairable     -> screened.jsonl
    build        environment + defect verification   -> tasks.jsonl
    calibrate    can the judge read this task's pair -> calibration.jsonl
    control      does a do-nothing answer fail       -> controls.jsonl
    attempt      run candidates                      -> answers.jsonl
    grade        read each answer three ways         -> attempts.jsonl
    report       the numbers                         -> report.json

The stages before `attempt` cost roughly eight model calls per moment and no
containers, so a cheap pass establishes the yield before anything expensive
starts.
"""

from __future__ import annotations

from ..store import STAGES, Paths, Progress, only_one
from .building import stage_build, stage_calibrate, stage_control
from .screening import (
    stage_locate, stage_read, stage_screen, stage_signature, stage_triage,
)
from .scoring import stage_attempt, stage_grade, stage_report

async def run_stages(
    root: Path,
    stages: tuple[str, ...] = STAGES,
    *,
    limit: int = 10_000,
    concurrency: int = 4,
    repeats: int = 3,
    grade_concurrency: int | None = None,
    passes: int = 1,
) -> list[Progress]:
    """Run the named stages in order, skipping work already recorded.

    ``grade_concurrency`` is separate because grading is the one stage bounded
    by a provider rather than by this laptop, and the right number depends on
    which judge it is: one deployment answers in two to six minutes and takes
    ten at once, another is capped at six calls a minute and errors above four.
    Left unset it matches ``concurrency``, so a careless run is merely slow.
    """
    paths = Paths(root)
    # One process per run directory. Every stage reads its output file to decide
    # what is left and appends its results, so two runs over one directory do
    # not collide -- they each do all of it. Measured: two `stages` over four
    # moments produced 8 triaged rows, 16 readings, 32 trajectories, 64
    # signatures and 95 screened, the factor doubling at each stage because the
    # next one reads the duplicated file, and 24 container runs for 12 answers.
    # Nothing is lost; everything is paid for twice, and a later solo pass does
    # not clean it up.
    with only_one(root, "running stages"):
        return await _run_stages(
            paths, stages, limit, concurrency, repeats, grade_concurrency, passes
        )


async def _run_stages(paths, stages, limit, concurrency, repeats, grade_concurrency, passes=1):
    out = []
    for name in stages:
        if name == "triage":
            out.append(await stage_triage(paths, limit, concurrency))
        elif name == "read":
            out.append(await stage_read(paths, limit, concurrency))
        elif name == "locate":
            out.append(await stage_locate(paths, limit, concurrency))
        elif name == "signature":
            out.append(await stage_signature(paths, limit, concurrency))
        elif name == "screen":
            out.append(await stage_screen(paths, limit, concurrency, passes))
        elif name == "build":
            out.append(stage_build(paths, limit))
        elif name == "calibrate":
            out.append(await stage_calibrate(paths, limit, concurrency))
        elif name == "control":
            out.append(await stage_control(paths, limit, concurrency))
        elif name == "attempt":
            out.append(await stage_attempt(paths, limit, concurrency, repeats))
        elif name == "grade":
            out.append(
                await stage_grade(paths, limit, grade_concurrency or concurrency)
            )
        elif name == "report":
            out.append(stage_report(paths))
        else:
            raise ValueError(f"unknown stage: {name}")
        print(out[-1].line(), flush=True)
    return out

