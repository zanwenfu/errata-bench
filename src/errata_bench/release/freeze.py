"""Freeze each task once, so running it needs neither the corpus nor GitHub.

Every attempt rebuilds its task today. It fetches the repository from GitHub at
the task's commit (`score.attempt.run`), replays the session's edits from the
SWE-chat corpus, and renders the conversation from the corpus's 1.3 GB parquet.
Someone outside has neither the gated corpus nor a guarantee that a repository
still exists: the build has lost tasks to deleted repositories and to
force-pushes. So each task is frozen here, into ``<out>/tasks/<task_id>/``:

  workspace.tar.gz  the developer's working copy at the cut: the repository at
                    the task's commit with up to ``history`` commits of its past,
                    on the session's branch, with the session's edits replayed
                    on top as uncommitted changes -- what `git status` showed.
  task.json         the task's description: repository, commit, branch, the
                    folder the developer worked in, the kind of defect, licence,
                    and digests of what is here.
  conversation.txt  the conversation the candidate is shown, exactly as
                    `transcript_for` renders it.
  shown_turns.json  the turns that conversation is rendered from, redacted and
                    with every result the corpus table cut given back whole
                    (`candidate_turns`), up to the cut: a rendering of what the
                    candidate is shown needs these alone (`shown_turns`).
  grading/          what only the graders read: the reference answers, the
                    defect and its signature, the controls' conversations, the
                    task row, and the turns up to the resolution, from which any
                    rendering of the conversation can be made again.

The working files are checked against the tree the benchmark has always built
(`fetch`, `export_tree`, `replay`), file by file, and a task whose frozen tree
differs is not written. No model calls.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..construct.edits import _checkout_root, edits_before, replay
from ..construct.workspace import GitError, _git, fetch
from ..spec import Task, fingerprint

# How much of a repository's past the working copy carries. Enough for the
# `git log`, `git show` and `git blame` an agent reaches for; bounded, because a
# task needs its recent past, not every commit since the repository began.
HISTORY = 100
# The most git's own files may hold, in MiB. Safecast-safecast-new-map-95 commits
# its compiled binaries, about 80 MB each, and a hundred commits of that past came
# to 579 MB beside a working tree of 255 MB. Over this, the past is fetched a
# quarter as deep, down to the commit alone, and the depth used is recorded.
MAX_GIT_MB = 200.0
# The name the working copy's branch gets when the corpus records none.
DEFAULT_BRANCH = "work"
# Who commits, if the candidate commits: git refuses to without an identity.
IDENTITY = ("developer", "developer@example.invalid")


@dataclass
class Frozen:
    task_id: str
    ok: bool = False
    reason: str = ""
    files: int = 0
    digest: str = ""
    workdir: str = ""
    branch: str = ""
    differ: list[str] = field(default_factory=list)


def _walk(root: Path) -> list[Path]:
    """Every path under ``root``, sorted, without following a symbolic link into a directory."""
    out = []
    for here, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in dirs + files:
            out.append(Path(here) / name)
    return sorted(out)


def tree_digest(root: Path, skip: tuple[str, ...] = (".git",)) -> tuple[str, int, dict[str, str]]:
    """A digest of every file under ``root`` (its path, executable bit and content), and each file's own."""
    each: dict[str, str] = {}
    for p in _walk(root):
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in skip:
            continue
        if p.is_symlink():
            each[rel.as_posix()] = "link:" + os.readlink(p)
        elif p.is_file():
            executable = "x" if os.access(p, os.X_OK) else "-"
            each[rel.as_posix()] = executable + hashlib.sha256(p.read_bytes()).hexdigest()
    h = hashlib.sha256("".join(f"{k}\0{v}\n" for k, v in sorted(each.items())).encode())
    return h.hexdigest(), len(each), each


def workdir_of(tree: Path, edits: list[dict], turns: list[dict]) -> tuple[str, str]:
    """Where the developer's checkout was: as a folder in the container, and as the session named it.

    The same election `replay` makes (`_checkout_root`), over the edits' paths
    and then every other absolute path the agent's tools named, so a session
    that edited nothing still says where it was. A session on Windows names a
    drive (``E:\\projects\\app``), which no Linux folder can be: its container
    folder is "/work", as is any session's whose paths say nothing.
    """
    paths = [e["args"].get("file_path", "") for e in edits]
    paths += [str(t.get("file_path") or "") for t in turns]
    posix = _checkout_root(tree, [p for p in paths if p.startswith("/")])
    if posix and posix[0] == "/" and len(posix) > 1:
        where = "/" + "/".join(posix[1:])
        return where, where
    drive = _checkout_root(tree, [p.replace("\\", "/") for p in paths if len(p) > 2 and p[1:3] in (":\\", ":/")])
    if drive and len(drive) > 1:
        return "/work", "\\".join(drive)
    return "/work", ""


def _tar(src: Path, dest: Path, name: str, mtime: int) -> None:
    """A tar.gz of ``src`` under ``name``/, in a fixed order with fixed times and owners.

    Two freezes of one task give the same working files (``tree_digest``), but not the
    same archive bytes: git's own files -- the index's timestamps, the packs a fetch
    writes -- differ from one fetch to the next. The archive's hash in task.json checks
    the archive published; the tree digest is what says two freezes agree.
    """
    with open(dest, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in [src, *_walk(src)]:
            rel = p.relative_to(src)
            info = tar.gettarinfo(str(p), arcname=str(Path(name) / rel) if rel.parts else name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = mtime, 0, 0, "", ""
            if info.isreg():
                with open(p, "rb") as fh:
                    tar.addfile(info, fh)
            else:
                tar.addfile(info)


def _git_mb(repo: Path) -> float:
    """How much git's own files hold, in MiB."""
    counts = dict(line.split(": ", 1) for line in _git("count-objects", "-v", cwd=repo).splitlines() if ": " in line)
    return (int(counts.get("size-pack", 0)) + int(counts.get("size", 0))) / 1024


def working_copy(task: Task, edits: list[dict], dest: Path, *, branch: str, history: int = HISTORY,
                 max_git_mb: float = MAX_GIT_MB) -> tuple[str, int]:
    """The developer's working copy: the commit with its past, the branch, the session's edits uncommitted.

    Fetched by commit with ``--no-tags``: only the commit's ancestors arrive,
    never what came after it, and no tag made later names them. A past larger
    than ``max_git_mb`` is fetched again a quarter as deep. Returns why it could
    not be built, or "", and the depth of past it holds.
    """
    depth = history
    while True:
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        try:
            _git("init", "-q", ".", cwd=dest)
            _git("remote", "add", "origin", task.repo_url, cwd=dest)
            _git("fetch", "-q", "--no-tags", f"--depth={depth}", "origin", task.sha, cwd=dest, timeout=900)
            if depth > 1 and _git_mb(dest) > max_git_mb:
                depth = max(1, depth // 4)
                continue
            _git("checkout", "-q", "-b", branch, task.sha, cwd=dest)
            _git("config", "user.name", IDENTITY[0], cwd=dest)
            _git("config", "user.email", IDENTITY[1], cwd=dest)
        except GitError as e:
            return f"could not build the working copy: {e}", depth
        break
    rep = replay(dest, edits, task.repo_id)
    if not rep.ok:
        return f"in-session edits do not apply: {rep.reason}", depth
    return "", depth


def reference_tree(task: Task, edits: list[dict], dest: Path) -> tuple[Path | None, str]:
    """The tree every attempt has started from so far: `fetch`, `export_tree`, `replay`."""
    try:
        checkout = fetch(task.repo_url, task.sha, dest / "repo")
        tree = checkout.export_tree(task.sha, dest / "tree")
    except GitError as e:
        return None, f"could not build the reference tree: {e}"
    rep = replay(tree, edits, task.repo_id)
    if not rep.ok:
        return None, f"in-session edits do not apply to the reference tree: {rep.reason}"
    return tree, ""


def controls_of(task: Task, turns: list[dict]) -> dict:
    """The controls' conversations, as `control_conversations_for` builds them, from turns already loaded."""
    from ..score.attempt import last_recorded_action, resolution_transcript_for, resolution_turns, transcript_for

    return {"cut": transcript_for(task, turns),
            "resolution": resolution_transcript_for(task, resolution_turns(task, turns)),
            "last_action": last_recorded_action(task, turns)}


def shown_turns(task: Task, turns: list[dict]) -> list[dict]:
    """The turns the candidate's conversation is rendered from, up to the cut: `candidate_turns`'s.

    Rendering them needs the corpus's raw transcripts (a result the table cut
    is given back whole from them, `recover.whole_results`), so they are kept
    as rendered from; `release.harbor` renders the conversation again from
    these alone, cut to fit when whole it is too long to pass to an agent.
    """
    from ..score.attempt import candidate_turns

    return [t for t in candidate_turns(task, turns) if (t.get("turn_number") or 0) <= task.cut_turn]


def write_shown_turns(task: Task, turns: list[dict], out: Path) -> bool:
    """Write ``out``/shown_turns.json; True when they render ``out``/conversation.txt exactly (record 3)."""
    from ..corpus.turns import RECORD, RECORD_CHARS, build_excerpt

    kept = shown_turns(task, turns)
    (out / "shown_turns.json").write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n")
    shown = build_excerpt(kept, task.cut_turn, max_chars=RECORD_CHARS, record=RECORD)
    return shown.encode("utf-8") == (out / "conversation.txt").read_bytes()


def freeze(task: Task, turns: list[dict], out: Path, *, branch: str | None, history: int = HISTORY,
           max_git_mb: float = MAX_GIT_MB, scratch: Path | None = None, language: str | None = None) -> Frozen:
    """Freeze one task into ``out`` (its own folder), checked against the reference tree.

    ``turns`` are the task's session turns as `turns_of` gives them; ``language``
    is the repository's, as the corpus records it, which decides its container.
    """
    from ..score.attempt import transcript_for, with_lost_calls

    result = Frozen(task.task_id, branch=branch or DEFAULT_BRANCH)
    full = with_lost_calls(task, turns)
    edits = edits_before(full, task.cut_turn)
    work = Path(tempfile.mkdtemp(prefix=f"freeze-{task.task_id[:20]}-", dir=scratch))
    try:
        ref, why = reference_tree(task, edits, work / "reference")
        if ref is None:
            result.reason = why
            return result
        ws = work / "workspace"
        why, depth = working_copy(task, edits, ws, branch=result.branch, history=history, max_git_mb=max_git_mb)
        if why:
            result.reason = why
            return result
        want, n, want_each = tree_digest(ref)
        got, _, got_each = tree_digest(ws)
        if got != want:
            result.differ = sorted(k for k in set(want_each) | set(got_each) if want_each.get(k) != got_each.get(k))
            result.reason = f"the working copy differs from the reference tree in {len(result.differ)} file(s)"
            return result
        result.files, result.digest = n, got
        result.workdir, session_workdir = workdir_of(ref, edits, full)
        (out / "grading").mkdir(parents=True, exist_ok=True)
        commit_time = int(_git("show", "-s", "--format=%ct", task.sha, cwd=ws).strip() or 0)
        _tar(ws, out / "workspace.tar.gz", "workspace", commit_time)
        # As bytes: a conversation can hold carriage returns from the tools' output, and text mode
        # would translate them on some platforms and on every read.
        (out / "conversation.txt").write_bytes(transcript_for(task, turns).encode("utf-8"))
        if not write_shown_turns(task, turns, out):
            result.reason = "the turns kept do not render the conversation the candidate is shown"
            return result
        grading = {
            "references.json": {
                "oracle": task.oracle, "criterion": task.criterion, "defect": task.defect, "kind": task.kind,
                "signature_path": task.signature_path, "signature_token": task.signature_token,
                "strength": task.strength, "presence_detail": task.presence_detail,
                "oracle_calls": task.oracle_calls, "criterion_calls": task.criterion_calls},
            "controls.json": controls_of(task, turns),
            "task.json": task.to_json(),
            "turns.json": [t for t in full if (t.get("turn_number") or 0) <= (task.resolved_turn or task.cut_turn)],
        }
        for name, body in grading.items():
            (out / "grading" / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
        (out / "task.json").write_text(json.dumps({
            "task_id": task.task_id, "repo_id": task.repo_id, "repo_url": task.repo_url, "sha": task.sha,
            "branch": result.branch, "workdir": result.workdir, "session_workdir": session_workdir,
            "history": depth, "kind": task.kind, "language": language,
            "license": task.license_type, "copyleft": task.is_copyleft, "fingerprint": fingerprint(task),
            "files": n, "tree_digest": got,
            "workspace_sha256": hashlib.sha256((out / "workspace.tar.gz").read_bytes()).hexdigest(),
            "conversation_sha256": hashlib.sha256((out / "conversation.txt").read_bytes()).hexdigest(),
            "shown_turns_sha256": hashlib.sha256((out / "shown_turns.json").read_bytes()).hexdigest(),
        }, indent=1) + "\n")
        result.ok = True
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
