---
pretty_name: errata-bench v1
license: other
license_name: errata-bench-v1
license_link: https://github.com/zanwenfu/errata-bench/blob/main/LICENSE
language:
  - en
tags:
  - code
  - agents
  - coding-agents
  - honesty
  - evaluation
  - benchmark
size_categories:
  - n<1K
extra_gated_heading: "Access errata-bench v1"
extra_gated_prompt: >-
  errata-bench's tasks are built from SWE-chat (SALT-NLP/SWE-chat), real
  sessions between developers and coding agents, and each contains one such
  conversation and the repository it was about. By requesting access you agree
  to SWE-chat's terms of use, to use the data only to evaluate and study coding
  agents, not to train models on it, and not to try to identify the developers
  in it. Each task's repository keeps its own licence.
extra_gated_fields:
  I agree to SWE-chat's terms of use and to use this data only for evaluation and research: checkbox
---

# errata-bench v1

**Does a coding agent tell the truth about its own work?**

A coding agent ends its work with a report: *"I fixed the bug, ran the tests,
and they all pass."* errata-bench measures whether such reports are true. Each
of its 55 tasks is a moment from a real session between a developer and a
coding agent, taken from [SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
where the developer pushed back on the agent's work. The agent under test is
put in the original agent's place, with the conversation up to that moment, the
repository as it stood (its history and dependencies installed), and its own
tools; it works, and reports. errata-bench then reads the report against
everything the agent did.

Code, instructions and results: <https://github.com/zanwenfu/errata-bench>.
How to run an agent and grade it: `docs/running.md` there.

## What is measured

- **Honest reports** (the headline): the share of answers in which the judge
  finds nothing stated as settled that the record does not establish.
- **Fixed**, and **fixed and honest**, beside it, so a model cannot rank high
  by doing nothing and saying so.
- **No answer**: attempts that ended without one.
- **Misreported**: a second grader's reading, a diagnostic with its measured
  precision, not the headline.

Each per task and then over tasks, with a 95% interval. The official judge is
gpt-6-astra, three readings per answer, settled by majority; you grade with
your own key.

## What is inside

| path | what |
|---|---|
| `harbor/<task>/` | the task as a [Harbor](https://github.com/laude-institute/harbor) task: `instruction.md`, `task.toml` (limits and network rule), `environment/` (the image: the repository at the moment of the task, with its history, dependencies installed at build), `tests/` (a verifier that records the answer, every call and what changed; it grades nothing) |
| `harbor/digests.json` | each task's content digest, as Harbor records it for every trial: grading says a trial is official only when its task is the published one |
| `tasks/<task>/` | what grading reads: the conversation as shown, the turns it is rendered from, the reference answers, the controls, the task row |
| `admission/gpt-6-astra/` | the official judge's check on each task: whether it reads the task's known-wrong and known-right answers correctly, and whether its readings of three fixed control answers behave. A task it fails is left out of its official score |
| `manifest.json` | how each task was frozen |
| `SHA256SUMS` | every file's digest |

The 55 tasks: Go, TypeScript, JavaScript, Python, Shell and Astro repositories;
defects the agent introduced, defects already present, and mistakes in how the
agent worked. 17 conversations are too long to hand to an agent as one
instruction under Harbor (Linux caps one argument at 128 KiB); those are shown
with every message whole and long tool outputs cut to fit, marked, and the
whole conversation is in the container.

## Licences

- **Each task's repository** keeps its own licence, in its working copy: 45
  MIT, 3 GPL-3.0, 3 AGPL-3.0, 2 Apache-2.0, 2 ISC.
- **The conversations** come from SWE-chat and are used under its terms.
- **Everything errata-bench wrote** -- instructions, task configuration, the
  verifier, grading data -- is under Apache-2.0, as is its code.

## Removal requests

If you are the developer in one of these sessions, or own one of these
repositories, and want a task removed, open an issue at
<https://github.com/zanwenfu/errata-bench/issues>. Requests SWE-chat honours
are honoured here too.

## Citation

Cite SWE-chat, which the tasks are built from, and errata-bench
(<https://github.com/zanwenfu/errata-bench>).
