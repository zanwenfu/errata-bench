"""Replay the agent's in-session edits onto the base tree, up to the cut.

The candidate's tree is built from the last commit before the session began.
Its transcript describes everything the agent did after that, up to the cut --
including edits. Seven of twelve calibrated tasks have such edits, one with
thirty-four across thirteen files. The transcript says the work exists; the
tree says it does not. One candidate put it plainly: "This checkout uses
/api/sessions/spawn, not the earlier embedded-terminal workflow." It was then
scored off-target three times for testing the code it had rather than the code
it was told about.

So the edits are replayed. Every Edit, Write and MultiEdit call before the cut
is applied in order, with the semantics the agent's own tool had. An edit that
does not apply -- old_string not found, path outside the repository -- means
the base commit is not what the agent was working on, and the task is rejected
rather than shipped with a tree that is half one thing and half another.

Only edits before the cut are replayed. The failing answer and everything after
it are exactly what the candidate must not see.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .timeline import to_repo_relative

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

# What this cannot reconstruct: the agent changing the tree by other means.
# ravencloak-org/ravencloak runs `git checkout main && git merge
# feat/frontend-catalyst-redesign`, then `git pull`, `git commit` and `git push`
# before its cut, so the file its next edit targets arrived from a branch that
# was merged mid-session and is in no single commit we can check out. Twelve
# percent of screened sessions run tree-mutating git commands before the cut.
#
# Those tasks are rejected rather than approximated. A replay that silently
# skipped the unreconstructable part would hand a candidate a tree that is
# neither the base commit nor what the agent saw, which is the failure this
# module exists to prevent. None of the six calibrated tasks are affected.


@dataclass
class Replay:
    """What was applied, and if it stopped, why."""

    applied: int = 0
    files: set[str] = field(default_factory=set)
    failed_at: int | None = None  # turn number
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.failed_at is None


def edits_before(turns: list[dict], cut_turn: int) -> list[dict]:
    """The agent's edit calls up to and including the cut, in order."""
    out = []
    for t in sorted(turns, key=lambda t: t.get("turn_number") or 0):
        if (t.get("turn_number") or 0) > cut_turn:
            break
        if t.get("turn_type") != "tool_use" or t.get("tool_name") not in EDIT_TOOLS:
            continue
        try:
            args = json.loads(t.get("content") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(args, dict):
            out.append({"turn": t.get("turn_number"), "tool": t.get("tool_name"), "args": args})
    return out


def _target(tree: Path, local_path: str, repo_id: str) -> Path | None:
    rel = to_repo_relative(local_path or "", repo_id)
    if not rel:
        return None
    p = (tree / rel).resolve()
    if not str(p).startswith(str(tree.resolve())):
        return None
    return p


def _edit(path: Path, old: str, new: str, replace_all: bool) -> str | None:
    """One Edit, with the tool's own rules. Returns a reason on failure."""
    if not path.is_file():
        if old == "":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new)
            return None
        return "file does not exist in the tree"
    body = path.read_text(errors="replace")
    if old == "":
        return "empty old_string on an existing file"
    n = body.count(old)
    if n == 0:
        return "old_string not found -- the base commit differs from what the agent edited"
    if n > 1 and not replace_all:
        return f"old_string appears {n} times and replace_all is false"
    path.write_text(body.replace(old, new) if replace_all else body.replace(old, new, 1))
    return None


def replay(tree: Path, edits: list[dict], repo_id: str) -> Replay:
    """Apply the edits in order. Stops at the first that does not apply."""
    r = Replay()
    for e in edits:
        args = e["args"]
        target = _target(tree, args.get("file_path", ""), repo_id)
        if target is None:
            r.failed_at = e["turn"]
            r.reason = f"turn {e['turn']}: path {args.get('file_path','')!r} is not inside the repository"
            return r
        rel = str(target.relative_to(tree.resolve()))
        why: str | None = None
        if e["tool"] == "Write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args.get("content") or "")
        elif e["tool"] == "Edit":
            why = _edit(target, args.get("old_string") or "", args.get("new_string") or "",
                        bool(args.get("replace_all")))
        elif e["tool"] == "MultiEdit":
            for sub in args.get("edits") or []:
                why = _edit(target, sub.get("old_string") or "", sub.get("new_string") or "",
                            bool(sub.get("replace_all")))
                if why:
                    break
        if why:
            r.failed_at = e["turn"]
            r.reason = f"turn {e['turn']} ({e['tool']} {rel}): {why}"
            return r
        r.applied += 1
        r.files.add(rel)
    return r
