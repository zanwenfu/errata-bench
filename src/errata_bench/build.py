"""Assemble runnable tasks from located trajectories.

This is the join: a trajectory gives the four turns, a signature says what the
defect looks like in a tree, the corpus gives the repository, and the timeline
gives a commit from before the session started. A task needs all four, and this
rejects loudly when one is missing rather than filling in a default.

The commit choice is the part that has already gone wrong once. An earlier
driver took the *last* sha a session produced, which is the state after the work
-- for moltis that commit's message is literally the turn-500 fix. Candidates
were handed a tree with the defect already repaired. The anchor here is the last
commit authored *before* the session began, which is the closest thing the
corpus has to what the developer started from.

It is not exactly that. The developer's working tree can hold uncommitted
changes, and in blittle/pressy the entire defect lived there -- the broken action
name is in the agent's own file reads and in no commit in the repository. That
is why presence is checked against the materialised tree instead of assumed, and
why pressy is rejected rather than silently shipped as a task measuring nothing.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .corpus import load_repos
from . import priming
from .presence import check, repo_url
from .reader import load_session_turns
from .signature import Signature
from .spec import MIN_ORACLE_CHARS, BuildResult, Rejection, Task
from .timeline import load_commits_by_repo, load_sessions
from .workspace import GitError, fetch



def last_user_message(turns: list[dict], cut_turn: int, *, window: int = 80) -> dict | None:
    """The developer's most recent message at or before the cut, if any."""
    users = [
        t
        for t in turns
        if t.get("turn_type") == "user_prompt"
        and (t.get("content") or "").strip()
        and cut_turn - window <= (t.get("turn_number") or 0) <= cut_turn
    ]
    if not users:
        return None
    return max(users, key=lambda t: t.get("turn_number") or 0)


def base_commit(repo_id: str, session_ns: int | None, commits) -> str | None:
    """The last commit in this repository before the session started.

    Later commits are excluded even though they are tempting: they are the work
    the session produced, including the fix a candidate is supposed to arrive at
    on its own.
    """
    if session_ns is None:
        return None
    earlier = [
        c for c in commits.get(repo_id, []) if c.author_ns and c.author_ns < session_ns
    ]
    if not earlier:
        return None
    return max(earlier, key=lambda c: c.author_ns).commit_sha


def build(located: list[dict], *, scratch: Path | None = None) -> BuildResult:
    """Turn located trajectories into tasks, checking each one's setup.

    ``located`` rows carry the trajectory fields (failed/complaint/resolved turns,
    defect, resolution) and the derived signature fields (kind, token, path).
    """
    result = BuildResult()
    sessions = load_sessions()
    commits = load_commits_by_repo()
    repos = load_repos()
    turns_by_session = load_session_turns({r["session_id"] for r in located})

    for row in located:
        repo_id = row.get("repo_id") or ""
        complaint = row.get("complaint", -1)

        def reject(why: str) -> None:
            result.rejected.append(Rejection(repo_id, complaint, why))

        if not row.get("usable"):
            reject(row.get("reason", "trajectory not usable"))
            continue
        if not row.get("kind"):
            reject("no signature was derived")
            continue

        turns_here = turns_by_session.get(row["session_id"]) or []
        primed = priming.check(turns_here, row["cut"])
        if primed.primed:
            # The conversation already signals that something is wrong, so a
            # candidate can take the hint rather than check anything. This is
            # measurable and severe: of twelve tasks scored before this gate
            # existed, every single "resolved" verdict came from a task whose
            # context leaked -- 4 of 6 leaky tasks resolved, against 0 of 6
            # clean ones. The benchmark was reading hint-taking, not care.
            reject(
                f"the context already signals trouble ({primed.concessions} agent "
                f"concessions, {primed.frustrations} frustrated user turns, "
                f"{primed.prior_pushbacks} earlier pushbacks)"
            )
            continue

        turns = turns_by_session.get(row["session_id"]) or []
        by_turn = {t.get("turn_number"): t for t in turns}

        asked = row.get("asks_for_something")
        why_not = row.get("request_reason", "")
        if asked is False:
            # A candidate answers the developer's most recent message. When the
            # excerpt ends without one -- or ends on pasted terminal output with
            # no question in it -- there is nothing to answer, and a reasonable
            # model summarises the logs. Six of nine attempts on present-defect
            # tasks were scored off_target for exactly this: moltis ends on 1,837
            # characters of validation output the developer pasted with no
            # question attached, and oddessentials-221 has no user turn within
            # eighty turns of its cut. Neither measures the model.
            reject(f"nothing for the candidate to answer: {why_not}")
            continue
        oracle = ((by_turn.get(row["failed"]) or {}).get("content") or "").strip()
        criterion = ((by_turn.get(row["resolved"]) or {}).get("content") or "").strip()
        if len(oracle) < MIN_ORACLE_CHARS:
            reject(f"the failed answer is {len(oracle)} characters: too short to test against")
            continue
        if len(criterion) < MIN_ORACLE_CHARS:
            reject(f"the resolution is {len(criterion)} characters: too short to judge against")
            continue

        repo = repos.get(repo_id)
        if repo is None:
            reject("repository not in the corpus")
            continue
        sha = base_commit(
            repo_id, getattr(sessions.get(row["session_id"]), "created_ns", None), commits
        )
        if sha is None:
            reject("no commit exists before the session started")
            continue

        sig = Signature(
            kind=row["kind"],
            path=row.get("path") or "",
            token=row.get("token") or "",
            is_symlink_defect=bool(row.get("symlink")),
            reasoning=row.get("sig_reasoning") or row.get("reasoning") or "",
        )
        task_id = f"{repo_id.replace('/', '-')}-{complaint}"
        url = repo_url(repo_id, repo.url)

        base = scratch or Path(tempfile.gettempdir()) / "errata-bench-build"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as d:
            try:
                checkout = fetch(url, sha, Path(d) / "repo")
                tree = checkout.export_tree(sha, Path(d) / "tree")
            except GitError as e:
                reject(f"could not build the tree: {str(e)[:110]}")
                continue
            presence = check(task_id, sig, tree)

        if not presence.usable:
            reject(presence.detail)
            continue

        result.tasks.append(
            Task(
                task_id=task_id,
                repo_id=repo_id,
                repo_url=url,
                sha=sha,
                session_id=row["session_id"],
                cut_turn=row["cut"],
                failed_turn=row["failed"],
                complaint_turn=complaint,
                resolved_turn=row["resolved"],
                oracle=oracle,
                criterion=criterion,
                defect=row.get("defect", ""),
                kind=row["kind"],
                signature_path=sig.path,
                signature_token=sig.token,
                strength=presence.strength,
                presence_detail=presence.detail,
                license_type=repo.license_type,
                is_copyleft=repo.is_copyleft,
                rounds=row.get("rounds", 1),
            )
        )
    return result
