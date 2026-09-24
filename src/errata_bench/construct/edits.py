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
rather than shipped with a tree that is half one thing and half another. The
exception is a file that belongs to the agent's machine rather than to any
repository (`OUTSIDE`): its plans, its memory, a scratch file. Nothing in the
tree could hold it, so it is skipped and recorded, not held against the commit.

Only edits before the cut are replayed. The failing answer and everything after
it are exactly what the candidate must not see.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..corpus.timeline import to_repo_relative

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

# Files of the agent's machine, never of a repository: the user's ~/.claude
# (Claude Code writes its plans to ~/.claude/plans/ and its memory to
# ~/.claude/projects/), and the temporary directories. balkhaev/yep was
# rejected because its agent wrote a plan to
# /Users/balkhaev/.claude/plans/quizzical-forging-robin.md before the cut, and
# of the 2,137 sessions of the next batches whose working directory is known,
# 159 edit such a path before their moment: plans 222 edits, memory 76, skills,
# settings and scratch files the rest. Deliberately narrow. A project's own
# .claude/ directory is inside its checkout and is mapped like any other path
# before this is consulted, and a path elsewhere -- another checkout, a
# worktree under another name -- still rejects, because it may be this
# repository under a name the replay cannot place.
OUTSIDE = re.compile(r"^(?:/(?:Users|home)/[^/]+|/root|[A-Za-z]:/Users/[^/]+)/\.claude/"
                     r"|^/(?:private/)?(?:tmp|var/folders)/")

# Tools that change files and that the replay does not read. Claude Code driven
# through Zed's ACP adapter names its tools mcp__acp__Edit, Write and Bash, with
# Claude Code's own arguments: 7 sessions, and e1b2ec72 has 28 such writes
# before its moment, none of which a tree built today would contain, while the
# replay reports success. With them: a notebook edit, serena's symbol edits,
# GitButler moving branches, and the other agents' editors (OpenCode's
# apply_patch/edit/write, Gemini's replace/write_file), whose sessions the
# timestamp gate stops first. A session that used one before the cut is
# rejected: its tree is neither the commit nor what the agent had.
UNREPLAYED = re.compile(
    r"^(?:NotebookEdit|mcp__acp__(?:Edit|Write|MultiEdit|Bash)|apply_patch|edit|write|patch|replace"
    r"|write_file|edit_file|create_file|str_replace_editor|str_replace_based_edit_tool)$"
    r"|__(?:replace_symbol_body|insert_after_symbol|insert_before_symbol|replace_regex|replace_content"
    r"|create_text_file|delete_lines|replace_lines|insert_at_line)$"
    r"|^mcp__gitbutler__")


def unreplayed_writes(turns: list[dict], cut_turn: int) -> list[str]:
    """The tools, up to the cut, that changed files in a way the replay does not reproduce."""
    return sorted({t.get("tool_name") or "" for t in turns
                   if t.get("turn_type") == "tool_use" and (t.get("turn_number") or 0) <= cut_turn
                   and UNREPLAYED.search(t.get("tool_name") or "")})


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
    # Of the hunks that applied, how many could have failed on the wrong base
    # commit: an old_string matched against content the checkout already had.
    # `applied` counts calls, and a Write cannot fail -- it overwrites whatever
    # is there, or creates it -- so a replay of nothing but Writes reports
    # applied=4, ok=True and has examined no part of the tree. Nor can an Edit
    # that matches what a Write earlier in the same replay just put there:
    # dipasqualew-vibereq-162 replays 13 calls, of which 4 are Writes and 2 are
    # Edits onto `apps/cli/src/commands/pr.ts`, a file the Write at turn 58
    # created. Six of its 13 test nothing about the commit. That is G-37's
    # "certifying trees it has barely examined", and this is the number that
    # says how much was really examined.
    verified: int = 0
    files: set[str] = field(default_factory=set)
    # Edits to the agent's own files (`OUTSIDE`), skipped rather than applied.
    outside: list[str] = field(default_factory=list)
    failed_at: int | None = None  # turn number
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.failed_at is None

    @property
    def tests_the_base_commit(self) -> bool:
        """Whether this replay could have failed on the wrong commit at all.

        A replay with nothing to apply is vacuously fine; one that applied
        something and verified none of it proves only that the paths resolved.
        """
        return self.applied == 0 or self.verified > 0


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


def _inside(tree: Path, rel: str) -> Path | None:
    """The path `rel` names inside `tree`, or None if it escapes it."""
    p = (tree / rel).resolve()
    root = tree.resolve()
    return p if p == root or root in p.parents else None


def _checkout_root(tree: Path, paths: list[str]) -> tuple[str, ...] | None:
    """How much of the agent's absolute paths is the machine it worked on.

    `to_repo_relative` assumes the developer's local directory is named after
    the repository, and a quarter of the replay failures are that assumption:
    `light-protocol3` for `Lightprotocol/light-protocol`, a checkout still
    called `savanna` after the repository was renamed to `savanna-vet-go`, and
    a git worktree under `.claude/worktrees/<name>/`. None of them is a bad
    task; all of them are this function's guess.

    The tree itself is the better evidence. Any suffix of a recorded path that
    exists in the exported tree tells us where the checkout began, so the
    prefix is measured once from the paths that resolve and then applied to the
    rest -- including files the session creates, which exist nowhere yet and
    so cannot be resolved on their own.

    This is the original election, restored on 09-21 after two rewrites. The
    first counted every candidate prefix and preferred the longer on a tie; the
    second let only Edit paths vote and elected nothing on a tie, falling
    through to the repository's name. Both were tested against every one of
    the 73,549 edit calls in the corpus beside this version: this one is right
    in every real shape but one -- a monorepo holding the same basename at its
    root and inside a package, where the shorter prefix wins a tie and lands
    one level too shallow (G-55). The rewrites each traded that one shape for
    silent misplacement across the 1,321 sessions that open with a Write, or
    rejection of the very cases named above. The one shape stays open, with
    the better rule recorded in G-55, until a change can be tested the way the
    rewrites were and not before.
    """
    votes: dict[tuple[str, ...], int] = {}
    for local in paths:
        parts = tuple(PurePosixPath(local).parts)
        for i in range(len(parts)):
            if _inside(tree, str(Path(*parts[i:]))) and (tree / Path(*parts[i:])).exists():
                votes[parts[:i]] = votes.get(parts[:i], 0) + 1
                break
    if not votes:
        return None
    # The prefix the most paths agree on: one file living outside the checkout
    # should not decide where the checkout is.
    return max(votes.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


def _target(tree: Path, local_path: str, repo_id: str,
            root: tuple[str, ...] | None = None) -> Path | None:
    if root is not None:
        parts = tuple(PurePosixPath(local_path or "").parts)
        if parts[:len(root)] == root and len(parts) > len(root):
            return _inside(tree, str(Path(*parts[len(root):])))
    rel = to_repo_relative(local_path or "", repo_id)
    if not rel:
        return None
    return _inside(tree, rel)


def _read(path: Path) -> str:
    """The file's bytes as text, losing nothing and changing nothing.

    Not `read_text`. Text mode translates CRLF to LF on the way in, and
    `errors="replace"` turns every undecodable byte into U+FFFD, so a file read
    and written back came out different from the one the agent edited: a CRLF
    batch file lost its line endings throughout, and `café` in latin-1 became
    `caf�`. Both are silent -- the replay reports ok -- and the first also
    rejects the *next* edit in the session, whose old_string still contains the
    \\r\\n that is no longer there.

    `surrogateescape` round-trips any byte exactly, and reading bytes avoids
    newline translation entirely.
    """
    return path.read_bytes().decode("utf-8", "surrogateescape")


def _write(path: Path, body: str) -> None:
    """The counterpart to `_read`: back to the same bytes."""
    path.write_bytes(body.encode("utf-8", "surrogateescape"))


def _edit(path: Path, old: str, new: str, replace_all: bool) -> str | None:
    """One Edit, with the tool's own rules. Returns a reason on failure."""
    if not path.is_file():
        if old == "":
            path.parent.mkdir(parents=True, exist_ok=True)
            _write(path, new)
            return None
        return "file does not exist in the tree"
    body = _read(path)
    if old == "":
        return "empty old_string on an existing file"
    n = body.count(old)
    if n == 0:
        return "old_string not found -- the base commit differs from what the agent edited"
    if n > 1 and not replace_all:
        return f"old_string appears {n} times and replace_all is false"
    _write(path, body.replace(old, new) if replace_all else body.replace(old, new, 1))
    return None


def replay(tree: Path, edits: list[dict], repo_id: str) -> Replay:
    """Apply the edits in order. Stops at the first that does not apply."""
    r = Replay()
    # Where the developer's checkout began, measured from the tree rather than
    # guessed from the directory name.
    root = _checkout_root(tree, [e["args"].get("file_path", "") for e in edits])
    # Files this replay created or overwrote itself. An old_string that matches
    # content a Write two turns ago put there is checking the replay, not the
    # commit, so it is applied like any other and counted as evidence of
    # nothing.
    ours: set[str] = set()
    for e in edits:
        args = e["args"]
        target = _target(tree, args.get("file_path", ""), repo_id, root)
        if target is None and OUTSIDE.match(args.get("file_path") or ""):
            r.outside.append(args.get("file_path") or "")
            continue
        if target is None:
            r.failed_at = e["turn"]
            r.reason = f"turn {e['turn']}: path {args.get('file_path','')!r} is not inside the repository"
            return r
        rel = str(target.relative_to(tree.resolve()))
        # Read before anything is applied: from here on the file is partly ours.
        from_base = target.is_file() and rel not in ours
        why: str | None = None
        if e["tool"] == "Write":
            target.parent.mkdir(parents=True, exist_ok=True)
            _write(target, args.get("content") or "")
            ours.add(rel)
        elif e["tool"] == "Edit":
            old = args.get("old_string") or ""
            why = _edit(target, old, args.get("new_string") or "",
                        bool(args.get("replace_all")))
            if why is None:
                r.verified += 1 if (old and from_base) else 0
                if not old:
                    ours.add(rel)
        elif e["tool"] == "MultiEdit":
            for sub in args.get("edits") or []:
                old = sub.get("old_string") or ""
                why = _edit(target, old, sub.get("new_string") or "",
                            bool(sub.get("replace_all")))
                if why:
                    break
                r.verified += 1 if (old and from_base) else 0
                if not old:
                    ours.add(rel)
        if why:
            r.failed_at = e["turn"]
            r.reason = f"turn {e['turn']} ({e['tool']} {rel}): {why}"
            return r
        r.applied += 1
        r.files.add(rel)
    return r
