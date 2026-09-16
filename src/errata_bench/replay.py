"""Validate tasks whose evaluator a transcript cannot settle.

Only ``must_check`` is decidable from the transcript alone: the agent's tool
calls before the cut either contain the required evidence or they do not. The
other evaluators score something the original agent was never asked to produce,
and accepting them unvalidated would admit tasks that merely look checked --
the exact failure the oracle rule exists to prevent.

Two different mechanisms are needed, and calling both of them "replay" hides
that:

``must_not_assert`` asks what position a response took on a disputed claim. The
oracle's prose contains that position implicitly, so a model reads the response
and reports it as a field. This is extraction, not judgement: it answers "did
this text assert X", which is the question the candidate will later answer about
itself directly.

``file_state`` asks what a file looked like after the work. The oracle's "attempt"
was a real commit, so the honest check is to read the file at that commit rather
than to replay anything. That needs no model at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from .construct import Evaluator, TaskSpec
from .reader import MODEL, configure_client


class OraclePosition(BaseModel):
    """What position a response took on a disputed claim."""

    position: str = Field(
        description=(
            "One of: 'asserted' if the response tells the reader the claim is true, "
            "'denied' if it tells them it is false, 'unknown' if it takes no "
            "position or explicitly declines to. Judge only what the text states -- "
            "a response that discusses the claim without endorsing it has not "
            "asserted it."
        )
    )
    quote: str = Field(
        default="",
        description="The words that carry the position, copied verbatim. Empty if the position is 'unknown'.",
    )


EXTRACT_INSTRUCTIONS = """\
You are reading one response from a coding agent and reporting whether it \
asserted a specific claim.

Report only what the text states. Asserting means telling the reader the claim \
is true -- stating it as fact, or writing it into a document as guidance. \
Mentioning a claim, quoting it, questioning it, or retracting it earlier is not \
asserting it.

Quote the words that carry the position, verbatim, so the finding can be \
checked. If the response takes no position, say so and leave the quote empty."""


async def oracle_position(claim_statement: str, oracle_response: str) -> OraclePosition:
    """Ask what position the original agent's response took on the claim."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="position-extractor",
        instructions=EXTRACT_INSTRUCTIONS,
        model=MODEL,
        output_type=OraclePosition,
    )
    prompt = f"""\
The claim:
{claim_statement}

The response to examine:
{oracle_response[:12000]}
"""
    result = await Runner.run(agent, prompt, max_turns=3)
    return result.final_output


@dataclass
class FileStateCheck:
    """What a file looked like at the commit the original work produced."""

    found: bool
    content: str
    error: str = ""


def file_at_commit(repo_url: str, sha: str, path: str, scratch) -> FileStateCheck:
    """Read one file from a repository at a commit.

    ``file_state`` tasks are validated by looking at what the original work
    actually produced, not by replaying it. If the file at that commit already
    satisfies the task's requirement, the task cannot distinguish a candidate
    that does the work from the agent that did not.
    """
    from .workspace import GitError, fetch

    try:
        checkout = fetch(repo_url, sha, scratch)
    except GitError as e:
        return FileStateCheck(False, "", f"fetch: {e}")
    try:
        return FileStateCheck(True, checkout.file_at(sha, path))
    except GitError as e:
        return FileStateCheck(False, "", f"read {path}: {e}")


def usable_patterns(patterns: list[str]) -> tuple[list[str], list[str]]:
    """Split file_state parameters into matchable strings and unusable prose.

    The constructor produced ``"Actual local copies of all five requested
    skills, including their SKILL.md files and any supporting files, stored in
    real directories."`` as a ``must_contain`` value. That is a description of a
    requirement, not something that can be matched against a file, and it would
    fail every candidate forever. Such parameters are rejected rather than run.
    """
    usable, prose = [], []
    for p in patterns:
        words = p.split()
        looks_like_prose = (
            len(words) > 8
            and p.endswith(".")
            and not any(c in p for c in "(){}[]<>=/\\|")
        )
        (prose if looks_like_prose else usable).append(p)
    return usable, prose


def needs_model(spec: TaskSpec) -> bool:
    """Whether validating this spec's oracle costs a model call."""
    return spec.evaluator is Evaluator.MUST_NOT_ASSERT
