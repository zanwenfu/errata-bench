#!/bin/bash
# Run one candidate's attempts until nothing is left errored, at most three
# rounds, then grade what it collected. Errored rows are dropped and retried by
# the stage itself.
#
# Candidates and grading are separate stages now, so this counts errors in
# answers.jsonl -- candidate failures live there, and attempts.jsonl holds only
# scores. Counting the old file meant the loop read zero errors from a file the
# attempt stage no longer writes to, stopped after one round, and left the run
# ungraded with nothing saying so.
# Optional: $6 attempts per task (default 3); ERRATA_MAX_CONTAINERS in the
# environment overrides the one-container default, per process.
run="$1"; cand="$2"; judge="$3"; conc="${4:-4}"; gconc="${5:-$conc}"; repeats="${6:-3}"
cd "$(dirname "$0")/.."
for round in 1 2 3; do
  echo "=== round $round $(date '+%H:%M:%S')" >> "runs/$run.log"
  ERRATA_PROVIDER=azure ERRATA_MODEL="$cand" ERRATA_JUDGE_MODEL="$judge" \
    ERRATA_MAX_CONTAINERS="${ERRATA_MAX_CONTAINERS:-1}" ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=5 \
    .venv/bin/python run.py stages --run "runs/$run" --only attempt --repeats "$repeats" --concurrency "$conc" \
    >> "runs/$run.log" 2>&1
  left=$(grep -c '"error"' "runs/$run/answers.jsonl" 2>/dev/null); left=${left:-0}
  echo "=== round $round done, $left candidates errored" >> "runs/$run.log"
  [ "$left" = "0" ] && break
done

# Grading waits on the provider, not on this laptop, so it runs wider than the
# candidates did. Two rounds: a rate-limited grade leaves an error row, which is
# dropped and retried exactly as an errored attempt is.
for round in 1 2; do
  echo "=== grade round $round $(date '+%H:%M:%S')" >> "runs/$run.log"
  ERRATA_PROVIDER=azure ERRATA_MODEL="$cand" ERRATA_JUDGE_MODEL="$judge" \
    ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=5 \
    .venv/bin/python run.py stages --run "runs/$run" --only grade --grade-concurrency "$gconc" \
    >> "runs/$run.log" 2>&1
  left=$(grep -c '"error"' "runs/$run/attempts.jsonl" 2>/dev/null); left=${left:-0}
  echo "=== grade round $round done, $left gradings errored" >> "runs/$run.log"
  [ "$left" = "0" ] && break
done

# What was collected against what was read. A run that stopped between the two
# stages otherwise reads as a smaller run rather than an unfinished one.
.venv/bin/python run.py stages --run "runs/$run" --only report >> "runs/$run.log" 2>&1
echo "=== done $(date '+%H:%M:%S')" >> "runs/$run.log"
