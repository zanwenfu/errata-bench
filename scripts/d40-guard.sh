#!/bin/bash
# D-40's spend guard: every 10 minutes, the spend from the rows' token counts;
# at the stop line, the whole chain is stopped and its containers with it.
# Ends when the chain ends.
cd /root/errata-bench-d40 || exit 1
LOG=runs/d40-spend.log
STOP="${STOP:-1600}"
while true; do
  .venv/bin/python d40_spend.py --stop "$STOP" >> "$LOG" 2>&1
  if [ $? -eq 3 ]; then
    echo "=== STOP LINE REACHED; stopping the run $(date -u +%FT%TZ)" >> "$LOG"
    echo "=== stopped by the spend guard at \$$STOP $(date -u +%FT%TZ)" >> runs/d40-chain.log
    pgid=$(cat runs/d40-chain.pid 2>/dev/null)
    [ -n "$pgid" ] && kill -TERM -- "-$pgid" 2>/dev/null
    sleep 30
    [ -n "$pgid" ] && kill -KILL -- "-$pgid" 2>/dev/null
    # Only this run's containers: errata-<pid>-..., never another tenant's.
    docker ps --format '{{.Names}}' | grep '^errata-' | xargs -r docker stop >> "$LOG" 2>&1
    exit 0
  fi
  if grep -q "=== D-40 done" runs/d40-chain.log 2>/dev/null; then
    echo "=== run finished; guard ends $(date -u +%FT%TZ)" >> "$LOG"
    exit 0
  fi
  sleep 600
done
