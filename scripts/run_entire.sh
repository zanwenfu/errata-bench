#!/bin/bash
# One Entire draw (runs/<name>-first and runs/<name>-later, from scripts/draw_entire.py) through finding,
# screening, the build and admission, with v1's settings (#16, agreed with #17, 10-03):
#   - every model call gpt-6-astra on Azure credits (v1's finding and judging model), at concurrency 3;
#   - screening at --passes 3, settled by majority (method.md step 6);
#   - calibration, the controls at --passes 3, then the gate at --passes 7 on every built task, in the order of
#     v1's scripts/admit-chain.sh (method.md step 8);
#   - the first list's locate finished before the later list's, so a failed answer both share is the first's;
#   - the free round-3 preflight (scripts/preflight_entire.py) before any admission call;
#   - at the end, the tally (scripts/tally_entire.py): one task per session, the first pushback kept.
# Each step is judged as admit-chain.sh judged it: by the rows it left in error, and run again, up to three times,
# since every stage retries its errored rows -- not by calibration's or the controls' exit code, which is 1 on a
# verdict. Two differences from admit-chain.sh, both stricter, both from the 10-03 review:
#   - an admission step is done only when every task has been read as many times as asked
#     (`tally_entire.py --short`), and any other step only when it also exits 0: a stage that refuses, or crashes,
#     writes no error row;
#   - a step still not done after three tries stops the run, where admit-chain.sh went on to the next and left
#     its tasks unadmitted without a word. Run again, it resumes: no stage asks again for what it has.
# Run it under the spend guard, with a stop line agreed beforehand:
#   scripts/guarded.sh runs/<name>.pid scripts/run_entire.sh <name>
set -u
name="${1:?usage: run_entire.sh <name>}"
cd "$(dirname "$0")/.." || exit 2
first="runs/$name-first"; later="runs/$name-later"
for d in "$first" "$later"; do
    [ -f "$d/moments.jsonl" ] || { echo "refused: $d/moments.jsonl is missing; draw it first (scripts/draw_entire.py)"; exit 2; }
done
[ -f "$HOME/errata-bench/.env" ] || { echo "refused: no key file at \$HOME/errata-bench/.env"; exit 2; }
set -a; . "$HOME/errata-bench/.env"; set +a
unset ERRATA_ALLOW_CLAUDE
# v1's settings. A paid step below takes them from here, never from a default.
JUDGE=gpt-6-astra; SCREEN_PASSES=3; GATE_PASSES=7; CONTROL_PASSES=3; CONCURRENCY=3
export ERRATA_PROVIDER=azure ERRATA_MODEL="$JUDGE" ERRATA_JUDGE_MODEL="$JUDGE" \
       PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
[ -n "${ERRATA_CORPUS:-}" ] || { echo "refused: ERRATA_CORPUS names no corpus"; exit 2; }
PY="${PY:-$HOME/errata-bench/.venv/bin/python}"
step() { echo "== $(date -u +%H:%M:%S) $*"; }
# Every paid command names its provider and model on the command itself (guard 121, D-45's lesson).
stages() { ERRATA_PROVIDER=azure ERRATA_MODEL="$JUDGE" ERRATA_JUDGE_MODEL="$JUDGE" \
    "$PY" run.py stages --run "$@" --concurrency "$CONCURRENCY"; }
gate() { ERRATA_PROVIDER=azure ERRATA_MODEL="$JUDGE" ERRATA_JUDGE_MODEL="$JUDGE" \
    "$PY" run.py gate --run "$1" --judge "$JUDGE" --passes "$GATE_PASSES" --concurrency "$CONCURRENCY"; }

# Rows a step left in error across its files, by the store's own rule (`store.rows._succeeded`, which also reads
# a located row's "error: ..." reason as one), and a build's transient refusals, which the next build retries.
errors() {
    "$PY" - "$@" <<'PY'
import sys
from pathlib import Path
from errata_bench.stages.building import TRANSIENT
from errata_bench.store.rows import _succeeded, load
run, n = Path(sys.argv[1]), 0
for name in sys.argv[2:]:
    for row in load(run / name):
        n += (TRANSIENT in str(row.get("reason") or "")) if name == "rejections.jsonl" else not _succeeded(row)
print(n)
PY
}

# attempt <run> "<its files>" <admission step: calibration|controls|gate, or -> <command...>
attempt() {
    local run=$1 files=$2 part=$3 try code left short
    shift 3
    for try in 1 2 3; do
        step "$run: $* (try $try)"
        "$@"; code=$?
        left=$(errors "$run" $files) || left=unknown
        if [ "$part" = - ]; then
            short=$code
        else
            "$PY" scripts/tally_entire.py --short "$run" "$part"; short=$?
        fi
        step "$run: exited $code, $left rows in error, shortfall check $short"
        [ "$left" = 0 ] && [ "$short" = 0 ] && return 0
        [ "$try" = 3 ] || sleep "${RETRY_PAUSE:-60}"
    done
    step "stopped: $run is not done after three tries of: $*"
    exit 1
}

FIND="triaged.jsonl readings.jsonl trajectories.jsonl"
step "finding: $first through locate, $later through reading, side by side"
attempt "$first" "$FIND" - stages "$first" --through locate & a=$!
attempt "$later" "triaged.jsonl readings.jsonl" - stages "$later" --through read & b=$!
wait "$a"; ra=$?; wait "$b"; rb=$?
[ "$ra" = 0 ] && [ "$rb" = 0 ] || { step "stopped: finding exited $ra and $rb"; exit 1; }
step "finding: $later's locate, after $first's"
attempt "$later" "$FIND" - stages "$later" --only locate

for run in "$first" "$later"; do
    attempt "$run" "signatures.jsonl" - stages "$run" --only signature
    attempt "$run" "" - "$PY" scripts/prescreen_buildable.py "$run"
    attempt "$run" "screened.jsonl" - stages "$run" --only screen --passes "$SCREEN_PASSES"
    attempt "$run" "rejections.jsonl" - stages "$run" --only build
    step "$run: the round-3 preflight"
    "$PY" scripts/preflight_entire.py "$run" || { step "stopped: the preflight found a fault to fix first"; exit 1; }
    attempt "$run" "calibration.jsonl" calibration stages "$run" --only calibrate
    attempt "$run" "controls.jsonl" controls stages "$run" --only control --passes "$CONTROL_PASSES"
    attempt "$run" "gate.jsonl" gate gate "$run"
done
step "the tally"
"$PY" scripts/tally_entire.py "runs/$name-tally.json" "$first:first" "$later:later" || { step "stopped: the tally refused"; exit 1; }
step "done"
