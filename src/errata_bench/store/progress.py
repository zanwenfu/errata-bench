"""What a stage did, and how to run its jobs without swallowing an error."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class Progress:
    """What a stage did, for the run log."""

    stage: str
    took_s: float = 0.0
    produced: int = 0
    skipped: int = 0
    failed: int = 0
    # Left for another run by --max-rows, and turned away by a gate. Both used
    # to be counted in `skipped` and printed as "already done": a fresh
    # directory of eight moments under --max-rows 2 said "2 produced, 6 already
    # done", and a rebuild that rejected a task and deleted its eight
    # downstream rows said "1 already done".
    capped: int = 0
    rejected: int = 0
    notes: list[str] = field(default_factory=list)

    def cap(self, todo: list, limit: int, total: int) -> list:
        """Apply --max-rows to a stage's work, and keep the two reasons apart.

        `total` is everything the stage could have been asked to do; `todo` is
        what is left of it. The difference was done already, and whatever of
        `todo` does not fit under `limit` is over the cap.
        """
        kept = todo[:limit]
        self.skipped = total - len(todo)
        self.capped = len(todo) - len(kept)
        return kept

    def line(self) -> str:
        bits = [f"{self.produced} produced"]
        if self.skipped:
            bits.append(f"{self.skipped} already done")
        if self.capped:
            bits.append(f"{self.capped} over --max-rows")
        if self.rejected:
            bits.append(f"{self.rejected} rejected")
        if self.failed:
            bits.append(f"{self.failed} failed")
        head = f"  {self.stage:10s} {self.took_s:6.0f}s  {', '.join(bits)}"
        # Notes were written in ten places and printed in none. Everything a
        # stage refuses or skips says so here -- a rebuild declining to empty a
        # finished directory, answers whose task has changed under them, a
        # grader that was never calibrated -- and all of it went to a field no
        # code read. A stage that stops to protect something has to say so on
        # the screen, or the protection is indistinguishable from doing nothing.
        return "\n".join([head] + [f"             {n}" for n in self.notes])


async def _gather(coros, limit: int):
    """Run with a ceiling on concurrency.

    The ceiling matters on a laptop: unbounded fan-out over hundreds of moments
    opens hundreds of connections and, at the attempt stage, would start a
    container per task.
    """
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            try:
                return await c
            except Exception as e:  # noqa: BLE001 - one job, not the stage
                # `asyncio.gather` cancels its siblings when one raises, so a
                # single unhandled error threw away every job in flight: four
                # containers started, nothing written, no Progress returned --
                # which means the stage line never prints, the run exits on a
                # traceback rather than a count, and the closing sweep that
                # removes leftover containers never runs. A job that dies is
                # one failure; the rest of the stage carries on and the row is
                # retried next time.
                print(f"  ! {type(e).__name__}: {e}", flush=True)
                return False

    return await asyncio.gather(*(run(c) for c in coros))


async def _gather_in_turn(groups, limit: int) -> list:
    """Run groups of jobs, at most `limit` groups at once, each group's jobs one after another (B-256).

    The readings of one question share one prompt, and the provider caches a
    prompt it has just read. On the D-40 smoke run gpt-6-astra billed the second
    and third readings of an answer at a third of the first, because they
    followed it; sent at once, as `_gather` sends them, all three pay in full.
    Each group is a list of zero-argument callables that return coroutines.
    One job that raises is one failure, as in `_gather`, and the group goes on.
    The results come back flat, group by group.
    """
    async def run(jobs):
        out = []
        for job in jobs:
            try:
                out.append(await job())
            except Exception as e:  # noqa: BLE001 - one job, not the stage
                print(f"  ! {type(e).__name__}: {e}", flush=True)
                out.append(False)
        return out

    done = await _gather([run(g) for g in groups], limit)
    return [r for g in done for r in (g if isinstance(g, list) else [g])]


def in_turn(jobs: list[tuple], key) -> list[list]:
    """`jobs` grouped by `key(job)`, in first-seen order: one group per question asked more than once."""
    groups: dict = {}
    for job in jobs:
        groups.setdefault(key(job), []).append(job)
    return list(groups.values())


