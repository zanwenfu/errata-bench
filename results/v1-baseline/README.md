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

| model | honest reports | fixed | fixed and honest | no answer | misreported (diagnostic) |
|---|---|---|---|---|---|
| grok-4.6 | 55.6% [45.1–66.0] | 38.6% [26.1–51.0] | 23.5% [13.7–34.6] | 0.0% | 14.4% [7.8–20.9] |
| Kimi-K2.7-Code | 44.4% [32.7–56.2] | 15.0% [7.2–24.2] | 7.2% [2.0–13.7] | 2.0% | 20.6% [11.8–29.7] |
| DeepSeek-V4-Pro | 43.1% [32.0–54.9] | 13.1% [5.2–22.2] | 6.5% [2.0–13.1] | 0.0% | 14.4% [9.2–20.3] |
| DeepSeek-V4-Flash | 34.6% [25.5–45.1] | 19.0% [9.8–29.4] | 5.9% [2.0–10.5] | 0.0% | 23.5% [15.7–32.0] |
| Mistral-Large-3 | 33.3% [22.9–45.1] | 2.6% [0.0–7.2] | 2.0% [0.0–5.2] | 0.0% | 26.1% [17.0–35.3] |
| MAI-Thinking-1 | 27.1% [18.0–37.9] | 5.9% [1.3–11.8] | 3.3% [0.7–7.2] | 1.3% | 36.3% [26.1–46.1] |

Each measure is computed per task, then over tasks. The intervals are 95%,
from resampling tasks. *Misreported* is the trace check's reading, a
diagnostic whose flags are 75% real (44/59) where measured; it is not the
headline.

**Read with these:**
- **No comparison is claimed.** The rows are ordered by honest reports only
  to be read. No rule for calling two models different is registered yet,
  and the intervals overlap widely.
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
