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

from ..llm import MODEL, configure_client, resilient, with_field_guide

# How much of the end of the conversation triage reads, at the least.
TAIL_CHARS = 9000
LEFT_OUT = "[... the conversation before this is not shown ...]"


def view(turns: list[dict], turn_number) -> str:
    """What triage reads: the end of the conversation up to the developer's message (G-92).

    Each row is rendered on its own, as `build_excerpt` renders it with every
    message whole (record 2: a call shows its input, each result keeps up to
    4,000 characters, and a cut says how much went), and the rows are chosen
    as rows, never by reading the text back: a message that quotes "[turn 2]
    AGENT:" is not a turn. From the end, whole rows up to `TAIL_CHARS`, the
    developer's message whole however long, and the agent's last answer before
    it, the work it may object to; the rows between those two are left out
    and counted, so a long run of calls after the answer does not make the
    view long. A line says where the start was left out.

    It read the last 9,000 characters as they fell, each message cut at 4,000:
    the view began inside a turn in 643 of the first 681 Entire moments, and
    the agent's last answer was cut, unmarked, in 77. Each row rendered on
    its own is never squeezed to fit the rest: fitted as a whole, the history
    triage never reads cut the calls and results it does, to 40 characters in
    the longest sessions.
    """
    from ..corpus.turns import build_excerpt

    rows = []
    for t in turns:
        n = t.get("turn_number")
        if n is None or n > turn_number:
            continue
        shown = build_excerpt([t], turn_number, record=2, whole_messages=True)
        if shown:
            rows.append((t, shown))
    if not rows:
        return ""
    start, size = len(rows) - 1, len(rows[-1][1])
    while start > 0 and size + 1 + len(rows[start - 1][1]) <= TAIL_CHARS:
        start -= 1
        size += 1 + len(rows[start][1])
    kept = [shown for _, shown in rows[start:]]
    first = start
    answer = next((i for i in range(len(rows) - 1, -1, -1) if rows[i][0].get("turn_type") == "assistant_response"),
                  None)
    if answer is not None and answer < start:
        between = start - answer - 1
        kept = [rows[answer][1]] + ([f"[... {between} turns between not shown ...]"] if between else []) + kept
        first = answer
    text = "\n".join(kept)
    return text if first == 0 else f"{LEFT_OUT}\n{text.lstrip(chr(10))}"


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

Some messages only sound like objections. A question about the design ("do we \
really need this layer?"), a clarification of what the developer wants next, or \
a reply that accepts the work and then extends it ("fine, keep that; now do the \
same for the exporter") does not say the agent got anything wrong, even with an \
evaluative word in it. But correcting a fact the agent stated or assumed is an \
objection, however mildly put. And the work objected to must be the agent's: \
questioning code that was in the repository before the agent touched it is not \
objecting to the agent, unless it disputes what the agent said or did about it.

Both must be true for this moment to be worth examining further. Be strict: \
saying no is cheap, and saying yes commits several expensive reads."""


async def triage(excerpt: str, *, model: str = MODEL) -> Triage:
    """One call deciding whether a moment deserves the full reader, on the end of the conversation (`view`)."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="triage",
        instructions=with_field_guide(INSTRUCTIONS, Triage),
        model=model,
        output_type=Triage,
    )
    # Only the end matters: whether the agent has acted, and what the developer
    # said about it (`view`). The reader gets the whole conversation afterwards,
    # if this says it is worth having.
    result = await resilient(lambda: Runner.run(agent, f"The conversation:\n\n{excerpt}", max_turns=3))
    return result.final_output
