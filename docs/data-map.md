# Where everything is, and how to review each step

Written 09-25 so that every step can be found, checked and redone on its own. The
research log (`docs/research-log.md`) says *why* each thing was done; this page
says *where* it is.

## Three places

| place | what is there | backed up |
|---|---|---|
| **GitHub** (this repository) | the code, the guard suite (`checks/`), the research log, the README, and `results/`: every summary, table and hand reading, small enough to keep in git | GitHub itself |
| **The laptop**: `runs/` (not in git) | every run directory: the task-building runs, the first grid, and D-40's runs copied back from the VPS | see below |
| **The VPS**: `root@167.235.236.135:/root/errata-bench-d40/runs/` | where D-40 runs. Its run directories are written here first | see below |

The SWE-chat corpus is not in git either. On the laptop, `data/swe-chat` links
to `~/IdeaProject/errata/data/corpora/swe-chat` (1.3 GB of parquet). The VPS
has its own copy.

## The steps, and the file each one writes

A **run directory** holds one file per step (`src/errata_bench/store/paths.py`
lists them). A step reads the previous step's file and adds rows to its own. It
never edits another step's file.

| # | step | what it decides | file in the run directory |
|---|---|---|---|
| 1 | moments | the developer messages that push back (`run.py moments`) | `moments.jsonl` |
| 2 | triage | is the complaint about work the agent already did? | `triaged.jsonl` |
| 3 | read | is it a genuine agent error, usable as a task? | `readings.jsonl` |
| 4 | locate | the request, the faulty answer, the complaint, the resolution | `trajectories.jsonl` |
| 5 | signature | what the defect looks like in the code | `signatures.jsonl` |
| 6 | screen | three gates: answerable, in scope, not leaking the answer | `screened.jsonl`, `rejections.jsonl` |
| 7 | build | rebuild the repository at that moment and confirm the defect | `tasks.jsonl` |
| 8 | admission | can the judge tell the known right answer from the known wrong one, repeatedly, and do the controls behave? | `calibration.jsonl`, `gate.jsonl`, `controls.jsonl` |
| 9 | attempt | a candidate model's answer, in a container with the rebuilt repository | `answers.jsonl` |
| 10 | grade | the first judge's three readings of each answer | `attempts.jsonl` |
| 11 | re-grade | a second judge: its own admission, then its three readings | `rejudge/<judge>/`: `calibration.jsonl`, `controls.jsonl`, `instrument.jsonl`, `attempts.jsonl` |

Also in a run directory:
- `served.jsonl`: which model each Azure deployment actually served;
- `report.json`: the stage's summary.

Each run also has a log beside it (`runs/<run>.log`, `runs/<run>.admit.log`).

## D-40, the confirmatory run

- **The task set.** `runs/d40-base/`: the 55 tasks with their admission rows.
  Its `d40.json` gives the three task lists; the same lists are in
  `results/d40-tasks-{headline,all,new}.json`. `results/step2-admission.txt`
  says which run each task came from.
- **One directory per candidate.** `runs/d40-<model>/` for grok-4.6,
  Kimi-K2.7-Code, DeepSeek-V4-Pro, DeepSeek-V4-Flash, Mistral-Large-3 and
  MAI-Thinking-1:
  - `answers.jsonl`: 165 answers, 55 tasks × 3 attempts;
  - `attempts.jsonl`: gpt-6-astra's 495 readings;
  - `rejudge/gpt-6-sol/`: gpt-6-sol's readings and its own checks.
- **gpt-6-sol's admission** of the 55 tasks, shared by every candidate:
  `runs/d40-soltests/`.
- **Runs set aside.** `runs/d40-aborted-0924`, `-0924b`, `-0924c` and `-0925a`
  are runs stopped for a bug or a quota; the research log says which and why.
  Their answers are in no analysis.
- **Smoke passes.** `runs/d40smoke*-<model>` were run before D-40 to find bugs.
  They are in no analysis.
- **Logs.**
  - `runs/d40-chain.log`: every phase, with its time;
  - `runs/d40-spend.log`: the spend guard;
  - `runs/d40-<model>.log`: each candidate's own log.
- **The analysis.** `scripts/d40_analysis.sh` writes `results/d40/`. Files
  ending `-keep-quotes` are the sensitivity analysis, not the registered one.
- **The flag reading.** `results/d40-flags/`: the rubric, the draw, the first
  and second readings, and the settled disagreements. `scripts/flag_tally.py`
  turns them into the criterion.

## How each step stays independent and reviewable

- **Its own file.** One step's output is never edited by another. Rerunning a
  step resumes it: rows already written stay.
- **Provenance on every row.** Each row records the code that wrote it
  (`code_version`, a commit), the model or judge (`model`, `judge_model`), and
  its identity (`task_id`, `run`, `pass`).
- **Nothing overwritten.** A run redone keeps the old one under a new name
  (`*-aborted-*`, `*.pre-scope2-0924` and so on), and the research log says
  why.
- **Each step is checked on its own.** `checks/guards_hold.py` has one section
  per bug fixed, and each was seen to fail with its fix removed.

## Copies

- **D-40's finished runs are on both machines.** The four fully graded
  candidates, `d40-soltests`, `d40-base` and the set-aside runs are on the VPS
  and the laptop: 184 files, identical by SHA-256 (09-25 05:3x UTC).
  grok-4.6 and Kimi-K2.7-Code will be copied and checked the same way when
  they finish.
- **The older runs** are on the laptop; the VPS has some of them.
- **No off-machine copy** of `runs/` exists yet beyond these two machines.
