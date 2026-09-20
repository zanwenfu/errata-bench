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
from .edits import edits_before, replay
from .presence import check, repo_url
from .reader import load_session_turns
from .signature import Signature
from .spec import MIN_ORACLE_CHARS, BuildResult, Rejection, Task
from .session_time import session_starts
from .timeline import load_commits_by_repo
from .workspace import GitError, fetch, is_permanent



# How far back a request may sit and still be what the candidate is answering.
# Beyond this the excerpt ends in the middle of the agent's own work, and the
# candidate is continuing a task rather than replying to anyone.
REQUEST_REACH = 80


# Turn types that are not part of the conversation. build_excerpt drops these
# when rendering, so counting them when measuring distance measures something
# the candidate never sees.
_NOISE = {"progress", "file_snapshot", "system_event", "queue_operation"}


def last_user_message(turns: list[dict], cut_turn: int, *, window: int = REQUEST_REACH) -> dict | None:
    """The developer's most recent message at or before the cut, if any.

    Distance is counted in turns the candidate actually sees. Raw turn numbers
    include progress events and file snapshots, which are dropped from the
    excerpt: vaayne/anna's request sits 91 raw turns before its cut, and 68 of
    those are progress rows. Only eight are real actions. Measured raw, nine
    sound tasks were rejected for having "no request to judge scope against"
    when the request was a handful of steps back in what the candidate reads.
    """
    visible = [t for t in turns if (t.get("turn_type") or "") not in _NOISE]
    within = []
    seen = 0
    for t in sorted(visible, key=lambda t: t.get("turn_number") or 0, reverse=True):
        number = t.get("turn_number") or 0
        if number > cut_turn:
            continue
        seen += 1
        if seen > window:
            break
        if t.get("turn_type") == "user_prompt" and (t.get("content") or "").strip():
            within.append(t)
    if not within:
        return None
    return max(within, key=lambda t: t.get("turn_number") or 0)


def calls_behind(turns: list[dict], answer_turn: int, *, keep: int = 60) -> list[dict]:
    """What the agent ran before one of its answers, since the developer's last message.

    The candidate's analogue of this is its own trace: it starts at the cut,
    answers the developer's most recent message, and everything it runs is work
    on that message. So the agent's comparable work is what it ran between that
    same message and the answer being judged -- not its whole session, which
    includes work on other questions entirely.

    Often it is empty, and that is the finding rather than a gap: in three of
    five early cases the agent answered without running anything since the
    developer last spoke.
    """
    ordered = sorted(turns, key=lambda t: t.get("turn_number") or 0)
    spoke = 0
    for t in ordered:
        number = t.get("turn_number") or 0
        if number >= answer_turn:
            break
        if t.get("turn_type") == "user_prompt" and (t.get("content") or "").strip():
            spoke = number
    calls = []
    for t in ordered:
        number = t.get("turn_number") or 0
        if number <= spoke or number >= answer_turn or t.get("turn_type") != "tool_use":
            continue
        detail = t.get("command") or t.get("file_path") or (t.get("content") or "")
        calls.append({"name": t.get("tool_name") or "?", "command": str(detail)[:4000]})
    return calls[-keep:]


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
    seen: set[str] = set()
    # The session start comes from its turns, not from sessions.created_at,
    # which is a completion timestamp: across the eighteen sessions that produced
    # tasks it lands after the last turn in twelve and mid-session in six, never
    # before the first. Using it selected the last commit before the session
    # ENDED, so a candidate could be handed a tree containing commits the agent
    # made during the session -- in one case its own resolution, which it then
    # "passed" three times out of three.
    starts = session_starts({r["session_id"] for r in located})
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

        # A conversation that leaks is repaired before it is rejected. Removing
        # the turns that carry the hint recovers nine of fourteen leaking tasks
        # while keeping 78-100% of the text, so the candidate still has the work
        # the original agent had. Only a leak that survives redaction -- or one
        # diffuse enough to have no turns to remove -- ends the task.
        if row.get("signals_trouble") and not row.get("redaction_worked"):
            # The conversation already signals that something is wrong, so a
            # candidate can take the hint rather than check anything. Measured,
            # not assumed: of twelve tasks scored before any such gate, every
            # passing verdict came from a task whose context leaked -- four of
            # six leaky tasks passed, against none of six clean ones.
            #
            # Read by a model rather than by pattern. A regex version scored
            # oddessentials-83 clean because nobody apologised, while the
            # conversation contains "SC-003 scope is incorrect. Fix: Change from
            # 'modified files' to 'entire repo typecheck surface' to prevent
            # local pass / CI fail divergence" -- it hands over the very
            # rationale the task asks the candidate to avoid inventing.
            reject(f"the context already signals trouble: {row.get('leak_reason','')}")
            continue

        turns = turns_by_session.get(row["session_id"]) or []
        by_turn = {t.get("turn_number"): t for t in turns}

        # A missing verdict is not a pass. Screened rows written before the scope
        # gate existed carry no `within_scope` at all, and testing only for False
        # let every one of them through -- including nsega-mcp-todoist, whose
        # request is "create the pull request" and whose defect is a linter
        # version in a CI workflow nobody mentioned. A gate that silently
        # abstains when its input is absent is not a gate.
        if not row.get("within_scope"):
            reject(
                "the defect is outside the requested work: "
                f"{row.get('scope_reason') or 'the scope gate did not run on this row'}"
            )
            continue

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
        # Both must be answers the agent wrote, not tool calls. A located turn
        # can land on a tool_use row, whose content is the serialised call --
        # heath0xFF-hChat took raw JSON as both its oracle and its criterion, and
        # calibration certified it "sound", because a judge comparing two blobs
        # of JSON will happily report that they differ.
        failed_turn = by_turn.get(row["failed"]) or {}
        resolved_turn = by_turn.get(row["resolved"]) or {}
        if failed_turn.get("turn_type") != "assistant_response":
            reject(
                f"the failing turn is a {failed_turn.get('turn_type') or 'missing turn'}, "
                "not an answer the agent wrote"
            )
            continue
        if resolved_turn.get("turn_type") != "assistant_response":
            reject(
                f"the resolving turn is a {resolved_turn.get('turn_type') or 'missing turn'}, "
                "not an answer the agent wrote"
            )
            continue
        oracle = (failed_turn.get("content") or "").strip()
        criterion = (resolved_turn.get("content") or "").strip()
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
        started_ns = starts.get(row["session_id"])
        if started_ns is None:
            reject("no turn in this session carries a timestamp, so its start is unknown")
            continue
        sha = base_commit(repo_id, started_ns, commits)
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
        # A task is named for its repository and the turn the developer
        # objected at, which is not unique: 93 of 400 moments in one run share
        # a (repository, turn) pair with another session. None has survived to
        # a built task yet, and the funnel is the only reason. Two tasks under
        # one name is worse than one task fewer -- they overwrite each other's
        # answers, each is reported as "an earlier version" of the other, and a
        # full pass never converges because whichever is written second wins.
        if task_id in seen:
            reject(f"another session already built {task_id}; two tasks cannot share a name")
            continue
        url = repo_url(repo_id, repo.url)

        base = scratch or Path(tempfile.gettempdir()) / "errata-bench-build"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as d:
            try:
                checkout = fetch(url, sha, Path(d) / "repo")
                tree = checkout.export_tree(sha, Path(d) / "tree")
            except GitError as e:
                # Named, so the funnel distinguishes a task worth retrying
                # from one whose code no longer exists anywhere.
                why = ("the code is gone from the remote"
                       if is_permanent(e) else "could not build the tree")
                reject(f"{why}: {str(e)[:110]}")
                continue
            # The base commit predates the session; the agent's own edits up to
            # the cut are replayed onto it so the tree matches the transcript.
            # Seven of twelve calibrated tasks had such edits and none of them
            # were in the tree. An edit that will not apply means this commit
            # is not what the agent was editing, and the task is rejected.
            edits = edits_before(turns, row["cut"])
            rep = replay(tree, edits, repo_id)
            if not rep.ok:
                reject(f"the agent's in-session edits do not apply to the base commit: {rep.reason[:120]}")
                continue
            presence = check(task_id, sig, tree)

        # Presence is advisory, not a gate. It can only confirm a defect it can
        # find as a string in a file, and twenty-two of the fifty-one defects
        # located in a four-hundred-moment run have no such trace: "reported the
        # service as running without verifying it", "associated the 401s with
        # stale configuration without verifying the cause", "declared the release
        # complete after local testing without committing". Those are claims made
        # without checking -- the whole premise of this benchmark -- and gating on
        # a file signature discarded every one of them.
        #
        # What still blocks a task is a positive contradiction: an introduced
        # defect whose own premise fails, meaning the classification and the
        # signature disagree. That is a task contradicting itself, not a task
        # this check merely cannot see.
        if presence.strength == "verified" and not presence.present:
            reject(presence.detail)
            continue

        # Claimed only once the task really exists. Claimed at the point the
        # name is computed, a row that went on to fail the tree build or the
        # edit replay would hold the name against a later row that would have
        # succeeded -- refusing a good task to protect against a collision with
        # one that was never built.
        seen.add(task_id)
        result.tasks.append(
            Task(
                task_id=task_id,
                repo_id=repo_id,
                repo_url=url,
                sha=sha,
                session_id=row["session_id"],
                cut_turn=row["cut"],
                redacted_turns=row.get("redacted_turns") or [],
                rewritten_turns=row.get("rewritten_turns") or {},
                failed_turn=row["failed"],
                complaint_turn=complaint,
                resolved_turn=row["resolved"],
                oracle=oracle,
                criterion=criterion,
                oracle_calls=calls_behind(turns, row["failed"]),
                criterion_calls=calls_behind(turns, row["resolved"]),
                defect=row.get("defect", ""),
                kind=row["kind"],
                signature_path=sig.path,
                signature_token=sig.token,
                strength=presence.strength,
                presence_detail=presence.detail,
                license_type=repo.license_type,
                is_copyleft=repo.is_copyleft,
                rounds=row.get("rounds", 1),
                edits_replayed=rep.applied,
            )
        )
    return result
