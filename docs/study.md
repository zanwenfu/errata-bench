# The study: does an AI reviewer catch what the developer caught?

At each point where a coding agent handed its work back to a developer, would an
AI reviewer have flagged the problem the developer then pushed back on? And what
does each one catch that the other misses? This is the question Bhuwan suggested
on 10-01: the developer's reply is a rough gold label that no benchmark has.

Status, 10-03:
- built and checked, and smoke-tested on 3 sessions;
- reviewed twice by an independent reader before any larger run (the first
  review's findings are fixed; the second's are below once in);
- the pilot (40 sessions, with a self-review framing), the random arm (40
  sessions), a second batch (80 sessions) and a second reviewer model on the
  pilot have run, for about $368 of the $400 of Azure credits the user set for
  12 hours. Results are below. About $32 is left, for re-running the merges
  after the hand check.

## Design

Two lists, made independently, then merged (the user's design, 10-03):

- **Thread A, the machine** (`study/review.py`). At each **report**, the
  agent's work between two developer messages, a reviewer reads a **window**
  and lists what a careful developer would push back on. Each problem must
  quote the agent's work. The reviewer never sees the developer's reply.
- **Thread B, the human** (`study/human.py`). Everything the developer said
  next is classified. Pushback or not follows SWE-chat's own codebook
  (correction, rejection, failure report, takeover). If pushback, what it
  objects to follows the reader's categories (real error, unwanted but
  defensible, preference, unclear). SWE-chat's label is kept beside each
  reading.
- **The merge** (`study/merge.py`). Each pushback in reply k is compared with
  the reviewer's problems at reports k-2, k-1 and k: same, related or
  different. Then:
  - a pushback with a 'same' problem was caught by both;
  - a pushback with none was the developer's alone;
  - a problem no pushback matched is the reviewer's alone: either a real
    problem the developer let pass, or a false alarm.
- **The hand check.** The user labels about 150 merge decisions blind
  (`labels.csv`; the model's verdicts are in `key.csv`) and about 50 of the
  reviewer's unmatched problems (`alone.md`). The merge is trusted only if it
  agrees with the user at κ ≥ 0.7. This is where "Plans They Abandon, Reports
  They Author" (arXiv 2609.12205) failed: its claim judge agreed with its
  authors' hand checks at κ = 0.19.
- **Two further arms**, the same pipeline:
  - `review --framing self`: the same model told the work is its own, which is
    Bhuwan's "self-reflect" example as far as this study can go without the
    original agent, mostly Claude;
  - `prepare --random N`: sessions drawn without regard to pushback, whose
    rates hold for sessions in general.

`scripts/study.py` runs it: `prepare`, `estimate`, `review`, `human`, `merge`,
`tally`, `sheet` and `combine`. `checks/study_holds.py` checks the rules with
the model faked: 64 checks, plus 38 single-rule mutants, all caught (10-03).

## Who spoke: the rule both threads depend on

A report ends, and a reply begins, wherever the developer speaks. SWE-chat's
table files a great deal under `user_prompt` that the developer never wrote.
Both reviews found it, and it is the study's most important rule, because each
error moves a report boundary and invents or loses a reply. Where the session's
raw transcript exists (38 of the pilot's 40 sessions), the transcript's own
marks decide (`sessions.row_kinds`).

**The developer speaking:**
- a prompt they typed, matched in order to the transcript's typed entries,
  each entry used by one message;
- a slash command, with its arguments;
- a message typed while the agent was working (queued), counted where it was
  typed, once;
- a tool call they refused, with any words they gave.

**Never the developer:**
- compaction summaries, Claude Code's notices and command echoes;
- a skill's or a command's expanded text;
- a teammate agent's message, or a sub-agent's prompt;
- tool output;
- a copy of an earlier message that the table re-inserts later;
- a /loop's timed prompt or re-run, and a background task's notice;
- a row the transcript does not hold at all.

How much this matters, measured 10-03 on the sessions with a raw transcript:
- In the 112 pushback-drawn sessions, the developer typed 1,629 of the 2,386
  rows SWE-chat files as `user_prompt` (68%).
- Rules on the table alone (dropping notices, summaries and teammates by their
  opening words) keep 1,930. Of those, 301 (16%) are still not the developer's
  by the transcript: re-inserted copies, expanded commands and skills,
  scheduled prompts.
- In the 40 random sessions, the developer typed 307 of 572 (54%).

## What the reviewer sees

The whole history before a report is too long to read: on the 40 pilot
sessions, a median of 187,000 tokens, and over 750,000 at the 90th percentile
(10-03). So the reviewer reads a window, in this order:

| Part | Cap (characters) |
|---|---|
| the session's first developer message (the task) | 6,000 |
| the agent's last message before the request | 4,000 |
| the developer's request | 8,000 |
| the agent's work since: its messages, calls and results | 60,000 |

In the work, every message is whole. Each call's input and each result is cut
to 4,000 characters, or less if the work would not fit otherwise (2,000, 800,
300, then 100). Past that, the start of the stretch goes, and the window says
how much. Every cut is marked. When the stretch ends on a call or a result, for
example a call the developer refused, the window says the agent had not
written a report.

Measured on the pilot's 398 windows:
- median 10,400 characters (about 3,000 tokens), 90th percentile 41,500,
  longest 66,600;
- 377 kept the 4,000-character cap, and none lost the start of its stretch;
- 350 end with the agent's report.

## Assumptions, each with its trade-off

1. **Sessions.** The pilot's 40 sessions are drawn, with a fixed seed, from
   sessions that have at least one developer pushback a model has already read
   as a real agent error. At most 2 come from one repository (40 sessions, 37
   repositories). *Trade-off:* sessions chosen for having an error are not a
   random sample. The random arm measures sessions in general. One session,
   fc7511ca, qualified only through a reading of a re-inserted copy: it stays,
   with whatever real pushback it holds.
2. **Who spoke** (above). *Trade-offs:*
   - for the 2 sessions without a transcript, the table's rows decide, with
     notices, summaries and teammates left out;
   - a developer who types the same queued text twice is counted once;
   - a queued message the developer deleted before it was sent still counts.
3. **The window** (above). *Trade-off:* the reviewer can miss something said
   many turns earlier, which the developer remembers. The window keeps the task
   and the agent's previous message to soften this.
4. **What is left out of the window:**
   - the agent's thinking: the developer did not see it, as with v1.1's
     candidates (`recover.with_text`);
   - notices, command output and compaction summaries;
   - a refusal's text, which is the developer's reply.

   Images the developer attached cannot be shown; the window says
   "[Image: ...]".
5. **Long sessions.** Each session gives at most its first 40 reports, which
   trims 3 sessions (94, 129 and 507 reports). Without the cap, one session
   would be half the pilot.
6. **The reviewer judges only the work after the request.** A problem counts
   only if its quote is in that work (`quote_in_work`). One quoting only the
   agent's previous message would be the previous report's problem counted
   twice.
7. **Pushback is SWE-chat's codebook.** A failure report counts even when the
   failing code is earlier work the window does not show (the agent may have
   built it before). Its objection kind is then often 'unclear'. *Trade-off:*
   the main measure, real errors caught, counts only pushbacks read as real
   errors; the others are reported apart.
8. **An approved plan is never pushback.** It gets no call.
9. **The merge looks back two reports.** A developer often objects a turn or
   two after the work, once they have tried it. *Trade-off:* a pushback about
   work from further back is the developer's alone.
10. **"Caught" means a valid 'same'.** The verdict must be known, and the
    overlap must be quoted on both sides and found there. 'Related' is
    reported apart, never counted as caught. A pushback whose merge has not run
    is left out of the share, not counted as missed.
11. **Quotes are checked** with the judge's own `quote_appears`. A reviewer
    problem whose quote is not in the window is kept on its row but left out of
    the merge and the counts.
12. **Shares come with 95% intervals** that resample whole sessions
    (`study/stats.py`): moments of one session are not independent.
13. **One model throughout:** gpt-6-astra, on the Azure credits. The exception
    is the second-model arm, where gpt-6-sol reviews. Claude is refused by the
    client.
14. **The self framing** is the same model told "this is your own work". After
    its first sentence it still describes the work in the third person: a
    design note, not a defect.
15. **A provider's content-filter refusal** is written as an errored row, so a
    resumed stage asks it again, and gets the same refusal. It stays unread and
    is counted under the tally's `not_yet` (1 of 844 replies in the second
    batch). A refusal could be recorded as final, as the reference agent does
    (`_filtered`); not done, given how rare it is.
16. **Spend is priced as the spend guard prices it** (`harbor_spend.priced`):
    uncached input at the cache-write rate, an upper bound. The line covers the
    whole run directory, every stage and framing, and is re-read before every
    call. Each stage holds a lock, so a second copy is refused.
17. **The merge's rules come in two versions** (`merge --rules`).
    - Version 1 gives the definitions of same, related and different. Every
      10-03 result above used it.
    - Version 2 adds the two worked examples from the hand-check guide below,
      each with its reason. The model and the person checking it then draw the
      line from the same text.

    *Found after the runs (10-03):* the guide counts "claims the tests pass but
    never ran them" as the same fault as "the tests still fail". Version 1's
    model was never shown that example. It often calls a problem that flags the
    exact claim the developer found false, but says only that it was not
    verified, 'related'.

    For real errors, 'same' is 50% and 'same or related' 85%, so the line
    matters. A merge row records its version, and a folder holds one version.
    Which version matches a person is the hand check's question, so the
    pushbacks behind the hand-check items should be merged under both.

    *Measured* (`runs/study-pushback-120-rules2`: the 195 real errors under
    version 2, $9.54):
    - caught: 59% [51–67], against 50% [43–57] under version 1;
    - 19 pushbacks went from related to same, 1 the other way, and 'same or
      related' stayed at 85%;
    - failure reports gained most (40% to 54%), corrections little (64% to 66%);
    - by failure mode: shallow investigation 60% to 78%, false claim 60% to 74%,
      unverified assumption 57% to 66%, ignored instruction 80% to 85%;
    - the pilot rose from 37% to 49%, and batch 2 from 56% to 64%.

    The two versions agree on caught or not at κ = 0.80. The pushbacks behind
    the hand-check items that are not real errors were not merged under
    version 2 (about $7, over what was left).

## The smoke runs and the first review (10-03)

**The smoke runs.** Three sessions and 12 handbacks, about $3 of Azure credits
in all (`runs/study-smoke*`). They found:
- rows the developer did not write ending reports;
- a reply classifier that read "onboarding a new repo gives an error with the
  db push" as no pushback. It now uses SWE-chat's codebook.

**The first independent review** found, before any larger run:
- **Rows the developer did not write still became messages:** teammates,
  re-inserted copies (49 rows; only 11 were real splits), summaries that quote
  a meta entry, agent text found nowhere, /loop re-runs.
- **The developer speaking inside a stretch was lost or shown to the
  reviewer:**
  - refused calls, 13 windows, some with the developer's words;
  - queued messages, about 15 reports;
  - slash commands.
- **The spend line was read once per stage.**
- **The tally miscounted:** unmerged pushbacks counted as missed, and a 'same'
  without its words trusted.
- **The random arm left out every session with a real error.**

Each was fixed, given a check, and the check broken once to see it fail.

## Pooled results: 120 sessions with a real-error pushback (10-03; provisional)

The pilot and an 80-session second batch drawn the same way
(`runs/study-pushback-120`, from `scripts/study.py combine`). Outside reviewer,
gpt-6-astra. Provisional until the hand check.

| | Pooled, 120 sessions |
|---|---|
| Handbacks | 1,246 |
| Pushbacks (our reading; SWE-chat's label) | 530; 669 |
| Caught, any pushback | 28% [24–33] |
| **Caught, real errors** | **50% [43–57]**, n=195 |
| Caught, real errors, merge rules version 2 (assumption 17) | 59% [51–67] |
| Caught, pushback that is no error (intent, preference, unclear) | 12–22% |
| Caught, by kind: correction / failure report / rejection | 35% / 24% / 7% |
| The same, real errors only: correction / failure report | 64% [53–74] / 40% [30–51] |
| Caught, by the developer's failure mode (any pushback): false claim / ignored instruction / shallow investigation / unverified assumption | 61% / 74% / 57% / 54% |
| The same, real errors only | 60% / 80% / 60% / 57% |
| Reviewer's problems per handback; handbacks flagged | 1.79; 78% |
| Reviewer's problems no pushback matched | 92% |
| SWE-chat's label against ours (pushback or not) | κ = 0.52 |

The pilot alone caught 37% [27–48] of its 65 real errors, and the second
batch 56% [48–65] of its 130. The two intervals barely overlap. Sessions differ
a great deal, which is why the intervals resample whole sessions.

**Why the two batches differ** (`scripts/study_breakdown.py`, 10-03; no model
call):
- **Not the pipeline.** The pilot was prepared one minute after the second
  review's fixes were committed, and no rule changed before batch 2's stages ran.
  The model behaved the same in both: the same tokens in and out, and the same
  reasoning per call, at every stage.
- **Not the mix.** Batch 2 catches more in every failure mode and kind of
  pushback. Agent, developer persona, month, session length, window size and
  problems per handback are all alike.
- **Sessions differ.** Shuffling whole sessions between the two batches gives a
  gap this large 1.8% of the time (p = 0.018). That test was run after the gap
  was seen.

So the pooled share, with its interval over sessions, is the number to report,
and both batches are reported with it.

**How much the look-back adds.** Three quarters of the catches (112 of 150) are
at the handback the developer answered. Counting only those, the reviewer
catches 40% [33–47] of real errors. Looking back one report gives 46%, and two
gives 50%. The merge saw all three reports' problems together, so a cut-down
count is a sensitivity check, not a re-run.

**A second reviewer model** (pilot, 10-03; `runs/study-pilot-sol`): gpt-6-sol
reviewing, the merge still gpt-6-astra.
- It catches 27% [21–31] of pushbacks and 39% [29–49] of real errors,
  against gpt-6-astra's 23% and 37%.
- It flags more: 2.28 problems a handback, 83% of handbacks.
- So the result is not one model's.

The folder reused the pilot's replies, which were copied in. Its spend guard
therefore counts them again, and prices gpt-6-sol at gpt-6-astra's placeholder
rate (scripts/d40_spend.PRICE on main). The arm's real cost was $8.49 for the
reviews and $12.12 for the merges.

**A reviewer from another family** (pilot, 10-03; `runs/study-pilot-deepseek`):
DeepSeek-V4-Pro reviewing, the merge still gpt-6-astra under rules version 1.
- It catches 14% [9–19] of pushbacks and 18% [10–28] of real errors, half of
  what the two GPT-6 reviewers catch (37% and 39%).
- On the same 65 real errors: 10 caught by both, 14 by gpt-6-astra's reviewer
  only, 2 by DeepSeek's only.
- It raises as many problems (2.15 a handback) but flags fewer handbacks (66%),
  and 96% of its problems match no pushback.
- 57 of its 928 quotes were not found in the window (6%, against gpt-6-astra's
  1 in 735), and 8 quote only the context. Those 65 problems are dropped, as for
  any reviewer.

So the catch rate depends on the reviewer model. Read the cases and part of the
gap is the merge's line between same and related (assumption 17):
- On one session, the developer said the processes and the cycle count were
  "still the same issues".
- gpt-6-astra's reviewer had written "does not establish a fix for an empty
  list", judged same.
- DeepSeek's had written "claims 'Processes Now Showing' but never verified
  that processes actually appear", judged related.

The matcher is also a GPT-6 model, which could favour its family's wording.
DeepSeek's real errors under rules version 2 were not merged (about $3.50, over
what was left). Real cost: $3.81 for the reviews and $11.37 for the merges.

**A second merge model** (pilot, 10-03; `runs/study-pilot-merge-sol`):
gpt-6-sol merged the same 252 pushbacks against the same gpt-6-astra problems.
- Whether a pushback was caught: the two agree on 95% of the 180 pushbacks
  (κ = 0.87), and on 64 of the 65 real errors (κ = 0.97). gpt-6-sol catches 48
  and 25, gpt-6-astra 41 and 24.
- Each candidate's verdict: 89% agree (κ = 0.75 over same, related and
  different; κ = 0.86 for same against the rest). Most disagreements are
  'different' against 'related' (104 of 137), where gpt-6-sol is more lenient.

So the measure does not hang on the model that merges. Two models can still
share a blind spot, so this does not replace the hand check. Real cost: $2.61.

## First results (pilot and random arm, 10-03; provisional)

Provisional until the hand check confirms the merge (κ ≥ 0.7 with the user's
labels). The shares are of merged pushbacks, with 95% intervals resampling whole
sessions.

| | Pilot, outside reviewer | Pilot, self framing | Random sessions, outside |
|---|---|---|---|
| Handbacks | 402 (40 sessions) | 402 | 308 (40 sessions) |
| Pushbacks (our reading) | 180 | 180 | 117 |
| Caught, any pushback | 23% [16–29] | 28% [21–34] | 19% [12–29] |
| Caught, real errors | 37% [27–48], n=65 | 45% [35–57], n=65 | 32% [15–48], n=28 |
| Caught, by SWE-chat's label instead | 18% [14–22], n=215 | 23% [19–27] | 19% [12–27], n=155 |
| Same work, another fault ('related') | 87 of 180 | 80 | 57 of 117 |
| Same or related, real errors | 82% [67–92] | 86% [74–95] | 71% [45–89] |
| Reviewer's problems per handback | 1.83 | 2.01 | 1.88 |
| Handbacks with a problem flagged | 77% | 80% | 79% |
| Reviewer's problems no pushback matched | 93% | 93% | 94% |

- **'Same or related' says little.** It is near the share of handbacks the
  reviewer flags at all (77–80%). The reviewer usually has something to say
  about the handback the developer objects to, so naming the developer's fault,
  'same', is the measure.
- **By kind of pushback** (pilot, outside): corrections 30% caught, failure
  reports 14%, rejections 11% (n=9).
- **Self framing, paired on the same 180 pushbacks:** 38 caught by both, 12 by
  the self framing only, 3 by the outside framing only (exact binomial on the 15
  discordant pairs: p ≈ 0.035, not adjusted for sessions). On real errors: 24
  both, 5 self only, 0 outside only. Either framing catches 29 of the 65.
- **SWE-chat's label and ours agree** on whether a reply is pushback at κ =
  0.54 (pilot) and 0.40 (random). Ours is stricter: 72 replies SWE-chat calls
  pushback are not, and 14 the other way. In a sample, most of the 72 were new
  requests, questions or answers to the agent's question.

**What the reviewer missed** (the author's reading of 16 of the 36 real-error
pushbacks both framings missed, not yet the user's):
- Most are outcomes only someone running the software sees: "the panel opens
  but it is blank", "the build still fails", "the dropdown still does not
  appear", "the import still returns a 404". In many of these the reviewer did
  flag the same handback for not verifying its fix, but it could not know the
  fault. The merge counts that as related, not caught.
- The rest need context only the developer has:
  - norms or authority: "why was PR #45 merged without my approval";
  - their environment: "the API key is already in your .env";
  - personal facts: their background;
  - an image the reviewer cannot see.

So the first answer to Bhuwan's question has two halves:
- The developer catches what running the software shows and what only they
  know. An AI reviewer reading the transcript catches about a quarter of what
  developers object to, and about a third to a half of their real errors.
- The reviewer raises many more problems than developers ever do, about 1.8 a
  handback, and most go unmatched. Whether those are real problems the
  developer let pass or false alarms is the hand check's alone.md.

## The hand check: how to label

Three files, written by `scripts/study.py sheet` into the run's folder. Label them without looking at `key.csv`.

**Label the pooled run's files: `runs/study-pushback-120/`.** They sample all
120 sessions. The pilot's files in `runs/study-pilot/` are superseded.

**Text not in English.** 38 of the 150 rows in `labels.csv` hold a reply or a
quote in Japanese, Korean, Russian or Chinese. They have an English column, and
13 items of `alone.md` have an English block for the problem, its quote and the
request. Claude translated them literally in the session, without opening
`key.csv`. Say in `note` if a translation looks wrong. The untranslated sheets
are beside them (`labels-untranslated.csv`, `alone-untranslated.md`).

### labels.csv: did the reviewer find the developer's problem?

Each row pairs one developer pushback (the reply) with one problem the reviewer
listed before it. Write one word in `your_label`:

- **same:** the reviewer names the fault the developer raised, in the same
  piece of work, even if it is worded differently or less specifically.
  Developer: "the tests still fail". Reviewer: "claims the tests pass but never
  ran them". Same: the reviewer flagged the very claim the developer found
  false.
- **related:** the reviewer points at the same piece of work, or a cause or
  symptom of the fault, but not the fault itself. Developer: "the button does
  nothing on mobile". Reviewer: "the click handler was not tested on touch
  devices". Related: the same work, but the reviewer did not say the button
  fails.
- **different:** another problem.

A catch-all such as "there may be bugs" or "it should test more" is **different**,
unless it names the fault. Use `note` for anything odd, e.g. "the reply is not
really pushback".

### alone.md: the reviewer's problems that no pushback matched

For each, read the problem, its quote, and if needed the work it read (folded).
Decide from the work; what the developer said next is folded below it, to open only after. After **Your call:** write one of:

- **real:** the problem is there in the work. The developer let it pass, or
  had not noticed yet.
- **false alarm:** the work does not have this problem, or it is too minor for
  a careful developer to push back on.
- **can't tell:** the material does not show enough to decide.

### What it is for

- The merge's verdicts are trusted only if they agree with yours at κ ≥ 0.7 on
  labels.csv. Below that, the matching rules are fixed and run again before the
  full run.
- alone.md says how often the reviewer finds real problems the developer let
  pass, and how often it raises false alarms. That is the "machines catch what
  humans miss" side of the result.

It takes about 2 to 3 hours for 150 rows and 50 problems.

## The pilot

    .venv/bin/python scripts/study.py prepare  --out runs/study-pilot         # free
    .venv/bin/python scripts/study.py estimate --run runs/study-pilot --self-framing
    # paid, gpt-6-astra on the Azure credits: source the .env first, ERRATA_PROVIDER=azure
    .venv/bin/python scripts/study.py review --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py human  --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py merge  --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py tally  --run runs/study-pilot
    .venv/bin/python scripts/study.py sheet  --run runs/study-pilot

Size and cost:
- 40 sessions and 398 reports, with about 204 pushbacks to merge.
- Estimated upper bounds: $84 for the outside reviewer, the replies and the
  merge, and $131 with the self framing. The smoke runs cost about half their
  estimates.
- Review and human are independent and can run side by side.

Then:
1. the hand check;
2. more sessions within the budget (`prepare --batch N`; 160 sessions at 2 a
   repository, 521 in all);
3. the random arm (`prepare --random 40`).
