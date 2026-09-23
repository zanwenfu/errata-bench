#!/bin/bash
# Ask the judge to read each task's known pair many times over, so the one
# yes/no decision that admits a task -- and carries its three attempts with it
# -- is measured rather than assumed. No candidate runs; the known pair is two
# fixed strings from the transcript.
#
# Measured once in runs/cand-grok: its tasks.jsonl is byte-identical to
# cand-kimi's and cand-deepseek's (md5 047360a9...), so one measurement covers
# all three.
set -u
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
judge="${1:-gpt-6-astra}"; passes="${2:-12}"; conc="${3:-6}"
log="runs/gate-$(printf %s "$judge" | tr -c 'A-Za-z0-9._-' '_').log"
: > "$log"
for round in 1 2 3; do
  echo "=== round $round $(date '+%H:%M:%S')" >> "$log"
  ERRATA_PROVIDER=azure ERRATA_MODEL="$judge" ERRATA_JUDGE_MODEL="$judge" \
    ERRATA_TIMEOUT=900 ERRATA_MAX_RETRIES=5 \
    .venv/bin/python run.py gate --run runs/cand-grok --judge "$judge" \
      --passes "$passes" --concurrency "$conc" >> "$log" 2>&1
  left=$(grep -c '"error"' runs/cand-grok/gate.jsonl 2>/dev/null); left=${left:-0}
  echo "=== round $round done, $left errored" >> "$log"
  [ "$left" = "0" ] && break
done
echo "=== done $(date '+%H:%M:%S')" >> "$log"
