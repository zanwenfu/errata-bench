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

# How much of each turn the surveyor is shown. A rewrite covers only this much.
SURVEY_CHARS = 2500


class TurnVerdict(BaseModel):
    """What to do with one turn: keep it, rewrite it, or drop it."""

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
    rewrite: str = Field(
        default="",
        description=(
            "When the turn carries a hint but also carries substance the candidate "
            "needs -- an error message, a requirement, a log, a question -- give the "
            "turn rewritten with the hint removed and everything else intact. Keep "
            "the developer's voice and keep every technical detail verbatim; change "
            "only the words that reveal the agent has been failing. Leave this empty "
            "when the turn is nothing but the hint, in which case it is dropped."
        ),
    )

    @property
    def action(self) -> str:
        if not self.leaks:
            return "keep"
        return "rewrite" if self.rewrite.strip() else "drop"


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
You are reading a conversation between a developer and a coding agent, to remove \
the parts that give away that the agent has been getting things wrong.

This conversation is about to be handed to another model, which will continue it \
from the end. Any turn revealing that mistakes have been made lets that model \
take a hint instead of thinking. But that model also has to do the work, and it \
can only do so with the context the original agent had.

So for each turn, one of three things:

  KEEP     The turn is ordinary work. Most turns are.
  DROP     The turn carries a hint. This is the normal treatment.
  REWRITE  Rare. Only when dropping would take substantial content the \
candidate cannot work without.

Drop by default. A turn that objects, corrects, complains, or concedes exists \
*to do that*, and removing the objection from it leaves nothing worth sending. \
Softening does not help either: a candidate reads a toned-down complaint as a \
complaint, so a rewritten objection still gives the game away while pretending \
not to. "That's still broken." "You said that already." "No, I meant the other \
file." All of these are dropped.

Rewrite only when the hint is incidental to a turn that is mostly something \
else -- and that is uncommon. The test is what would be lost. If dropping the \
turn would take away an error message, a stack trace, a requirement, a \
specification or a question that appears nowhere else, rewrite it. If dropping \
it would take away nothing but the complaint, drop it.

The clear case for rewriting looked like this: "Ok next problem, we seem to be \
going in circles - the app startup fails because no tables were found, we need \
to apply the sql files as we create the database", followed by two pages of \
stack traces ending in `no such table: LogContent`. Five words of hint on three \
and a half thousand characters of error output and requirement. Dropping it left \
the candidate with no problem to solve.

If you are unsure, drop. An over-rewritten conversation still leaks, which \
wastes the task entirely; an over-dropped one merely loses a turn.

When you rewrite, keep the developer's voice and keep every technical detail \
exactly as written -- error text, file paths, commands, requirements. Change only \
the words that reveal the agent has been failing. Do not summarise, do not \
tidy, and do not add anything.

A turn does not leak merely by being technical, long, or negative about the \
code. A developer reporting a bug, redirecting to another approach, or asking a \
hard question is ordinary work. Be strict: marking ordinary turns as leaks \
removes the work the candidate needs.

Sometimes the signal is in no single turn -- the agent runs a command, gets an \
error, and quietly fixes it, again and again. Nothing is said, but the pattern \
shows an agent struggling. Report that as diffuse rather than picking turns, \
because editing turns will not fix it.

Quote the words that carry each hint, so every judgement can be checked."""


@dataclass
class Redaction:
    """What was edited or removed, and whether the result is usable."""

    removed_turns: list[int] = field(default_factory=list)
    rewritten: dict[int, str] = field(default_factory=dict)
    diffuse: bool = False
    reason: str = ""
    quotes: dict[int, str] = field(default_factory=dict)

    @property
    def touched(self) -> list[int]:
        return sorted(set(self.removed_turns) | set(self.rewritten))

    @property
    def repairable(self) -> bool:
        """Whether editing these turns could plausibly fix the conversation.

        A diffuse leak cannot be. The caller still has to re-check the result:
        this says the attempt is worth making, not that it worked.
        """
        return not self.diffuse and bool(self.touched)


async def survey(turns: list[dict], cut_turn: int, *, model: str = MODEL) -> Redaction:
    """Decide, per turn, whether to keep it, rewrite it, or drop it."""
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
        f"{(t.get('content') or '')[:SURVEY_CHARS]}"
        for t in shown[-40:]
    )
    agent = Agent(
        name="hint-surveyor", instructions=INSTRUCTIONS, model=model, output_type=Survey
    )
    result = await Runner.run(agent, f"The conversation:\n\n{rendered}", max_turns=3)
    s: Survey = result.final_output
    leaking = [v for v in s.verdicts if v.leaks]
    return Redaction(
        removed_turns=[v.turn for v in leaking if v.action == "drop"],
        rewritten={v.turn: v.rewrite for v in leaking if v.action == "rewrite"},
        diffuse=s.diffuse,
        reason=s.reasoning,
        quotes={v.turn: v.quote for v in leaking if v.quote},
    )


def apply(
    turns: list[dict], removed: list[int], rewritten: dict[int, str] | None = None
) -> list[dict]:
    """Drop the named turns and substitute the rewritten ones.

    A turn is dropped only when it is nothing but the hint. Anything carrying
    substance is rewritten instead, because dropping it takes the work with it:
    nosman-gossamer lost a 3,735-character message whose hint was the five words
    "we seem to be going in circles" and whose remainder was the error output,
    the failing table name and the requirement. All three attempts at that task
    then exhausted their turn limit with nothing to work on.

    Turns are removed rather than blanked. A placeholder saying something was
    taken out is itself a signal -- a candidate seeing "[redacted]" knows exactly
    what kind of thing it is not being shown.
    """
    drop = set(removed)
    edits = rewritten or {}
    out = []
    for t in turns:
        n = t.get("turn_number") or 0
        if n in drop:
            continue
        if n in edits:
            # The surveyor sees each turn truncated, so a rewrite of a long turn
            # covers only what it was shown. Re-attaching the untouched tail
            # keeps the error output and logs that usually sit at the end --
            # losing them is the exact failure rewriting exists to prevent.
            original = t.get("content") or ""
            replacement = edits[n]
            if len(original) > SURVEY_CHARS:
                replacement = replacement + original[SURVEY_CHARS:]
            t = {**t, "content": replacement}
        out.append(t)
    return out
