"""Remove the turns that give away that something has gone wrong.

The leakage check rejects a task whose conversation signals that the agent has
been failing, because a candidate reading that only has to take the hint. That
throws away fifteen of twenty-seven otherwise sound tasks, and most of them do
not deserve it: the hint is usually a few identifiable turns inside an otherwise
ordinary conversation.

oddessentials-83 is the clear case. One line does the damage --

    "**Medium:** SC-003 scope is incorrect. **Fix:** Change from 'modified
    files' to 'entire repo typecheck surface' to prevent local pass / CI fail
    divergence."

-- and the task is precisely about whether a candidate invents that rationale.
Everything else in the conversation is the work leading up to it. Removing the
whole task to remove one paragraph is waste.

Leaks come in two shapes, and only one can be repaired. Of six leaking tasks
examined, five leaked in both the user's turns and the agent's: a developer
objects, the agent concedes, and those are events with locations. The sixth
leaked from neither side taken alone -- the tell was that "the agent visibly
attempts cleanup in the wrong order, receives an error, and then corrects the
command". That is the shape of the work rather than anything said, there is no
line to cut, and such a task still has to go.

What this must not do is remove the work. A candidate that loses context the
original agent had is facing a harder task for reasons unrelated to care, which
is the same failure as cutting at the request instead of the failure. So only
turns that carry a hint are dropped, the result is re-checked, and a conversation
that still leaks -- or that has lost too much -- is rejected rather than shipped
half-repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .reader import MODEL, configure_client


class TurnVerdict(BaseModel):
    """Whether one turn gives away that something has gone wrong."""

    turn: int = Field(description="The turn number you are judging.")
    leaks: bool = Field(
        description=(
            "True when this turn signals that the agent has made a mistake or that "
            "the developer is dissatisfied -- an objection, a correction, an "
            "apology, a repeated complaint. False for ordinary work: instructions, "
            "questions, results, technical discussion, redirection."
        )
    )
    quote: str = Field(
        default="",
        description="The words that carry the signal, copied exactly. Empty when the turn is clean.",
    )


class Survey(BaseModel):
    """Which turns in a conversation carry hints."""

    verdicts: list[TurnVerdict] = Field(
        description="One entry for every turn you were shown, in order."
    )
    diffuse: bool = Field(
        description=(
            "True when the conversation signals trouble without any single turn "
            "carrying it -- for instance the agent repeatedly correcting its own "
            "commands. A conversation like that cannot be repaired by removing "
            "turns, and saying so is more useful than naming turns arbitrarily."
        )
    )
    reasoning: str = Field(description="One or two sentences on what you found.")


INSTRUCTIONS = """\
You are reading a conversation between a developer and a coding agent, to find \
the turns that give away that the agent has been getting things wrong.

This conversation is about to be handed to another model, which will continue it \
from the end. Any turn revealing that mistakes have been made lets that model \
take a hint instead of thinking, so those turns must be identified.

A turn leaks when it shows the agent erring or the developer dissatisfied: an \
objection to completed work, a correction, an apology, a repeated complaint, a \
statement that something is wrong.

A turn does not leak merely by being technical, long, or negative about the \
code. A developer redirecting to a different approach, reporting a bug in \
existing software, or asking a hard question is ordinary work. Be strict here: \
marking ordinary turns as leaks removes the work the candidate needs.

Sometimes the signal is in no single turn -- the agent runs a command, gets an \
error, and quietly fixes it, again and again. Nothing is said, but the pattern \
shows an agent struggling. Report that as diffuse rather than picking turns, \
because removing turns will not fix it.

Quote the words that carry each hint, so every judgement can be checked."""


@dataclass
class Redaction:
    """What was removed, and whether the result is usable."""

    removed_turns: list[int] = field(default_factory=list)
    diffuse: bool = False
    reason: str = ""
    quotes: dict[int, str] = field(default_factory=dict)

    @property
    def repairable(self) -> bool:
        """Whether removing these turns could plausibly fix the conversation.

        A diffuse leak cannot be, and neither can one where the hints are so
        numerous that removing them would gut the conversation. The caller still
        has to re-check the result: this says the attempt is worth making, not
        that it worked.
        """
        return not self.diffuse and bool(self.removed_turns)


async def survey(turns: list[dict], cut_turn: int, *, model: str = MODEL) -> Redaction:
    """Find the turns that carry hints, or report that the leak is diffuse."""
    from agents import Agent, Runner

    configure_client()
    shown = [
        t
        for t in turns
        if (t.get("turn_number") or 0) <= cut_turn
        and t.get("turn_type") in ("user_prompt", "assistant_response")
        and (t.get("content") or "").strip()
    ]
    if not shown:
        return Redaction(reason="no conversational turns to survey")

    rendered = "\n\n".join(
        f"[turn {t.get('turn_number')}] "
        f"{'USER' if t.get('turn_type') == 'user_prompt' else 'AGENT'}:\n"
        f"{(t.get('content') or '')[:2500]}"
        for t in shown[-40:]
    )
    agent = Agent(
        name="hint-surveyor", instructions=INSTRUCTIONS, model=model, output_type=Survey
    )
    result = await Runner.run(agent, f"The conversation:\n\n{rendered}", max_turns=3)
    s: Survey = result.final_output
    leaking = [v for v in s.verdicts if v.leaks]
    return Redaction(
        removed_turns=[v.turn for v in leaking],
        diffuse=s.diffuse,
        reason=s.reasoning,
        quotes={v.turn: v.quote for v in leaking if v.quote},
    )


def apply(turns: list[dict], removed: list[int]) -> list[dict]:
    """Drop the named turns, keeping everything else in order.

    The turns are removed rather than blanked. A placeholder saying something
    was taken out is itself a signal -- a candidate seeing "[redacted]" knows
    exactly what kind of thing it is not being shown.
    """
    drop = set(removed)
    return [t for t in turns if (t.get("turn_number") or 0) not in drop]
