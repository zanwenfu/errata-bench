# Known issues in errata-bench v1

What is known to be wrong or unproven in v1, how much it matters, and when it
will be fixed. Each item links to its issue. Reviewed on 27 September 2026, at
v1.0.1.

Running the benchmark is not affected: the tasks, their containers, the
network rule, the verifier and grading all work as documented, and were
checked end to end on a fresh download. The items below concern how far the
official score can be trusted, and what the research record does not yet
report.

## To fix before official v1 results are published

- **The official judge was checked on a different view than it grades, on 13
  of the 51 official tasks** ([#7](https://github.com/zanwenfu/errata-bench/issues/7)).
  - 17 conversations are too long to hand an agent whole, so their long tool
    outputs are cut to fit.
  - Grading shows the judge that cut view, as the agent was shown it. But the
    check that admits the judge to each task read all 17 whole.
  - 13 of the 17 are among the 51 official tasks, and the 4 tasks the judge is
    not admitted to are all among them.
  - Until this is fixed, official scores over these 13 tasks rest on a check
    made on another view. The v1 subset's published results are unaffected:
    its one long task is not admitted.

## What the official score does and does not establish

- **The headline judge passed its registered check narrowly, on another
  setup.**
  - On D-45's answers, 33 of its 36 calls were right (92%, against 90%
    required). That pass rests on one adjudication; ruled the other way, it
    would be 89%.
  - Those answers were graded with conversations cut to 75,000 characters,
    and v1 shows them whole.
  - The per-task admission is the judge's check under v1's own setup (#7
    aside).
- **The second grader, the trace check, is a diagnostic, not validated.**
  - 75% of its flags were real (44 of 59, against 90% required).
  - The two graders agree at κ 0.59, against 0.6 required.
  - An exploratory re-read of the unclear items gave 78-81%.
- **No person has checked the tasks or the grades**
  ([#3](https://github.com/zanwenfu/errata-bench/issues/3)). The readings
  behind these figures were made by Claude models under a written rubric.
- **Four official tasks' defects were never checked in their trees**
  ([#8](https://github.com/zanwenfu/errata-bench/issues/8)).
  - Their signatures name nothing to look for.
  - The stored label of how strongly a defect was confirmed overstates it on
    18 of the 55 tasks. Grading does not read it.
- **On 17 tasks, the rebuilt tree could not be compared with anything the
  conversation read**
  ([#9](https://github.com/zanwenfu/errata-bench/issues/9),
  [#5](https://github.com/zanwenfu/errata-bench/issues/5)). All 55 pass the
  consistency check, but on these 17 it compared no file. On one, that is
  because the check cannot read Windows paths.
- **Too few answers to rank models yet.** The v1 subset (10 tasks, one attempt
  each) checks the pipeline and the graders, not the models.

## Fixed in this review

- **Documentation** ([#10](https://github.com/zanwenfu/errata-bench/issues/10)):
  - commands that failed with "permission denied", now `python scripts/...`;
  - the README's validation figures and v1 status;
  - a stale row in the research log;
  - numbers in the v1 plan.

## Gaps in the research record, for the next milestone

- **[#1](https://github.com/zanwenfu/errata-bench/issues/1):** D-45's four
  criteria are published (R-40). The tool output lost when D-44 stored its
  answers (527 cuts in 77 answers, 3.4 million characters) is not yet
  reported beside them.
- **[#2](https://github.com/zanwenfu/errata-bench/issues/2):** the README's
  descriptions of screening, task admission, consistency and provenance
  promise more than the code does.
- **[#4](https://github.com/zanwenfu/errata-bench/issues/4):** a grader's
  "record cut" citation is checked for its form, not against the record it
  was shown.
- **[#5](https://github.com/zanwenfu/errata-bench/issues/5):** there is no
  per-task report of how completely each task was rebuilt.
- **[#6](https://github.com/zanwenfu/errata-bench/issues/6):** the harness's
  own sandbox images are named by tag, and the model served is checked at
  each stage's start and end, not on each request. v1's task images are
  pinned by digest.
