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


def build_evidence(
    repo_id: str,
    repo_commits: list[CommitInfo],
    after_ns: int,
    deferred_files: set[str],
    *,
    patches: dict[str, str] | None = None,
    max_subjects: int = 900,
    max_patches: int = 12,
    patch_chars: int = 6_000,
) -> str:
    """Later work in this repo, rendered for a reader to judge.

    Broad by design: a consequence often lands in a file the agent never
    touched, so every later commit contributes its subject line. Those are
    cheap -- 62 characters at the median, so even 800 of them is ~50k chars --
    and a subject like "fix: handle the case we skipped earlier" is exactly the
    signal that a narrow file-overlap filter would miss.

    Diffs are the opposite: 7.3k chars at the median and 128k at p95, enough to
    exhaust a context on one commit. So full messages and capped diffs go only
    to commits that touch the deferred files.
    """
    later = [c for c in repo_commits if c.author_ns and c.author_ns > after_ns]
    if not later:
        return "(no commits recorded in this repository after this session)"

    overlapping = [c for c in later if c.files & deferred_files]
    lines = [
        f"{len(later)} commits were recorded in {repo_id} after this session.",
        f"{len(overlapping)} of them touch the files involved in the deferral.",
        "",
        "--- subject line of every later commit, oldest first ---",
    ]
    for c in later[:max_subjects]:
        subject = (c.message or "").split("\n", 1)[0][:120]
        lines.append(f"  {subject}")
    if len(later) > max_subjects:
        lines.append(f"  [... {len(later) - max_subjects} more ...]")

    if overlapping:
        lines += ["", "--- commits touching the deferred files, in full ---"]
        for c in overlapping[:max_patches]:
            lines.append(f"\n=== files: {sorted(c.files & deferred_files)[:6]}")
            lines.append((c.message or "")[:1500])
            diff = c.patch or (patches or {}).get(c.commit_sha, "")
            if diff:
                lines.append(f"--- diff (capped) ---\n{diff[:patch_chars]}")
    return "\n".join(lines)


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
