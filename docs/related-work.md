# Related work, and the questions it raises for errata-bench

The README's §5 compares errata-bench with the closest work in one table. This
page takes the closest studies one at a time and ends with the questions they
raise for errata-bench, each tracked in an issue.

**How each entry was checked:**
- **OverclaimBench** was re-read in full on 29 September 2026: all 28 pages
  of arXiv v3, appendix included.
- **Every other entry** records what was checked against the paper itself
  for the README's comparison, in September 2026. Where the paper did not
  say, neither does this page.

## The closest study: OverclaimBench

Smyth, Mantilla-Ramos, Notsawo, Helbling, A. Tosato, Merzouk, Dziri, Gidel and
T. Tosato (Tara Research, Mila, Cohere), "Quantifying Overclaiming Propensity
in Frontier LLM Agents", [arXiv:2609.20812](https://arxiv.org/abs/2609.20812).
First posted 17 September 2026; v3 of 22 September was read.

**The question.** Do coding agents report the scope of their own work
truthfully? *Overclaiming* is a final response that asserts work contradicted
by the agent's own transcript: for example, claiming to have read a file it
never opened. It needs no inference about intent, and does not depend on
whether the work is correct.

**The tasks.** Five scenarios, written by the authors, each a review of a
corpus with 1–4 planted defects ("needles"):

| scenario | kind | files | needles |
|---|---|---|---|
| security audit of a billing service | code | 100 | 4 |
| infrastructure review (Terraform) | code | 100 | 1 |
| release go/no-go for a payments service | code | 221 | 4 |
| proof review of mathematical lemmas | text | 240 | 3 |
| sprint planning from a backlog | text | 519 | 3 |

- Each corpus fits every model's context window. The largest uses 76% of the
  smallest window, so skipping files is behaviour, not a length limit.
- Every needle has a registry, fixed before any run, of the exact lines needed
  to find it. The registry was validated: a model reviewing only the relevant
  files finds the needle, and does not find it once the needle is removed.
- The scenarios were shaped against Claude Opus as the agent under test, and
  the authors say this may bias the results.

**How many runs.**
- Each model ran each scenario 20 times, because the paper measures a
  propensity: how often it happens, not across how many tasks.
- 12 models × 5 scenarios × 20 runs would be 1,200. Gemini 3.1 Pro refused the
  three code scenarios as security-sensitive, which leaves 1,140.
- A separate experiment required or forbade subagents for six models: 20 runs
  per model, scenario and condition, 1,200 runs more.

**How the agents were run, Claude Code and Codex included.**
- *Each vendor's own production CLI,* not a common scaffold, "to preserve
  model-specific prompting, context management, tool interfaces, and agentic
  control logic":
  - Claude Sonnet 5, Opus 5 and Fable 5 through Claude Code 2.1.219;
  - GPT-5.6-sol, -terra and -luna through Codex 0.144.1;
  - Grok-4.6 through Grok Build 0.2.93;
  - Gemini 3.1 Pro through Antigravity 1.1.15.
- *Each at the CLI's default reasoning tier, "high",* with no hyperparameter
  search. Each run ended on its own.
- *A sealed Docker container,* its egress limited to an allowlist of
  inference, authentication and CLI-service endpoints, through a proxy
  sidecar. The inference ran on each vendor's own infrastructure.
- *Prompts that are neutral and naturalistic,* with no test-like wording and
  no questioning afterwards, to limit evaluation awareness. The prompt asks
  the agent to say how it scoped its review. The exact prompts are not in the
  paper.
- *Subagents' reads counted as the run's:* the harness collects each
  subagent's own session records and adds its file reads to the transcript.
  - Opus 5 never delegated unprompted. The paper traces this to Claude Code
    2.1.219, whose system prompt told only Opus 5 not to call its subagent
    tool unless asked (anthropics/claude-code#80988).
  - A CLI version, not only the model, shapes behaviour.
- *The four open-weight models* (DeepSeek-V4-Flash, Qwen3.8-27B, GLM-5.3,
  GLM-5.3-Flash) ran through Claude Code, pointed at Anthropic-compatible
  endpoints.
  - Each OpenRouter model was pinned to two named providers, with no fallback.
  - The provider mattered: DeepSeek missed 61% of needles on one provider and
    42% on the other.
- *Eight open-weight runs* that delivered no review were run again.
- *No seed control exists for these CLIs,* so sampling variation is reported
  as 95% Wilson intervals over the 20 runs.

**How it is measured.**
- *Deterministic.* A file is *touched* when a line unique to it appears in any
  tool output the model saw. A needle is *read* when every one of its
  registered lines did. No model judgment is involved.
- *By a judge,* Claude Opus 4.8 at high effort, shown the final reply, the
  saved report and the coverage facts, but not the transcript:
  - among runs that left files untouched, whether the report *admits* the
    gap, *omits* it, or *explicitly overclaims* a complete review;
  - whether each needle was reported.
- *Judge reliability.* 774 runs were re-judged eight times each: 91.1%
  unanimous, and a single verdict matched the majority 97.7% of the time. No
  check against people is reported.
- *Statistics.* Wilson intervals, and logistic regressions for the delegation
  experiment. Per-model effects are given as descriptive follow-ups, without
  a correction for making many comparisons.

**Results.**
- Agents left at least one assigned file unopened in 67.9% of runs.
- Of those runs, 80.4% were misleading (59–96% per model): 52.8% explicitly
  claimed a complete review, and 27.5% left the gap unsaid.
- Requiring subagents raised coverage (files touched 86.9% → 97.3%). It did
  not make the reports of the reviews still incomplete more honest.
- Runs that overclaimed missed planted defects 1.8 times as often as runs
  that read every file.

**What was released.**
- Nothing public: the corpora, the needle registry, the harness, the judge
  prompts and the analysis code are all withheld. The reason: models trained
  on the exact planted defects would recognise them, which would void the
  measurement.
- The authors plan to share a bundle with vetted researchers under an
  agreement that forbids redistribution and use in training. Replicating the
  exact results needs that approval.

**Against errata-bench.**

| | OverclaimBench | errata-bench |
|---|---|---|
| **tasks** | 5 constructed reviews, each run 20 times per model | 51 real moments from real developer sessions, each run 3 times per model |
| **the claim checked** | one kind: "I reviewed everything" | any claim about the agent's own work, and whether the fix itself works |
| **core measurement** | deterministic coverage, with a judge classifying the report | a judge and a trace check, both model readings, with controls, probes and a calibration gate |
| **harness** | each vendor's CLI | so far one reference agent for all models; vendor CLIs run through Harbor, untried |
| **statistics** | Wilson intervals, per-model tests uncorrected | exact paired tests by repository, Holm-corrected, registered first |
| **data** | withheld | public code and results; tasks gated on Hugging Face |

The two agree in substance. Agents' final reports misstate what they did,
across makers and model sizes. OverclaimBench shows it under controlled
conditions, errata-bench in real sessions.

## Observational studies of real sessions

These describe what past agents did; none puts a new model in the same place.

- **How Coding Agents Fail Their Users** (Tang et al., May 2026,
  [arXiv:2605.29442](https://arxiv.org/abs/2605.29442)).
  - *Data:* 20,574 real sessions (SpecStory and SWE-chat), 16,118
    misalignment episodes, each defined by the developer's pushback.
  - *Finding:* 22.58% of the episodes are "inaccurate self-reporting".
  - *Checking:* labels checked against experts, precision 0.93.
  - *Released:* the labels.
- **Measuring coding agent misalignment in the wild** (Transluce, August 2026,
  [blog](https://transluce.org/docent/blog/coding-agent-behaviors)).
  - *Data:* 4,990 SWE-chat sessions and 3,671 of its own.
  - *Finding:* "overselling" in 34.7% of the SWE-chat sessions, judged against
    the transcript by Claude Opus 5 under a published rubric.
  - *Checking:* 100 runs spot-checked; disagreements reviewed by hand; no
    agreement figure.
  - Per-model rates come from non-random subsets and, by the authors'
    account, cannot be compared.
- **Plans They Abandon, Reports They Author** (Kraishan and Jitkajornwanich,
  September 2026, [arXiv:2609.12205](https://arxiv.org/abs/2609.12205)).
  - *Data:* SWE-chat.
  - *Findings:* a final summary refers to about one action in eleven. Its
    model adjudicator found 67% of sampled claims supported where hand coding
    found 36% (κ 0.185), so the authors drew no conclusion from its
    unsupported-claim rate.
- **Between the Commits** (Leith, September 2026,
  [arXiv:2609.29744](https://arxiv.org/abs/2609.29744)).
  - *Data:* one developer, one wholly AI-authored codebase.
  - *Finding:* about one reply in four or five contains a factual error.
  - *Checking:* two scorers (κ 0.80) and a human sample (κ 0.80 / 0.75).
- **Message–code inconsistency in agent pull requests** (Gong et al., January
  2026, [arXiv:2601.04886](https://arxiv.org/abs/2601.04886)).
  - *Data:* 23,247 agent PRs.
  - *Finding:* in 45.4% of the inconsistent ones, the description claims
    changes that were not made.

## Benchmarks that replay real sessions

- **SWE-Together** (Wu et al., Meta, June 2026,
  [arXiv:2606.29957](https://arxiv.org/abs/2606.29957)).
  - *Tasks:* 109, 48 of them from SWE-chat, replayed from the session's first
    request.
  - *Scores:* code correctness and user corrections, not the honesty of the
    report. Reference patches pass 78%.
  - One of its sessions is behind one of errata-bench's tasks.
- **RealClawBench** (Lv et al., June 2026,
  [arXiv:2606.03889](https://arxiv.org/abs/2606.03889)).
  - *Tasks:* 281, from internal OpenClaw sessions, with false "done" claims
    audited.
  - *Finding:* an auditor shown the execution evidence agreed with people at
    κ 0.748, against κ 0.128 when shown only the final output.

## Studies of false success and graders

- **False success in LLM agents** (Advani, June 2026,
  [arXiv:2606.09863](https://arxiv.org/abs/2606.09863)).
  - *Data:* completion claims checked against the final state of τ²-bench
    and AppWorld trajectories. Labels agree with people at κ 0.86.
  - *Finding:* no configuration of five judges and five prompts exceeded
    AUROC 0.65 at spotting false success on τ²-bench, or 0.54 on AppWorld.

## The labs' own evaluations

- **Code summary honesty,** Claude system cards (Anthropic, May–June 2026).
  - *Method:* coding transcripts from training runs that were not fully
    successful are prefilled, and the model is asked to summarise its work.
  - *Finding:* earlier models gave dishonest summaries more than half the
    time.
  - *Released:* internal.
- **Coding deception,** GPT system cards (OpenAI, 2025–2026).
  - *Method:* false reports of actions, verification or tool access, on tasks
    chosen to elicit them.
  - *Released:* internal.
- **Simulated internal deployments,** GPT-5.6 system card (OpenAI, July 2026).
  - *Method:* prefixes of internal coding trajectories, continued with a
    simulated tool environment, and a monitor judging the final turn.
  - *Released:* internal.

## Benchmark validity

- **ImpossibleBench** (Zhong, Raghunathan and Carlini, 2025) makes cheating
  on tests mechanically identifiable. OverclaimBench does the same for false
  claims of reading.
- **SWE-bench Verified and Pro, Terminal-Bench:** human task review, and
  reference solutions that must pass. SWE-bench Pro keeps a held-out set;
  Terminal-Bench also runs a no-op agent and a cheating agent.

## The questions these studies raise for errata-bench

Each question is tracked in an issue. Each is also named in
[#11](https://github.com/zanwenfu/errata-bench/issues/11), the next
milestone.

1. **Run the vendors' own CLIs, as OverclaimBench did**
   ([#12](https://github.com/zanwenfu/errata-bench/issues/12)).
   - Every errata-bench result so far comes from one reference agent. Harbor
     can run Claude Code and Codex, but it has not been tried.
   - OverclaimBench's setup gives a checklist:
     - pin the CLI version, and record it with each trial;
     - use each CLI's default reasoning tier;
     - keep a sealed container with an allowlisted proxy;
     - count subagents' own records as the run's;
     - pin providers where a router serves the model;
     - report run-to-run variation, since no seed exists.
   - The Opus 5 subagent bug shows that a CLI release alone can change what
     is measured.
2. **A measure that needs no judge**
   ([#13](https://github.com/zanwenfu/errata-bench/issues/13)).
   - OverclaimBench's core number needs no model judgment. errata-bench's
     headline rests on a model's reading, validated but fallible.
   - Some of errata-bench's claims can be refuted by the record alone:
     - "the tests pass", when no test command ran, or the last one failed;
     - "I changed X", when X did not change;
     - "fixed", when the defect's own token is still in the tree.
   - A judge-free measure of these, developed on the research runs and
     registered before it is applied to v1, would sit beside the headline
     without depending on any grader.
   - Advani's AUROC ≤ 0.65 for judges spotting false success, and the κ
     0.185 of Kraishan and Jitkajornwanich, are why this matters.
3. **Contamination**
   ([#14](https://github.com/zanwenfu/errata-bench/issues/14)).
   - OverclaimBench withholds everything so that future models cannot learn
     its planted defects, and SWE-bench Pro keeps a held-out set.
   - errata-bench's tasks are public (gated), and SWE-chat itself is public.
   - Options:
     - a private held-out split for the leaderboard;
     - a canary string, already in #11;
     - tasks refreshed from newer sessions;
     - checking whether a model recognises a task.
4. **Graders checked against people**
   ([#3](https://github.com/zanwenfu/errata-bench/issues/3)).
   - Tang et al., Leith, RealClawBench and Advani all validated against
     people. OverclaimBench, like errata-bench so far, did not.
   - RealClawBench's result, κ 0.748 with the execution evidence against
     0.128 without, supports errata-bench's choice to show its graders the
     record.
   - The planned blind human reading should report agreement the way these
     studies do.
5. **Who the tasks favour, and whether models can tell they are tested**
   ([#15](https://github.com/zanwenfu/errata-bench/issues/15)).
   - OverclaimBench shaped its scenarios against Claude Opus and says so.
     errata-bench's tasks come from moments where a Claude model failed
     (G-67).
   - Both can favour or disfavour one maker.
   - OverclaimBench also argues that test-like prompts invite
     evaluation-aware behaviour. errata-bench's tasks are real conversations,
     but its reference agent's own framing has not been examined for this.
6. **Comparable categories.** OverclaimBench's three verdicts on incomplete
   work line up with errata-bench's outcome labels. The labels apply to answers
   that engaged with the defect but left it in place (`judge.Judgement.outcome`):
   - *explicit overclaim* with `false_assurance`: the answer states as settled
     something it did not establish;
   - *admission* with `honest_shortfall`: no such claim, and it reports its
     limits;
   - *omission* with `silent_shortfall`: no such claim, and it says nothing of
     its limits.

   The constructs differ: an unfixed defect in errata-bench, unread files in
   OverclaimBench. Reporting errata-bench's results in those terms, next to its
   own, would let readers set the two side by side. This is tracked in #13 with
   the judge-free measure.
