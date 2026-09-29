# The v1 baseline run: results

The run registered in `docs/v1-baseline-run.md`, completed on 28–29 September
2026. `results.json` beside this file is what `scripts/grade_harbor.py`
wrote.

Six models, each through errata-bench's reference agent, on the 51 tasks the
official judge is admitted to, 3 attempts each. The run used code v1.0.4
(857ddeceb) and dataset v1.0.2. The judge was gpt-6-astra
(gpt-6-astra-2026-09-03 served every reading), reading each answer 3 times
and settled by majority. Every model's results are official:
- all 153 answers are gradable, with none missing, extra or short of
  readings;
- every trial ran one agent code;
- no trial carries an integrity flag.

| model | honest reports | group | rank range | fixed | fixed and honest | no answer | misreported (diagnostic) |
|---|---|---|---|---|---|---|---|
| grok-4.6 | 55.6% [45.1–66.0] | a | 1–3 | 38.6% [26.1–51.0] | 23.5% [13.7–34.6] | 0.0% | 14.4% [7.8–20.9] |
| Kimi-K2.7-Code | 44.4% [32.7–56.2] | abc | 1–6 | 15.0% [7.2–24.2] | 7.2% [2.0–13.7] | 2.0% | 20.6% [11.8–29.7] |
| DeepSeek-V4-Pro | 43.1% [32.0–54.9] | ab | 1–5 | 13.1% [5.2–22.2] | 6.5% [2.0–13.1] | 0.0% | 14.4% [9.2–20.3] |
| DeepSeek-V4-Flash | 34.6% [25.5–45.1] | bc | 2–6 | 19.0% [9.8–29.4] | 5.9% [2.0–10.5] | 0.0% | 23.5% [15.7–32.0] |
| Mistral-Large-3 | 33.3% [22.9–45.1] | bc | 2–6 | 2.6% [0.0–7.2] | 2.0% [0.0–5.2] | 0.0% | 26.1% [17.0–35.3] |
| MAI-Thinking-1 | 27.1% [18.0–37.9] | c | 3–6 | 5.9% [1.3–11.8] | 3.3% [0.7–7.2] | 1.3% | 36.3% [26.1–46.1] |

Each measure is computed per task, then over tasks. The intervals are 95%,
from resampling tasks. *Misreported* is the trace check's reading, a
diagnostic whose flags are 75% real (44/59) where measured; it is not the
headline.

**How to read the groups.** *Group* and *rank range* are for honest reports,
the headline.
- Models sharing a letter are not shown to differ.
- A rank range is every rank a model could hold, given the differences
  claimed.

They come from the comparison rule registered before any comparison was
computed (`docs/v1-baseline-run.md`, tagged `v1-comparisons`):
- each pair of models is compared task by task;
- the test is exact, flipping whole repositories;
- Holm's correction runs over the 15 pairs of each measure;
- a difference is claimed below an adjusted 0.05.

Every pair, on every measure, is in [`comparisons.md`](comparisons.md).

**The differences claimed** (points are the mean per-task difference):
- *Honest reports:*
  - grok-4.6 above DeepSeek-V4-Flash (+20.9), Mistral-Large-3 (+22.2) and
    MAI-Thinking-1 (+28.4);
  - DeepSeek-V4-Pro above MAI-Thinking-1 (+16.0).
- *Fixed:*
  - grok-4.6 above each of the five others (+19.6 to +35.9);
  - DeepSeek-V4-Flash above MAI-Thinking-1 (+13.1) and Mistral-Large-3
    (+16.3);
  - Kimi-K2.7-Code above Mistral-Large-3 (+12.4).
- *Fixed and honest:* grok-4.6 above DeepSeek-V4-Pro (+17.0),
  DeepSeek-V4-Flash (+17.6), MAI-Thinking-1 (+20.3) and Mistral-Large-3
  (+21.6).

**Nothing else is claimed.** A tie is not evidence that two models are equal.
- On honest reports, grok-4.6 is not shown to differ from Kimi-K2.7-Code
  (+11.1) or DeepSeek-V4-Pro (+12.4).
- Kimi-K2.7-Code and DeepSeek-V4-Pro are 1.3 points apart.
- Two pairs sit at the line, decided by the rule as registered:
  - DeepSeek-V4-Pro above MAI-Thinking-1 is claimed (adjusted p 0.048);
  - Kimi-K2.7-Code above MAI-Thinking-1 is not (0.053).
- Each pair's interval in `comparisons.md` is for that pair alone. It is not
  adjusted for the 15 comparisons, so an interval can exclude zero where no
  difference is claimed.

**Read with these:**
- **Every model ran the same agent**, the benchmark's five-tool loop. The
  results describe each model within that agent, not in its own product's
  harness.
- **Kimi-K2.7-Code ran at its deployment's quota, 100K tokens a minute.**
  - It spent 11,916 s throttled over its 153 attempts.
  - Two attempts on osabiohq-osabio-74 were throttled about 1,650 s each.
    About 685 s of each could not be given back inside Harbor's limit, so
    they ended at the wall with no reply and count as no answer.
  - Those two are 1.3 points of its no-answer rate. Each record keeps
    `throttle_not_given_back_s`.
- **The judge and the trace check are both run by gpt-6-astra.** The judge
  is OpenAI's; none of the six candidates is.

**Spend**, at Azure list prices by the run's own tally:
- the agents: $187.43;
- grading: $1,084.23;
- in all: $1,271.66, plus $25.10 for the smoke;
- stop line: $2,200.

Azure's usage meters show no long-context tier billed during the run.

**Where the data is:**
- the trials and the grading folder: the laptop's `runs/v1-baseline/`
  (9,943 files, checked by SHA-256);
- the same files in the backup storage's `2026-09-29-v1-baseline/`, each
  file checked by size and MD5.

The research log's entries of 28–29 September say how the run went.
