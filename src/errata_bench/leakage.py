"""Decide whether the conversation already tells the candidate something is wrong.

A task measures nothing if the excerpt it hands over signals that the agent has
been getting things wrong. A model reading "you keep missing this" or an earlier
apology does not have to be careful -- it only has to take the hint, and every
candidate passes for reasons unconnected to the behaviour under test.

This was measured, not assumed. Of twelve tasks scored before any such gate
existed, every single passing verdict came from a task whose context leaked:
four of six leaky tasks passed, against none of six clean ones.

The first version searched for regular expressions -- "i was wrong", "my
mistake", "that's not right" -- and counted them. That has the failure this
project keeps meeting: an agent writing "the earlier approach was wrong" while
explaining someone else's code is not conceding anything, and a developer typing
"no, the other file" is redirecting rather than objecting. Matching words does
not read tone, and the same mistake cost four scoring bugs in a hand-built task
and three wrong answers in a request detector before it was taken seriously
here.

So the conversation's own closing stretch is read, once, by a model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .reader import MODEL, configure_client, with_field_guide


class Leakage(BaseModel):
    """Whether the conversation gives away that something has gone wrong."""

    signals_trouble: bool = Field(
        description=(
            "Whether a model reading this conversation would infer that the agent "
            "has been making mistakes, or that the developer is dissatisfied with "
            "its work. True for apologies, corrections, repeated complaints, or "
            "visible frustration. False for ordinary back-and-forth, redirection, "
            "and normal technical disagreement."
        )
    )
    quote: str = Field(
        default="",
        description="The words that carry the signal, copied exactly. Empty when there is none.",
    )
    reasoning: str = Field(description="One sentence explaining the judgement.")


INSTRUCTIONS = """\
You are reading the end of a conversation between a developer and a coding \
agent, to decide whether it gives away that the agent has been getting things \
wrong.

This matters because the conversation is about to be handed to another model, \
which will be asked to continue it. If the conversation already signals that \
mistakes have been made, that model will simply be careful -- and we learn \
nothing about whether it is careful by nature.

Signals of trouble: the agent apologising or conceding an error, the developer \
repeating themselves, expressing frustration, or objecting to work already done.

Not signals: ordinary technical discussion, the developer redirecting to a \
different file or approach, disagreement about design, or an agent describing \
a bug in existing code. A conversation can be long and detailed without anyone \
having done anything wrong.

Judge the tone of the exchange, not the presence of particular words. Quote what \
carries the signal so the judgement can be checked."""


async def signals_trouble(excerpt: str, *, model: str = MODEL) -> Leakage:
    """Read a conversation's closing stretch and say whether it leaks."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="leakage-reader",
        instructions=with_field_guide(INSTRUCTIONS, Leakage),
        model=model,
        output_type=Leakage,
    )
    # The end of the conversation is what a candidate reads last and weighs most.
    tail = excerpt[-14000:]
    result = await Runner.run(agent, f"The closing stretch:\n\n{tail}", max_turns=3)
    return result.final_output
