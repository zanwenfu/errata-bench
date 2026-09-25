#!/bin/bash
# D-45's analysis, as pre-registered and amended: do D-44's criteria hold when
# the graders see the whole record (view 2)? No model calls.
#
#   scripts/d45_analysis.sh [out dir]        (default: results/d45)
#
# Over the 165 D-44 answers: D-44's readings for those view 1 left whole, D-45's
# for the rest (runs/d45-<model>, set up by scripts/d45_setup.py).
#   1. the controls and the 35 probes, under view 2, by gpt-6-astra
#      (runs/d45-astratests: D-44's checks view 1 left whole, the rest asked
#      again; scripts/d42_checks.py --rules 5 --probes 35 -> criterion1.txt);
#   2. the trace check's flags, at least 90% real (flags/, flag_sample.py);
#   3. the judges' agreement on `misreported`, pooled, kappa at least 0.6;
#   4. the judge's unverified-claim calls, at least 90% real (judge-flags/,
#      judge_sample.py).
# A packet byte for byte one D-44's reading read keeps its verdicts
# (scripts/packet_reuse.py; the lists in flags/reuse.json and
# judge-flags/reuse.json). Also reported, not tested: extras.txt
# (scripts/d45_extras.py) and each candidate's rates under each judge.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT="${1:-results/d45}"
mkdir -p "$OUT"
RUNS=""
for c in grok-4.6 DeepSeek-V4-Pro Mistral-Large-3; do
  [ -f "runs/d45-$c/tasks.jsonl" ] || { echo "runs/d45-$c is missing" >&2; exit 1; }
  RUNS="$RUNS runs/d45-$c"
done
[ -d runs/d45-astratests ] || { echo "runs/d45-astratests is missing" >&2; exit 1; }
py=.venv/bin/python
$py scripts/d42_checks.py runs/d45-astratests $RUNS --rules 5 --probes 35 > "$OUT/criterion1.txt" 2>&1
$py scripts/judge_agreement.py --first-judge gpt-6-astra --judge gpt-6-sol $RUNS > "$OUT/agreement.txt" 2>&1
$py scripts/grid_table.py $RUNS > "$OUT/table-gpt-6-astra.txt" 2>&1
$py scripts/grid_table.py $RUNS --judge gpt-6-sol > "$OUT/table-gpt-6-sol.txt" 2>&1
$py scripts/d45_extras.py $RUNS > "$OUT/extras.txt" 2>&1
rm -rf "$OUT/flags" "$OUT/judge-flags"
$py scripts/flag_sample.py gpt-6-astra "$OUT/flags" $RUNS > "$OUT/flags.txt" 2>&1
$py scripts/judge_sample.py "$OUT/judge-flags" $RUNS > "$OUT/judge-flags.txt" 2>&1
$py scripts/packet_reuse.py compare "$OUT/flags" results/d44/flags >> "$OUT/flags.txt" 2>&1
$py scripts/packet_reuse.py compare "$OUT/judge-flags" results/d44/judge-flags >> "$OUT/judge-flags.txt" 2>&1
echo "written to $OUT. Read the packets listed as new in reuse.json; carry the others' verdicts with" \
     "scripts/packet_reuse.py carry, then tally with scripts/flag_tally.py as for D-44."
