"""Phase three: run the candidates, read what they wrote, count it.

`attempt` and `grade` are deliberately apart. A container is what strains a
laptop; grading is network waiting. Held together, every grading call occupied
a slot no candidate could use -- and a scoring change cost a full re-run.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from ..instrument.control import controlled
from ..store import (
    Paths, Progress, _gather, _gather_in_turn, append, completed, finished, held, in_turn, key_of,
    load, replace, sort_answers,
)


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
MAX_ATTEMPT_FAILURES = 3
KEPT_FILE_CHARS = 40_000
KEPT_STATE_CHARS = 2_000_000


def _capped(state: dict[str, str], first: str = "") -> dict[str, str]:
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


async def stage_attempt(
    paths: Paths, limit: int, concurrency: int, repeats: int = 3
) -> Progress:
    """Run candidates against every task the judge can read, and grade nothing.

    What this writes is the answer and the record of how it was produced: the
    reply, the trace with each call's output, and the structural reading, which
    has to be taken here because it reads files that are deleted the moment the
    attempt ends. The three readings of that answer belong to `grade`.
    """
    from ..corpus.turns import RECORD
    from ..score.attempt import INSTRUCTIONS as CANDIDATE_RULES
    from ..score.attempt import attempt_limits, environment_note, run, transcript_for
    from ..project import code_version
    from ..construct.container import IMAGES, available, host_allowed, image_for, max_containers, sweep
    from ..corpus.sessions import load_repos
    from ..score.judge import can_be_scored
    from ..corpus.turns import load_session_turns
    from ..spec import fingerprint, read
    from ..score.structure import analyse

    p = Progress("attempt")
    t0 = time.monotonic()
    sweep()
    sound = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)}
    # A task a control passed is satisfiable without doing the work, so running
    # candidates against it measures nothing -- and the same gate the grading
    # stage applies, not a looser one. Left on "not known-broken" while grading
    # moved to "known-good", this stage paid for containers on answers grading
    # then refused: one control run of two is enough to admit a task here and
    # not there.
    every = read(paths.tasks)
    tasks = [t for t in every if t.task_id in sound and t.task_id in controlled(paths)]
    # An errored attempt is not a finished one. Twelve of thirty-six attempts
    # died on API rate limits and were then counted as done, so a re-run would
    # have skipped exactly the work that needed redoing. Errored rows are
    # dropped here and their (task, run) pairs retried.
    #
    # A pair already graded counts as done as well. Every run directory made
    # before grading was split out holds its answers only inside attempts.jsonl,
    # and without this line the split would silently re-run eighty-one
    # candidates, at full price, for answers already on disk.
    # Only answers still about the task they name count as done. A stale one is
    # work again: grading will not read it, so counting it here would retire the
    # task permanently -- one of the three places the fingerprint has to agree.
    # Fingerprints for every task in the file, not only the ones admitted this
    # run. Built from the admitted list, an answer counted as belonging to no
    # task at all the moment its task failed a control or lost its calibration
    # -- and the rewrite below then deleted it. That is a candidate run, the
    # expensive thing here, destroyed because a judge changed its mind about
    # the task; and the note said one row had gone when two had.
    prints = {t.task_id: fingerprint(t) for t in every}
    # How often each pair has already died, read before `completed` drops the
    # errored rows that carry the count.
    failures = {
        (r.get("task_id"), r.get("run")): r.get("failures", 0)
        for r in load(paths.answers) if r.get("error")
    }
    fresh, orphaned, stale = sort_answers(completed(paths.answers), prints)
    if stale:
        # Superseded rows are removed, because left in place they would be
        # graded beside the answers replacing them and the report counts rows.
        # An answer carrying no stamp at all counts as superseded too -- no
        # answers file predates the field, and re-collecting is the safe
        # direction where mis-grading is not. Orphans are kept: an answer whose
        # task is not in the file is `build`'s to prune, when it rewrites the
        # task list and knows what survived.
        #
        # Named rows are dropped from a list read under the lock, rather than a
        # snapshot written back over it. The snapshot was taken before the lock
        # and anything appended in between -- by the grading stage, or by a
        # second attempt run -- would be gone with the rename.
        drop = {(r["task_id"], r["run"]) for r in stale}
        with held(paths.answers):
            replace(paths.answers, [
                r for r in load(paths.answers)
                if (r.get("task_id"), r.get("run")) not in drop
            ])
        p.notes.append(
            f"dropped {len(stale)} answers about an earlier version of their task, and re-running them"
        )
    done = {(r["task_id"], r["run"]) for r in fresh}
    # A graded row claims its pair too, so a stale one would keep the task
    # retired just as a stale answer did. Rows with no fingerprint are counted
    # as current: every run directory made before the split holds its answers
    # only there, and requiring one would re-run eighty-one candidates.
    graded_fresh, _, _ = sort_answers(
        finished(paths.attempts), prints, unstamped_is_stale=False
    )
    done |= {(r["task_id"], r["run"]) for r in graded_fresh}
    # One candidate per directory, refused rather than assumed. The resume key
    # is (task, run) with no model in it, so pointing a second candidate at a
    # directory that already holds the first one's answers found every pair
    # "done", ran nothing, and exited 0 -- a whole model's slice of the grid
    # silently missing, discovered only when its column came out empty. The
    # judge has had the equivalent refusal for a long time.
    from ..llm import model_name
    here = {r.get("model") for r in list(fresh) + list(graded_fresh) if r.get("model")}
    if here and here != {model_name()}:
        p.failed = 1
        p.notes.append(
            f"refused: this directory holds answers from {', '.join(sorted(here))}, and the "
            f"candidate is {model_name()}. Give each candidate its own run directory, with "
            f"the tasks, calibration and controls copied in."
        )
        p.took_s = time.monotonic() - t0
        return p
    repos = load_repos()
    images = {
        t.task_id: image_for(getattr(repos.get(t.repo_id), "language", None))
        for t in tasks
    }
    # A task with no container is not run on the developer's machine unless
    # they have said so (G-48): named, with what would let it run, and left out
    # of the work rather than failed -- nothing about it is wrong.
    unboxed = [t for t in tasks if images.get(t.task_id) is None]
    if unboxed and not host_allowed():
        def wants(t) -> str:
            language = getattr(repos.get(t.repo_id), "language", None)
            image = IMAGES.get(language or "")
            return (f"{t.task_id} (docker pull {image})" if image
                    else f"{t.task_id} (no image is listed for {language or 'its language'})")
        why = ("Docker is not reachable" if not available()
               else "no container image is on this machine for them")
        p.notes.append(
            f"{len(unboxed)} tasks were not run, because {why}: "
            + ", ".join(wants(t) for t in unboxed[:6]) + (" ..." if len(unboxed) > 6 else "")
            + ". Set ERRATA_ALLOW_HOST=1 to run them on this machine, unsandboxed."
        )
        tasks = [t for t in tasks if images.get(t.task_id) is not None]
    jobs = [(t, i) for t in tasks for i in range(repeats) if (t.task_id, i) not in done]
    # `--max-rows` is documented as capping how many rows each stage processes
    # and was read by two of eleven stages. It is how anyone would smoke-test a
    # four-hundred-task directory, and on the one stage that starts containers
    # it did nothing: `--max-rows 1` ran twelve hundred of them.
    jobs = p.cap(jobs, limit, len(tasks) * repeats)
    if not jobs:
        p.took_s = time.monotonic() - t0
        return p

    # Once for every task, rather than once per attempt: each load is a pass
    # over a 1.3 GB parquet, and three attempts at six tasks paid for it
    # eighteen times.
    turns_by_session = load_session_turns({t.session_id for t in tasks})
    # The conversation each candidate is shown, built here and stored on the
    # row rather than rebuilt at grading time. `run` renders exactly this text
    # for the candidate, and the trace check has to judge claims against the
    # same words: rebuilt later it can differ -- a re-read corpus, a changed
    # redaction, a session that no longer loads -- and a transcript that comes
    # back empty turns the trace check into an accusation machine, because
    # every claim citing the conversation then has nothing behind it.
    transcripts = {
        t.task_id: transcript_for(t, turns_by_session.get(t.session_id) or []) for t in tasks
    }

    # Containerised work is what strains a laptop, so it gets the tighter bound.
    # There is no second semaphore here any more: with grading moved out, the
    # only thing this stage waits on is the container it is holding.
    box = asyncio.Semaphore(max_containers())
    host = asyncio.Semaphore(max(1, concurrency // 2))
    budget_s, max_turns = attempt_limits()

    async def one(task, i):
        try:
            return await _one(task, i)
        except Exception as e:  # noqa: BLE001 - recorded, budgeted and retried
            # `_gather` turns a raise into a failure and writes nothing, so the
            # give-up budget -- built from rows that carry an error -- never saw
            # them: an expired API key produced "0 produced, 1 failed" on every
            # resume for ever, and a crash after the container had run paid for
            # a container each time and recorded nothing.
            before = failures.get((task.task_id, i), 0) + 1
            append(paths.answers, {
                "task_id": task.task_id, "run": i,
                "error": f"{type(e).__name__}: {e}", "failures": before,
            })
            return False

    async def _one(task, i):
        if not transcripts.get(task.task_id, "").strip():
            # The candidate is shown this conversation and nothing else; empty,
            # it would be asked to respond to a blank page, and the trace check
            # would then call every claim citing that conversation unsupported.
            # A session the corpus cannot produce is an error to retry, not an
            # attempt to score.
            append(paths.answers, {
                "task_id": task.task_id, "run": i,
                "error": "no conversation for this session: the corpus returned nothing",
            })
            return False
        image = images.get(task.task_id)
        started = time.monotonic()
        async with (box if image else host):
            attempt = await run(
                task, image=image, turns=turns_by_session.get(task.session_id),
                budget_s=budget_s, max_turns=max_turns,
            )
        if attempt.error:
            # How many times this pair has already died. An errored row is
            # dropped and retried, which is right for a rate limit and wrong
            # for a repository that will not clone: five resumes paid for
            # fifteen clone attempts on one dead repository, and the run's exit
            # code stayed at 1 for ever. After three, it is recorded as a
            # scored failure and stops costing anything.
            before = failures.get((task.task_id, i), 0) + 1
            if before >= MAX_ATTEMPT_FAILURES:
                append(paths.answers, {
                    "task_id": task.task_id, "run": i, "kind": task.kind,
                    "model": attempt.model, "environment": attempt.environment,
                    "seconds": round(time.monotonic() - started, 1),
                    "reply": "", "out_of_time": False, "tool_calls": [],
                    "actual_changes": {}, "declared_changes": [],
                    "structure": analyse(task, attempt, None).to_json(),
                    "final_state": {}, "final_state_files": 0,
                    "transcript": transcripts.get(task.task_id, ""), "record": RECORD,
                    "rules": f"{CANDIDATE_RULES}\n\n{environment_note(attempt.environment)}",
                    "task_fingerprint": fingerprint(task),
                    "code_version": code_version(), "budget_s": budget_s, "max_turns": max_turns,
                    "gave_up_after": before, "last_error": attempt.error, "usage": attempt.usage,
                })
                p.notes.append(
                    f"{task.task_id} #{i} failed {before} times and was given up on: {attempt.error[:80]}"
                )
                return False
            append(
                paths.answers,
                {"task_id": task.task_id, "run": i, "error": attempt.error,
                 "failures": before, "usage": attempt.usage, "null_responses": attempt.null_responses,
                 "throttled_s": attempt.throttled_s},
            )
            return False
        # Taken now, not at grading time. The token check reads the files the
        # working copy held, and `run` deletes that copy before it returns, so
        # this is the last moment the question can be asked at all.
        structure = analyse(task, attempt, attempt.final_state)
        append(
            paths.answers,
            {
                "task_id": task.task_id,
                "run": i,
                "kind": task.kind,
                "model": attempt.model,
                "environment": attempt.environment,
                # The candidate's own time, and only that. Before the split
                # this field carried the grading as well, so a two-minute
                # answer behind a slow judge was recorded as twenty minutes of
                # candidate work. `grade` records its own time separately.
                "seconds": round(time.monotonic() - started, 1),
                # Whole, not cut. The judge reads up to 12,000 characters and
                # the trace check 8,000; storing 4,000 meant three answers
                # could not be regraded on the text the original judge read.
                "reply": attempt.reply,
                "out_of_time": attempt.out_of_time,
                # How it ended and what it cost (D-36 A4, A6). The attempt
                # carried all five from D-36 on, and this row -- the only place
                # an attempt is kept -- never did, so no answer ever recorded
                # its tokens or whether its reply was the forced report (B-254).
                "ended_by": attempt.ended_by,
                "final_report_forced": attempt.final_report_forced,
                "final_report_error": attempt.final_report_error,
                "past_deadline": attempt.past_deadline,
                "usage": attempt.usage,
                "last_response": attempt.last_response,
                # Empty responses from the provider, sent again (B-255).
                "null_responses": attempt.null_responses,
                # Seconds the provider's throttling added to the deadline (B-262).
                "throttled_s": attempt.throttled_s,
                # The trace itself, not just its length. Without it a finished
                # run cannot be re-examined: every attempt in the first
                # corrected run recorded "9 calls" and nothing about what those
                # calls were, so no later check could ask whether the candidate
                # ran what it claimed to have run.
                "tool_calls": [c.to_json() for c in attempt.tool_calls],
                "actual_changes": attempt.actual_changes,
                "declared_changes": attempt.declared_changes,
                "structure": structure.to_json(),
                # The files the token check read, as they stood when the
                # candidate stopped. Never used for scoring -- the reading
                # above was taken from the tree itself -- but without them a
                # change to what counts as fixed can only be applied by running
                # every candidate again, and those are the expensive calls.
                "final_state": _capped(attempt.final_state, task.signature_path),
                "final_state_files": len(attempt.final_state or {}),
                # What the candidate was shown and what it was told, kept
                # verbatim. These are inputs to the grading, and an input that
                # is reconstructed later is an input that can drift.
                "transcript": transcripts.get(task.task_id, ""),
                # How that conversation was rendered (D-41.1): 2 shows each
                # call's input and marks every cut. Rows without it: record 1.
                "record": RECORD,
                "rules": f"{CANDIDATE_RULES}\n\n{environment_note(attempt.environment)}",
                # Which version of this task the answer was written about.
                "task_fingerprint": fingerprint(task),
                # And which version of the harness collected it, under which
                # limits (G-05, G-20).
                "code_version": code_version(), "budget_s": budget_s, "max_turns": max_turns,
            },
        )
        return True

    # Which model the candidate deployment served, before and after (D-36 A6).
    from ..llm import model_name as _served_name, record_served

    await record_served(paths.served, _served_name(), "attempt", "start")
    results = await _gather([one(t, i) for t, i in jobs], concurrency)
    await record_served(paths.served, _served_name(), "attempt", "end")
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    sweep()
    p.took_s = time.monotonic() - t0
    return p


async def stage_grade(paths: Paths, limit: int, concurrency: int,
                      passes: int = 1) -> Progress:
    """Read every stored answer three ways and write the scored row.

    Nothing is re-run: the answer, its trace and what the tree showed are taken
    from `answers.jsonl` exactly as the candidate left them. Only the two model
    readings happen here, so this stage is network waiting and can run far wider
    than the candidates did.

    What it writes is the row the rest of the project already reads -- the
    report, the regrade tool and the comparison table were not touched, because
    `attempts.jsonl` still means the same thing.
    """
    from ..score.attempt import INSTRUCTIONS as CANDIDATE_RULES
    from ..score.attempt import environment_note, transcripts_for
    from ..project import code_version
    from ..score.judge import can_be_scored, files_after, judge
    from ..llm import judge_model, model_name
    from ..spec import fingerprint, read
    from ..score.structure import Structure, combine
    from ..score.trace import check as check_trace

    p = Progress("grade")
    t0 = time.monotonic()
    grader = judge_model()
    tasks = {t.task_id: t for t in read(paths.tasks)}
    prints = {tid: fingerprint(t) for tid, t in tasks.items()}
    # Read, not tidied: this stage does not own answers.jsonl, and `completed`
    # would rewrite it from a stale snapshot while the attempt stage may be
    # appending to it.
    answers, orphaned, stale = sort_answers(finished(paths.answers), prints)
    # The same admission the candidate stage applies. Before the split the
    # judge call sat inside the loop over admitted tasks, so an unsound or
    # broken task could not be graded; afterwards this stage read only
    # answers.jsonl and tasks.jsonl and graded whatever it found. A task whose
    # gate failed after its answers were collected -- a control that now passes
    # on a do-nothing answer, a calibration lost to a transient error -- then
    # contributed to the pass rate, while the candidate stage correctly refused
    # to run it. Reproduced at two tasks: half the published rate came from a
    # task the pipeline had already decided could measure nothing.
    sound = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)}
    passes_controls = controlled(paths)
    admitted = [a for a in answers if a["task_id"] in sound and a["task_id"] in passes_controls]
    if len(admitted) != len(answers):
        p.notes.append(
            f"{len(answers) - len(admitted)} answers belong to tasks that no longer "
            f"pass their known-answer pair or their controls: they are not graded "
            f"here, and any grading they already have is not counted"
        )
    answers = admitted
    # Before anything is rewritten. The refusal below exists to keep this run
    # out of a file another judge owns, and it used to fire after `completed`
    # had already dropped that judge's error rows -- destroying the very rows
    # it had to retry, in a run that then reported doing nothing.
    others = {r.get("judge_model") for r in load(paths.attempts)} - {grader, None}
    if others:
        p.notes.append(
            f"REFUSED: {len(load(paths.attempts))} answers here were graded by "
            f"{', '.join(sorted(others))}, and this run's judge is {grader}. "
            f"Mixing two judges in one file makes its counts meaningless. "
            f"Use `run.py rejudge --run <dir> --judge {grader}`, which keeps the "
            f"second opinion separate and puts that judge through the known "
            f"answers first."
        )
        # At least one, even when there was nothing left to grade: the usual
        # reason to point a second judge at a directory is that every answer is
        # already graded, and a refusal that exits zero is a refusal the shell
        # loop driving these runs cannot see.
        p.failed = 1
        p.took_s = time.monotonic() - t0
        return p
    # Errored grades are dropped by `completed` and come back as work, exactly
    # as errored attempts do. This stage owns attempts.jsonl, so it tidies it.
    graded = completed(paths.attempts)
    graded, orphan_graded, outdated = sort_answers(graded, prints, unstamped_is_stale=False)
    if outdated:
        # Same rule as the answers: superseded rows go, orphans stay for `build`
        # to prune. Keeping them is also what makes the count in this note true
        # -- it reported only the superseded rows while removing both kinds.
        drop = {(r["task_id"], r["run"]) for r in outdated}
        with held(paths.attempts):
            replace(paths.attempts, [
                r for r in load(paths.attempts)
                if (r.get("task_id"), r.get("run")) not in drop
            ])
        p.notes.append(
            f"dropped {len(outdated)} scores of an earlier version of their task"
        )
    # One reading per (answer, pass). Read once, a verdict was one draw of a
    # reader that does not always answer the same way: on the first candidates
    # run against the three new tasks the single pass awarded came back
    # off_target three times of three when re-read. `settled()` combines the
    # readings, and every rate reads the settled verdict.
    done = {(r["task_id"], r["run"], r.get("pass", 0)) for r in graded}
    todo = [(a, n) for a in answers for n in range(max(1, passes))
            if (a["task_id"], a["run"], n) not in done]
    todo = p.cap(todo, limit, len(answers) * max(1, passes))
    p.notes.append(f"graded by {grader}")
    # Naming no grader leaves `judge_model()` falling back to the candidate's
    # own model, which is the thing B-118 was fixed to stop. It is a legitimate
    # configuration -- the first eighteen scored answers were graded that way --
    # but it is never what someone wants by accident, and this stage is now run
    # by itself, where the setting is easiest to forget.
    #
    # Refused rather than warned, since 09-22: the grid about to run puts three
    # candidates through one judge, and a warning in the middle of a long log is
    # the kind of thing read after the paid calls are spent. Compared against
    # the model recorded on the answers, not against ERRATA_MODEL, because the
    # grading stage is run on its own and the environment then describes
    # whatever was set last, not who wrote these answers.
    import os as _os
    writers = {a.get("model") for a in answers if a.get("model")}
    if grader in (writers or {model_name()}) and not _os.environ.get("ERRATA_ALLOW_SELF_GRADING"):
        p.failed = 1
        p.notes.append(
            f"refused: {grader} would grade answers written by {grader}. Set "
            f"ERRATA_JUDGE_MODEL to a different model, or ERRATA_ALLOW_SELF_GRADING=1 "
            f"if self-grading is what you mean to measure."
        )
        p.took_s = time.monotonic() - t0
        return p
    if orphaned:
        p.notes.append(f"{len(orphaned)} answers belong to tasks that no longer exist")
    if stale:
        p.notes.append(f"{len(stale)} answers were written about an earlier version of their task")
    # A judge is trusted on a task because it read that task's known pair
    # correctly. If the model doing the grading is not the one that was
    # calibrated, the gate those rows represent says nothing about it.
    calibrators = {r.get("judge_model") for r in load(paths.calibration) if r.get("judge_model")}
    if calibrators and grader not in calibrators:
        p.notes.append(
            f"warning: calibrated with {', '.join(sorted(calibrators))}, grading with {grader}"
        )
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    # Only for answers stored before the conversation was kept on the row.
    missing = [a for a, _ in todo if a.get("transcript") is None]
    context = transcripts_for([tasks[a["task_id"]] for a in missing]) if missing else {}

    async def one(a, n):
        task = tasks[a["task_id"]]
        started = time.monotonic()
        try:
            structure = Structure.from_json(a["structure"])
            # `Score.to_json` writes task_id from the reading, and it is merged
            # over the row -- so if the two ever disagreed, the row would be
            # filed under one identity and resumed under another, and every
            # answer would be regraded on every pass with "0 already done".
            if structure.task_id != a["task_id"]:
                raise ValueError(
                    f"the reading is for {structure.task_id}, the answer for {a['task_id']}"
                )
        except (KeyError, TypeError, ValueError) as e:
            # Unreadable, not empty. Defaulting the missing fields would score
            # the attempt as having done nothing, which fails it.
            append(paths.attempts, {
                "task_id": a["task_id"], "run": a["run"], "pass": n, "judge_model": grader,
                "error": f"the stored reading could not be read: {e}",
            })
            return False
        row = {
            "task_id": a["task_id"],
            "run": a["run"],
            "pass": n,
            # The kind the judge actually applied, read from the task now, not
            # the label the answer was stored with. They are the same thing
            # while the fingerprint matches, and the row should carry the one
            # that decided the rule: `solved` for an introduced defect means
            # "did the work" and for a present one means "addressed it", so a
            # row labelled with the other kind reads as an impossible pass.
            "kind": task.kind,
            "task_fingerprint": prints[a["task_id"]],
            # The harness that read it (G-05); the one that collected the
            # answer is on the answer row and may be another.
            "code_version": code_version(),
            "environment": a.get("environment"),
            "calls": structure.tool_calls,
            "tool_calls": a.get("tool_calls") or [],
            "model": a.get("model"),
            # Which model graded this, since it need not be the one that
            # answered. Earlier rows omit it; they were graded by the candidate
            # model itself.
            "judge_model": grader,
            "seconds": a.get("seconds"),
            "reply": a.get("reply") or "",
            "out_of_time": a.get("out_of_time", False),
            # Carried through so the regrade tool reads the reading that was
            # taken from the tree rather than reconstructing a thinner one.
            "structure": a["structure"],
            # Whether the trace check had the conversation to check claims
            # against. Without it, an answer citing what it was shown looks
            # like an answer inventing it, and the row should say which case
            # this was.
            "had_conversation": bool(
                (a.get("transcript") or context.get(task.task_id, "")).strip()
            ),
        }
        if a.get("gave_up_after"):
            # The harness never got the candidate to the question -- a
            # repository that will not clone, a container that will not start.
            # Scored as a no-answer it was indistinguishable from a candidate
            # that used every turn and said nothing, and the note asserted
            # "answered with nothing", which is false: it never ran. It enters
            # no rate, and carries why.
            append(paths.attempts, {
                **row,
                "gave_up_after": a["gave_up_after"], "last_error": a.get("last_error", ""),
                "outcome": "gave_up", "passed": False, "scoreable": False,
                "solved": False, "dishonest": False, "trustworthy": False,
                "checked": structure.checked, "wrote": structure.wrote,
                "fixed": structure.fixed,
                "told_the_truth_about_edits": structure.declaration_matches,
                "claims_match_trace": None, "unsupported_claims": [],
                "overclaimed_work": False,
                "note": f"the harness could not run this attempt after "
                        f"{a['gave_up_after']} tries: {str(a.get('last_error', ''))[:80]}",
            })
            return True
        if not row["reply"].strip():
            # No answer to read, so nothing to judge. Recorded as a failed
            # attempt rather than an error, because "used every turn and never
            # reported" is a result this benchmark is interested in, and an
            # error row would be retried forever and counted nowhere.
            append(paths.attempts, {
                **row,
                "outcome": "no_answer", "passed": False, "scoreable": True,
                "solved": False, "dishonest": False, "trustworthy": True,
                "checked": structure.checked, "wrote": structure.wrote,
                "fixed": structure.fixed,
                "told_the_truth_about_edits": structure.declaration_matches,
                "claims_match_trace": None, "unsupported_claims": [],
                "overclaimed_work": False,
                "note": "used every turn without answering" if row["out_of_time"]
                        else "answered with nothing",
            })
            return True
        if not row["had_conversation"]:
            # Recorded and then graded anyway was the wrong shape: the row said
            # the check had nothing to check claims against, and nothing read
            # that field, so a known-bad reading was counted like any other.
            #
            # Terminal, not an error. Nothing about this row can change -- the
            # session the corpus cannot produce today it will not produce
            # tomorrow -- so an error row was dropped and retried on every
            # pass, paying for a 1.3 GB corpus read each time and keeping the
            # run's exit code at 1 for ever. Recorded once, counted nowhere.
            append(paths.attempts, {
                **row,
                "outcome": "no_context", "passed": False, "scoreable": False,
                "solved": False, "dishonest": False, "trustworthy": False,
                "checked": structure.checked, "wrote": structure.wrote,
                "fixed": structure.fixed,
                "told_the_truth_about_edits": structure.declaration_matches,
                "claims_match_trace": None, "unsupported_claims": [],
                "overclaimed_work": False,
                "note": "the conversation this answer was written about could not be rebuilt, "
                        "so its claims cannot be checked against it",
            })
            return True
        try:
            # The judge is shown what the candidate did, because "did it claim
            # something it had not established" cannot be read off the prose --
            # and what it left behind, because "is the defect still there"
            # cannot be read off the prose either. Only 3 of the 15 tasks ever
            # built carry a literal defect string, so the reading that looks at
            # the files could answer at all on 18 of 172 stored gradings; on
            # the other 154 nothing had looked at them (D-32).
            verdict = await judge(
                task, row["reply"], tool_calls=row["tool_calls"], model=grader,
                # `None`, not `{}`, for a row whose files were never captured:
                # an empty mapping tells the judge the candidate changed
                # nothing, which is a claim, while `None` leaves the prompt as
                # it was. Every answer row on disk has `final_state` today, so
                # this guards the shape rather than a case -- the same guard
                # `regrade_all` carries, where 133 of the rows it reads do
                # predate the capture.
                changed=(files_after(a, task.signature_path)
                         if a.get("final_state") is not None else None),
                # The conversation it was given, as the trace check below gets
                # it (D-36 A2).
                context=a.get("transcript") or context.get(task.task_id, ""),
            )
            # A third reading, independent of both: does the answer's account of
            # its own work match the recorded trace and the conversation it was
            # given. This is what the token check cannot do for a behavioural
            # defect.
            trace_check = await check_trace(
                row["reply"],
                row["tool_calls"],
                model=grader,
                # The words the candidate actually read, taken from the row.
                # Rebuilt only for answers stored before they were kept.
                context=a.get("transcript") or context.get(task.task_id, ""),
                # What the candidate was told it had. Otherwise "the network is
                # unavailable here" reads as an unsupported claim, when it is
                # the harness's own sentence. Also taken from the row: these
                # rules live in the candidate's own module and change, and a
                # check told the wrong rules judges an answer against
                # instructions it was never given.
                given=a.get("rules")
                or f"{CANDIDATE_RULES}\n\n{environment_note(a.get('environment') or 'host')}",
            )
        except Exception as e:
            # One failed grading used to abort the whole stage: the judge call
            # sat outside any handler, while the candidate run beside it caught
            # everything. An answer that cannot be graded is one row to retry,
            # not a reason to stop reading the other eighty.
            append(paths.attempts, {
                "task_id": a["task_id"], "run": a["run"], "pass": n, "judge_model": grader,
                "error": f"{type(e).__name__}: {e}",
            })
            return False
        score = combine(verdict, structure, trace_check)
        append(paths.attempts, {
            **row,
            "graded_seconds": round(time.monotonic() - started, 1),
            "judgement": verdict.to_json(),
            **score.to_json(),
        })
        return True

    # Which model the judge deployment served, before and after (D-36 A6).
    from ..llm import record_served

    await record_served(paths.served, grader, "grade", "start")
    # An answer's readings one after another, so the later ones find its prompt
    # cached (B-256); the ceiling is on answers read at once.
    results = await _gather_in_turn(
        [[lambda a=a, n=n: one(a, n) for a, n in g] for g in in_turn(todo, lambda j: (j[0]["task_id"], j[0]["run"]))],
        concurrency)
    await record_served(paths.served, grader, "grade", "end")
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


def stage_report(paths: Paths) -> Progress:
    """Count what happened, and how much of it is trustworthy."""
    from collections import Counter

    from ..score.judge import can_be_scored

    p = Progress("report")
    t0 = time.monotonic()
    # The same admission every other counter applies. This was the one place
    # that read attempts.jsonl directly, so a task whose control later failed
    # kept contributing to the pass rate here while `run.py judges` dropped it
    # -- two different pass rates printed for one directory.
    admitted = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)} & controlled(paths)
    # Through `settled()`: one verdict per attempt, the conservative one when
    # it was read more than once. Errored readings are dropped there. Without
    # this the primary report and the rejudge report read the same rows by
    # different rules and disagreed on the one pass in twenty-seven.
    from ..score.rejudge import settled

    attempts = [a for a in settled(load(paths.attempts)) if a.get("task_id") in admitted]
    # Missing means excluded, as it does in every rate: a row with no verdict
    # about whether its reading could be supported is not a result.
    scoreable = [a for a in attempts if a.get("scoreable")]
    # Named, not merely dropped. A denominator that quietly shrank by one is
    # indistinguishable from a task that was never attempted, and an attempt
    # withdrawn because the harness broke is exactly the one a reader has to be
    # told about: G-43 asked for that row to be excluded *and said so*.
    unreadable = sorted(
        (f"{a.get('task_id')} #{a.get('run')} ({a.get('unreadable')})"
         for a in attempts if a.get("unreadable")),
    )
    # Unfiltered: this is how many answers were collected, not a rate, and a
    # gate file that has gone missing should not make a finished run read as an
    # empty one.
    answers = [a for a in load(paths.answers) if not a.get("error")]
    answer_keys = {(a.get("task_id"), a.get("run")) for a in answers}
    # From every reading on disk, not from the admitted ones. Counted after the
    # gate, an answer whose task had since left the benchmark was reported as
    # "not yet graded" for ever -- it had been graded, no run of `grade` could
    # clear it, and the number is there to say whether grading finished. And
    # per reading: `settled()` folds two rows for one attempt into one verdict,
    # so a row written twice by two processes has to be looked for before that.
    raw_readings = [a for a in load(paths.attempts) if not a.get("error")]
    graded_keys = {(a.get("task_id"), a.get("run")) for a in raw_readings}
    reading_keys = [(a.get("task_id"), a.get("run"), a.get("pass", 0)) for a in raw_readings]
    moments = len(load(paths.moments))
    readings = load(paths.readings)
    viable = [r for r in readings if (r.get("reading") or {}).get("benchmark_viable")]
    tasks = load(paths.tasks)
    # `can_be_scored`, not the raw field: on any calibration row written before
    # 09-19 `sound` means the older, stricter bar, so the funnel reported two
    # tasks calibrated beside twenty-seven attempts over nine of them.
    sound = [c for c in load(paths.calibration) if can_be_scored(c)]

    per_task = Counter(a.get("task_id") for a in scoreable)
    with_tools = [a for a in scoreable
                  if a.get("calls") or a.get("tool_calls") or (a.get("structure") or {}).get("tool_calls")]
    if len(set(per_task.values())) > 1:
        # What was measured, not a cause. This named a lowered `--repeats`,
        # which cannot produce it -- lowering it leaves the old answers in
        # place, so every task keeps the count it had. On all three stored
        # candidate runs the cause is attempts the judge could not be trusted
        # on (`scoreable: false`, a quote it could not find in the reply),
        # which are collected and then not counted (B-223).
        p.notes.append(
            "tasks do not all have the same number of scored attempts "
            f"({dict(sorted(Counter(per_task.values()).items()))} tasks by attempts) -- "
            "an attempt whose judge could not be trusted is collected and not scored; "
            "see answers_not_yet_graded and tasks_with_no_scored_attempt"
        )

    report = {
        "funnel": {
            "moments": moments,
            "read": len(readings),
            "viable": len(viable),
            "trajectories_usable": sum(
                1 for t in load(paths.trajectories) if t.get("usable")
            ),
            "tasks_built": len(tasks),
            "tasks_calibrated": len({c["task_id"] for c in sound}),
            # Answers collected, against answers read. A grading stage that
            # stopped partway would otherwise look like a smaller run rather
            # than an unfinished one -- the report counts graded rows, and
            # every ungraded answer is simply invisible to it.
            "answers_collected": len(answers),
            # Unique keys on both sides. Subtracting a set size from a row
            # count reported ungraded answers that did not exist the moment a
            # row appeared twice -- which is the case the next line exists to
            # report.
            "answers_not_yet_graded": len(answer_keys - graded_keys),
            "answers_recorded_twice": len(answers) - len(answer_keys),
            # Two processes grading one directory would each append a row for
            # the same attempt, and every count below would include it twice.
            # Stated rather than silently deduplicated: a number that repairs
            # itself hides the fact that something ran twice.
            "attempts_recorded_twice": len(reading_keys) - len(set(reading_keys)),
            # A task with no scored attempt reads exactly like a task that was
            # never built, and every silent hole found so far ended there:
            # answers deleted, a stale row blocking its own re-run, a duplicate
            # name. Naming them costs a line and makes the next one visible.
            # Scoreable rows only: a task whose every attempt was unreadable
            # has no scored attempt, and was being reported as though it had.
            "tasks_with_no_scored_attempt": sorted(
                {t.get("task_id") for t in tasks} - {a.get("task_id") for a in scoreable}
            )[:25],
            "tasks_with_no_scored_attempt_total": len(
                {t.get("task_id") for t in tasks} - {a.get("task_id") for a in scoreable}
            ),
        },
        "attempts": len(attempts),
        "scoreable": len(scoreable),
        # In the file, not only on the console: report.json is what the numbers
        # are read out of, and a reader who never watched the run has no other
        # place to learn that an attempt left the denominator.
        "excluded_as_unreadable": unreadable,
        "passed": sum(1 for a in scoreable if a.get("passed")),
        "made_unverified_claim": sum(1 for a in scoreable if a.get("dishonest")),
        "checked_first": sum(1 for a in scoreable if a.get("checked")),
        # No rate without the number of tasks behind it (G-39): eighteen of
        # twenty-four cells in the first three-model table were 0/3 or 3/3, so
        # "21 of 27" is nine tasks, not twenty-seven observations.
        "tasks": len(per_task),
        # How many SCORED attempts each task has, which is not the same as how
        # many were collected: an attempt whose judge could not be trusted is
        # in `answers_collected` and not here. A lowered `--repeats` (G-35) can
        # also raise it, since the extra answers stay and stay counted.
        "attempts_per_task": {str(n): k for n, k in sorted(Counter(per_task.values()).items())},
        # A model that never picks up a tool cannot pass most of these tasks,
        # so the raw rate mixes "can it do the work" with "does it try"
        # (G-38). Both, side by side.
        "used_a_tool": {
            "attempts": len(with_tools),
            "passed": sum(1 for a in with_tools if a.get("passed")),
        },
        # How much of this rests on more than one reading (D-30).
        "read_more_than_once": {
            "attempts": sum(1 for a in scoreable if (a.get("readings") or 1) > 1),
            "unanimous": sum(1 for a in scoreable
                             if (a.get("readings") or 1) > 1 and a.get("unanimous")),
        },
        "outcomes": dict(Counter(a.get("outcome") for a in scoreable)),
        "by_kind": {
            k: {
                "attempts": sum(1 for a in scoreable if (a.get("kind") or "unknown") == k),
                "passed": sum(
                    1 for a in scoreable
                    if (a.get("kind") or "unknown") == k and a.get("passed")
                ),
            }
            # Taken from the data, not listed. The list said present and
            # introduced, while fifteen of eighteen scored attempts are
            # behavioural ("none"), so the report showed three attempts by kind
            # and silently left out the rest.
            for k in sorted({a.get("kind") or "unknown" for a in scoreable})
        },
    }
    if unreadable:
        p.notes.append(
            "excluded as unreadable, not counted as failures: " + "; ".join(unreadable)
        )
    if not admitted and load(paths.attempts):
        p.notes.append(
            "no task passes its known pair and every control, so nothing here is "
            "counted -- check calibration.jsonl and controls.jsonl exist and are complete"
        )
    # Through an atomic writer like every other file: a kill during this left a
    # half-written report.json, which `run.py status` then died on. Named for
    # this process, like `replace`, so two writers cannot race on one name.
    tmp = paths.report.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(report, indent=2))
    tmp.replace(paths.report)
    p.produced = len(scoreable)
    p.took_s = time.monotonic() - t0
    # Extend, not assign -- the note appended above says why a directory full
    # of graded work counts nothing, and assigning over it threw away the one
    # line that distinguishes a gated-out run from an empty one. A directory
    # holding 27 graded attempts printed an all-zero funnel and no explanation,
    # which is the B-135 shape `stage_build` carries a comment about.
    p.notes.extend([json.dumps(report["funnel"]), json.dumps(report["outcomes"])])
    return p


