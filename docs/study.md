# The study: does an AI reviewer catch what the developer caught?

At each point where a coding agent handed its work back to a developer, would an
AI reviewer have flagged the problem the developer then pushed back on? And what
does each one catch that the other misses? This is the question Bhuwan suggested
on 10-01: the developer's reply is a rough gold label that no benchmark has.

Status, 10-03 (15:00 UTC):
- built and checked, and smoke-tested on 3 sessions;
- reviewed twice by an independent reader before any larger run, with every
  finding fixed;
- runs done, for about $396 of the $400 of Azure credits the user set for 12
  hours (no paid run is left in that window):
  - the pilot (40 sessions, also with a self-review framing);
  - a second batch (80 sessions);
  - the random arm (40 sessions);
  - on the pilot: a second reviewer model, a reviewer from another family, and
    a second merge model;
  - the 195 real errors merged again under the claim-level rules
    (assumption 17);
- two independent reviews of the day's code and numbers (10-03, afternoon)
  found that the planned hand check could not test the main measure, and then
  that its first replacement's trust rule could pass the wrong version. Both
  were fixed before anyone labelled anything (below);
- waiting for the user's hand check (the guide is near the end of this page).

Results are below. A first draft of the paper built from them is in
`docs/paper-draft.md`.

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
- **The hand check.** The user calls 60 real-error pushbacks (`caught.md`):
  every pushback the two versions of the merge's rules decide differently, and
  20 each that both call caught and that neither does. For each, the user says
  which of the reviewer's problems, if any, name the developer's fault.
  - Weighed back to each group's size, this gives the person's own share of
    real errors caught, with an interval.
  - With every item called, a version of the merge is consistent with the
    person when the interval of its share minus the person's holds 0. κ,
    weighed by group, is reported beside it with an interval, as agreement,
    not as the rule. (A κ ≥ 0.7 rule was planned first. The review showed it
    would pass version 1 at κ 0.80 even with the person agreeing exactly with
    version 2, 9 points away.)
  - The user also calls 50 of the reviewer's unmatched problems (`alone.md`).

  A hand check is where "Plans They Abandon, Reports They Author" (arXiv
  2609.12205) failed: its claim judge agreed with its authors' hand checks at
  κ = 0.19.
- **Two further arms**, the same pipeline:
  - `review --framing self`: the same model told the work is its own, which is
    Bhuwan's "self-reflect" example as far as this study can go without the
    original agent, mostly Claude;
  - `prepare --random N`: sessions drawn without regard to pushback, whose
    rates hold for sessions in general.

`scripts/study.py` runs it: `prepare`, `estimate`, `review`, `human`, `merge`,
`tally`, `combine`, and for the hand check `caught-sheet`, `sheet`,
`replies-sheet` and `agreement`. `checks/study_holds.py` checks the rules with
the model faked: 97 checks. `checks/study_mutants.py` breaks 86 rules one at a
time, and a check fails for each (10-03).

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
  opening words) keep 1,930. The transcript's rules keep 301 fewer (16%): they
  drop re-inserted copies, expanded commands and skills, and scheduled prompts.
  301 is a difference of counts, so at least that many rows differ.
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

Measured on the pilot's 402 windows:
- median 11,242 characters (about 3,200 tokens), 90th percentile 41,625,
  longest 66,573;
- 381 kept the 4,000-character cap, and none lost the start of its stretch;
- 352 end with the agent's report.

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
5. **Long sessions.** Each session gives at most its first 40 reports. On the
   pilot this trims 3 sessions (85, 127 and 512 reports), and on all 120 it trims
   6 (also 43, 46 and 53). Without the cap, one session would be half the
   pilot.
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
    - 19 pushbacks went from related to same, 1 the other way, and 1 from none
      to related ('same or related': 85% under both);
    - failure reports gained most (40% to 54%), corrections little (64% to 66%);
    - by failure mode: shallow investigation 59% to 78%, false claim 60% to 74%,
      unverified assumption 57% to 66%, ignored instruction 80% to 85%;
    - the pilot rose from 37% to 49%, and batch 2 from 56% to 64%.

    The two versions agree on caught or not at κ = 0.80. The hand check
    (`caught.md`) holds all 20 pushbacks whose outcome differs between them.

    *Priming:* the guide gives the person version 2's examples, so agreement
    with version 2 is partly built in. That is the design: the guide is the
    study's definition of "same", and the hand check tests whether each
    version applies it as a person does.

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
| Caught, pushback that is no error (intent, preference, unclear) | 11–22% |
| Caught, by kind: correction / failure report / rejection | 35% / 23% / 7% |
| The same, real errors only: correction / failure report | 64% [53–74] / 40% [30–51] |
| Caught, by the developer's failure mode (any pushback): false claim / ignored instruction / shallow investigation / unverified assumption | 60% / 74% / 57% / 54% |
| The same, real errors only | 60% / 80% / 59% / 57% |
| Reviewer's problems per handback; handbacks flagged | 1.79; 78% |
| Reviewer's problems that no pushback matched as the same fault | 92% (2,052 of 2,232) |
| SWE-chat's label against ours (pushback or not) | κ = 0.52 |

Of the 2,052 unmatched problems:
- 565 were judged 'related' to a pushback;
- 263 were never compared with one, because no pushback followed within two
  reports;
- 1,224 were compared and judged different from every pushback.

The pilot alone caught 37% [27–48] of its 65 real errors, and the second
batch 56% [47–65] of its 130. The two intervals barely overlap.

**Why the two batches differ** (`scripts/study_breakdown.py`, 10-03; no model
call): not found.
- **Not the draw.** Both come from one shuffled order of the same pool: the
  pilot is its first 40, and batch 2 the next 80 that no other study run held.
- **Not the pipeline, as far as we can see.** The pilot was prepared one
  minute after the second review's fixes were committed, and no rule changed
  before batch 2's stages ran. The model behaved the same in both: the same
  tokens in and out, and the same reasoning per call, at every stage.
- **Not the mix.** Batch 2 catches more in every failure mode and kind with
  more than a handful of cases (not in the smallest: rejections, 5% against
  11% across all pushbacks). Agent, developer persona, month, session length,
  window size and problems per handback are all alike. The mix would predict
  batch 2 catching less, not more.
- **A candidate, not tested: the pilot was the development set.**
  - `runs/study-dev` to `study-dev6` each prepared exactly the pilot's 40
    sessions while the who-spoke rules were being fixed, and the smoke runs
    used 3 of them. Batch 2 shares none.
  - Rules tuned on the pilot's sessions may handle them better than batch 2's,
    and errors left in batch 2's report boundaries could move its numbers.
  - A check of who spoke on a sample of batch 2's sessions would test this.

The batches were drawn at random, so the gap is chance, a processing
difference we did not find, or the development history above. Shuffling whole sessions between them gives a gap
this large 1.8% of the time (p = 0.018), about 1 in 55, by a test chosen after
the gap was seen. The pooled share, with its interval over sessions, is the
number to report, and both batches are reported with it.

**How much the look-back adds.** Three quarters of the catches (112 of 150) are
at the handback the developer answered. Counting only those, the reviewer
catches 39% [33–47] of real errors. Looking back one report gives 46%, and two
gives 50%. The merge saw all three reports' problems together, so a cut-down
count is a sensitivity check, not a re-run.

**A second reviewer model** (pilot, 10-03; `runs/study-pilot-sol`): gpt-6-sol
reviewing, the merge still gpt-6-astra.
- It catches 27% [21–31] of pushbacks and 38% [29–49] of real errors,
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
  what the two GPT-6 reviewers catch (37% and 38%).
- On the same 65 real errors: 10 caught by both, 14 by gpt-6-astra's reviewer
  only, 2 by DeepSeek's only.
- It raises as many problems (2.15 a handback) but flags fewer handbacks (66%),
  and 96% of its problems match no pushback.
- 57 of its 928 quotes were not found in the window (6%, against gpt-6-astra's
  1 in 735), and 8 quote only the context. Those 65 problems are dropped, as for
  any reviewer.

So the catch rate depends on the reviewer model. Part of the gap may be the
merge's line between same and related (assumption 17). That was seen in one
case, not measured:
- On one session, the developer said the processes and the cycle count were
  "still the same issues".
- Both reviewers had flagged the same unverified claim in nearly the same
  words. gpt-6-astra's: "claims processes now appear without checking ...
  does not establish a fix for an empty list", judged same. DeepSeek's:
  "claims 'Processes Now Showing' but never verified that processes actually
  appear", judged related.
- So the strict line is applied unevenly, which version 2's examples are meant
  to fix.

The matcher is also a GPT-6 model, which could favour its family's wording.
DeepSeek's real errors under rules version 2 were not merged (about $3.50, over
what was left). That run would measure how much of the gap the line accounts
for. Real cost: $3.81 for the reviews and $11.37 for the merges.

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

Provisional until the hand check confirms the merge (its share consistent
with the user's calls in `caught.md`). The shares are of merged pushbacks, with 95%
intervals resampling whole sessions.

| | Pilot, outside reviewer | Pilot, self framing | Random sessions, outside |
|---|---|---|---|
| Handbacks | 402 (40 sessions) | 402 | 308 (40 sessions) |
| Pushbacks (our reading) | 180 | 180 | 117 |
| Caught, any pushback | 23% [16–29] | 28% [21–34] | 19% [12–29] |
| Caught, real errors | 37% [27–48], n=65 | 45% [35–57], n=65 | 32% [15–48], n=28 |
| Caught, by SWE-chat's label instead | 18% [14–22], n=215 | 23% [18–27] | 19% [12–27], n=155 |
| Same work, another fault ('related') | 87 of 180 | 80 | 57 of 117 |
| Same or related, real errors | 82% [67–92] | 86% [74–95] | 71% [45–88] |
| Reviewer's problems per handback | 1.83 | 2.01 | 1.88 |
| Handbacks with a problem flagged | 77% | 80% | 79% |
| Reviewer's problems no pushback matched | 93% | 93% | 94% |

- **'Same or related' says little.** It is near the share of handbacks the
  reviewer flags at all (77–80%). The reviewer usually has something to say
  about the handback the developer objects to, so naming the developer's fault,
  'same', is the measure.
- **By kind of pushback** (pilot, outside): corrections 30% caught, failure
  reports 14%, rejections 11% (n=9).
- **Self framing, paired on the same 180 pushbacks:**
  - 38 caught by both, 12 by the self framing only, 3 by the outside framing
    only. An exact binomial on the 15 discordant pairs gives p ≈ 0.035, not
    adjusted for sessions.
  - On real errors: 24 both, 5 self only, 0 outside only (p = 0.0625). Either
    framing catches 29 of the 65.
  - The self framing also raises more problems: 2.01 a handback against 1.83.
    Part of its gain may be volume.
- **SWE-chat's label and ours agree** on whether a reply is pushback at κ =
  0.54 (pilot) and 0.40 (random). Ours is stricter: 72 replies SWE-chat calls
  pushback are not, and 14 the other way. In a sample, most of the 72 were new
  requests, questions or answers to the agent's question.

**What the reviewer missed** (a preliminary reading, by Claude in the session,
of 16 of the pilot's 36 real-error pushbacks that both framings missed; not yet
the user's):
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
  handback. 92% match no pushback as the same fault, though a quarter of those
  were judged related to one. Whether the rest are real problems the developer
  let pass or false alarms is the hand check's alone.md.

## The hand check: how to label

**Label the pooled run's files, in `runs/study-pushback-120/`:** `caught.md`
first, then `alone.md`, then `replies.csv` if there is time. Each is in random
order, so label from the top:
- the figures weighed back to all 195 pushbacks appear once each group in
  `caught.md` has 10 calls (or all it has);
- the verdict on each version needs all 60.

Do not open `caught-key.json` or `replies-key.csv`.

The earlier `labels.csv` is in `superseded/`. It was a uniform sample of 150
single merge decisions, and the 10-03 review showed it could not test the main
measure: 113 were plain 'different', and only 4 were 'same' on real errors.
Its κ could pass with every 'same' wrong. The pilot's sheets in
`runs/study-pilot/` are superseded too.

**Text not in English.** English translations, by Claude in the session,
literal and made without opening any key:
- `caught.md`: 9 items have an English block;
- `alone.md`: 13 items have one for the problem, its quote and the request;
- `replies.csv`: 10 rows have an English column.

Note any translation that looks wrong. The untranslated sheets are beside
them (`*-untranslated.*`).

### What "same" means (the guide both you and the merge follow)

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

A catch-all such as "there may be bugs" or "it should test more" is not the
same fault unless it names it.

### caught.md: did the reviewer name the developer's fault?

60 items. Each is one developer pushback about a real agent error: the
developer's reply, then every problem the reviewer raised in the handbacks
before it, lettered A, B, C and on. The end of the agent's work is folded below.

After **Your call:**, on that line or the next, write the letters of the
problems that are the **same** fault, for example `A` or `A, C`, or `a and c`.
If none is, write `none`. A comment after the letters is fine, for example
`B (the build claim)`. Leave the mark `**Your call:**` as it is.

### alone.md: the reviewer's problems that no pushback matched

For each, read the problem, its quote, and if needed the work it read (folded).
Decide from the work; what the developer said next is folded below it, to open only after. After **Your call:** write one of:

- **real:** the problem is there in the work. The developer let it pass, or
  had not noticed yet.
- **false alarm:** the work does not have this problem, or it is too minor for
  a careful developer to push back on.
- **can't tell:** the material does not show enough to decide.

### replies.csv (optional): is thread B's reading right?

The main measure counts only pushbacks that thread B read as real agent errors,
and that reading agrees with SWE-chat's own label only at κ = 0.52. These 40
replies check it by hand. Half were read as real-error pushback, a quarter as
other pushback, a quarter as none, mixed in random order (the reading is in
`replies-key.csv`).

Each row has the developer's request, the end of the agent's work, and the
developer's reply. For each, write:
- **pushback?** `yes` or `no`, by SWE-chat's codebook: a correction, a
  rejection, a failure report or a takeover;
- **about a real agent error?** if pushback: `yes` (the agent got something
  wrong), `no` (a preference or a change of mind), or `unclear`.

A spreadsheet may save it with semicolons or a byte-order mark; both are read.

### When you are done

    .venv/bin/python scripts/study.py agreement --run runs/study-pushback-120

It prints, and writes to `agreement.json`:
- **caught.md, under each version of the merge's rules, on the same items:**
  - the share of real errors caught by your calls, over all 195 pushbacks,
    with an interval. It covers your calls' uncertainty only; the tally's
    intervals cover the sessions';
  - each version's share, its share minus yours with an interval, and whether
    the two are consistent (with all 60 called);
  - κ and agreement weighed by group, κ with an interval;
  - how many of the disputed 20 agree with each version, with a sign test;
  - whether you and the merge name the same problem when both call it caught;
  - each group's counts, and the items still uncalled or unreadable.

  The figures weighed back to all 195 are given once each group has its
  calls.
- **alone.md:** your calls, with the share that are real among those you could
  decide.
- **replies.csv:** for each group thread B's reading came from, how often you
  call it pushback and a real error.

A call it cannot read (a letter the item does not have, or a deleted mark) is
listed, not guessed.

### What it is for

- **The main measure.** Your calls give the share of real errors caught as a
  person judges it. A version of the merge is accepted if its share is
  consistent with yours: the interval of the difference holds 0, with all 60
  called. κ is reported beside it.
- **The disputed 20.** These decide between the two versions: on how many of
  them you agree with each, with a sign test.
- **The other side of the result.** alone.md says how often the reviewer finds
  real problems the developer let pass, and how often it raises false alarms.
  That is the "machines catch what humans miss" side.

It takes about 2 to 3 hours for caught.md and alone.md, and 30 minutes more
for replies.csv.

## The pilot

    .venv/bin/python scripts/study.py prepare  --out runs/study-pilot         # free
    .venv/bin/python scripts/study.py estimate --run runs/study-pilot --self-framing
    # paid, gpt-6-astra on the Azure credits: source the .env first, ERRATA_PROVIDER=azure
    .venv/bin/python scripts/study.py review --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py human  --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py merge  --run runs/study-pilot --max-usd 150 --concurrency 8
    .venv/bin/python scripts/study.py tally  --run runs/study-pilot
    .venv/bin/python scripts/study.py sheet  --run runs/study-pilot   # alone.md

Size and cost:
- 40 sessions and 402 reports, with 252 pushbacks merged (by either reading).
- Estimated upper bounds: $84 for the outside reviewer, the replies and the
  merge, and $131 with the self framing. The smoke runs cost about half their
  estimates.
- Review and human are independent and can run side by side.

Then:
1. the hand check;
2. more sessions within the budget (`prepare --batch N`; 160 sessions at 2 a
   repository, 521 in all);
3. the random arm (`prepare --random 40`).
