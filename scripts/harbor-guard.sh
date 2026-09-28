#!/bin/bash
# A Harbor run's spend guard: every 10 minutes, the spend its trials and its
# grading recorded (`scripts/harbor_spend.py`); at the stop line, the run's own
# process groups are stopped, then its own containers -- never another
# tenant's.
#
#   STOP=2200 JOBS="jobs/b1-*" GRADED="runs/b1-grade" PIDS="runs/b1-*.pid" \
#     LEDGER=runs/b1-spend.ledger LOG=runs/b1-spend.log scripts/harbor-guard.sh
#
# Start it after the guarded processes, each launched with `scripts/guarded.sh`
# (which writes the pid files). It refuses to start unless every pid file names
# a live process group and every JOBS pattern names an existing job folder: a
# guard that guards nothing used to exit at once, saying the run had ended
# (09-28 review). GRADED may not exist yet. Ends when every group has exited.
cd "$(dirname "$0")/.." || exit 1
STOP="${STOP:?set STOP, the dollar line}"; LOG="${LOG:?set LOG}"; PIDS="${PIDS:?set PIDS, the pid files}"
JOBS="${JOBS:?set JOBS, the job folders}"; LEDGER="${LEDGER:?set LEDGER, the spend ledger}"
GRADED="${GRADED:-}"; PY="${PY:-.venv/bin/python}"; WAIT_S="${WAIT_S:-900}"
shopt -s nullglob
# shellcheck disable=SC2206  # the patterns are meant to expand
pidfiles=($PIDS); jobs=($JOBS); graded=($GRADED)
say() { echo "$*" | tee -a "$LOG"; }
[ "${#pidfiles[@]}" -gt 0 ] || { say "refused: no pid file matches $PIDS"; exit 2; }
[ "${#jobs[@]}" -gt 0 ] || { say "refused: no job folder matches $JOBS"; exit 2; }
groups=()
for f in "${pidfiles[@]}"; do
  g=$(tr -dc '0-9' < "$f")
  { [ -n "$g" ] && kill -0 -- "-$g" 2>/dev/null; } || { say "refused: $f names no live process group ($g)"; exit 2; }
  groups+=("$g")
done
say "=== guarding ${#groups[@]} process group(s) (${groups[*]}), ${#jobs[@]} job folder(s), stop line \$$STOP $(date -u +%FT%TZ)"
alive() { for g in "${groups[@]}"; do kill -0 -- "-$g" 2>/dev/null && return 0; done; return 1; }
while true; do
  # shellcheck disable=SC2206
  jobs=($JOBS); graded=($GRADED)
  args=(--jobs "${jobs[@]}" --ledger "$LEDGER" --stop "$STOP")
  [ "${#graded[@]}" -gt 0 ] && args+=(--graded "${graded[@]}")
  $PY scripts/harbor_spend.py "${args[@]}" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -eq 3 ]; then
    say "=== STOP LINE \$$STOP REACHED $(date -u +%FT%TZ): interrupting ${groups[*]}"
    for g in "${groups[@]}"; do kill -INT -- "-$g" 2>/dev/null; done
    # Harbor tears its trials down on an interrupt; a second signal cuts that
    # short and can leave an agent running in its container, still paying.
    waited=0
    while alive && [ "$waited" -lt "$WAIT_S" ]; do sleep 15; waited=$((waited + 15)); done
    if alive; then
      say "=== still running after ${WAIT_S}s: terminating"
      for g in "${groups[@]}"; do kill -TERM -- "-$g" 2>/dev/null; done
      sleep 60
      for g in "${groups[@]}"; do kill -KILL -- "-$g" 2>/dev/null; done
    fi
    # This run's containers only: Harbor names each trial's compose project
    # after the trial's folder, "<trial>__env", lower-cased.
    for j in "${jobs[@]}"; do
      for t in "$j"/*/; do
        project=$(basename "$t" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9_-]/-/g')__env
        ids=$(docker ps -q --filter "label=com.docker.compose.project=$project")
        [ -n "$ids" ] && { say "stopping $project"; docker stop $ids >> "$LOG" 2>&1; }
      done
    done
    say "=== stopped $(date -u +%FT%TZ)"
    exit 0
  fi
  [ "$rc" -eq 0 ] || say "=== the spend tally failed (exit $rc) $(date -u +%FT%TZ); see above"
  if ! alive; then
    say "=== every guarded process has ended; guard ends $(date -u +%FT%TZ)"
    exit 0
  fi
  sleep 600
done
