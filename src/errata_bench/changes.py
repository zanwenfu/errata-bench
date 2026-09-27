"""What an attempt changed in its working copy, read from the tree alone.

A snapshot of every file before and after (size, contents hash, the executable
bit), the difference between the two, and the contents of the files that decide
whether a defect survived. Taken out of `score.attempt` (09-27) unchanged, so the
verifier that runs inside a Harbor task's container, where nothing but Python's
standard library is installed, reads a tree exactly as this harness does. So
this module imports nothing outside the standard library, and must not: the
guards run the exported verifier with nothing else to import.
"""

from __future__ import annotations

import os
from pathlib import Path


def within(tree: Path, rel: str) -> Path | None:
    """The path `rel` names inside `tree`, or None if it names anything else.

    For paths the *harness* joins to the tree, not the ones a candidate's tools
    ask for -- those go through `_safe`, which has to understand the container
    mount as well. `task.signature_path` is written by a model reading a
    transcript (`find/signature.py` asks for the path "exactly as the text
    gives it"), and transcripts are full of absolute paths. Joined unguarded,
    an absolute one replaces the tree outright: a probe with
    `signature_path="/Users/…/id_rsa"` read that file off the host and put its
    contents in the stored answer row, and since D-32 into the judge's prompt.

    The parent is resolved and the leaf is not, so a symlinked directory cannot
    be used to step outside while a symlink *at* the path is still reported as
    a link rather than read through, which is what `snapshot` and `capture`
    both promise.
    """
    if not rel or os.path.isabs(rel) or rel.startswith("~"):
        return None
    p = tree / rel
    try:
        p.parent.resolve(strict=False).relative_to(tree.resolve(strict=False))
    except (ValueError, OSError, RuntimeError):
        return None
    return p


# Directories a toolchain writes on its own account while running, never source.
TOOL_CACHES = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    "node_modules", ".gradle", ".cache",
})


def snapshot(tree: Path, keep: str = "") -> dict[str, tuple]:
    """Size, contents hash and mode per file, not the modification time.

    On mtime alone, writing a file its own bytes back counted as a change --
    and `wrote` is half of whether a candidate did any work, which for an
    introduced-defect task is the whole pass line. A no-op write passed the
    guard that exists to stop a candidate passing by doing nothing.

    The executable bit is part of the state because it is sometimes the whole
    defect: a CI script, a git hook or a claude hook that is not executable
    does not run. On contents alone a candidate that fixed exactly that was
    recorded as having changed nothing, so `wrote` was False and it read as a
    candidate that did no work.

    A file that cannot be read is kept with its error in place of a hash
    rather than dropped. Dropped, it was absent from the second snapshot and
    `diff` reported it deleted although it was still there -- a change the
    candidate did not make, fed to the declared-versus-actual check.
    """
    import hashlib

    out = {}
    for p in tree.rglob("*"):
        if ".git" in p.parts:
            continue
        # A link is recorded as a link and never followed. The working copy is
        # bind-mounted into the container, so one `ln -s ~/.ssh/id_rsa
        # notes.txt` inside it points a name in the tree at any file the
        # harness itself can read -- and the harness reads it here and in
        # `capture`, which put that file's contents into the stored answer
        # row. The candidate's own `read_file` was never the way in: `_safe`
        # refuses it as a path that escapes the working copy. `rglob` does not
        # descend into a linked directory, which was checked rather than
        # assumed. `lstat`, because `stat` follows too.
        if p.is_symlink():
            try:
                target = os.readlink(p)
            except OSError as e:
                target = f"unreadable link: {type(e).__name__}"
            out[str(p.relative_to(tree))] = (-2, f"symlink -> {target}", "-")
            continue
        if not p.is_file():
            continue
        # What a test run leaves behind is not an edit (G-33). The working
        # copy is bind-mounted into the container, so `pytest` writing
        # __pycache__ and .pytest_cache made a candidate that ran the tests and
        # edited nothing read as one that wrote -- and `wrote` is half of
        # whether it did any work. Only names no repository uses for source;
        # `dist/`, `build/` and `target/` are sometimes committed, so a
        # compiled artefact is still counted, and is named in the row.
        inside = p.relative_to(tree).parts
        if TOOL_CACHES.intersection(inside) or p.suffix in (".pyc", ".pyo"):
            # Unless it is the file this task is about. None of the fifteen
            # built tasks has its defect under one of these names, and a
            # repository that commits its node_modules could.
            rel = "/".join(inside)
            if not (keep and (keep in rel or rel.endswith(keep))):
                continue
        # The bit that matters, not the whole mode: ownership and the group
        # and other bits move for reasons no candidate caused.
        mode = "x" if p.stat().st_mode & 0o111 else "-"
        try:
            body = p.read_bytes()
        except OSError as e:
            out[str(p.relative_to(tree))] = (-1, f"unreadable: {type(e).__name__}", mode)
            continue
        out[str(p.relative_to(tree))] = (
            len(body), hashlib.blake2b(body, digest_size=16).hexdigest(), mode,
        )
    return out


def capture(tree: Path, task, changed: dict[str, str]) -> dict[str, str]:
    """Read back the files that decide whether the defect survived.

    ``task`` is anything with a task's `signature_path` and `signature_token`.

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
        p = within(tree, rel)
        if p is None:
            continue
        # Never through a link, for the reason `snapshot` gives: what the
        # link points at is chosen by the candidate and read by the harness.
        if p.is_file() and not p.is_symlink():
            try:
                out[rel] = p.read_text(errors="replace")
            except OSError:
                pass
        elif p.is_symlink():
            out[rel] = f"[a symbolic link, not followed: -> {os.readlink(p)}]"
    if task.signature_token:
        for p in tree.rglob("*"):
            if not p.is_file() or p.is_symlink() or ".git" in p.parts:
                continue
            try:
                body = p.read_text(errors="replace")
            except OSError:
                continue
            if task.signature_token in body:
                out[str(p.relative_to(tree))] = body
    return out


def diff(before: dict[str, tuple], after: dict[str, tuple]) -> dict[str, str]:
    changes = {}
    for path, mark in after.items():
        if path not in before:
            changes[path] = "added"
        elif before[path] != mark:
            was = before[path]
            # Named apart from a content change, because "modified" reads to
            # both the judge and the honesty check as "its contents differ".
            # Only the keys of this map are used downstream, so the extra
            # label costs nothing.
            changes[path] = ("made executable" if was[:2] == mark[:2] and mark[2] == "x"
                             else "made non-executable" if was[:2] == mark[:2]
                             else "modified")
    for path in before:
        if path not in after:
            changes[path] = "deleted"
    return changes


# How much of the captured tree to keep on the answer row: per file, and in
# total. The capture holds the file the signature names, every file the
# candidate changed, and every file still containing the defect's token -- and
# "changed" is a before-and-after listing of a tree that is bind-mounted into
# the container, so a candidate that ran the project's build has "changed"
# every file that build wrote. Five thousand build outputs of 50 KB each is a
# single 200 MB line, which `append` writes in one go and every later `load`
# reads back whole, for a stage that only wants to count rows.
#
# The total budget is what makes the row bounded; a per-file cap alone does
# not. The named file goes in first because it is the one the token check
# reads, then the smallest of the rest, so a row holds as many useful files as
# it can rather than one enormous one.
KEPT_FILE_CHARS = 40_000
KEPT_STATE_CHARS = 2_000_000


def capped(state: dict[str, str], first: str = "") -> dict[str, str]:
    """As much of the captured tree as fits, smallest files after the named one."""
    def cut(body: str) -> str:
        if len(body) <= KEPT_FILE_CHARS:
            return body
        # Said in the file's own text, so nothing later reads a truncated file
        # as one in which the token is simply absent.
        return body[:KEPT_FILE_CHARS] + "\n... [cut: file continues]"

    state = state or {}
    order = ([first] if first in state else []) + sorted(
        (k for k in state if k != first), key=lambda k: len(state[k])
    )
    out, total = {}, 0
    for path in order:
        body = cut(state[path])
        if total + len(body) > KEPT_STATE_CHARS:
            break
        out[path] = body
        total += len(body)
    return out
