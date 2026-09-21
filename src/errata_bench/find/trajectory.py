"""Locate the four points that define a task, by reading the trajectory.

A task is built from a moment where an agent got something wrong. Four turns
matter, and only the first is obvious:

    request     the user's ask
    failed      the agent's wrong answer          <- the candidate is cut before this
    complaint   the user pointing out the error   <- evidence it was wrong
    resolved    where the agent finally got it right  <- the success criterion

Cutting at the complaint was the original mistake. A candidate shown the failed
answer *and* a user saying it was wrong is being asked to recover from a
mistake it has been handed, which any competent model does -- every reply opened
"You're right, my earlier fix was insufficient".

Cutting at the request overcorrects. The request can sit far upstream of the
failure: in blittle/pressy it is turn 5 and the failure is turn 54, so a cut
there yields 515 characters of bare ask, discarding the exploration, the file
read at turn 47, and the edit at turn 50 that the failing answer goes on to
endorse. A candidate given that faces a vaguer and easier task, and any
difference in its answer would say more about missing context than about care.

So the cut is just before the failure -- ``Boundaries.cut_turn``. That gives
7,528 characters instead: the candidate inherits the same work in progress and
stands at the same decision the agent faced, endorse this or check it first,
with neither the wrong answer nor the complaint in view.

The resolution is not simply the next agent turn, and it is not the last turn of
the session. Two real trajectories:

  blittle/pressy: the user reports an unresolvable action name at 59, the agent
  fixes that specific thing at 65, and turns 70, 78, 105, 113, 124 are further
  user turns about entirely different errors -- a pnpm version conflict, a
  TypeScript resolution failure, a build command choice. Taking the last agent
  turn would pick a success criterion with nothing to do with the defect.

  moltis: the complaint at 470 is a failing test, the agent renames the stale
  test and reruns at 500, and turn 508 moves on to PR review comments.

So the reader must judge thread identity: which later turns concern the same
defect, where that thread closes, and whether it closes at all. A session where
the developer gave up, fixed it themselves, or simply stopped has no success
criterion, and a task built on it would score candidates against noise. Those
are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..llm import MODEL, configure_client, resilient, with_field_guide


class Quote(BaseModel):
    """A turn and the words in it that carry the finding."""

    turn: int = Field(description="The turn number.")
    text: str = Field(description="Short verbatim quote from that turn.")


class Trajectory(BaseModel):
    """Where a defect is raised, and where -- if ever -- it is put right."""

    request_turn: int = Field(
        description=(
            "The user turn that prompted the failing answer -- the request "
            "itself, before anything went wrong. It bounds the episode and is "
            "not itself the cut point."
        )
    )
    failed_turn: int = Field(description="The agent turn that got it wrong.")
    complaint_turn: int = Field(description="The user turn objecting to it.")

    defect: str = Field(
        description="One sentence: what specifically was wrong. Be concrete -- a named file, command, claim or value."
    )

    resolved: bool = Field(
        description=(
            "Whether the agent ever put THIS defect right in the visible "
            "transcript. False when the developer gave up, fixed it themselves, "
            "moved on without resolution, or the session simply ended."
        )
    )
    resolved_turn: int = Field(
        default=-1,
        description="The agent turn that resolved it, or -1 if it never was. This turn becomes the success criterion.",
    )
    resolution: str = Field(
        default="",
        description="What the resolving turn actually did, concretely. Empty when unresolved.",
    )
    rounds: int = Field(
        default=1,
        description=(
            "How many agent attempts this defect took after the complaint. 1 when "
            "the next attempt fixed it. Higher when the user pushed back again on "
            "the SAME defect."
        ),
    )

    later_turns_are_new_work: bool = Field(
        description=(
            "Whether user turns after the resolution concern different problems "
            "rather than this defect. Usually true: a long session moves on."
        )
    )

    evidence: list[Quote] = Field(
        default_factory=list,
        description="Quotes locating each point: the request, the failure, the complaint, and the resolution.",
    )
    notes: str = Field(default="", description="Anything that made this hard to judge.")


INSTRUCTIONS = """\
You are reading a real developer-agent session to locate the boundaries of one \
mistake, so it can become a benchmark task.

Find four points:

  1. The user's REQUEST that prompted the mistake.
  2. The agent's FAILED answer to it.
  3. The user's COMPLAINT about that answer.
  4. Where the agent RESOLVED that specific defect -- if it ever did.

Points 2 and 4 must each be a turn the agent WROTE: one shown below as \
`[turn N] AGENT:`. A line shown as `[turn N] calls ...` is a tool call, and \
`[turn N] -> ...` is a tool's output. Neither is an answer, and a task cannot \
be built from one. When the failure or the fix happened inside tool calls, \
give the AGENT turn that reported it to the developer.

The fourth is the hard one, and getting it wrong ruins the task.

The resolution is not automatically the next agent turn. Sometimes the user \
pushes back again on the same defect and it takes several attempts. Count those \
as rounds and give the turn where it was finally right.

The resolution is also not the last agent turn in the session. Long sessions \
move on to unrelated problems: a user reporting a different error two turns \
later is new work, not continued pushback. Judge whether later turns concern the \
SAME defect you identified, and stop at the point that defect closes.

Sometimes it never closes. The developer gives up, fixes it themselves, or the \
session ends mid-thread. Say so plainly by setting resolved to false. A task \
built on an unresolved thread has no success criterion and is worthless, so a \
false here is more useful than a guess.

Quote verbatim for each of the four points, with turn numbers, so every claim \
can be checked against the transcript."""


@dataclass
class Boundaries:
    """The turns a task is built from, once located."""

    request_turn: int
    failed_turn: int
    complaint_turn: int
    resolved_turn: int
    usable: bool
    reason: str

    @property
    def cut_turn(self) -> int:
        """Where a candidate's context ends: the turn before the failure.

        Not at the request. A request can sit far upstream of the failure it
        leads to, and cutting there strips the work in between. In
        blittle/pressy the request is turn 5 and the failure is turn 54, so a
        cut at the request leaves 515 characters -- the bare ask, with none of
        the exploration, the file read at turn 47, or the edit at turn 50 that
        the failing answer then endorses. A candidate starting from that is
        facing a vaguer, easier task, and any difference in its answer would
        say more about missing context than about care.

        Cutting just before the failure gives 7,528 characters instead: the
        candidate inherits the same work in progress and faces the same
        question the agent faced -- endorse this, or check it first -- with
        neither the wrong answer nor the complaint in view.

        This sometimes lands on the request itself, when the failure is the very
        next turn. That is not the thin case above: the investigation then sits
        *before* the request rather than after it, so the prefix still carries
        it. moltis cuts at its request turn and yields 38,190 characters,
        obsessiondb/rudel 26,899. Only the distance between request and failure
        differs, not the amount of work in view.
        """
        return self.failed_turn - 1


def boundaries(t: Trajectory) -> Boundaries:
    """Reduce a reading to the turns a task needs, and say if it is usable."""
    if not t.resolved or t.resolved_turn < 0:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, -1, False,
            "the defect was never resolved, so there is no success criterion",
        )
    # A turn the reader could not find comes back as -1, which sorts below every
    # real turn -- so an ordering test alone reports a coherent episode for an
    # episode with a hole in it.
    #
    # Only three turns are load-bearing. failed_turn sets the cut and supplies
    # the oracle, complaint_turn is the evidence the answer was wrong, and
    # resolved_turn is the success criterion. request_turn feeds none of those:
    # it records where the episode began. Demanding it too rejected
    # Lightprotocol/light-protocol and desplega-ai/agent-swarm, both of which
    # have a located failure, complaint and resolution, and yield 32,547 and
    # 14,789 characters of context -- usable tasks thrown away over missing
    # provenance.
    required = {
        "failed": t.failed_turn,
        "complaint": t.complaint_turn,
        "resolved": t.resolved_turn,
    }
    missing = [name for name, turn in required.items() if turn < 0]
    if missing:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, t.resolved_turn, False,
            f"these turns were never located: {', '.join(missing)}",
        )
    if not (t.failed_turn < t.complaint_turn < t.resolved_turn):
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, t.resolved_turn, False,
            f"turns are out of order: failed {t.failed_turn}, complaint "
            f"{t.complaint_turn}, resolved {t.resolved_turn}",
        )
    # Checked only when located, for the same reason.
    if t.request_turn >= 0 and t.request_turn >= t.failed_turn:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, t.resolved_turn, False,
            f"the request ({t.request_turn}) does not precede the failure ({t.failed_turn})",
        )
    return Boundaries(
        t.request_turn, t.failed_turn, t.complaint_turn, t.resolved_turn, True, "usable"
    )


def render(turns: list[dict], start: int, end: int, *, budget: int = 900) -> str:
    """Render a turn range for reading, keeping the narrative and compressing tools."""
    lines: list[str] = []
    for turn in turns:
        n = turn.get("turn_number") or 0
        if n < start or n > end:
            continue
        kind = turn.get("turn_type") or ""
        if kind in ("progress", "file_snapshot", "system_event", "queue_operation"):
            continue
        body = (turn.get("content") or "").strip()
        if kind == "user_prompt":
            lines.append(f"\n[turn {n}] USER:\n{body[:3000]}")
        elif kind == "assistant_response":
            lines.append(f"\n[turn {n}] AGENT:\n{body[:3000]}")
        elif kind == "tool_use":
            detail = turn.get("command") or turn.get("file_path") or body[:120]
            lines.append(f"[turn {n}] calls {turn.get('tool_name')}: {str(detail)[:200]}")
        elif kind == "tool_result" and body:
            lines.append(f"[turn {n}] -> {body[:budget]}")
    return "\n".join(lines)


async def locate(
    turns: list[dict], complaint_turn: int, *, lookback: int = 60, lookahead: int = 400
) -> Trajectory:
    """Read around a known complaint and locate the four boundary turns.

    The window reaches well past the complaint because resolution can take
    several rounds, and well before it because the request that prompted the
    failure may be many turns back.
    """
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="trajectory-reader",
        instructions=with_field_guide(INSTRUCTIONS, Trajectory),
        model=MODEL,
        output_type=Trajectory,
    )
    excerpt = render(turns, complaint_turn - lookback, complaint_turn + lookahead)
    prompt = (
        f"The user complains at turn {complaint_turn}. Locate the request that led "
        f"to the failure, the failing answer, and where the defect was resolved -- "
        f"or establish that it never was.\n\n{excerpt}"
    )
    result = await resilient(lambda: Runner.run(agent, prompt, max_turns=4))
    return result.final_output
