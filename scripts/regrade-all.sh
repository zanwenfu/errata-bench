#!/bin/bash
# Grade all eighty-one stored answers with one judge that answered none of them.
#
# Why: the three candidate runs were graded by different models -- grok's
# answers by Kimi, the other two by grok -- and those judges differ in
# strictness on exactly the honesty reading, so the rates cannot be compared as
# they stand (G-29). Pass/fail is unaffected; the judges agree on it
# answer-for-answer (R-18).
#
# gpt-6-astra is the judge because it wrote none of these answers, so nothing
# here is a model grading itself. It has to pass the same known-answer tests
# first -- the known pair in both orders, the two controls, the eight trace
# probes -- and the summary counts only the tasks where it did.
#
# Nothing is re-run and nothing in the runs themselves is touched: every grade
# lands under <run>/rejudge/gpt-6-astra/.
set -u
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

judge="${1:-gpt-6-astra}"
conc="${2:-6}"
# printf, not echo: echo appends a newline, and `tr -c` replaces it too, so
# the log was written to a name ending in an underscore.
log="runs/regrade-$(printf %s "$judge" | tr -c 'A-Za-z0-9._-' '_').log"

: > "$log"
for run in cand-grok cand-kimi cand-deepseek; do
  # Two rounds each: a grading that hits a rate limit leaves an error row,
  # which is dropped and retried on the next invocation.
  for round in 1 2; do
    echo "=== $run round $round $(date '+%H:%M:%S')" >> "$log"
    ERRATA_PROVIDER=azure ERRATA_MODEL="$judge" ERRATA_JUDGE_MODEL="$judge" \
      ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=5 \
      .venv/bin/python run.py rejudge --run "runs/$run" --judge "$judge" --concurrency "$conc" \
      >> "$log" 2>&1
    left=$(grep -c '"error"' "runs/$run/rejudge/$judge/attempts.jsonl" 2>/dev/null); left=${left:-0}
    echo "=== $run round $round done, $left gradings errored" >> "$log"
    [ "$left" = "0" ] && break
  done
done

echo "=== all three done $(date '+%H:%M:%S')" >> "$log"
for run in cand-grok cand-kimi cand-deepseek; do
  echo "--- $run" >> "$log"
  .venv/bin/python run.py judges --run "runs/$run" >> "$log" 2>&1
done
