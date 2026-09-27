# errata-bench v1: a benchmark other people can run

**The goal of this milestone:** a real, production-grade, robust and rigorous
benchmark that other people can run their agents on. Adding tasks is the next
milestone, not this one. Started 27 September 2026; the research log has the
dated record of each step.

## Decisions

| | Decision | Status |
|---|---|---|
| 1 | **The headline measure is honest reports**: the share of answers in which the judge finds nothing stated as settled that the record does not establish. Beside it, always: *fixed the problem*, and *fixed and honest* (the clean pass), so a model cannot top the table by doing nothing and saying so. The trace check becomes the evidence behind a flag (which sentence had nothing behind it), reported with its measured precision; it joins the headline only if it reaches 90% on new answers. | recommended; to be confirmed |
| 2 | **Agents run through [Harbor](https://github.com/laude-institute/harbor)**, the harness behind Terminal-Bench 2.0 (Apache-2.0), so the 40+ agents it already runs can be tested with no connector of ours. Our five-tool loop stays as the reference agent that runs every model the same way. | decided |
| 3 | **Each task gets its own container, built the mainstream way** (as SWE-bench and Terminal-Bench do): the repository with its git history, every dependency installed so the project's own checks run, the network closed while the agent works except for its model's API, and base images pinned by digest. | decided |
| 4 | **Users bring their own API keys**, for their agent's model and for grading. The official judge is fixed: its model and version, its rules, three readings settled by majority. Only results graded that way are official; the results file says which judge graded it. A command tests any other judge on our known-answer checks, and its results are marked unofficial. We pay only for the reference results we publish. | decided |
| 5 | **Licences and data**: Apache-2.0 for our code; tasks published on Hugging Face behind the same click-to-agree step as SWE-chat, credited to it, with SWE-chat's removal requests followed; the six GPL/AGPL tasks kept, each with its licence file; the reading files already in the repo kept public, with SWE-chat's credit and a removal process. | recommended; to be confirmed at the release step |
| 6 | Reading by people | parked |

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
| 1 | **The graders, on stored answers.** B-269; re-read what D-45 left unclear; the majority rule (`settled(rule="majority")`); trick probes for the judge and the trace check, and a per-task check that tells its grader it was verified; a first look at what the judge misses. | done, except the probes' paid run (under $10, Azure credits), waiting for your OK |
| 2 | **Environments.** Per task: the rebuilt tree with its history, dependencies installed, the original paths, pinned images; each task's own checks confirmed to run. | needs Docker (not running on the laptop) |
| 3 | **Harbor and a frozen release.** Each task frozen once (tree, conversation, grading references), so running needs neither the corpus nor GitHub; exported as Harbor tasks; the grader as the verifier; ATIF read into our record; our reference agent as a Harbor agent. Tested with stand-in models. | not started |
| 4 | **The user's side.** One command to run, grade and report; bring-your-own-key setup; the official judge; the judge-testing command; results stamped with the benchmark version; a quickstart with cost and time. And a sweep for what else would stop an outside user: our billing guard made local, no silent default model, check rows that record their code version, a container test in CI. | not started |
| 5 | **A pilot run** on the finished setup, and both graders checked again on its fresh answers: flags, misses, tricks. | paid: a cost estimate and your OK first |
| 6 | **The pre-registered v1 run**, its readings and results. | paid: a cost estimate and your OK first |
| 7 | **The release.** Licence, the dataset, the v1.0 tag, the leaderboard. | your OK on decision 5 first |
