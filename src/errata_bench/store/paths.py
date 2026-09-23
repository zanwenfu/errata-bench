"""Where each stage's output lives, and what the stages are called.

Split out of pipeline.py, which held the storage layer and all eleven stages
in one 1,800-line module. The two halves shared exactly one reference, and it
was inside a docstring -- and keeping them together forced a circular import
with spec.py, worked around by six function-level imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STAGES = (
    "triage",
    "read",
    "locate",
    "signature",
    "screen",
    "build",
    "calibrate",
    "control",
    "attempt",
    "grade",
    "report",
)


# Every file a stage may read or write. Named rather than derived, because
# `__getattr__` answered any name at all: `paths.attemps` was a valid path to a
# file nothing writes, so a typo became a stage that found no work, did none,
# and reported success. A stage that reads the wrong file must fail loudly.
FILES = (
    "moments",
    "triaged",
    "readings",
    "trajectories",
    "signatures",
    "screened",
    "tasks",
    "calibration",
    "controls",
    "answers",
    "attempts",
    "rejections",
    "gate",
    # The instrument's own checks per task (D-36 A3): measured, never gated on.
    "instrument",
    # Which model each deployment served, at a stage's start and end (D-36 A6).
    "served",
)


@dataclass
class Paths:
    """Where each stage's output lives."""

    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name: str) -> Path:
        if name not in FILES:
            raise AttributeError(f"no stage file named {name!r}; expected one of {', '.join(FILES)}")
        return self.root / f"{name}.jsonl"

    @property
    def report(self) -> Path:
        return self.root / "report.json"


