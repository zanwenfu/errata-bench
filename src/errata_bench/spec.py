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
    # What the agent ran before writing each of those answers, taken from the
    # transcript. The judge is shown the candidate's tool calls when it asks
    # whether a claim was established, so calibration has to show it the same
    # thing for the two known answers -- otherwise the pair it is calibrated
    # on is judged under a different rule from the candidates it then grades.
    #
    # None and [] are different and must stay so. None means a task built
    # before this existed, where nobody knows what the agent ran; [] means it
    # ran nothing, which is the common case and is itself the finding. Telling
    # a judge "no tool calls were made" about an answer whose trace was never
    # recovered is a false statement about the known-right answer.
    oracle_calls: list[dict] | None = None
    criterion_calls: list[dict] | None = None

    # Turns dropped because they revealed the agent had been failing. The
    # conversation is rendered without them, so a candidate cannot take a hint
    # that the original agent never had. Recorded rather than silently applied:
    # anyone auditing a task needs to know the transcript is not verbatim.
    redacted_turns: list[int] = field(default_factory=list)
    # Turns whose text was edited to remove a hint while keeping the substance.
    # Dropping such a turn takes the work with it: one 3,735-character message
    # was removed for the five words "we seem to be going in circles", and with
    # it went the error output, the failing table name and the requirement.
    rewritten_turns: dict[str, str] = field(default_factory=dict)
    # How many of the agent's own edits were replayed onto the base commit so
    # the tree matches what the transcript describes at the cut.
    edits_replayed: int = 0

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


def fingerprint(task: Task) -> str:
    """A short hash of everything that decides how an answer is graded.

    Task identifiers are derived from the repository and the turn, so a rebuilt
    task keeps its name while its content changes -- a different base commit,
    more of the agent's edits replayed, a repaired transcript, a re-read defect.
    An answer collected before such a rebuild was written about a different
    question, and grading it against the new task's reference answers scores it
    on a problem the candidate was never shown.

    So the answer carries this, and the grading stage checks it. Only the
    fields a grade actually depends on are included: the tree the candidate
    started from, the conversation it was cut at, the defect, and the two
    reference answers it is judged against.
    """
    import hashlib

    material = "\x00".join(str(x) for x in (
        task.task_id, task.sha, task.kind, task.defect,
        task.oracle, task.criterion, task.signature_path, task.signature_token,
        task.edits_replayed,
        # Which conversation, and where it is cut. The session was missing, so
        # a task rebuilt onto a different session -- the repaired-transcript
        # case this exists for -- kept its stamp and every stored answer was
        # graded as though it had been asked the same question.
        task.session_id, task.cut_turn,
        sorted(task.redacted_turns),
        sorted((task.rewritten_turns or {}).items()),
        # What the judge is shown beside each reference answer during
        # calibration. Recovering these traces changes whether the task is
        # admitted at all, so it changes the question.
        task.oracle_calls, task.criterion_calls,
    ))
    return hashlib.sha1(material.encode()).hexdigest()[:16]


def write(tasks: list[Task], path: Path) -> None:
    """Write the task file atomically.

    build() rewrites this wholesale on every run, so writing in place would mean
    a kill partway through leaves a truncated task list and loses the rest.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(t.to_json()) + "\n" for t in tasks))
    tmp.replace(path)


def read(path: Path) -> list[Task]:
    """Every complete task in the file, skipping any line left truncated."""
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(Task.from_json(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out


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
