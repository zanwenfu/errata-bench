"""The turns of one session, and the excerpt a candidate is shown.

Corpus handling rather than reading: what a session contains, in order, and how
much of it fits in a prompt. Eight modules import `load_session_turns` and
three `build_excerpt`, none of them to read a pushback.
"""

from __future__ import annotations

from pathlib import Path


TURN_COLUMNS = [
    "session_id",
    "turn_number",
    "role",
    "turn_type",
    "content",
    "tool_name",
    "command",
    "file_path",
    "prompt_pushback",
    "is_conversational",
]


def load_session_turns(session_ids: set[str]) -> dict[str, list[dict]]:
    """Pull every turn for the given sessions in one pass over the parquet.

    conversations.parquet is 1.3 GB and 2.7M rows, so it is streamed in batches
    and scanned once for all requested sessions rather than once per session.
    """
    import pyarrow.parquet as pq

    from .sessions import CORPUS

    out: dict[str, list[dict]] = {s: [] for s in session_ids}
    pf = pq.ParquetFile(CORPUS / "conversations.parquet")
    for batch in pf.iter_batches(batch_size=200_000, columns=TURN_COLUMNS):
        sids = batch.column("session_id").to_pylist()
        hits = [i for i, s in enumerate(sids) if s in session_ids]
        if not hits:
            continue
        cols = {c: batch.column(c).to_pylist() for c in TURN_COLUMNS}
        for i in hits:
            out[sids[i]].append({c: cols[c][i] for c in TURN_COLUMNS})
    for s in out:
        out[s].sort(key=lambda t: t["turn_number"] or 0)
    return out



# How much of one developer or agent message the candidate is shown. Named
# because two gates decide things about that message and must read the same
# amount: the answerable gate read 8,000 characters of a message the candidate
# saw 4,000 of, so a request sitting in the second half made a task
# "answerable" by a question its candidate was never shown (G-56).
MESSAGE_CHARS = 4000


def _fit_result_budget(turns: list[dict], cut_turn: int, max_chars: int) -> int:
    """How many characters each tool result may keep, given the space available.

    Early moments are where this matters. A flat 400-character cap showed only
    8-29% of tool result bodies -- 93k characters cut to 8.5k in one case -- and
    the reader said so directly: "the edit payloads and relevant code bodies are
    absent or truncated". Meanwhile the prompts themselves were tiny, 21 of 40
    under a fifth of the budget. The cap was starving the reader while most of
    the room went unused.

    So the budget is fitted rather than fixed: measure what the conversation
    costs, then spend what is left on tool output. A short session gets generous
    results, a long one falls back to the tight cap that keeps it in bounds.
    """
    fixed = 0
    results = []
    for t in turns:
        n = t.get("turn_number")
        if n is None or n > cut_turn:
            continue
        kind = t.get("turn_type") or ""
        content = (t.get("content") or "").strip()
        if kind in ("user_prompt", "assistant_response"):
            fixed += min(len(content), MESSAGE_CHARS) + 40
        elif kind == "assistant_thinking":
            fixed += min(len(content), 1500) + 40
        elif kind == "tool_use":
            detail = t.get("command") or t.get("file_path") or content[:150]
            fixed += min(len(str(detail)), 220) + 40
        elif kind == "tool_result" and content:
            results.append(len(content))

    if not results:
        return 400
    remaining = max_chars - fixed - 40 * len(results)
    if remaining <= 0:
        return 400
    # Spread what is left evenly, then clamp: never below the old cap, and not
    # so high that one enormous result swamps the rest.
    return max(400, min(4000, remaining // len(results)))


def build_excerpt(
    turns: list[dict],
    cut_turn: int,
    *,
    max_chars: int = 60_000,
    mark_pushback: bool = False,
) -> str:
    """Render the turns leading up to a pushback into something readable.

    Sessions run to ~1,750 turns but only ~40 are conversational; the rest is
    tool traffic. Tool calls still matter -- they are the difference between an
    agent that checked and one that asserted -- so they are kept in compressed
    form, while progress and file-snapshot noise is dropped.
    """
    result_budget = _fit_result_budget(turns, cut_turn, max_chars)
    lines: list[str] = []
    for t in turns:
        n = t.get("turn_number")
        if n is None or n > cut_turn:
            continue
        kind = t.get("turn_type") or ""
        if kind in ("progress", "file_snapshot", "system_event", "queue_operation"):
            continue
        content = (t.get("content") or "").strip()
        if not content and kind not in ("tool_use",):
            continue

        if kind == "user_prompt":
            marker = " <-- THE PUSHBACK" if (mark_pushback and n == cut_turn) else ""
            lines.append(f"\n[turn {n}] USER{marker}:\n{content[:MESSAGE_CHARS]}")
        elif kind == "assistant_response":
            lines.append(f"\n[turn {n}] AGENT:\n{content[:MESSAGE_CHARS]}")
        elif kind == "assistant_thinking":
            lines.append(f"\n[turn {n}] AGENT (thinking):\n{content[:1500]}")
        elif kind == "tool_use":
            tool = t.get("tool_name") or "?"
            detail = t.get("command") or t.get("file_path") or content[:200]
            lines.append(f"[turn {n}] AGENT calls {tool}: {str(detail)[:220]}")
        elif kind == "tool_result":
            lines.append(f"[turn {n}] -> result: {content[:result_budget]}")

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    # Overrun: squeeze tool traffic rather than cutting the conversation. An
    # earlier version elided the middle, which cost 13 of 25 readings part of
    # their context -- including 3 of the 6 that produced a usable case. Tool
    # results are the bulk (59% of a large excerpt) while the user/agent
    # narrative is under a third, so tightening the former is enough.
    for tool_budget, result_budget in ((200, 200), (100, 80), (60, 40)):
        squeezed: list[str] = []
        for t in turns:
            n = t.get("turn_number")
            if n is None or n > cut_turn:
                continue
            kind = t.get("turn_type") or ""
            if kind in ("progress", "file_snapshot", "system_event", "queue_operation"):
                continue
            content = (t.get("content") or "").strip()
            if kind == "user_prompt":
                marker = " <-- THE PUSHBACK" if (mark_pushback and n == cut_turn) else ""
                squeezed.append(f"\n[turn {n}] USER{marker}:\n{content[:MESSAGE_CHARS]}")
            elif kind == "assistant_response":
                squeezed.append(f"\n[turn {n}] AGENT:\n{content[:MESSAGE_CHARS]}")
            elif kind == "assistant_thinking":
                squeezed.append(f"\n[turn {n}] AGENT (thinking):\n{content[:800]}")
            elif kind == "tool_use":
                tool = t.get("tool_name") or "?"
                detail = t.get("command") or t.get("file_path") or content[:150]
                squeezed.append(f"[turn {n}] AGENT calls {tool}: {str(detail)[:tool_budget]}")
            elif kind == "tool_result" and content:
                # A result announcing a background task carries its id and
                # output path, and both are load-bearing: without them a model
                # cannot discover the artifact that establishes what the task
                # did. These lines are short, so keeping them whole costs
                # almost nothing while squeezing them makes a task unpassable.
                budget = (
                    400
                    if "running in background with ID" in content
                    else result_budget
                )
                squeezed.append(f"[turn {n}] -> result: {content[:budget]}")
        text = "\n".join(squeezed)
        if len(text) <= max_chars:
            break
    return text

