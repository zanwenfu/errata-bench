"""Decide whether a task gives the candidate something it can answer.

A candidate replies to the developer's most recent message. When that message
carries no request, the candidate has nothing to do and does the sensible thing
with what it was handed -- usually summarising it. Six of nine attempts on
present-defect tasks were scored as going off-target for exactly that, and the
tasks were at fault rather than the model.

moltis is the clear case. Its last user turn is 1,837 characters of pasted
validation output: build directories being removed, `bash: biome: command not
found`, three checks passing and one failing. No question anywhere. The original
agent inferred "tell me whether this is fine" from months of shared context; the
candidate saw a log and summarised the log, three runs out of three.

Three regexes were tried first and each was wrong in a new way. The last one
stripped lines that looked like terminal output and searched what remained, at
which point "One or more parallel local **check**s failed" and "configuration
files **are** not trusted" registered as a request. That is the same trap this
project keeps falling into: prose does not yield to pattern matching, and four
scoring bugs in a hand-built task came from believing otherwise.

So it is a model call, kept deliberately small -- one question, one short answer,
no repository access.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm import MODEL, configure_client, resilient, with_field_guide


class Answerable(BaseModel):
    """Whether a developer's message asks the agent for anything."""

    asks_for_something: bool = Field(
        description=(
            "Whether this message gives the agent something to do or answer -- a "
            "question, an instruction, a report of a problem it should act on. "
            "False when it is only pasted output, a log, a status dump, or an "
            "acknowledgement with no request attached."
        )
    )
    request: str = Field(
        default="",
        description=(
            "The request in your own words, one sentence. Empty when there is none."
        ),
    )
    reasoning: str = Field(
        description="One sentence: why this does or does not ask for something."
    )


INSTRUCTIONS = """\
You are shown one message a developer sent to a coding agent. Say whether it \
asks the agent for anything.

Developers often paste terminal output -- build logs, test results, stack \
traces -- and that alone is not a request, however much text it is. A log \
saying a check failed is information, not an instruction. Words like "check", \
"failed" or "are" appearing inside log lines do not make a message a question.

It is a request when the developer asks something, tells the agent to do \
something, or reports a problem they plainly want acted on. A pasted error \
followed by "what's going on?" is a request. The same error pasted alone is not.

Judge the message as written, not what the agent might reasonably infer."""


async def asks_for_something(message: str, *, model: str = MODEL) -> Answerable:
    """Ask whether one developer message contains a request."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="request-reader",
        instructions=with_field_guide(INSTRUCTIONS, Answerable),
        model=model,
        output_type=Answerable,
    )
    result = await resilient(lambda: Runner.run(agent, f"The developer's message:\n\n{message[:8000]}", max_turns=3))
    return result.final_output
