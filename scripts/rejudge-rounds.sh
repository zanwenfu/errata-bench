#!/bin/bash
# Re-judge run directories with a second judge, in rounds, until nothing errored.
#
#   ERRATA_API=responses TESTS_FROM=/abs/runs/grid1-DeepSeek-V4-Pro \
#     scripts/rejudge-rounds.sh claude-opus-5 2 3 \
#       /abs/runs/grid1-DeepSeek-V4-Pro /abs/runs/grid1-grok-4.6 /abs/runs/grid1-Kimi-K2.7-Code
#
# Arguments: the judge (on Azure, the deployment), the concurrency, the number
# of readings per question (D-35 asks for 3), then the run directories, which
# are done one after another in the order given.
#
# Why rounds: a call that is still rate-limited after its retries leaves an
# error row, and an error row is not a reading -- `completed` drops it and the
# next invocation asks again. claude-opus-5 on Azure allows 40,000 tokens and
# 40 requests a minute (its own x-ratelimit headers, 09-23), and one judge or
# trace-check prompt is thousands of tokens, so a round at any useful
# concurrency can end with rows errored. Each retry round waits $PAUSE seconds
# first, so the minute's allowance has renewed.
#
# Done means the round exited cleanly AND left no errored row in the judge's
# calibration, controls or grades, counted by the harness's own `_succeeded`.
# Either alone is not enough: a killed round leaves no error rows, and a clean
# exit can still have written them. A run not done after $ROUNDS rounds stops
# the whole script, since the next run would only compete for the same quota.
#
# TESTS_FROM: the judge's calibration, controls and instrument checks depend
# on the task and nothing else, so a grid whose directories hold the same
# tasks.jsonl needs them once. Before a run's first round, when its judge directory has neither
# file yet, they are copied from TESTS_FROM's judge directory -- only if the two
# tasks.jsonl are byte-identical and TESTS_FROM's tests finished with nothing
# errored; otherwise the script stops rather than pay for them again or copy
# answers to a different question.
#
# Only the caller's ERRATA_API survives the .env. Nothing is written outside
# <run>/rejudge/<judge>/, and one log per run beside the run directory.
set -u
cd "$(dirname "$0")/.."
if [ $# -lt 4 ]; then
  echo "usage: $0 <judge> <concurrency> <passes> <run dir>..." >&2
  exit 2
fi
api="${ERRATA_API:-}"
set -a; . ./.env; set +a
[ -n "$api" ] && export ERRATA_API="$api"

judge="$1"; conc="$2"; passes="$3"; shift 3
rounds="${ROUNDS:-8}"
pause="${PAUSE:-90}"
from="${TESTS_FROM:-}"

errored() {  # rows in the given files that did not finish, by the harness's rule
  .venv/bin/python - "$@" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from errata_bench.store import _succeeded, load
print(sum(not _succeeded(r) for f in sys.argv[1:] if Path(f).exists() for r in load(Path(f))))
EOF
}

for run in "$@"; do
  run="${run%/}"
  dir="$run/rejudge/$judge"
  log="$(dirname "$run")/rejudge-$(printf %s "$judge" | tr -c 'A-Za-z0-9._-' '_')-$(basename "$run").log"
  if [ -n "$from" ] && [ "$run" != "${from%/}" ] \
     && [ ! -e "$dir/calibration.jsonl" ] && [ ! -e "$dir/controls.jsonl" ]; then
    src="${from%/}/rejudge/$judge"
    if ! cmp -s "$run/tasks.jsonl" "${from%/}/tasks.jsonl"; then
      echo "=== $(basename "$run"): tasks.jsonl differs from TESTS_FROM's; not copying its tests" | tee -a "$log"
      exit 1
    fi
    bad=$(errored "$src/calibration.jsonl" "$src/controls.jsonl" "$src/instrument.jsonl")
    if [ ! -s "$src/calibration.jsonl" ] || [ ! -s "$src/controls.jsonl" ] || [ "$bad" != 0 ]; then
      echo "=== $(basename "$run"): TESTS_FROM's tests are missing or have $bad errored rows; not copying" | tee -a "$log"
      exit 1
    fi
    mkdir -p "$dir"
    cp "$src/calibration.jsonl" "$src/controls.jsonl" "$dir/"
    # The instrument checks depend on the task alone too (D-36 A3), where the
    # source has them; a judge from before they existed has none to share.
    [ -s "$src/instrument.jsonl" ] && cp "$src/instrument.jsonl" "$dir/"
    echo "=== $(basename "$run"): calibration and controls copied from $(basename "${from%/}") (same tasks.jsonl)" >> "$log"
  fi
  for round in $(seq 1 "$rounds"); do
    echo "=== $(basename "$run") round $round $(date -u '+%F %H:%M:%S') UTC" >> "$log"
    ERRATA_PROVIDER=azure ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=10 \
      nice -n 10 .venv/bin/python run.py rejudge --run "$run" --judge "$judge" \
      --passes "$passes" --concurrency "$conc" >> "$log" 2>&1
    status=$?
    left=$(errored "$dir/calibration.jsonl" "$dir/controls.jsonl" "$dir/instrument.jsonl" "$dir/attempts.jsonl")
    echo "=== $(basename "$run") round $round exited $status, $left rows errored $(date -u '+%H:%M:%S')" >> "$log"
    [ "$status" = 0 ] && [ "$left" = 0 ] && continue 2
    [ "$round" -lt "$rounds" ] && sleep "$pause"
  done
  echo "=== $(basename "$run") NOT DONE after $rounds rounds; stopping" >> "$log"
  exit 1
done
