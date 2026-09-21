"""Reading and writing a stage's rows, safely, from several processes.

Every row here is a paid model call or a container run, so the primitives are
written for the failure that costs money: a kill partway through a write, two
processes appending at once, a tidy that overwrites what arrived while it was
thinking. Pure stdlib by design -- nothing in this module knows what a task is.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


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
    # Read as bytes and decode per line. `read_text()` decodes the whole file
    # at once, so a row cut off in the middle of a character -- an em dash in a
    # model's reply, which is common -- raised UnicodeDecodeError and made the
    # entire file unreadable, losing every finished row in it. B-126 repaired
    # the newline and left this half of the same accident in place.
    for raw in path.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw.decode()))
        except (ValueError, UnicodeDecodeError):
            broken += 1
    if broken:
        print(f"  note: skipped {broken} incomplete row(s) in {path.name}", flush=True)
    return rows


@contextmanager
def only_one(directory: Path, doing: str, *, name: str = "run.lock"):
    """Refuse to start when another process is already working here.

    Every stage reads its output file to decide what is left and appends its
    results, so two of them over one directory do not collide -- they each do
    all of it. Measured: two `stages` over four moments produced 8 triaged
    rows, 16 readings, 32 trajectories, 64 signatures and 95 screened, the
    factor doubling at each stage because the next one reads the duplicated
    file, and 24 container runs for 12 answers. Nothing is lost; everything is
    paid for twice, and a later solo pass does not clean it up.

    `rejudge` and `gate` do the same kind of work and were not covered by this,
    because both return before `run_stages` ever runs. Both are driven by shell
    scripts that retry in rounds, and re-launching one because it looks stuck
    is the case this exists to refuse. They lock the directory each actually
    writes, so two judges still run side by side.
    """
    directory.mkdir(parents=True, exist_ok=True)
    guard = directory / name
    with guard.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(
                f"  another process is already {doing} in {directory}.\n"
                f"  Wait for it to finish, or use a different --run directory."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
    # Named for this process. With one fixed `.tmp` per stage file, two writers
    # raced on the same name: half the calls in a ten-way test raised
    # FileNotFoundError out of the middle of a stage, and the process that
    # reported success had written bytes that were not in the file.
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
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


