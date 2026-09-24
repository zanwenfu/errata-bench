#!/bin/bash
# D-40's Kimi-K2.7-Code branch, run on its own once its Azure quota is raised.
# Paused at 22:4x UTC on 09-24: at 50K tokens a minute, long tasks' 30K-token
# requests were throttled and attempts ran out of time for Azure's reasons, not
# the model's. Every Kimi answer collected under that quota is set aside in
# runs/d40-aborted-0924c; this starts the branch again from nothing, as the chain
# runs every candidate: 55 tasks x 3 attempts, gpt-6-astra's 3 readings in turn,
# then gpt-6-sol's 3 with its tests copied from d40-soltests.
cd /root/errata-bench-d40 || exit 1
LOG=runs/d40-chain.log
# Started by setsid, so this is a process group the spend guard can stop.
echo $$ > runs/d40-kimi.pid
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
c=Kimi-K2.7-Code
if [ -e "runs/d40-$c" ]; then say "runs/d40-$c exists; stopping"; exit 1; fi
mkdir -p "runs/d40-$c" && cp runs/d40-base/{tasks,calibration,controls,gate}.jsonl "runs/d40-$c/"
say "D-40 Kimi branch start at $(git rev-parse --short HEAD)"
GRADE_PASSES=3 ERRATA_MAX_CONTAINERS=1 scripts/attempt-rounds.sh "d40-$c" "$c" gpt-6-astra 1 4 3
say "$c attempts and gpt-6-astra grading done"
until [ -e runs/d40-soltests.done ] || [ -e runs/d40-soltests.failed ]; do sleep 60; done
if [ -e runs/d40-soltests.done ]; then
  TESTS_FROM=/root/errata-bench-d40/runs/d40-soltests \
    scripts/rejudge-rounds.sh gpt-6-sol 2 3 "/root/errata-bench-d40/runs/d40-$c" >> "$LOG" 2>&1
  say "$c gpt-6-sol grading done, exit $?"
else
  say "$c: gpt-6-sol grading not started, its tests failed"
fi
say "D-40 Kimi done"
