#!/bin/bash
# Carry a run's screened moments through to admission: build, calibrate,
# controls (3 readings) and the repeated gate (7 readings). No candidate runs.
#
#   scripts/admit-chain.sh runs/later-sample
#
# Waits for any stage already running on the run to finish first, since each
# stage takes the run lock. Model calls go to the configured model (on Azure,
# ERRATA_PROVIDER=azure and the .env). Logs beside the run: <run>.admit.log.
set -u
cd "$(dirname "$0")/.."
run="${1:?usage: $0 <run dir>}"
[ -f "$run/screened.jsonl" ] || { echo "no screened.jsonl in $run" >&2; exit 2; }
set -a; . ./.env; set +a
export ERRATA_PROVIDER="${ERRATA_PROVIDER:-azure}"
log="$run.admit.log"
while pgrep -f "run.py stages .*--run $run( |$)" > /dev/null; do sleep 30; done
judge="${JUDGE:-gpt-6-astra}"
steps=("stages --only build" "stages --only calibrate" "stages --only control --passes 3" "gate --passes 7 --judge $judge")
[ -n "${FROM:-}" ] && steps=("${steps[@]:$FROM}")   # FROM=2 resumes at the controls
for step in "${steps[@]}"; do
  echo "=== $step $(date -u '+%H:%M:%S')" >> "$log"
  # A stage exits 1 when any row "failed", and for calibration and the
  # controls a failed row is usually a verdict -- a task whose known pair the
  # judge cannot read -- not a crash. The rows say which; the chain goes on.
  nice -n 10 .venv/bin/python run.py $step --run "$run" --concurrency 3 >> "$log" 2>&1
  echo "=== $step exited $?" >> "$log"
done
echo "=== done $(date -u '+%H:%M:%S')" >> "$log"
