"""Git, anonymously and in pieces: list refs, fetch trees without blobs, then only the blobs wanted.

Every call runs with credential helpers and prompts off, so a repository that
turns private or asks for a login fails at once instead of waiting on a prompt
or reaching for the developer's keychain. Blobs are streamed to files, never
held whole in memory: one session's transcript runs to tens of megabytes, and
a repository can hold hundreds.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/echo", "SSH_ASKPASS": "/bin/echo",
           "GCM_INTERACTIVE": "never", "GIT_CONFIG_NOSYSTEM": "1"}
TIMEOUT_S = 900
BATCH = 1000  # object ids per prefetch request


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: Path | None = None, input: bytes | None = None, timeout: int = TIMEOUT_S) -> bytes:
    """Run git with no credentials; its stdout, or GitError with the tail of its stderr."""
    try:
        p = subprocess.run(["git", "-c", "credential.helper=", "-c", "core.askPass=", *args], cwd=cwd, env=GIT_ENV,
                           input=input, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from e
    if p.returncode:
        raise GitError(f"git {args[0]}: {p.stderr.decode(errors='replace').strip()[-300:]}")
    return p.stdout


def ls_remote(url: str) -> dict[str, str]:
    """Every ref the remote advertises, name to object id."""
    refs = {}
    for line in git("ls-remote", url, timeout=120).decode(errors="replace").splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name] = sha
    return refs


def bare_promisor(path: Path, url: str) -> None:
    """A bare repository whose missing blobs are fetched from `url` when read."""
    if (path / "HEAD").exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(path))
    git("remote", "add", "origin", url, cwd=path)
    git("config", "remote.origin.promisor", "true", cwd=path)
    git("config", "remote.origin.partialclonefilter", "blob:none", cwd=path)


def fetch_trees(repo: Path, refspecs: list[str]) -> None:
    """Fetch commits and trees for the refspecs, no blobs, tips only."""
    git("fetch", "-q", "--no-tags", "--filter=blob:none", "--depth=1", "origin", *refspecs, cwd=repo)


def blob_ids(repo: Path, treeish: str) -> dict[str, str]:
    """Every blob under a tree, path to object id, read from the trees alone.

    `ls-tree` needs no blob, so it never triggers a fetch in a blobless
    repository, where `cat-file --batch-check` would fetch each blob it is
    asked about.
    """
    out = {}
    for entry in git("ls-tree", "-r", "-z", treeish, cwd=repo).split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        _, kind, oid = meta.split()
        if kind == b"blob":
            out[path.decode(errors="replace")] = oid.decode()
    return out


def prefetch(repo: Path, oids: list[str]) -> None:
    """Fetch the blobs in batches, the way git's own lazy fetch asks for missing objects."""
    oids = sorted(set(oids))
    for i in range(0, len(oids), BATCH):
        git("-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags", "--no-write-fetch-head",
            "--recurse-submodules=no", "--filter=blob:none", "--stdin", cwd=repo,
            input="\n".join(oids[i:i + BATCH]).encode())


class _Reader:
    """`git cat-file --batch`, one object at a time, so no object is held whole unless asked."""

    def __init__(self, repo: Path):
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo, env=GIT_ENV, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def open(self, oid: str) -> int:
        self.proc.stdin.write(oid.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) != 3:
            raise GitError(f"cat-file: no object {oid}")
        return int(header[2])

    def copy(self, size: int, sink, chunk: int = 1 << 20) -> None:
        while size:
            piece = self.proc.stdout.read(min(chunk, size))
            if not piece:
                raise GitError("cat-file: object ended early")
            if sink is not None:
                sink.write(piece)
            size -= len(piece)
        self.proc.stdout.read(1)  # the newline after each object

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


def read_small(repo: Path, oids: list[str], cap: int = 8 << 20) -> dict[str, bytes | None]:
    """Small blobs (metadata) by id. One over `cap` is skipped and read as None.

    A checkpoint's metadata.json once grew past 100 MB (entireio/cli#1937), so
    the cap is what keeps one repository from filling memory.
    """
    out: dict[str, bytes | None] = {}
    reader = _Reader(repo)
    try:
        for oid in oids:
            size = reader.open(oid)
            if size > cap:
                reader.copy(size, None)
                out[oid] = None
                continue
            buf = io.BytesIO()
            reader.copy(size, buf)
            out[oid] = buf.getvalue()
    finally:
        reader.close()
    return out


def write_blobs(repo: Path, targets: list[tuple[str, Path]]) -> int:
    """Stream each blob (by id) to its file, written whole or not at all; the bytes written."""
    total = 0
    if not targets:
        return total
    reader = _Reader(repo)
    try:
        for oid, path in targets:
            size = reader.open(oid)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            with open(tmp, "wb") as f:
                reader.copy(size, f)
            tmp.replace(path)
            total += size
    finally:
        reader.close()
    return total
