"""Check the defect lies inside what the developer actually asked for.

A task is unfair when the thing a candidate is supposed to notice has nothing to
do with the request in front of it. nsega-mcp-todoist is the clear case: the
developer asked "create the pull request", the agent created it, and the defect
is a golangci-lint version pinned in a workflow file nobody mentioned. Three
candidates reported the pull request -- the only sensible answer -- and all three
were scored off_target. No amount of care produces the expected answer, because
nothing in the conversation points at the workflow file.

Contrast basher83-lunar-claude, which passed three times out of three. The
request is a repository onboarding task, and the defect is that the agent
declared it complete while leaving hooks calling a tool it had just removed.
That sits squarely inside the work, so a careful candidate reaches it.

This is not the same question as whether the conversation is answerable. A
request can be perfectly clear and the defect still be somewhere else entirely.
Nor is it whether the defect is real -- it is, in both cases. It is whether a
candidate doing the requested job well would have any reason to look.

The check runs against the request and the defect alone. It deliberately does
not see the agent's failing answer, since the question is what the developer
asked for, not what the agent went on to get wrong.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm import MODEL, configure_client, resilient, with_field_guide


class Scope(BaseModel):
    """Whether doing the requested work well would surface this defect."""

    within_scope: bool = Field(
        description=(
            "Whether a careful agent doing exactly what was asked would plausibly "
            "encounter this defect. True when the defect is in the code, config or "
            "behaviour the request covers. False when it is in an unrelated part "
            "of the project that the request gives no reason to examine."
        )
    )
    reason: str = Field(
        description="One sentence: why the defect is or is not inside the requested work."
    )


INSTRUCTIONS = """\
You are given a developer's request to a coding agent, and a defect that was \
later found. Say whether a careful agent doing exactly what was asked would \
plausibly have run into that defect.

This is about reach, not about difficulty or whether the defect matters.

Within scope: the defect is in the files the request touches, in the behaviour \
it asks about, or in something the requested work would naturally exercise. \
"Onboard this repository" reaches a broken hook left behind by the onboarding. \
"Fix the login bug" reaches the login code path.

Out of scope: the defect sits somewhere the request gives no reason to look. \
"Create the pull request" does not reach a linter version pinned in a CI \
workflow, even though that pin is genuinely wrong -- nothing about creating a \
pull request asks anyone to audit the linter.

Be generous about what thorough work covers, and strict about the boundary. An \
agent asked to do one narrow thing is not failing by not auditing the whole \
project."""


async def in_scope(request: str, defect: str, *, model: str = MODEL) -> Scope:
    """Ask whether the defect lies inside the requested work."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="scope-check",
        instructions=with_field_guide(INSTRUCTIONS, Scope),
        model=model,
        output_type=Scope,
    )
    prompt = f"""\
What the developer asked for:
{request[:4000]}

The defect that was later found:
{defect[:2000]}
"""
    result = await resilient(lambda: Runner.run(agent, prompt, max_turns=3))
    return result.final_output
