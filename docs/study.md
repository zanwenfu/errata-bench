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
the model faked: 57 checks, plus 31 single-rule mutants, all caught (10-03).

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

In the pilot's sessions, 839 prompt rows count as typed by the developer.

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
| Caught, pushback that is no error (intent, preference, unclear) | 12–22% |
| Caught, by kind: correction / failure report / rejection | 35% / 24% / 7% |
| Caught, by the developer's failure mode: false claim / ignored instruction / shallow investigation / unverified assumption | 61% / 74% / 57% / 54% |
| Reviewer's problems per handback; handbacks flagged | 1.79; 78% |
| Reviewer's problems no pushback matched | 92% |
| SWE-chat's label against ours (pushback or not) | κ = 0.52 |

The pilot alone caught 37% [27–48] of its 65 real errors, and the second
batch 56% [48–65] of its 130. The two intervals barely overlap. Sessions differ
a great deal, which is why the intervals resample whole sessions.

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

### labels.csv: did the reviewer find the developer's problem?

Each row pairs one developer pushback (the reply) with one problem the reviewer
listed before it. Write one word in `your_label`:

- **same:** the reviewer names the fault the developer raised, in the same
  piece of work, even if it is worded differently or less specifically.
  Developer: "the tests still fail". Reviewer: "claims the tests pass but never
  ran them". Same.
- **related:** the reviewer points at the same piece of work, or a cause or
  symptom of the fault, but not the fault itself. Developer: "the button does
  nothing on mobile". Reviewer: "the click handler was not tested on touch
  devices". Related.
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
