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
  head      a commit printed by `git log` or `git rev-parse` before the agent
            moved HEAD itself (a commit, a reset), against the commit the tree
            was built from.
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

import posixpath
import re
import shlex
from pathlib import Path

from .edits import OUTSIDE, posix_form

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
#
# Measured over the 486 distinct commands the first version flagged before the
# moments of the sample and the next batches (B-249): 377 changed the tree; 29
# stashed and popped in one call, which the call's own output proves; 75 did
# not change it -- `git merge-base` and `merge-tree` read as `merge` (the verb
# was matched by \b, and `-` ends a word), 17 never ran (declined at the
# prompt, denied by a hook), `stash drop`, `checkout -b <new> 2>&1` counted as
# three arguments, `checkout <sha> -- /dev/null`. 52 moments were rejected for
# these alone. And three that do change the tree were missed: `git mv`, `git
# rm` and `gh pr checkout`.
# Git's own options, which may stand between `git` and its verb: `git -c
# user.name=a commit`, `git --no-pager log`, `git -C sub checkout`. Missed, a
# `git -c advice.detachedHead=false checkout abc1234` changed the tree unseen
# (10-02 review).
_GIT_OPTS = (r"(?:(?:-C|-c)\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--no-pager|-P|--paginate|--no-optional-locks"
             r"|--literal-pathspecs|--no-replace-objects|--bare|--git-dir=\S+|--work-tree=\S+|--namespace=\S+)")
GIT_TREE = re.compile(
    r"\bgit\s+(?:" + _GIT_OPTS + r"\s+)*"
    r"(checkout-index|checkout|switch|pull|merge-file|merge|rebase|reset|stash|cherry-pick|am|apply"
    r"|restore|clean|revert|rm|mv)(?![\w-])([^|;&\n)]*)")
GH_CHECKOUT = re.compile(r"\bgh\s+pr\s+checkout\b")
_REDIRECT = re.compile(r"^(?:\d*>>?|&>>?|\d*<|>&)")
_CREATE = ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan")
_QUIET_CHECKOUT = ("-q", "--quiet", "--no-track", "--track", "-t", "--no-guess", "--progress", "--no-progress")
_DRY_RUN = re.compile(r"--dry-run|-[a-zA-Z]*n[a-zA-Z]*")
# A call that never ran: declined at the permission prompt, denied by a hook,
# blocked by a plugin, or refused by the tool before running.
_NEVER_RAN = re.compile(
    r"\s*(?:The user doesn't want to proceed with this tool use|The user doesn't want to take this action"
    r"|Hook PreToolUse:\S+ denied this tool|BLOCKED by Safety Net|<tool_use_error>)")


def _words(rest: str) -> list[str]:
    """An invocation's arguments, without its redirections (`2>&1` is not an argument)."""
    return [w for w in rest.split() if not _REDIRECT.match(w)]


def _stash_sub(rest: str) -> str:
    return next((w for w in _words(rest) if w not in ("-q", "--quiet")), "")


def _changes_tree(verb: str, rest: str) -> bool:
    """Whether one git invocation changes the working tree, not just refs or the index."""
    words = _words(rest)
    if "-h" in words or "--help" in words:
        return False
    if verb == "stash":
        return _stash_sub(rest) not in ("list", "show", "drop", "clear", "create", "store")
    if verb in ("checkout", "switch"):
        # Flags that change neither where HEAD lands nor what the files hold, in
        # front of the one that names a new branch: `checkout -q -b <new>` was
        # read as a switch, so a branch made where HEAD already was rejected its
        # moment (G-87). `-f` is not among them: it throws away local changes.
        words = [w for w in words if w not in _QUIET_CHECKOUT]
        if not words:
            return False
        if words[0] in _CREATE and (len(words) <= 2 or (len(words) == 3 and words[2] in ("HEAD", "@"))):
            return False   # a new branch where HEAD already is
        if "--" in words:
            paths = words[words.index("--") + 1:]
            if paths and all(p == "/dev/null" for p in paths):
                return False
        return True
    if verb == "reset":
        return any(w in ("--hard", "--merge", "--keep") for w in words)   # else the index, not the files
    if verb == "restore" and "--staged" in words and not {"--worktree", "-W"} & set(words):
        return False
    if verb == "rm" and "--cached" in words:
        return False
    if verb in ("clean", "rm", "mv") and any(_DRY_RUN.fullmatch(w) for w in words):
        return False
    if verb == "apply" and (("--cached" in words and "--index" not in words)
                            or ({"--check", "--stat", "--numstat", "--summary"} & set(words)
                                and "--apply" not in words)):
        return False
    return True


def _undone(hits: list, result: str, cmd: str) -> list:
    """The invocations that changed the tree, less stashes the call's own output shows undone.

    `git stash && npm test | tail -5 && git stash pop` leaves the tree as it
    was, and says so: one "Saved working directory" and one "Dropped
    refs/stash@{0}" per pair. Anything the output does not prove -- a
    conflict, an entry kept, a `cd` or another `-C` between the two -- stays.
    """
    stash = [h for h in hits if h.group(1) == "stash"]
    if not stash:
        return hits
    others = [h for h in hits if h.group(1) != "stash"]
    kinds = [{"pop": "pop", "apply": "apply"}.get(_stash_sub(h.group(2)), "push") for h in stash]
    targets = {(re.search(r"\s-C\s+(\S+)", h.group(0)[:h.start(1) - h.start(0)]) or [None, None])[1] for h in stash}
    if len(targets) > 1 or re.search(r"(?:^|[\s;&|(])cd\s", cmd[stash[0].start():stash[-1].start()]):
        return hits
    if "apply" in kinds or any(s in result for s in ("CONFLICT", "would be overwritten",
                                                      "The stash entry is kept", "could not restore")):
        return hits
    saved = result.count("Saved working directory")
    dropped = len(re.findall(r"Dropped (?:refs/)?stash@\{0\}", result))
    nothing = "No local changes to save" in result
    pairs = len(kinds) // 2
    alternating = pairs > 0 and kinds == ["push", "pop"] * pairs
    if alternating and saved == pairs == dropped and not nothing:
        return others
    if kinds == ["push"] and nothing and saved == 0 and dropped == 0:
        return others
    if alternating and saved == 0 and dropped == 0 and "No stash entries found" in result:
        return others
    return hits


# SWE-chat replaced what its secret scanners flagged with placeholders -- in
# 45,627 rows, mostly a bare REDACTED, often over long hashes and ids. A
# placeholder stands for the text it replaced, so it matches whatever the tree
# holds there. Read literally, one hid an IPFS hash in oozoofrog-108's
# chronology_unicode.md and the build rejected a tree that was right. Marks
# with a kind count too: `[REDACTED:SECRET]` appears 1,639 times in the next
# batches' tool results, `[REDACTED:DB_PASSWORD]` 477, `[REDACTED:ENV]` 231, and
# read as the bare word their brackets and kind were demanded of the tree --
# BugViper-101 was rejected over `github_access_token=[REDACTED:SECRET],`.
_PLACEHOLDER = re.compile(
    r"<TRUFFLEHOG_REDACTED_[A-Z_]+>|\[REDACTED(?:[_:][A-Z0-9_]+)?\]|REDACTED(?:_[A-Z0-9_]+)?")


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
    """The developer's absolute path as a path in the tree: its longest suffix that exists.

    Split as the machine that wrote it would: on Linux and macOS a Windows path
    is one part, so none of a Windows session's reads was ever found, and its
    task compared nothing (#9).
    """
    parts = [p for p in Path(posix_form(path)).parts if p not in ("/", "")]
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


# Where each part of a shell command runs (G-87, G-88). A `cd` or `git -C` into
# another folder runs git on a repository that is not the session's: a scratch
# clone in /tmp, a copy made to compare against, another worktree of the same
# repository on another branch. Read as the session's own, a checkout in a
# throwaway clone rejected two moments as a changed tree, and a `git log` in
# another worktree was taken for the session's HEAD.
_VAR = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_NAME = re.compile(r"(?:export\s+)?([A-Za-z_]\w*)=(.*)", re.S)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w.-]*)\1")
# The worktrees agent tools and developers make inside a repository, each a
# checkout on its own branch: Claude Code's, herdr's, and the hidden folders
# developers keep them in (raman325's `.worktrees/reader-provider` printed a
# HEAD that was read as the session's). Not a bare `worktrees/`, which a
# project's own source may hold.
_WORKTREE = re.compile(r"^(.*?/(?:\.claude/worktrees|\.herdr/worktrees/[^/]+|\.worktrees|\.trees)/[^/]+)(?:/|$)")
_HOME = re.compile(r"^(/Users/[^/]+|/home/[^/]+)")
OWN, ELSEWHERE, UNPLACED = "own", "elsewhere", "unplaced"


def checkout_of(path: str | None) -> str:
    """The agent-made worktree an absolute path lies in, as its folder; "" for any other path."""
    m = _WORKTREE.match(posix_form(path or ""))
    return m.group(1) if m else ""


def main_checkout_of(path: str | None) -> str | None:
    """The repository's own checkout a worktree inside it belongs to; None for any other path,
    and for a worktree kept elsewhere (herdr's, under the home folder)."""
    m = re.match(r"^(.*?)/(?:\.claude/worktrees|\.worktrees|\.trees)/[^/]+(?:/|$)", posix_form(path or ""))
    return m.group(1) if m else None


_DRIVE_PATH = re.compile(r"^/(?:mnt/|cygdrive/)?[A-Za-z](?=/|$)")
# Windows' temporary folder once `_posix` has dropped the drive.
_WINDOWS_TEMP = re.compile(r"^/Users/[^/]+/AppData/Local/Temp(?:/|$)")


def _posix(path: str, cwd: str | None) -> str:
    """A path as `posix_form` writes it, with a POSIX shell's drive prefix dropped on a Windows session.

    Git Bash writes D:\\repo as /d/repo, WSL as /mnt/d/repo, and `posix_form`
    keeps neither drive, so the two would never be the same folder.
    """
    p = posix_form(path)
    if cwd and re.match(r"^[A-Za-z]:", cwd):
        p = _DRIVE_PATH.sub("", p) or "/"
    return p


def where(folder: str | None, cwd: str | None) -> str:
    """Whether a command's folder is the session's own checkout, another one, or cannot be told.

    `folder` is None for the folder the session started in, UNPLACED for one
    this cannot work out, else an absolute path. `cwd` is the folder the
    session started in. Another checkout only when that is certain: another
    worktree of the repository, or the agent's scratch, /tmp and the like.
    Anything else outside the starting folder -- a sibling package of a
    monorepo the session began inside, `cd ../backend` -- may be the same
    repository, so it cannot be told (10-02 review). Containment is read
    first: a session that began in /tmp/proj owns /tmp/proj/src.
    """
    if folder is None:
        return OWN
    if folder == UNPLACED:
        return UNPLACED
    p = posixpath.normpath(_posix(folder, cwd))
    if cwd:
        c = posixpath.normpath(_posix(cwd, cwd))
        if checkout_of(p) != checkout_of(c):
            return ELSEWHERE
        if p == c or p.startswith(c.rstrip("/") + "/"):
            return OWN
    if OUTSIDE.match(p.rstrip("/") + "/") or _WINDOWS_TEMP.match(p):
        return ELSEWHERE
    return UNPLACED


def _resolve(target: str, here: str | None, names: dict, cwd: str | None) -> str | None:
    """The folder a `cd` or `-C` names, from the folder it ran in; UNPLACED when unknowable.

    `target` is one shell word, its quotes and escapes already undone.
    Anything the shell would still expand -- a name not set in this command,
    `${X:-/tmp/x}`, `$PWD` kept inside a name's value, `$(...)` -- cannot be
    placed.
    """
    unknown = []
    t = _VAR.sub(lambda m: names.get(m.group(1) or m.group(2)) or unknown.append(1) or "", target)
    if unknown or t in ("", "-") or "$" in t or "`" in t:
        return UNPLACED
    t = _posix(t, cwd)
    if t.startswith("~"):
        home = _HOME.match(posix_form(cwd or ""))
        if not home:
            return UNPLACED
        t = home.group(1) + t[1:]
    if t.startswith("/"):
        return posixpath.normpath(t)
    if here == UNPLACED:
        return UNPLACED
    base = here if here is not None else (_posix(cwd, cwd) if cwd else None)
    if base is None:
        # Inside the session's own folder, which is not known: a subfolder stays
        # its own; leaving it, or entering a worktree, cannot be placed.
        return UNPLACED if t.startswith("..") or "worktrees/" in t or "/.trees/" in f"/{t}" else None
    return posixpath.normpath(posixpath.join(base, t))


def _parts(cmd: str) -> list[tuple[int, int, str]]:
    """Where a command splits into the parts the shell runs one after another: (start, end, the separator
    before it, "" for the first). Not inside quotes or a heredoc's text: a commit message that reads
    "x && cd /tmp/y" is not two commands, and a heredoc line `cd /tmp` is not one (10-02 review)."""
    bounds, seps = [], []
    i, n, start, quote, pending = 0, len(cmd), 0, None, []
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = _HEREDOC.match(cmd, i)
            if m:
                pending.append(m.group(2))
                i = m.end()
                continue
        if ch == "\n":
            bounds.append((start, i))
            seps.append("\n")
            i += 1
            for delim in pending:
                m = re.compile(r"^[ \t]*" + re.escape(delim) + r"[ \t]*$", re.M).search(cmd, i)
                i = m.end() + 1 if m else n
            pending, start = [], i
            continue
        if cmd.startswith(("&&", "||"), i):
            bounds.append((start, i))
            seps.append(cmd[i:i + 2])
            i += 2
            start = i
            continue
        if ch in ";|":
            bounds.append((start, i))
            seps.append(ch)
            i += 1
            start = i
            continue
        i += 1
    bounds.append((start, n))
    before = [""] + seps
    return [(s0, e0, before[k]) for k, (s0, e0) in enumerate(bounds)]


def _shell_words(part: str) -> list[str]:
    """A part's words as the shell reads them: quotes and escapes undone."""
    try:
        return shlex.split(part, comments=False, posix=True)
    except ValueError:
        return part.split()


def _spans(cmd: str, cwd: str | None, start: str | None = None) -> list[tuple[int, int, str | None, dict, str]]:
    """Each part of a command, as (start, end, the folder it runs in, the shell names set before it, the
    separator before it).

    `start` is where the call ran, as its transcript records it, when that is
    not where the session started: the shell keeps a `cd` from one call to the
    next (10-02 review). A `cd` moves every later part, until a subshell it was
    made in closes: `(cd "$WT" && go build)` leaves the next command where it
    was, and `$(pwd)` closes no subshell. `popd`, a bare `cd` and `cd -` go
    where this cannot follow.
    """
    out, names, here, stack = [], {}, start, []
    for s, e, sep in _parts(cmd):
        part = cmd[s:e]
        lead = part.lstrip()
        opens = len(lead) - len(lead.lstrip("("))
        stack.extend([here] * opens)
        out.append((s, e, here, dict(names), sep))
        words = _shell_words(lead.lstrip("( \t"))
        if words and all(_NAME.fullmatch(w) for w in (words[1:] if words[0] == "export" else words)):
            for w in (words[1:] if words[0] == "export" else words):
                k, v = _NAME.fullmatch(w).groups()
                names[k] = v
        elif words and words[0] in ("cd", "pushd", "builtin") and (words[0] != "builtin" or words[1:2] == ["cd"]):
            args = [w for w in words[(2 if words[0] == "builtin" else 1):] if w not in ("-P", "-L", "-e", "-@", "--")]
            here = _resolve(args[0], here, names, cwd) if args else UNPLACED
        elif words and words[0] == "popd":
            here = UNPLACED
        closes = len(part.rstrip()) - len(part.rstrip().rstrip(")")) - part.count("$(")
        for _ in range(max(0, closes)):
            if stack:
                here = stack.pop()
    return out


def _git_folder(cmd: str, at: int, spans: list, cwd: str | None) -> str | None:
    """The folder the git invocation starting at `at` runs in: its part's, moved by any `-C`.

    `--git-dir` or `--work-tree` point git at a repository this cannot place.
    """
    part = next((x for x in spans if x[0] <= at < x[1]), (0, 0, None, {}, ""))
    words = _shell_words(cmd[at:part[1]])
    here, i = part[2], 1
    while i < len(words):
        w = words[i]
        if w == "-C" and i + 1 < len(words):
            here = _resolve(words[i + 1], here, part[3], cwd)
            i += 2
        elif w == "-c" and i + 1 < len(words):
            i += 2
        elif w.startswith(("--git-dir", "--work-tree")):
            return UNPLACED
        elif w.startswith("-"):
            i += 1
        else:
            break
    return here


# `git log` options after which the first commit printed is still HEAD. Any
# other -- `--all`, `--grep`, `--author`, `--reverse`, `--skip`, `--since` --
# prints some other commit first: `git log --oneline --all --grep=2173` gave
# discourse-graph-200 a "HEAD" from another branch (G-88). A format must open
# with the commit's own name: `%P` prints its parent, `%s` may quote another.
_LOG_KEEPS_HEAD = re.compile(
    r"-\d+|-n\d*|--max-count(?:=\d+)?|--oneline|--decorate(?:=\S*)?|--no-decorate|--abbrev(?:=\d+)?|--abbrev-commit"
    r"|--no-abbrev-commit|--stat(?:=\S*)?|--shortstat|--numstat|--name-only|--name-status|--graph|--color(?:=\S*)?"
    r"|--no-color|-p|--patch|--no-patch|-s|--date=\S*|--first-parent|--topo-order|--date-order|--show-signature"
    r"|--no-notes|--parents|--pretty")
_LOG_VALUE = ("-n", "--max-count", "--format", "--date", "--pretty")
_NAMED_FORMATS = ("oneline", "short", "medium", "full", "fuller", "reference", "raw")


def _format_keeps_head(value: str) -> bool:
    v = value.strip("'\"")
    if v in _NAMED_FORMATS:
        return True
    return re.sub(r"^t?format:", "", v).startswith(("%h", "%H"))


# Git invocations that print no commit name. Not `git fetch`, nor `git remote
# update`, which is one: they print the range they moved a branch over.
_QUIET_GIT = re.compile(r"\bgit\s+(?:status\s+(?:-s|-sb|--short|--porcelain)\b|add\b|remote(?:\s+-v)?\s*$"
                        r"|remote\s+get-url\b|config\b|branch\s+(?:--show-current|-m|-M)\b"
                        r"|diff\s+(?:--stat|--name-only|--name-status|--shortstat)\b)")
_GIT_CALL = re.compile(r"\bgit\s")
_HEX_RUN = re.compile(r"[0-9a-f]{7,}")
# What may follow the part that prints HEAD without dropping its first line.
_KEEPS_FIRST = re.compile(r"\s*(?:head\b(?:\s+-n?\s*\d+|\s+-\d+)?\s*|cat\s*|less\s*)$")


def _quiet(part: str) -> bool:
    """Whether a part prints no commit name: a quiet git invocation, a branch made where HEAD
    already is, a `cd`, a name set, or an `echo`/`printf` of literal words with no such name."""
    lead = part.strip().lstrip("(").strip()
    if _QUIET_GIT.search(lead):
        return True
    m = GIT_TREE.search(lead)
    if m and m.group(1) in ("checkout", "switch") and not _changes_tree(m.group(1), m.group(2)):
        return True
    words = _shell_words(lead)
    if not words:
        return True
    if words[0] in ("cd", "pushd", "popd", "export", "true", ":", "set") or _NAME.fullmatch(words[0]):
        return True
    if words[0] in ("echo", "printf"):
        return not ("$" in lead or "`" in lead or _HEX_RUN.search(lead))
    return False


def _prints_head(cmd: str) -> bool:
    """Whether a part of a command prints HEAD as the first commit it names.

    `git rev-parse HEAD`, or `git log` with only options that keep HEAD first
    and no revision but HEAD. Not a range: `main...HEAD` lists commits HEAD
    lacks, and an empty `main..HEAD` hands the first name in the output to
    whatever prints next. Not `git log -- path`, `git log other-branch`, or
    `... | xargs git log -1`, which logs whatever the pipe handed it (G-88).
    """
    if re.search(r"\bxargs\b[^|;&]*\bgit\b", cmd) or "$(" in cmd or "`" in cmd:
        return False
    if re.search(r"\bgit\s+rev-parse\s+(?:(?:--short(?:=\d+)?|--verify|-q|--quiet)\s+)*(?:HEAD|@)(?![\w~^@{:./-])"
                 r"\s*(?:\d?>\S*\s*)*$", cmd):
        return True
    m = re.search(r"\bgit\s+log\b(.*)", cmd, re.S)
    if not m:
        return False
    value = None
    for a in _shell_words(m.group(1)):
        if value:
            if value in ("--format", "--pretty") and not _format_keeps_head(a):
                return False
            value = None
        elif _REDIRECT.match(a):
            continue
        elif a in _LOG_VALUE:
            value = a
        elif a.startswith(("--format=", "--pretty=")):
            if not _format_keeps_head(a.split("=", 1)[1]):
                return False
        elif a.startswith("-"):
            if not _LOG_KEEPS_HEAD.fullmatch(a):
                return False
        elif a not in ("HEAD", "@"):
            return False
    return True


def _moves_head(cmd: str, spans: list, cwd: str | None) -> bool:
    """Whether the command moves HEAD in the session's checkout, or may: a commit,
    a reset, a merge, a checkout or switch to another commit, `gh pr checkout`."""
    moves = [*HEAD_MOVES.finditer(cmd), *(m for m in GIT_TREE.finditer(cmd)
                                         if m.group(1) in ("checkout", "switch") and _changes_tree(m.group(1), m.group(2)))]
    if any(where(_git_folder(cmd, m.start(), spans, cwd), cwd) != ELSEWHERE for m in moves):
        return True
    return any(where(next((x[2] for x in spans if x[0] <= m.start() < x[1]), None), cwd) != ELSEWHERE
               for m in GH_CHECKOUT.finditer(cmd))


# Commands after which HEAD is no longer the commit the session started from,
# although the files may be exactly the base plus the replayed edits: the
# agent's own `git commit`, a `git reset` that keeps the files, and the history
# changes the git rule rejects anyway. 114 of the 2,340 sessions of the sample
# and the next batches print a HEAD after one of these before their moment, and
# every such HEAD differs from the base by construction.
HEAD_MOVES = re.compile(r"\bgit\s+(?:" + _GIT_OPTS + r"\s+)*(commit|reset|merge|pull|rebase|cherry-pick|am|revert)(?![\w-])")


def _call_start(t: dict, folders: dict | None, cwd: str | None) -> str | None:
    """Where a call ran, when its transcript says it was not the folder the session started in."""
    f = (folders or {}).get(str(t.get("tool_call_id") or ""))
    if not f or not cwd or posixpath.normpath(_posix(f, cwd)) == posixpath.normpath(_posix(cwd, cwd)):
        return None
    return posixpath.normpath(_posix(f, cwd))


def printed_heads(turns: list[dict], cut: int, cwd: str | None = None, folders: dict | None = None) -> list[str]:
    """Commits that `git log` or `git rev-parse` printed first, before the cut,
    while HEAD was still the commit the session started from.

    Only from the session's own checkout (`cwd`, the folder it started in, and
    `folders`, where each call ran), and only when everything the command ran
    before it prints no commit name and nothing after it drops its first line:
    the first name in the output is read. A checkout or switch to another
    commit moves HEAD as a commit does, and a move declined at the prompt moves
    nothing (G-88). `start_evidence` reads the same, over the whole session.
    """
    return start_evidence(_turns_until(turns, cut), cwd, folders)["heads"]


# What the first command that moves HEAD says of the commit it moved from:
# a pull or merge that fast-forwards prints "Updating 6d93c52..c8a9188", and a
# commit prints "[main 3583f62] message", whose parent is where HEAD was.
_UPDATING = re.compile(r"^Updating ([0-9a-f]{7,40})\.\.[0-9a-f]{7,40}\s*$", re.M)
_COMMITTED = re.compile(r"^\[[^\]\s]+(?: \([^)]*\))? ([0-9a-f]{7,40})\]", re.M)


def start_evidence(turns: list[dict], cwd: str | None = None, folders: dict | None = None) -> dict:
    """What the whole session says of the commit it started from, read up to its first move of HEAD (G-86).

    A HEAD printed before anything moved it is the start, at any turn, before
    the cut or after it: nrmeyers-agentalloy-20's first move came after its cut,
    a `git pull` printing "Updating 6d93c52..c8a9188", and 6d93c52 was the start
    while the remote already held c8a9188. So `heads`, every HEAD printed before
    the first move; `updated_from`, the commit a first move that is one pull or
    merge in the session's own checkout says it moved from; `committed`, the
    commit such a first move that is one commit (not an amend) made, whose
    parent is the start. A move this does not know, made in a command or by
    the developer outside the session, can come before what it reads: the
    build refuses what it says unless the remote holds it, from before the
    session, and not as the session's own commit.
    """
    shown = _turns_until(turns, 10**9)
    results = _results_by_call(shown)
    out = {"heads": [], "updated_from": None, "committed": None, "first_move": None}
    for i, t in enumerate(shown):
        cmd = str(t.get("command") or "")
        if t.get("turn_type") != "tool_use" or (t.get("tool_name") or "") not in SHELL_TOOLS:
            continue
        spans = _spans(cmd, cwd, _call_start(t, folders, cwd))
        result = results.get(i, "")
        if _moves_head(cmd, spans, cwd):
            if _NEVER_RAN.match(result):
                continue
            out["first_move"] = " ".join(cmd.split())[:120]
            mine = [m for m in HEAD_MOVES.finditer(cmd)
                    if where(_git_folder(cmd, m.start(), spans, cwd), cwd) != ELSEWHERE]
            other = [m for m in GIT_TREE.finditer(cmd) if m.group(1) in ("checkout", "switch")
                     and _changes_tree(m.group(1), m.group(2))] + list(GH_CHECKOUT.finditer(cmd))
            if len(mine) == 1 and not other and where(_git_folder(cmd, mine[0].start(), spans, cwd), cwd) == OWN:
                verb = mine[0].group(1)
                rest = cmd[mine[0].end():mine[0].end() + 200]
                if verb in ("pull", "merge"):
                    m = _UPDATING.search(result)
                    out["updated_from"] = m.group(1) if m and len(_UPDATING.findall(result)) == 1 else None
                elif verb == "commit" and "--amend" not in rest.split("&&")[0]:
                    m = _COMMITTED.search(result)
                    out["committed"] = m.group(1) if m and len(_COMMITTED.findall(result)) == 1 else None
            break
        printing = None
        for k, (s0, e0, here, names, sep) in enumerate(spans):
            part = cmd[s0:e0]
            if sep == "|":
                continue          # a filter of what came before: as quiet as it was
            if _GIT_CALL.search(part) and _prints_head(part):
                printing = k
                break
            if not _quiet(part):
                break
        if printing is None or where(spans[printing][2], cwd) != OWN:
            continue
        after = spans[printing + 1:]
        if after and after[0][4] == "|" and not _KEEPS_FIRST.fullmatch(cmd[after[0][0]:after[0][1]]):
            continue              # `| tail -1`, `| grep x`: the first line may be gone
        m = SHA.search(result)
        if m:
            out["heads"].append(m.group(1))
    return out


def tree_changing_git(turns: list[dict], cut: int, cwd: str | None = None, folders: dict | None = None) -> list[str]:
    """The agent's git commands before the cut that changed its files.

    Each call is read with its result: a call declined at the prompt never
    ran, and a stash its own output shows popped changed nothing. And each git
    invocation with the folder it ran in (`cwd` where the session started,
    `folders` where each call ran): one certainly in another checkout -- the
    agent's scratch, /tmp, another worktree -- changed that checkout's files,
    not these (G-87). One whose folder cannot be told is counted, as before.
    """
    shown = _turns_until(turns, cut)
    results = _results_by_call(shown)
    out = []
    for i, t in enumerate(shown):
        if t.get("turn_type") != "tool_use" or (t.get("tool_name") or "") not in SHELL_TOOLS:
            continue
        cmd = str(t.get("command") or "")
        result = results.get(i, "")
        if _NEVER_RAN.match(result):
            continue
        spans = _spans(cmd, cwd, _call_start(t, folders, cwd))
        hits = [m for m in GIT_TREE.finditer(cmd) if _changes_tree(m.group(1), m.group(2))
                and where(_git_folder(cmd, m.start(), spans, cwd), cwd) != ELSEWHERE]
        gh = [m for m in GH_CHECKOUT.finditer(cmd)
              if where(next((x[2] for x in spans if x[0] <= m.start() < x[1]), None), cwd) != ELSEWHERE]
        if _undone(hits, result, cmd) or gh:
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
          recovered: list[dict] | None = None, cwd: str | None = None, folders: dict | None = None) -> dict:
    """The consistency of one rebuilt tree with its conversation, as a row.

    ``recovered`` is ``turns`` with the calls the table lost put back
    (``corpus.recover``); without it `lost_edits` is None, not measured.
    ``cwd`` is the folder the session started in, and ``folders`` where each
    call ran, so that a HEAD printed in another checkout is not read as its
    own (G-88).
    """
    files = []
    for path, lines in sorted(observed_lines(turns, cut).items()):
        # The agent's own files (a plan, a scratch clone in /tmp) are not the
        # repository's. Matched by suffix, /tmp/clone/package.json would be
        # compared with the tree's package.json and differ from it.
        if OUTSIDE.match(path):
            continue
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
    heads = printed_heads(turns, cut, cwd, folders)
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
