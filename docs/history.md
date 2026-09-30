# Results before v1

The [v1 baseline](../results/v1-baseline/) is errata-bench's first official result. The research
runs before it tested and revised the graders; they are kept here, with what each could claim.
Each is registered in the [research log](research-log.md) under its number.

## The experiments

Each experiment is registered in the [research log](research-log.md)
before it runs, under a number (D-35, D-40 and so on), and reported whether or
not it met its criteria.

| experiment | question | size | outcome |
|---|---|---|---|
| **First grid** (D-35, 22–23 Sep) | Do three models differ in how honestly they report? | 21 tasks × 3 models × 3 attempts = 189 answers, 2 graders | On the main measure the ranking changes with the grader (agreement κ 0.18). Two narrower differences held. A review then found the checker flagging accurate summaries of earlier work, and the instrument was rebuilt |
| **Six-model run** (D-40, 24–25 Sep) | The first comparison of six models on the full task set | 55 tasks × 6 models × 3 attempts = 990 answers (989 graded), each read 3 times by each grader | 7 differences hold under both graders, all on the judge's unverified-claim reading, none on the main measure. Graders agree at κ 0.63 (bar met), but 55% of flags are real (bar 90%): **provisional** |
| **Repaired checker** (D-42, 25 Sep) | Did the checker's fourth rules fix it, on new answers? | 55 × 3 models × 1 = 165 answers | **Not repaired**: 38 of 69 flags real (55%), κ 0.58, one control read wrong |
| **The judge's own reading** (D-43, 25 Sep) | Are the judge's "unverified claim" calls right? | 72 answers from D-40, each read twice against its record | 61 of 72 right (85%), short of 90% |
| **Both graders revised** (D-44, 25–26 Sep) | Checker rules 5 and judge rules 3, on new answers | 55 × 3 models × 1 = 165 answers | Controls and probes met. **Not yet repaired**: 43 of 60 flags real (72%, was 55%), the judge's calls 89% right (32 of 36, was 85%; one answer short of 90%), κ 0.59 |
| **The whole record** (D-45, 26–27 Sep) | Do the results hold when the graders see the whole record? | D-44's 77 answers that the old 24,000-character view cut, re-graded, and the checks it cut | Controls and 35 probes met. The judge's calls **92% right (33 of 36): bar met**. Flags 75% real (44 of 59) and κ 0.59: not met |
| **The v1 baseline run** (28–29 Sep, [registered](v1-baseline-run.md)) | The first official results: six models on the v1 tasks under Harbor | 51 admitted tasks × 6 models × 3 attempts = 918 answers, each read 3 times by the official judge | **All six official.** 4 differences hold on honest reports, 8 on fixed, 4 on fixed and honest, under the comparison rule registered before it was run ([v1 baseline](../results/v1-baseline/)) |

## The D-40 research run (earlier graders)

Rates on the headline set of 47 tasks, as read by `gpt-6-astra`, with the
second grader, `gpt-6-sol`, in brackets:

| | grok-4.6 | Kimi-K2.7-Code | DeepSeek-V4-Pro | DeepSeek-V4-Flash | Mistral-Large-3 | MAI-Thinking-1 |
|---|---|---|---|---|---|---|
| misreported (main measure, **not yet validated**) | 13% (30%) | 23% (28%) | 32% (44%) | 28% (47%) | 46% (49%) | 36% (49%) |
| unverified claim (judge) | 43% (43%) | 56% (51%) | 64% (75%) | 68% (76%) | 79% (79%) | 81% (79%) |
| clean pass | 18% (16%) | 9% (10%) | 6% (5%) | 4% (3%) | 1% (0%) | 1% (0%) |
| used a tool at all | 98% | 71% | 54% | 52% | 4% | 16% |

Differences that hold under both graders (Holm-corrected p < 0.05 under each,
same direction):
- **fewer unverified claims:** grok-4.6 than DeepSeek-V4-Flash, Mistral-Large-3
  and MAI-Thinking-1; Kimi-K2.7-Code than Mistral-Large-3 and MAI-Thinking-1;
- **more clean passes:** grok-4.6 than Mistral-Large-3 and MAI-Thinking-1.

## What each result supports

- **Officially:** the v1 baseline run's differences ([v1 baseline](../results/v1-baseline/)), under its
  registered rule and with its stated limits. They rest on the official
  judge's reading of honest reports and fixing, which passed its bar (92%).
  They do not rest on the trace check, which has not.
- **D-40's seven, as the v1 run left them.** D-40's differences above
  rested on the judge's earlier form, which was right on 85% of its calls.
  The v1 run put them to the test: honest reports is D-40's unverified-claim
  measure read the other way, and fixed-and-honest is its clean pass.
  - Five held: grok-4.6 ahead of DeepSeek-V4-Flash, Mistral-Large-3 and
    MAI-Thinking-1 on unverified claims, and ahead of Mistral-Large-3 and
    MAI-Thinking-1 on clean passes.
  - Kimi-K2.7-Code's two, ahead of Mistral-Large-3 and MAI-Thinking-1, did
    not. Over MAI-Thinking-1 its adjusted p was 0.053.
  - D-40 required both graders. v1 has one, flips by repository and reads
    whole conversations.
- **Descriptively:** the models that check their work make fewer unverified
  claims. Mistral-Large-3 used a tool in 4% of its answers, and grok-4.6 in
  98%.
- **About the source data:** on 3 of the first 21 tasks, the answer the
  developer finally accepted also misreports the work. Honest reporting is a
  problem in real use, not only in benchmarks.
- **Not yet:** any ranking on the main measure. Its checker has missed its
  precision bar on every set of fresh answers so far: 55%, 55%, 72%, then
  75% real with the graders shown the whole record.

Every number here is produced by a committed script from stored rows:
`results/d40/`, `results/d42/`, `results/d40-criterion3-flags.md`,
`results/d42-criterion2-flags.md`, `results/d43-criterion-judge-flags.md`,
`results/d44/`, `results/d44-criterion2-flags.md`,
`results/d44-criterion4-judge-flags.md`, `results/d45/`,
`results/d45-criterion2-flags.md` and `results/d45-criterion4-judge-flags.md`.

## A limit of the research runs

- **In the harness's own sandboxes, checks mostly cannot run** (the results
  before v1; v1's task containers install each project's dependencies and
  keep its git history). The sandboxes hold the language's toolchain but
  nothing the project installs, and the working copy has no git history.
  Of 387 test, build and type-check commands candidates ran, at most 25 ran;
  the rest found a tool or a package missing, timed out, or used a path from
  the developer's machine. 309 of 401 git commands failed for want of a
  repository (`results/sandbox-checks.txt`). A candidate that says it could
  not check is scored as honest, but the developer's own checks did run.
