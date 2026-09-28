# errata-bench v1.0.2: what it is, its limits, and what comes next

Written on 28 September 2026, after five independent reviews of v1.0.2. Each
item links to its issue, and everything still to do is tracked in one place:
[#11](https://github.com/zanwenfu/errata-bench/issues/11).

## In short

- **You can run it.** v1.0.2 runs any coding agent on 55 real tasks, in
  realistic containers with the network closed, and grades the result with
  your own key. This was checked end to end on a fresh download.
- **The headline score is usable, with stated limits.** It rests on a judge
  that passed its accuracy check narrowly, measured on an earlier setup.
- **The diagnostic beside it is not reliable yet.** It has a known flaw (#4)
  and has not passed its accuracy bar.
- **It is not yet a leaderboard.** There are no official results, no way to
  verify outside submissions, and no registered way to rank models.

## What v1.0.2 is

- **55 tasks.** Each is a moment from a real session between a developer and
  a coding agent (from SWE-chat), where the developer pushed back on the
  agent's work.
- **Each task gives the agent:**
  - the conversation up to that moment;
  - the repository as it was, with its git history and dependencies installed,
    at the path the developer had it;
  - a network that reaches only model APIs.

  The agent works, then replies to the developer.
- **What gets recorded.** A verifier records the reply, every command the
  agent ran with its output, and every file it changed. It grades nothing.
- **Grading happens later, outside the container**, by one model,
  gpt-6-astra, reading each answer three times, with the majority deciding.
  It reads in two ways:
  - **the judge** asks whether the reply states anything as settled that the
    record does not show, and whether the problem was fixed;
  - **the trace check** goes claim by claim, pointing to the command or output
    behind each one.
- **The measures:**
  - **honest reports** (the judge; the headline);
  - **fixed**, and **fixed and honest**, always beside it, so doing nothing
    cannot win;
  - **no answer**;
  - **misreported** (the trace check; a diagnostic).
- **51 of the 55 tasks count officially.** A task counts only after the
  grading model has read that task's known right and wrong answers, and three
  test answers, correctly.
  - The judge's readings failed on 2 tasks (entireio-cli-253,
    entireio-cli-38).
  - The trace check's readings failed on 2 more (Whiteknight07-AiTutor-34,
    Pavel401-BugViper-85).

## What has been verified

- **The release itself.** A fresh download of v1.0.2 matches its checksums
  (1,454 files), and every task's digest matches the published one.
- **Offline test suites** pass: 829 guard checks and 57 live bug checks, with
  CI green. Each of the 32 fixes made for v1.0.2 was undone one at a time, and
  each time a check failed.
- **Every published figure reproduces from the stored data:**
  - the validation results;
  - agreement;
  - the coverage report;
  - the storage loss in D-44.

  The one exception is the cut-citation audit (#4).
- **The containers.**
  - Each holds only the starting commit and its history, so no later fix is
    in reach.
  - The task's defect, reference answers and grading instructions are never
    in the container.
  - On 22 real trials, no tampering was detected.

## Limits of the score

These do not stop the benchmark from running. They limit what its numbers can
be taken to mean.

1. **The judge's validation is narrow.**
   - 33 of 36 of its calls were right (92%, against 90% required), and that
     pass rests on one adjudication (89% without it).
   - It was measured on conversations cut to 75,000 characters; v1 shows them
     whole.
   - What it misses has been screened on only 12 answers.
2. **The trace check is a diagnostic, not validated.**
   - 75% of its flags were real (44 of 59, against 90%).
   - Two grading models, gpt-6-astra and gpt-6-sol, agree on its reading at
     κ 0.59 (against 0.6). On the judge's reading they agree at κ 0.78.
   - It also has a known flaw in how it excuses claims resting on cut output
     ([#4](https://github.com/zanwenfu/errata-bench/issues/4)).
3. **No person has checked the tasks or the grades yet**
   ([#3](https://github.com/zanwenfu/errata-bench/issues/3)).
   - Every reading behind the figures above was made by Claude models under a
     written rubric.
   - Four official tasks' defects name nothing a check could look for
     ([#8](https://github.com/zanwenfu/errata-bench/issues/8)):
     entireio-cli-283, femto-mcp-chrome-58, hutusi-amytis-15 and
     hutusi-amytis-349.
4. **The graders share a maker, and the tasks favour one.**
   - Both grading models are OpenAI's, so their agreement is a weaker check
     than agreement across makers, and OpenAI-family agents cannot yet be
     graded independently.
   - Every task comes from a moment where a Claude model failed, so the tasks
     may be harder for Claude agents.
5. **The rules were tuned on these same tasks.**
   - The graders' rules were revised after reading answers to these 55 tasks.
   - No held-out tasks exist yet.
6. **The rebuilt world is incomplete** ([#5](https://github.com/zanwenfu/errata-bench/issues/5)).
   - Only file edits are replayed, not other commands' effects.
   - On 16 tasks, nothing the conversation read could be compared with the
     rebuilt tree. Of the 147 files that were compared, none differs.
   - 17 files the conversation read are missing. Per task:
     `results/v1-coverage.md`.
7. **Too few answers to rank models yet.**
   - The only v1 results are a pipeline check: 2 models on 10 tasks, one
     attempt each.
   - With 51 tasks, only large differences between models can be told apart,
     and one repository (entireio/cli) supplies 16 of the 55 tasks.
8. **Contamination.** SWE-chat has been public since April 2026, and there is
   not yet a canary string to detect training on the tasks.

## Known defects in v1.0.2

- **Cut citations (#4, reopened).** The trace check still excuses some claims
  it should not, and counts some against the agent that it should not. This
  affects `misreported` only.
- **The `v1.0.2` code tag writes an outdated label** into `results.json`: the
  exploratory 78% for the trace check. The fix is on `main`; a `v1.0.3` tag
  is to follow.
- **`results.json` says dataset version "1.0.1"** when grading v1.0.2. The
  tasks are the same, but the release is not named.
- **Small gaps in the tests:**
  - no check covers the rows grading writes for a long task;
  - the note for a judge served by more than one model is printed, not
    saved, and can fire falsely;
  - a refused grading run still creates its output folder.

## What v1.0.2 is not yet

- **A leaderboard.** There are no official entries. The first will be
  baseline runs of standard agents, run by us.
- **Safe for outside submissions.** An agent's own software writes the
  command log the judge trusts, and the verifier runs inside the agent's
  container. A submitter could therefore forge the record (G-78 in the
  research log). Until that is closed, only runs we execute ourselves can be
  called verified.
- **A way to rank models.** No comparison method, tie rule or completeness
  rule is registered yet.

## Fixed in v1.0.2

- **The judge graded long tasks on a different view than it was admitted
  on** ([#7](https://github.com/zanwenfu/errata-bench/issues/7)). The graders
  now read every conversation whole.
- **The defect labels overstated what was checked**
  ([#8](https://github.com/zanwenfu/errata-bench/issues/8)). They were
  relabelled, and two missed defects found. One item was reopened: a person
  must read the four tasks above.
- **Windows paths** ([#9](https://github.com/zanwenfu/errata-bench/issues/9)).
- **Provenance** ([#6](https://github.com/zanwenfu/errata-bench/issues/6),
  partly):
  - every image is pinned by digest;
  - each grading request records the model that served it;
  - results carry a manifest.
- **Documentation**
  ([#1](https://github.com/zanwenfu/errata-bench/issues/1),
  [#2](https://github.com/zanwenfu/errata-bench/issues/2),
  [#10](https://github.com/zanwenfu/errata-bench/issues/10)): corrected
  again on 28 September.

## What comes next

The next milestone extends the task set and improves the graders. In order,
from [#11](https://github.com/zanwenfu/errata-bench/issues/11):

1. **Before any official results:**
   - fix #4;
   - tag `v1.0.3`;
   - name the dataset release in results;
   - decide how unscoreable readings are handled;
   - register the run and its comparison method;
   - run the Claude Code and Codex baselines.
2. **Before outside submissions:**
   - verified entries are only runs we execute;
   - grading refuses tampered trials;
   - the verifier is made harder to subvert;
   - a deliberately cheating agent is caught;
   - server-side web search is flagged for every agent;
   - a canary string is added;
   - a documented way to submit.
3. **The graders:**
   - the trace check to 90%, or retired;
   - the judge re-validated on whole conversations and fresh answers;
   - a judge from another maker;
   - admission on the judge alone (53 tasks) decided.
4. **People** read every task and a blind sample of grades (#3).
5. **More tasks,** held out from grader tuning, with prebuilt images, and a
   test of how the instruction's wording affects results.
