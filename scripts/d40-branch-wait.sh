#!/bin/bash
# Wait for a paused candidate's quota to reach a threshold, then start its D-40
# branch and a spend guard for it:  ./d40-branch-wait.sh <candidate> <tokens/min>
# Probes every 15 minutes with a one-token request; gives up after 48 hours.
cd /root/errata-bench-d40 || exit 1
c="$1"; want="$2"; LOG=runs/d40-chain.log
for i in $(seq 1 192); do
  limit=$(.venv/bin/python scripts/d40_limit_of.py "$c" 2>/dev/null)
  if [ "${limit:-0}" -ge "$want" ]; then
    echo "=== $c's quota is now $limit tokens a minute; its branch starts $(date -u +%FT%TZ)" >> "$LOG"
    (setsid nohup scripts/d40-branch.sh "$c" > /dev/null 2>&1 < /dev/null &)
    sleep 5
    (STOP=2500 DONE_MARK="=== D-40 $c done" setsid nohup scripts/d40-guard.sh > /dev/null 2>&1 < /dev/null &)
    exit 0
  fi
  sleep 900
done
echo "=== $c's quota did not reach $want in 48 hours; its branch did not start $(date -u +%FT%TZ)" >> "$LOG"
