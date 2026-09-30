"""Which developer messages push back, labelled as SWE-chat labelled its own (#16, step 7).

The pipeline takes its moments from the corpus's `prompt_pushback` label
(`run.py`, `PUSHBACK_KINDS`). SWE-chat's came from a model, Qwen3.5-9B, reading
each developer message with the conversation before it, under the codebook
printed in its paper's Appendix E.2.4 (arXiv 2604.20779). Entire's sessions come
with no label, so the collected corpus holds no moment until they are labelled.

Here that codebook, word for word (`CODEBOOK`), is asked of a model about the
view triage, the next stage, reads of the same moment: the last
`CONTEXT_CHARS` of the pipeline's own rendering of the conversation, lost calls
put back as screening puts them back, and the message as the rendering shows
it. Calibration on SWE-chat's own messages, against SWE-chat's labels, says how
far the two agree before any Entire message is labelled
(`scripts/crawl_label.py calibrate`).

The label is only the first filter: triage and the reader read every moment it
passes, so what matters most is that it misses few pushbacks. SWE-chat's
codebook has three pushback classes and non_pushback. Its corpus also carries
`takeover` (435 of 62,544 prompts), which the final codebook does not have;
nothing here produces it.

One row per labelled message, in ``labels.jsonl``, keyed by session and turn
and carrying the message's digest; `corpus.assemble` puts a label on its row
only while the digest still matches. A row that errored is asked again. The
model's explanation is stored as ``why``: the row store takes "error:" in a
row's ``reason`` for a failure, and a pushback's explanation often says it.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

LABELS = ("correction", "rejection", "failure_report", "non_pushback")
PUSHBACK = frozenset(LABELS) - {"non_pushback"}
# What triage reads of the same moment (`find.triage`: the excerpt's last 9,000
# characters), and of the message itself (`corpus.turns.MESSAGE_CHARS`).
CONTEXT_CHARS = 9000
INTERRUPTED = "[Request interrupted by user"
CONTINUED = "This session is being continued"

# SWE-chat's user pushback classifier, Appendix E.2.4 of arXiv 2604.20779 (v1),
# transcribed from the PDF: its extraction spaced apostrophes and hyphens
# ("agent ' s", "mid - task"), put back here as the prompt wrote them. Its last
# two lines, the context and the prompt, are the message `classify` sends.
CODEBOOK = """\
You are a classifier that determines whether a user prompt in a coding agent session represents pushback \
against the agent's preceding action, and if so, what kind.

Pushback is any prompt where the user resists, corrects, redirects, or takes over from the agent - rather \
than simply continuing the workflow. Use the preceding conversation context to understand what the agent just did.

Classify the prompt into exactly one of the following categories:

- correction - The user redirects the agent by providing missing context, correcting a misunderstanding, \
pointing out factual errors, or changing requirements/direction/scope mid-task.
  Examples: "I said X not Y", "you changed the wrong file", "actually the API uses POST not GET", \
"actually, let's do X instead", "forget that approach, try Y", "on second thought, skip the tests"

- rejection - The user explicitly rejects, reverts, or refuses the agent's output without providing a \
specific correction.
  Examples: "undo that", "revert the last change", "no", "that's wrong", "I don't want that", \
"put it back the way it was"

- failure_report - The user reports that the agent's output does not work: bugs, errors, test failures, \
or broken behavior.
  Examples: "this still doesn't work", "it's still crashing", "same error, try again", \
"the tests are failing", "I get a 404 now"

- non_pushback - The prompt moves the session forward normally: a new task, building on agent output, \
asking a question, or routine iteration.
  Examples: "now add a login page", "good, also add unit tests", "why did you use a list here?", \
"change the button color to blue"

Disambiguation:
- correction vs rejection: correction provides a specific fix, missing information, or new direction; \
rejection just says "no" or "undo" without explaining what was wrong.
- failure_report vs rejection: failure_report = "it doesn't work" (something is broken); \
rejection = "I don't want that" (output is unwanted even if functional).

When uncertain:
- If the prompt contains words like "undo", "revert", "wrong", "broken", "doesn't work", "I said", \
"you missed", or "never mind", lean toward a pushback category.
- If the prompt reads like a standalone next step with no negative reaction, lean toward non_pushback.

Respond in valid JSON only:
{
"label": "<one of: correction, rejection, failure_report, non_pushback>",
"reason": "<1-2 sentence explanation>"
}"""


class Pushback(BaseModel):
    """One developer message, classified under SWE-chat's codebook."""

    label: Literal["correction", "rejection", "failure_report", "non_pushback"] = Field(
        description="One of: correction, rejection, failure_report, non_pushback.")
    reason: str = Field(description="1-2 sentence explanation.")


def digest(text: str | None) -> str:
    """A message's identity on its row: the label is kept only while the text is the text labelled."""
    return hashlib.sha256((text or "").encode("utf-8", "surrogatepass")).hexdigest()[:16]


def to_label(turns: list[dict]) -> list[dict]:
    """A session's developer messages that are labelled: SWE-chat's non-interruption prompts.

    Not an interruption, which SWE-chat left unlabelled, not the summary that
    opens a continued session, and not an empty message. A turn the table
    holds twice is labelled once: SWE-chat's has 39 developer rows that repeat
    another's session, turn and text, in 13 sessions.
    """
    out, seen = [], set()
    for t in turns:
        text = (t.get("content") or "").strip()
        if (t.get("turn_type") == "user_prompt" and t.get("turn_number") is not None and text
                and not text.startswith(INTERRUPTED) and not text.startswith(CONTINUED)
                and t["turn_number"] not in seen):
            seen.add(t["turn_number"])
            out.append(t)
    return out


def context_for(turns: list[dict], turn_number: float) -> str:
    """What triage reads of the conversation before a message: the tail of the pipeline's own rendering.

    Every turn before the message, put-back ones included (their numbers are
    fractional), and nothing from the message on.
    """
    from ..corpus.turns import build_excerpt

    before = [t for t in turns if t.get("turn_number") is not None and t["turn_number"] < turn_number]
    return build_excerpt(before, turn_number)[-CONTEXT_CHARS:]


def message_for(turn: dict) -> str:
    """The message as the rendering shows it to triage: cut where a developer message is cut."""
    from ..corpus.turns import MESSAGE_CHARS

    text = (turn.get("content") or "").strip()
    return text if len(text) <= MESSAGE_CHARS else text[:MESSAGE_CHARS] + " [...]"


def prompt_for(context: str, message: str) -> str:
    """The message a model is sent: the codebook's own last two parts."""
    return f"Preceding conversation context:\n{context}\n\nUser prompt to classify:\n{message}"


async def classify(context: str, message: str, *, model: str) -> tuple[Pushback, dict | None]:
    """One message's label under the codebook, and the call's token use."""
    from agents import Agent, Runner

    from ..llm import configure_client, resilient, usage_of, with_field_guide

    configure_client()
    agent = Agent(name="pushback", instructions=with_field_guide(CODEBOOK, Pushback), model=model,
                  output_type=Pushback)
    result = await resilient(lambda: Runner.run(agent, prompt_for(context, message), max_turns=2))
    return result.final_output, usage_of(result)


async def label_turns(turns_by_session: dict[str, list[dict]], out: Path, *, model: str, concurrency: int = 4,
                      limit: int = 0, only: set | None = None, extra: dict | None = None, ask=None,
                      log=print) -> Counter:
    """Label every message of these sessions not already labelled in `out`; the labels given.

    `only` restricts it to those (session, turn) keys, and `extra` adds fields
    to each row by key (calibration's reference label). `ask` is the call that
    labels one message, `classify` unless another is given. Resumable: a
    message with a row that did not error is not asked again.
    """
    ask = ask or classify
    from ..llm import ClaudeRefused
    from ..project import code_version
    from ..store.rows import append, completed

    done = {(r["session_id"], r["turn_number"]) for r in completed(out)} if out.exists() else set()
    todo = [(sid, t) for sid, turns in sorted(turns_by_session.items()) for t in to_label(turns)
            if (sid, t["turn_number"]) not in done and (only is None or (sid, t["turn_number"]) in only)]
    if limit:
        todo = todo[:limit]
    log(f"{len(todo)} messages to label ({len(done)} already labelled)")
    version, gate, counts = code_version(), asyncio.Semaphore(concurrency), Counter()

    async def one(sid: str, turn: dict) -> None:
        async with gate:
            row = {"session_id": sid, "turn_number": turn["turn_number"], "digest": digest(turn.get("content")),
                   "model": model, "code_version": version, **((extra or {}).get((sid, turn["turn_number"])) or {})}
            try:
                verdict, usage = await ask(context_for(turns_by_session[sid], turn["turn_number"]),
                                           message_for(turn), model=model)
                row.update(label=verdict.label, why=verdict.reason, usage=usage)
            except ClaudeRefused:
                raise
            except Exception as e:  # recorded, and asked again on the next run
                row["error"] = f"{type(e).__name__}: {str(e)[-300:]}"
            append(out, row)
            counts[row.get("label") or "error"] += 1
            if sum(counts.values()) % 200 == 0:
                log(f"  {sum(counts.values())}/{len(todo)}: {dict(counts)}")

    await asyncio.gather(*(one(s, t) for s, t in todo))
    return counts


def estimate(pairs: list[tuple[str, str]], *, price_in: float, price_out: float,
             out_tokens: int = 90, chars_per_token: float = 3.5) -> dict:
    """What labelling these (context, message) pairs costs, before any call: an upper bound, no cache."""
    from ..llm import with_field_guide

    fixed = len(with_field_guide(CODEBOOK, Pushback))
    chars = sum(fixed + len(prompt_for(c, m)) for c, m in pairs)
    tokens_in = chars / chars_per_token
    cost = tokens_in / 1e6 * price_in + len(pairs) * out_tokens / 1e6 * price_out
    return {"messages": len(pairs), "input_tokens": round(tokens_in), "output_tokens": len(pairs) * out_tokens,
            "usd": round(cost, 2)}


CLAUDE_5 = r"^claude-(opus|sonnet|fable|haiku)-5(?![0-9])"


def commonest_models(corpus: Path, session_ids: set[str] | None = None) -> dict[str, str]:
    """Each session's commonest model among its agent's rows, as the transcript records it."""
    import pyarrow.parquet as pq

    seen: dict[str, Counter] = {}
    for batch in pq.ParquetFile(corpus / "conversations.parquet").iter_batches(
            batch_size=200_000, columns=["session_id", "role", "model"]):
        d = batch.to_pydict()
        for sid, role, model in zip(d["session_id"], d["role"], d["model"]):
            if role in ("assistant", "tool_use") and model and not model.startswith("<") and (
                    session_ids is None or sid in session_ids):
                seen.setdefault(sid, Counter())[model] += 1
    return {sid: c.most_common(1)[0][0] for sid, c in seen.items()}


def entire_sessions(corpus: Path, *, since: str, exclude: set[str], model_pattern: str | None = CLAUDE_5
                    ) -> dict[str, str]:
    """The collected corpus's sessions to label, each with its commonest model.

    Created on or after `since` and not in `exclude` (SWE-chat's own sessions),
    in a repository whose language the benchmark can sandbox, and, when a
    pattern is given, whose commonest model matches it.
    """
    import json
    import re

    import pyarrow.parquet as pq

    from ..construct.container import can_be_sandboxed

    language = {r["repo_id"]: json.loads(r["repo_github_metadata"] or "{}").get("language") for r in
                pq.read_table(corpus / "repositories.parquet", columns=["repo_id", "repo_github_metadata"]).to_pylist()}
    chosen = set()
    for r in pq.read_table(corpus / "sessions.parquet", columns=["session_id", "repo_id", "created_at"]).to_pylist():
        if (r["created_at"] and r["created_at"].strftime("%Y-%m-%d") >= since and r["session_id"] not in exclude
                and can_be_sandboxed(language.get(r["repo_id"]))):
            chosen.add(r["session_id"])
    models = commonest_models(corpus, chosen)
    wanted = re.compile(model_pattern) if model_pattern else None
    return {s: models.get(s, "") for s in sorted(chosen) if wanted is None or wanted.search(models.get(s, ""))}


REFERENCE = ("non_pushback", "correction", "failure_report", "rejection", "takeover")


def calibration_sample(corpus: Path, per_class: dict[str, int], seed: int = 0,
                       also: list[tuple[str, float]] = ()) -> tuple[dict[tuple[str, float], dict], Counter]:
    """SWE-chat's own messages to label, by SWE-chat's label; and how often each label occurs.

    A fixed number of messages per label, drawn with `seed` from those
    `to_label` would label, and `also` (the moments v1's tasks were built
    from), each with SWE-chat's label beside it.
    """
    import random

    import pyarrow.parquet as pq

    import pyarrow.compute as pc

    by_label: dict[str, list[tuple[str, float]]] = {k: [] for k in REFERENCE}
    label_of: dict[tuple[str, float], str] = {}
    wanted_also, seen = set(also), set()
    for batch in pq.ParquetFile(corpus / "conversations.parquet").iter_batches(
            batch_size=200_000, columns=["session_id", "turn_number", "turn_type", "content", "prompt_pushback"]):
        # Only developer messages are made into Python objects: tool results
        # run to 10 KB each, and the whole table is 1.3 GB.
        d = batch.filter(pc.equal(batch.column("turn_type"), "user_prompt")).to_pydict()
        for sid, n, kind, text, label in zip(d["session_id"], d["turn_number"], d["turn_type"], d["content"],
                                             d["prompt_pushback"]):
            if (sid, n) in seen or not to_label([{"turn_type": kind, "turn_number": n, "content": text}]):
                continue
            seen.add((sid, n))
            if (sid, n) in wanted_also:
                label_of[(sid, n)] = label
            if label in by_label:
                by_label[label].append((sid, n))
    frequency = Counter({k: len(v) for k, v in by_label.items()})
    rng = random.Random(seed)
    picked: dict[tuple[str, float], dict] = {}
    for label in REFERENCE:
        pool = sorted(by_label[label])
        for key in rng.sample(pool, min(per_class.get(label, 0), len(pool))):
            picked[key] = {"swechat": label, "source": "sample"}
    for key in also:
        picked[key] = {"swechat": label_of.get(key), "source": "v1 task"}
    return picked, frequency


def calibration_report(rows: list[dict], frequency: Counter) -> dict:
    """How far the labels agree with SWE-chat's, on SWE-chat's own messages.

    For each of SWE-chat's labels, the share of its sampled messages labelled a
    pushback here: for a first filter, what matters most is that SWE-chat's
    pushbacks come out as pushbacks. Agreement on pushback or not is also given
    reweighted to how often each of SWE-chat's labels occurs, since the sample
    draws the classes in fixed numbers. The moments v1's tasks were built from
    are counted apart.
    """
    errors = sum(1 for r in rows if r.get("error"))
    rows = [r for r in rows if r.get("label") and not r.get("error")]
    sample = [r for r in rows if r.get("source") == "sample"]
    by_ref = {ref: Counter(r["label"] for r in sample if r.get("swechat") == ref) for ref in REFERENCE}
    called = {ref: sum(v for k, v in c.items() if k in PUSHBACK) / sum(c.values()) if sum(c.values()) else None
              for ref, c in by_ref.items()}
    agrees = {ref: None if called[ref] is None else (1 - called[ref] if ref == "non_pushback" else called[ref])
              for ref in REFERENCE}
    weight = sum(frequency[ref] for ref in REFERENCE if agrees[ref] is not None)
    binary = sum(frequency[ref] * agrees[ref] for ref in REFERENCE if agrees[ref] is not None) / weight if weight else None
    four = [r for r in sample if r.get("swechat") in LABELS]
    tasks = [r for r in rows if r.get("source") == "v1 task"]
    return {"labelled": len(rows), "errors": errors, "by_swechat_label": {k: dict(v) for k, v in by_ref.items()},
            "called_pushback": called, "binary_agreement_reweighted": binary,
            "four_class_agreement": sum(r["label"] == r["swechat"] for r in four) / len(four) if four else None,
            "v1_task_moments_called_pushback": [sum(r["label"] in PUSHBACK for r in tasks), len(tasks)]}
