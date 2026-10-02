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

**Run these tasks with the code at tag `v1.0.4`:** `git clone --branch v1.0.4
https://github.com/zanwenfu/errata-bench.git`. The code on `main` is ahead,
for the next version of the tasks, and does not run these.

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
| `harbor/<task>/` | the task as a [Harbor](https://github.com/harbor-framework/harbor) task: `instruction.md`, `task.toml` (limits and network rule), `environment/` (the image: the repository at the moment of the task, with its history, dependencies installed at build), `tests/` (a verifier that records the answer, every call and what changed; it grades nothing) |
| `harbor/digests.json` | each task's content digest, as Harbor records it for every trial: grading says a trial is official only when its task is the published one |
| `tasks/<task>/` | what grading reads: the whole conversation, the turns it is rendered from, the reference answers, the controls, the task row |
| `admission/gpt-6-astra/` | the official judge's check on each task: whether it reads the task's known-wrong and known-right answers correctly, and whether its readings of three fixed control answers behave, and the second grader's too. A task that fails either is left out of the official score |
| `manifest.json` | how each task was frozen |
| `SHA256SUMS` | every file's digest |

While the agent works, its container reaches model APIs and nothing else: the
main providers' (Anthropic, OpenAI, Google, xAI, DeepSeek, Mistral, Kimi, GLM,
MiniMax, Qwen, Groq, Together, Fireworks, Cerebras, NVIDIA, OpenRouter,
Vercel's AI Gateway, Azure, AWS Bedrock), as each `task.toml` lists them.

The 55 tasks: Go, TypeScript, JavaScript, Python, Shell and Astro repositories;
defects the agent introduced, defects already present, and mistakes in how the
agent worked. 17 conversations are too long to hand to an agent as one
instruction under Harbor (Linux caps one argument at 128 KiB); those are shown
with every message whole and long tool outputs cut to fit, marked; the whole
conversation is in the container, and the graders read it whole.

## Known issues

Running the tasks is not affected. What is known to be wrong or unproven is
listed, each item with its issue, in
[known-issues.md](https://github.com/zanwenfu/errata-bench/blob/main/docs/known-issues.md).
The most important:
- **High priority:** the task conversations omit most of what the earlier
  agent wrote between its tool calls: 577 of its 630 messages there (92%),
  about 1.5% of the text ([#17](https://github.com/zanwenfu/errata-bench/issues/17)).
  Three more defects in what the tasks show are fixed in the code since: the
  leak screen read less of each conversation than candidates see (G-81), the
  graders could take the earlier agent's words as evidence (G-82), and in 5
  tasks a repair removed the developer's request (G-83). A rebuilt version of
  the tasks, v1.1, is in progress.
- No person has yet checked the tasks or the grades
  ([#3](https://github.com/zanwenfu/errata-bench/issues/3)), and the headline
  judge passed its registered check narrowly (92% against 90%). Each task's
coverage is in `results/v1-coverage.md` there.

## Versions

The current release is the code at tag `v1.0.4` with these tasks at v1.0.2:
the code had two releases after the tasks last changed. Download a version by
its tag (`hf download ... --revision v1.0.2`). Grade a trial with the version
it ran: v1.0.2's tasks are v1.0.1's, digests and all.

- **v1.0.4** and **v1.0.3** (code only, 28 September): grading and the
  reference agent made ready for the first official run. The tasks are
  v1.0.2's. Release notes:
  <https://github.com/zanwenfu/errata-bench/releases/tag/v1.0.4>.
- **v1.0.2**: each task's defect labelled with what its check established,
  and two defects the check had missed found (`tasks/*/grading/task.json`).
  Nothing an agent sees changed.
- **v1.0.1**: the model APIs an agent may reach widened from five providers
  to the main ones above, so an agent on another provider runs officially.
  Nothing else in the tasks changed.
- **v1.0**: the first release.

## Licences

- **Each task's repository** keeps its own licence, in its working copy: 45
  MIT, 3 GPL-3.0, 3 AGPL-3.0, 2 Apache-2.0, 2 ISC.
- **The conversations** come from SWE-chat and are used under its terms.
- **Everything errata-bench wrote** -- instructions, task configuration, the
  verifier, grading data -- is under Apache-2.0, as is its code. Copyright
  2026 Zanwen Fu.

## Removal requests

If you are the developer in one of these sessions, or own one of these
repositories, and want a task removed, open an issue at
<https://github.com/zanwenfu/errata-bench/issues>. Requests SWE-chat honours
are honoured here too.

## Citation

errata-bench is by Zanwen Fu. Cite it, and SWE-chat, which the tasks are built
from:

```bibtex
@software{fu2026erratabench,
  author  = {Fu, Zanwen},
  title   = {errata-bench: Does a Coding Agent Tell the Truth About Its Own Work?},
  year    = {2026},
  version = {1.0.4},
  url     = {https://github.com/zanwenfu/errata-bench}
}
```
