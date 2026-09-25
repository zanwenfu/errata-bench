#!/bin/bash
# D-45's spend guard: every 10 minutes, the spend from its rows' token counts;
# at the stop line, D-45's process groups are stopped, and only their
# containers -- never D-40's, which may still run beside it, nor another
# tenant's. Ends when the tests and every branch have said they are done.
cd /root/errata-bench-d45 || exit 1
prefix="${PREFIX:-d45}"; STOP="${STOP:-220}"; branches="${BRANCHES:-3}"
LOG="runs/$prefix-spend.log"; CHAIN="runs/$prefix-chain.log"
while true; do
  .venv/bin/python scripts/d40_spend.py --prefix "$prefix" --stop "$STOP" --code "$(git rev-parse --short=9 HEAD)" >> "$LOG" 2>&1
  if [ $? -eq 3 ]; then
    echo "=== STOP LINE REACHED; stopping D-45 $(date -u +%FT%TZ)" >> "$LOG"
    echo "=== stopped by the spend guard at \$$STOP $(date -u +%FT%TZ)" >> "$CHAIN"
    groups=$(cat "runs/$prefix-tests.pid" runs/"$prefix"-branch-*.pid 2>/dev/null)
    # Each run.py names its containers errata-<its pid>-...: collect the pids of
    # these groups before they are killed, and stop only their containers.
    pids=$(for g in $groups; do pgrep -g "$g"; done | paste -sd'|' -)
    for pgid in $groups; do kill -TERM -- "-$pgid" 2>/dev/null; done
    sleep 30
    for pgid in $groups; do kill -KILL -- "-$pgid" 2>/dev/null; done
    [ -n "$pids" ] && docker ps --format '{{.Names}}' | grep -E "^errata-($pids)-" | xargs -r docker stop >> "$LOG" 2>&1
    exit 0
  fi
  if [ "$(grep -c '^=== D-45 .* done' "$CHAIN" 2>/dev/null)" -ge $((branches + 1)) ]; then
    echo "=== run finished; guard ends $(date -u +%FT%TZ)" >> "$LOG"
    exit 0
  fi
  sleep 600
done
