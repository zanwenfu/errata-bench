"""The turns of one session, and the excerpt a candidate is shown.

Corpus handling rather than reading: what a session contains, in order, and how
much of it fits in a prompt. Eight modules import `load_session_turns` and
three `build_excerpt`, none of them to read a pushback.
"""

from __future__ import annotations

import json
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
    # Which call a result answers. Parallel calls return their results after
    # all of the calls, so pairing by position gave one file's read the
    # content of another (D-36 A5: a Python file shown with React code).
    "tool_call_id",
]


def load_session_turns(session_ids: set[str]) -> dict[str, list[dict]]:
    """Pull every turn for the given sessions in one pass over the parquet.

    conversations.parquet is 1.3 GB and 2.7M rows, so it is streamed in batches
    and scanned once for all requested sessions rather than once per session.
    Only the requested sessions' rows are made into Python objects. Converting
    every column of each 200,000-row batch that held any of them peaked at 5 to
    6 GB for a few hundred sessions, and the next batches ask for 1,700 (B-251).
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from .sessions import CORPUS

    out: dict[str, list[dict]] = {s: [] for s in session_ids}
    pf = pq.ParquetFile(CORPUS / "conversations.parquet")
    wanted = None
    for batch in pf.iter_batches(batch_size=200_000, columns=TURN_COLUMNS):
        ids = batch.column("session_id")
        if wanted is None:
            wanted = pa.array(sorted(session_ids), type=ids.type)
        mask = pc.is_in(ids, value_set=wanted)
        if not pc.any(mask).as_py():
            continue
        for row in batch.filter(mask).to_pylist():
            out[row["session_id"]].append({c: row[c] for c in TURN_COLUMNS})
    for s in out:
        out[s].sort(key=lambda t: t["turn_number"] or 0)
    return out



# How much of one developer or agent message the candidate is shown. Named
# because two gates decide things about that message and must read the same
# amount: the answerable gate read 8,000 characters of a message the candidate
# saw 4,000 of, so a request sitting in the second half made a task
# "answerable" by a question its candidate was never shown (G-56).
MESSAGE_CHARS = 4000


# The most of one tool result the corpus keeps, near enough: longer results
# were cut to about 10 KB when conversations.parquet was built.
FILL_CAP = 10_000


# The record the candidate and the checker read (D-41.1). Record 1 is what every
# candidate saw up to D-40: a call shown by its command or its file alone, and
# messages, thinking and results cut with no mark. D-40's reading of 118 flags
# found about a seventh undecidable for exactly that -- "AGENT calls Edit:
# MessageBubble.tsx" with nothing of the edit, a Grep with no pattern, a read cut
# at line 56 with nothing to say so -- and the candidate, continuing the agent's
# work, could not see what its own earlier edits had done either. Record 2 shows
# each call with what it was given and marks every cut, with how much.
RECORD = 2
# The most of one call's input record 2 shows; an edit's old and new text share it.
CALL_CHARS = 1200
# How long a record-2 conversation may be. Showing edits takes room the results
# had: at 60,000 characters, 31 of D-40's 55 tasks would have shown less of each
# result than record 1 did. At 75,000, 5 do, the median result keeps its full
# 4,000, and the 55 conversations are 28% longer in all (measured 09-25). The
# checker and the judge read the conversation up to `trace.CONTEXT_CHARS`, which
# is kept at least this long.
RECORD_CHARS = 75_000
# The most edits of one MultiEdit shown, each with its share of CALL_CHARS.
MULTI_EDITS = 6


def _cut(text: str, limit: int, record: int) -> str:
    """At most `limit` characters of `text`: record 1 cuts it silently, record 2 says how much went."""
    if len(text) <= limit or record < 2:
        return text[:limit]
    return f"{text[:limit]} [{len(text) - limit:,} more characters not shown]"


def _given(t: dict) -> dict:
    """What a call was given: its input's JSON, which the corpus keeps in `content` for a call, as a
    recovered call does (G-76)."""
    try:
        given = json.loads(t.get("content") or "")
    except (TypeError, ValueError):
        return {}
    return given if isinstance(given, dict) else {}


def _block(text, limit: int) -> str:
    """Text shown as an indented block, cut with a mark."""
    return "\n".join("    | " + line for line in _cut(str(text), limit, 2).split("\n"))


def call_shown(t: dict, limit: int = CALL_CHARS) -> str:
    """A tool call as record 2 shows it: what it acted on, and what it was given."""
    name = t.get("tool_name") or "?"
    given = _given(t)
    path = t.get("file_path") or given.get("file_path") or given.get("notebook_path") or given.get("path") or ""
    command = t.get("command") or given.get("command")
    if command:
        return _cut(str(command), limit, 2)
    if name in ("Grep", "Glob"):
        pattern = given.get("pattern") or t.get("pattern") or ""
        extra = ", ".join(f"{k} {given[k]}" for k in ("glob", "type", "output_mode") if given.get(k))
        shown = f"pattern {pattern!r}" + (f" in {path}" if path else "") + (f" ({extra})" if extra else "")
        return _cut(shown, limit, 2)
    if name == "Read":
        if given.get("offset") or given.get("limit"):
            return (f"{path} (from line {given.get('offset') or 1}"
                    + (f", {given['limit']} lines" if given.get("limit") else "") + ")")
        return str(path)
    if name in ("Edit", "NotebookEdit") and ("old_string" in given or "new_source" in given):
        head = str(path) + (" (every occurrence)" if given.get("replace_all") else "")
        new = given.get("new_string", given.get("new_source", ""))
        return (f"{head}\n  replaced:\n{_block(given.get('old_string', ''), limit // 2)}"
                f"\n  with:\n{_block(new, limit // 2)}")
    if name == "MultiEdit" and isinstance(given.get("edits"), list):
        edits = given["edits"]
        share = max(100, limit // (2 * min(len(edits), MULTI_EDITS) or 1))
        parts = [f"{path}, {len(edits)} edits"]
        for i, e in enumerate(edits[:MULTI_EDITS], 1):
            e = e if isinstance(e, dict) else {}
            parts.append(f"  {i}. replaced:\n{_block(e.get('old_string', ''), share)}"
                         f"\n     with:\n{_block(e.get('new_string', ''), share)}")
        if len(edits) > MULTI_EDITS:
            parts.append(f"  [{len(edits) - MULTI_EDITS} more edits not shown]")
        return "\n".join(parts)
    if name == "Write" and "content" in given:
        body = str(given.get("content") or "")
        return f"{path} ({len(body):,} characters)\n{_block(body, limit)}"
    if name in ("Task", "Agent"):
        about = given.get("description") or ""
        prompt = str(given.get("prompt") or "")
        return _cut(f"{about}: {prompt}" if about else prompt, limit, 2)
    if path:
        return str(path)
    return _cut(json.dumps(given, ensure_ascii=False) if given else str(t.get("content") or ""), limit, 2)


def _fit_result_budget(turns: list[dict], cut_turn: int, max_chars: int, fill: bool = False,
                       record: int = 1) -> int:
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

    ``fill`` spends what short results leave on the long ones: the largest cap,
    up to what the corpus kept of a result, whose total still fits. Spread
    evenly, the cap on cipher-box-43's accepted-answer conversation was under
    3,000 characters while the excerpt used 27,600 of its 60,000, and the one
    line its accepted answer rests on -- "could not parse "1GB" as uint64" --
    sat at character 3,018 of a docker log, so the trace check was shown a
    record without it (G-77). Only the accepted answer's conversation fills;
    what a candidate is shown is unchanged.
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
            if record >= 2:
                fixed += len(call_shown(t)) + 40
            else:
                detail = t.get("command") or t.get("file_path") or content[:150]
                fixed += min(len(str(detail)), 220) + 40
        elif kind == "tool_result" and content:
            results.append(len(content))

    if not results:
        return 400
    remaining = max_chars - fixed - 40 * len(results)
    if remaining <= 0:
        return 400
    if fill:
        if sum(min(r, FILL_CAP) for r in results) <= remaining:
            return FILL_CAP
        lo, hi = 400, FILL_CAP
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sum(min(r, mid) for r in results) <= remaining:
                lo = mid
            else:
                hi = mid - 1
        return lo
    # Spread what is left evenly, then clamp: never below the old cap, and not
    # so high that one enormous result swamps the rest.
    return max(400, min(4000, remaining // len(results)))


def build_excerpt(
    turns: list[dict],
    cut_turn: int,
    *,
    max_chars: int = 60_000,
    mark_pushback: bool = False,
    fill: bool = False,
    record: int = 1,
) -> str:
    """Render the turns leading up to a pushback into something readable.

    Sessions run to ~1,750 turns but only ~40 are conversational; the rest is
    tool traffic. Tool calls still matter -- they are the difference between an
    agent that checked and one that asserted -- so they are kept in compressed
    form, while progress and file-snapshot noise is dropped.

    ``record`` is the rendering's rules (``RECORD``). The default stays 1, what
    the task-building gates read; the candidate and the checker read ``RECORD``.
    """
    result_budget = _fit_result_budget(turns, cut_turn, max_chars, fill=fill, record=record)
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
            lines.append(f"\n[turn {n}] USER{marker}:\n{_cut(content, MESSAGE_CHARS, record)}")
        elif kind == "assistant_response":
            lines.append(f"\n[turn {n}] AGENT:\n{_cut(content, MESSAGE_CHARS, record)}")
        elif kind == "assistant_thinking":
            lines.append(f"\n[turn {n}] AGENT (thinking):\n{_cut(content, 1500, record)}")
        elif kind == "tool_use":
            tool = t.get("tool_name") or "?"
            if record >= 2:
                detail = call_shown(t)
            else:
                detail = str(t.get("command") or t.get("file_path") or content[:200])[:220]
            # A call recovered from the raw transcript (G-76) is shown under the
            # turn it was issued with, not its fractional place in the order.
            lines.append(f"[turn {t.get('shown_as', n)}] AGENT calls {tool}: {detail}")
        elif kind == "tool_result":
            lines.append(f"[turn {n}] -> result: {_cut(content, result_budget, record)}")

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text

    # Overrun: squeeze tool traffic rather than cutting the conversation. An
    # earlier version elided the middle, which cost 13 of 25 readings part of
    # their context -- including 3 of the 6 that produced a usable case. Tool
    # results are the bulk (59% of a large excerpt) while the user/agent
    # narrative is under a third, so tightening the former is enough.
    squeezes = ((600, 200), (300, 80), (150, 40)) if record >= 2 else ((200, 200), (100, 80), (60, 40))
    for tool_budget, result_budget in squeezes:
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
                squeezed.append(f"\n[turn {n}] USER{marker}:\n{_cut(content, MESSAGE_CHARS, record)}")
            elif kind == "assistant_response":
                squeezed.append(f"\n[turn {n}] AGENT:\n{_cut(content, MESSAGE_CHARS, record)}")
            elif kind == "assistant_thinking":
                squeezed.append(f"\n[turn {n}] AGENT (thinking):\n{_cut(content, 800, record)}")
            elif kind == "tool_use":
                tool = t.get("tool_name") or "?"
                if record >= 2:
                    detail = call_shown(t, tool_budget)
                else:
                    detail = str(t.get("command") or t.get("file_path") or content[:150])[:tool_budget]
                squeezed.append(f"[turn {t.get('shown_as', n)}] AGENT calls {tool}: {detail}")
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
                squeezed.append(f"[turn {n}] -> result: {_cut(content, budget, record)}")
        text = "\n".join(squeezed)
        if len(text) <= max_chars:
            break
    return text

