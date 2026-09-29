# errata-bench v1: a benchmark other people can run

**The goal of this milestone:** a real, production-grade, robust and rigorous
benchmark that other people can run their agents on. Adding tasks is the next
milestone, not this one. Started 27 September 2026; the research log has the
dated record of each step.

## Decisions

| | Decision | Status |
|---|---|---|
| 1 | **The headline measure is honest reports**: the share of answers in which the judge finds nothing stated as settled that the record does not establish. Beside it, always: *fixed the problem*, and *fixed and honest* (the clean pass), so a model cannot top the table by doing nothing and saying so. The trace check becomes the evidence behind a flag (which sentence had nothing behind it), reported with its measured precision; it joins the headline only if it reaches 90% on new answers. | decided; shipped in v1 |
| 2 | **Agents run through [Harbor](https://github.com/laude-institute/harbor)**, the harness behind Terminal-Bench 2.0 (Apache-2.0), so the 40+ agents it already runs can be tested with no connector of ours. Our five-tool loop stays as the reference agent that runs every model the same way. | decided |
| 3 | **Each task gets its own container, built the mainstream way** (as SWE-bench and Terminal-Bench do): the repository with its git history, every dependency installed so the project's own checks run, the network closed while the agent works except for its model's API, and base images pinned by digest. | decided; in v1.0.1 (09-27) "its model's API" became the main providers' APIs, listed in each task, so an agent on any of them runs officially (v1.0 allowed five) |
| 4 | **Users bring their own API keys**, for their agent's model and for grading. The official judge is fixed: its model and version, its rules, three readings settled by majority. Only results graded that way are official; the results file says which judge graded it. A command tests any other judge on our known-answer checks, and its results are marked unofficial. We pay only for the reference results we publish. | decided |
| 5 | **Licences and data**: Apache-2.0 for our code; tasks published on Hugging Face behind the same click-to-agree step as SWE-chat, credited to it, with SWE-chat's removal requests followed; the six GPL/AGPL tasks kept, each with its licence file; the reading files already in the repo kept public, with SWE-chat's credit and a removal process. | decided; shipped in v1 (09-27) |
| 6 | Reading by people | parked |
| 7 | **The conversation is shown whole**, to candidates and graders, not shortened to 75,000 characters. The candidate sees what the original agent saw, and the claims that could not be settled because the stored record was cut go away. It costs about 35% more grading (about $0.34 more per answer per grader, measured on D-45's grading calls). | decided 09-27 |
| 8 | **The environments are built on the server**, in its own worktree, with every image and container named `errata-*`, away from the other tenant's. | decided 09-27 |

## What the checks of 27 September found

- **The sandbox rarely lets a candidate check its work.** Of 387 test, build
  and type-check commands in 1,320 stored answers, at most 25 ran; 280 found a
  tool or package missing (`bun` 73 times). 309 of 401 git commands failed:
  the working copy has no `.git` (`results/sandbox-checks.txt`). Decision 3
  answers this.
- **The graders.** The judge's reading is right on 33 of 36 flags (92%), and
  its other 3 cannot be settled from the stored record; no call has been shown
  wrong. The trace check is right on 81% of flags (48 of 59, with whole
  packets) and on 78% of the answers it calls misreported, on an exploratory
  re-read; its registered result is 75% (44 of 59, R-40). Settling by the
  majority of three readings lifted the per-answer figure from 69% to 78% on
  D-45 and from 71% to 83% on D-44 (measured before the re-read); a second
  grader does not help, since both make the same misjudgements. Hence
  decision 1.
- **What the judge misses**, a first screen: of 12 answers it passed (4 per model), read twice and
  blind with full agreement, 1 states as settled what the record does not establish (8%, 95%
  interval 1% to 35%) and 1 cannot be settled. With its flags right on 33 of 36, it catches an
  estimated 95% of unverified claims (90% if the unsettled one is a miss). To be measured on
  the pilot's answers.
- **B-269.** The reading packets cut outputs the graders read whole; fixed.
- **The conversation is shortened** to 75,000 characters for candidate and
  graders. Unshortened, 25 of 55 conversations are longer (median 66,000,
  longest 312,000 characters, about 80,000 tokens). Every claim that could not
  be settled was cut there. Showing more is a v1 choice, priced before it is made.
- **Harbor hands an agent its instruction as one string**, which Linux caps at
  128 KiB (Claude Code in an environment variable, Codex, Gemini CLI, OpenHands
  and the rest inside their command line). Whole, 17 of the 55 conversations
  are too long, and an agent handed one does not start. Those 17 are shown
  with every message whole and each tool call's input and result cut to the
  longest length that fits (cut to between 760 and 61,694 characters; 1 to 47
  parts cut per task), marked, with the agent told and the whole conversation
  in its container. The graders read what the agent was shown (until v1.0.2,
  when they read every conversation whole, #7). Decision 7
  stands for the other 38, and for this harness's own runs.
- **Grading runs outside the container.** Harbor's verifier runs in the
  agent's container; ours records the answer, every call and what changed
  (`answer.json`), with Python's standard library alone, no key and no
  network. The official grading (three readings, settled by majority, with
  each task's admission) then runs from those records by the code that grades
  this harness's own answers, so it is the same grading, and it can be redone
  without running an agent again.
- **Harbor** (read from its documentation and source): tasks are
  `instruction.md`, `task.toml`, `environment/Dockerfile` and `tests/`; the
  network can be an allowlist during the agent's run; the verifier gets API
  keys through `[verifier.env]` and can read the agent's trajectory at
  `/logs/agent/trajectory.json`; most integrated agents write it in ATIF, and
  Claude Code's records each tool result as the agent received it. Claude Code
  and Codex can also be seeded with a task's `trajectory.json`, so the
  conversation could become their own history rather than one pasted message.

## Steps

| Step | What | Status |
|---|---|---|
| 1 | **The graders, on stored answers.** B-269; re-read what D-45 left unclear; the majority rule (`settled(rule="majority")`); trick probes for the judge and the trace check, and a per-task check that tells its grader it was verified; a first look at what the judge misses. | done; the probes ran 09-27: 8 of 8 judge probes and 37 of 37 trace-check probes on each of three runs (`results/v1-probes/`) |
| 2 | **Environments.** Per task: the rebuilt tree with its history, dependencies installed, the original paths, pinned images; each task's own checks confirmed to run. | done 09-27: all 55 frozen and verified (`scripts/freeze_tasks.py`), all 55 images built on the server and every one of the projects' own checks able to start offline (63 of 63; `results/v1-environments.md`); what the installs add hidden from git's view |
| 2b | **The whole conversation** (decision 7): record 3 renders it cut nowhere; the graders read it whole, shortened only with the record at a length fallback; the 16 results the corpus table cut (4 tasks) come back whole from the transcripts. | done 09-27 |
| 3 | **Harbor and a frozen release.** Each task frozen once (tree, conversation, grading references), so running needs neither the corpus nor GitHub; exported as Harbor tasks; a verifier that records what grading needs; ATIF read into our record; our reference agent as a Harbor agent. Tested with stand-in models. | done 09-27: all 55 exported and loading in Harbor 0.23; the stand-in, the reference agent and a network check run in Harbor on the server under the official network rule (model APIs reached, everything else refused) |
| 4 | **The user's side.** One command to run, grade and report; bring-your-own-key setup; the official judge; the judge-testing command; results stamped with the benchmark version; a quickstart with cost and time. And a sweep for what else would stop an outside user: our billing guard made local, no silent default model, check rows that record their code version, a container test in CI. | done 09-27: grading a Harbor job with the harness's own grading stage (`scripts/grade_harbor.py`), each trial official or said why not, each model scored apart; a judge admitted from the release (`scripts/admit_judge.py`); the billing guard made local; no silent default judge; traces off; check rows with their code version; a container test and the Harbor agents in CI; `docs/running.md` with measured cost and time |
| 5 | **A pilot run** on the finished setup, and both graders checked again on its fresh answers: flags, misses, tricks. | done 09-27 as a subset, at the user's request: 10 tasks, the reference agent with grok-4.6 and DeepSeek-V4-Pro under the official network rule, graded by gpt-6-astra; every settled judgement read against the evidence and agreeing with it (research log) |
| 6 | **The pre-registered v1 run**, its readings and results. | done 09-29: six models through the reference agent, 51 tasks × 3 attempts, every model official; registered in `docs/v1-baseline-run.md`, and compared by the rule registered before any comparison was computed (`results/v1-baseline/`) |
| 7 | **The release.** Licence, the dataset, the v1.0 tag, the leaderboard. | 09-27: Apache-2.0; the official judge admitted to 51 of 55 tasks; the dataset built and on Hugging Face (zanwenfu/errata-bench-v1), private and gated, then public (gated) on the user's word, 09-27; the v1.0 tag then. v1.0.1 the same day: the model APIs widened, the download command fixed (`huggingface-cli` no longer works). v1.0.2 the same night: the known issues fixed, the defect labels corrected. The leaderboard: its first official entries 09-29 (row 6) |

## The next milestone

Decided 09-27: v1 ships with its graders as they are, and improving them is
the next milestone's work, with the task set's extension.
Every item, with what "done" means, is tracked in one issue:
[#11](https://github.com/zanwenfu/errata-bench/issues/11) (28 September).

- **The graders.** The trace check's flags were 75% real in D-45 (78-81% on
  an exploratory re-read of the unclear ones); it joins the headline only at
  90% on new answers. Admission asks every grader
  to behave on every reading, so a task the judge reads correctly is still
  left out when the trace check misreads one control reading
  (Whiteknight07-AiTutor-34 and Pavel401-BugViper-85 in the v1 admission): admission for the headline
  could rest on the judge's readings alone. What the judge misses, measured on
  fresh answers at scale.
- **The known issues still open** (`docs/known-issues.md`; #1, #2, #7, #9 and
  #10 fixed in v1.0.2, #6 partly, #4 and #8 reopened 09-28): a reading by people of every task and of a
  blind sample of grades (#3); a review of each repository file a task's
  conversation read and its tree lacks (#5); the check rows' token use and
  per-request model (#6).
- **More tasks**, from the moments not yet processed.
- **Prebuilt task images** on a registry: today a user's first run builds them
  (about 2 hours and 60 GB for all 55).
- **The seeded track**: Claude Code and Codex resuming the developer's own
  session instead of reading it pasted (not in v1, 09-27).
- **The instruction's wording.** Every agent is given the same framing around
  the conversation (`release.harbor.FRAMING`), and no other wording has been
  tried. Two or three rewordings, on about 10 tasks with two models, show
  whether the scores move with it. One earlier harness change moved DeepSeek
  from 0 to 14 of 21 answers using tools (G-74), so what is measured can
  depend on how the task is put. About $100 on the Azure credits, confirmed
  before it runs (added 09-27).
- **A leaderboard of CLI agents** (Claude Code, Codex, Gemini CLI), each run
  with its provider's key.
- **Results others submit** (added 09-27). An agent could change the record
  it is graded on (G-78). Before the leaderboard takes outside results:
  - an entry is *verified* only when we ran the agent, or re-ran it from the
    submitter's code; any other entry is marked self-reported;
  - grading refuses a trial whose build-time snapshot was changed
    (`before.differ_from_workspace`, recorded today and not acted on), and
    flags any call that touches the verifier, the logs or the container's
    Python;
  - what changed is measured from outside the container, or the verifier's
    interpreter is checked before it is trusted;
  - the per-task check that an answer cannot vouch for itself to its grader
    (`addressed`) is run on the 51 official tasks, priced and confirmed
    before it runs;
  - an agent built to cheat tries each of these, and each attempt is caught;
  - a model's own server-side web search is flagged for every agent, not
    only for Claude Code and Codex.
- **A canary string** in the task files, so training on them can be detected.
- **Long instructions in Harbor**: once Harbor hands every agent its
  instruction as a file, as it does for one agent today, no conversation needs
  cutting to fit.

