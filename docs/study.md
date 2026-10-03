# The study: does an AI reviewer catch what the developer caught?

At each point where a coding agent handed its work back to a developer, would an
AI reviewer have flagged the problem the developer then pushed back on? And what
does each one catch that the other misses? This is the question Bhuwan suggested
on 10-01: the developer's reply is a rough gold label that no benchmark has.

Status, 10-03: built, checked and smoke-tested on 3 sessions. The 40-session
pilot is ready to run and waits for the user's go.

## Design

Two lists, made independently, then merged (the user's design, 10-03):

- **Thread A, the machine** (`study/review.py`). At each **report**, the
  agent's work between two developer messages, a reviewer reads a **window**
  and lists what a careful developer would push back on. Each problem must
  quote the agent's work. The reviewer never sees the developer's reply.
- **Thread B, the human** (`study/human.py`). Every reply the developer wrote
  is classified: pushback or not, by SWE-chat's own codebook (correction,
  rejection, failure report); and if pushback, what it objects to, in the
  reader's categories (real error, unwanted but defensible, preference,
  unclear). SWE-chat's label is kept beside each reading.
- **The merge** (`study/merge.py`). Each pushback in reply k is compared with
  the reviewer's problems at reports k-2, k-1 and k: same, related or
  different. A pushback with a 'same' problem was caught by both. A pushback
  with none was the developer's alone. A problem no pushback matched is the
  reviewer's alone: either a real problem the developer let pass, or a false
  alarm.
- **The hand check.** The user labels about 150 merge decisions blind
  (`labels.csv`; the model's verdicts are in `key.csv`) and about 50 of the
  reviewer's unmatched problems (`alone.md`). The merge is trusted only if it
  agrees with the user at κ ≥ 0.7. This is where "Plans They Abandon, Reports
  They Author" (arXiv 2609.12205) failed: its claim judge agreed with its
  authors' hand checks at κ = 0.19.

`scripts/study.py` runs it (`prepare`, `estimate`, `review`, `human`, `merge`,
`tally`, `sheet`). `checks/study_holds.py` checks the rules with the model
faked: 32 checks, plus 7 single-rule mutants, all caught (10-03).

## What the reviewer sees

The whole history before a report is too long to read: on the 40 pilot
sessions, a median of 187,000 tokens, and over 750,000 at the 90th percentile
(10-03). So the reviewer reads a window, in this order:

| Part | Cap (characters) |
|---|---|
| the session's first developer message (the task) | 6,000 |
| the agent's last message before the request | 4,000 |
| the developer's request | 8,000 |
| the agent's work since, ending with its report | 60,000 |

In the work, every message is whole. Each call's input and each result is cut
to 4,000 characters, or less if the work would not fit otherwise (2,000, 800,
300, then 100). Past that, the start of the stretch goes, and the window says
how much. Every cut is marked.

Measured on the pilot's 381 windows: median 10,900 characters (about 3,100
tokens), 90th percentile 41,600, longest 66,800. 97% kept the 4,000-character
cap, and no window lost the start of its stretch.

## Assumptions, each with its trade-off

1. **Sessions.** The pilot's 40 sessions are drawn, with a fixed seed, from
   sessions that have at least one developer pushback a model has already read
   as a real agent error, at most 2 per repository (40 sessions, 37
   repositories). That guarantees each session has a human pushback to compare
   against. *Trade-off:* sessions chosen for having an error are not a random
   sample, so a rate such as "how often a reviewer raises a false alarm" holds
   for these sessions, not for all sessions. A random-session arm is a later
   decision.
2. **Who wrote a message.** SWE-chat's table files a skill's text, a command's
   expansion, a hook, a sub-agent's prompt and tool output as developer
   messages too. In the pilot's sessions that was 156 of 1,070 rows. The raw
   transcript's own marks decide (`recover._prompt`: not meta, not a result,
   not a notice); it exists for 38 of the 40 sessions. Rows the table splits
   out of one long typed message count as one message. *Trade-off:* for the 2
   sessions without a transcript, and for 15 rows found nowhere in theirs, the
   table's word stands.
3. **Messages in a row.** Two developer messages with no agent work between
   them (a split, or a message typed while the agent was working) are one
   message.
4. **The window** (above). *Trade-off:* the reviewer can miss something said
   many turns earlier, which the developer remembers. The window keeps the
   task and the agent's previous message to soften this.
5. **What is left out.** The agent's thinking (the developer did not see it;
   `recover.with_text`, as for v1.1's candidates); notices, command output,
   compaction summaries and a skill's text. Images the developer attached
   cannot be shown; the window says "[Image: ...]".
6. **Long sessions.** Each session gives at most its first 40 reports, which
   trims 3 sessions (110, 141 and 426 reports). Without the cap, one session
   would be half the pilot.
7. **One reviewer.** An outside reviewer, gpt-6-astra. True self-review needs
   the original agent, mostly Claude Opus 4.5 and 4.6, and there is no Claude
   budget. A "this is your own work" framing of the same model is a possible
   second arm, not built.
8. **The reviewer judges only the work after the request.** A problem counts
   only if its quote is in that work (`quote_in_work`). One quoting only the
   agent's previous message would be the previous report's problem counted
   twice.
9. **Pushback is SWE-chat's codebook.** A failure report counts even when the
   failing code is earlier work the window does not show (the agent may have
   built it before). Its objection kind is then often 'unclear'. *Trade-off:*
   the main measure, real errors caught, counts only pushbacks read as real
   errors; the others are reported apart.
10. **The merge looks back two reports.** A developer often objects a turn or
    two after the work, once they have tried it. *Trade-off:* a pushback about
    work from further back is the developer's alone, and the merge cannot know
    the reviewer caught it earlier.
11. **"Caught" means 'same'.** 'Related' (the same work, but a cause, symptom
    or neighbour of the fault) is reported apart, never counted as caught.
12. **Quotes are checked** with the judge's own `quote_appears`. A reviewer
    problem whose quote is not in the window is kept on its row but left out
    of the merge and the counts.
13. **One model throughout:** gpt-6-astra, on the Azure credits. Claude is
    refused by the client.
14. **Spend is priced as the spend guard prices it** (`harbor_spend.priced`):
    uncached input at the cache-write rate, an upper bound. The smoke runs
    cost $0.03 to $0.05 a call, about half the estimate.

## The smoke runs (10-03)

Three sessions, 12 handbacks, about $3 of Azure credits in all
(`runs/study-smoke*`). They found two defects, both fixed before the pilot:

- Rows SWE-chat files as the developer's that the developer did not write
  ended reports and became replies (assumption 2).
- The reply classifier read "onboarding a new repo gives an error with the db
  push" as no pushback, because a first wording of our own took a bug report
  for a new request. It now uses SWE-chat's codebook (assumption 9). On the
  smoke replies it agrees with SWE-chat's label on pushback or not 10 times
  in 12.

What a run looks like (smoke 3, too small to mean anything):
- 6 of 12 replies were pushback, and 4 of those were real errors;
- the reviewer caught 2 of the 4 as 'same';
- the reviewer listed 19 problems, of which 17 matched no pushback.

## The pilot

    .venv/bin/python scripts/study.py prepare --out runs/study-pilot        # free
    .venv/bin/python scripts/study.py estimate --run runs/study-pilot       # free
    # paid, gpt-6-astra on the Azure credits; source the .env first, ERRATA_PROVIDER=azure
    .venv/bin/python scripts/study.py review --run runs/study-pilot --max-usd 120 --concurrency 8
    .venv/bin/python scripts/study.py human  --run runs/study-pilot --max-usd 120 --concurrency 8
    .venv/bin/python scripts/study.py merge  --run runs/study-pilot --max-usd 120 --concurrency 8
    .venv/bin/python scripts/study.py tally  --run runs/study-pilot
    .venv/bin/python scripts/study.py sheet  --run runs/study-pilot

Size and cost: 40 sessions, 381 reports, about 220 pushbacks to merge. The
estimate is $80 as an upper bound, with real spend likely nearer $40. --max-usd
is the whole run's line, across all three stages. Review and human are
independent and can run side by side. Each stage resumes after an interruption
and asks errored rows again.

Then the hand check, then the full run (all 520 sessions with a real-error
pushback, or SWE-chat v2's), decided after the pilot.
