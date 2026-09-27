# Known issues in errata-bench v1

What is known to be wrong or unproven in v1, how much it matters, and where it
stands. Each item links to its issue. Reviewed on 27 September 2026, at
v1.0.2.

Running the benchmark is not affected. The tasks, their containers, the network
rule, the verifier and grading all work as documented, and were checked end to
end on a fresh download. What remains open concerns how far the official score
can be trusted, and what still needs a person.

## Fixed in v1.0.2

- **The judge graded on a view it was not admitted on**
  ([#7](https://github.com/zanwenfu/errata-bench/issues/7)).
  - 17 conversations are too long to hand an agent whole, so their long tool
    outputs are cut in the instruction, and the whole conversation is left in
    the container.
  - Grading showed the judge that cut view, but its admission to each task read
    the conversation whole.
  - Graders now read the whole conversation on every task, as the admission did.
    Grading refuses to run on any task where the two differ.
  - The 51 official tasks stand.
- **A grader's cut citations were checked for their wording only**
  ([#4](https://github.com/zanwenfu/errata-bench/issues/4)).
  - A `record cut` claim is now excused only when the cut it quotes is one the
    grader was shown: the same character count, in the call or turn it names.
  - A citation of a cut the grader was never shown counts against the answer,
    and its row marks it as the grader's error.
  - All 3,176 stored citations in D-44 and D-45 were checked: none was
    invented, so no stored grade changes (`scripts/audit_cut_citations.py`).
- **What a run was made with** ([#6](https://github.com/zanwenfu/errata-bench/issues/6)).
  - The harness's own sandbox images are pinned by digest, as v1's task images
    are.
  - Each grading request records the model that served it.
  - A Harbor run's `results.json` carries a manifest of its code, tasks,
    admission, dependency lock and provider, with no credential.
- **The consistency check could not read Windows paths**
  ([#9](https://github.com/zanwenfu/errata-bench/issues/9)). The one Windows
  task now compares its three files, and none differs.
- **The defect label overstated what was checked**
  ([#8](https://github.com/zanwenfu/errata-bench/issues/8)).
  - Each task is now labelled with what its check established: the defect's
    text found, its file found, nothing to look for, or not found.
  - The two defects the check had missed are found: one split by a Markdown
    link, one in a file named by the end of its path.
  - Corrected labels ship in v1.0.2.
- **Documentation** ([#10](https://github.com/zanwenfu/errata-bench/issues/10),
  [#2](https://github.com/zanwenfu/errata-bench/issues/2),
  [#1](https://github.com/zanwenfu/errata-bench/issues/1)):
  - commands that failed with "permission denied";
  - the README's validation figures and v1 status;
  - its descriptions of screening, the rebuild, admission, provenance and
    resumability;
  - the research log's stale entries.

## What the official score does and does not establish

- **The headline judge passed its registered check narrowly, on another
  setup.**
  - On D-45's answers, 33 of its 36 calls were right (92%, against 90%
    required). That pass rests on one adjudication; ruled the other way, it
    would be 89%.
  - Those answers were graded with conversations cut to 75,000 characters, and
    v1 shows them whole.
  - The per-task admission is the judge's check under v1's own setup.
- **The second grader, the trace check, is a diagnostic, not validated.**
  - 75% of its flags were real (44 of 59, against 90% required).
  - The two graders agree at κ 0.59, against 0.6 required.
- **No person has checked the tasks or the grades**
  ([#3](https://github.com/zanwenfu/errata-bench/issues/3)).
  - The readings behind these figures were made by Claude models under a
    written rubric.
  - Four official tasks' defects name nothing a check could look for:
    entireio-cli-283, femto-mcp-chrome-58, hutusi-amytis-15 and
    hutusi-amytis-349 (#8).
- **On 16 tasks the rebuilt tree could not be compared with anything the
  conversation read** ([#5](https://github.com/zanwenfu/errata-bench/issues/5)).
  - All 55 pass the consistency check. Across them, 147 files were compared,
    none differing, and 17 were not found.
  - Each task's coverage is in `results/v1-coverage.md`.
- **Too few answers to rank models yet.** The v1 subset (10 tasks, one attempt
  each) checks the pipeline and the graders, not the models.

## Still open, for the next milestone

- **[#3](https://github.com/zanwenfu/errata-bench/issues/3), a reading by
  people:** of every task, and of a blind sample of graded answers.
- **[#5](https://github.com/zanwenfu/errata-bench/issues/5), the coverage
  report's review:** for each repository file the conversation read and the
  tree lacks, whether it limits the task or merely a conclusion.
- **[#6](https://github.com/zanwenfu/errata-bench/issues/6), provenance of the
  checks:** calibration, control and probe rows record no token use, and the
  model that served them only at a stage's start and end.
