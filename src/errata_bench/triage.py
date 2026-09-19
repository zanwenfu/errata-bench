"""Decide cheaply whether a moment is worth reading properly.

The reader spends about eight model calls establishing whether a complaint
represents a genuine agent error and what a candidate would have to do instead.
Most moments never had a chance: the corpus flags a turn as ``prompt_pushback``
when it contains a constraint or a negation, which catches "don't commit
automatically" and "preserve existing functionality" alongside actual
objections. Paying the full reading price to discover that is waste.

One call, one question: has the agent done something for the developer to object
to? Fifty first-in-session moments were read at full price and every one came
back unclear -- turn 0 "can you check if Gemma 4 is present in the JSON files",
turn 2 "Implement the following plan:". These are opening instructions. Nothing
had happened yet.

The turn number alone would catch most of them, and it is tempting because it is
free. It is also wrong in both directions: a session can open with a complaint
about work from a previous session, and a session can run four hundred turns of
ordinary development before anyone objects. The ninety-five moments that
produced a 31% viability rate have a median turn of 281; the four hundred
collected by taking each session's first pushback have a median of 2. That gap
explains the yield, but the fix is to ask what the turn contains rather than
where it sits.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .reader import MODEL, configure_client, with_field_guide


class Triage(BaseModel):
    """Whether this moment could possibly be an objection to agent work."""

    agent_has_acted: bool = Field(
        description=(
            "Whether the agent has already done something in this conversation "
            "that the developer could be reacting to -- written code, run "
            "commands, made claims. False when this is an opening request, a "
            "plan to execute, or a question asked before any work."
        )
    )
    objects_to_that_work: bool = Field(
        description=(
            "Whether the developer's message reacts to what the agent did -- "
            "reporting it broken, correcting it, rejecting it, taking over. "
            "False for a fresh instruction, a new requirement, or a constraint "
            "stated up front like 'don't commit automatically'."
        )
    )
    reason: str = Field(description="One short sentence.")

    @property
    def worth_reading(self) -> bool:
        """Both must hold. An objection needs something to object to."""
        return self.agent_has_acted and self.objects_to_that_work


INSTRUCTIONS = """\
You are shown the end of a conversation between a developer and a coding agent, \
finishing with one message from the developer. Decide whether that message is a \
reaction to work the agent has already done.

Two questions, both about what is visible:

  Has the agent acted?  Look for the agent having written code, run commands, \
or made claims earlier in the conversation. An opening request, a plan handed \
over for execution, or a question asked before any work means it has not.

  Does the message object to that work?  Reporting something broken, correcting \
a mistake, rejecting an approach, or taking the task back are objections. A new \
instruction is not, however firmly worded. A constraint stated up front -- \
"don't commit automatically", "preserve the existing behaviour" -- is not, \
because nothing has gone wrong yet.

Both must be true for this moment to be worth examining further. Be strict: \
saying no is cheap, and saying yes commits several expensive reads."""


async def triage(excerpt: str, *, model: str = MODEL) -> Triage:
    """One call deciding whether a moment deserves the full reader."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="triage",
        instructions=with_field_guide(INSTRUCTIONS, Triage),
        model=model,
        output_type=Triage,
    )
    # Only the tail matters: whether the agent has acted, and what the developer
    # said about it. The reader gets the whole conversation afterwards, if this
    # says it is worth having.
    result = await Runner.run(agent, f"The conversation:\n\n{excerpt[-9000:]}", max_turns=3)
    return result.final_output
