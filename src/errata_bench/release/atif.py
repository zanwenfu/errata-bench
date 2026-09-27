"""A Harbor trajectory (ATIF) read into the record the graders read (v1 step 3).

Harbor runs 40+ agents, and most write their session as ATIF: steps from the
user or the agent, each agent step with its tool calls, and an observation
holding each call's result by the call's id (`harbor.models.trajectories`).
The graders read a list of calls in this harness's own shape -- a name, what
the call acted on (`command` or `path`), what an edit replaced and with what,
what a write wrote, and the result -- rendered by `trace.render`. This reads the
one into the other and keeps everything: every call, all of its arguments and
all of its result, as the agent received it.

What is left out, and why (read from Harbor's source, 09-27):

  - the seeded conversation. A task that gives an agent the developer's
    conversation as its own history (a task's trajectory.json) gets it back
    in the agent's trajectory, and it is not this attempt's work; the graders
    are given the conversation separately. Some agents mark those steps
    `is_copied_context`; Claude Code and Codex do not, so with ``instruction``
    given, only the steps after the last user step carrying it are read (an
    agent may add to the instruction it was given, so a step carries it when
    its message holds it, spacing aside).
  - nothing else. A subagent's calls are the agent's work too: those in
    `subagent_trajectories`, and Claude Code's, which sit in the main list
    marked `extra.is_sidechain`, are kept and named as a subagent's.

A result is the text the agent received. Claude Code's conversion appends
sections of its own to it -- "[stdout]", "[exit_code]", "[metadata] {...}" --
and keeps the exact text beside it in `extra.tool_result_metadata.
raw_tool_result`, which is read when it is there.

A call is one call however often it is written: Harbor's OpenHands conversion
gives every event a step, and the event holding a call's result carries the
call again, under the same id; it is read once.

The answer is what the main agent (not a subagent) said to end its work: its
last step, when that is a message with no tool call beside it -- how Claude
Code, Codex and Gemini CLI end -- or a `finish` call, how OpenHands ends, whose
message is the answer. When its last step is any other call, it stopped before
it replied (a time limit, a crash, a turn limit), and there is no answer: an
earlier message was said on the way, not to end the work.
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


def _sidechain(step: dict) -> bool:
    return bool((step.get("extra") or {}).get("is_sidechain"))


def _result_text(r: dict) -> str:
    """What the agent received: Claude Code's exact text when its conversion kept it, else the content."""
    raw = ((r.get("extra") or {}).get("tool_result_metadata") or {}).get("raw_tool_result")
    if isinstance(raw, dict) and "content" in raw:
        return text_of(raw.get("content"))
    return text_of(r.get("content"))


def instruction_at(steps: list[dict], instruction: str) -> int | None:
    """Where the last user step carrying the instruction is, or None when none does."""
    want = " ".join(instruction.split())
    marks = [i for i, s in enumerate(steps)
             if want and s.get("source") == "user" and want in " ".join(text_of(s.get("message")).split())]
    return marks[-1] if marks else None


def attempt_steps(trajectory: dict, instruction: str | None = None) -> list[dict]:
    """The steps of this attempt: not copied context, and after the instruction when it is given."""
    steps = [s for s in trajectory.get("steps") or [] if not s.get("is_copied_context")]
    at = instruction_at(steps, instruction) if instruction else None
    return steps if at is None else steps[at + 1:]


def _calls(trajectory: dict, via: str = "", instruction: str | None = None) -> list[dict]:
    steps = attempt_steps(trajectory, instruction)
    results: dict[str, str] = {}
    for s in steps:
        for r in (s.get("observation") or {}).get("results") or []:
            if r.get("source_call_id") is not None:
                results[str(r["source_call_id"])] = _result_text(r)
    out, seen = [], set()
    for s in steps:
        if s.get("source") != "agent":
            continue
        for c in s.get("tool_calls") or []:
            key = str(c.get("tool_call_id") or "")
            if key and key in seen:
                continue
            seen.add(key)
            call = call_of(str(c.get("function_name") or "?"), c.get("arguments"),
                           results.get(key, ""))
            who = via or ("subagent" if _sidechain(s) else "")
            if who:
                call["name"] = f"{who}: {call['name']}"
            out.append(call)
    for sub in trajectory.get("subagent_trajectories") or []:
        name = ((sub.get("agent") or {}).get("name") or "subagent")
        out.extend(_calls(sub, via=f"subagent {name}"))
    return out


def _finish_message(call: dict) -> str | None:
    """What a `finish` call says (OpenHands ends its work with one); None for any other call."""
    if str(call.get("function_name") or "").lower() != "finish":
        return None
    args = call.get("arguments")
    said = args.get("message") if isinstance(args, dict) else None
    return said.strip() if isinstance(said, str) else ""


def final_reply(steps: list[dict]) -> str:
    """What the main agent said to end its work, or "" when its last step was a call that is not `finish`."""
    for s in reversed(steps):
        if s.get("source") != "agent" or _sidechain(s):
            continue
        text = text_of(s.get("message")).strip()
        calls = s.get("tool_calls") or []
        finished = [m for m in map(_finish_message, calls) if m is not None]
        if finished:
            return next((m for m in finished if m), text)
        if calls:
            return ""
        if text:
            return text
    return ""


def record_of(trajectory: dict, instruction: str | None = None) -> tuple[str, list[dict]]:
    """The agent's answer and this attempt's calls, from its ATIF trajectory.

    ``instruction`` is the task's instruction as the agent received it: given,
    only what came after the last user step carrying it is this attempt's.
    """
    return final_reply(attempt_steps(trajectory, instruction)), _calls(trajectory, instruction=instruction)
