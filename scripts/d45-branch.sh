#!/bin/bash
# One of D-45's candidates (docs/research-log.md, D-45):
#   scripts/d45-branch.sh <candidate> <concurrency>
# The D-44 answers that view 1 cut, read again under view 2: gpt-6-astra's three
# readings as the run's own grading, then gpt-6-sol's three. The answers view 1
# did not cut already carry D-44's readings (scripts/d45_setup.py), so the
# grading stage and the re-grade driver, which both resume, read only the cut.
cd /root/errata-bench-d45 || exit 1
c="${1:?candidate}"; conc="${2:-2}"
LOG="runs/d45-chain.log"
echo $$ > "runs/d45-branch-$c.pid"
say() { echo "=== $* $(date -u +%FT%TZ)" >> "$LOG"; }
say "D-45 $c branch start at $(git rev-parse --short HEAD)"
.venv/bin/python run.py stages --run "runs/d45-$c" --only grade --grade-concurrency "$conc" --passes 3 \
  >> "runs/d45-$c.log" 2>&1
say "$c gpt-6-astra grading done, exit $?"
scripts/rejudge-rounds.sh gpt-6-sol "$conc" 3 "/root/errata-bench-d45/runs/d45-$c" >> "$LOG" 2>&1
say "$c gpt-6-sol grading done, exit $?"
say "D-45 $c done"
