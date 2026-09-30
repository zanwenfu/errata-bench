"""Tool calls the conversations table lost, put back from the raw transcripts (G-76).

Claude Code writes one transcript entry per content block, so an assistant
message that makes several calls at once is several entries sharing one message
id. SWE-chat's conversations table keeps one tool_use row per message: of a
batch of parallel calls only the last survives, while every call's result is
kept, with its id. Across the corpus 76,617 of 408,085 tool results (18.8%)
have no call, in 4,385 of 4,856 sessions. In gemini-voyager-17 they are eight
of the nine README edits its accepted answer reports, so the trace check read
that answer against a record in which the edits were never made.

The raw transcripts ship with the corpus (transcripts/{session_id}.jsonl), so a
lost call can be put back where its batch was issued: just before the batch's
surviving call or its first result, whichever comes first. Each gets a
fractional turn number, so no existing turn number moves -- tasks store their
cut, resolution and redactions as turn numbers -- and is rendered with the turn
number of the call it was issued beside (`shown_as`). A recovered row says so.

Where it applies. Always to the conversation an accepted answer was written
after, which is the record its author had. For tasks built from now on
(phase B), to every stage: screening reads the recovered record, the build
replays the lost edits, and the task says so (`Task.calls_recovered`), so its
candidates are shown the calls too. Never to tasks built before: the first
grid's candidates saw the table as it is, and reading their answers against a
record they were not shown would change the question.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


def transcript_path(session_id: str) -> Path:
    from .sessions import CORPUS

    return CORPUS / "transcripts" / f"{session_id}.jsonl"


# The entry types of Claude Code's transcripts, measured over the corpus's
# 4,927 transcripts of sessions labelled Claude Code, "unknown" or "Agent" (the
# last two are Claude Code recorded by development builds of the Entire CLI).
# Other agents' transcripts sit in the same directory under the same name:
# OpenCode's is one pretty-printed JSON object, Codex's opens with
# `session_meta`, Copilot's with `session.start`, Cursor's carries no type.
CLAUDE_CODE_TYPES = frozenset({
    "user", "assistant", "system", "summary", "progress", "file-history-snapshot",
    "queue-operation", "permission-mode", "custom-title", "ai-title", "pr-link",
    "attachment", "last-prompt", "agent-name"})


def has_transcript(session_id: str) -> bool:
    """Whether this session's lost calls can be put back here: a transcript in Claude Code's format.

    Any file was enough before, so screening marked all 623 OpenCode rows
    `calls_recovered` although `raw_calls` reads nothing from them.

    A transcript whose first entry is of a type SWE-chat's period knew is Claude
    Code's, as before. One that opens otherwise is Claude Code's when its user
    or assistant entries have Claude Code's shape: newer versions open with
    types SWE-chat never saw (2.1.246's `bridge-session`), and the collector's
    corpus holds them (#16). Other agents' transcripts have no such entries.
    Across SWE-chat's 5,850 transcripts the two readings agree on every one.
    """
    path = transcript_path(session_id)
    if not path.is_file():
        return False
    first = None
    with path.open(errors="replace") as fh:
        for n, line in enumerate(fh):
            if n >= 500:
                break
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if first is None and entry.get("type") is not None:
                first = entry["type"]
                if first in CLAUDE_CODE_TYPES:
                    return True
            message = entry.get("message")
            if (entry.get("type") in ("user", "assistant") and "sessionId" in entry and isinstance(message, dict)
                    and "role" in message):
                return True
    return False


def foreign_transcript(session_id: str) -> bool:
    """Whether a transcript is here in another agent's format, which nothing here can read."""
    return transcript_path(session_id).is_file() and not has_transcript(session_id)


def recovered(turns_by_session: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Every session's turns with its lost calls put back, where its transcript is here.

    What the stages that build new tasks read (phase B): the leak gate and the
    candidate then see the same record, and the build replays the lost edits.
    """
    return {sid: recover(sid, turns) for sid, turns in turns_by_session.items()}


def raw_calls(path: Path) -> list[dict]:
    """Every call the session's main agent made, in order, with its message id.

    Subagents' calls are left out: they are recorded as progress entries of the
    parent's call, and the agent itself saw only the report its subagent
    returned.
    """
    out = []
    with path.open() as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            # Not every line is an entry: some transcripts carry bare JSON
            # strings, and some entries a message that is a string. Found on
            # the first real screening run, which it stopped before a call.
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if (entry.get("type") != "assistant" or entry.get("isSidechain")
                    or not isinstance(message, dict) or not isinstance(message.get("content"), list)):
                continue
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    out.append({"id": block["id"], "name": block.get("name"),
                                "input": block.get("input") or {}, "message": message.get("id")})
    return out


def raw_results(path: Path) -> dict[str, str]:
    """Every result the session's main agent received, whole, by the id of its call.

    As `raw_calls` reads the calls: a subagent's results are its own, and the
    agent saw only the report it returned.
    """
    out: dict[str, str] = {}
    with path.open() as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if (entry.get("type") != "user" or entry.get("isSidechain")
                    or not isinstance(message, dict) or not isinstance(message.get("content"), list)):
                continue
            for block in message["content"]:
                if not (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")):
                    continue
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                out[str(block["tool_use_id"])] = str(body or "")
    return out


def whole_results(session_id: str, turns: list[dict]) -> list[dict]:
    """``turns`` with each result the table cut given back whole, from the raw transcript (record 3).

    SWE-chat's conversations table keeps about 10 KB of a result. Up to the
    cut, it kept less than the transcript for 16 of the 55 tasks' 1,505
    results, in 4 tasks, 181,960 characters in all (09-27). A result is given
    back only when the transcript's text begins with what the table kept, so a
    different result under the same id is never put in its place. Returned
    unchanged when the session has no transcript in Claude Code's format.
    """
    if not has_transcript(session_id):
        return turns
    whole = raw_results(transcript_path(session_id))
    out = []
    for t in turns:
        kept = str(t.get("content") or "")
        full = whole.get(str(t.get("tool_call_id") or ""))
        # Compared as kept, not stripped: a read's output opens "     1→", and
        # stripped, not one of the 14 cut reads of the 55 tasks matched (09-27).
        if (t.get("turn_type") == "tool_result" and full and len(full) > len(kept)
                and kept.strip() and full.startswith(kept[:200])):
            t = {**t, "content": full, "whole_from_transcript": True}
        out.append(t)
    return out


def recover(session_id: str, turns: list[dict]) -> list[dict]:
    """``turns`` with the calls the table lost put back, in order.

    Only a call whose result the table kept is put back: the result is what
    places it. Returned unchanged when the session has no transcript here.
    """
    path = transcript_path(session_id)
    if not path.is_file():
        return turns
    numbered = [t for t in turns if t.get("turn_number") is not None and t.get("tool_call_id")]
    have = {t["tool_call_id"] for t in numbered if t.get("turn_type") == "tool_use"}
    call_at = {t["tool_call_id"]: t["turn_number"] for t in numbered if t.get("turn_type") == "tool_use"}
    result_at = {t["tool_call_id"]: t["turn_number"] for t in numbered if t.get("turn_type") == "tool_result"}
    batches: dict[str, list[dict]] = {}
    for call in raw_calls(path):
        batches.setdefault(call["message"] or call["id"], []).append(call)
    added = []
    for calls in batches.values():
        lost = [c for c in dict((c["id"], c) for c in calls).values()
                if c["id"] not in have and c["id"] in result_at]
        if not lost:
            continue
        at = min([call_at[c["id"]] for c in calls if c["id"] in call_at]
                 + [result_at[c["id"]] for c in calls if c["id"] in result_at])
        for j, call in enumerate(lost, 1):
            given = call["input"] if isinstance(call["input"], dict) else {}
            added.append({
                "session_id": session_id,
                "turn_number": at - 1 + j / (len(lost) + 1),
                "role": "assistant",
                "turn_type": "tool_use",
                "content": json.dumps(given, ensure_ascii=False),
                "tool_name": call["name"],
                "command": given.get("command"),
                "file_path": given.get("file_path") or given.get("notebook_path") or given.get("path"),
                "prompt_pushback": None,
                "is_conversational": False,
                "tool_call_id": call["id"],
                "recovered": True,
                "shown_as": at,
            })
    if not added:
        return turns
    return sorted(turns + added, key=lambda t: t["turn_number"] if t.get("turn_number") is not None else 0)


def _flat(text) -> str:
    """Text with its whitespace collapsed, as rows and transcript blocks are compared."""
    return " ".join(str(text or "").split())


def raw_messages(path: Path) -> list[dict]:
    """The main agent's messages in order, each with its id and its content blocks, each block once.

    Claude Code writes each block of a message as its own entry under the
    message's id, and an entry written twice adds nothing. Sub-agents' messages
    are left out, as `raw_calls` leaves out their calls.
    """
    order: list[str] = []
    blocks: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}
    with path.open(errors="replace") as fh:
        for n, line in enumerate(fh):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if (entry.get("type") != "assistant" or entry.get("isSidechain")
                    or not isinstance(message, dict) or not isinstance(message.get("content"), list)):
                continue
            key = message.get("id") or f"line-{n}"
            if key not in blocks:
                order.append(key)
                blocks[key], seen[key] = [], set()
            for block in message["content"]:
                if not isinstance(block, dict):
                    continue
                ident = (block.get("type"), block.get("id") or _flat(block.get("text") or block.get("thinking")))
                if ident not in seen[key]:
                    seen[key].add(ident)
                    blocks[key].append(block)
    return [{"id": key, "blocks": blocks[key]} for key in order]


# The kinds of block the table's one-block-per-message rule loses that
# `restore_text` puts back, and the row each becomes. Thinking is put back only
# when asked: whether a candidate should read another model's thinking is #17's
# open question.
RESTORED_AS = {"text": ("assistant_response", True), "thinking": ("assistant_thinking", False)}


def restore_text(session_id: str, turns: list[dict], *, thinking: bool = False) -> list[dict]:
    """``turns`` with the agent's text the table lost put back, each in its message's place (G-79).

    The table keeps the last block of each assistant message, so what the agent
    wrote before a call in the same message is gone: 582 of the 641 agent texts
    up to the cut in v1's 55 tasks. `recover` put the calls back; this puts the
    text back the same way. A message is placed by the blocks of it the turns
    hold -- its calls by id, including those `recover` put back, and its
    closing text matched in order -- and each lost text goes between its
    neighbours in the message, with a fractional turn number, so no stored turn
    moves. It is shown under the turn of the block that follows it
    (`shown_as`), as a recovered call is, and says it was put back
    (`recovered`). A message the turns hold nothing of is left out: there is
    nothing to place it by. A message's closing block is never put back: the
    table keeps it (2,735 of 2,736 closing texts in a sample of 300 sessions),
    and one it did keep but was not matched would be shown twice. Returned
    unchanged when the session has no transcript in Claude Code's format.

    ``thinking`` also puts back the thinking the rule lost, as thinking rows.
    """
    if not has_transcript(session_id):
        return turns
    kinds = {"text", "thinking"} if thinking else {"text"}
    rows = sorted((t for t in turns if t.get("turn_number") is not None), key=lambda t: t["turn_number"])
    numbers = [t["turn_number"] for t in rows]
    call_row = {t["tool_call_id"]: i for i, t in enumerate(rows)
                if t.get("turn_type") == "tool_use" and t.get("tool_call_id")}
    held = {kind: [(i, _flat(t.get("content"))) for i, t in enumerate(rows) if t.get("turn_type") == row_type]
            for kind, (row_type, _) in RESTORED_AS.items()}
    words = lambda b: _flat(b.get("text") if b.get("type") == "text" else b.get("thinking"))
    # Each interval between two rows, and what goes into it in the order the
    # agent wrote it. Keyed by the interval alone: the end of one message and
    # the start of the next can fall between the same two rows.
    gaps: dict[tuple, list[tuple[dict, object]]] = {}
    last = -1
    for message in raw_messages(transcript_path(session_id)):
        blocks = message["blocks"]
        # Where each block of the message is among the rows, when the rows hold
        # it: a call by its id, and a text by its words, matched in order after
        # the messages before -- the closing text anywhere after them, any
        # other only before the next block of its message the rows hold, so it
        # never takes a later message's words. SWE-chat's table holds no text
        # but the closing one; the collector's corpus (#16) holds every block,
        # and there nothing is put back twice.
        at: list[int | None] = [call_row.get(b.get("id")) if b.get("type") == "tool_use" else None for b in blocks]
        close = len(blocks) - 1
        if close >= 0 and blocks[close].get("type") in held and words(blocks[close]):
            at[close] = next((i for i, s in held[blocks[close]["type"]] if i > last and s == words(blocks[close])),
                             None)
        cursor = last
        for j in range(close):
            bound = next((at[k] for k in range(j + 1, len(blocks)) if at[k] is not None), None)
            if blocks[j].get("type") in held and words(blocks[j]) and bound is not None:
                hit = next((i for i, s in held[blocks[j]["type"]] if cursor < i < bound and s == words(blocks[j])),
                           None)
                if hit is not None:
                    at[j], cursor = hit, hit
        placed = [j for j, i in enumerate(at) if i is not None]
        if not placed:
            continue
        last = max(last, max(at[j] for j in placed))
        for j, b in enumerate(blocks):
            if at[j] is not None or j == len(blocks) - 1 or b.get("type") not in kinds or not words(b):
                continue
            before = [p for p in placed if p < j]
            after = [p for p in placed if p > j]
            # Between the block before it and the next row of all, so no row,
            # a result included, ever falls inside the interval it is given.
            if before:
                low = numbers[at[before[-1]]]
                nxt = at[before[-1]] + 1
                high = numbers[nxt] if nxt < len(rows) else low + 1
            else:
                first = at[after[0]]
                low = numbers[first - 1] if first > 0 else numbers[first] - 1
                high = numbers[first]
            label_row = rows[at[after[0]]] if after else rows[at[before[-1]]]
            gaps.setdefault((low, high), []).append((b, label_row.get("shown_as", label_row["turn_number"])))
    added = []
    for (low, high), members in gaps.items():
        # Short of the next whole number too: redactions and rewrites name turns
        # by whole numbers, and a text placed at 3.0 between rows 2 and 4 would be
        # taken for turn 3, removed or rewritten with it.
        high = min(high, math.floor(low) + 1)
        if not low < high:
            continue
        for r, (b, label) in enumerate(members, 1):
            turn_type, conversational = RESTORED_AS[b.get("type")]
            added.append({
                "session_id": session_id,
                "turn_number": low + (high - low) * r / (len(members) + 1),
                "role": "assistant",
                "turn_type": turn_type,
                "content": str((b.get("text") if b.get("type") == "text" else b.get("thinking")) or "").strip(),
                "tool_name": None,
                "command": None,
                "file_path": None,
                "prompt_pushback": None,
                "is_conversational": conversational,
                "tool_call_id": None,
                "recovered": True,
                "shown_as": label,
            })
    if not added:
        return turns
    return sorted(turns + added, key=lambda t: t["turn_number"] if t.get("turn_number") is not None else 0)


# What a sub-agent writes. Its calls are recorded only as `progress` entries of
# the main agent's call that spawned it (data.type "agent_progress", the call's
# id in `parentToolUseID`); the conversations table has no rows for them and
# `raw_calls` leaves them out, so the replay never applies them. 125 of the next
# batches' 1,601 buildable moments have sub-agent edits before them, 3,592 calls
# in all; in 40 the main agent made no edit of its own, and the replay reported
# a tree it had applied nothing to.
SUBAGENT_WRITES = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def subagent_dir(session_id: str) -> Path | None:
    """Where the collector keeps this session's subagent transcripts, when the corpus has them (#16).

    SWE-chat's corpus has none, so this is None there and nothing below changes
    for it.
    """
    from .sessions import CORPUS

    root = CORPUS / "subagents"
    return root / session_id if root.is_dir() else None


def subagent_edits(session_id: str) -> list[dict]:
    """File edits the session's sub-agents made, each with the main agent's call that spawned it.

    `spawned_by` is that call's id: a sub-agent's own sub-agent is followed up
    to the main agent's call. Read from the parent's transcript, where Claude
    Code wrote sub-agents' calls as progress entries until 2.1, and from the
    sub-agents' own transcripts where the collector kept them
    (``subagents/<session>/<spawning call>/agent-*.jsonl``).
    """
    path = transcript_path(session_id)
    parent: dict[str, str | None] = {}
    edits: list[dict] = []
    own = subagent_dir(session_id)
    for folder in sorted(own.iterdir()) if own is not None and own.is_dir() else []:
        for sub in sorted(folder.glob("agent-*.jsonl")):
            with sub.open(errors="replace") as fh:
                for line in fh:
                    if '"tool_use"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    message = entry.get("message") if isinstance(entry, dict) else None
                    content = message.get("content") if isinstance(message, dict) else None
                    for block in content if isinstance(content, list) else []:
                        if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")):
                            continue
                        parent[block["id"]] = folder.name
                        if block.get("name") in SUBAGENT_WRITES:
                            given = block.get("input") if isinstance(block.get("input"), dict) else {}
                            edits.append({"id": block["id"], "tool": block["name"],
                                          "file_path": given.get("file_path") or given.get("notebook_path"),
                                          "parent": folder.name})
    if not path.is_file():
        return _placed(edits, parent)
    with path.open() as fh:
        for line in fh:
            if '"agent_progress"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            data = entry.get("data") if isinstance(entry, dict) else None
            if not isinstance(data, dict) or data.get("type") != "agent_progress":
                continue
            outer = data.get("message")
            message = outer.get("message") if isinstance(outer, dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")):
                    continue
                parent[block["id"]] = entry.get("parentToolUseID")
                if block.get("name") in SUBAGENT_WRITES:
                    given = block.get("input") if isinstance(block.get("input"), dict) else {}
                    edits.append({"id": block["id"], "tool": block["name"],
                                  "file_path": given.get("file_path") or given.get("notebook_path"),
                                  "parent": entry.get("parentToolUseID")})
    return _placed(edits, parent)


def _placed(edits: list[dict], parent: dict[str, str | None]) -> list[dict]:
    """Each edit with the main agent's call it descends from."""
    for e in edits:
        top, seen = e["parent"], set()
        while top in parent and top not in seen:
            seen.add(top)
            top = parent[top]
        e["spawned_by"] = top
    return edits


SPAWNING_TOOLS = frozenset({"Task", "Agent"})


def unrecorded_subagents(session_id: str, turns: list[dict]) -> list[str]:
    """The main agent's sub-agent calls whose sub-agent left no record here: their ids.

    Only where the corpus keeps sub-agent transcripts (the collector's, #16).
    There, a sub-agent's work is either in its own transcript or, before
    Claude Code 2.1, in the parent's progress entries. A call with neither
    record may have changed files no replay knows of. Empty for SWE-chat,
    whose corpus keeps no sub-agent transcripts, so its builds are unchanged.
    """
    own = subagent_dir(session_id)
    if own is None:
        return []
    kept = {p.name for p in own.iterdir()} if own.is_dir() else set()
    progressed: set[str] = set()
    path = transcript_path(session_id)
    if path.is_file():
        with path.open(errors="replace") as fh:
            for line in fh:
                if '"agent_progress"' in line:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("parentToolUseID"):
                        progressed.add(str(entry["parentToolUseID"]))
    return [str(t["tool_call_id"]) for t in turns if t.get("turn_type") == "tool_use"
            and t.get("tool_name") in SPAWNING_TOOLS and t.get("tool_call_id")
            and str(t["tool_call_id"]) not in kept and str(t["tool_call_id"]) not in progressed]
