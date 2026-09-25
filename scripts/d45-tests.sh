#!/bin/bash
# D-45's own checks under view 2 (docs/research-log.md, D-45 and its amendment):
# of gpt-6-astra's checks in D-44, those whose prompt view 1 cut, asked again one
# reading each as D-44's were -- the rest copied by scripts/d45_tests_setup.py,
# run by d45-start.sh -- then the probes three times (scripts/probe_runs.py),
# which is what the first criterion counts. No candidate runs.
cd /root/errata-bench-d45 || exit 1
LOG="runs/d45-chain.log"
echo $$ > "runs/d45-tests.pid"
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
dir="runs/d45-astratests"
[ -d "$dir/rejudge/gpt-6-astra" ] || { say "$dir is not set up; stopping"; exit 1; }
say "D-45 astratests start at $(git rev-parse --short HEAD)"
scripts/rejudge-rounds.sh gpt-6-astra 3 1 "/root/errata-bench-d45/$dir" >> "$LOG" 2>&1
rc=$?
( set -a; . ./.env; set +a; ERRATA_PROVIDER=azure .venv/bin/python scripts/probe_runs.py gpt-6-astra \
    "$dir/rejudge/gpt-6-astra/probes.jsonl" --runs 3 ) >> "$LOG" 2>&1
say "D-45 astratests done, exit $rc"
