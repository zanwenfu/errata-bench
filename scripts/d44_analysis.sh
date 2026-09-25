#!/bin/bash
# D-44's analysis, as pre-registered: are the trace check's fifth rules and the
# judge's third rules repaired? No model calls.
#
#   scripts/d44_analysis.sh [out dir]        (default: results/d44)
#
# The four criteria (docs/research-log.md, D-44):
#   1. the controls and the 33 probes, under the new instrument, by gpt-6-astra
#      (scripts/d42_checks.py --rules 5 --probes 33 -> criterion1.txt);
#   2. the trace check's flags, at least 90% real: drawn here into flags/ by
#      scripts/flag_sample.py, read twice, and tallied by scripts/flag_tally.py;
#   3. the judges' agreement on `misreported`, pooled, kappa at least 0.6
#      (agreement.txt);
#   4. the judge's unverified-claim calls, at least 90% real: drawn here into
#      judge-flags/ by scripts/judge_sample.py (D-43's method), read twice, and
#      tallied by scripts/flag_tally.py.
# Also reported, not tested: each candidate's rates under each judge, and what
# scripts/d42_checks.py prints beside criterion 1. The run is frozen at tag
# d44-run.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT="${1:-results/d44}"
mkdir -p "$OUT"
RUNS=""
for c in grok-4.6 DeepSeek-V4-Pro Mistral-Large-3; do
  [ -f "runs/d44-$c/tasks.jsonl" ] || { echo "runs/d44-$c is missing" >&2; exit 1; }
  RUNS="$RUNS runs/d44-$c"
done
py=.venv/bin/python
$py scripts/d42_checks.py runs/d44-astratests $RUNS --rules 5 --probes 33 \
  --sol-probes runs/d44smoke2-grok-4.6 runs/d44smoke2-DeepSeek-V4-Pro runs/d44smoke2-Mistral-Large-3 \
  > "$OUT/criterion1.txt" 2>&1
$py scripts/judge_agreement.py --first-judge gpt-6-astra --judge gpt-6-sol $RUNS > "$OUT/agreement.txt" 2>&1
$py scripts/grid_table.py $RUNS > "$OUT/table-gpt-6-astra.txt" 2>&1
$py scripts/grid_table.py $RUNS --judge gpt-6-sol > "$OUT/table-gpt-6-sol.txt" 2>&1
rm -rf "$OUT/flags" "$OUT/judge-flags"
$py scripts/flag_sample.py gpt-6-astra "$OUT/flags" $RUNS > "$OUT/flags.txt" 2>&1
$py scripts/judge_sample.py "$OUT/judge-flags" $RUNS > "$OUT/judge-flags.txt" 2>&1
echo "written to $OUT. Once read: $py scripts/flag_tally.py $OUT/flags/sample.json <first> --second <second>" \
     "--adjudicated <settled> (criterion 2), and the same over $OUT/judge-flags (criterion 4)"
