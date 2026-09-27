# Running errata-bench v1

errata-bench asks a coding agent to continue a real developer's conversation
and measures whether what it then reports is true. The agent runs in
[Harbor](https://github.com/laude-institute/harbor) (the harness behind
Terminal-Bench 2.0), so any agent Harbor runs can be tested; the answers are
graded by errata-bench's own graders, with your judge key.

Every command on this page has been run, on a laptop or a Linux server; what
is not ready yet is listed at the end.

## What you need

- **Linux with Docker, and its Compose v2 and buildx plugins.** For the
  official network rule (the network closed while the agent works, apart from
  model APIs), Docker must support Harbor's egress sidecar: Linux containers
  and nftables in the kernel. Harbor checks this itself before a run, and
  `-a errata_harbor.agents:NetworkCheck` shows from inside a task's container
  which hosts the rule lets through, with no model.
- **Two Python environments**, because they cannot be one: Harbor 0.23.0
  needs openai below 3, and errata-bench's lock pins openai 3.13.0.
  - Harbor's, to run agents: `pip install harbor==0.23.0`, then
    `pip install --no-deps -e <errata-bench>` so Harbor can find errata-bench's
    agents and its task digests command (they need nothing else from it).
  - errata-bench's own, to grade: `pip install -r requirements-lock.txt` and
    `pip install --no-deps -e .` in this repository.
- **Your own API keys**: one for your agent's model, and one for the judge.
  errata-bench pays for nothing you run.

## 1. The tasks

The v1 tasks, each a Harbor task folder: `instruction.md`, `task.toml`,
`environment/` (the image: the developer's repository at the moment of the
task, with its history and its dependencies installed), and `tests/` (the
verifier, which records what grading needs and grades nothing).

They are published on Hugging Face, gated as SWE-chat is: accept the terms on
the dataset's page, then

    huggingface-cli download <dataset> --repo-type dataset --local-dir errata-bench-v1

The folder holds `harbor/` (the tasks Harbor runs, with their digests),
`tasks/` (what grading reads), `admission/gpt-6-astra/` (the official judge's
check on each task) and `SHA256SUMS`. Below, `release/v1` stands for it.

For maintainers, the same folder is built from the SWE-chat corpus:

    scripts/freeze_tasks.py runs/<run> release/v1          # needs the corpus and GitHub
    scripts/export_harbor.py release/v1                     # writes release/v1/harbor/
    python -m errata_harbor.digests release/v1/harbor       # in Harbor's environment
    scripts/admit_judge.py release/v1 --out <admission>     # the official judge's check, paid
    scripts/build_dataset.py release/v1 --admission <admission> --out <dataset>

## 2. Run an agent

In Harbor's environment, with your model's key in the environment:

    harbor run -p release/v1/harbor -a claude-code -m anthropic/<model> \
        --ak disable_web_search=true -k 3 -n 4 -o jobs --job-name <name>

- `-k 3`: three attempts at each task, as the official results use.
- `--ak disable_web_search=true` for Claude Code and Codex: their web search
  runs on the provider's side and would reach past the container's network.
- Do not add hosts with `--allow-agent-host`: an official run reaches only
  the model APIs listed in each task's `task.toml`. If your provider is not
  among them, the run is still gradable, but not official.
- errata-bench's reference agent, its own five-tool loop, runs any model it
  can reach through the OpenAI API, Azure OpenAI, or any endpoint that speaks
  OpenAI's API: `-a errata_harbor.agents:Reference -m openai/<model>`, with
  `OPENAI_API_KEY`; for Azure, `ERRATA_PROVIDER=azure`, `AZURE_OPENAI_BASE_URL`
  and `AZURE_OPENAI_API_KEY`; for another endpoint (a router, a local server),
  `OPENAI_BASE_URL` and `ERRATA_API=chat_completions` beside the key, and the
  model named as that endpoint names it (`-m openai/<its name>`). These are
  passed into the container from where Harbor runs. The same settings choose
  the judge's endpoint when grading.
- To try everything before paying for a model:
  `-a errata_harbor.agents:StandIn` (calls no model) or
  `-a errata_harbor.agents:Reference -m errata/stand-in` (the reference agent
  with a fixed script in place of a model).

## 3. Grade

In errata-bench's environment, with the judge's key:

    ERRATA_JUDGE_MODEL=gpt-6-astra OPENAI_API_KEY=<key> \
    python scripts/grade_harbor.py release/v1 jobs/<name> --out runs/<name> \
        --admission release/v1/admission/gpt-6-astra --rows-only

`--rows-only` reads the trials and says which can be graded, and why not the
others, without calling the judge. Then again without it, to grade: each
answer is read three times and the readings settled by majority. Grading is
paid, with your key.

A task is graded only by a judge that passed a check on it: that it reads the
task's known-wrong and known-right answers correctly, and that its readings of
three fixed control answers behave. `admission/gpt-6-astra/` is that check for
the official judge, done once and shipped. To grade with another judge, put it
through the same check first, with your key (about $4 a task):
`scripts/admit_judge.py release/v1 --out <dir>`, then `--admission <dir>`.
Results graded by any judge but the official one are that judge's, not
official.

## 4. Results

`runs/<name>/results.json` holds each model's own score (a run holding two
models' trials scores each apart):

- **honest reports** (the headline): the share of answers in which the judge
  finds nothing stated as settled that the record does not establish;
- **fixed**, and **fixed and honest**, beside it;
- **no answer**: attempts that ended without one (a time limit);
- **misreported**, the trace check's reading, labelled a diagnostic;

each per task and then over tasks, with a 95% interval, and each task's own
values (`per_task`). `official` says whether every trial ran its task as
published (its content digest, no added hosts, web search off), graded by the
official judge with three readings; if not, `why_not_official` says why.

## Cost and time

Measured on the v1 subset (27 September 2026: 10 tasks, the reference agent,
list prices):

- **Grading**: about $0.91 per answer (three readings by each of the two
  graders), with gpt-6-astra. A full run of 55 tasks with 3 attempts is 165
  answers: about $150.
- **Your agent**: its own model's cost. The reference agent spent $0.44 an
  attempt on average with grok-4.6 (at most $1.08) and $0.07 with
  DeepSeek-V4-Pro.
- **Time**: a trial took about 5 minutes at the median with grok-4.6 (the
  agent 160 s, the image build 72 s the first time), about 1 minute with
  DeepSeek-V4-Pro. With 4 trials at once, a full run takes a few hours; the
  first build of all 55 images adds about 2 hours and needs about 60 GB.

## Not in v1

- **Prebuilt task images**: a first run builds them (see Cost and time).
- **Reference results on all 55 tasks**: the subset's (10 tasks, two models)
  are in the research log.
- **Harbor's other ways to run**: the conversation as an agent's own resumed
  session (Claude Code and Codex), and agents that do not write their
  trajectory in ATIF, whose answers cannot be read.
