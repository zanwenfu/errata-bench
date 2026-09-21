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


# Multiplier from each arrow timestamp unit to nanoseconds.
_UNIT_SCALE = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}


def _to_ns(column, arrow_type) -> list[int | None]:
    """Normalise an arrow timestamp column to integer nanoseconds.

    The unit is read from the arrow type object, not sniffed out of its string
    form. A substring test is wrong here and fails silently: ``"s," in
    "timestamp[ns, tz=UTC]"`` is true because of the ``ns,``, so a nanosecond
    column took the seconds branch and was scaled by a further 10^9. Every
    session timestamp then exceeded every commit timestamp, and "did any commit
    land after this session" answered zero for the whole corpus -- which reads
    as "nothing to trace" rather than as a bug.
    """
    raw = column.cast("int64").to_pylist()
    unit = getattr(arrow_type, "unit", None)
    if unit not in _UNIT_SCALE:
        raise ValueError(f"unexpected timestamp unit {unit!r} on {arrow_type}")
    scale = _UNIT_SCALE[unit]
    if scale == 1:
        return raw
    return [None if v is None else v * scale for v in raw]


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
    message: str = ""
    patch: str = ""
    commit_sha: str = ""



def load_commits_by_repo(*, with_patches: bool = False) -> dict[str, list[CommitInfo]]:
    """Commits per repository, oldest first.

    Patches are left out by default. A median patch is 7.3k characters and p95
    is 128k, so loading all 14,459 of them holds roughly a gigabyte -- reckless
    on a machine with a few GB free, and wasted when only a handful of commits
    are ever rendered. Fetch those with :func:`load_patches`.
    """
    columns = ["repo_id", "author_date", "files_changed", "status", "commit_message", "commit_sha"]
    if with_patches:
        columns.append("patch")
    t = pq.read_table(CORPUS / "commits.parquet", columns=columns)
    ns = _to_ns(t.column("author_date"), t.schema.field("author_date").type)
    patches = t.column("patch").to_pylist() if with_patches else None
    out: dict[str, list[CommitInfo]] = collections.defaultdict(list)
    for i, (rid, when, files, status, message, sha) in enumerate(
        zip(
            t.column("repo_id").to_pylist(),
            ns,
            t.column("files_changed").to_pylist(),
            t.column("status").to_pylist(),
            t.column("commit_message").to_pylist(),
            t.column("commit_sha").to_pylist(),
        )
    ):
        if status != "ok" or not files or when is None or not sha:
            continue
        paths = frozenset(
            line.split("\t")[-1].strip() for line in files.split("\n") if "\t" in line
        )
        out[rid].append(
            CommitInfo(
                repo_id=rid,
                author_ns=when,
                files=paths,
                message=message or "",
                patch=(patches[i] or "") if patches else "",
                commit_sha=sha,
            )
        )
    for rid in out:
        out[rid].sort(key=lambda c: c.author_ns or 0)
    return out


def load_patches(shas: set[str]) -> dict[str, str]:
    """Fetch diffs for specific commits, so the big column is read once."""
    if not shas:
        return {}
    t = pq.read_table(CORPUS / "commits.parquet", columns=["commit_sha", "patch"])
    out = {}
    for sha, patch in zip(
        t.column("commit_sha").to_pylist(), t.column("patch").to_pylist()
    ):
        if sha in shas and patch:
            out[sha] = patch
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




