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

Until 09-24 (B-253) it read the message alone, and a bare reply read as asking
for nothing: "yes" to the agent's "Want me to promote to production now?", "it
is ok", a notification that a background task the agent was waiting on had
finished. Those do ask for something -- what the agent had just proposed, or the
work it was in the middle of -- and a candidate reading the conversation knows
what. So the gate now also sees the agent's last message before the developer's
(`ANSWERABLE_GATE` 2). A pasted log with no question is still not a request:
moltis's candidate had the whole conversation and summarised the log.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..corpus.turns import MESSAGE_CHARS
from ..llm import MODEL, configure_client, resilient, with_field_guide


#: Which version of this gate judged a row, recorded on it: 1 read the message
#: alone, 2 reads it after the agent's last message.
ANSWERABLE_GATE = 2


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

Judge the message as written, not what the agent might reasonably infer.

You may also be shown the agent's message just before it. A short reply is a \
request when it answers a question the agent asked or approves what the agent \
proposed: "yes" after "Shall I deploy to production?" asks the agent to deploy; \
"3" after a numbered list of options asks for option 3; a notification that a \
background task finished, when the agent said it was waiting for that task, asks \
it to carry on. That is reading the reply, not inferring a request. Pasted \
output is still not a request unless the agent had just asked for it, and a \
reply that answers nothing the agent asked is judged on its own."""


async def asks_for_something(message: str, *, before: str = "", model: str = MODEL) -> Answerable:
    """Ask whether one developer message contains a request, read after the agent's last message."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="request-reader",
        instructions=with_field_guide(INSTRUCTIONS, Answerable),
        model=model,
        output_type=Answerable,
    )
    shown = (f"The agent's message just before it:\n\n{before[:MESSAGE_CHARS]}\n\n" if before else "")
    shown += f"The developer's message:\n\n{message[:MESSAGE_CHARS]}"
    result = await resilient(lambda: Runner.run(agent, shown, max_turns=3))
    return result.final_output


def agent_message_before(turns: list[dict], message: dict) -> str:
    """The agent's last written message before the developer's ``message``, or "" if none."""
    at = message.get("turn_number")
    earlier = [t for t in turns if t.get("turn_type") == "assistant_response" and t.get("turn_number") is not None
               and at is not None and t["turn_number"] < at]
    return str(max(earlier, key=lambda t: t["turn_number"]).get("content") or "") if earlier else ""
