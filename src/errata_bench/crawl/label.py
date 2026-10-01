"""Which developer messages push back, labelled as SWE-chat labelled its own; our own triage then decides (#16, step 7).

The pipeline takes its moments from the corpus's `prompt_pushback` label
(`run.py`, `PUSHBACK_KINDS`). SWE-chat's came from a model, Qwen3.5-9B, reading
each developer message with the conversation before it, under the codebook
printed in its paper's Appendix E.2.4 (arXiv 2604.20779). The pipeline's first
stage, triage (`find.triage`), then re-read every moment those labels flagged
and kept the ones where the developer objects to work the agent had done. It
kept two in five. Entire's sessions come with no label, so the collected
corpus holds no moment until they are labelled.

Here that codebook, word for word (`CODEBOOK`), is asked of a model about the
view triage reads of the same moment: the last `CONTEXT_CHARS` of the
pipeline's rendering of the conversation before the message, and the message
as the rendering shows it. The label is a first flag, as SWE-chat's was, and
triage still reads every moment it passes, so what matters most is that it
misses few pushbacks.

Checked on 10-01 against triage's own verdicts on SWE-chat's moments (`scripts/
crawl_label.py calibrate --against triage`): the codebook caught 95.2% of
triage's pushbacks, and flagged 41% of what triage turned away. Triage's own
question, asked in its place, caught 84.6% and was set aside (the user's
choice, 10-01). SWE-chat's codebook has three pushback classes and
non_pushback. Its corpus also carries `takeover`, which the final codebook
does not have, and nothing here produces it.

One row per labelled message, in ``labels.jsonl``, keyed by session and turn
and carrying the message's digest; `corpus.assemble` puts a label on its row
only while the digest still matches. A row that errored is asked again. The
model's explanation is stored as ``why``: the row store takes "error:" in a
row's ``reason`` for a failure, and a pushback's explanation often says it.
A file holds one model's labels at one reasoning effort to one question, each
recorded on every row.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

LABELS = ("correction", "rejection", "failure_report", "non_pushback")
PUSHBACK = frozenset(LABELS) - {"non_pushback"}
# What a row's label answers: SWE-chat's codebook.
QUESTION = "codebook"
# What triage reads of the same moment (`find.triage`: the excerpt's last 9,000
# characters), and of the message itself (`corpus.turns.MESSAGE_CHARS`).
CONTEXT_CHARS = 9000
INTERRUPTED = "[Request interrupted by user"
CONTINUED = "This session is being continued"
# A provider's content filter refusing the request, as `release.reference_agent`
# reads one: asking again gets the same refusal.
FILTERED = re.compile(r"content_filter|ResponsibleAIPolicyViolation|content management policy", re.IGNORECASE)

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


# Claude Code's own notices and command output, written into the developer's
# turn. In SWE-chat each of the 2,824 messages opening with a task notification
# is the notice and one line of Claude Code's after it, and the 169 opening with
# a command's output are that output. None holds a word anyone typed, and
# SWE-chat's labeller called 22% of the notices pushback. Not labelled since
# 10-01, the first calibration having counted them as SWE-chat's failure reports.
OWN = ("task-notification", "bash-stdout", "bash-stderr", "local-command-stdout", "local-command-stderr")
OWN_LINE = ("Read the output file to retrieve the result:", "Full transcript available at:")
# A block, and the one line Claude Code writes after a notice.
_OWN_BLOCK = re.compile(r"<(%s)>.*?(?:</\1>|\Z)(?:\s*(?:%s)[^\n]*)?"
                        % ("|".join(OWN), "|".join(map(re.escape, OWN_LINE))), re.DOTALL)


def written_by_claude_code(text: str) -> bool:
    """Whether a message is only Claude Code's own: its notice or output blocks, each with its one line after.

    A message that opens with one but holds anything else, typed after it or
    between two, is the developer's and is labelled.
    """
    text = (text or "").strip()
    return text.startswith(tuple(f"<{tag}>" for tag in OWN)) and not _OWN_BLOCK.sub("", text).strip()


# What Claude Code delivers into the developer's turn from elsewhere: a message
# another Claude session sent, a skill's instructions as it loads, and a hook's
# feedback when the agent stops. Nobody typed them here. In the 10-01 Entire
# pilot 19 of 286 messages were the first two, and the codebook called 11 of
# them pushback; the Claude 5 sessions hold 36 of the third.
DELIVERED = ("<cross-session-message", "Another Claude session sent a message:", "Base directory for this skill:",
             "Stop hook feedback:")


def to_label(turns: list[dict]) -> list[dict]:
    """A session's developer messages that are labelled: SWE-chat's non-interruption prompts, less what no one typed.

    Not an interruption, which SWE-chat left unlabelled, not the summary that
    opens a continued session, not Claude Code's own notice or output
    (`written_by_claude_code`), not what it delivers from elsewhere
    (`DELIVERED`), and not an empty message. A turn the table holds twice is
    labelled once: SWE-chat's has 39 developer rows that repeat another's
    session, turn and text, in 13 sessions.
    """
    out, seen = [], set()
    for t in turns:
        text = (t.get("content") or "").strip()
        if (t.get("turn_type") == "user_prompt" and t.get("turn_number") is not None and text
                and not text.startswith(INTERRUPTED) and not text.startswith(CONTINUED)
                and not written_by_claude_code(text) and not text.startswith(DELIVERED)
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


async def classify(context: str, message: str, *, model: str, effort: str | None = None
                   ) -> tuple[Pushback, dict | None]:
    """One message's label under the codebook, and the call's token use; `effort` is the reasoning effort asked."""
    from agents import Agent, ModelSettings, Runner
    from openai.types.shared import Reasoning

    from ..llm import configure_client, resilient, usage_of, with_field_guide

    configure_client()
    agent = Agent(name="pushback", instructions=with_field_guide(CODEBOOK, Pushback), model=model,
                  output_type=Pushback,
                  model_settings=ModelSettings(reasoning=Reasoning(effort=effort)) if effort else ModelSettings())
    result = await resilient(lambda: Runner.run(agent, prompt_for(context, message), max_turns=2))
    return result.final_output, usage_of(result)


async def label_turns(turns_by_session: dict[str, list[dict]], out: Path, *, model: str, effort: str | None = None,
                      concurrency: int = 4, limit: int = 0, only: set | None = None, extra: dict | None = None,
                      ask=None, spend=None, max_usd: float = 0.0, log=print) -> Counter:
    """Label every message of these sessions not already labelled in `out`; the labels given.

    `only` restricts it to those (session, turn) keys, and `extra` adds fields
    to each row by key (calibration's reference label). `ask` is the call that
    labels one message, `classify` unless another is given. Resumable: a
    message with a row that did not error is not asked again.

    One file holds one model's labels at one effort to one question: resumed
    under another, a run would take the first one's labels for its own. With
    `spend`, which prices a call's usage, no message is asked once the calls
    this run made reach `max_usd`; a later run asks the rest. A request the
    provider's content filter refuses is recorded as ``filtered``, with no
    label, and not asked again.
    """
    ask = ask or classify
    from ..llm import ClaudeRefused
    from ..project import code_version
    from ..store.rows import append, completed, load

    if out.exists():
        other = {(r.get("model"), r.get("effort"), r.get("question")) for r in load(out)} - {(model, effort, QUESTION)}
        if other:
            raise ValueError(f"{out} holds labels by {sorted(other, key=str)}, not by {model} at effort {effort} "
                             f"to the {QUESTION} question: one file holds one model's labels")
    done = {(r["session_id"], r["turn_number"]) for r in completed(out)} if out.exists() else set()
    todo = [(sid, t) for sid, turns in sorted(turns_by_session.items()) for t in to_label(turns)
            if (sid, t["turn_number"]) not in done and (only is None or (sid, t["turn_number"]) in only)]
    if limit:
        todo = todo[:limit]
    log(f"{len(todo)} messages to label ({len(done)} already labelled)")
    version, gate, counts = code_version(), asyncio.Semaphore(concurrency), Counter()
    spent = 0.0

    async def one(sid: str, turn: dict) -> None:
        nonlocal spent
        async with gate:
            if spend is not None and spent >= max_usd:
                counts["not asked: the spend cap was reached"] += 1
                return
            row = {"session_id": sid, "turn_number": turn["turn_number"], "digest": digest(turn.get("content")),
                   "model": model, "effort": effort, "question": QUESTION, "code_version": version,
                   **((extra or {}).get((sid, turn["turn_number"])) or {})}
            try:
                verdict, usage = await ask(context_for(turns_by_session[sid], turn["turn_number"]),
                                           message_for(turn), model=model, effort=effort)
                row.update(label=verdict.label, why=verdict.reason, usage=usage)
                if spend is not None:
                    spent += spend(usage)
            except ClaudeRefused:
                raise
            except Exception as e:  # recorded, and asked again on the next run unless filtered
                if FILTERED.search(str(e)):
                    row["filtered"] = f"{type(e).__name__}: {str(e)[-300:]}"
                else:
                    row["error"] = f"{type(e).__name__}: {str(e)[-300:]}"
            append(out, row)
            counts[row.get("label") or ("filtered" if "filtered" in row else "error")] += 1
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
# The calibration's pass rules, registered in the research log (10-01) before
# any model was asked. SWE-chat's labels are about 79% right on pushback or not
# (the paper's validation), so agreement with them is bounded below 100%:
# a labeller right 90% of the time agrees about 73% of the time.
PASS = {"v1 task moments called pushback, at least": 50, "SWE-chat failure reports called pushback, at least": 0.85,
        "agreement on pushback or not, reweighted, at least": 0.70}


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


def triage_key(triaged: list[Path]) -> dict[tuple[str, float], str]:
    """Our own triage's verdict on each moment it read, the answer key since 10-01 (the user's choice).

    Triage (`find.triage`, gpt-6-astra) read moments SWE-chat flagged. Each
    verdict is "pushback" when the agent had acted and the developer objects
    to that work, or "not pushback" when the agent had acted and they do not.
    Left out: errored rows, moments where the agent had not acted, and a
    moment two runs judged differently.
    """
    from ..store.rows import load

    seen: dict[tuple[str, float], set[str]] = {}
    for path in triaged:
        for r in load(path):
            if r.get("error") or "objects_to_that_work" not in r or not r.get("agent_has_acted"):
                continue
            verdict = "pushback" if r["objects_to_that_work"] else "not pushback"
            seen.setdefault((r["session_id"], r["turn_number"]), set()).add(verdict)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def triage_sample(key: dict[tuple[str, float], str], counts: dict[str, int], seed: int = 0,
                  also: list[tuple[str, float]] = ()) -> dict[tuple[str, float], dict]:
    """A fixed number of moments per triage verdict, drawn with `seed`, and `also` (v1's task moments)."""
    import random

    rng, picked = random.Random(seed), {}
    for verdict, n in counts.items():
        pool = sorted(k for k, v in key.items() if v == verdict)
        for k in rng.sample(pool, min(n, len(pool))):
            picked[k] = {"triage": verdict, "source": "sample"}
    for k in also:
        picked[k] = {"triage": key.get(k), "source": "v1 task"}
    return picked


# The triage check's pass rules, registered in the research log (10-01) before any model was asked.
TRIAGE_PASS = {"triage pushbacks called pushback, at least": 0.90, "v1 task moments called pushback, at least": 50}


def triage_report(rows: list[dict], picked: dict[tuple[str, float], dict]) -> dict:
    """How many of triage's pushbacks the labels catch, and how many of its non-pushbacks they flag.

    A sampled moment with no row was not labelled: `to_label` skipped it, as
    the full run would, so a triage pushback without one counts as missed.
    """
    answered = {(r["session_id"], r["turn_number"]): r for r in rows if not r.get("error")}
    errors = sum(1 for r in rows if r.get("error"))
    out: dict = {"errors": errors}
    for verdict in ("pushback", "not pushback"):
        keys = [k for k, v in picked.items() if v["source"] == "sample" and v["triage"] == verdict]
        called = sum(1 for k in keys if answered.get(k, {}).get("label") in PUSHBACK)
        out[f"triage {verdict}"] = {"moments": len(keys), "not labelled": sum(1 for k in keys if k not in answered),
                                    "filtered": sum(1 for k in keys if answered.get(k, {}).get("filtered")),
                                    "called pushback": called, "share": called / len(keys) if keys else None}
    v1 = [k for k, v in picked.items() if v["source"] == "v1 task"]
    v1_called = sum(1 for k in v1 if answered.get(k, {}).get("label") in PUSHBACK)
    recall = out["triage pushback"]["share"]
    rule_recall, rule_v1 = TRIAGE_PASS.values()
    passes = {"no row failed": errors == 0, "pushbacks caught": recall is not None and recall >= rule_recall,
              "v1 task moments": v1_called >= rule_v1}
    return {**out, "v1_task_moments_called_pushback": [v1_called, len(v1)], "rules": TRIAGE_PASS,
            "passes": passes, "passed": all(passes.values())}


def calibration_report(rows: list[dict], frequency: Counter, expected: int | None = None) -> dict:
    """How far the labels agree with SWE-chat's, on SWE-chat's own messages, and whether that passes `PASS`.

    For each of SWE-chat's labels, the share of its sampled messages labelled a
    pushback here: for a first filter, what matters most is that SWE-chat's
    pushbacks come out as pushbacks. Agreement on pushback or not is also given
    reweighted to how often each of SWE-chat's labels occurs, since the sample
    draws the classes in fixed numbers. The moments v1's tasks were built from
    are counted apart; one the content filter refused counts as not called.
    A pass also needs no failed row and, given `expected`, every message answered.
    """
    errors = sum(1 for r in rows if r.get("error"))
    filtered = sum(1 for r in rows if r.get("filtered") and not r.get("error"))
    v1_called = sum(1 for r in rows if r.get("source") == "v1 task" and r.get("label") in PUSHBACK
                    and not r.get("error"))
    v1_answered = sum(1 for r in rows if r.get("source") == "v1 task" and not r.get("error"))
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
    rule_v1, rule_failure, rule_agree = PASS.values()
    passes = {"no row failed": errors == 0,
              "every message answered": expected is None or len(rows) + filtered == expected,
              "v1 task moments": v1_called >= rule_v1,
              "failure reports": called["failure_report"] is not None and called["failure_report"] >= rule_failure,
              "agreement": binary is not None and binary >= rule_agree}
    return {"labelled": len(rows), "filtered": filtered, "errors": errors, "expected": expected,
            "by_swechat_label": {k: dict(v) for k, v in by_ref.items()},
            "called_pushback": called, "binary_agreement_reweighted": binary,
            "four_class_agreement": sum(r["label"] == r["swechat"] for r in four) / len(four) if four else None,
            "v1_task_moments_called_pushback": [v1_called, v1_answered],
            "rules": PASS, "passes": passes, "passed": all(passes.values())}
