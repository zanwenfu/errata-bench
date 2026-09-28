#!/bin/bash
# A Harbor run's spend guard: every 10 minutes, the spend its trials and its
# grading recorded (`scripts/harbor_spend.py`); at the stop line, the run's own
# process groups are interrupted, then stopped -- never another tenant's.
#
#   STOP=2200 JOBS="jobs/b1-*" GRADED="runs/b1-grade" PIDS="runs/b1-*.pid" LOG=runs/b1-spend.log \
#     scripts/harbor-guard.sh
#
# Each pid file holds the process-group id of one `harbor run` or grading
# process, written when it was started (`setsid ... & echo $! > runs/<name>.pid`).
# Ends when every pid file's group has exited.
cd "$(dirname "$0")/.." || exit 1
STOP="${STOP:?set STOP, the dollar line}"; LOG="${LOG:?set LOG}"; PIDS="${PIDS:?set PIDS, the pid files}"
PY="${PY:-.venv/bin/python}"
while true; do
  # shellcheck disable=SC2086  # the globs are meant to expand
  $PY scripts/harbor_spend.py --jobs $JOBS --graded $GRADED --stop "$STOP" >> "$LOG" 2>&1
  if [ $? -eq 3 ]; then
    echo "=== STOP LINE \$$STOP REACHED $(date -u +%FT%TZ)" >> "$LOG"
    # shellcheck disable=SC2086
    groups=$(cat $PIDS 2>/dev/null)
    for pgid in $groups; do kill -INT -- "-$pgid" 2>/dev/null; done
    sleep 60
    for pgid in $groups; do kill -TERM -- "-$pgid" 2>/dev/null; done
    exit 0
  fi
  alive=0
  # shellcheck disable=SC2086
  for pgid in $(cat $PIDS 2>/dev/null); do kill -0 -- "-$pgid" 2>/dev/null && alive=1; done
  if [ "$alive" -eq 0 ]; then
    echo "=== every guarded process has ended; guard ends $(date -u +%FT%TZ)" >> "$LOG"
    exit 0
  fi
  sleep 600
done
