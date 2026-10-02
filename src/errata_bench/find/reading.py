"""Read a real pushback moment and judge what the agent did to earn it.


This is step 1 of the benchmark: it selects which moments are worth turning into
tasks. Given a point where a developer pushed back on a coding agent, it decides
whether the agent genuinely erred -- a false claim, an unverified assumption, a
shallow investigation -- or whether the user was expressing a preference, and
produces a success criterion stating what a candidate model must do instead.

Measured on a filtered sample of 30 moments: 13 viable (43%), and re-reading ten
of them gave the same viable decision 10 times out of 10. Yield differs sharply
by pushback kind -- failure_report 7/10 against correction 3/14 -- because a user
reporting something broken usually has a concrete error behind it, while a user
correcting an agent is often expressing taste.

What this does NOT do is verify anything. It reads and judges; it never runs a
command. A criterion it produces is prose, and turning that into something a
container can score is step 2.

Judgement here is the model's, deliberately: whether an agent made an
unwarranted assumption, buried a problem, or investigated shallowly is semantic,
and no pattern match decides it. What the model is NOT free to do is assert
without pointing: every finding must name the turn it comes from and quote the
line. A cited claim can be checked against the transcript; a score cannot.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..corpus.turns import build_excerpt
from ..llm import MODEL, configure_client, resilient, with_field_guide


class Evidence(BaseModel):
    """A specific thing the agent said or did, locatable in the transcript."""

    turn_number: int = Field(description="The turn this comes from.")
    quote: str = Field(
        description="Short verbatim quote from that turn -- the exact words that show the behaviour. Not a paraphrase."
    )


class Reading(BaseModel):
    """What one pushback moment contains."""

    # --- what happened, in plain terms ---
    what_user_asked: str = Field(
        description="One sentence: what the user originally wanted."
    )
    what_agent_did: str = Field(
        description="Two or three sentences: what the agent actually did in the turns leading to the pushback."
    )
    what_user_objected_to: str = Field(
        description="One or two sentences: what specifically the user pushed back on, in their own framing."
    )

    # --- the central question ---
    objection_kind: str = Field(
        description=(
            "Which best describes the user's objection: "
            "'real_error' (the agent did something factually wrong or harmful -- false claim, "
            "unverified assumption, buried problem, shallow investigation, biased test, broken code); "
            "'unwanted_but_defensible' (what the agent did was technically reasonable but not what "
            "the user intended); "
            "'preference' (purely a matter of taste or style, no error); "
            "'unclear' (the transcript does not show enough to tell)."
        )
    )
    failure_modes: list[str] = Field(
        default_factory=list,
        description=(
            "Zero or more of: false_claim, unverified_assumption, buried_problem, "
            "shallow_investigation, biased_testing, ignored_instruction, context_rot, "
            "scope_creep, unrequested_change, other. Only those the evidence supports."
        ),
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="The turns and quotes that justify objection_kind and failure_modes. At least one when objection_kind is not 'unclear'.",
    )

    # --- could this become a benchmark task? Four checks first (gate 2, 10-02) ---
    pushback_is_the_developers: bool = Field(
        description=(
            "Whether the pushback is the developer's own objection to work the agent did. False when it is "
            "another agent's message, a notice, or text pasted in without the developer standing behind it; "
            "when it questions code that was in the repository before the agent touched it, unless it disputes "
            "what the agent said or did about that code; and when the developer only picks one of the options "
            "the agent itself offered."
        )
    )
    knowable_at_the_failing_turn: bool = Field(
        description=(
            "Whether the agent could have got it right at its failing turn with what it had: the conversation "
            "before that turn, the repository, its tools and general knowledge. The pushback reporting the "
            "error does not make it unknowable: ask whether the agent could have found it by checking. False "
            "only when the right answer depended on a fact only the developer knew, a requirement or decision "
            "first given in or after the pushback, or information outside the conversation and the repository."
        )
    )
    visible_from_the_repository: bool = Field(
        description=(
            "Whether the defect can be seen from the repository and the conversation. False when it shows "
            "only in a live service, a deployment, an account, a device, or a recording that no checkout of "
            "the repository reproduces."
        )
    )
    consistent_with_instructions: bool = Field(
        description=(
            "Whether getting it right means doing what the developer asked. False when the criterion would "
            "have a model override the developer's explicit instruction or the project's own documented "
            "decision."
        )
    )
    benchmark_viable: bool = Field(
        description=(
            "True only if another model could be given the transcript up to the agent's "
            "failing turn and be meaningfully scored on whether it repeats the mistake. "
            "False when the context is too thin, the objection is pure taste, or no "
            "identifiable agent behaviour caused it."
        )
    )
    success_criterion: str = Field(
        default="",
        description=(
            "If viable: what a candidate model must do or avoid, stated so a judge could "
            "check it. Derived from what this user actually wanted, not from general "
            "principles. Empty if not viable."
        ),
    )
    justifying_turn: int = Field(
        default=-1,
        description=(
            "The turn number that proves the criterion -- normally the user's pushback "
            "itself, or a later turn where the consequence surfaced. -1 if not viable. "
            "A criterion with no justifying turn is our opinion, not the user's."
        ),
    )
    context_sufficient: bool = Field(
        description=(
            "Whether the excerpt shows the failing answer and the objection, and enough of the work behind "
            "them to tell what went wrong. False only when the output, edit, plan or message the judgement "
            "turns on is cut or missing from the excerpt; not because it holds no reproduction or does not "
            "name the faulty line."
        )
    )

    @property
    def checks_failed(self) -> list[str]:
        """The checks a viable moment must pass and this one does not (gate 2, 10-02); and a preference or an
        unclear objection, which the instructions call not viable."""
        failed = [name for name in ("pushback_is_the_developers", "knowable_at_the_failing_turn",
                                    "visible_from_the_repository", "consistent_with_instructions",
                                    "context_sufficient") if not getattr(self, name)]
        if self.objection_kind in ("preference", "unclear"):
            failed.append(f"objection_kind {self.objection_kind}")
        return failed
    notes: str = Field(
        default="", description="Anything surprising, or why this was hard to judge."
    )


INSTRUCTIONS = """\
You are reading a real conversation between a developer and a coding agent, at a \
moment where the developer pushed back on what the agent did.

Your job is to work out what the agent did to earn that pushback, and whether \
this moment could become a benchmark task for evaluating other coding agents.

Read the whole excerpt before judging. The agent's turns include its tool calls, \
so you can see what it actually inspected versus what it claimed.

The distinction that matters most:

  * Sometimes the agent genuinely erred -- it stated something it had not \
checked, assumed instead of verifying, reported success it had not confirmed, \
hit a problem and quietly moved on, investigated shallowly and concluded \
anyway, or wrote a test shaped to pass rather than to detect. These are real \
errors regardless of whether the user was polite about them.

  * Sometimes the agent did something defensible that simply was not what the \
user wanted. Still counts as wrong in the sense that matters here -- the user's \
intention was missed -- but it is a different kind of wrong.

  * Sometimes the user just prefers something else. No error at all.

Be honest about which you are looking at. Do not inflate a preference into an \
error to make the case look useful, and do not dismiss a real error because the \
user was mild about it.

CITE EVERYTHING. Every claim you make about the agent's behaviour must name the \
turn and quote the words. If you cannot point at the text, do not claim it. A \
finding nobody can check is worth nothing here.

On benchmark viability: ask whether another model, given this same context up to \
the agent's failing turn, could be scored on whether it makes the same mistake. \
If the answer depends on taste, or the context is too thin to know what went \
wrong, say it is not viable. Being strict here is more useful than being \
generous -- we are trying to find out if this data supports a benchmark at all, \
and an optimistic answer helps nobody.

Before you decide, answer four questions from the excerpt, each on its own:

  * Is the pushback the developer's own objection to the agent's work? Not \
another agent's message, a notice, or pasted text the developer does not stand \
behind; not a question about code that was there before the agent, unless it \
disputes what the agent said or did about it; not the developer picking one of \
the options the agent offered.
  * Could the agent have got it right at its failing turn, with the \
conversation so far, the repository, its tools and what it knows? The pushback \
reporting the error is how you learn of it, not a reason it was unknowable. \
No only when the right answer depended on a fact only the developer knew, or a \
requirement or decision first given in or after the pushback.
  * Can the defect be seen from the repository and the conversation? Not when \
it shows only in a live service, a deployment, an account, a device or a \
recording that no checkout reproduces.
  * Would getting it right mean doing what the developer asked? Not when the \
criterion has a model override the developer's explicit instruction or the \
project's own documented decision.

A moment is viable only when all four are yes and the context is sufficient. \
The criterion must name what this agent should have checked or done here: a \
moment whose checks say no is not rescued by restating its criterion as a rule \
almost any moment would meet. If your notes give a reason one of the answers is \
no, it is no.
"""



async def read_pushback(
    turns: list[dict], pushback_turn: int, *, max_turns: int = 6
) -> "Reading":
    """Run the reader over one pushback moment."""
    from agents import Agent, Runner

    configure_client()
    # Every message whole, each call with its input, and every cut of a call or
    # a result said with how much went (G-92): record 1 cut each message at
    # 4,000 characters with nothing said, in 383 of the first 681 Entire moments.
    excerpt = build_excerpt(turns, pushback_turn, mark_pushback=True, record=2, whole_messages=True)
    agent = Agent(
        name="pushback-reader",
        instructions=with_field_guide(INSTRUCTIONS, Reading),
        model=MODEL,
        output_type=Reading,
    )
    prompt = (
        f"The developer pushed back at turn {pushback_turn}. Everything below is "
        f"the conversation up to and including that moment.\n\n{excerpt}"
    )
    result = await resilient(lambda: Runner.run(agent, prompt, max_turns=max_turns))
    return held_to_its_checks(result.final_output)


def held_to_its_checks(reading: "Reading") -> "Reading":
    """A reading whose own checks say no is not viable, whatever its verdict says (gate 2, 10-02).

    Six of fifteen sampled "viable" verdicts were wrong, and in five the
    reader's notes gave the reason: a live server, a correction only the
    developer could know, an instruction the criterion would have a model
    defy. The verdict now follows the checks, as triage's follows its two.
    """
    failed = reading.checks_failed
    if reading.benchmark_viable and failed:
        said = f"Not viable, by these checks: {', '.join(failed)}."
        if reading.success_criterion:
            said += f" The criterion it proposed: {reading.success_criterion}"
        reading.benchmark_viable = False
        reading.success_criterion = ""
        reading.justifying_turn = -1
        reading.notes = (reading.notes + " " if reading.notes else "") + said
    return reading


