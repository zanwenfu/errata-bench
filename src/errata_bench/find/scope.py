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

The check deliberately does not see the agent's failing answer, since the
question is what the developer asked for, not what the agent went on to get
wrong.

It ran against the request and the defect alone until 09-24 (B-252), and the
request is only the developer's most recent message -- which for a later
pushback is often a bare reply. Of the 41 moments of step 2 it rejected, 26
were rejected for that alone: "yes" to the agent's "shall I deploy to prod?",
whose defect was a false report that the deploy was live, read as asking for
nothing. So it now reads the conversation the agent had, as the leak gate does,
and judges the request in it (`SCOPE_GATE` 2).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..corpus.turns import MESSAGE_CHARS
from ..llm import MODEL, configure_client, resilient, with_field_guide


#: Which version of this gate judged a row, recorded on it: 1 read the request
#: alone, 2 reads it in the conversation.
SCOPE_GATE = 2


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
project.

The request is the developer's most recent message, and it often means nothing \
on its own: "yes", "go ahead", "approved", "3", "push it", a pasted error, a \
notification that a background task finished. Read it in the conversation you \
are given. A "yes" approves what the agent had just proposed; a pasted error \
asks for it to be dealt with; a notification continues the work already under \
way. What was asked is the work the conversation had arrived at when the \
developer wrote that message -- no more, and no less."""


async def in_scope(request: str, defect: str, *, conversation: str = "", model: str = MODEL) -> Scope:
    """Ask whether the defect lies inside the requested work, read in its conversation."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="scope-check",
        instructions=with_field_guide(INSTRUCTIONS, Scope),
        model=model,
        output_type=Scope,
    )
    context = f"The conversation so far, as the agent sees it:\n{conversation}\n\n" if conversation else ""
    prompt = f"""\
{context}What the developer asked for (their most recent message):
{request[:MESSAGE_CHARS]}

The defect that was later found:
{defect[:2000]}
"""
    result = await resilient(lambda: Runner.run(agent, prompt, max_turns=3))
    return result.final_output
