#!/bin/bash
# D-44's instrument checks, on the new instrument (D-44): gpt-6-astra's own
# calibration, controls, instrument checks and probes, one reading each, over
# D-40's 55 tasks; then the probes three more times (scripts/probe_runs.py),
# which is what D-44's first criterion counts. No candidate runs.
cd /root/errata-bench-d44 || exit 1
prefix="${PREFIX:-d44}"; base="${BASE:-runs/d44-base}"
LOG="runs/$prefix-chain.log"
echo $$ > "runs/$prefix-tests.pid"
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
dir="runs/$prefix-astratests"
if [ -e "$dir" ]; then say "$dir exists; stopping"; exit 1; fi
mkdir -p "$dir" && cp "$base"/{tasks,calibration,controls,gate}.jsonl "$dir/"
say "D-44 astratests start at $(git rev-parse --short HEAD) ($prefix)"
scripts/rejudge-rounds.sh gpt-6-astra 3 1 "/root/errata-bench-d44/$dir" >> "$LOG" 2>&1
rc=$?
( set -a; . ./.env; set +a; ERRATA_PROVIDER=azure .venv/bin/python scripts/probe_runs.py gpt-6-astra \
    "$dir/rejudge/gpt-6-astra/probes.jsonl" --runs 3 ) >> "$LOG" 2>&1
say "D-44 astratests done, exit $rc"
