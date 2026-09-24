#!/bin/bash
# D-40's spend guard: every 10 minutes, the spend from the rows' token counts;
# at the stop line, the whole chain is stopped and its containers with it.
# Ends when the chain ends.
cd /root/errata-bench-d40 || exit 1
LOG=runs/d40-spend.log
STOP="${STOP:-1600}"
# What in the chain log ends the guard: the chain's own last line by default,
# the Kimi branch's when it guards that (scripts/d40-kimi.sh).
DONE_MARK="${DONE_MARK:-=== D-40 done}"
while true; do
  .venv/bin/python scripts/d40_spend.py --stop "$STOP" >> "$LOG" 2>&1
  if [ $? -eq 3 ]; then
    echo "=== STOP LINE REACHED; stopping the run $(date -u +%FT%TZ)" >> "$LOG"
    echo "=== stopped by the spend guard at \$$STOP $(date -u +%FT%TZ)" >> runs/d40-chain.log
    # The chain's process group, and the Kimi branch's when it runs apart.
    groups=$(cat runs/d40-chain.pid runs/d40-kimi.pid 2>/dev/null)
    for pgid in $groups; do kill -TERM -- "-$pgid" 2>/dev/null; done
    sleep 30
    for pgid in $groups; do kill -KILL -- "-$pgid" 2>/dev/null; done
    # Only this run's containers: errata-<pid>-..., never another tenant's.
    docker ps --format '{{.Names}}' | grep '^errata-' | xargs -r docker stop >> "$LOG" 2>&1
    exit 0
  fi
  if grep -qF "$DONE_MARK" runs/d40-chain.log 2>/dev/null; then
    echo "=== run finished; guard ends $(date -u +%FT%TZ)" >> "$LOG"
    exit 0
  fi
  sleep 600
done
