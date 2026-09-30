# The research pipeline

How this repository builds tasks from the SWE-chat corpus and runs its reference candidates. To
test your own agent on v1, see [running your agent](running.md) instead.

Setup:

    python -m venv .venv && .venv/bin/pip install -e .
    echo 'OPENAI_API_KEY=...' > .env

The corpus is expected at `data/swe-chat/`. Download SWE-chat from Hugging
Face (`SALT-NLP/SWE-chat`; access is gated behind its terms). Other providers
are opt-in:

    ERRATA_PROVIDER=azure ERRATA_MODEL=<deployment> python run.py stages ...

Building tasks and running candidates:

    python run.py moments --limit 400      collect pushback moments (--later for later pushbacks)
    python run.py stages --through screen  the cheap stages: no containers, about eight model calls a moment
    python run.py stages                   everything, including candidates in containers
    python run.py status                   what exists so far

Grading stored answers with another grader, running no candidate:

    python run.py rejudge --run runs/<run> --judge <deployment> --passes 3
    scripts/rejudge-rounds.sh <judge> <concurrency> <passes> <run>...   retries until nothing errored

Analysis:

    python scripts/grid_table.py runs/<run>...           rates per model
    python scripts/paired_tests.py runs/<run>...         paired sign-flip tests with Holm's correction
    python scripts/judge_agreement.py --judge <judge> runs/<run>...    agreement between graders
    python scripts/flag_sample.py <judge> <out> runs/<run>...          a fixed sample of flags to read
    python scripts/flag_tally.py <sample.json> <first> --second <second> --adjudicated <file>
    python scripts/funnel.py                             the funnel, counted from the corpus

Checks, as CI runs them:

    python checks/guards_hold.py            the regression suite
    python checks/fixes_are_still_in.py     one live check per fixed bug
    python checks/split_changes_nothing.py
    python checks/front_stages_run.py
    python checks/imports_resolve.py
    harbor-env/bin/python checks/harbor_agents.py      in Harbor's environment
    .venv/bin/python checks/verifier_in_container.py   needs Docker

**Cost.** Grading dominates. The D-40 run (six models, 990 answers, two graders,
three readings each, plus each grader's checks) cost about $1,320 at list
prices. A three-model run of one attempt per task costs about $330–350.

## Repository layout

    src/errata_bench/
      corpus/       SWE-chat: sessions, turns, and the calls its table lost (recover.py)
      find/         which moments can become tasks: triage, reading, the four turns,
                    the defect signature, the three screening gates, redaction
      construct/    rebuilding the tree: checkout, edit replay, the consistency check,
                    the defect check, the container
      instrument/   admission: calibration, controls, the instrument checks, the gate
      score/        the attempt harness and the graders: judge, trace check,
                    structure; re-grading
      stages/       the stages and the driver
      store/        run directories: rows in, rows out; errored and stale rows pruned by atomic rewrite
      release/      v1: freezing tasks, the Harbor export and verifier, admission,
                    grading Harbor trials, the report, the reference agent
      llm.py        model access, and which model version was served
      spec.py       the task, and its fingerprint
    scripts/        analysis, sampling, tallies, and the run and spend-guard scripts
    src/errata_harbor/  the Harbor side: the reference agent and task digests
    checks/         the suites CI runs (seven), and three run by hand
    results/        every published number, as produced

## Documents

- [`docs/running.md`](running.md): how to run an agent on v1 and grade it.
- [`docs/known-issues.md`](known-issues.md): what is known to be wrong
  or unproven in v1, how much it matters, and its issue.
- [`docs/research-log.md`](research-log.md): every decision, bug,
  experiment and result, dated, with its evidence. Registrations and their
  amendments are here.
- [`docs/data-map.md`](data-map.md): where each step's output lives, and
  how it is backed up.
- [`docs/SWE-CHAT-FINDINGS.md`](SWE-CHAT-FINDINGS.md): whether SWE-chat can
  become a runnable benchmark at all (measured 13 September).
- [`docs/PUSHBACK-FINDINGS.md`](PUSHBACK-FINDINGS.md): whether developer
  pushback identifies real agent errors (measured 14 September).
- [`checks/README.md`](../checks/README.md): what each check suite covers.
