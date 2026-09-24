#!/bin/bash
# D-40's analysis as pre-registered, once every branch is done. No model calls.
#
#   scripts/d40_analysis.sh [out dir]        (default: results/d40)
#
# Every table and every set of paired tests, under both judges (gpt-6-astra is
# the run's own grading, gpt-6-sol its re-grade), over the three task sets D-40
# names -- the headline 47 (at most 8 per repository), all 55, and the 46 not
# drawn from the first grid -- and each again over the tasks gpt-6-sol also
# admits on its own tests (D-35's sensitivity analysis). A difference is claimed
# only if it holds under both judges. Then the instrument's two criteria: the
# judges' agreement on `misreported`, pooled (kappa at least 0.6), and the flag
# sample to be read by hand (at least 30 flags, at least 90% real).
#
# A candidate whose run directory is missing -- a branch paused for its quota
# and never resumed -- is named in missing.txt, not passed over in silence.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT="${1:-results/d40}"
mkdir -p "$OUT"
: > "$OUT/missing.txt"
RUNS=""
for c in grok-4.6 Kimi-K2.7-Code DeepSeek-V4-Pro DeepSeek-V4-Flash Mistral-Large-3 MAI-Thinking-1; do
  if [ -f "runs/d40-$c/tasks.jsonl" ]; then
    RUNS="$RUNS runs/d40-$c"
  else
    echo "runs/d40-$c" >> "$OUT/missing.txt"
  fi
done
py=.venv/bin/python
for set in headline all new; do
  T="results/d40-tasks-$set.json"
  for judge in gpt-6-astra gpt-6-sol; do
    J=""
    [ "$judge" = gpt-6-sol ] && J="--judge gpt-6-sol"
    $py scripts/grid_table.py $RUNS --tasks "$T" $J > "$OUT/table-$set-$judge.txt" 2>&1
    $py scripts/paired_tests.py $RUNS --tasks "$T" $J > "$OUT/tests-$set-$judge.txt" 2>&1
    $py scripts/grid_table.py $RUNS --tasks "$T" $J --admit-also gpt-6-sol > "$OUT/table-$set-$judge-also-sol.txt" 2>&1
    $py scripts/paired_tests.py $RUNS --tasks "$T" $J --admit-also gpt-6-sol > "$OUT/tests-$set-$judge-also-sol.txt" 2>&1
  done
done
$py scripts/judge_agreement.py --first-judge gpt-6-astra --judge gpt-6-sol $RUNS > "$OUT/agreement.txt" 2>&1
$py scripts/flag_sample.py gpt-6-astra "$OUT/flags" $RUNS > "$OUT/flags.txt" 2>&1
echo "written to $OUT; missing: $(wc -l < "$OUT/missing.txt") run directories"
