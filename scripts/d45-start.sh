#!/bin/bash
# Start D-45 on the VPS, once D-44 is done: copy D-44's answers and the readings
# view 1 left whole (scripts/d45_setup.py), then read the cut answers again, the
# candidates side by side, under the spend guard. Each in its own process group.
cd /root/errata-bench-d45 || exit 1
grep -q "^=== D-44 Mistral-Large-3 done" /root/errata-bench-d44/runs/d44-chain.log \
  && grep -q "^=== D-44 grok-4.6 done" /root/errata-bench-d44/runs/d44-chain.log \
  && grep -q "^=== D-44 DeepSeek-V4-Pro done" /root/errata-bench-d44/runs/d44-chain.log \
  || { echo "D-44 is not done; D-45 starts after it"; exit 1; }
for c in grok-4.6 DeepSeek-V4-Pro Mistral-Large-3; do
  .venv/bin/python scripts/d45_setup.py "/root/errata-bench-d44/runs/d44-$c" "runs/d45-$c" --judge gpt-6-sol \
    >> runs/d45-setup.txt 2>&1 || { echo "setup failed for $c"; exit 1; }
done
setsid nohup scripts/d45-branch.sh grok-4.6 3 > /dev/null 2>&1 < /dev/null &
setsid nohup scripts/d45-branch.sh DeepSeek-V4-Pro 2 > /dev/null 2>&1 < /dev/null &
setsid nohup scripts/d45-branch.sh Mistral-Large-3 2 > /dev/null 2>&1 < /dev/null &
sleep 5
setsid nohup scripts/d45-guard.sh > /dev/null 2>&1 < /dev/null &
echo "started d45"; cat runs/d45-setup.txt | grep -v "^  re-graded"
