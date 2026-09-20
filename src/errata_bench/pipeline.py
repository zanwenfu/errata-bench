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
    attempt      run candidates                      -> answers.jsonl
    grade        read each answer three ways         -> attempts.jsonl
    report       the numbers                         -> report.json

The stages before `attempt` cost roughly eight model calls per moment and no
containers. `attempt` costs one container per run, several runs per task.
Splitting them means a cheap pass can establish the yield before anything
expensive starts.

`attempt` and `grade` are separate for the same reason. A container is what
strains a laptop, so candidates run two or three at a time; grading is network
waiting, so it can run ten at a time. Held together, every grading call
occupied a slot that no candidate could use: eighty-one answers took two and a
half hours of wall clock for eleven hours of work, and the slowest single
attempt took twenty-two minutes while the candidate answered in seconds and a
slow judge queued behind a three-wide bound. Apart, the expensive resource is
released the moment the candidate stops using it.

The split also means a grading change costs no candidate runs: the answers are
already on disk, and re-grading them is one stage.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from contextlib import contextmanager
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
    "grade",
    "report",
)


# Every file a stage may read or write. Named rather than derived, because
# `__getattr__` answered any name at all: `paths.attemps` was a valid path to a
# file nothing writes, so a typo became a stage that found no work, did none,
# and reported success. A stage that reads the wrong file must fail loudly.
FILES = (
    "moments",
    "triaged",
    "readings",
    "trajectories",
    "signatures",
    "screened",
    "tasks",
    "calibration",
    "controls",
    "answers",
    "attempts",
)


@dataclass
class Paths:
    """Where each stage's output lives."""

    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name: str) -> Path:
        if name not in FILES:
            raise AttributeError(f"no stage file named {name!r}; expected one of {', '.join(FILES)}")
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


@contextmanager
def held(path: Path):
    """Exclusive access to one stage file, across processes.

    Appending and tidying are both safe alone and not safe together. `completed`
    reads a file, drops the errored rows and writes the rest back; another
    process appending in between has its row read by nobody and overwritten by
    the rename. Splitting grading out makes that likely rather than theoretical,
    because running the cheap stage again over a directory is now the obvious
    thing to do when one looks stuck.

    The lock is a sibling file, not the data file: `replace` renames a new file
    over the old one, and a lock on the old inode would protect nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    guard = path.with_suffix(path.suffix + ".lock")
    with guard.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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
    line = json.dumps(row).encode() + b"\n"
    # A row cut off by a kill leaves no newline, and the next append lands on
    # the same line: one truncated row silently eats the next good one as well,
    # and `load` skips the pair without either being recoverable. Closing the
    # broken line first costs one byte read and loses only the row that never
    # finished. Done in bytes: a text handle's seek accepts only offsets its own
    # tell produced, and `tell() - 1` happens to work solely because these rows
    # are ASCII today.
    # Probe and write under one lock: read outside it and another process can
    # append between the two, so the repair is decided against a file that no
    # longer ends where it did.
    with held(path):
        size = path.stat().st_size if path.exists() else 0
        if size:
            with path.open("rb") as fh:
                fh.seek(size - 1)
                if fh.read(1) != b"\n":
                    line = b"\n" + line
        with path.open("ab") as fh:
            fh.write(line)


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


def _succeeded(row: dict) -> bool:
    """Whether a row records work that finished. One definition, two readers."""
    return not row.get("error") and "error:" not in str(row.get("reason", ""))


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
    if all(_succeeded(r) for r in rows):
        return rows
    # Re-read under the lock before writing. The rows counted a moment ago may
    # be out of date by now, and writing that stale list back would delete
    # whatever another process appended in between.
    with held(path):
        kept = [r for r in load(path) if _succeeded(r)]
        replace(path, kept)
    return kept


def sort_answers(
    answers: list[dict], prints: dict[str, str], *, unstamped_is_stale: bool = True
) -> tuple[list, list, list]:
    """Split stored answers by whether they still describe their task.

    Task identifiers are the repository and the complaint turn, so a rebuilt
    task keeps its name while its content changes underneath. An answer written
    before that rebuild was about a different question.

    One helper for the three stages that must agree about this. The check went
    into grading alone first, and that was worse than not having it: grading
    refused the answer, the attempt stage counted it as work already done, and
    the rebuild kept it -- so the task sat at zero scored attempts for ever,
    re-running every stage changed nothing, and the only trace was a count in
    the report that never went down.

    An answer with no fingerprint at all is stale: nothing on disk predates the
    field, and refusing to grade is the safe direction. Scored rows are read
    with ``unstamped_is_stale=False``, because every run directory made before
    the split holds graded attempts carrying no fingerprint, and calling those
    stale would re-run eighty-one candidates to replace answers already paid
    for.
    """
    fresh, orphaned, stale = [], [], []
    for a in answers:
        task_id = a.get("task_id")
        stamp = a.get("task_fingerprint")
        if task_id not in prints:
            orphaned.append(a)
        elif stamp is None and not unstamped_is_stale:
            fresh.append(a)
        elif stamp != prints[task_id]:
            stale.append(a)
        else:
            fresh.append(a)
    return fresh, orphaned, stale


def finished(path: Path) -> list[dict]:
    """The same reading as `completed`, for a file this stage does not own.

    `completed` rewrites what it reads, which is right for the stage that
    produces a file and wrong for every other reader: the rewrite is a
    read-modify-write with no lock, so a grading stage tidying answers.jsonl
    while an attempt stage appends to it silently drops whatever was written in
    between. Now that answers and scores are separate files, two stages read
    each of them and only one writes it -- so only that one tidies it.
    """
    return [r for r in load(path) if _succeeded(r)]


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
        head = f"  {self.stage:10s} {self.took_s:6.0f}s  {', '.join(bits)}"
        # Notes were written in ten places and printed in none. Everything a
        # stage refuses or skips says so here -- a rebuild declining to empty a
        # finished directory, answers whose task has changed under them, a
        # grader that was never calibrated -- and all of it went to a field no
        # code read. A stage that stops to protect something has to say so on
        # the screen, or the protection is indistinguishable from doing nothing.
        return "\n".join([head] + [f"             {n}" for n in self.notes])


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
    from .spec import fingerprint, write

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
        p.notes = [
            "refused: no screened rows to build from, but this directory already holds "
            "tasks and results. Re-run the earlier stages first, or use --only to name "
            "the stage you meant."
        ]
        p.took_s = time.monotonic() - t0
        return p
    result = build(rows)
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
        rows = load(downstream)
        kept = [r for r in rows if still_describes(r)]
        if len(kept) != len(rows):
            replace(downstream, kept)
            p.notes.append(f"dropped {len(rows) - len(kept)} stale rows from {downstream.name}")
    p.produced = len(result.tasks)
    p.failed = len(result.rejected)
    # extend, not assign: the loop above records what it deleted, and assigning
    # here threw that away two lines later -- so the one message saying a
    # rebuild removed graded rows and paid-for answers never survived to be
    # printed, even once notes are printed.
    p.notes.extend(f"{r.repo_id} t={r.complaint_turn}: {r.reason[:70]}" for r in result.rejected)
    p.took_s = time.monotonic() - t0
    return p


async def stage_calibrate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Check the judge can read each task's known-wrong and known-right answers."""
    from .judge import calibrate
    from .reader import judge_model
    from .spec import read

    p = Progress("calibrate")
    t0 = time.monotonic()
    tasks = read(paths.tasks)
    done = {r["task_id"] for r in completed(paths.calibration)}
    todo = [t for t in tasks if t.task_id not in done]
    p.skipped = len(tasks) - len(todo)
    grader = judge_model()

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
    from .judge import can_be_scored
    from .reader import judge_model
    from .spec import read

    p = Progress("control")
    t0 = time.monotonic()
    sound = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)}
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
            result = await check(task, control, model=judge_model())
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
    from .attempt import INSTRUCTIONS as CANDIDATE_RULES
    from .attempt import environment_note, run, transcript_for
    from .container import image_for, max_containers, sweep
    from .corpus import load_repos
    from .judge import can_be_scored
    from .reader import load_session_turns
    from .spec import fingerprint, read
    from .structure import analyse

    p = Progress("attempt")
    t0 = time.monotonic()
    sweep()
    sound = {r["task_id"] for r in load(paths.calibration) if can_be_scored(r)}
    # A task a control passed is satisfiable without doing the work, so running
    # candidates against it measures nothing.
    broken = {r["task_id"] for r in load(paths.controls) if not r.get("ok")}
    tasks = [t for t in read(paths.tasks) if t.task_id in sound and t.task_id not in broken]
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
    prints = {t.task_id: fingerprint(t) for t in tasks}
    fresh, _, stale = sort_answers(completed(paths.answers), prints)
    if stale:
        # Removed, not ignored. Left in place they would be graded as
        # duplicates of the fresh answers about to replace them, and the report
        # counts rows. Only rows that carry a fingerprint and disagree with the
        # current task are removed, so nothing written before the field existed
        # is ever deleted on a rule it predates.
        replace(paths.answers, fresh)
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

    async def one(task, i):
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
                task, image=image, turns=turns_by_session.get(task.session_id)
            )
        if attempt.error:
            append(
                paths.answers,
                {"task_id": task.task_id, "run": i, "error": attempt.error},
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
                "rules": f"{CANDIDATE_RULES}\n\n{environment_note(attempt.environment)}",
                # Which version of this task the answer was written about.
                "task_fingerprint": fingerprint(task),
            },
        )
        return True

    results = await _gather([one(t, i) for t, i in jobs], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    sweep()
    p.took_s = time.monotonic() - t0
    return p


async def stage_grade(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Read every stored answer three ways and write the scored row.

    Nothing is re-run: the answer, its trace and what the tree showed are taken
    from `answers.jsonl` exactly as the candidate left them. Only the two model
    readings happen here, so this stage is network waiting and can run far wider
    than the candidates did.

    What it writes is the row the rest of the project already reads -- the
    report, the regrade tool and the comparison table were not touched, because
    `attempts.jsonl` still means the same thing.
    """
    from .attempt import INSTRUCTIONS as CANDIDATE_RULES
    from .attempt import environment_note, transcripts_for
    from .judge import judge
    from .reader import judge_model, model_name
    from .spec import fingerprint, read
    from .structure import Structure, combine
    from .trace import check as check_trace

    p = Progress("grade")
    t0 = time.monotonic()
    grader = judge_model()
    tasks = {t.task_id: t for t in read(paths.tasks)}
    prints = {tid: fingerprint(t) for tid, t in tasks.items()}
    # Read, not tidied: this stage does not own answers.jsonl, and `completed`
    # would rewrite it from a stale snapshot while the attempt stage may be
    # appending to it.
    answers, orphaned, stale = sort_answers(finished(paths.answers), prints)
    # Errored grades are dropped by `completed` and come back as work, exactly
    # as errored attempts do. This stage owns attempts.jsonl, so it tidies it.
    graded = completed(paths.attempts)
    graded, _, outdated = sort_answers(graded, prints, unstamped_is_stale=False)
    if outdated:
        replace(paths.attempts, graded)
        p.notes.append(
            f"dropped {len(outdated)} scores of an earlier version of their task"
        )
    done = {(r["task_id"], r["run"]) for r in graded}
    todo = [a for a in answers if (a["task_id"], a["run"]) not in done]
    p.skipped = len(answers) - len(todo)
    p.notes.append(f"graded by {grader}")
    # Naming no grader leaves `judge_model()` falling back to the candidate's
    # own model, which is the thing B-118 was fixed to stop. It is a legitimate
    # configuration -- the first eighteen scored answers were graded that way --
    # but it is never what someone wants by accident, and this stage is now run
    # by itself, where the setting is easiest to forget.
    if grader == model_name():
        p.notes.append(
            f"warning: {grader} is grading its own answers; set ERRATA_JUDGE_MODEL "
            f"to have a different model read them"
        )
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
    # Re-grading the same answers with a second judge is what the rejudge tool
    # is for: it writes under <run>/rejudge/<judge>/ and puts that judge through
    # the same known-answer tests first. Here, one answer has one grade, so
    # pointing a different judge at a graded run would otherwise do nothing at
    # all and report success.
    others = {r.get("judge_model") for r in graded} - {grader, None}
    if others:
        # Refused rather than reported. One answer has one grade here, so this
        # would have graded nothing and said "0 produced", which reads exactly
        # like a run with nothing left to do.
        p.notes.append(
            f"REFUSED: {len(graded)} answers here were graded by "
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
        p.failed = max(1, len(todo))
        p.took_s = time.monotonic() - t0
        return p
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    # Only for answers stored before the conversation was kept on the row.
    missing = [a for a in todo if a.get("transcript") is None]
    context = transcripts_for([tasks[a["task_id"]] for a in missing]) if missing else {}

    async def one(a):
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
                "task_id": a["task_id"], "run": a["run"], "judge_model": grader,
                "error": f"the stored reading could not be read: {e}",
            })
            return False
        row = {
            "task_id": a["task_id"],
            "run": a["run"],
            # The kind the judge actually applied, read from the task now, not
            # the label the answer was stored with. They are the same thing
            # while the fingerprint matches, and the row should carry the one
            # that decided the rule: `solved` for an introduced defect means
            # "did the work" and for a present one means "addressed it", so a
            # row labelled with the other kind reads as an impossible pass.
            "kind": task.kind,
            "task_fingerprint": prints[a["task_id"]],
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
            append(paths.attempts, {
                "task_id": a["task_id"], "run": a["run"], "judge_model": grader,
                "error": "no conversation to check the answer's claims against",
            })
            return False
        try:
            # The judge is shown what the candidate did, because "did it claim
            # something it had not established" cannot be read off the prose.
            verdict = await judge(
                task, row["reply"], tool_calls=row["tool_calls"], model=grader
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
                "task_id": a["task_id"], "run": a["run"], "judge_model": grader,
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

    results = await _gather([one(a) for a in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


def stage_report(paths: Paths) -> Progress:
    """Count what happened, and how much of it is trustworthy."""
    from collections import Counter

    p = Progress("report")
    t0 = time.monotonic()
    attempts = [a for a in load(paths.attempts) if not a.get("error")]
    scoreable = [a for a in attempts if a.get("scoreable")]
    answers = [a for a in load(paths.answers) if not a.get("error")]
    answer_keys = {(a.get("task_id"), a.get("run")) for a in answers}
    graded_keys = {(a.get("task_id"), a.get("run")) for a in attempts}
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
            # Answers collected, against answers read. A grading stage that
            # stopped partway would otherwise look like a smaller run rather
            # than an unfinished one -- the report counts graded rows, and
            # every ungraded answer is simply invisible to it.
            "answers_collected": len(answers),
            "answers_not_yet_graded": len(answers) - len(graded_keys & answer_keys),
            # Two processes grading one directory would each append a row for
            # the same attempt, and every count below would include it twice.
            # Stated rather than silently deduplicated: a number that repairs
            # itself hides the fact that something ran twice.
            "attempts_recorded_twice": len(attempts) - len(graded_keys),
        },
        "attempts": len(attempts),
        "scoreable": len(scoreable),
        "passed": sum(1 for a in scoreable if a.get("passed")),
        "made_unverified_claim": sum(1 for a in scoreable if a.get("dishonest")),
        "checked_first": sum(1 for a in scoreable if a.get("checked")),
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
    grade_concurrency: int | None = None,
) -> list[Progress]:
    """Run the named stages in order, skipping work already recorded.

    ``grade_concurrency`` is separate because grading is the one stage bounded
    by a provider rather than by this laptop, and the right number depends on
    which judge it is: one deployment answers in two to six minutes and takes
    ten at once, another is capped at six calls a minute and errors above four.
    Left unset it matches ``concurrency``, so a careless run is merely slow.
    """
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
        elif name == "grade":
            out.append(
                await stage_grade(paths, limit, grade_concurrency or concurrency)
            )
        elif name == "report":
            out.append(stage_report(paths))
        else:
            raise ValueError(f"unknown stage: {name}")
        print(out[-1].line(), flush=True)
    return out
