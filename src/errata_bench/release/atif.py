"""A Harbor trajectory (ATIF) read into the record the graders read (v1 step 3).

Harbor runs 40+ agents, and most write their session as ATIF: steps from the
user or the agent, each agent step with its tool calls, and an observation
holding each call's result by the call's id (`harbor.models.trajectories`).
The graders read a list of calls in this harness's own shape -- a name, what
the call acted on (`command` or `path`), what an edit replaced and with what,
what a write wrote, and the result -- rendered by `trace.render`. This reads the
one into the other and keeps everything: every call, all of its arguments and
all of its result, as the agent received it.

Two things are left out, deliberately:

  - steps marked `is_copied_context`: a task that seeds an agent with the
    developer's conversation as its own history (a task's trajectory.json)
    gets those steps back in its trajectory, and they are the conversation,
    which the graders are given separately, not this attempt's work;
  - nothing else. A subagent's calls are the agent's work too, and are kept,
    each named as its subagent's.

The answer is the last message the agent wrote that is not empty.
"""

from __future__ import annotations

import json
from typing import Any

# Argument names, across the agents Harbor runs, for the one thing a call acted on.
_COMMAND = ("command", "cmd", "keystrokes", "script")
_PATH = ("file_path", "path", "filePath", "notebook_path", "absolute_path", "target_file")
_OLD = ("old_string", "old_str", "old_text", "search")
_NEW = ("new_string", "new_str", "new_text", "replace")
_BODY = ("content", "file_text", "contents", "text")


def text_of(content: Any) -> str:
    """A message's or a result's text: a string as it is, a list of parts joined, an image named."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" or "text" in part:
                    parts.append(str(part.get("text", "")))
                else:
                    parts.append(f"[{part.get('type', 'part')}]")
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def _first(args: dict, names: tuple[str, ...]):
    for n in names:
        if n in args and args[n] not in (None, ""):
            return args[n]
    return None


def call_of(name: str, args: dict | None, result: str) -> dict:
    """One call in the graders' shape, with nothing of it dropped.

    `command` is what `trace.render` shows first: a shell command as it was
    run (a list, as Codex gives it, joined), or for any other tool a readable
    account of what it was asked -- a search's pattern and where, a read's
    part of a file -- so a call that is not a shell command is never shown by
    its name alone. The arguments are kept whole under `args`.
    """
    args = dict(args or {})
    call: dict = {"name": name}
    command = _first(args, _COMMAND)
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    path = _first(args, _PATH)
    old, new, body = _first(args, _OLD), _first(args, _NEW), _first(args, _BODY)
    # An editor's operation, not a shell command: OpenHands' str_replace_editor
    # passes "view", "create" or "str_replace" as `command` beside a path.
    operation = isinstance(command, str) and path is not None and command.strip() and not any(
        ch.isspace() for ch in command.strip())
    if operation:
        call["command"] = f"{command.strip()} {path}"
    elif isinstance(command, str) and command and not (old is not None or new is not None):
        call["command"] = command
    if path is not None:
        call["path"] = str(path)
    if old is not None or new is not None:
        call["old_text"], call["new_text"] = str(old or ""), str(new or "")
    elif body is not None and not isinstance(body, (dict, list)):
        call["content"] = str(body)
    if isinstance(args.get("edits"), list):   # several edits in one call
        call["old_text"] = "\n...\n".join(str(_first(e, _OLD) or "") for e in args["edits"] if isinstance(e, dict))
        call["new_text"] = "\n...\n".join(str(_first(e, _NEW) or "") for e in args["edits"] if isinstance(e, dict))
    if "command" not in call:
        # What else it was asked, after what it acted on: a read's part of a
        # file, a search's pattern and where.
        rest = {k: v for k, v in args.items() if k not in _PATH + _OLD + _NEW + _BODY + ("edits",) + _COMMAND}
        shown = ([str(path)] if path is not None else []) + ([json.dumps(rest, ensure_ascii=False)] if rest else [])
        if shown:
            call["command"] = " ".join(shown)
    call["args"] = args
    call["result"] = result
    return call


def _calls(trajectory: dict, via: str = "") -> list[dict]:
    steps = [s for s in trajectory.get("steps") or [] if not s.get("is_copied_context")]
    results: dict[str, str] = {}
    for s in steps:
        for r in (s.get("observation") or {}).get("results") or []:
            if r.get("source_call_id") is not None:
                results[str(r["source_call_id"])] = text_of(r.get("content"))
    out = []
    for s in steps:
        if s.get("source") != "agent":
            continue
        for c in s.get("tool_calls") or []:
            call = call_of(str(c.get("function_name") or "?"), c.get("arguments"),
                           results.get(str(c.get("tool_call_id")), ""))
            if via:
                call["name"] = f"{via}: {call['name']}"
            out.append(call)
    for sub in trajectory.get("subagent_trajectories") or []:
        name = ((sub.get("agent") or {}).get("name") or "subagent")
        out.extend(_calls(sub, via=f"subagent {name}"))
    return out


def record_of(trajectory: dict) -> tuple[str, list[dict]]:
    """The agent's answer and this attempt's calls, from its ATIF trajectory."""
    answer = ""
    for s in trajectory.get("steps") or []:
        if s.get("source") == "agent" and not s.get("is_copied_context"):
            text = text_of(s.get("message")).strip()
            if text:
                answer = text
    return answer, _calls(trajectory)
