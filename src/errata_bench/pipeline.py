"""Run the benchmark end to end, one resumable stage at a time.

Until now this existed only as a chain of scripts in a scratch directory, run by
hand in an order held in someone's head. That is not a pipeline: a full run over
the corpus could not be reproduced, and a crash halfway through lost every model
call already paid for.

Each stage reads the previous stage's file and writes its own. A stage that
finds its output already present skips the rows it has, so an interrupted run
resumes instead of restarting, and a stage can be re-run alone after its code
changes without redoing the ones before it.

    moments      pushback moments worth reading      -> moments.jsonl
    triage       does the agent have work to object to-> triaged.jsonl
    read         which are genuine agent error       -> readings.jsonl
    locate       the four turns that define a task   -> trajectories.jsonl
    signature    what the defect looks like in a tree-> signatures.jsonl
    screen       answerable, leaking, repairable     -> screened.jsonl
    build        environment + defect verification   -> tasks.jsonl
    calibrate    can the judge read this task's pair -> calibration.jsonl
    control      does a do-nothing answer fail        -> controls.jsonl
    attempt      run candidates                      -> attempts.jsonl
    report       the numbers                         -> report.json

The stages before `attempt` cost roughly eight model calls per moment and no
containers. `attempt` costs one container and one judge call per run, several
runs per task. Splitting them means a cheap pass can establish the yield before
anything expensive starts.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

STAGES = (
    "triage",
    "read",
    "locate",
    "signature",
    "screen",
    "build",
    "calibrate",
    "control",
    "attempt",
    "report",
)


@dataclass
class Paths:
    """Where each stage's output lives."""

    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    @property
    def report(self) -> Path:
        return self.root / "report.json"


def load(path: Path) -> list[dict]:
    """Every complete row in a stage's file.

    A line that will not parse is skipped rather than raised. Killing a run
    mid-write leaves its last line truncated, and a loader that crashes on that
    makes the entire run unreadable -- every finished row lost to one interrupted
    append. Skipping costs at most the row that was being written when the
    process died, which by definition never completed.
    """
    if not path.exists():
        return []
    rows, broken = [], 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            broken += 1
    if broken:
        print(f"  note: skipped {broken} incomplete row(s) in {path.name}", flush=True)
    return rows


def replace(path: Path, rows: list[dict]) -> None:
    """Rewrite a stage's file atomically.

    Writing in place means a kill partway through leaves a half-written file and
    destroys every row that was already there. Writing a sibling and renaming
    makes the swap atomic: the reader sees either the old file or the new one.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(path)


def append(path: Path, row: dict) -> None:
    """Write one row immediately.

    Rows are flushed as they are produced rather than at the end, so an
    interrupted stage keeps what it has done. A five-minute git fetch once
    killed a rebuild and lost every model call before it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def key_of(row: dict) -> tuple:
    """What makes a row unique: one pushback moment in one session.

    Turn zero is a real turn. Written as ``turn_number or complaint`` it is
    falsy, so a moment at turn 0 silently keys on its complaint turn instead --
    a different number, so the row looks new on every resume and is processed
    again, or collides with another row that genuinely has that complaint.
    """
    turn = row.get("turn_number")
    if turn is None:
        turn = row.get("complaint")
    return (row.get("session_id"), turn)


def completed(path: Path) -> list[dict]:
    """Rows a stage actually finished, dropping any that errored.

    A row that failed is not a row that is done. Written without this, resume
    keys on identity alone and an errored row looks finished forever: a run that
    exhausted its API credits partway recorded 253 read failures, and resuming
    after a top-up would have skipped every one of them permanently, leaving a
    funnel that silently lost two thirds of its input.

    Dropping them from the file is what makes the retry happen -- the stage
    recomputes `done` from what is left, so the failed rows come back as work.
    """
    rows = load(path)
    kept = [r for r in rows if not r.get("error") and "error:" not in str(r.get("reason", ""))]
    if len(kept) != len(rows):
        replace(path, kept)
    return kept


def already_done(path: Path) -> set[tuple]:
    return {key_of(r) for r in completed(path)}


@dataclass
class Progress:
    """What a stage did, for the run log."""

    stage: str
    took_s: float = 0.0
    produced: int = 0
    skipped: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        bits = [f"{self.produced} produced"]
        if self.skipped:
            bits.append(f"{self.skipped} already done")
        if self.failed:
            bits.append(f"{self.failed} failed")
        return f"  {self.stage:10s} {self.took_s:6.0f}s  {', '.join(bits)}"


async def _gather(coros, limit: int):
    """Run with a ceiling on concurrency.

    The ceiling matters on a laptop: unbounded fan-out over hundreds of moments
    opens hundreds of connections and, at the attempt stage, would start a
    container per task.
    """
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros))


async def stage_triage(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Discard moments where the agent has not done anything to object to.

    One call each, against roughly eight for a full read. Fifty first-in-session
    moments were read at full price and every one came back unclear, because
    they were opening instructions rather than objections.
    """
    from .reader import build_excerpt, load_session_turns
    from .triage import triage

    p = Progress("triage")
    t0 = time.monotonic()
    moments = load(paths.moments)[:limit]
    done = already_done(paths.triaged)
    todo = [m for m in moments if key_of(m) not in done]
    p.skipped = len(moments) - len(todo)
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({m["session_id"] for m in todo})

    async def one(m):
        try:
            excerpt = build_excerpt(turns[m["session_id"]], m["turn_number"])
            verdict = await triage(excerpt)
            append(
                paths.triaged,
                {
                    **m,
                    "worth_reading": verdict.worth_reading,
                    "agent_has_acted": verdict.agent_has_acted,
                    "objects_to_that_work": verdict.objects_to_that_work,
                    "triage_reason": verdict.reason,
                },
            )
            return verdict.worth_reading
        except Exception as e:
            # A moment that cannot be triaged goes to the reader rather than
            # being dropped: the reader is the authority, and this stage exists
            # only to save money.
            append(paths.triaged, {**m, "worth_reading": True, "triage_reason": f"error: {e}"})
            return True

    results = await _gather([one(m) for m in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.notes = [f"{len(todo) - p.produced} discarded before reading"]
    p.took_s = time.monotonic() - t0
    return p


async def stage_read(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Decide which pushback moments represent a genuine agent error."""
    from .reader import load_session_turns, read_pushback

    p = Progress("read")
    t0 = time.monotonic()
    triaged = load(paths.triaged)
    if triaged:
        moments = [m for m in triaged if m.get("worth_reading")][:limit]
    else:
        moments = load(paths.moments)[:limit]
    done = already_done(paths.readings)
    todo = [m for m in moments if key_of(m) not in done]
    p.skipped = len(moments) - len(todo)
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({m["session_id"] for m in todo})

    async def one(m):
        try:
            reading = await read_pushback(turns[m["session_id"]], m["turn_number"])
            append(paths.readings, {**m, "reading": reading.model_dump()})
            return True
        except Exception as e:
            append(paths.readings, {**m, "error": f"{type(e).__name__}: {e}"})
            return False

    results = await _gather([one(m) for m in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_locate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Find the four turns that define each task."""
    from .reader import load_session_turns
    from .trajectory import boundaries, locate

    p = Progress("locate")
    t0 = time.monotonic()
    viable = [
        r
        for r in load(paths.readings)
        if (r.get("reading") or {}).get("benchmark_viable")
    ]
    done = already_done(paths.trajectories)
    todo = [r for r in viable if key_of(r) not in done]
    p.skipped = len(viable) - len(todo)
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({r["session_id"] for r in todo})

    async def one(r):
        try:
            t = await locate(turns[r["session_id"]], r["turn_number"])
            b = boundaries(t)
            append(
                paths.trajectories,
                {
                    "session_id": r["session_id"],
                    "repo_id": r.get("repo_id"),
                    "complaint": r["turn_number"],
                    "failed": t.failed_turn,
                    "resolved": t.resolved_turn,
                    "request": t.request_turn,
                    "cut": b.cut_turn,
                    "usable": b.usable,
                    "reason": b.reason,
                    "defect": t.defect,
                    "resolution": t.resolution,
                    "rounds": t.rounds,
                },
            )
            return True
        except Exception as e:
            append(
                paths.trajectories,
                {
                    "session_id": r["session_id"],
                    "repo_id": r.get("repo_id"),
                    "complaint": r["turn_number"],
                    "usable": False,
                    "reason": f"error: {type(e).__name__}: {e}",
                },
            )
            return False

    results = await _gather([one(r) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_signature(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Work out what each defect looks like in a repository."""
    from .signature import derive

    p = Progress("signature")
    t0 = time.monotonic()
    usable = [r for r in load(paths.trajectories) if r.get("usable")]
    done = already_done(paths.signatures)
    todo = [r for r in usable if key_of(r) not in done]
    p.skipped = len(usable) - len(todo)

    async def one(r):
        try:
            s = await derive(
                r.get("defect", ""), r.get("resolution", ""), repo_id=r.get("repo_id", "")
            )
            append(paths.signatures, {**r, **s.model_dump()})
            return True
        except Exception as e:
            append(paths.signatures, {**r, "error": f"{type(e).__name__}: {e}"})
            return False

    if todo:
        results = await _gather([one(r) for r in todo], concurrency)
        p.produced = sum(1 for r in results if r)
        p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_screen(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Check each conversation is answerable, and repair it if it leaks."""
    from .answerable import asks_for_something
    from .build import last_user_message
    from .leakage import signals_trouble
    from .reader import build_excerpt, load_session_turns
    from .redact import apply, survey
    from .scope import in_scope

    p = Progress("screen")
    t0 = time.monotonic()
    rows = [r for r in load(paths.signatures) if r.get("kind")]
    done = already_done(paths.screened)
    todo = [r for r in rows if key_of(r) not in done]
    p.skipped = len(rows) - len(todo)
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({r["session_id"] for r in todo})

    async def one(r):
        out = dict(r)
        try:
            ts = turns[r["session_id"]]
            message = last_user_message(ts, r["cut"])
            if message is None:
                out["asks_for_something"] = False
                out["request_reason"] = "no user message within 80 turns of the cut"
            else:
                a = await asks_for_something(message.get("content") or "")
                out["asks_for_something"] = a.asks_for_something
                out["request_reason"] = a.request or a.reasoning

            # Is the defect even reachable from what was asked? nsega-mcp-todoist
            # asked "create the pull request" and its defect is a linter version
            # in a CI workflow; three candidates reported the pull request, the
            # only sensible answer, and all three were scored off_target.
            request = (message or {}).get("content") or ""
            if request:
                scope = await in_scope(request, r.get("defect", ""))
                out["within_scope"] = scope.within_scope
                out["scope_reason"] = scope.reason
            else:
                out["within_scope"] = False
                out["scope_reason"] = "no request to judge scope against"

            leak = await signals_trouble(build_excerpt(ts, r["cut"]))
            out["signals_trouble"] = leak.signals_trouble
            out["leak_reason"] = leak.reasoning
            out["redacted_turns"] = []
            out["rewritten_turns"] = {}
            out["redaction_worked"] = False

            if leak.signals_trouble:
                red = await survey(ts, r["cut"])
                out["diffuse"] = red.diffuse
                if red.repairable:
                    kept = apply(ts, red.removed_turns, red.rewritten)
                    again = await signals_trouble(build_excerpt(kept, r["cut"]))
                    if not again.signals_trouble:
                        out["redacted_turns"] = red.removed_turns
                        out["rewritten_turns"] = {
                            str(k): v for k, v in red.rewritten.items()
                        }
                        out["redaction_worked"] = True
            append(paths.screened, out)
            return True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            append(paths.screened, out)
            return False

    results = await _gather([one(r) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


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
    from .build import build
    from .spec import write

    p = Progress("build")
    t0 = time.monotonic()
    rows = [r for r in load(paths.screened) if not r.get("error")]
    result = build(rows)
    write(result.tasks, paths.tasks)

    surviving = {t.task_id for t in result.tasks}
    for downstream in (paths.calibration, paths.controls, paths.attempts):
        if not downstream.exists():
            continue
        kept = [r for r in load(downstream) if r.get("task_id") in surviving]
        dropped = len(load(downstream)) - len(kept)
        if dropped:
            replace(downstream, kept)
            p.notes.append(f"dropped {dropped} stale rows from {downstream.name}")
    p.produced = len(result.tasks)
    p.failed = len(result.rejected)
    p.notes = [f"{r.repo_id} t={r.complaint_turn}: {r.reason[:70]}" for r in result.rejected]
    p.took_s = time.monotonic() - t0
    return p


async def stage_calibrate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Check the judge can read each task's known-wrong and known-right answers."""
    from .judge import calibrate
    from .spec import read

    p = Progress("calibrate")
    t0 = time.monotonic()
    tasks = read(paths.tasks)
    done = {r["task_id"] for r in completed(paths.calibration)}
    todo = [t for t in tasks if t.task_id not in done]
    p.skipped = len(tasks) - len(todo)

    async def one(t):
        try:
            c = await calibrate(t)
            append(
                paths.calibration,
                {
                    "task_id": t.task_id,
                    "sound": c.sound,
                    "separates": c.separates,
                    "order_invariant": c.order_invariant,
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
                {"task_id": t.task_id, "sound": False, "error": f"{type(e).__name__}: {e}",
                 "detail": f"{type(e).__name__}: {e}"},
            )
            return False

    if todo:
        results = await _gather([one(t) for t in todo], concurrency)
        p.produced = sum(1 for r in results if r)
        p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_control(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Score answers whose correct result is known, before running candidates.

    A benchmark that cannot fail a candidate which does nothing is measuring
    noise, and this one could not: every introduced-defect task passed on the
    absence of the defect alone, so declining to work was a pass. Three
    attempts at one task read "Onboarding is blocked in this environment ... I
    made no changes" and all three were scored as successes.

    Running here, before `attempt`, means a broken task is caught for the price
    of two judge calls rather than after a container has run against it.
    """
    from .control import CONTROLS, check
    from .spec import read

    p = Progress("control")
    t0 = time.monotonic()
    sound = {r["task_id"] for r in load(paths.calibration) if r.get("sound")}
    tasks = [t for t in read(paths.tasks) if t.task_id in sound]
    # A control that could not run is not a control that failed. An errored row
    # sets ok=False, which marks the task broken and excludes it from the attempt
    # stage -- so one transient API error would retire a sound task permanently,
    # because resume keys on (task, control) regardless of why the row exists.
    # Errored rows are dropped and retried, exactly as errored attempts are.
    done = {(r["task_id"], r["control"]) for r in completed(paths.controls)}
    jobs = [(t, c) for t in tasks for c in CONTROLS if (t.task_id, c.name) not in done]
    p.skipped = len(tasks) * len(CONTROLS) - len(jobs)
    if not jobs:
        p.took_s = time.monotonic() - t0
        return p

    async def one(task, control):
        try:
            result = await check(task, control)
            append(paths.controls, result.to_json())
            return result.ok
        except Exception as e:
            append(
                paths.controls,
                {"task_id": task.task_id, "control": control.name, "ok": False,
                 "error": f"{type(e).__name__}: {e}",
                 "detail": f"the control could not run: {type(e).__name__}"},
            )
            return False

    results = await _gather([one(t, c) for t, c in jobs], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    if p.failed:
        p.notes = [f"{p.failed} control checks behaved wrongly -- those tasks are unsound"]
    p.took_s = time.monotonic() - t0
    return p


async def stage_attempt(
    paths: Paths, limit: int, concurrency: int, repeats: int = 3
) -> Progress:
    """Run candidates against every task the judge can read."""
    from .attempt import run
    from .container import MAX_CONTAINERS, image_for, sweep
    from .corpus import load_repos
    from .judge import judge
    from .reader import MODEL as JUDGE_MODEL
    from .spec import read
    from .structure import analyse, combine
    from .trace import check as check_trace

    p = Progress("attempt")
    t0 = time.monotonic()
    sweep()
    sound = {r["task_id"] for r in load(paths.calibration) if r.get("sound")}
    # A task a control passed is satisfiable without doing the work, so running
    # candidates against it measures nothing.
    broken = {r["task_id"] for r in load(paths.controls) if not r.get("ok")}
    tasks = [t for t in read(paths.tasks) if t.task_id in sound and t.task_id not in broken]
    # An errored attempt is not a finished one. Twelve of thirty-six attempts
    # died on API rate limits and were then counted as done, so a re-run would
    # have skipped exactly the work that needed redoing. Errored rows are
    # dropped here and their (task, run) pairs retried.
    done = {(r["task_id"], r["run"]) for r in completed(paths.attempts)}
    repos = load_repos()
    images = {
        t.task_id: image_for(getattr(repos.get(t.repo_id), "language", None))
        for t in tasks
    }
    jobs = [(t, i) for t in tasks for i in range(repeats) if (t.task_id, i) not in done]
    p.skipped = len(tasks) * repeats - len(jobs)
    if not jobs:
        p.took_s = time.monotonic() - t0
        return p

    # Containerised work is what strains a laptop, so it gets the tighter bound.
    box = asyncio.Semaphore(MAX_CONTAINERS)
    host = asyncio.Semaphore(max(1, concurrency // 2))

    async def one(task, i):
        image = images.get(task.task_id)
        async with (box if image else host):
            started = time.monotonic()
            attempt = await run(task, image=image)
            model = attempt.model
            if attempt.error:
                append(
                    paths.attempts,
                    {"task_id": task.task_id, "run": i, "error": attempt.error},
                )
                return False
            verdict = await judge(task, attempt.reply)
            structure = analyse(task, attempt, attempt.final_state)
            # A third reading, independent of both: does the answer's account of
            # its own work match the recorded trace. This is what the token check
            # cannot do for a behavioural defect.
            trace_check = await check_trace(
                attempt.reply, [c.to_json() for c in attempt.tool_calls]
            )
            score = combine(verdict, structure, trace_check)
            append(
                paths.attempts,
                {
                    "task_id": task.task_id,
                    "run": i,
                    "kind": task.kind,
                    "environment": attempt.environment,
                    "calls": structure.tool_calls,
                    # The trace itself, not just its length. Without it a
                    # finished run cannot be re-examined: every attempt in the
                    # first corrected run recorded "9 calls" and nothing about
                    # what those calls were, so no later check could ask whether
                    # the candidate ran what it claimed to have run.
                    "tool_calls": [c.to_json() for c in attempt.tool_calls],
                    "model": model,
                    # Which model graded this, since it need not be the one
                    # that answered. Earlier rows omit it; they were graded by
                    # the candidate model itself.
                    "judge_model": JUDGE_MODEL,
                    "seconds": round(time.monotonic() - started, 1),
                    # Whole, not cut. The judge reads up to 12,000 characters
                    # and the trace check 8,000; storing 4,000 meant three
                    # answers could not be regraded on the text the original
                    # judge had actually read.
                    "reply": attempt.reply,
                    "judgement": verdict.to_json(),
                    **score.to_json(),
                },
            )
            return True

    results = await _gather([one(t, i) for t, i in jobs], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    sweep()
    p.took_s = time.monotonic() - t0
    return p


def stage_report(paths: Paths) -> Progress:
    """Count what happened, and how much of it is trustworthy."""
    from collections import Counter

    p = Progress("report")
    t0 = time.monotonic()
    attempts = [a for a in load(paths.attempts) if not a.get("error")]
    scoreable = [a for a in attempts if a.get("scoreable")]
    moments = len(load(paths.moments))
    readings = load(paths.readings)
    viable = [r for r in readings if (r.get("reading") or {}).get("benchmark_viable")]
    tasks = load(paths.tasks)
    sound = [c for c in load(paths.calibration) if c.get("sound")]

    report = {
        "funnel": {
            "moments": moments,
            "read": len(readings),
            "viable": len(viable),
            "trajectories_usable": sum(
                1 for t in load(paths.trajectories) if t.get("usable")
            ),
            "tasks_built": len(tasks),
            "tasks_calibrated": len(sound),
        },
        "attempts": len(attempts),
        "scoreable": len(scoreable),
        "passed": sum(1 for a in scoreable if a.get("passed")),
        "made_unverified_claim": sum(1 for a in scoreable if a.get("dishonest")),
        "checked_first": sum(1 for a in scoreable if a.get("checked")),
        "outcomes": dict(Counter(a.get("outcome") for a in scoreable)),
        "by_kind": {
            k: {
                "attempts": sum(1 for a in scoreable if a.get("kind") == k),
                "passed": sum(
                    1 for a in scoreable if a.get("kind") == k and a.get("passed")
                ),
            }
            for k in ("present", "introduced")
        },
    }
    paths.report.write_text(json.dumps(report, indent=2))
    p.produced = len(scoreable)
    p.took_s = time.monotonic() - t0
    p.notes = [json.dumps(report["funnel"]), json.dumps(report["outcomes"])]
    return p


async def run_stages(
    root: Path,
    stages: tuple[str, ...] = STAGES,
    *,
    limit: int = 10_000,
    concurrency: int = 4,
    repeats: int = 3,
) -> list[Progress]:
    """Run the named stages in order, skipping work already recorded."""
    paths = Paths(root)
    out = []
    for name in stages:
        if name == "triage":
            out.append(await stage_triage(paths, limit, concurrency))
        elif name == "read":
            out.append(await stage_read(paths, limit, concurrency))
        elif name == "locate":
            out.append(await stage_locate(paths, limit, concurrency))
        elif name == "signature":
            out.append(await stage_signature(paths, limit, concurrency))
        elif name == "screen":
            out.append(await stage_screen(paths, limit, concurrency))
        elif name == "build":
            out.append(stage_build(paths, limit))
        elif name == "calibrate":
            out.append(await stage_calibrate(paths, limit, concurrency))
        elif name == "control":
            out.append(await stage_control(paths, limit, concurrency))
        elif name == "attempt":
            out.append(await stage_attempt(paths, limit, concurrency, repeats))
        elif name == "report":
            out.append(stage_report(paths))
        else:
            raise ValueError(f"unknown stage: {name}")
        print(out[-1].line(), flush=True)
    return out
