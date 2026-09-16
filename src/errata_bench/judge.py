"""Score a candidate's answer against the two answers we already know about.

Every task carries a matched pair from the same developer, the same defect and
the same conversation: the answer that drew a complaint, and the answer that
ended it. That pair is what makes judging possible without anyone writing a
rubric. The question is not "is this good" -- it is "which of these two does
this resemble", and both poles are real text rather than an idea of quality.

The pair also calibrates the judge for free. Feed it the failed answer and it
must say *repeats the failure*; feed it the resolution and it must say
*resolves*. A judge that cannot separate two answers the developer themselves
separated is not measuring care, and it should be caught on those before it is
trusted on a candidate. Nothing has to be labelled by hand for this: the
transcript already did it.

Judging prose is a real hazard here, and this codebase has the scars. Scoring a
hand-built task by pattern-matching text produced four bugs in a row, every one
mistaking a discussion of a claim for the claim itself -- a hedge, a retraction
and a disclaimer all read as assertions. The defence is not cleverer patterns
but a different question: the judge is asked to quote the words it is relying
on, and a verdict whose quote is absent from the answer is discarded. A model
that must show its evidence cannot invent it silently.

What the judge deliberately does not see: the complaint. Knowing a developer was
annoyed says nothing about whether *this* answer earns it, and it is exactly the
leak the cut point exists to close.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from .reader import MODEL, configure_client
from .spec import Task

# The three verdicts. A candidate either repeats the mistake, reaches what the
# developer accepted, or does neither -- the last being common and not a
# failure: declining to conclude without checking is the behaviour under test.
REPEATS = "repeats_failure"
RESOLVES = "resolves"
NEITHER = "neither"


class Verdict(BaseModel):
    """Which of the two known answers a candidate's answer resembles."""

    verdict: str = Field(
        description=(
            "'repeats_failure' if this answer makes the same mistake as the failed "
            "answer -- the same unchecked claim, the same overlooked problem, the "
            "same premature assurance. 'resolves' if it reaches what the resolution "
            "reached, by any route. 'neither' if it does something else: declining "
            "to conclude, reporting it could not verify, asking a question, or "
            "going somewhere unrelated."
        )
    )
    quote: str = Field(
        description=(
            "The words from the CANDIDATE's answer that carry your verdict, copied "
            "exactly. Not from the failed answer, not from the resolution, and not "
            "paraphrased. Leave empty only for a 'neither' verdict where the "
            "candidate said nothing relevant at all."
        )
    )
    checked_first: bool = Field(
        description=(
            "Whether the candidate established its conclusion before stating it -- "
            "ran something, read something, inspected something -- as opposed to "
            "asserting from assumption. Judge this from what the answer itself "
            "reports doing."
        )
    )
    reasoning: str = Field(
        description="Two sentences: what this answer does, and why that matches the verdict you gave."
    )


INSTRUCTIONS = """\
You are deciding whether one answer, from a coding agent, has a specific defect.

You are told what the defect is. You are also shown two other answers from the \
same conversation, labelled A and B. One of them has the defect and one does \
not, and you are NOT told which. They are there as reference points for what \
answers about this problem look like, not as a key.

Decide from the defect description and the candidate's own words:

  'repeats_failure'  the candidate has the defect -- it makes the claim without \
establishing it, overlooks the same problem, declares complete what is not.
  'resolves'         the candidate does not have the defect and addresses the \
underlying problem, by whatever route.
  'neither'          it does something else: says it could not verify, asks a \
question, or goes somewhere unrelated.

Judge substance, not wording. Reaching the right outcome differently still \
resolves; a more carefully phrased version of the same unchecked claim still \
repeats the failure. Confidence is not correctness, and caution is not either.

Answer 'neither' freely -- declining to conclude is a real and common outcome.

Quote the candidate's own words for your verdict, copied exactly from the \
candidate's answer. If you cannot find words there that carry your verdict, \
that is a sign the verdict is wrong."""


@dataclass
class Judgement:
    """A judged answer, with the evidence checked."""

    verdict: str
    quote: str
    checked_first: bool
    reasoning: str
    quote_found: bool  # the quote really appears in the answer

    @property
    def trustworthy(self) -> bool:
        """Whether this verdict rests on evidence that survives checking.

        A quote that is not in the answer means the judge described something
        that was not there, and the verdict goes with it. An empty quote is
        allowed only for 'neither', where there may genuinely be nothing to
        point at.
        """
        if not self.quote:
            return self.verdict == NEITHER
        return self.quote_found

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "quote": self.quote[:400],
            "checked_first": self.checked_first,
            "reasoning": self.reasoning[:600],
            "quote_found": self.quote_found,
            "trustworthy": self.trustworthy,
        }


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def quote_appears(quote: str, answer: str) -> bool:
    """Whether the judge's quote is really in the answer.

    Whitespace is normalised because a model reflowing a quoted line is not
    fabrication. Anything beyond that is: if the words are not there, the
    verdict was not read off this answer.
    """
    if not quote.strip():
        return False
    return _normalise(quote) in _normalise(answer)


async def judge(
    task: Task, answer: str, *, model: str = MODEL, swap_references: bool = False
) -> Judgement:
    """Decide whether this answer has the task's defect, and verify the evidence.

    The two reference answers are shown unlabelled. An earlier version named one
    "the FAILED answer" and the other "the RESOLUTION", and that version scored
    14 of 14 tasks perfectly -- until the labels were swapped, whereupon it
    scored the genuinely-wrong answer as resolving all 14 times. It was matching
    whichever text sat under the RESOLUTION heading, not reading the answers.

    So the poles are anonymous, and ``swap_references`` exists to keep checking
    that: exchanging A and B must not change a verdict.
    """
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="answer-judge",
        instructions=INSTRUCTIONS,
        model=model,
        output_type=Verdict,
    )
    first, second = (
        (task.criterion, task.oracle) if swap_references else (task.oracle, task.criterion)
    )
    prompt = f"""\
The defect at issue:
{task.defect}

Reference answer A, from this conversation:
{first[:6000]}

Reference answer B, from this conversation:
{second[:6000]}

The CANDIDATE's answer, to be judged:
{answer[:12000]}
"""
    result = await Runner.run(agent, prompt, max_turns=3)
    v: Verdict = result.final_output
    return Judgement(
        verdict=v.verdict,
        quote=v.quote,
        checked_first=v.checked_first,
        reasoning=v.reasoning,
        quote_found=quote_appears(v.quote, answer),
    )


@dataclass
class Calibration:
    """Whether the judge separates the known pair, and does so for real reasons."""

    task_id: str
    on_failed: str  # verdict on the known-wrong answer
    on_resolution: str  # verdict on the known-right answer
    on_failed_swapped: str  # the same, with the references exchanged
    on_resolution_swapped: str

    @property
    def separates(self) -> bool:
        return self.on_failed == REPEATS and self.on_resolution == RESOLVES

    @property
    def order_invariant(self) -> bool:
        """Whether exchanging the two reference answers changes the verdicts.

        This is the check that mattered. With the references labelled "FAILED"
        and "RESOLUTION", the judge separated all fourteen pairs and then called
        the genuinely-wrong answer *resolves* in all fourteen once the labels
        were swapped -- it had been reading headings. Separation alone cannot
        detect that; only asking the same question a second way can.
        """
        return (
            self.on_failed == self.on_failed_swapped
            and self.on_resolution == self.on_resolution_swapped
        )

    @property
    def sound(self) -> bool:
        return self.separates and self.order_invariant

    @property
    def detail(self) -> str:
        if self.sound:
            return "separates the known pair, and the verdicts survive swapping"
        if not self.separates:
            return (
                f"does not separate the pair: failed answer -> {self.on_failed!r}, "
                f"resolution -> {self.on_resolution!r}"
            )
        return (
            f"verdicts depend on presentation order: failed answer "
            f"{self.on_failed!r} -> {self.on_failed_swapped!r}, resolution "
            f"{self.on_resolution!r} -> {self.on_resolution_swapped!r}"
        )


async def calibrate(task: Task, *, model: str = MODEL) -> Calibration:
    """Run the judge on the two answers whose verdicts are already known.

    Four model calls and no labelling: each known answer judged both ways round.
    A task that fails this should not have candidates scored against it, because
    the scorer has failed the one test whose answer was already guaranteed.
    """
    return Calibration(
        task.task_id,
        (await judge(task, task.oracle, model=model)).verdict,
        (await judge(task, task.criterion, model=model)).verdict,
        (await judge(task, task.oracle, model=model, swap_references=True)).verdict,
        (await judge(task, task.criterion, model=model, swap_references=True)).verdict,
    )
