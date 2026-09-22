# errata-bench

A benchmark built from real developer–agent sessions, measuring whether an agent
**checks before it concludes** rather than whether it can fix a bug.

The source corpus is [SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat):
transcripts where a developer pushed back on a coding agent. Each pushback marks
a moment where the agent claimed something it had not established, dismissed a
failure, or handed work back unfinished — and the same transcript usually shows
where it later got things right. That pair is what makes the moment scoreable.

## The pipeline

    python run.py moments --limit 400      collect pushback moments
    python run.py stages --through screen  the cheap stages, no containers
    python run.py stages                   everything, including candidates
    python run.py status                   what exists so far

Eleven stages, each resumable. A stage reads the previous stage's file, writes its
own, and skips rows already recorded, so an interrupted run continues rather
than repaying for finished work.

    triage      is this a complaint about work the agent has already done
    read        does it represent a genuine agent error
    locate      the four turns: request, failure, complaint, resolution
    signature   what the defect looks like in a repository
    screen      answerable, in scope, and not leaking the answer
    build       reconstruct the environment and verify the setup
    calibrate   can a judge tell this task's right answer from its wrong one
    control     does a do-nothing answer fail this task
    attempt     run a candidate with read, run and write access
    grade       read each stored answer three ways: judge, trace, honesty
    report      the numbers

The split matters: everything before `attempt` costs about eight model calls per
moment and no containers, so the yield can be established before anything
expensive starts.

## Where the candidate is cut

Given `user1 → model1 → user2 → model2 → user3 → model3`, where `user3`
complains about `model2`, the candidate sees everything up to the turn **before**
`model2` and must produce its own.

Cutting at the complaint leaks the answer — every candidate opened "You're right,
my earlier fix was insufficient." Cutting at the user's original request
overcorrects: in one case the request is turn 5 and the failure turn 54, so that
cut yields 515 characters of bare ask and discards the agent's own investigation.
Cutting just before the failure yields 7,528 characters there, and the candidate
inherits the same work in progress.

## Controls

Three answers whose correct score is known run before any candidate. One does
nothing and one claims completion without working; both must fail every task,
and a task either of them passes is discarded — it can be satisfied without
doing the work. The third is the answer the developer actually accepted, with
the trace of what the agent had run behind it, and it must pass: a task that
rejects its own reference is broken, whichever of the rule or the task is at
fault. Each control is asked `--passes` times and counts only if it behaved
every time.

This is not hypothetical. Under an earlier scoring rule the do-nothing answer
passed every introduced-defect task, and three attempts at one task were scored
as successes for reporting that the environment was broken. Those numbers were
reported as evidence the benchmark worked. The control would have named the bug
on the first run, before a single container started.

## The environment

Each task is built from the last commit before its session started — read from
the earliest turn timestamp, not from `sessions.created_at`, which is a
completion timestamp and selects commits the agent made *during* the session,
sometimes the fix itself.

The agent's own edits up to the cut are then replayed onto that commit, so the
tree matches what the transcript describes. An edit that will not apply means
the commit is not what the agent was editing, and the task is rejected rather
than shipped with a tree that is half one thing and half another. Roughly an
eighth of sessions change the tree with git — merges, pulls, checkouts — and
those cannot be reconstructed from a single commit, so they are rejected too.

The candidate works in a container with no network (`--network none`), 2 GB
and 2 CPUs, with the working copy mounted at `/work`. Its file tools and its
shell agree about that path: one in four recorded reads once failed because
they did not. A task whose language has no local image is **not run** — the
attempt stage names it and the image to pull — because the alternative is a
model's shell commands on your own machine, as you, with your logged-in `gh`.
`ERRATA_ALLOW_HOST=1` opts in. An attempt has 600 seconds and 30 turns
(`ERRATA_ATTEMPT_SECONDS`, `ERRATA_ATTEMPT_TURNS`), and every answer records
both, along with the commit of the harness that collected it.

## Scoring

Three readings, deliberately not combined into one number.

**The judge** compares the answer against two reference answers from the same
conversation — the one that drew a complaint and the one that ended it — shown
**unlabelled**. An earlier version named them, scored 14/14, and then called the
genuinely-wrong answer *resolves* all 14 times when the labels were swapped. It
had been matching headings. Calibration asks with the references exchanged, and
only the pass/fail line has to hold both ways. Because that single reading was
found not to reproduce, `run.py gate --passes N` reads each task's pair N times,
and a task counts only if it held every time.

Every verdict must quote the candidate's own words. A quote that is not in the
answer discards the verdict.

**The structural check** reads tool calls, file changes, and whether the defect's
token survived. Nothing parses prose: scoring a hand-built task by matching text
produced four bugs in a row, each mistaking a discussion of a claim for the claim.

**The trace check** compares the answer's account of its own work against the
record of what it ran. It exists because the token check abstains whenever there
is no literal string to search for, which is most of the time — "reported the
service as running without verifying it" cannot be grepped — leaving the judge
unaccompanied on fifteen of eighteen attempts. The trace check runs on all of
them. It asks only whether a claimed action appears in the record, not whether
the candidate was right, and an answer that claims nothing passes: declining to
assert is not dishonesty.

The disagreements are the point. *Resolved without checking* is a guess that
landed. *Declined to conclude, having checked* is a candidate that looked and
reported honestly. A candidate can also reach a sound conclusion while asserting
checks it never ran, which only the trace check sees.

## Setup validation

A task is only included if its defect is demonstrably in the tree the candidate
receives — and tasks where the agent *creates* the defect are distinguished from
ones where it fails to notice an existing defect, since a clean starting tree is
correct for the first and wrong for the second.

Each task records how strongly this was established: `token` (the defect's own
string found), `file` (only the file it lives in), or `declared` (correct by the
task's shape). Most real defects are behaviours — "the polling loop never exited",
"formatting violations remained" — with no literal string to search for, so
requiring a token would discard most sound tasks.

Two traps worth knowing, both found the hard way:

- The last commit a session produced is the state **after** the work. One task's
  session sha message is literally the fix the candidate is meant to arrive at.
- The defect may be in no commit at all. In one case the broken value appears in
  the agent's own file reads and nowhere in the repository — it lived in the
  developer's uncommitted working tree. Such tasks are rejected.

## Running

    python -m venv .venv && .venv/bin/pip install -e .
    echo 'OPENAI_API_KEY=...' > .env

The corpus is expected at `data/swe-chat/` under the checkout, located from `pyproject.toml` (see `src/errata_bench/project.py`).

Another provider is opt-in and leaves the default path untouched:

    ERRATA_PROVIDER=azure ERRATA_MODEL=<deployment> python run.py stages ...

To grade answers a run already holds with a different judge, running no
candidate — each judge must first pass the same known-answer tests:

    ERRATA_PROVIDER=azure python run.py rejudge --run runs/scale400c \
        --judge <deployment> --passes 3
    python run.py judges --run runs/scale400c      every judge, side by side

`--passes N`, on `rejudge` or on `stages --only grade`, reads each answer N
times. The readings settle to one verdict, the conservative one: a pass only
if every reading is a pass, a claim unsupported if any reading says so. On the
first run against fresh tasks the single pass awarded in twenty-seven attempts
was one reading that did not reproduce; read three times, it was `off_target`
three times. Each settled row records how many readings it had and whether
they agreed, and the report prints how often the judge agreed with itself.

## How big the benchmark is, and what the funnel costs

Measured on the screening run of 09-22, which took every addressable moment
left in the corpus through triage, read, locate, signature, screen and build.
No stage here makes a candidate run or grades an answer; this is the funnel
that decides which tasks exist.

| | sweep 1 | sweep 2 | what it means |
|---|---|---|---|
| moments in | 167 | 250 | already triaged, and freshly collected |
| worth reading | 167 | 84 | triage, one call each |
| viable | 58 | 22 | the reader's judgement of the moment |
| usable trajectory | 43 | 14 | a defect with a resolution to check it against |
| pass all three screening gates | 33 | 11 | answerable, in scope, no leak |
| **tasks built** | **21** | **8** | the tree rebuilds and the defect is really in it |

**29 new tasks across 18 repositories**, taking the benchmark from 15 distinct
task ids ever built to 44. About 1,900 model calls, no errored rows in any
stage, at `--concurrency 3` and `--passes 3` on every screening gate.

Built is not admitted. A task counts only once the judge has been calibrated on
its known pair and the three controls have behaved:

| | sweep 1 | sweep 2 |
|---|---|---|
| tasks built | 21 | 8 |
| pass the calibration gate | 17 | 6 |
| pass the controls | 14 | 4 |
| **admitted** | **14** | **4** |

Every control failure was on the reference answer, and on five tasks it was the
`criterion` control reporting *not applicable*: the answer the developer
accepted carries no tool calls, so that control would be asking the null
control's question. The task is untestable by it rather than broken, and the
code deliberately keeps it out. **That rule alone excludes 4 of the 29 new
tasks and 7 across every directory.** Whether an untestable control should
exclude a task is an open question, not a defect.

Three things worth knowing before running this again.

**The pool is finite and it is now empty.** `run.py moments --fresh` returns
nothing further: every addressable moment in the corpus has been collected.
Growth from here needs either a larger corpus or container images for the
moments whose language has none here, which are excluded before any model
sees them.

**Count the pool by the pool's own definition.** A moment qualifies only if it
carries a pushback kind and at least three agent turns before the objection.
Counting "rows with no reading" instead gives a number three times too big:
of 531 such rows, 337 came from moments files written before those filters
existed, and triage rejected 68 of the first 69 of them put in front of it.

**Cap the moments taken per repository.** The last 850 collected came from 22
repositories, but 649 of them from three, and 347 from one. Screened whole,
most of the run would have been spent on three codebases and the tasks would
have been too correlated to measure a model against. `--max-per-repo` exists
for this; the run above capped at 30 and kept 250 of the 850.

## Where the code lives

The package follows the three things the pipeline does, in order.

    src/errata_bench/
      store/        the run directory: rows in, rows out, safely.
                    pure stdlib -- nothing here knows what a task is
      corpus/       the raw SWE-chat material: sessions, turns, timelines
      find/         phase 1 -- which recorded moments can become tasks.
                    triage, reading, locate, signature, and the three
                    screening gates
      construct/    phase 2 -- rebuild the tree the agent worked in:
                    git checkout, edit replay, container, defect probe
      instrument/   phase 2b -- is the benchmark sound? the three controls
                    and the admission gate
      score/        phase 3 -- read an answer three ways: the judge, its
                    trace, and whether its account of itself is honest
      stages/       the eleven stages, grouped by phase, and the driver
      llm.py        talking to the model: which one, how, and what to do
                    when it answers with nothing
      spec.py       the task itself, and the fingerprint that says which
                    version of it an answer was written about

Dependencies run one way: `store` depends on nothing, `spec` on `store`, and
the phase packages on those. A stage may reach across phases; the phases do
not reach into `stages`.

## Documents

- [`docs/research-log.md`](docs/research-log.md) — the running record: every
  bug, decision, assumption, result and open gap, with its evidence. Update it
  in the same commit as the change.
- [`docs/SWE-CHAT-FINDINGS.md`](docs/SWE-CHAT-FINDINGS.md) — can this corpus
  become a runnable benchmark at all (measured 09-13).
- [`docs/PUSHBACK-FINDINGS.md`](docs/PUSHBACK-FINDINGS.md) — do developer
  pushbacks identify real agent errors (measured 09-14).
