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

**`front_stages_run.py`** runs moments -> triage -> read -> locate -> signature
-> screen end to end with the model faked, and asserts a row survives all five
and arrives with the fields `build` needs. Everything before `build` had no
test at all, which is why the 09-20 restructure broke `stage_triage` and
`stage_locate` (B-212) with every other suite passing. Its fakes return the
real pydantic models, and every one asserts it was actually called -- a stub
that silently never runs is how B-151 passed while testing nothing.

**`oracle_over_real_runs.py`** fingerprints every read-only path over the run
directories on disk: each stored row, admission under both standards,
`summarise`, `compare`, `tally_of`, the per-row pass predicates, the structure
rebuild and the two-column `across()`. Run it before a refactor and after, and
diff. It found no difference across the 09-20 restructure, in which every
module in the package moved. It reads `runs/`, which is gitignored, so on a
fresh clone it prints ABSENT and proves nothing -- the right tool before a
refactor, the wrong one in CI.

A note on `split_changes_nothing.py` after the restructure: the pre-split
`pipeline.py` it loads was written against the flat layout, so it asks for
`errata_bench.attempt`, `errata_bench.judge` and the rest. Those names are
aliased back into `sys.modules` for the length of the check, pointing at
today's modules -- which is the point, since the check isolates `pipeline.py`
and says so in its own header.
