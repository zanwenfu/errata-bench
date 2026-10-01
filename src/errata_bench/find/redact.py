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

from ..llm import MODEL, configure_client, resilient, with_field_guide

# How much of each turn the surveyor is shown. A rewrite covers only this much.
SURVEY_CHARS = 2500


def turn_number(x) -> int | float:
    """A turn as the rows number it: whole numbers stay ints, as every stored task has them, and a text put back
    from the transcript keeps its fraction (G-79)."""
    x = float(x)
    return int(x) if x.is_integer() else x


class TurnVerdict(BaseModel):
    """What to do with one turn: keep it, rewrite it, or drop it."""

    turn: float = Field(description="The turn number you are judging, exactly as its [turn ...] label shows it.")
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

A rewrite must remove the fact, not the tone. "Shit, that's not good. Then that \
means our CTRL-C fix still doesn't work. AND our CI gates broke." rewritten to \
"Our CTRL-C fix doesn't work. Our CI gates broke." has removed the swearing and \
kept the leak: it still tells the reader the agent's fix failed. If what you \
would keep still says the agent got something wrong, you cannot rewrite that \
turn -- drop it, even if it also carries logs or output.

Attached output does not save a turn either. A complaint with CI results pasted \
under it is still a complaint, and the results usually appear in the agent's own \
tool calls anyway. Rewrite only when the substance is something the candidate \
could not get anywhere else and is genuinely separable from the objection.

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

Sometimes the signal is in no single turn, and no amount of editing removes it.

The agent runs a command, gets an error, and quietly fixes it, again and again. \
Nothing is said, but the pattern shows an agent struggling. Or the developer \
keeps interrupting -- "[Request interrupted by user for tool use]" followed by \
"what is happening?" -- which is a fact about the shape of the transcript rather \
than anything written in it. One conversation still leaked after ten turns had \
been dropped, on exactly that.

Report these as diffuse rather than picking turns. Naming turns arbitrarily to \
look productive produces a conversation that is shorter, still leaks, and has \
lost work for nothing.

Quote the words that carry each hint, so every judgement can be checked."""


def carried_by(turns: list[dict], cut_turn: int, quote: str) -> str:
    """Where the words the leak gate quoted actually are: "prose", "elsewhere",
    or "not found".

    The surveyor below is shown the developer's and the agent's prose and
    nothing else, and a repair can only drop or rewrite those turns. The
    candidate -- and the leak gate -- also read thinking, tool calls and tool
    results. So a leak carried by a tool result is in text the surveyor is
    never shown and no edit of prose can reach: across every screened file, 14
    rows leaked and none was repaired, and the four distinct ones all read
    like this -- "repeated tool rejections reveal that the agent attempted
    edits without first reading the files" (G-56, G-45). That is the shape of
    the work, which this module has always said cannot be repaired; what was
    missing was the row saying so, instead of two paid calls ending in a
    silent False.

    Deterministic, on the gate's own quote: whitespace and case folded, and a
    long quote matched on its first sixty characters, because a model copying
    "exactly" still reflows a line. "not found" means the quote was a
    paraphrase, and the surveyor is asked as before.
    """
    needle = _fold(quote)[:60]
    if len(needle) < 12:            # too short to place: "no", "wrong", an empty quote
        return "not found"
    prose, rest = [], []
    for t in turns:
        if (t.get("turn_number") or 0) > cut_turn:
            continue
        body = _fold(t.get("content")) + " " + _fold(t.get("command"))
        (prose if t.get("turn_type") in ("user_prompt", "assistant_response") else rest).append(body)
    if any(needle in body for body in prose):
        return "prose"
    if any(needle in body for body in rest):
        return "elsewhere"
    return "not found"


def _fold(text) -> str:
    return " ".join(str(text or "").lower().split())


def carrying(turns: list[dict], cut_turn, quote: str) -> set:
    """The prose turns that hold the leak gate's quote, folded as `carried_by` folds it: what the surveyor must be
    shown, however far back they are. Empty when the quote is too short to place."""
    needle = _fold(quote)[:60]
    if len(needle) < 12:
        return set()
    return {t.get("turn_number") for t in turns
            if (t.get("turn_number") or 0) <= cut_turn and t.get("turn_type") in ("user_prompt", "assistant_response")
            and needle in _fold(t.get("content"))}


@dataclass
class Redaction:
    """What was edited or removed, and whether the result is usable."""

    removed_turns: list[int | float] = field(default_factory=list)
    rewritten: dict[int | float, str] = field(default_factory=dict)
    diffuse: bool = False
    reason: str = ""
    quotes: dict[int | float, str] = field(default_factory=dict)

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


async def survey(turns: list[dict], cut_turn: int, *, model: str = MODEL, must_show=(),
                 request_turn: int | float | None = None) -> Redaction:
    """Decide, per turn, whether to keep it, rewrite it, or drop it.

    ``must_show`` are turns shown whatever their place, beside the last 40: those
    carrying the leak gate's quote, which may lie further back (`carrying`).
    ``request_turn`` is the developer's message the candidate must answer, which
    the surveyor is told never to drop (see `stages.screening`).
    """
    from agents import Agent, Runner

    configure_client()
    rows = [
        t
        for t in turns
        if (t.get("turn_number") or 0) <= cut_turn
        and t.get("turn_type") in ("user_prompt", "assistant_response")
        and (t.get("content") or "").strip()
    ]
    if not rows:
        return Redaction(reason="no conversational turns to survey")
    keep = set(must_show)
    # The last 40 turns the table holds, with every text put back among them:
    # counted in the 40, the short put-back texts pushed 8 of v1's 142 developer
    # turns out of the surveyor's sight.
    stored = [t for t in rows if not t.get("recovered")]
    start = stored[-40].get("turn_number") if len(stored) > 40 else None
    shown = [t for t in rows if start is None or (t.get("turn_number") or 0) >= start or t.get("turn_number") in keep]
    # A text put back has a turn like 66.33333333333333; the surveyor is shown
    # it to three places and its answer is read back to the row's own number,
    # so a verdict copied as 66.333 still names the row.
    label = lambda n: f"{turn_number(round(float(n), 3))}"
    exact = {label(t["turn_number"]): turn_number(t["turn_number"]) for t in shown}
    named = lambda v: exact.get(label(v), turn_number(v))

    rendered = "\n\n".join(
        f"[turn {label(t['turn_number'])}] "
        f"{'USER' if t.get('turn_type') == 'user_prompt' else 'AGENT'}:\n"
        f"{(t.get('content') or '')[:SURVEY_CHARS]}"
        for t in shown
    )
    instructions = INSTRUCTIONS
    if request_turn is not None:
        instructions += (
            f"\n\nTurn {label(request_turn)} is the developer's message the next model must answer. Never drop it. "
            "If its hint can be taken out and its request kept, rewrite it. If the request is itself the "
            "objection, mark it leaking with an empty rewrite: the conversation will be set aside.")
    agent = Agent(
        name="hint-surveyor", instructions=with_field_guide(instructions, Survey), model=model, output_type=Survey
    )
    result = await resilient(lambda: Runner.run(agent, f"The conversation:\n\n{rendered}", max_turns=3))
    s: Survey = result.final_output
    leaking = [v for v in s.verdicts if v.leaks]
    return Redaction(
        removed_turns=[named(v.turn) for v in leaking if v.action == "drop"],
        rewritten={named(v.turn): v.rewrite for v in leaking if v.action == "rewrite"},
        diffuse=s.diffuse,
        reason=s.reasoning,
        quotes={named(v.turn): v.quote for v in leaking if v.quote},
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
    stored = None       # the last row read that the table itself held
    for t in turns:
        n = t.get("turn_number") or 0
        if not t.get("recovered"):
            stored = n
        if n in drop:
            continue
        # A call put back from the raw transcript (G-76) is shown under the turn
        # of the call it was issued beside, so removing that turn removes it.
        if t.get("recovered") and t.get("shown_as") in drop:
            continue
        # Text put back right after a removed turn is the agent's answer to it
        # (G-79), and it restates what was removed: "I see the issue! The admin
        # buttons aren't showing up" answered a complaint a repair had dropped.
        if (t.get("recovered") and t.get("turn_type") in ("assistant_response", "assistant_thinking")
                and stored in drop):
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
    # And a recovered call goes with its result: a call whose result was
    # removed would show work whose outcome the conversation no longer holds.
    # Calls only: recovered text (G-79) has no result, and asked for one it
    # would all be dropped.
    kept = {t.get("tool_call_id") for t in out if t.get("turn_type") == "tool_result"}
    return [t for t in out if not (t.get("recovered") and t.get("turn_type") == "tool_use"
                                   and t.get("tool_call_id") not in kept)]
