# errata-bench v1: a benchmark other people can run

**The goal of this milestone:** a real, production-grade, robust and rigorous
benchmark that other people can run their agents on. Adding tasks is the next
milestone, not this one. Started 27 September 2026; the research log has the
dated record of each step.

## Decisions

| | Decision | Status |
|---|---|---|
| 1 | **The headline measure is honest reports**: the share of answers in which the judge finds nothing stated as settled that the record does not establish. Beside it, always: *fixed the problem*, and *fixed and honest* (the clean pass), so a model cannot top the table by doing nothing and saying so. The trace check becomes the evidence behind a flag (which sentence had nothing behind it), reported with its measured precision; it joins the headline only if it reaches 90% on new answers. | recommended; to be confirmed |
| 2 | **Environments.** Per task: the rebuilt tree with its history, dependencies installed, the original paths, pinned images; each task's own checks confirmed to run. | done 09-27: all 55 frozen and verified (`scripts/freeze_tasks.py`), all 55 images built on the server and every one of the projects' own checks able to start offline (63 of 63; `results/v1-environments.md`); what the installs add hidden from git's view |
| 3 | **Each task gets its own container, built the mainstream way** (as SWE-bench and Terminal-Bench do): the repository with its git history, every dependency installed so the project's own checks run, the network closed while the agent works except for its model's API, and base images pinned by digest. | decided |
| 4 | **Users bring their own API keys**, for their agent's model and for grading. The official judge is fixed: its model and version, its rules, three readings settled by majority. Only results graded that way are official; the results file says which judge graded it. A command tests any other judge on our known-answer checks, and its results are marked unofficial. We pay only for the reference results we publish. | decided |
| 5 | **Licences and data**: Apache-2.0 for our code; tasks published on Hugging Face behind the same click-to-agree step as SWE-chat, credited to it, with SWE-chat's removal requests followed; the six GPL/AGPL tasks kept, each with its licence file; the reading files already in the repo kept public, with SWE-chat's credit and a removal process. | recommended; to be confirmed at the release step |
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
  packets) and on 78% of the answers it calls misreported. Settling by the
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
  longest length that fits (cut to between 732 and 61,864 characters; 1 to 47
  parts cut per task), marked, with the agent told and the whole conversation
  in its container. The graders read what the agent was shown. Decision 7
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
| 1 | **The graders, on stored answers.** B-269; re-read what D-45 left unclear; the majority rule (`settled(rule="majority")`); trick probes for the judge and the trace check, and a per-task check that tells its grader it was verified; a first look at what the judge misses. | done; the probes' run approved 09-27 and under way |
| 2 | **Environments.** Per task: the rebuilt tree with its history, dependencies installed, the original paths, pinned images; each task's own checks confirmed to run. | in progress: all 55 tasks frozen and checked (`scripts/freeze_tasks.py`, 685 MB, outside git); the images need Docker, not running on the laptop |
| 2b | **The whole conversation** (decision 7): record 3 renders it cut nowhere; the graders read it whole, shortened only with the record at a length fallback; the 16 results the corpus table cut (4 tasks) come back whole from the transcripts. | done 09-27 |
| 3 | **Harbor and a frozen release.** Each task frozen once (tree, conversation, grading references), so running needs neither the corpus nor GitHub; exported as Harbor tasks; a verifier that records what grading needs; ATIF read into our record; our reference agent as a Harbor agent. Tested with stand-in models. | in progress: the frozen release, the ATIF reader, the export (`scripts/export_harbor.py`, all 55 load in Harbor 0.23) and the verifier (run end to end on a real task, without Docker) are done, and run in Harbor on the server with a stand-in agent (`errata_harbor.agents:StandIn`) and Harbor's `nop`, the network open; and the reference agent (`errata_harbor.agents:Reference`, the harness's own loop installed in the task's container) run there with a stand-in model; next the official network settings, which need two small images on the server (your OK) |
| 4 | **The user's side.** One command to run, grade and report; bring-your-own-key setup; the official judge; the judge-testing command; results stamped with the benchmark version; a quickstart with cost and time. And a sweep for what else would stop an outside user: our billing guard made local, no silent default model, check rows that record their code version, a container test in CI. | in progress: grading a Harbor job with the harness's own grading stage (`scripts/grade_harbor.py`), each trial official or said why not, results stamped (`release.report`); check rows record their code version; the billing guard made local (Claude refused only bound for Azure); grading refuses without a named judge; a judge admitted from the release (`scripts/admit_judge.py`) |
| 5 | **A pilot run** on the finished setup, and both graders checked again on its fresh answers: flags, misses, tricks. | paid: a cost estimate and your OK first |
| 6 | **The pre-registered v1 run**, its readings and results. | paid: a cost estimate and your OK first |
| 7 | **The release.** Licence, the dataset, the v1.0 tag, the leaderboard. | your OK on decision 5 first |
