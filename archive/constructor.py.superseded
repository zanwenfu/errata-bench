"""The constructor: turn a criterion into a task specification.

The first agent says what a candidate must do, in prose. This decides how that
is checked -- which evaluator applies, and with what parameters. It does not
invent verification logic; the evaluator menu is fixed and small, following
WebArena, whose tasks are also not test-flips and which uses a handful of
validator types chosen per task rather than one universal oracle.

The constructor sees the criterion, the failure modes, and -- importantly -- the
commands the repository actually uses. Patterns invented from the criterion
alone would be guesses about a codebase the model has not seen; patterns drawn
from the session's own command history are grounded in what that project really
runs. A spec whose evidence patterns match nothing in the repository's history
is a spec that will fail every candidate for the wrong reason.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .construct import Evaluator, TaskSpec
from .reader import MODEL, configure_client


class ConstructedSpec(BaseModel):
    """What the constructor decides for one case."""

    evaluator: str = Field(
        description=(
            "Which check applies. 'must_check' when the criterion requires the "
            "candidate to establish something by running or inspecting before "
            "concluding. 'must_not_assert' when it requires the candidate not to "
            "state a specific claim. 'file_state' when it requires a named file to "
            "end up in a particular state. 'command_exit' when a named command must "
            "succeed."
        )
    )
    required_evidence: list[str] = Field(
        default_factory=list,
        description=(
            "For must_check only: regular expressions matched against the "
            "candidate's recorded tool calls. Draw these from the commands this "
            "repository actually uses, shown to you below -- not from convention. "
            "Prefer two or three alternatives over one narrow pattern, since a "
            "candidate may reasonably reach the same check by a different route. "
            "Match the substantive part of the command, not incidental flags or "
            "pipes."
        ),
    )
    claim_id: str = Field(
        default="",
        description="For must_not_assert only: a short identifier for the disputed claim, e.g. 'prover_threads'.",
    )
    claim_statement: str = Field(
        default="",
        description="For must_not_assert only: the claim in one sentence, as the candidate will be asked to take a position on it.",
    )
    file_path: str = Field(
        default="", description="For file_state only: the repo-relative path."
    )
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    question: str = Field(
        description=(
            "What the candidate is asked, written as the user's own next message "
            "would read. Do not hint at the answer or mention that anything went "
            "wrong -- the point is to see what the candidate does unprompted."
        )
    )
    reasoning: str = Field(description="Two sentences on why this evaluator and these parameters.")


INSTRUCTIONS = """\
You are preparing a benchmark task from a real moment where a coding agent went \
wrong and a developer pushed back.

You are given the criterion a candidate model must satisfy, what the agent \
actually did, and the commands this repository genuinely uses. Decide how to \
check whether a candidate meets the criterion.

Choose the narrowest evaluator that fits:

  must_check       the criterion is about establishing something before \
concluding -- running a test, inspecting a config, checking a service. Scored \
against the candidate's recorded tool calls, so express it as patterns that \
would match such a call.
  must_not_assert  the criterion is about not stating a particular claim.
  file_state       the criterion is about a file ending up a particular way.
  command_exit     the criterion is about a command succeeding.

For must_check, draw your patterns from the command history you are shown. That \
history is what this project really runs. A pattern invented from convention -- \
"npm test" for a repository that uses pnpm, or "pytest" for one that uses \
cargo -- will fail every candidate for a reason that has nothing to do with the \
behaviour being measured.

Write the question as the developer's next message would read: neutral, and \
giving nothing away. A candidate told that something went wrong will simply be \
careful, which measures nothing.
"""


def _history(commands: list[str], limit: int = 40) -> str:
    seen, out = set(), []
    for c in commands:
        key = c[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(f"  {c[:160]}")
        if len(out) >= limit:
            break
    return "\n".join(out)


async def construct(
    *,
    criterion: str,
    failure_modes: list[str],
    what_agent_did: str,
    repo_id: str,
    commands: list[str],
    max_turns: int = 4,
) -> ConstructedSpec:
    """Decide the evaluator and its parameters for one case."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="constructor",
        instructions=INSTRUCTIONS,
        model=MODEL,
        output_type=ConstructedSpec,
    )
    prompt = f"""\
Repository: {repo_id}

The criterion a candidate must satisfy:
{criterion}

What the original agent did:
{what_agent_did}

Failure modes identified: {", ".join(failure_modes) or "(none recorded)"}

Commands this repository actually uses, from the session's own history:
{_history(commands)}
"""
    result = await Runner.run(agent, prompt, max_turns=max_turns)
    return result.final_output


def to_spec(
    built: ConstructedSpec,
    *,
    task_id: str,
    repo_id: str,
    repo_url: str,
    session_id: str,
    cut_turn: int,
    criterion: str,
    failure_modes: list[str],
    justifying_turn: int,
    license_type: str | None,
    is_copyleft: bool,
    oracle_turn: int,
    oracle_response: str,
) -> TaskSpec:
    """Fold a constructor decision into a full specification."""
    args: dict = {}
    ev = Evaluator(built.evaluator)
    if ev is Evaluator.MUST_CHECK:
        args["required_evidence"] = built.required_evidence
    elif ev is Evaluator.MUST_NOT_ASSERT:
        args["claim_id"] = built.claim_id or "claim"
        args["claim_statement"] = built.claim_statement
    elif ev is Evaluator.FILE_STATE:
        args["path"] = built.file_path
        args["must_contain"] = built.must_contain
        args["must_not_contain"] = built.must_not_contain
    return TaskSpec(
        task_id=task_id,
        repo_id=repo_id,
        repo_url=repo_url,
        session_id=session_id,
        cut_turn=cut_turn,
        question=built.question,
        criterion=criterion,
        evaluator=ev,
        evaluator_args=args,
        failure_modes=failure_modes,
        justifying_turn=justifying_turn,
        license_type=license_type,
        is_copyleft=is_copyleft,
        oracle_turn=oracle_turn,
        oracle_response=oracle_response,
    )
