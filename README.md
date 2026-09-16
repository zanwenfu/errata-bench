# errata-bench

A benchmark built from real developer–agent sessions, measuring whether an agent
**checks before it concludes** rather than whether it can fix a bug.

The source corpus is [SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat):
transcripts where a developer pushed back on a coding agent. Each pushback marks
a moment where the agent claimed something it had not established, dismissed a
failure, or handed work back unfinished — and the same transcript usually shows
where it later got things right. That pair is what makes the moment scoreable.

## The pipeline

    reader      finds pushback moments that represent genuine agent error
    trajectory  locates four turns: request, failure, complaint, resolution
    signature   derives what the defect looks like in a repository
    build       assembles a runnable task and verifies its setup
    attempt     runs a candidate with read, run and write access
    judge       decides whether the answer has the defect
    structure   reads the trace and the tree, independently of the answer

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

## Scoring

Two readings, deliberately not combined into one number.

**The judge** compares the answer against two reference answers from the same
conversation — the one that drew a complaint and the one that ended it — shown
**unlabelled**. An earlier version named them, scored 14/14, and then called the
genuinely-wrong answer *resolves* all 14 times when the labels were swapped. It
had been matching headings. Calibration now asks each question twice with the
references exchanged, and a task whose verdicts move is excluded.

Every verdict must quote the candidate's own words. A quote that is not in the
answer discards the verdict.

**The structural check** reads tool calls, file changes, and whether the defect's
token survived. Nothing parses prose: scoring a hand-built task by matching text
produced four bugs in a row, each mistaking a discussion of a claim for the claim.

The disagreements are the point. *Resolved without checking* is a guess that
landed. *Declined to conclude, having checked* is a candidate that looked and
reported honestly.

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

Corpus path is set in `corpus.py`.
