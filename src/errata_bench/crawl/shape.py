"""A Claude Code transcript as rows of SWE-chat's conversations table, with nothing dropped (#16, G-76, G-79).

The pipeline reads SWE-chat's tables, so a session collected here is written
in their schema (`CONVERSATIONS`) with their row types. Four things differ
from SWE-chat's parser, each on purpose:

- **Every block is a row.** Claude Code writes each block of a message as its
  own entry, and SWE-chat kept only the last block of each message. That lost
  batched calls (G-76) and the text written before a call (G-79). Here each
  text, thinking and tool_use block is its own row. A message's token counts
  go on its last row only, so they are counted once.
- **A tool result is kept whole.** SWE-chat cut some long results (most near
  10 KB, not all). `recover.whole_results` puts the whole result back from the
  transcript anyway, so nothing downstream depended on the cut.
- **Who wrote a user message is decided by rule.** SWE-chat's split between
  `user_prompt` and `system_injected` was inconsistent: the same kind of entry
  landed on both sides, and a message holding an image was dropped. Measured
  on 300 sessions, 09-29. Here, by the marks Claude Code writes on the entry
  where it writes them, and by the text where it does not (`user_kind`, G-90):
  - `peer_message`: another agent's message, delivered into the agent's
    turn: a teammate's in an agent team, a subagent's hand-back, another
    Claude session's, a coordinator's relay. SWE-chat has no such type. No
    developer typed these, and they are shown as another agent's
    (`corpus.turns`);
  - `system_injected`: built-in commands (`<command-name>/model`), command
    and shell output (`<local-command-stdout>`, `<bash-stdout>`, ...),
    background-task notices, system reminders and instructions, and CI
    events. SWE-chat counted most background-task notices as the
    developer's; they are not. Also what Claude Code writes there itself: a
    skill the agent loaded with its own call, a prompt a timer or the
    harness sent, a Stop hook's feedback, the line it writes on resuming, a
    note on an image a tool returned, and the "Tool loaded." beside a
    deferred tool's result;
  - `user_prompt` (the developer's): everything else, including commands
    with their arguments and expanded instructions, `!` shell input,
    interruptions, messages typed while the agent was busy (written only as
    queue entries, or beside a tool's result), and the summary that opens a
    continued session (kept as SWE-chat kept it, with `is_continuation`,
    since the candidate needs what it says);
  - an image is kept as the text ``[Image: <type>]``, SWE-chat's form, and
    context an IDE attaches (``<ide_selection>``, the file opened) is kept,
    where SWE-chat dropped it.
- **A message Claude Code writes in the agent's turn is not the agent's.**
  One with model ``<synthetic>`` (a usage limit, an API error, "No response
  requested.") is `system_injected`, not `assistant_response` (G-94).

Entries that are not messages (progress, file snapshots, system events,
queue operations) are kept as SWE-chat kept them, as ``metadata`` rows holding
the entry's JSON.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pyarrow as pa

S = pa.large_string()
CONVERSATIONS = pa.schema([
    ("turn_id", S), ("session_id", S), ("checkpoint_pk", S), ("repo_id", S), ("user_id", S),
    ("turn_number", pa.int64()), ("conversation_turn_number", pa.float64()), ("role", S), ("turn_type", S),
    ("is_conversational", pa.bool_()), ("content", S), ("model", S), ("timestamp", pa.timestamp("us", tz="UTC")),
    ("input_tokens", pa.int64()), ("output_tokens", pa.int64()), ("cache_creation_input_tokens", pa.int64()),
    ("cache_read_input_tokens", pa.int64()), ("is_continuation", pa.bool_()), ("is_first_turn", pa.bool_()),
    ("word_count", pa.int64()), ("char_count", pa.int64()), ("tool_name", S), ("tool_call_id", S),
    ("file_path", S), ("command", S), ("pattern", S), ("tool_input_json", S), ("category", S),
    ("bash_category", S), ("queue_op_subtype", S), ("agent", S), ("strategy", S), ("language", S),
    ("prompt_intent", S), ("prompt_pushback", S),
])

# Output Claude Code puts in the user's turn, not something the developer typed.
# A built-in command's entry opens with its name (`<command-name>/model`); a
# command the developer wrote opens with its message and carries their
# arguments, and stays theirs.
INJECTED = re.compile(r"^\s*<(local-command-caveat|local-command-stdout|local-command-stderr|bash-stdout|bash-stderr"
                      r"|command-name|task-notification|system-reminder|system[_-]instructions?|ci-monitor-event)>")
CONTINUED = "This session is being continued"
# Another agent's message, delivered into the agent's turn (G-90): a teammate's,
# a subagent handing its result back, another Claude session's, a coordinator's
# relay. Claude Code 2.1 marks most (`origin.kind` or `turnOrigin` "peer"); the
# opening catches those delivered from a queue, or written by a version that
# marks nothing. 3,244 of the corpus's 95,496 developer rows were these (10-02).
PEER = re.compile(r"^\s*(?:<(?:teammate-message|agent-message|cross-session-message|relay)\b"
                  r"|Another Claude session sent a message)")
# What Claude Code writes into the user's turn itself, by the marks on the
# entry (G-90): a notice, an automatic continuation, a timer's or the harness's
# prompt.
HARNESS_ORIGINS = frozenset({"task-notification", "auto-continuation"})  # origin.kind
HARNESS_TURNS = frozenset({"task_notification", "scheduled", "system"})  # turnOrigin
# Written beside a deferred tool's result when the agent loads it.
TOOL_LOADED = "Tool loaded."
# The model Claude Code writes on a message of its own in the agent's turn
# (G-94): "No response requested." (546 rows of the corpus, 10-02), a usage
# limit (205), "Prompt is too long" (114), an API error or a login expiring.
# Shown as the agent's words, two located failed answers were one of these.
SYNTHETIC = "<synthetic>"
METADATA = {"progress": "progress", "file-history-snapshot": "file_snapshot", "system": "system_event",
            "summary": "summary", "queue-operation": "queue_operation"}
FILE_KEYS = ("file_path", "notebook_path", "path")


def _time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _text(content) -> str:
    """A message's or a result's content as text; an image as ``[image]``."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text") or "")
        elif block.get("type") == "image":
            media = (block.get("source") or {}).get("media_type") if isinstance(block.get("source"), dict) else None
            parts.append(f"[Image: {media or 'image'}]")  # SWE-chat's form
        elif block.get("type") == "tool_result":
            continue
        elif "text" in block:
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def user_kind(entry: dict, text: str, *, beside_result: bool = False, expansion: bool = False) -> str:
    """Who wrote a user entry's text: the developer (`user_prompt`), another agent (`peer_message`) or Claude Code
    (`system_injected`). By the marks Claude Code writes on the entry, and by the text where it wrote none (G-90).

    `isMeta` marks what Claude Code wrote into the turn, and among that a
    command's expanded instructions, which are what the developer asked for
    and what the agent was given; SWE-chat kept those as the developer's in
    109 of 124 cases measured, and so does this. So a meta entry is the
    developer's only when it expands a command they ran (``expansion``, as
    `is_expansion` decides) and no call of the agent's loaded it
    (`sourceToolUseID`, a skill the agent ran). Every other meta entry is
    Claude Code's: a hook's feedback, the line it writes on resuming, a note
    on an image a tool returned, a timer's or a wake-up's prompt, "your
    previous response had no visible output", a command's output. Of the
    corpus's meta entries, 3,307 expand a command (10-02).

    Text beside a tool's result in the same entry (``beside_result``) is a
    message the developer typed while the tool ran, as their answer to a
    question or a permission prompt is, except the "Tool loaded." Claude Code
    writes beside a deferred tool's.
    """
    if entry.get("isCompactSummary") or text.lstrip().startswith(CONTINUED):
        return "user_prompt"
    if INJECTED.match(text):
        return "system_injected"
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    if origin.get("kind") == "peer" or entry.get("turnOrigin") == "peer" or PEER.match(text):
        return "peer_message"
    if (origin.get("kind") in HARNESS_ORIGINS or entry.get("turnOrigin") in HARNESS_TURNS
            or entry.get("promptSource") == "system"
            or entry.get("isMeta") and (not expansion or bool(entry.get("sourceToolUseID")))
            or beside_result and text.strip() == TOOL_LOADED):
        return "system_injected"
    return "user_prompt"


COMMAND_ENTRY = re.compile(r"^\s*<command-message>")


def is_expansion(entries: list[dict], i: int, index: dict[str, int]) -> bool:
    """Whether meta entry ``i`` expands a command the developer ran: the first entry before it, looking past
    attachments, system entries, progress and other meta entries, is their command entry.

    Their own command opens with its message (`<command-message>`); a built-in
    one opens with its name, and a meta entry after it is its output (`/context`
    writes its report so). A command a timer ran (`turnOrigin` "scheduled") is
    not theirs either. The entry before is the one `parentUuid` names, or the
    one before it in the file when the entry names none.
    """
    seen = 0
    while seen < 200:
        seen += 1
        entry = entries[i]
        if "parentUuid" in entry:
            parent = entry.get("parentUuid")
            if parent not in index:
                return False
            i = index[parent]
        elif i > 0:
            i -= 1
        else:
            return False
        before = entries[i]
        if before.get("type") == "assistant":
            return False
        if before.get("type") != "user" or before.get("isMeta"):
            continue
        msg = before.get("message") if isinstance(before.get("message"), dict) else {}
        text = _text(msg.get("content"))
        return bool(COMMAND_ENTRY.match(text)) and user_kind(before, text) == "user_prompt"
    return False


_COMMAND_NAME = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def as_typed(text: str) -> str | None:
    """A slash command as the developer typed it, its name and arguments, from the form Claude Code delivers it in."""
    name = _COMMAND_NAME.search(text)
    if not name:
        return None
    args = _COMMAND_ARGS.search(text)
    typed = name.group(1) if name.group(1).startswith("/") else "/" + name.group(1)
    return " ".join(f"{typed} {args.group(1) if args else ''}".split())


def queued_and_delivered(entries: list[dict]) -> set[int]:
    """The queue entries whose message is written again where it was delivered (G-84).

    A message typed while the agent was busy is written to a queue entry, and
    newer versions of Claude Code write it again when it is delivered: as the
    developer's own entry, or mid-turn as a `queued_command` attachment. That
    copy is where the agent read it, so it is the row, and the queue entry is a
    developer row only for a message written nowhere else. Each delivered copy
    is paired with the earliest queue entry of the same text still waiting, one
    to one, so a message queued twice and delivered twice stays two.

    A slash command is queued as typed (``/ship-it merge it``) and delivered
    as Claude Code's command form, so it is matched by its name and arguments
    (`as_typed`). An attachment whose prompt is content blocks makes no row of
    its own: it takes its queue entry out of waiting, and the queue entry stays
    the row, so a later message of the same text is not paired with it.
    Pairing goes by text alone, so in a version that writes only the queue, a
    message typed later with the same text can still take an earlier one's place.
    """
    waiting: dict[str, list[int]] = {}
    paired: set[int] = set()
    for i, entry in enumerate(entries):
        kind = entry.get("type")
        if kind == "queue-operation":
            text = " ".join(entry["content"].split()) if isinstance(entry.get("content"), str) else ""
            if entry.get("operation") == "enqueue" and text:
                waiting.setdefault(text, []).append(i)
            continue
        if kind == "user" and isinstance(entry.get("message"), dict):
            text = " ".join(_text(entry["message"].get("content")).split())
            texts, a_row = (text, as_typed(text)), True
        elif kind == "attachment" and isinstance(entry.get("attachment"), dict) \
                and entry["attachment"].get("type") == "queued_command":
            prompt = entry["attachment"].get("prompt")
            texts, a_row = (" ".join((prompt if isinstance(prompt, str) else _text(prompt)).split()),), isinstance(prompt, str)
        else:
            continue
        for text in texts:
            if text and waiting.get(text):
                queued = waiting[text].pop(0)
                if a_row:
                    paired.add(queued)
                break
    return paired


def _said(entry: dict) -> str:
    """What an entry says, without its bookkeeping (branch, directory, version, ids)."""
    return json.dumps([entry.get(k) for k in ("type", "subtype", "message", "attachment", "content")], sort_keys=True)


def once(entries: list[dict]) -> list[dict]:
    """The transcript with each entry once (G-85).

    In 42 of the collected corpus's transcripts, a session's history is written
    into its file again partway through: 36,036 entries under the uuid they
    already had, among them 8,859 of the developer's turns. Every call, result
    and message after that point became a row twice, and a build would replay a
    session's edits twice. The copy refreshes the entry's bookkeeping (branch,
    directory, version) but says what the first said, in 35,993 of the 36,036.
    So an entry is left out when an earlier one has its uuid and says the same.
    Two that share a uuid but say different things are both kept.
    """
    said: dict[str, set[str]] = {}
    out = []
    for entry in entries:
        uuid = entry.get("uuid")
        if uuid:
            this = _said(entry)
            if this in said.setdefault(uuid, set()):
                continue
            said[uuid].add(this)
        out.append(entry)
    return out


def claude_code_rows(session_id: str, repo_id: str, checkpoint_pk: str, entries: list[dict],
                     strategy: str | None = None) -> list[dict]:
    """The session's rows in order, numbered from 0.

    Each entry is read once (`once`), and so is each call and its result: a
    call re-sent under a new entry with the id of one already written, as a
    retried or replayed message is, is not a second call (G-85).
    """
    entries = once(entries)
    rows: list[dict] = []
    call_names: dict[str, str] = {}
    results: set[str] = set()  # calls whose result is written
    last_of_message: dict[str, int] = {}  # message id -> index of its last row
    delivered = queued_and_delivered(entries)
    index: dict[str, int] = {}  # uuid -> the entry's place, for what a meta entry follows
    for n, entry in enumerate(entries):
        if entry.get("uuid"):
            index.setdefault(entry["uuid"], n)

    def add(role, turn_type, content, entry, **fields):
        row = {"role": role, "turn_type": turn_type, "content": content,
               "timestamp": None if turn_type == "file_snapshot" else _time(entry.get("timestamp")), **fields}
        rows.append(row)
        return len(rows) - 1

    for n, entry in enumerate(entries):
        kind = entry.get("type")
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if kind == "user":
            content = msg.get("content")
            text = _text(content)
            if text.strip() or isinstance(content, str):
                beside = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                turn_type = user_kind(entry, text, beside_result=beside,
                                      expansion=bool(entry.get("isMeta")) and is_expansion(entries, n, index))
                add("user", turn_type, text, entry, is_continuation=bool(entry.get("isCompactSummary"))
                    or text.lstrip().startswith(CONTINUED))
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    cid = block.get("tool_use_id")
                    if cid and cid in results:
                        continue
                    results.add(cid)
                    add("tool_result", "tool_result", _text(block.get("content")), entry, tool_call_id=cid,
                        tool_name=call_names.get(cid))
        elif kind == "assistant":
            mid = msg.get("id")
            for block in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":  # stripped, as SWE-chat stored it
                    # Claude Code's own text in the agent's turn (G-94): a usage
                    # limit, an API error, "No response requested.". Not the agent's.
                    kind_of = "system_injected" if msg.get("model") == SYNTHETIC else "assistant_response"
                    i = add("assistant", kind_of, (block.get("text") or "").strip(), entry, model=msg.get("model"))
                elif btype == "thinking":
                    i = add("assistant", "assistant_thinking", block.get("thinking") or "", entry, model=msg.get("model"))
                elif btype == "tool_use":
                    if block.get("id") and block.get("id") in call_names:
                        continue
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    dumped = json.dumps(inp)  # SWE-chat's form: non-ASCII escaped; the same JSON either way
                    call_names[block.get("id")] = block.get("name")
                    i = add("tool_use", "tool_use", dumped, entry, model=msg.get("model"), tool_name=block.get("name"),
                            tool_call_id=block.get("id"), tool_input_json=dumped,
                            file_path=next((str(inp[k]) for k in FILE_KEYS if inp.get(k)), None),
                            command=str(inp["command"]) if isinstance(inp.get("command"), (str, list)) else None,
                            pattern=str(inp["pattern"]) if inp.get("pattern") else None)
                else:
                    continue
                if mid:
                    last_of_message[mid] = i
                    rows[i]["_usage"] = msg.get("usage")
        elif kind in METADATA:
            add("metadata", METADATA[kind], json.dumps(entry, ensure_ascii=False), entry,
                queue_op_subtype=entry.get("operation") if kind == "queue-operation" else None)
            # A message typed while the agent was busy is queued. Where the queue
            # entry is the only place it is written, it is the developer's --
            # often a correction mid-run -- so it is a row of its own, as
            # SWE-chat made it (194 of 300 sessions' unmatched messages, 09-29).
            # Where it is written again on delivery, that copy is the row (G-84).
            text = entry.get("content") if kind == "queue-operation" else None
            if entry.get("operation") == "enqueue" and isinstance(text, str) and text.strip() and n not in delivered:
                add("user", user_kind({}, text), text, entry)
        elif kind == "attachment":
            att = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
            if att.get("type") == "queued_command" and isinstance(att.get("prompt"), str) and att["prompt"].strip():
                # A message queued while the agent was busy, delivered as an
                # attachment by newer versions: the developer's unless it is
                # itself a notice (`commandMode` "task-notification").
                kind_of = ("system_injected" if att.get("commandMode") not in (None, "prompt")
                           else user_kind({}, att["prompt"]))
                add("user", kind_of, att["prompt"], entry)
            elif att.get("type") == "edited_text_file":
                add("user", "system_injected", json.dumps(att, ensure_ascii=False), entry)
            else:
                # Hook output, reminders, memory files, tool and skill listings:
                # the harness talking to itself, kept as metadata.
                add("metadata", "system_event", json.dumps(entry, ensure_ascii=False), entry)
        # Other entry types (titles, permission modes, last-prompt markers,
        # bridge sessions) carry no turn.

    usage_rows = set(last_of_message.values())
    out, conversational = [], 0
    first_prompt_seen = False
    for n, row in enumerate(rows):
        usage = row.pop("_usage", None)
        if n not in usage_rows:
            usage = None  # a message's counts go on its last row only
        is_conv = row["turn_type"] in ("user_prompt", "assistant_response")
        content = row["content"] or ""
        first = row["turn_type"] == "user_prompt" and not first_prompt_seen
        first_prompt_seen |= first
        out.append({
            "turn_id": f"{session_id}#{n}", "session_id": session_id, "checkpoint_pk": checkpoint_pk,
            "repo_id": repo_id, "user_id": None, "turn_number": n,
            "conversation_turn_number": float(conversational) if is_conv else None,
            "role": row["role"], "turn_type": row["turn_type"], "is_conversational": is_conv, "content": content,
            "model": row.get("model"), "timestamp": row["timestamp"],
            "input_tokens": (usage or {}).get("input_tokens"), "output_tokens": (usage or {}).get("output_tokens"),
            "cache_creation_input_tokens": (usage or {}).get("cache_creation_input_tokens"),
            "cache_read_input_tokens": (usage or {}).get("cache_read_input_tokens"),
            "is_continuation": bool(row.get("is_continuation")), "is_first_turn": first,
            "word_count": len(content.split()), "char_count": len(content),
            "tool_name": row.get("tool_name"), "tool_call_id": row.get("tool_call_id"),
            "file_path": row.get("file_path"), "command": row.get("command"), "pattern": row.get("pattern"),
            "tool_input_json": row.get("tool_input_json"), "category": None, "bash_category": None,
            "queue_op_subtype": row.get("queue_op_subtype"), "agent": "Claude Code", "strategy": strategy,
            "language": None, "prompt_intent": None, "prompt_pushback": None,
        })
        conversational += is_conv
    return out


def read_entries(path) -> list[dict]:
    """A transcript's entries in order; a line that will not parse is skipped."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                out.append(value)
    return out


def is_claude_code(entries: list[dict]) -> bool:
    """Whether a transcript is in Claude Code's format, read from its entries' shape.

    Not from a list of entry types: Claude Code adds types (2.1.246 writes
    `bridge-session` and `atis-latch`, which SWE-chat's period never saw).
    Its user and assistant entries carry a `sessionId` and a `message` with a
    `role`; Codex, OpenCode, Copilot and Cursor write none of that.
    """
    for e in entries[:500]:
        msg = e.get("message")
        if e.get("type") in ("user", "assistant") and "sessionId" in e and isinstance(msg, dict) and "role" in msg:
            return True
    return False
