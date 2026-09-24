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

from ..corpus.recover import foreign_transcript, has_transcript, recovered, subagent_edits
from ..corpus.sessions import load_repos
from .consistency import check as consistency_check, tree_changing_git, why_inconsistent
from .edits import OUTSIDE, edits_before, replay, unreplayed_writes
from .presence import check, repo_url
from ..corpus.turns import load_session_turns
from ..find.signature import Signature
from ..spec import MIN_ORACLE_CHARS, BuildResult, Rejection, Task
from ..corpus.session_time import session_starts
from ..corpus.timeline import load_commits_by_repo, session_checkpoints
from .workspace import GitError, fetch, is_permanent



# How far back a request may sit and still be what the candidate is answering.
# Beyond this the excerpt ends in the middle of the agent's own work, and the
# candidate is continuing a task rather than replying to anyone.
# How far back to look for the developer's request. Unlimited, because the
# candidate sees every user message at or before the cut: `build_excerpt`
# squeezes tool traffic when it overruns and never drops a user prompt, so
# there is no distance at which the request becomes invisible. At 80 visible
# turns it rejected five tasks whose requests sit 94 to 208 turns back and are
# plainly in the excerpt -- "Implement the following plan: ...", "Check
# ~/Developer/Projects/designs/wtload.pen and get started". Whether a distant
# request is still the thing to answer is what `asks_for_something` and
# `in_scope` decide, and they read the text rather than counting rows.
REQUEST_REACH = 0   # 0 means no limit


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
        if window and seen > window:
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


def base_commit(repo_id: str, session_ns: int | None, commits,
                own: set[str] | frozenset[str] = frozenset()) -> str | None:
    """The last commit in this repository before the session started.

    Later commits are excluded even though they are tempting: they are the work
    the session produced, including the fix a candidate is supposed to arrive at
    on its own.

    "Before" is when the commit entered the history, not when it was authored:
    a rebase or an amend keeps the author date, and 122 of 1,565 bases chosen
    by it were committed after their session started. And a commit recorded
    under one of this session's own checkpoints (`own`) is never the base, in
    whichever order its dates fall: 5 of those 1,565 were.
    """
    if session_ns is None:
        return None
    when = lambda c: c.commit_ns if c.commit_ns is not None else c.author_ns
    # By sha: one commit is a row per checkpoint that recorded it.
    mine = {c.commit_sha for c in commits.get(repo_id, []) if c.checkpoint_pk in own}
    earlier = [c for c in commits.get(repo_id, [])
               if when(c) and when(c) < session_ns and c.commit_sha not in mine]
    if not earlier:
        return None
    return max(earlier, key=when).commit_sha


def subagent_edits_before(session_id: str, turns: list[dict], cut: int) -> list[str]:
    """Files the session's sub-agents edited before the cut, which the replay does not apply.

    Placed by the main agent's call that spawned the sub-agent. A spawning call
    the record does not hold cannot be placed, so it is not assumed to come
    after the cut. The agent's own files (`OUTSIDE`) are not the repository's.
    """
    at = {str(t["tool_call_id"]): t["turn_number"] for t in turns
          if t.get("turn_type") == "tool_use" and t.get("tool_call_id") and t.get("turn_number") is not None}
    out = []
    for e in subagent_edits(session_id):
        when = at.get(str(e["spawned_by"]))
        if (when is None or when <= cut) and not OUTSIDE.match(e["file_path"] or ""):
            out.append(e["file_path"] or "(no path)")
    return out


def build(located: list[dict], *, scratch: Path | None = None) -> BuildResult:
    """Turn located trajectories into tasks, checking each one's setup.

    ``located`` rows carry the trajectory fields (failed/complaint/resolved turns,
    defect, resolution) and the derived signature fields (kind, token, path).
    """
    result = BuildResult()
    seen: set[str] = set()
    # Sorted, because a task is named for its repository and the turn the
    # developer objected at, and that name is not unique: 106 of the 922 moments
    # in `runs/scale900` share a (repository, turn) pair with a *different*
    # session, against 8 of 400 at the smaller size, so this gets worse as the
    # corpus grows. The duplicate is rejected below, which is right -- but which
    # one is the duplicate was the order of `screened.jsonl`, and that order
    # changes whenever a row is re-screened and appended at the end. So a
    # directory built twice could name a different session under the same
    # task_id, giving it a new fingerprint, and `stage_build`'s prune would then
    # delete the calibration, controls, answers and graded attempts underneath
    # it. Sorting makes the winner a property of the rows rather than of the
    # file, so a rebuild is a rebuild rather than a reshuffle.
    located = sorted(located, key=lambda r: (str(r.get("repo_id") or ""),
                                             r.get("complaint", -1),
                                             str(r.get("session_id") or "")))
    # The session start comes from its turns, not from sessions.created_at,
    # which is a completion timestamp: across the eighteen sessions that produced
    # tasks it lands after the last turn in twelve and mid-session in six, never
    # before the first. Using it selected the last commit before the session
    # ENDED, so a candidate could be handed a tree containing commits the agent
    # made during the session -- in one case its own resolution, which it then
    # "passed" three times out of three.
    starts = session_starts({r["session_id"] for r in located})
    commits = load_commits_by_repo()
    checkpoints = session_checkpoints({r["session_id"] for r in located})
    repos = load_repos()
    # With the calls SWE-chat's table lost put back (G-76): edits among them
    # are replayed like any other, and a tree built without them lacked what
    # the conversation's own results say was written.
    turns_by_session = recovered(load_session_turns({r["session_id"] for r in located}))

    for row in located:
        repo_id = row.get("repo_id") or ""
        complaint = row.get("complaint", -1)

        def reject(why: str) -> None:
            result.rejected.append(Rejection(repo_id, complaint, why))

        try:
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
            asked = row.get("asks_for_something")
            why_not = row.get("request_reason", "")

            # Before the scope gate, because when no request was found the scope
            # gate cannot have run and its message -- "the defect is outside the
            # requested work" -- describes a judgement nobody made. Five of the
            # eight rows rejected for scope were really this, and reading them as
            # scope judgements sent an audit looking in the wrong place.
            if asked is False and "no user message" in str(why_not):
                reject(f"nothing for the candidate to answer: {why_not}")
                continue

            if not row.get("within_scope"):
                reject(
                    "the defect is outside the requested work: "
                    f"{row.get('scope_reason') or 'the scope gate did not run on this row'}"
                )
                continue
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
            sha = base_commit(repo_id, started_ns, commits, checkpoints.get(row["session_id"], set()))
            if sha is None:
                reject("no commit exists before the session started")
                continue

            sig = Signature(
                kind=row["kind"],
                path=row.get("path") or "",
                token=row.get("token") or "",
                # The field `stage_signature` writes is `is_symlink_defect`, from
                # `Signature.model_dump()`. Reading `symlink` found nothing, so
                # every derived True became a definite False -- B-122's shape -- and
                # `probe_for` never reached its symlink branch, falling through to
                # the token and path probes instead.
                is_symlink_defect=bool(row.get("is_symlink_defect")),
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
            # A tree changed by git before the cut is in no commit plus edits
            # (B-239). Documented as rejected since the replay was written; this is
            # the check that does it.
            git = tree_changing_git(turns, row["cut"])
            if git:
                reject(f"the agent changed its files with git before the cut, which no replay "
                       f"reproduces: {git[0][:90]}")
                continue
            # The same for files changed by a tool the replay does not read, or by
            # a sub-agent, whose calls only the raw transcript records. Either way
            # the replay would report success on a tree lacking the agent's work.
            unread = unreplayed_writes(turns, row["cut"])
            if unread:
                reject(f"the agent changed files before the cut with a tool the replay does not "
                       f"read: {', '.join(unread)[:90]}")
                continue
            by_subagent = subagent_edits_before(row["session_id"], turns, row["cut"])
            if by_subagent:
                reject(f"a sub-agent edited files before the cut, which the replay does not "
                       f"reproduce: {by_subagent[0][:90]}")
                continue
            # A transcript in another agent's format: the table may hold none of
            # the session's calls (a Copilot session's has no tool rows, while its
            # transcript records 14 edits before the moment), and nothing here can
            # read the transcript to tell.
            if foreign_transcript(row["session_id"]):
                reject("the session's transcript is not in Claude Code's format, so its calls "
                       "cannot be checked against the table")
                continue

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
                # The tree against what the conversation showed of it (A5): a file's
                # last read before the cut, line by line, and any HEAD it printed.
                # On the first grid 3 of 21 trees differed -- an older base, an
                # uncommitted file -- and an agent that checked then found the
                # opposite of what the conversation said.
                consistent = consistency_check(tree, turns, row["cut"], sha)
                if not consistent["consistent"]:
                    reject(why_inconsistent(consistent))
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
            task = (
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
                    edits_verified=rep.verified,
                    calls_recovered=bool(row.get("calls_recovered")) and has_transcript(row["session_id"]),
                )
            )
            seen.add(task_id)
            result.tasks.append(task)
        except Exception as e:  # noqa: BLE001 - one row's surprise is that row's rejection
            # An error nothing anticipated -- in the replay, the consistency check,
            # a transcript -- stopped the whole build with a traceback and left
            # tasks.jsonl as it was (B-251). Rejected as a tree that could not be
            # built this pass, which the prune treats as transient: whatever was
            # bought for the task is kept until a build gets past it.
            reject(f"could not build the tree: an unexpected {type(e).__name__}: {e}"[:200])
    return result
