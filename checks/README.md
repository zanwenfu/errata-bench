# Checks

Eight scripts, no network, no Docker, no model calls. Run them from the
repository root with the project's own interpreter:

    .venv/bin/python checks/imports_resolve.py
    .venv/bin/python checks/front_stages_run.py
    .venv/bin/python checks/fixes_are_still_in.py
    .venv/bin/python checks/guards_hold.py
    .venv/bin/python checks/split_changes_nothing.py
    .venv/bin/python checks/oracle_over_real_runs.py     # needs runs/
    .venv/bin/python checks/renderer_effect.py           # needs runs/, a one-off measurement
    .venv/bin/python checks/checkout_election_over_corpus.py   # needs data/, ~6 min, a measurement

Four review rounds in two days found that these pass through real bugs more
often than they catch them -- every suite was green while the pipeline could
not build a task, while a move broke every stage that reads the corpus, and
while a check's own fixture had a field no real object has. They are a floor,
not a proof. A fix counts as covered only when its check has been shown red
with the fix reverted, and that output pasted, not asserted. Three assertions
written on 09-21 were hollow when that was actually done: one read the joined
notes of a stage, which end with two JSON dumps in which every field name
appears; one used a fixture a separate exclusion already caught, so it passed
with the rule it named reverted; and one compared an elapsed time without an
alarm, so it hung instead of failing. Assume the next one is hollow too until
its red output exists, and revert ONE fix at a time: reverting a whole file
tests the union of its branches, which is how half a symlink fix sat
uncovered while the suite passed. An independent reviewer ran 48 single-fix
reverts against the sections written that day; 4 left the suite fully green.

Each exits non-zero on failure and prints one line per assertion.

**`split_changes_nothing.py`** takes the last revision of `pipeline.py` from
before grading became its own stage, runs it and the current pair over the same
fakes, and compares every field the old row carried, on every scored row, and requires that there be six of them. Splitting the stage was
supposed to change how fast the work runs and nothing else; this is what says
so. It also covers resume from stored answers alone and from stored grades, the run
directories made before the split, and what happens when a candidate or a
grading fails. Its ninth section requires that every served-model probe the
stages made went to its stand-in, so none of it reaches the network.

**`guards_hold.py`** is one hundred and one numbered sections, one per guard: what the
attempt and grade stages refuse to do; the gate asked repeatedly; the
reference-answer control and when it is not applicable; the clean-pass
standard and the hedged one priced side by side; permanent git loss; the
checkout election; the screening gates asked repeatedly against the real
models; what the trace records versus what the candidate saw; recovered tool
names; a report that cannot disagree with itself; the throttle retry made to
fail once; controls asked repeatedly, with a proven top-up; the did_the_work
half of a pass priced everywhere; the renderer's last-call reserve; a
control run for either standard; a verdict read more than once settled to
the conservative one, on both sides of every agreement rate; the file tools
and the shell agreeing about where the repository is; nothing running on the
developer's machine without an opt-in, and the network screen against a table
of commands drawn from the corpus; tool caches, refused reads and the trace
check's claim counts; and the harness version, attempt limits and the counts
a report puts behind a rate; each gate reading as much as the candidate is
shown, the leak gate all of it; a re-judge's controls asked repeatedly, every
reading required, and the same rule where the numbers are printed; the network
screen against a table of commands and against the three shapes that used to
take it exponential (under an alarm, because with the fix reverted it does not
fail, it runs for ever); and the harness never following a link a candidate
made, and the snapshot recording it as a link rather than reading through
it; an attempt whose container died counted as a harness failure rather than
a model failure; the outcome name split where it hid the subject of the
benchmark, and re-derived wherever a stored row is read; the working copy put
in front of the judge, each file labelled with what the candidate did to it
and the block opening with what it changed; a stand-in reader required to
name every argument the real one takes; and a judge's self-agreement measured
on what it read rather than on what we renamed. Section 53 is what the
850-conversation screen must not be able to do: prune the rows of a task whose
remote was merely unreachable, wipe a directory of judge calls because the
guard did not count them, read a file off the developer's machine because a
model wrote an absolute path, resolve a judge's name to the run it is reading,
accept the flag that silently buys every row at full price, or grow the judge's
prompt without bound because a candidate ran a formatter. Sections 54 to 58
are the first full grid's: an exclusion naming the party that caused it, an
attempt ended at its budget when the model never answers, D-35's statistics
against brute-force enumeration, the human-study packets showing exactly what
each reader saw, and both judges read over the same answers, with D-35's
also-reported numbers. Section 59 keeps a task out of a re-judge's admission
when its trace check failed a control. Section 60 has every analysis script
refuse a path that is not a run directory. Sections 61 and 62 are phase A's
first repairs: the agent's earlier turns count as its own work for the trace
check, with each flag named, and the judge sees the conversation. Section 63
reads each control against its own conversation and checks the instrument per
task. Section 64 has every tool keep the clock and every attempt that runs out
still report. Section 65 has calibration read each answer against its own
conversation, as grading does. Section 66 checks a rebuilt tree against what
the conversation showed of it. Section 67 has the attempt, grading and
re-judge stages each record which model their deployment served, at their
start and end, and a probe that cannot be made recorded as such rather than
stopping the stage. Sections 68 to 73 are round 3's: fixed control answers
that claim nothing a conversation could make true or false, and an overclaim
that does not apply where its invented file already exists; a summary paired
with its call by id and quoted truthfully; the calls the corpus table lost put
back from the raw transcripts; the accepted answer read against that record,
with its budget filled, while the candidate's conversation stays as shown; lost
edits in the consistency check; and each control claim's source and problem
recorded. Section 74 keeps every claim a reading flagged on its row, however
many claims it checked. Section 75 has the analysis scripts read the honesty
endpoint under whichever trace rules each row was read by. Section 76 stamps
the re-grade of an empty answer with the harness that wrote it. Sections 77 to
79 are phase B's data repair: redaction that takes a recovered call with its
turn or its result, candidates shown the recovered calls only on tasks built
that way (older tasks keep their fingerprints), and a build that replays the
lost edits and rejects a tree git changed or one that contradicts its
conversation. Section 80 has each grade row record what its judge and trace
readings cost. Section 81 holds the trace check's third rules, their probes,
and the analysis reading third-rules rows. Section 82 collects later pushbacks,
one per session, after new work. Section 83 takes the judges' agreement between
two re-grades. Section 84 has the build skip the agent's own files (a plan in
~/.claude/plans/, its memory, a scratch file in /tmp) instead of rejecting the
task, while a path elsewhere still rejects. Section 85 compares only a HEAD printed
before the agent's own commit or reset with the base. Section 86 leaves out a
moment whose session has no timestamp before any call is spent on it; section 87
has the build reject edits it cannot replay: a tool it does not read (Zed's
mcp__acp__Edit), a sub-agent's, or a session whose transcript is another agent's. Section 88 skips an edit
whose result says it failed or was refused, and reads every redaction mark as a
wildcard. Section 89 reads each git call with its result -- declined calls
never ran, a stash its output shows popped changed nothing -- and flags `git mv`,
`git rm` and `gh pr checkout`. Section 90 chooses the base by when a commit
entered the history, never one of the session's own commits. Section 91 holds an unattended batch
together: turns loaded without converting whole batches, a dropped connection
retried, one surprising row rejected rather than the build aborted, gate
readings fingerprinted and pruned, and pruned rows set aside. Section 92 has no
request reach a Claude deployment: refused in the HTTP client before it is
sent, never retried, and refused where a model is chosen. Section 93 has the scope gate read the
request in its conversation; section 94 has the answerable gate read a reply after
the agent's message it answers. Section 95 has every answer row record its tokens
and how the attempt ended, including an attempt that called no tool, and fails
when a field the attempt reports is missing from the stored row. Section 96 sends
a request again when the provider answered it with nothing (no output, no tokens),
keeps an empty reply the model did read, and makes a request never answered an
error rather than an answer. Section 97 asks one question's readings one after
another -- in the grade stage and in the second judge's grading, controls and
probes -- so the later readings find the prompt cached. Section 98 makes a forced
final report the provider refuses the attempt's error rather than an empty
answer; one the clock cuts off stays a result (section 55). Section 99 has the
attempt replay the edits SWE-chat's table lost for a task built with them, as the
build did, and only for such a task. Section 100 sends the candidate's tools
without strict schemas, the same to every candidate. Section 101 has a call to a
tool the candidate does not have refused and recorded rather than ending the
attempt, through the model library's own loop as well as a stand-in. It closes by asserting it handed production back unpatched, because three
sections restored one module attribute through a name a fourth binds to
something else, and that the model library's Runner is the real one again (section 26
had left a stand-in in its place for every later section). Its fake judge sets the task's kind the way the
real one does -- left at the default, every task here was graded by the
present-kind rule and an attempt that did no work passed.

**`fixes_are_still_in.py`** is one live assertion per bug found on 09-20 and
after: B-122 to B-163, B-187, B-210, B-211 and GATE-1 to GATE-6 in
`docs/research-log.md`, and A6, which requires the stages' served-model probes
to have gone to the stand-in. B-164 is left out because it was a comment that said the
opposite of the code, with no behaviour to assert. It exists because a log entry
saying "fixed" is a claim, and several of these were dangerous enough that the
claim should be re-checkable: one of them deleted three finished run
directories on an ordinary-looking command.

**`front_stages_run.py`** runs moments -> triage -> read -> locate -> signature
-> screen end to end with the model faked, and asserts a row survives all five
and arrives with the fields `build` needs. Everything before `build` had no
test at all, which is why the 09-20 restructure broke `stage_triage` and
`stage_locate` (B-212) with every other suite passing. Its fakes return the
real pydantic models, and every one asserts it was actually called -- a stub
that silently never runs is how B-151 passed while testing nothing. Its fifth
section drives a leak carried by a tool result, one the developer typed, and
one that survives its repair through the real screening stage, and reads what
the row says about each. Its eighth has every screening stage read the record
with SWE-chat's lost calls put back, and the screened row say so.

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
and asserts the corpus is where the code looks. It exists because 105 of the
package's 279 imports are deferred and a wrong one fails only when its stage runs;
two of those were the expensive stages, and every other suite was green.

**`renderer_effect.py`** is a one-off measurement, not a guard: the same 81
answers graded through a starved trace renderer and a repaired one, to see
whether the clipping had been manufacturing honesty flags. It had not -- one
verdict of 81 moved, the other way. Kept because the question will come up
again.

**`checkout_election_over_corpus.py`** is a measurement too, and the one G-55
asks for by name: it runs any candidate version of `_checkout_root` against
every one of the 73,549 edit calls in the corpus and scores it against what the
session's own commits say its checkout was. The tree is synthesised from the
repository's recorded files rather than cloned, so the whole corpus is 4,452
sessions and six minutes instead of 4,452 checkouts. It scores three things per
candidate: the root elected, where each call would land once the fallback to
the repository's name is included, and what becomes of the session as a whole
-- clean, a stray file, a silently overwritten real file, or a rejected task.
Run it before touching the election and after; it is what says whether a change
is an improvement or a trade, and it is where the two 09-20 rewrites would have
been caught (rewrite 1 silently overwrites a file in 50 sessions the original
gets right; rewrite 2 rejects 12).
