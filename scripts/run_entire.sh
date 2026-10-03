#!/bin/bash
# One Entire draw (runs/<name>-first and runs/<name>-later, from scripts/draw_entire.py) through finding,
# screening, the build and admission, with v1's settings (#16, agreed with #17, 10-03):
#   - every model call gpt-6-astra on Azure credits (v1's finding and judging model);
#   - screening at --passes 3, settled by majority (method.md step 6);
#   - calibration, then the gate at --passes 7 on every built task, then the controls at --passes 3
#     (method.md step 8; every v1 run folder holds 7 gate readings a task, controls at 3);
#   - the first list's locate finished before the later list's, so a failed answer both share is the first's;
#   - the free round-3 preflight (scripts/preflight_entire.py) before any admission call;
#   - at the end, the tally (scripts/tally_entire.py): one task per session, the first pushback kept.
# Run it under the spend guard, with a stop line agreed beforehand:
#   scripts/guarded.sh runs/<name>.pid scripts/run_entire.sh <name>
set -u
name="${1:?usage: run_entire.sh <name>}"
cd "$(dirname "$0")/.." || exit 2
first="runs/$name-first"; later="runs/$name-later"
for d in "$first" "$later"; do
    [ -s "$d/moments.jsonl" ] || { echo "refused: $d/moments.jsonl is missing; draw it first (scripts/draw_entire.py)"; exit 2; }
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
stages() { "$PY" run.py stages --run "$@" --concurrency "$CONCURRENCY"; }

step "finding: $first through locate, $later through reading, side by side"
stages "$first" --through locate & a=$!
stages "$later" --through read & b=$!
wait "$a"; ra=$?; wait "$b"; rb=$?
[ "$ra" = 0 ] && [ "$rb" = 0 ] || { step "stopped: finding exited $ra and $rb"; exit 1; }
step "finding: $later's locate, after $first's"
stages "$later" --only locate || { step "stopped: $later's locate exited $?"; exit 1; }

for run in "$first" "$later"; do
    step "$run: signature"
    stages "$run" --only signature || { step "stopped: signature exited $?"; exit 1; }
    step "$run: the free pre-check"
    "$PY" scripts/prescreen_buildable.py "$run" || { step "stopped: the pre-check exited $?"; exit 1; }
    step "$run: screening at $SCREEN_PASSES readings"
    stages "$run" --only screen --passes "$SCREEN_PASSES" || { step "stopped: screening exited $?"; exit 1; }
    step "$run: build"
    stages "$run" --only build || { step "stopped: the build exited $?"; exit 1; }
    step "$run: the round-3 preflight"
    "$PY" scripts/preflight_entire.py "$run" || { step "stopped: the preflight found a task to read first"; exit 1; }
    step "$run: calibration"
    stages "$run" --only calibrate || { step "stopped: calibration exited $?"; exit 1; }
    step "$run: the gate, $GATE_PASSES readings of every built task"
    "$PY" run.py gate --run "$run" --judge "$JUDGE" --passes "$GATE_PASSES" --concurrency "$CONCURRENCY" \
        || { step "stopped: the gate exited $?"; exit 1; }
    step "$run: the controls at $CONTROL_PASSES readings"
    stages "$run" --only control --passes "$CONTROL_PASSES" || { step "stopped: the controls exited $?"; exit 1; }
done
step "the tally"
"$PY" scripts/tally_entire.py "runs/$name-tally.json" "$first:first" "$later:later"
step "done"
