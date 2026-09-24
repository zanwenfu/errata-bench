#!/bin/bash
# D-40, the confirmatory run (pre-registered at 6f7b933a7).
# 55 tasks x 6 candidates x 3 attempts, in containers on this VPS.
# gpt-6-astra: 3 readings per answer, asked in turn (B-256), right after each candidate's attempts.
# gpt-6-sol: its own tests run once, on d40-soltests (no answers), while the attempts run;
# then 3 readings per answer in each candidate's directory, its tests copied (TESTS_FROM).
cd /root/errata-bench-d40 || exit 1
LOG=runs/d40-chain.log
# Started by setsid, so this is the process group the spend guard stops.
echo $$ > runs/d40-chain.pid
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
C="grok-4.6 Kimi-K2.7-Code DeepSeek-V4-Pro DeepSeek-V4-Flash Mistral-Large-3 MAI-Thinking-1"
say "D-40 start at $(git rev-parse --short HEAD)"
for d in $C; do
  if [ -e "runs/d40-$d" ]; then say "runs/d40-$d exists; stopping"; exit 1; fi
done
# d40-soltests resumes if it is there: the second judge's tests depend on the
# tasks alone, and it holds no answers.
if [ -e runs/d40-soltests ] && ! cmp -s runs/d40-soltests/tasks.jsonl runs/d40-base/tasks.jsonl; then
  say "runs/d40-soltests holds other tasks; stopping"; exit 1
fi
for d in soltests $C; do
  [ -e "runs/d40-$d" ] && continue
  mkdir -p "runs/d40-$d" && cp runs/d40-base/{tasks,calibration,controls,gate}.jsonl "runs/d40-$d/"
done

# The second judge's tests: calibration, controls and probes on the 55 tasks, once.
( if scripts/rejudge-rounds.sh gpt-6-sol 4 3 /root/errata-bench-d40/runs/d40-soltests >> "$LOG" 2>&1; then
    touch runs/d40-soltests.done; say "gpt-6-sol tests done"
  else
    touch runs/d40-soltests.failed; say "gpt-6-sol tests FAILED"
  fi ) &

# grok's attempts take minutes each and set the run's length: three containers for it,
# one for each of the others, eight in all on a machine another tenant shares.
for c in $C; do
  ( n=1; [ "$c" = grok-4.6 ] && n=3
    GRADE_PASSES=3 ERRATA_MAX_CONTAINERS=$n scripts/attempt-rounds.sh "d40-$c" "$c" gpt-6-astra "$n" 4 3
    say "$c attempts and gpt-6-astra grading done"
    until [ -e runs/d40-soltests.done ] || [ -e runs/d40-soltests.failed ]; do sleep 60; done
    if [ -e runs/d40-soltests.done ]; then
      TESTS_FROM=/root/errata-bench-d40/runs/d40-soltests \
        scripts/rejudge-rounds.sh gpt-6-sol 2 3 "/root/errata-bench-d40/runs/d40-$c" >> "$LOG" 2>&1
      say "$c gpt-6-sol grading done, exit $?"
    else
      say "$c: gpt-6-sol grading not started, its tests failed"
    fi ) &
done
wait
say "D-40 done"
