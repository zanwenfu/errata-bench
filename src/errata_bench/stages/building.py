"""Phase two: rebuild the environment, then test the instrument on it.

`build` reconstructs the tree the agent was working in. `calibrate` and
`control` do not build anything -- they ask whether the benchmark itself
behaves on answers whose correct score is already known.
"""

from __future__ import annotations

import time

from ..store import Paths, Progress, _gather, append, completed, held, key_of, load, replace


def stage_build(paths: Paths, limit: int) -> Progress:
    """Reconstruct each environment and verify the defect is really in it.

    This stage rewrites tasks.jsonl from scratch rather than appending, because
    a task's environment depends on code that changes -- the base commit, the
    presence probe, the oracle checks. Resuming would preserve tasks built under
    rules that no longer hold.

    Rewriting has a consequence the later stages have to respect: a task that
    disappears here leaves its calibration and control verdicts behind, and
    those stages resume on task_id alone. So their outputs are pruned to what
    still exists. Without that, a rebuild that drops seven of thirteen tasks --
    which is exactly what fixing the session-start bug did -- leaves seven stale
    "sound" verdicts pointing at tasks that are gone.
    """
    from ..construct.build import build
    from ..spec import fingerprint, write

    p = Progress("build")
    t0 = time.monotonic()
    rows = [r for r in load(paths.screened) if not r.get("error")]
    # Refuse to rebuild nothing over something. The candidate run directories
    # hold their tasks, calibration, controls and answers but not the screened
    # rows those were derived from -- they were copied in. Running every stage
    # against one of them, which `run.py stages --run runs/cand-grok` does by
    # default, would build zero tasks from zero input, write that empty list
    # over tasks.jsonl, and then prune every downstream file to match: three
    # directories of twenty-seven graded attempts each, deleted in a second by
    # a command that looks like a resume. Rebuilding from a genuinely empty
    # directory is still fine; it has nothing to lose.
    if not rows and (load(paths.tasks) or load(paths.attempts) or load(paths.answers)):
        p.failed = 1
        p.notes += [
            "refused: no screened rows to build from, but this directory already holds "
            "tasks and results. Re-run the earlier stages first, or use --only to name "
            "the stage you meant."
        ]
        p.took_s = time.monotonic() - t0
        return p
    # `limit` is deliberately ignored here, and saying so is the whole of it.
    # This stage rewrites tasks.jsonl in full and prunes four downstream files
    # to match, so a capped run that honoured the cap would delete the rows of
    # every task it skipped. `build()` takes no cap and is handed every row,
    # so that has never been possible.
    #
    # It was briefly refused instead, which was worse: nothing was ever
    # truncated, so the refusal only blocked runs that would have been correct
    # and complete -- and it killed the incremental workflow outright, where
    # `--max-rows 3` run repeatedly advances three fresh rows a pass. Pass one
    # built three tasks; pass two had six screened rows and was refused, and so
    # was every pass after it, for ever. The only escape was dropping the cap,
    # which uncaps `attempt` -- a container per task per repeat, the stage the
    # cap exists for.
    if limit and limit < len(rows):
        p.notes.append(
            f"--max-rows {limit} does not apply to build: it rebuilds all {len(rows)} "
            "screened rows, because it rewrites tasks.jsonl in full"
        )

    result = build(rows)

    # The guard above refuses to build nothing from nothing. This refuses to
    # build nothing from *something*, which is the likelier accident: the rows
    # are all there and every one of them failed for a reason that has nothing
    # to do with the tasks. An unreachable remote, an absent or broken git, an
    # expired token and a GitHub outage each reject every row alive, and the
    # prune below then rewrites four files to match. Measured on a copy with an
    # unresolvable host: 18 graded attempts, 11 calibrations and 12 controls
    # destroyed by a command that exits 0 and prints "0 produced, 51 already
    # done", because `skipped` renders as "already done" and a total wipe reads
    # like a no-op resume.
    if not result.tasks and (load(paths.tasks) or load(paths.attempts) or load(paths.answers)):
        transient = sum(1 for r in result.rejected
                        if "could not build the tree" in (r.reason or ""))
        p.failed = 1
        p.notes += [
            f"refused: built 0 tasks from {len(rows)} screened rows, and this directory "
            f"already holds results. {transient} of {len(result.rejected)} rejections were "
            "transient (the tree could not be fetched), which is what an unreachable "
            "remote or a broken git looks like. Nothing was pruned. Check the remote, "
            "then re-run."
        ]
        p.rejected = len(result.rejected)
        p.took_s = time.monotonic() - t0
        return p

    write(result.tasks, paths.tasks)

    surviving = {t.task_id for t in result.tasks}
    prints = {t.task_id: fingerprint(t) for t in result.tasks}

    def still_describes(row: dict) -> bool:
        if row.get("task_id") not in surviving:
            return False
        # A task that kept its name and changed its content leaves rows about
        # the older version behind. They are dropped here for the same reason
        # rows of a vanished task are: the stage that reads them resumes on
        # task_id, and a stale row would sit there claiming work that no longer
        # applies. Rows written before fingerprints existed carry none, and are
        # kept rather than destroyed on a rule they predate.
        stamp = row.get("task_fingerprint")
        return stamp is None or stamp == prints[row["task_id"]]

    for downstream in (paths.calibration, paths.controls, paths.answers, paths.attempts):
        if not downstream.exists():
            continue
        # Under the lock, re-read: this loop rewrites four files a candidate or
        # grading run may be appending to, and a snapshot written back over one
        # of them silently destroyed whatever arrived in between -- measured at
        # 21 of 40 answer rows lost to a peer appending during the window.
        with held(downstream):
            rows = load(downstream)
            kept = [r for r in rows if still_describes(r)]
            if len(kept) != len(rows):
                replace(downstream, kept)
                p.notes.append(f"dropped {len(rows) - len(kept)} stale rows from {downstream.name}")
    # Kept on disk, not only printed. Forty of fifty-one located defects are
    # rejected here, and which gate each one died at is the question anyone
    # asking "where do more tasks come from?" needs answered -- but the reasons
    # lived in this stage's stdout and were gone with the terminal scrollback,
    # so answering it meant replaying every gate by hand against the screened
    # rows. The file is rewritten with the tasks, since both describe the same
    # build.
    with held(paths.rejections):
        replace(paths.rejections, [
            {"repo_id": r.repo_id, "complaint": r.complaint_turn, "reason": r.reason}
            for r in result.rejected
        ])
    p.produced = len(result.tasks)
    # Skipped, not failed. Four of five located defects are rejected here by
    # design -- that is the funnel, and its reasons are printed below -- but
    # counted as failures they made `run.py` exit 1 on every pass of a finished
    # run, for ever, saying "1 rows failed in: build" when nothing had failed.
    p.rejected = len(result.rejected)
    # extend, not assign: the loop above records what it deleted, and assigning
    # here threw that away two lines later -- so the one message saying a
    # rebuild removed graded rows and paid-for answers never survived to be
    # printed, even once notes are printed.
    p.notes.extend(f"{r.repo_id} t={r.complaint_turn}: {r.reason[:70]}" for r in result.rejected)
    p.took_s = time.monotonic() - t0
    return p


async def stage_calibrate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Check the judge can read each task's known-wrong and known-right answers."""
    from ..score.judge import calibrate
    from ..llm import judge_model
    from ..spec import fingerprint, read

    p = Progress("calibrate")
    t0 = time.monotonic()
    tasks = read(paths.tasks)
    grader = judge_model()
    # Keyed on the judge as well as the task. Calibration rows already store
    # `judge_model` because "the verdict belongs to that judge and says nothing
    # about another one", but the resume key did not read it, so pointing
    # ERRATA_JUDGE_MODEL at a different judge reported "9 already done", took
    # no readings, and graded against the previous judge's admission gate. A
    # row that predates the field is treated as this judge's, which is what it
    # was.
    done = {
        r["task_id"] for r in completed(paths.calibration)
        if r.get("judge_model", grader) == grader
    }
    todo = p.cap([t for t in tasks if t.task_id not in done], limit, len(tasks))

    async def one(t):
        try:
            c = await calibrate(t, model=grader)
            append(
                paths.calibration,
                {
                    "task_id": t.task_id,
                    # Which model read the pair. A task is admitted because a
                    # judge read its known answers correctly, so the verdict
                    # belongs to that judge and says nothing about another one
                    # -- and the grading stage can now be pointed at a
                    # different model than the one calibrated here.
                    "judge_model": grader,
                    # Which version of the task this verdict is about. A task
                    # rebuilt under the same name keeps its reference answers'
                    # gate otherwise: the answers were correctly retired and
                    # the two verdicts admitting the task to the benchmark --
                    # "the judge can read its known pair" and "a do-nothing
                    # answer fails it" -- survived, so candidates then ran
                    # under a gate never applied to the question they were
                    # asked. The rebuild already prunes any downstream row
                    # whose stamp disagrees; these rows simply had none.
                    "task_fingerprint": fingerprint(t),
                    # `sound` gates: the pass/fail line in both orders. `strict`
                    # is the older bar -- all four readings identical -- kept
                    # beside it because it says something about the judge even
                    # when it says nothing about the task.
                    "sound": c.sound,
                    "strict": c.strict,
                    "separates": c.separates,
                    "separates_both_ways": c.separates_both_ways,
                    "order_invariant": c.order_invariant,
                    "failed_solved": c.failed_solved,
                    "resolution_solved": c.resolution_solved,
                    "failed_solved_swapped": c.failed_solved_swapped,
                    "resolution_solved_swapped": c.resolution_solved_swapped,
                    # The four readings themselves, not only the verdict on
                    # them. With just `detail`, a task marked order-dependent
                    # could not be told apart as a pass/fail flip or a wobble
                    # in a side observation -- and two of the three were the
                    # latter.
                    "failed_outcome": c.failed_outcome,
                    "resolution_outcome": c.resolution_outcome,
                    "failed_outcome_swapped": c.failed_outcome_swapped,
                    "resolution_outcome_swapped": c.resolution_outcome_swapped,
                    "detail": c.detail,
                },
            )
            return c.sound
        except Exception as e:
            # Marked as an error so the next run retries it. Without the key,
            # completed() cannot tell a call that failed from a judge that
            # misread the pair, and one dropped connection retires a sound
            # task for good -- the same trap the other stages were fixed for.
            append(
                paths.calibration,
                {"task_id": t.task_id, "judge_model": grader, "sound": False,
                 "error": f"{type(e).__name__}: {e}",
                 "detail": f"{type(e).__name__}: {e}"},
            )
            return False

    if todo:
        results = await _gather([one(t) for t in todo], concurrency)
        p.produced = sum(1 for r in results if r)
        p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_control(paths: Paths, limit: int, concurrency: int,
                        passes: int = 1) -> Progress:
    """Score answers whose correct result is known, before running candidates.

    A benchmark that cannot fail a candidate which does nothing is measuring
    noise, and this one could not: every introduced-defect task passed on the
    absence of the defect alone, so declining to work was a pass. Three
    attempts at one task read "Onboarding is blocked in this environment ... I
    made no changes" and all three were scored as successes.

    Running here, before `attempt`, means a broken task is caught for the price
    of two judge calls rather than after a container has run against it.

    Asked `passes` times, for the reason D-28 gives for every other gate that
    reads prose: it does not answer the same way twice. Measured 09-20, by
    running the must-pass control over the same nine tasks in two directories
    with the same judge and byte-identical task fingerprints --
    `basher83-tailnet-microservices-83` and `shunkakinoki-dotfiles-26` came
    back `solved` one time and `solved_with_unverified_claim` the other. Two
    of eight flipped. Under D-26 the second reading is a failure, so those two
    tasks left the benchmark on a coin toss, and the message they left under
    said the task rejects its own reference -- which on the other reading it
    does not.

    A control counts as behaving only if it behaved every time, which keeps a
    doubtful task out in both directions: a must-fail control that passed once
    is alarming, and a must-pass control that failed once is unproven.
    """
    from ..instrument.control import CONTROLS, check
    from ..score.judge import can_be_scored
    from ..llm import judge_model
    from ..spec import fingerprint, read

    p = Progress("control")
    t0 = time.monotonic()
    sound = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)}
    tasks = [t for t in read(paths.tasks) if t.task_id in sound]
    # A control that could not run is not a control that failed. An errored row
    # sets ok=False, which marks the task broken and excludes it from the attempt
    # stage -- so one transient API error would retire a sound task permanently,
    # because resume keys on (task, control) regardless of why the row exists.
    # Errored rows are dropped and retried, exactly as errored attempts are.
    #
    # Keyed on the judge too, and the rows now record it. Without it a control
    # scored by one judge was resumed as done for the next, and unlike
    # calibration there was no `judge_model` on the row to notice it
    # afterwards -- the mismatch was undetectable.
    grader = judge_model()
    # How many readings each (task, control) already has from this judge. A row
    # written before `pass` existed counts as the first one.
    seen: dict[tuple, int] = {}
    asked: dict[tuple, int] = {}
    for r in completed(paths.controls):
        if r.get("judge_model", grader) == grader:
            k = (r["task_id"], r["control"])
            seen[k] = seen.get(k, 0) + 1
            asked[k] = max(asked.get(k, 1), int(r.get("passes") or 1))

    # The requirement ratchets: once a run asked for five readings, five it
    # is, however many a later run asks for. `controlled()` requires as many
    # finished readings as the rows say were asked, so a run at --passes 5
    # that left two readings errored, re-run at --passes 3, found three
    # finished, said "already done", and `controlled()` excluded the task on
    # three of five -- with no note anywhere. Topping up to the largest ask
    # closes that: the stage always finishes what some run started.
    def need(task, control) -> int:
        return max(max(1, passes), asked.get((task.task_id, control.name), 1))

    jobs = p.cap([
        (t, c, n)
        for t in tasks for c in CONTROLS
        for n in range(seen.get((t.task_id, c.name), 0), need(t, c))
    ], limit, sum(need(t, c) for t in tasks for c in CONTROLS))
    ratcheted = sorted({t.task_id for t, c, _ in jobs if need(t, c) > max(1, passes)})
    if ratcheted:
        p.notes.append(
            f"{len(ratcheted)} task(s) were asked for more readings by an earlier run "
            f"than --passes {passes}; finishing that count: {', '.join(ratcheted[:4])}"
            + (" ..." if len(ratcheted) > 4 else "")
        )
    if not jobs:
        p.took_s = time.monotonic() - t0
        return p

    async def one(task, control, n):
        try:
            result = await check(task, control, model=grader)
            append(paths.controls, {**result.to_json(), "judge_model": grader,
                                    "pass": n, "passes": need(task, control),
                                    "task_fingerprint": fingerprint(task)})
            return "behaved" if result.ok else "wrong"
        except Exception as e:
            append(
                paths.controls,
                {"task_id": task.task_id, "control": control.name, "ok": False,
                 "judge_model": grader, "pass": n, "passes": need(task, control),
                 "error": f"{type(e).__name__}: {e}",
                 "detail": f"the control could not run: {type(e).__name__}"},
            )
            return f"{type(e).__name__}: {e}"

    results = await _gather([one(t, c, n) for t, c, n in jobs], concurrency)
    # A verdict and an error are different news. A dropped connection was
    # printed as "behaved wrongly -- those tasks are unsound", about a reading
    # that was stored as an error and succeeds on the next run. And both are
    # appended: assigned, this threw away the note above saying an earlier
    # run had asked for more readings.
    wrong = sum(1 for r in results if r == "wrong")
    errors = [r for r in results if r not in ("behaved", "wrong")]
    p.produced = sum(1 for r in results if r == "behaved")
    p.failed = wrong + len(errors)
    if wrong:
        p.notes.append(f"{wrong} control checks behaved wrongly -- those tasks are unsound")
    if errors:
        p.notes.append(f"{len(errors)} control checks could not run and will be retried: "
                       f"{str(errors[0] or 'the job raised')[:90]}")
    p.took_s = time.monotonic() - t0
    return p


