"""The merge: line the reviewer's problems up with the developer's pushback, one pushback at a time.

For a pushback in reply k, the candidates are the problems the reviewer listed
at reports k-2, k-1 and k of the same session: a developer often objects a turn
or two after the work, once they have tried it. Each candidate is judged 'same'
(the problem the developer raised), 'related' (the same work, but a cause,
symptom or neighbour of that fault) or 'different'. A pushback with no
candidates is the developer's alone without a call.

A problem the reviewer listed that no pushback within the next three replies
matches as 'same' is the reviewer's alone. Whether such a problem is real (the
developer let it pass) or a false alarm is not decided here: that is the hand
check (docs/study.md).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

LOOKBACK = 2          # a pushback in reply k is compared with the problems of reports k-2..k
MATCHES = ("same", "related", "different")


class Verdict(BaseModel):
    problem_id: str = Field(description="The id of the reviewer's problem, exactly as given.")
    match: str = Field(description="'same', 'related' or 'different'.")
    developer_words: str = Field(default="", description="For 'same' or 'related': the developer's exact words that "
                                 "overlap, copied verbatim from the reply. Empty for 'different'.")
    reviewer_words: str = Field(default="", description="For 'same' or 'related': the reviewer's exact words that "
                                "overlap, copied verbatim from the problem. Empty for 'different'.")
    reason: str = Field(description="One sentence: why.")


class Merge(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list, description="One verdict for each problem listed.")


INSTRUCTIONS = """\
You are comparing two independent readings of the same coding session.

A developer replied to a coding agent and pushed back on its work. Separately, a reviewer \
who never saw the developer's reply read the agent's work at the moments it handed back, \
and listed problems. Decide, for each of the reviewer's problems listed, whether it is the \
problem the developer raised:
  * 'same': the reviewer names the fault the developer raised, in the same piece of work, \
even if worded differently or less specifically;
  * 'related': the reviewer flags the same piece of work, or a cause or symptom of the \
fault, but not the fault the developer raised;
  * 'different': another problem.

Judge by substance, not wording. A catch-all such as "there may be bugs" or "it should test \
more" is not 'same' unless it names the fault. For 'same' and 'related', copy the words \
that overlap from the developer's reply and from the reviewer's problem, verbatim. Give one \
verdict for every problem listed, using its id exactly.
"""


def candidates(problems_by_report: dict[int, list[dict]], k: int) -> list[dict]:
    """The reviewer's problems a pushback in reply k is compared with: reports k-LOOKBACK..k.

    Only problems whose quote is in the work their report judged (`quote_in_work`):
    an invented quote is not a finding, and one quoting the context is the
    previous report's.
    """
    out = []
    for j in range(max(1, k - LOOKBACK), k + 1):
        for i, p in enumerate(problems_by_report.get(j, [])):
            if p.get("quote_in_work"):
                out.append({**p, "problem_id": f"r{j}p{i}", "report": j})
    return out


def prompt(reply_row: dict, reply_text: str, cands: list[dict]) -> str:
    lines = [f"THE DEVELOPER'S REPLY:\n{reply_text.strip()}",
             f"\nWHAT THE DEVELOPER OBJECTS TO (a separate reading of the reply): "
             f"{reply_row.get('what_developer_objects_to') or '(not stated)'}"]
    if reply_row.get("target_quote_found") and reply_row.get("target_quote"):
        lines.append(f"THE AGENT'S WORDS IT OBJECTS TO: \"{reply_row['target_quote']}\"")
    lines.append("\nTHE REVIEWER'S PROBLEMS:")
    for c in cands:
        lines.append(f"- id {c['problem_id']} (at the handback {c['report']}): {c['what_is_wrong']} "
                     f"[it quoted: \"{c['quote']}\"]")
    return "\n".join(lines)


async def merge(reply_row: dict, reply_text: str, cands: list[dict], *, model: str, max_turns: int = 3):
    """Run the merge for one pushback. Returns the run result (`final_output` is a `Merge`)."""
    from agents import Agent, Runner

    from ..llm import configure_client, resilient, with_field_guide

    configure_client()
    agent = Agent(name="study-merge", instructions=with_field_guide(INSTRUCTIONS, Merge),
                  model=model, output_type=Merge)
    return await resilient(lambda: Runner.run(agent, prompt(reply_row, reply_text, cands), max_turns=max_turns))


def checked(result: Merge, cands: list[dict], reply_text: str) -> list[dict]:
    """The verdicts as stored: one per candidate, in order; a candidate the merge skipped is recorded as missing."""
    from ..score.judge import quote_appears

    by_id = {v.problem_id: v for v in result.verdicts}
    out = []
    for c in cands:
        v = by_id.get(c["problem_id"])
        if v is None:
            out.append({"problem_id": c["problem_id"], "report": c["report"], "match": None, "missing": True})
            continue
        overlap = v.match in ("same", "related")
        out.append({"problem_id": c["problem_id"], "report": c["report"], "match": v.match,
                    "match_known": v.match in MATCHES, "developer_words": v.developer_words,
                    "reviewer_words": v.reviewer_words, "reason": v.reason,
                    # A 'same' or 'related' must show the overlap on both sides; 'different' shows none.
                    "developer_words_found": (not overlap) or (bool(v.developer_words.strip())
                                                               and quote_appears(v.developer_words, reply_text)),
                    "reviewer_words_found": (not overlap) or (bool(v.reviewer_words.strip()) and quote_appears(
                        v.reviewer_words, f"{c.get('what_is_wrong', '')}\n{c.get('quote', '')}")),
                    "missing": False})
    return out
