"""One runnable task: what a candidate is shown, and what it is judged against.

Everything here is derived from a located trajectory. The four turns fix the
shape:

    cut       where the candidate's view of the conversation ends
    failed    the answer we know was wrong -- the oracle
    complaint the developer objecting to it -- never shown
    resolved  where the agent finally got it right -- the success criterion

The pair of answers is what makes this scoreable without hand-written rules. The
failed answer is a response known to be wrong, and the resolution is one known
to be right, both from the same developer on the same defect. A scorer that
cannot tell those two apart cannot be trusted on a candidate, and that check is
free: no labelling, no judgement call, just the transcript.

Two things are deliberately not stored. There is no hand-written evaluator per
task -- an earlier version of this pipeline kept a table keyed by repository,
which worked for eight repositories and no others. And there is no success
criterion written by anyone here: it is the resolution the developer accepted,
which is the only definition of "right" this corpus actually contains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# A failed answer shorter than this is not a wrong answer worth testing against.
# Two of twenty-seven located trajectories have one of 22 and 5 characters --
# an acknowledgement or a fragment, not a claim a candidate could repeat.
MIN_ORACLE_CHARS = 40


@dataclass
class Task:
    """A benchmark task, ready to run."""

    task_id: str
    repo_id: str
    repo_url: str
    sha: str  # the tree the candidate starts from
    session_id: str

    # the four turns
    cut_turn: int
    failed_turn: int
    complaint_turn: int
    resolved_turn: int

    # the two answers, from the transcript
    oracle: str  # the failed answer: known wrong
    criterion: str  # the resolution: known right

    # what the defect is, and how its presence was established
    defect: str
    kind: str  # present | introduced | none
    signature_path: str = ""
    signature_token: str = ""
    strength: str = "token"  # token | file | declared
    presence_detail: str = ""

    # provenance
    license_type: str | None = None
    is_copyleft: bool = False
    rounds: int = 1

    @property
    def discriminates(self) -> bool:
        """Whether this task can distinguish a careful answer from a careless one.

        Both kinds can. A present-defect task fails a candidate that misses what
        is in the tree. An introduced-defect task fails one that fabricates the
        same thing the original agent fabricated -- and that agent was not
        unlucky, it wrote a false warning into CLAUDE.md while doing ordinary
        work, unprompted. A candidate that documents only what it verified has
        done the thing being measured, and never mentioning the subject is what
        success looks like rather than evidence of going off-target.

        This was briefly marked False for introduced-defect tasks on the grounds
        that "not inventing one specific falsehood is not an achievement". That
        was wrong. Avoiding a fabrication the original agent made, under the same
        conditions, is the whole point of the benchmark.

        What remains unmeasured is the *rate*: if almost every model passes these
        without effort, they are easy rather than broken, and only running more
        of them will show which. That is a question about difficulty, not about
        whether the task is valid.
        """
        return True

    @property
    def scoreable_structurally(self) -> bool:
        """Whether a file check can decide this task on its own.

        Only a token-strength defect can: its literal string is in the tree, so
        its absence afterwards means something definite. A file-strength defect
        was only ever confirmed to *live* somewhere -- "the polling loop never
        exited" leaves no string to look for -- and an introduced defect has
        nothing in the starting tree at all.

        Those still make good tasks. They just cannot be settled by grep, which
        is why the judge is not optional.
        """
        return self.strength == "token" and self.kind == "present"

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["oracle"] = self.oracle[:6000]
        d["criterion"] = self.criterion[:6000]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Task":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def write(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for t in tasks:
            fh.write(json.dumps(t.to_json()) + "\n")


def read(path: Path) -> list[Task]:
    return [
        Task.from_json(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


@dataclass
class Rejection:
    """A located trajectory that cannot become a task, and why."""

    repo_id: str
    complaint_turn: int
    reason: str


@dataclass
class BuildResult:
    tasks: list[Task] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    def summary(self) -> str:
        by_strength: dict[str, int] = {}
        for t in self.tasks:
            by_strength[t.strength] = by_strength.get(t.strength, 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in sorted(by_strength.items()))
        return f"{len(self.tasks)} tasks ({parts}); {len(self.rejected)} rejected"
