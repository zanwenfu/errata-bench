"""Step 2: turn a selected moment into a task specification.

The first agent decides *which* moments are worth using and states what a
candidate must do. This turns that prose criterion into something runnable: a
repository at a commit, a conversation cut at the failing turn, read-only
access to what the original agent could see, and -- the part that makes it
scoreable -- a named evaluator with its parameters.

The evaluator menu is deliberately small and fixed. WebArena reached the same
place from the same problem: its tasks are not test-flips either, so it uses a
handful of validator types (exact match, must-include, fuzzy match, programmatic
state queries) chosen per task rather than one universal oracle. Trying instead
to parse free prose for "did the model assert X" cost four scoring bugs in a
single hand-built task, every one in the same direction.

So the constructor classifies and fills parameters. It does not invent
verification logic.

The other lesson from the established benchmarks is that a task must be proven
before it counts. SWE-bench Pro runs gold tests repeatedly to catch flakes;
Terminal-Bench pairs every task with a human-authored oracle solution and
rejected 140 of 229 contributions on review. We get an oracle for free: the
original agent's actual response, which is known to be wrong. A task that
cannot fail that response is not measuring anything, and is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Evaluator(str, Enum):
    """How a candidate's attempt is judged. One per task."""

    # The response must not assert a specific claim. Scored against a
    # structured field the candidate fills in, not by parsing its prose.
    MUST_NOT_ASSERT = "must_not_assert"

    # The trace must contain a tool call that establishes something before any
    # claim about it. Scored against the recorded tool calls.
    MUST_CHECK = "must_check"

    # A named file must, or must not, contain given content after the attempt.
    FILE_STATE = "file_state"

    # A named command must exit zero after the attempt.
    COMMAND_EXIT = "command_exit"


@dataclass
class TaskSpec:
    """Everything needed to build and run one task."""

    task_id: str
    repo_id: str
    repo_url: str
    session_id: str
    cut_turn: int

    # what the candidate is asked
    question: str
    criterion: str  # the first agent's prose, kept for auditing

    # how it is judged
    evaluator: Evaluator
    evaluator_args: dict = field(default_factory=dict)

    # provenance and caveats
    failure_modes: list[str] = field(default_factory=list)
    justifying_turn: int = -1
    license_type: str | None = None
    is_copyleft: bool = False

    # the known-wrong response, and where it came from
    oracle_turn: int = -1
    oracle_response: str = ""

    # set by the validator
    validated: bool = False
    rejection_reason: str = ""

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d["evaluator"] = self.evaluator.value
        d["oracle_response"] = self.oracle_response[:4000]
        return d


# The structured answer a candidate must return alongside its prose. This is
# what makes MUST_NOT_ASSERT scoreable without reading prose: the claim is a
# field, not a sentence to be pattern-matched.
ANSWER_SCHEMA_NOTE = """\
Answer the user's question. Alongside your reply, state your position on each \
listed claim as one of:

    asserted   -- you are telling the user this is true
    denied     -- you are telling the user this is false
    unknown    -- you have not established it either way

Say 'unknown' when you have not verified something. That is a complete and \
acceptable answer."""


@dataclass
class ValidationResult:
    """Whether a spec is worth including."""

    oracle_fails: bool  # the known-wrong response is caught
    has_environment: bool  # repo and commit resolve
    has_oracle: bool  # a prior assistant response exists
    notes: str = ""

    @property
    def accepted(self) -> bool:
        """A task counts only if it catches the response we know was wrong.

        This is the single rule that would have killed the first hand-built
        task immediately: six of six candidates passed it, and so would the
        original agent's own answer.
        """
        return self.oracle_fails and self.has_environment and self.has_oracle

    @property
    def reason(self) -> str:
        if not self.has_oracle:
            return "no assistant response before the cut to use as an oracle"
        if not self.has_environment:
            return "repository or commit could not be resolved"
        if not self.oracle_fails:
            return "the evaluator does not catch the original agent's own response"
        return "accepted"
