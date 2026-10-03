"""Thread B: the developer's reply to each report, read as pushback or not, and if so, what it objects to.

Every reply is read, not only those SWE-chat labelled as pushback: SWE-chat's
label agreed with expert labels 63 to 74 percent of the time (its paper), and a
list built from the label alone would miss what it missed. The label is kept on
each row to compare. The categories are the reader's (find/reading.py), so this
list is classified as the 556 real-error moments were.

The classifier sees the same window the reviewer saw, then the reply. It never
sees the reviewer's list.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .review import KINDS

OBJECTIONS = ("real_error", "unwanted_but_defensible", "preference", "unclear")


class Reply(BaseModel):
    """What the developer's reply does."""

    is_pushback: bool = Field(description="True if the reply says the agent's work is wrong, broken, incomplete, "
                                          "not what was asked or not believable, rejects it, corrects it, or takes "
                                          "over. A new request, a question, thanks or approval is not pushback.")
    what_developer_objects_to: str = Field(default="", description="If pushback: one or two sentences, in the "
                                           "developer's own framing. Empty otherwise.")
    developer_quote: str = Field(default="", description="If pushback: the reply's exact words that carry it, "
                                 "copied verbatim. Empty otherwise.")
    objection_kind: str = Field(default="", description="If pushback, one of: 'real_error' (the agent did something "
                                "factually wrong or harmful: false claim, unverified assumption, buried problem, "
                                "shallow investigation, biased test, broken code); 'unwanted_but_defensible' "
                                "(technically reasonable but not what the developer intended); 'preference' (taste "
                                "or style, no error); 'unclear' (the material does not show enough to tell). "
                                "Empty if not pushback.")
    failure_modes: list[str] = Field(default_factory=list, description="If pushback: zero or more of "
                                     + ", ".join(KINDS) + ". Only those the evidence supports.")
    target_shown: bool = Field(default=False, description="If pushback: True if the work the reply objects to is in "
                               "the material shown before the reply; False if it is earlier work not shown.")
    target_turn: float = Field(default=-1, description="If the target is shown: the turn it is in. -1 otherwise.")
    target_quote: str = Field(default="", description="If the target is shown: the agent's exact words or result "
                              "the reply objects to, copied verbatim. Empty otherwise.")


INSTRUCTIONS = """\
You are reading a real conversation between a developer and a coding agent. You are shown \
what was in front of the developer when the agent handed its work back: the developer's \
first message in the session, the current request, and what the agent did after it, ending \
with its report. Then you are shown the developer's reply.

Decide whether the reply pushes back on the agent's work: says it is wrong, broken, \
incomplete, not what was asked, or not believable; rejects it; corrects it; or takes over. \
A new request, a question, thanks, or approval is not pushback, even when it asks for more \
work.

If it is pushback, say what the developer objects to in their own framing, quote the \
reply's words that carry it, and classify it:
  * objection_kind: 'real_error' when the agent did something factually wrong or harmful; \
'unwanted_but_defensible' when what it did was reasonable but not what the developer \
intended; 'preference' for taste or style with no error; 'unclear' when the material does \
not show enough to tell.
  * failure_modes: only those the evidence supports.
  * the target: if the work the reply objects to is in the material, quote the agent's \
exact words or result and give the turn; if it is earlier work not shown, say so.

Be honest about which you are looking at: do not inflate a preference into an error, and \
do not dismiss a real error because the developer was mild. Copy every quote verbatim.
"""


async def classify(window_text: str, reply: str, handoff_turn: float, *, model: str, max_turns: int = 3):
    """Run the classifier on one reply. Returns the run result (`final_output` is a `Reply`)."""
    from agents import Agent, Runner

    from ..llm import configure_client, resilient, with_field_guide

    configure_client()
    agent = Agent(name="study-replies", instructions=with_field_guide(INSTRUCTIONS, Reply),
                  model=model, output_type=Reply)
    prompt = f"{window_text}\n\nTHE DEVELOPER'S REPLY (turn {handoff_turn:g}):\n{reply.strip()}"
    return await resilient(lambda: Runner.run(agent, prompt, max_turns=max_turns))


def checked(reply: Reply, window_text: str, reply_text: str) -> dict:
    """The reading as stored: with whether each quote is really where it says, and whether its labels are known."""
    from ..score.judge import quote_appears

    row = reply.model_dump()
    row["developer_quote_found"] = bool(reply.developer_quote.strip()) and quote_appears(reply.developer_quote,
                                                                                         reply_text)
    row["target_quote_found"] = bool(reply.target_quote.strip()) and quote_appears(reply.target_quote, window_text)
    row["labels_known"] = ((not reply.is_pushback or reply.objection_kind in OBJECTIONS)
                           and all(m in KINDS for m in reply.failure_modes))
    return row
