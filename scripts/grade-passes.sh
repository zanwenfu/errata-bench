#!/bin/bash
# Read every stored answer in a run again -- up to <passes> readings each --
# then rewrite the report from the settled verdicts (D-30). Runs no candidate
# and no container. Readings already stored are kept and counted: the resume
# key is (task, run, pass), and a row with no `pass` is reading 0.
run="$1"; judge="$2"; passes="${3:-3}"; gconc="${4:-4}"
cd "$(dirname "$0")/.."
cand=$(.venv/bin/python -c "import json; print(json.loads(open('runs/$run/answers.jsonl').readline())['model'])") || exit 1
# Two rounds: a rate-limited grading leaves an error row, which the stage
# drops and retries, exactly as attempt-rounds.sh relies on.
for round in 1 2; do
  echo "=== grade --passes $passes round $round $(date '+%H:%M:%S')" >> "runs/$run.log"
  ERRATA_PROVIDER=azure ERRATA_MODEL="$cand" ERRATA_JUDGE_MODEL="$judge" \
    ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=5 \
    .venv/bin/python run.py stages --run "runs/$run" --only grade --passes "$passes" \
      --grade-concurrency "$gconc" >> "runs/$run.log" 2>&1
  left=$(grep -c '"error"' "runs/$run/attempts.jsonl" 2>/dev/null); left=${left:-0}
  echo "=== grade round $round done, $left gradings errored" >> "runs/$run.log"
  [ "$left" = "0" ] && break
done
.venv/bin/python run.py stages --run "runs/$run" --only report >> "runs/$run.log" 2>&1
echo "=== done $(date '+%H:%M:%S')" >> "runs/$run.log"
