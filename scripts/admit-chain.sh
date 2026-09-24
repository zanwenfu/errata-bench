#!/bin/bash
# Carry a run's moments through to admission: screening (when not yet done),
# build, calibrate, controls (3 readings) and the repeated gate (7 readings).
# No candidate runs.
#
#   scripts/admit-chain.sh /abs/path/to/runs/later-sample
#
# Run it from a pinned worktree of a commit, with an absolute run path: a stage
# imports modules as it starts, and editing src/ under a running chain crashed
# one on 09-24 (B-251).
#
# Each step first waits for the run's lock, so it never starts beside a stage
# already running there; waiting on a process name missed `--run=` forms and
# absolute paths. A step that leaves rows in error -- a dropped connection, a
# laptop that slept -- is run again, up to three times, because every stage
# retries its errored rows; the exit code cannot say this, since calibration
# and the controls exit 1 on verdicts too. The steps are numbered 0 screen,
# 1 build, 2 calibrate, 3 control, 4 gate: FROM=2 resumes at calibration, and
# SCREEN=1 re-runs the screening a run already has. Calibration, the controls
# and the gate read with one judge, $JUDGE (default gpt-6-astra). Model calls
# go to the configured model (on Azure, ERRATA_PROVIDER=azure and the .env).
# Logs beside the run: <run>.admit.log.
set -u
cd "$(dirname "$0")/.."
run="${1:?usage: $0 <run dir>}"
set -a; . ./.env; set +a
export ERRATA_PROVIDER="${ERRATA_PROVIDER:-azure}"
judge="${JUDGE:-gpt-6-astra}"
export ERRATA_JUDGE_MODEL="${ERRATA_JUDGE_MODEL:-$judge}"
log="$run.admit.log"
py=.venv/bin/python

wait_lock() {   # block until nothing holds the lock, then let go of it
  $py -c 'import fcntl, sys; fcntl.flock(open(sys.argv[1], "a+"), fcntl.LOCK_EX)' "$1"
}

errors() {      # rows the step left in error, across its files
  $py - "$run" "$@" <<'PY'
import json, sys
from pathlib import Path
run, n = Path(sys.argv[1]), 0
for name in sys.argv[2:]:
    path = run / name
    if not path.exists():
        continue
    for line in path.open():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if name == "rejections.jsonl":
            n += "could not build the tree" in (row.get("reason") or "")
        else:
            n += bool(row.get("error"))
print(n)
PY
}

steps=("stages --through screen --passes 3|triaged.jsonl readings.jsonl trajectories.jsonl signatures.jsonl screened.jsonl"
       "stages --only build|rejections.jsonl"
       "stages --only calibrate|calibration.jsonl"
       "stages --only control --passes 3|controls.jsonl"
       "gate --passes 7 --judge $judge|gate.jsonl")
start=1
{ [ ! -f "$run/screened.jsonl" ] || [ -n "${SCREEN:-}" ]; } && start=0
start="${FROM:-$start}"
for entry in "${steps[@]:$start}"; do
  step="${entry%%|*}"
  outputs="${entry#*|}"
  lock="$run/run.lock"
  [ "${step%% *}" = gate ] && lock="$run/gate.lock"
  for try in 1 2 3; do
    wait_lock "$lock"
    echo "=== $step (try $try) $(date -u '+%H:%M:%S')" >> "$log"
    nice -n 10 $py run.py $step --run "$run" --concurrency 3 >> "$log" 2>&1
    code=$?
    left=$(errors $outputs)
    echo "=== $step exited $code, $left rows in error" >> "$log"
    [ "$left" = 0 ] && break
    sleep 60
  done
done
echo "=== done $(date -u '+%H:%M:%S')" >> "$log"
