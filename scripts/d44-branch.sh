#!/bin/bash
# One of D-44's candidates (docs/research-log.md, D-44):
#   scripts/d44-branch.sh <candidate> <concurrency>
# 55 tasks x 1 attempt on the current harness (record 2, calls record 2), gpt-6-astra's three
# readings under trace rules 5 and judge rules 2, then gpt-6-sol's three.
# gpt-6-sol's own admission tests are not repeated: the re-grade driver wants
# them present, so D-40's are copied in (TESTS_FROM), and nothing in D-44 reads
# them.
#
# PREFIX, BASE, SOL_TESTS_FROM and SOL_PASSES exist for the smoke pass: two
# tasks, gpt-6-sol's tests asked afresh (they are cheap on two), one reading.
cd /root/errata-bench-d44 || exit 1
c="${1:?candidate}"; conc="${2:-2}"
prefix="${PREFIX:-d44}"; base="${BASE:-runs/d44-base}"
sol_from="${SOL_TESTS_FROM-/root/errata-bench-d40/runs/d40-soltests}"; sol_passes="${SOL_PASSES:-3}"
LOG="runs/$prefix-chain.log"
# Started by setsid, so this is a process group the spend guard can stop.
echo $$ > "runs/$prefix-branch-$c.pid"
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
if [ -e "runs/$prefix-$c" ]; then say "runs/$prefix-$c exists; stopping"; exit 1; fi
mkdir -p "runs/$prefix-$c" && cp "$base"/{tasks,calibration,controls,gate}.jsonl "runs/$prefix-$c/"
say "D-44 $c branch start at $(git rev-parse --short HEAD) ($prefix)"
GRADE_PASSES=3 ERRATA_MAX_CONTAINERS="$conc" scripts/attempt-rounds.sh "$prefix-$c" "$c" gpt-6-astra "$conc" 4 1
say "$c attempts and gpt-6-astra grading done"
TESTS_FROM="$sol_from" scripts/rejudge-rounds.sh gpt-6-sol 2 "$sol_passes" "/root/errata-bench-d44/runs/$prefix-$c" >> "$LOG" 2>&1
say "$c gpt-6-sol grading done, exit $?"
say "D-44 $c done"
