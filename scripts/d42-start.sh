#!/bin/bash
# Start D-42 on the VPS: its instrument checks, the three candidates side by
# side, and its spend guard, each in its own process group.
#   scripts/d42-start.sh            the run
#   SMOKE=1 scripts/d42-start.sh    the smoke pass: two tasks, one gpt-6-sol reading
cd /root/errata-bench-d42 || exit 1
if [ -n "${SMOKE:-}" ]; then
  export PREFIX=d42smoke BASE=runs/d42smoke-base SOL_TESTS_FROM="" SOL_PASSES=1 STOP=40
fi
setsid nohup scripts/d42-tests.sh > /dev/null 2>&1 < /dev/null &
setsid nohup scripts/d42-branch.sh grok-4.6 3 > /dev/null 2>&1 < /dev/null &
setsid nohup scripts/d42-branch.sh DeepSeek-V4-Pro 2 > /dev/null 2>&1 < /dev/null &
setsid nohup scripts/d42-branch.sh Mistral-Large-3 2 > /dev/null 2>&1 < /dev/null &
sleep 5
setsid nohup scripts/d42-guard.sh > /dev/null 2>&1 < /dev/null &
echo "started ${PREFIX:-d42}"
