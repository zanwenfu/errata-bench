# Checks

Seven scripts, no network, no Docker, no model calls. Run them from the
repository root with the project's own interpreter:

    .venv/bin/python checks/imports_resolve.py
    .venv/bin/python checks/front_stages_run.py
    .venv/bin/python checks/fixes_are_still_in.py
    .venv/bin/python checks/guards_hold.py
    .venv/bin/python checks/split_changes_nothing.py
    .venv/bin/python checks/oracle_over_real_runs.py     # needs runs/
    .venv/bin/python checks/renderer_effect.py           # needs runs/, a one-off measurement

Four review rounds in two days found that these pass through real bugs more
often than they catch them -- every suite was green while the pipeline could
not build a task, while a move broke every stage that reads the corpus, and
while a check's own fixture had a field no real object has. They are a floor,
not a proof. A fix counts as covered only when its check has been shown red
with the fix reverted, and that output pasted, not asserted.

Each exits non-zero on failure and prints one line per assertion.

**`split_changes_nothing.py`** takes the last revision of `pipeline.py` from
before grading became its own stage, runs it and the current pair over the same
fakes, and compares every field the old row carried, on every scored row, and requires that there be six of them. Splitting the stage was
supposed to change how fast the work runs and nothing else; this is what says
so. It also covers resume from stored answers alone and from stored grades, the run
directories made before the split, and what happens when a candidate or a
grading fails.

**`guards_hold.py`** is thirty numbered sections, one per guard: what the
attempt and grade stages refuse to do; the gate asked repeatedly; the
reference-answer control and when it is not applicable; the clean-pass
standard and the hedged one priced side by side; permanent git loss; the
checkout election; the screening gates asked repeatedly against the real
models; what the trace records versus what the candidate saw; recovered tool
names; a report that cannot disagree with itself; the throttle retry made to
fail once; controls asked repeatedly, with a proven top-up; the did_the_work
half of a pass priced everywhere; the renderer's last-call reserve; and a
control run for either standard.

**`fixes_are_still_in.py`** is one live assertion per bug found on 09-20 and
after (B-122 to B-164, B-210, B-211 and GATE-1 to GATE-6 in
`docs/research-log.md`). It exists because a log entry
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

**`imports_resolve.py`** resolves every import in the package -- module-level,
deferred inside a function, and plain `import a.b` -- against the real package,
and asserts the corpus is where the code looks. It exists because 82 of the
package's imports are deferred and a wrong one fails only when its stage runs;
two of those were the expensive stages, and every other suite was green.

**`renderer_effect.py`** is a one-off measurement, not a guard: the same 81
answers graded through a starved trace renderer and a repaired one, to see
whether the clipping had been manufacturing honesty flags. It had not -- one
verdict of 81 moved, the other way. Kept because the question will come up
again.
