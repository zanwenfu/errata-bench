"""Does the rebuilt tree agree with what the conversation showed of it? (D-36 A5, G-71)

A task's tree is the last commit before the session with the agent's own file
edits replayed. Nothing else is replayed -- not what its commands did, not a
branch it was on -- so the container can hold something other than what the
conversation says the repository held. An agent that checks then finds the
opposite of what the conversation told it, and the benchmark scores the check.

SWE-chat has no per-turn snapshots to rebuild from (only 1 of the 21 grid
sessions has a commit before its cut), so this measures instead. Three things,
each from the conversation up to the cut:

  lines     every line a Read showed, against the same line of the rebuilt
            file. Only a file's last read before the cut is compared, and only
            if no edit to that file followed it: a replayed edit changes the
            file legitimately.
  head      a commit printed by `git log` or `git rev-parse`, against the
            commit the tree was built from.
  commands  commands that change state outside the files the replay knows
            about -- installs, version bumps, git history, containers. Listed,
            not judged: whether one matters depends on the task.
  lost      edits the agent made before the cut that the corpus table lost
            (G-76): of a batch of parallel calls it keeps only the last, so
            the replay never saw the others and the tree lacks them. Measured
            only when the record recovered from the raw transcript is given.

A task is `consistent` when no compared line differs, no printed head
contradicts the base, and no edit before the cut was lost. Whether an inconsistent task stays in the benchmark is a
decision, not this module's.
"""

from __future__ import annotations

import re
from pathlib import Path

READ_TOOLS = frozenset({"Read", "read_file"})
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit", "edit_file", "write_file"})
SHELL_TOOLS = frozenset({"Bash", "run_command"})
# Claude Code's Read output: "    12→text", or "12\ttext" in older sessions.
LINE = re.compile(r"^\s*(\d+)(?:→|\t)(.*)$")
SHA = re.compile(r"\b([0-9a-f]{7,40})\b")
# Near the length at which conversations.parquet cuts a tool result.
RESULT_CAP = 9_900
# Commands whose effects the replay does not reproduce. Deliberately broad: the
# list is reported, and a false entry costs a reader a glance.
MUTATING = re.compile(
    r"\b(npm|pnpm|yarn|bun)\s+(install|i|add|remove|run\s+bump|version|link)\b"
    r"|\bpip\s+install\b|\buv\s+(add|sync|pip)\b|\bcargo\s+(install|add)\b|\bgo\s+(get|install)\b"
    r"|\bgit\s+(commit|checkout|switch|pull|merge|rebase|reset|stash|cherry-pick|am|apply|restore|clean|tag)\b"
    r"|\bsed\s+-i\b|\bprettier\b.*--write|\beslint\b.*--fix|\bgofmt\s+-w\b|\bblack\b"
    r"|\b(rm|mv|cp|mkdir|touch|chmod|ln)\s|\btee\b|>\s*(?!/dev/null)[\w./]"
    r"|\bdocker\b|\bdocker-compose\b|\bgh\s|\bnvm\s+(install|use)\b|\bvolta\b|\bbrew\s+install\b"
    r"|\bbump\b"
)


# Git commands that change the files. The replay reproduces edits and nothing
# else, so a session that ran one of these before the cut has a tree that no
# commit and list of edits can rebuild (B-239: documented as rejected since the
# replay was written, and never implemented).
GIT_TREE = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(checkout|switch|pull|merge|rebase|reset|stash|cherry-pick|am|apply"
    r"|restore|clean|revert)\b([^|;&]*)")


def _changes_tree(verb: str, rest: str) -> bool:
    """Whether one git invocation changes the working tree, not just refs or the index."""
    words = rest.split()
    if verb == "stash" and words[:1] in (["list"], ["show"]):
        return False
    if (verb in ("checkout", "switch") and words[:1] and len(words) <= 2
            and words[0] in ("-b", "-B", "-c", "-C", "--create", "--orphan")):
        return False   # a new branch where HEAD already is
    if verb == "reset" and not any(w in ("--hard", "--merge", "--keep") for w in words):
        return False   # the index, not the files
    if verb == "restore" and "--staged" in words and not {"--worktree", "-W"} & set(words):
        return False
    return True


# SWE-chat replaced what its secret scanners flagged with placeholders -- in
# 45,627 rows, mostly a bare REDACTED, often over long hashes and ids. A
# placeholder stands for the text it replaced, so it matches whatever the tree
# holds there. Read literally, one hid an IPFS hash in oozoofrog-108's
# chronology_unicode.md and the build rejected a tree that was right.
_PLACEHOLDER = re.compile(r"<TRUFFLEHOG_REDACTED_[A-Z_]+>|\[REDACTED(?:_[A-Z_]+)?\]|REDACTED")


def same_line(shown: str, held: str) -> bool:
    """Whether a line the conversation showed is the line the tree holds."""
    shown, held = shown.rstrip(), held.rstrip()
    if shown == held:
        return True
    if "REDACTED" not in shown:
        return False
    pattern = ".+?".join(re.escape(part) for part in _PLACEHOLDER.split(shown))
    return re.fullmatch(pattern, held, flags=re.S) is not None


def _turns_until(turns: list[dict], cut: int) -> list[dict]:
    kept = [t for t in turns if t.get("turn_number") is not None and t["turn_number"] <= cut]
    return sorted(kept, key=lambda t: t["turn_number"])


def _results_by_call(turns: list[dict]) -> dict[int, str]:
    """Each tool call's result, keyed by the call's position in `turns`.

    By `tool_call_id` where both sides carry one. Parallel calls return their
    results after all of the calls, so pairing a call with the next result gave
    one file's read the content of another: a Python file "shown" holding React
    code. Without ids, the k-th call of a run of calls is paired with the k-th
    result of the run of results that follows it.
    """
    by_id = {str(t["tool_call_id"]): str(t.get("content") or "") for t in turns
             if t.get("turn_type") == "tool_result" and t.get("tool_call_id")}
    out: dict[int, str] = {}
    pending: list[int] = []
    for i, t in enumerate(turns):
        kind = t.get("turn_type")
        if kind == "tool_use":
            if t.get("tool_call_id") and str(t["tool_call_id"]) in by_id:
                out[i] = by_id[str(t["tool_call_id"])]
            elif not t.get("tool_call_id"):
                pending.append(i)
        elif kind == "tool_result" and not t.get("tool_call_id") and pending:
            out[pending.pop(0)] = str(t.get("content") or "")
        elif kind not in ("tool_use", "tool_result"):
            pending = []
    return out


def relative(path: str, tree: Path) -> str | None:
    """The developer's absolute path as a path in the tree: its longest suffix that exists."""
    parts = [p for p in Path(path).parts if p not in ("/", "")]
    for i in range(len(parts)):
        candidate = Path(*parts[i:])
        if (tree / candidate).is_file():
            return str(candidate)
    return None


def observed_lines(turns: list[dict], cut: int) -> dict[str, dict[int, str]]:
    """What each file's last read before the cut showed, line by line.

    A file edited after its last read is dropped: the replay applies that
    edit, so the difference would be the agent's own work, not a fault.
    """
    shown = _turns_until(turns, cut)
    results = _results_by_call(shown)
    last_read: dict[str, tuple[int, dict[int, str]]] = {}
    last_edit: dict[str, int] = {}
    for i, t in enumerate(shown):
        if t.get("turn_type") != "tool_use":
            continue
        tool, path = t.get("tool_name") or "", t.get("file_path") or ""
        if not path:
            continue
        if tool in EDIT_TOOLS:
            last_edit[path] = t["turn_number"]
        elif tool in READ_TOOLS:
            result = results.get(i, "")
            lines = {}
            for raw in result.splitlines():
                m = LINE.match(raw)
                if m:
                    lines[int(m.group(1))] = m.group(2)
            # The corpus keeps about 10 KB of a tool result, so a long read's
            # last line can be cut mid-line; compared, it would differ falsely.
            if len(result) >= RESULT_CAP and lines:
                lines.pop(max(lines))
            if lines:
                last_read[path] = (t["turn_number"], lines)
    return {path: lines for path, (at, lines) in last_read.items()
            if last_edit.get(path, -1) < at}


def _prints_head(cmd: str) -> bool:
    """Whether a command's first printed commit is HEAD.

    `git rev-parse HEAD`, or `git log` with nothing but flags or a range that
    ends at HEAD. `git log -- path` starts with the last commit to touch that
    path, and `git log other-branch` with that branch: either would read as a
    contradiction of the base that is not one.
    """
    m = re.search(r"\bgit\s+rev-parse\s+(--short\s+)?HEAD\b", cmd)
    if m:
        return True
    m = re.search(r"\bgit\s+log\b([^|;&]*)", cmd)
    if not m:
        return False
    args = m.group(1).split()
    return all(a.startswith("-") or a.isdigit() or a.endswith("HEAD") for a in args)


def printed_heads(turns: list[dict], cut: int) -> list[str]:
    """Commits that `git log` or `git rev-parse` printed first, before the cut."""
    shown = _turns_until(turns, cut)
    results = _results_by_call(shown)
    heads = []
    for i, t in enumerate(shown):
        cmd = str(t.get("command") or "")
        if t.get("turn_type") != "tool_use" or (t.get("tool_name") or "") not in SHELL_TOOLS:
            continue
        if not _prints_head(cmd):
            continue
        m = SHA.search(results.get(i, ""))
        if m:
            heads.append(m.group(1))
    return heads


def tree_changing_git(turns: list[dict], cut: int) -> list[str]:
    """The agent's git commands before the cut that changed its files."""
    out = []
    for t in _turns_until(turns, cut):
        if t.get("turn_type") != "tool_use" or (t.get("tool_name") or "") not in SHELL_TOOLS:
            continue
        cmd = str(t.get("command") or "")
        if any(_changes_tree(m.group(1), m.group(2)) for m in GIT_TREE.finditer(cmd)):
            out.append(" ".join(cmd.split())[:120])
    return out


def why_inconsistent(row: dict) -> str:
    """One line saying where a rebuilt tree departs from its conversation."""
    if row.get("head_contradicts_base"):
        return (f"the conversation printed commit {row['heads_printed'][0]} as HEAD, "
                f"not the base the tree was built from")
    for f in row.get("files") or []:
        if f.get("lines_differing"):
            d = f.get("first_difference") or {}
            return (f"the rebuilt tree differs from what the conversation showed of {f['path']}: "
                    f"{f['lines_differing']} of {f['lines_checked']} lines, first at line {d.get('line')}")
    if row.get("lost_edits"):
        return f"edits before the cut that the corpus table lost: {', '.join(row['lost_edits'][:3])}"
    return "consistent"


def mutating_commands(turns: list[dict], cut: int) -> list[str]:
    return [" ".join(str(t.get("command")).split())[:120] for t in _turns_until(turns, cut)
            if t.get("turn_type") == "tool_use" and (t.get("tool_name") or "") in SHELL_TOOLS
            and MUTATING.search(str(t.get("command") or ""))]


def lost_edits(recovered: list[dict], cut: int) -> list[str]:
    """The files of edits before the cut that only the raw transcript records."""
    return [str(t.get("file_path") or "(no path)") for t in _turns_until(recovered, cut)
            if t.get("recovered") and t.get("turn_type") == "tool_use"
            and (t.get("tool_name") or "") in EDIT_TOOLS]


def check(tree: Path, turns: list[dict], cut: int, base_sha: str,
          recovered: list[dict] | None = None) -> dict:
    """The consistency of one rebuilt tree with its conversation, as a row.

    ``recovered`` is ``turns`` with the calls the table lost put back
    (``corpus.recover``); without it `lost_edits` is None, not measured.
    """
    files = []
    for path, lines in sorted(observed_lines(turns, cut).items()):
        rel = relative(path, tree)
        if rel is None:
            files.append({"path": path, "found": False})
            continue
        # Split, not splitlines: a Read shows the empty line after a final
        # newline, and dropping it reported every such file as differing.
        body = (tree / rel).read_text(errors="replace").split("\n")
        differing = [n for n, text in lines.items()
                     if n > len(body) or not same_line(text, body[n - 1])]
        files.append({"path": rel, "found": True, "lines_checked": len(lines),
                      "lines_differing": len(differing),
                      "first_difference": (
                          {"line": differing[0], "conversation": lines[differing[0]][:120],
                           "tree": (body[differing[0] - 1][:120] if differing[0] <= len(body) else "(past end)")}
                          if differing else None)})
    heads = printed_heads(turns, cut)
    contradicting = [h for h in heads if not (base_sha.startswith(h) or h.startswith(base_sha))]
    compared = [f for f in files if f.get("found")]
    lost = None if recovered is None else lost_edits(recovered, cut)
    return {
        "files_compared": len(compared),
        "files_differing": sum(1 for f in compared if f["lines_differing"]),
        "files_not_found": sum(1 for f in files if not f.get("found")),
        "files": files,
        "heads_printed": heads,
        "head_contradicts_base": bool(contradicting),
        "mutating_commands": mutating_commands(turns, cut),
        "lost_edits": lost,
        "consistent": (not any(f.get("lines_differing") for f in compared) and not contradicting
                       and not lost),
    }
