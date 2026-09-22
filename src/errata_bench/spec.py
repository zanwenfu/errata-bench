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
import os
from dataclasses import dataclass, field
from pathlib import Path

# Module level, not inside the functions. These were function-level imports
# because pipeline.py imported spec.py and spec.py imported pipeline.py --
# a cycle that only worked because every one of them was deferred. The
# storage layer is its own package now and imports nothing from the domain,
# so the cycle is gone and the imports can say so.
from .store import held, load, replace

# A failed answer shorter than this is not a wrong answer worth testing against.
# Two of twenty-seven located trajectories have one of 22 and 5 characters --
# an acknowledgement or a fragment, not a claim a candidate could repeat.
MIN_ORACLE_CHARS = 40

#: How much of each reference answer is kept on disk, and therefore all the
#: fingerprint can describe. Written as a bare 6000 in `to_json` while
#: `fingerprint` hashed the field uncut, the same task had two stamps: the one
#: `stage_build` computes from the tasks in memory, and the one every other
#: stage computes after reading them back. `still_describes` compares those
#: two, so for any task whose oracle or criterion reached the cap it was False
#: for every downstream row -- each rebuild deleting its calibration, controls,
#: answers and graded attempts, the next stages re-paying for them, and the
#: next rebuild deleting them again, for ever. Two of the 120 tasks on disk sit
#: exactly at 6,000 characters, both built 09-22.
REFERENCE_CHARS = 6000


def within(tree: Path, rel: str) -> Path | None:
    """The path `rel` names inside `tree`, or None if it names anything else.

    For paths the *harness* joins to the tree, not the ones a candidate's tools
    ask for -- those go through `_safe`, which has to understand the container
    mount as well. `task.signature_path` is written by a model reading a
    transcript (`find/signature.py` asks for the path "exactly as the text
    gives it"), and transcripts are full of absolute paths. Joined unguarded,
    an absolute one replaces the tree outright: a probe with
    `signature_path="/Users/…/id_rsa"` read that file off the host and put its
    contents in the stored answer row, and since D-32 into the judge's prompt.

    The parent is resolved and the leaf is not, so a symlinked directory cannot
    be used to step outside while a symlink *at* the path is still reported as
    a link rather than read through, which is what `_snapshot` and `_capture`
    both promise.
    """
    if not rel or os.path.isabs(rel) or rel.startswith("~"):
        return None
    p = tree / rel
    try:
        p.parent.resolve(strict=False).relative_to(tree.resolve(strict=False))
    except (ValueError, OSError, RuntimeError):
        return None
    return p


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
    # How many of those actually tested the commit -- an old_string matched
    # against content the checkout already had. A Write cannot fail and neither
    # can an Edit onto a file a Write in the same replay created, so
    # `edits_replayed` alone reads as evidence it is not: 13 replayed and 7
    # verified is what dipasqualew-vibereq-162's tree is really worth (G-37).
    # Deliberately not in `fingerprint`: it changes nothing about what the
    # candidate is asked, and stamping it would call every stored answer stale.
    edits_verified: int = 0

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
        d["oracle"] = self.oracle[:REFERENCE_CHARS]
        d["criterion"] = self.criterion[:REFERENCE_CHARS]
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
        # Cut to what `to_json` keeps, so the stamp a task has in memory and
        # the stamp it has after a round trip through disk are the same one.
        task.oracle[:REFERENCE_CHARS], task.criterion[:REFERENCE_CHARS],
        task.signature_path, task.signature_token,
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
    # Through the same writer as every other stage file: a per-process
    # temporary name and the run directory's lock. With one fixed `.tmp`, four
    # concurrent writers raised FileNotFoundError on 92 of 240 rewrites and a
    # reader saw a zero-task list four times -- and `stage_build` has no handler
    # there, so the run dies after paying for every clone and export.

    with held(path):
        replace(path, [t.to_json() for t in tasks])


def read(path: Path) -> list[Task]:
    """Every complete task in the file, skipping any line left truncated."""
    # A missing file is an empty task list, as it is everywhere else. Without
    # this, `--only grade` on a directory with no tasks.jsonl ended in a
    # traceback rather than "nothing to do".
    if not path.exists():
        return []
    # Through the shared reader, which decodes per line from bytes. This used
    # `read_text()`, which decodes the whole file in one call, so a row cut
    # inside a UTF-8 sequence raised UnicodeDecodeError out of here and lost
    # the entire task list rather than the torn row -- B-165, in the reader for
    # tasks.jsonl. `write` was pulled into the shared writer by B-182 and this
    # was left behind. tasks.jsonl arrives by other routes than this code:
    # candidate directories are copied in, and an interrupted copy is exactly
    # the shape that produces one.

    out, dropped = [], 0
    for row in load(path):
        try:
            out.append(Task.from_json(row))
        except (ValueError, TypeError, KeyError):
            dropped += 1
    # Said out loud. Dropped with a bare `continue`, a task vanishing from the
    # file shrank the benchmark with nothing on screen.
    if dropped:
        print(f"  note: {dropped} row(s) in {path.name} are not readable as tasks")
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
