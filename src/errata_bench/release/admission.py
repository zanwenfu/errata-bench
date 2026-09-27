"""A judge's admission to each task, read from the frozen release, not the corpus (v1 step 4).

A task is graded only by a judge admitted to it: one that reads the task's
known-wrong and known-right answers correctly, in both orders
(`stages.building.stage_calibrate`), and whose readings of the task's controls
behave -- a do-nothing answer fails, an accurate summary passes
(`stages.building.stage_control`). Both stages read each task's conversations
through `score.attempt.control_conversations_for`, which renders them from the
SWE-chat corpus. The frozen release keeps exactly what it renders
(`grading/controls.json`, written by the freeze from the same function), so
while `conversations_from(release)` is open the stages read them there and
need no corpus.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path


def release_conversations(release: Path, tasks) -> dict[str, dict]:
    """Each task's control conversations, as the freeze kept them."""
    return {t.task_id: json.loads((release / "tasks" / t.task_id / "grading" / "controls.json").read_text())
            for t in tasks}


@contextmanager
def conversations_from(release: Path):
    """While open, the admission stages read the controls' conversations from ``release``."""
    from ..score import attempt

    saved = attempt.control_conversations_for
    attempt.control_conversations_for = lambda tasks: release_conversations(release, tasks)
    try:
        yield
    finally:
        attempt.control_conversations_for = saved
