#!/bin/bash
# D-42's analysis, as pre-registered: is the repaired instrument repaired? No
# model calls.
#
#   scripts/d42_analysis.sh [out dir]        (default: results/d42)
#
# The three criteria (docs/research-log.md, D-42):
#   1. the controls and probes, under the new instrument, by gpt-6-astra
#      (scripts/d42_checks.py -> criterion1.txt);
#   2. the flags, at least 90% real: the draw is redone here and must be byte
#      for byte the sample that was read (results/d42-flags/sample.json); the
#      readings are tallied into results/d42-criterion2-flags.md by
#      scripts/flag_tally.py, as the last line printed says;
#   3. the judges' agreement on `misreported`, pooled, kappa at least 0.6
#      (agreement.txt).
# Also reported, not tested: each candidate's rates under each judge (one
# attempt per task is not powered for a comparison), and what
# scripts/d42_checks.py prints beside criterion 1.
#
# The scoring code has not changed since D-42's own commit (f6794d1) except
# for B-263's refusal of a session missing from the corpus, which no D-42
# answer meets, so this runs at any later commit.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT="${1:-results/d42}"
mkdir -p "$OUT"
RUNS=""
for c in grok-4.6 DeepSeek-V4-Pro Mistral-Large-3; do
  [ -f "runs/d42-$c/tasks.jsonl" ] || { echo "runs/d42-$c is missing" >&2; exit 1; }
  RUNS="$RUNS runs/d42-$c"
done
py=.venv/bin/python
$py scripts/d42_checks.py runs/d42-astratests $RUNS \
  --sol-probes runs/d42smoke-grok-4.6 runs/d42smoke-DeepSeek-V4-Pro runs/d42smoke-Mistral-Large-3 \
  > "$OUT/criterion1.txt" 2>&1
$py scripts/judge_agreement.py --first-judge gpt-6-astra --judge gpt-6-sol $RUNS > "$OUT/agreement.txt" 2>&1
$py scripts/grid_table.py $RUNS > "$OUT/table-gpt-6-astra.txt" 2>&1
$py scripts/grid_table.py $RUNS --judge gpt-6-sol > "$OUT/table-gpt-6-sol.txt" 2>&1
rm -rf "$OUT/flags"
$py scripts/flag_sample.py gpt-6-astra "$OUT/flags" $RUNS > "$OUT/flags.txt" 2>&1
if cmp -s "$OUT/flags/sample.json" results/d42-flags/sample.json; then
  echo "the flag draw reproduces the sample that was read" >> "$OUT/flags.txt"
else
  echo "THE FLAG DRAW DIFFERS from results/d42-flags/sample.json, the sample that was read" >> "$OUT/flags.txt"
fi
tail -1 "$OUT/flags.txt"
echo "written to $OUT; criterion 2: $py scripts/flag_tally.py results/d42-flags/sample.json results/d42-flags/first" \
     "--second results/d42-flags/second/readings --adjudicated results/d42-flags/second/adjudicated.json" \
     "--out results/d42-criterion2-flags.md"
