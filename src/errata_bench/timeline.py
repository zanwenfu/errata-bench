"""Time and identity joins across sessions and commits.

Two traps live here, both of which produce silent wrong answers rather than
errors, and both of which cost real debugging time when first hit:

1. The timestamp columns use different resolutions. ``sessions.created_at`` is
   ``timestamp[ns]`` and ``commits.author_date`` is ``timestamp[us]``. Casting
   both to int64 and comparing makes every commit look like January 1970, so
   "did any commit happen after this session" is uniformly false -- which reads
   as "nothing to trace" rather than as a bug.

2. ``repo_id`` disagrees between ``conversations.parquet`` and
   ``sessions.parquet`` for 397 of 5,785 sessions (6.9%), systematically pairing
   a repo with its fork (``marcus-sa/brain`` against ``osabiohq/osabio``). A
   join keyed on the conversations value searches the wrong repository and finds
   nothing.

Everything here normalises to UTC nanoseconds and treats ``sessions.parquet`` as
the authority on which repository a session belongs to.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass

import pyarrow.parquet as pq

from .corpus import CORPUS

NS_PER_US = 1000


def _to_ns(column, arrow_type) -> list[int | None]:
    """Normalise an arrow timestamp column to integer nanoseconds."""
    raw = column.cast("int64").to_pylist()
    unit = str(arrow_type)
    if "us" in unit:
        return [None if v is None else v * NS_PER_US for v in raw]
    if "ms" in unit:
        return [None if v is None else v * NS_PER_US * 1000 for v in raw]
    if "s," in unit or unit.endswith("[s]"):
        return [None if v is None else v * NS_PER_US * 1_000_000 for v in raw]
    return raw  # already nanoseconds


@dataclass
class SessionInfo:
    session_id: str
    repo_id: str  # authoritative, from sessions.parquet
    created_ns: int | None


@dataclass
class CommitInfo:
    repo_id: str
    author_ns: int | None
    files: frozenset[str]  # repo-relative paths


def load_sessions() -> dict[str, SessionInfo]:
    t = pq.read_table(
        CORPUS / "sessions.parquet", columns=["session_id", "repo_id", "created_at"]
    )
    ns = _to_ns(t.column("created_at"), t.schema.field("created_at").type)
    return {
        sid: SessionInfo(sid, rid, v)
        for sid, rid, v in zip(
            t.column("session_id").to_pylist(), t.column("repo_id").to_pylist(), ns
        )
    }


def load_commits_by_repo() -> dict[str, list[CommitInfo]]:
    t = pq.read_table(
        CORPUS / "commits.parquet",
        columns=["repo_id", "author_date", "files_changed", "status"],
    )
    ns = _to_ns(t.column("author_date"), t.schema.field("author_date").type)
    out: dict[str, list[CommitInfo]] = collections.defaultdict(list)
    for rid, when, files, status in zip(
        t.column("repo_id").to_pylist(),
        ns,
        t.column("files_changed").to_pylist(),
        t.column("status").to_pylist(),
    ):
        if status != "ok" or not files or when is None:
            continue
        paths = frozenset(
            line.split("\t")[-1].strip() for line in files.split("\n") if "\t" in line
        )
        out[rid].append(CommitInfo(rid, when, paths))
    for rid in out:
        out[rid].sort(key=lambda c: c.author_ns or 0)
    return out


def to_repo_relative(local_path: str, repo_id: str) -> str | None:
    """Turn an agent's absolute local path into a repo-relative one.

    Tool calls record paths like ``/Users/michael/Code/cipher-box/packages/...``
    while commits store ``packages/...``. Without this every path comparison
    fails and the failure looks like "the file was never touched again".
    """
    name = repo_id.split("/")[-1]
    match = re.search(r"(?:^|/)" + re.escape(name) + r"/(.+)$", local_path)
    return match.group(1) if match else None


def commits_after(
    repo_commits: list[CommitInfo], after_ns: int, *, touching: set[str] | None = None
) -> list[CommitInfo]:
    """Commits in this repo later than ``after_ns``, optionally touching files.

    Note the corpus boundary: commits stop at roughly the last recorded session,
    so a consequence landing after that window is simply not present. Absence of
    a later commit is therefore weak evidence, not proof nothing happened.
    """
    later = [c for c in repo_commits if c.author_ns and c.author_ns > after_ns]
    if touching:
        later = [c for c in later if c.files & touching]
    return later
