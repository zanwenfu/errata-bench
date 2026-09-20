# Checks

Three scripts, no network, no Docker, no model calls. Run them from the
repository root with the project's own interpreter:

    .venv/bin/python checks/split_changes_nothing.py
    .venv/bin/python checks/guards_hold.py
    .venv/bin/python checks/fixes_are_still_in.py

Each exits non-zero on failure and prints one line per assertion.

**`split_changes_nothing.py`** takes the last revision of `pipeline.py` from
before grading became its own stage, runs it and the current pair over the same
fakes, and compares every field the old row carried, on every scored row, and requires that there be six of them. Splitting the stage was
supposed to change how fast the work runs and nothing else; this is what says
so. It also covers resume from stored answers alone and from stored grades, the run
directories made before the split, and what happens when a candidate or a
grading fails.

**`guards_hold.py`** covers what the two stages refuse to do: grade an answer
whose task has been rebuilt, grade one whose task is gone, grade with a judge
that was never calibrated, grade a second time with a different judge, read a
stored reading that is missing a field, spend a container on a task with no
conversation, or let one answer row grow without bound.

**`fixes_are_still_in.py`** is one live assertion per bug found on 09-20
(B-122 to B-149 in `docs/research-log.md`). It exists because a log entry
saying "fixed" is a claim, and several of these were dangerous enough that the
claim should be re-checkable: one of them deleted three finished run
directories on an ordinary-looking command.
