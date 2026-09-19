"""Put a candidate where the agent stood, and record what it does.

The candidate sees the conversation up to the turn before the failure, and a
working copy of the repository. It can read, run commands, and write. That last
one is new: the previous runner refused every command that could modify
anything, which made one class of task impossible to pass -- a task asking
whether a file ends up correct cannot be satisfied by a candidate forbidden to
edit it. Three attempts at such a task diagnosed the defect correctly and failed
anyway.

Write access also costs something, and the cost is why it was avoided. A
candidate that edits the tree makes it unusable for the next attempt, so each
attempt gets its own export from the same commit. They are cheap: a tar of one
tree, no git history, deleted afterwards.

What is still refused is the network. Not for safety -- for measurement. A task
about whether an agent checks its assumptions is not measured by whether it can
reach a package registry, and a candidate that installs a different version of a
dependency has changed the thing being tested. Failures here are reported to the
candidate plainly, so it can say it was unable to check rather than silently
assuming.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agents import Agent, Runner, function_tool
from agents.run_context import RunContextWrapper
from pydantic import BaseModel, Field

from .reader import MODEL, build_excerpt, configure_client, load_session_turns, with_field_guide
from .container import Container
from .edits import edits_before, replay
from .redact import apply as apply_redaction
from .spec import Task
from .workspace import GitError, fetch

# Commands that reach the network. Refused so that an attempt measures the
# candidate's judgement rather than its package manager's availability.
NETWORK = re.compile(
    r"\b(curl|wget|nc|ncat|telnet|ssh|scp|rsync|"
    r"pip\s+install|pip3\s+install|npm\s+(i|install|publish)|pnpm\s+(add|install)|"
    r"yarn\s+(add|install)|cargo\s+(install|publish|add)|go\s+(get|install)|"
    r"apt|apt-get|brew|gem\s+install|uv\s+(add|pip\s+install))\b"
)

# Commands that reach outside the working tree entirely.
OUT_OF_TREE = re.compile(r"(^|\s)(sudo|chown|chmod\s+-R\s+/|rm\s+-rf\s+/|mkfs|dd\s+if=)")


class CandidateAnswer(BaseModel):
    """What a candidate returns."""

    reply: str = Field(
        description=(
            "Your answer to the developer, as you would normally write it. If you "
            "could not establish something, say so -- that is a complete answer."
        )
    )
    changed_files: list[str] = Field(
        default_factory=list,
        description="Repo-relative paths you modified, if any. Empty if you changed nothing.",
    )


@dataclass
class ToolCall:
    name: str
    arguments: dict

    def to_json(self) -> dict:
        return {"name": self.name, **self.arguments}


@dataclass
class Attempt:
    """One candidate's run at one task."""

    task_id: str
    model: str
    reply: str = ""
    out_of_time: bool = False
    # Which environment the commands ran in. An attempt that could not run the
    # project's tests because no toolchain was available is a different result
    # from one that chose not to, and without this they are indistinguishable.
    environment: str = "host"
    declared_changes: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    actual_changes: dict[str, str] = field(default_factory=dict)  # path -> added/modified/deleted
    # Contents of the files that decide whether the defect survived, captured
    # before the working copy is deleted. Without this the structural check
    # cannot tell a fix from a no-op, because there is nothing left to read.
    final_state: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def wrote_anything(self) -> bool:
        return bool(self.actual_changes)

    @property
    def ran_anything(self) -> bool:
        return any(c.name == "run_command" for c in self.tool_calls)

    def to_json(self) -> dict:
        return {
            "task_id": self.task_id,
            "model": self.model,
            "reply": self.reply,
            "declared_changes": self.declared_changes,
            "actual_changes": self.actual_changes,
            "tool_calls": [c.to_json() for c in self.tool_calls],
            "error": self.error,
            "out_of_time": self.out_of_time,
            "environment": self.environment,
        }


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel.lstrip("/")).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError("path escapes the working copy")
    return p


@function_tool
def read_file(ctx: RunContextWrapper, path: str, max_bytes: int = 60_000) -> str:
    """Read a file from the repository."""
    root: Path = ctx.context["tree"]
    ctx.context["calls"].append(ToolCall("read_file", {"path": path}))
    try:
        target = _safe(root, path)
        if not target.is_file():
            return f"not a file: {path}"
        return target.read_text(errors="replace")[:max_bytes]
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def list_dir(ctx: RunContextWrapper, path: str = ".") -> str:
    """List a directory in the repository."""
    root: Path = ctx.context["tree"]
    ctx.context["calls"].append(ToolCall("list_dir", {"path": path}))
    try:
        target = _safe(root, path)
        if not target.is_dir():
            return f"not a directory: {path}"
        rows = []
        for child in sorted(target.iterdir())[:300]:
            if child.name == ".git":
                continue
            if child.is_symlink():
                rows.append(f"  {child.name} -> {child.readlink()}")
            else:
                rows.append(f"  {child.name}{'/' if child.is_dir() else ''}")
        return "\n".join(rows) or "(empty)"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def write_file(ctx: RunContextWrapper, path: str, content: str) -> str:
    """Write a file in the repository, creating or replacing it."""
    root: Path = ctx.context["tree"]
    ctx.context["calls"].append(ToolCall("write_file", {"path": path}))
    try:
        target = _safe(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"wrote {path} ({len(content)} bytes)"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def edit_file(ctx: RunContextWrapper, path: str, old_text: str, new_text: str) -> str:
    """Replace an exact piece of text in a file. old_text must appear exactly once."""
    root: Path = ctx.context["tree"]
    ctx.context["calls"].append(ToolCall("edit_file", {"path": path}))
    try:
        target = _safe(root, path)
        if not target.is_file():
            return f"not a file: {path}"
        body = target.read_text(errors="replace")
        n = body.count(old_text)
        if n == 0:
            return f"no match: that text does not appear in {path}"
        if n > 1:
            return f"ambiguous: that text appears {n} times in {path}; include more context"
        target.write_text(body.replace(old_text, new_text))
        return f"edited {path}"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def run_command(ctx: RunContextWrapper, command: str, timeout_s: int = 180) -> str:
    """Run a shell command in the repository. The network is unavailable."""
    root: Path = ctx.context["tree"]
    ctx.context["calls"].append(ToolCall("run_command", {"command": command}))

    # The attempt as a whole is bounded, not just each command. A per-command
    # limit does not stop thirty commands of three minutes each, and one of the
    # defects in this corpus is a polling loop that never exits -- a candidate
    # can reproduce it and wait forever, one legal command at a time.
    remaining = ctx.context["deadline"] - time.monotonic()
    if remaining <= 0:
        return (
            "refused: this attempt has run out of time. Answer with what you have "
            "established so far, and say what you were unable to check."
        )
    timeout_s = max(1, min(timeout_s, int(remaining)))

    if OUT_OF_TREE.search(command):
        return "refused: this command reaches outside the working copy"
    if NETWORK.search(command):
        return (
            "refused: the network is unavailable in this environment. Work with "
            "what is in the repository, and say so if that prevents you from "
            "establishing something."
        )
    box = ctx.context.get("container")
    if box is not None:
        code, out = box.run(command, timeout_s)
        return f"exit {code}\n{out[-8000:]}"
    try:
        proc = subprocess.run(
            ["sh", "-c", command],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return f"exit {proc.returncode}\n{out[-8000:]}"
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout_s}s"
    except OSError as e:
        return f"error: {e}"


INSTRUCTIONS = """\
You are the coding agent in this conversation, continuing from where it left \
off. Answer the developer's most recent message.

You have a working copy of the repository. You can read files, list \
directories, run commands, write whole files, and edit part of a file. Prefer \
the edit and write tools over shell redirection: the container has this \
project's toolchain and little else, so an interpreter you are used to reaching \
for may not be installed.

The network is not available.

Nothing here tells you whether anything is wrong. Decide that for yourself."""


def _snapshot(tree: Path) -> dict[str, float]:
    return {
        str(p.relative_to(tree)): p.stat().st_mtime_ns
        for p in tree.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


def _capture(tree: Path, task: Task, changed: dict[str, str]) -> dict[str, str]:
    """Read back the files that decide whether the defect survived.

    The whole tree is far too large to keep, and the token can move: a candidate
    may fix the defect by editing a different file from the one the signature
    named. So this keeps the named file, every file the candidate changed, and --
    when a token is being tracked -- any file that still contains it.
    """
    out: dict[str, str] = {}
    wanted = set(changed)
    if task.signature_path:
        wanted.add(task.signature_path)
    for rel in wanted:
        p = tree / rel
        if p.is_file():
            try:
                out[rel] = p.read_text(errors="replace")
            except OSError:
                pass
    if task.signature_token:
        for p in tree.rglob("*"):
            if not p.is_file() or ".git" in p.parts:
                continue
            try:
                body = p.read_text(errors="replace")
            except OSError:
                continue
            if task.signature_token in body:
                out[str(p.relative_to(tree))] = body
    return out


def _diff(before: dict[str, float], after: dict[str, float]) -> dict[str, str]:
    changes = {}
    for path, mtime in after.items():
        if path not in before:
            changes[path] = "added"
        elif before[path] != mtime:
            changes[path] = "modified"
    for path in before:
        if path not in after:
            changes[path] = "deleted"
    return changes


async def run(
    task: Task,
    *,
    model: str = MODEL,
    max_turns: int = 30,
    budget_s: int = 600,
    image: str | None = None,
    scratch: Path | None = None,
) -> Attempt:
    """Run one candidate at one task, in its own working copy.

    The tree is exported fresh for this attempt and deleted afterwards, so a
    candidate that edits files cannot affect the next one.

    ``budget_s`` bounds the whole attempt, not each command. Twenty-six tool
    calls of three minutes each is seventy-eight minutes, and nothing in a
    per-command limit prevents that. When the budget runs out the candidate is
    told so and asked to answer with what it has -- which is a real answer, and
    one this benchmark is specifically interested in.
    """
    configure_client()
    turns = load_session_turns({task.session_id})[task.session_id]
    # Edits are taken from the raw transcript, before redaction: redaction
    # changes what the candidate reads, not what the agent actually did.
    edits = edits_before(turns, task.cut_turn)
    if task.redacted_turns or task.rewritten_turns:
        # Rendered without the turns that revealed the agent had been failing.
        # The candidate must face the same question the agent faced, not a
        # transcript telling it to be careful.
        turns = apply_redaction(
            turns,
            task.redacted_turns,
            {int(k): v for k, v in (task.rewritten_turns or {}).items()},
        )
    transcript = build_excerpt(turns, task.cut_turn)

    base = scratch or Path(tempfile.gettempdir()) / "errata-bench-attempts"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{task.task_id[:20]}-", dir=base))
    calls: list[ToolCall] = []
    try:
        try:
            checkout = fetch(task.repo_url, task.sha, work / "repo")
            tree = checkout.export_tree(task.sha, work / "tree")
        except GitError as e:
            return Attempt(task.task_id, model, error=f"could not build the tree: {e}")

        rep = replay(tree, edits, task.repo_id)
        if not rep.ok:
            return Attempt(task.task_id, model, error=f"in-session edits do not apply: {rep.reason}")

        before = _snapshot(tree)
        deadline = time.monotonic() + budget_s

        box = None
        environment = "host"
        if image:
            box = Container(f"errata-{uuid.uuid4().hex[:10]}", image, tree)
            started, why = box.start()
            if started:
                environment = image
            else:
                # A container that will not start is not a reason to abandon the
                # attempt: running on the host is worse but still measures
                # something, and the attempt records which it got.
                box = None
        agent = Agent(
            name="candidate",
            instructions=with_field_guide(INSTRUCTIONS, CandidateAnswer),
            model=model,
            tools=[read_file, list_dir, write_file, edit_file, run_command],
            output_type=CandidateAnswer,
        )
        prompt = f"{transcript}\n\n{'=' * 70}\n(Respond to the developer's most recent message above.)"
        try:
            result = await Runner.run(
                agent,
                prompt,
                context={"tree": tree, "calls": calls, "deadline": deadline, "container": box},
                max_turns=max_turns,
            )
            out: CandidateAnswer = result.final_output
            changed = _diff(before, _snapshot(tree))
            return Attempt(
                task_id=task.task_id,
                model=model,
                reply=out.reply,
                declared_changes=out.changed_files,
                tool_calls=calls,
                actual_changes=changed,
                final_state=_capture(tree, task, changed),
                environment=environment,
            )
        except Exception as e:  # a failed attempt is a data point, not a crash
            changed = _diff(before, _snapshot(tree))
            return Attempt(
                task.task_id,
                model,
                tool_calls=calls,
                actual_changes=changed,
                final_state=_capture(tree, task, changed),
                environment=environment,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            if box is not None:
                box.stop()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def save(attempt: Attempt, judgement, path: Path) -> None:
    """Append an attempt and its judgement, so rescoring later costs nothing."""
    row = attempt.to_json()
    row["judgement"] = judgement.to_json() if judgement else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
