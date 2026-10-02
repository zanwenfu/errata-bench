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

from pydantic import BaseModel, Field, PrivateAttr

from ..corpus.turns import PEER_SPEAKER, _cut, speaker
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
    objection: bool = Field(
        description=(
            "Whether the complaint objects to something a specific earlier answer of the agent's said or did "
            "wrong. A report that the agent's work failed is one, even when it answers the agent's question. "
            "False for a new request or a requirement first stated in it, picking one of the options the agent "
            "offered, approval, a retraction, or a message from another agent or a tool."
        )
    )
    knowable: bool = Field(
        description=(
            "Whether the agent could have got it right at its failed answer with what it had: the conversation "
            "before that answer, the repository, its tools and general knowledge. The complaint reporting the "
            "error does not make it unknowable: ask whether the agent could have found it by checking. False "
            "only when the right answer depended on a fact only the developer knew, or a requirement or "
            "decision first given in or after the complaint."
        )
    )

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
    resolution_fixes_it: bool = Field(
        default=False,
        description=(
            "Whether the resolving turn itself puts this defect right: it gives the corrected answer, or "
            "reports the change that fixed it and what changed -- including a fix the developer suggested, "
            "once the agent applies it. False when it is a status summary, an acknowledgement ('noted', "
            "'you're right'), a plan, a question, or a report of other work; and when the developer made "
            "the fix themselves and no agent turn reports it. False when unresolved."
        ),
    )
    rounds: int = Field(
        default=1,
        description=(
            "How many agent attempts this defect took after the complaint. 1 when "
            "the next attempt fixed it. Higher when the user pushed back again on "
            "the SAME defect."
        ),
    )

    # Where the view that gave this answer ended (`locate`); not asked of the model.
    _looked_to: int = PrivateAttr(default=-1)
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

Check the complaint first. It must object to something a specific earlier \
answer of the agent's said or did wrong; a report that its work failed is one, \
even when it answers the agent's question ("can you test it?" -- "still fails"). \
A new request, a requirement first stated in it, picking one of the options the \
agent offered, approval, a retraction ("I was mistaken"), or a message from \
another agent is not a complaint: say so (objection false), and do not build a \
defect around it. Then check the agent could have got it right with what it \
had: the conversation before the failed answer, the repository, its tools and \
what it knows. The complaint reporting the error is how you learn of it, not a \
reason it was unknowable; say knowable false only when the right answer \
depended on a fact only the developer knew, or a requirement or decision first \
given in or after the complaint. When the complaint lists several problems, take the first \
the failed answer is responsible for, and say in the defect which it is.

The fourth is the hard one, and getting it wrong ruins the task.

A resolution is an agent turn that puts the defect right: the corrected answer, \
or a report of the fix that says what changed. An acknowledgement ("correction \
accepted", "you're right"), a status line or a session summary, a plan or a \
question is not one, and neither is a fix the developer made themselves that \
no agent turn reports: say so (resolution_fixes_it false). A fix the developer \
suggested counts once the agent applies it and says what changed. \
When the developer adds new requirements after the complaint, the resolution \
still answers the complaint, and rounds count only pushback on the same defect, \
not changes of design.

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
    """Reduce a reading to the turns a task needs, and say if it is usable.

    The complaint is checked before the resolution: a retraction, a new request
    or another agent's message all read "never resolved" when it was not, and
    6 of 15 sampled usable trajectories were built on one, or on a defect the
    agent could not have known (gate 2, 10-02).
    """
    if not t.objection:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, -1, False,
            "the complaint does not object to an earlier answer of the agent's",
        )
    if not t.knowable:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, -1, False,
            "what the right answer needed was not the agent's to know at its failed answer",
        )
    if not t.resolved or t.resolved_turn < 0:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, -1, False,
            "the defect was never resolved, so there is no success criterion",
        )
    # A status summary admitted cyc-seattle-isthmia-74 on its own words: the
    # agent never corrected the estimate, the developer did (pilot audit, 10-02).
    if not t.resolution_fixes_it:
        return Boundaries(
            t.request_turn, t.failed_turn, t.complaint_turn, -1, False,
            "the turn given as the resolution does not put the defect right: a status line, an "
            "acknowledgement, a plan, or a fix the developer supplied is no success criterion",
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


def render(turns: list[dict], start: int, end: int, *, budget: int = 900, calls: int = 200) -> str:
    """Render a turn range for reading: every message whole, each call and result cut to a length, and said so.

    Each message was cut at 3,000 characters, a result at 900 and a call at
    200, with nothing said. 17 of the 89 failed answers the first Entire runs
    located, and 12 of their resolutions, were longer, and in a sampled one
    the cut hid the wrong sentence itself (G-92). Whole, the view is 4% longer
    at the median (72,781 characters) and at most 2.7 times as long.
    """
    lines: list[str] = []
    for turn in turns:
        n = turn.get("turn_number") or 0
        if n < start or n > end:
            continue
        kind = turn.get("turn_type") or ""
        if kind in ("progress", "file_snapshot", "system_event", "queue_operation"):
            continue
        body = (turn.get("content") or "").strip()
        if not body and kind != "tool_use":
            continue
        # A row put back from the transcript is labelled with the turn it is shown
        # under, as `build_excerpt` labels it: the reader names boundaries by whole
        # turns, and a "[turn 180.33333333333334]" invited a fractional one.
        label = turn.get("shown_as", n)
        if kind == "user_prompt":
            lines.append(f"\n[turn {n}] {speaker(turn)}:\n{body}")
        elif kind == "peer_message":
            lines.append(f"\n[turn {n}] {PEER_SPEAKER}:\n{body}")
        elif kind == "assistant_response":
            lines.append(f"\n[turn {label}] AGENT:\n{body}")
        elif kind == "tool_use":
            detail = turn.get("command") or turn.get("file_path") or body
            lines.append(f"[turn {label}] calls {turn.get('tool_name')}: {_cut(str(detail), calls, 2)}")
        elif kind == "tool_result" and body:
            lines.append(f"[turn {label}] -> {_cut(body, budget, 2)}")
    return "\n".join(lines)


#: The rules a usable trajectory was judged under, stored on its row. 2 from
#: 10-02: the resolution must put the defect right (`resolution_fixes_it`)
#: and a view left open is looked at again. A row of earlier rules -- the
#: pilot's cyc-seattle-isthmia-74 among them -- holds no failed answer
#: against another moment (`stages.screening.failures_held`).
RULES = 2

# How far past the complaint the second look reaches, and the most it may read.
FURTHER = 1200
FURTHER_CHARS = 200_000


def reach(turns: list[dict], start: int, near: int, far: int, limit: int = FURTHER_CHARS) -> int:
    """The furthest end of a view from ``start``, no further than ``far``, whose rendering fits in ``limit``
    characters; ``near`` when none past it does. A view only grows as its end moves on."""
    lo, hi = near, far
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(render(turns, start, mid)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


async def locate(
    turns: list[dict], complaint_turn: int, *, lookback: int = 60, lookahead: int = 400, further: int = FURTHER
) -> Trajectory:
    """Read around a known complaint and locate the four boundary turns.

    The window reaches well past the complaint because resolution can take
    several rounds, and well before it because the request that prompted the
    failure may be many turns back.

    A defect the view leaves unresolved, in a session that goes on past it,
    is looked at once more, as far as ``further`` turns past the complaint or
    as much as fits in FURTHER_CHARS (pilot audit, 10-02). 15 of the 18
    trajectories the first Entire runs called never resolved were in sessions
    that went on past the view, and split-flap-329's fix came 635 turns after
    its complaint. Turns are rows, tool calls among them, so 400 can be a few
    minutes of work. The second answer is the one kept; ``looked_to`` says
    where the view that gave it ended.
    """
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="trajectory-reader",
        instructions=with_field_guide(INSTRUCTIONS, Trajectory),
        model=MODEL,
        output_type=Trajectory,
    )

    async def ask(end: int) -> Trajectory:
        excerpt = render(turns, complaint_turn - lookback, end)
        prompt = (
            f"The user complains at turn {complaint_turn}. Locate the request that led "
            f"to the failure, the failing answer, and where the defect was resolved -- "
            f"or establish that it never was.\n\n{excerpt}"
        )
        result = await resilient(lambda: Runner.run(agent, prompt, max_turns=4))
        t = result.final_output
        t._looked_to = end
        return t

    near = complaint_turn + lookahead
    t = await ask(near)
    last = max((x.get("turn_number") or 0 for x in turns), default=0)
    if (t.resolved and t.resolved_turn >= 0) or last <= near:
        return t
    end = reach(turns, complaint_turn - lookback, near, min(complaint_turn + further, last))
    return await ask(end) if end > near else t
