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
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        bits = [f"{self.produced} produced"]
        if self.skipped:
            bits.append(f"{self.skipped} already done")
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


