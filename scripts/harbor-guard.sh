#!/bin/bash
# A Harbor run's spend guard: every 10 minutes, the spend its trials and its
# grading recorded (`scripts/harbor_spend.py`); at the stop line, the run's own
# process groups are stopped, then its own containers -- never another
# tenant's.
#
#   STOP=2200 JOBS="jobs/b1-grok jobs/b1-dsp" PIDS="runs/b1-grok.pid runs/b1-dsp.pid" \
#     LEDGER=runs/b1-spend.ledger LOG=runs/b1-spend.log scripts/harbor-guard.sh   # while the Harbor jobs run
#   STOP=2200 JOBS="jobs/b1-grok jobs/b1-dsp" GRADED=runs/b1-grade PIDS=runs/b1-grade.pid \
#     LEDGER=runs/b1-spend.ledger LOG=runs/b1-spend.log scripts/harbor-guard.sh   # again, while grading runs
#
# Each guarded process is started with `scripts/guarded.sh`, which writes its
# pid file. One LEDGER for every phase keeps the whole run's spend in one
# total: the agents' and the grading's, whatever folders a later phase names.
# Every word of JOBS, PIDS and GRADED is read once, when the guard starts, and
# a pattern must match: start the guard after every job has made its folder,
# or name each one. The guard refuses to start (exit 2) unless the stop line is
# a number, every pid file names a live process group, every JOBS word is a
# Harbor job folder, every GRADED folder is a grading run that has begun (it
# waits up to 2 minutes for one), and a first tally runs: a guard that priced
# nothing used to say it was guarding (09-28 reviews). Once running, a tally
# that fails twice in a row stops the run as the stop line does. Ends when
# every group has exited.
cd "$(dirname "$0")/.." || exit 1
STOP="${STOP:?set STOP, the dollar line}"; LOG="${LOG:?set LOG}"; PIDS="${PIDS:?set PIDS, the pid files}"
JOBS="${JOBS:?set JOBS, the job folders}"; LEDGER="${LEDGER:?set LEDGER, the spend ledger}"
GRADED="${GRADED:-}"; PY="${PY:-.venv/bin/python}"
WAIT_S="${WAIT_S:-900}"; KILL_AFTER_S="${KILL_AFTER_S:-60}"; EVERY_S="${EVERY_S:-600}"; APPEAR_S="${APPEAR_S:-120}"
say() { echo "$*" | tee -a "$LOG"; }
refuse() { say "refused: $*"; exit 2; }
[[ "$STOP" =~ ^[0-9]+(\.[0-9]+)?$ ]] || refuse "the stop line is not a number: $STOP"
[ -x "$PY" ] || refuse "no Python at $PY"
shopt -s nullglob
# Every word must name something. Each is expanded on its own: expanded
# together, a pattern that matched nothing left the list without a word, and
# its job went unpriced or its group unstopped (09-28 review).
words() {
  local w m x ws; read -ra ws <<< "$1"
  for w in "${ws[@]}"; do m=($w); [ "${#m[@]}" -gt 0 ] || return 1; for x in "${m[@]}"; do printf '%s\n' "$x"; done; done
}
words "$PIDS" > /dev/null || refuse "a pid file pattern matches nothing: $PIDS"
mapfile -t pidfiles < <(words "$PIDS")
[ "${#pidfiles[@]}" -gt 0 ] || refuse "no pid file given: $PIDS"
words "$JOBS" > /dev/null || refuse "a job folder pattern matches nothing: $JOBS"
mapfile -t jobs < <(words "$JOBS")
for j in "${jobs[@]}"; do [ -f "$j/config.json" ] || refuse "not a Harbor job folder (no config.json): $j"; done
graded=()
begun() {   # every grading folder named, each with the tasks.jsonl a grading run writes first
  local g; words "$GRADED" > /dev/null || return 1
  mapfile -t graded < <(words "$GRADED"); [ "${#graded[@]}" -gt 0 ] || return 1
  for g in "${graded[@]}"; do [ -f "$g/tasks.jsonl" ] || return 1; done
}
if [ -n "$GRADED" ]; then
  waited=0
  until begun; do
    [ "$waited" -ge "$APPEAR_S" ] && refuse "no grading run begun at $GRADED after ${APPEAR_S}s (no tasks.jsonl)"
    sleep 5; waited=$((waited + 5))
  done
fi
groups=()
for f in "${pidfiles[@]}"; do
  g=$(tr -dc '0-9' < "$f")
  { [ -n "$g" ] && [ "$g" -gt 1 ]; } || refuse "$f holds no usable process group id ($g)"
  kill -0 -- "-$g" 2>/dev/null || refuse "$f names no live process group ($g)"
  groups+=("$g")
done
tally() {
  local args=(--jobs "${jobs[@]}" --ledger "$LEDGER" --stop "$STOP")
  [ "${#graded[@]}" -gt 0 ] && args+=(--graded "${graded[@]}")
  "$PY" scripts/harbor_spend.py "${args[@]}" >> "$LOG" 2>&1
}
tally; rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] || refuse "the first spend tally failed (exit $rc); see $LOG"
alive() {   # prunes groups that have ended, so a reused id is never signalled
  local live=() g
  for g in "${groups[@]}"; do kill -0 -- "-$g" 2>/dev/null && live+=("$g"); done
  groups=("${live[@]}"); [ "${#groups[@]}" -gt 0 ]
}
stop_run() {
  say "=== $1 $(date -u +%FT%TZ): stopping ${groups[*]}"
  # SIGTERM first: Harbor tears its trials down on it, as on an interrupt, and
  # a process started in the background by a script ignores SIGINT.
  alive && for g in "${groups[@]}"; do kill -TERM -- "-$g" 2>/dev/null; done
  waited=0
  while alive && [ "$waited" -lt "$WAIT_S" ]; do sleep 5; waited=$((waited + 5)); done
  if alive; then
    say "=== still running after ${WAIT_S}s: killing ${groups[*]}"
    for g in "${groups[@]}"; do kill -KILL -- "-$g" 2>/dev/null; done
    sleep "$KILL_AFTER_S"
  fi
  # This run's containers only: Harbor names each trial's compose project after
  # the trial's folder, "<trial>__env", sanitised as it sanitises it.
  for j in "${jobs[@]}"; do
    for t in "$j"/*/; do
      project=$("$PY" -c 'import re,sys; n=(sys.argv[1]+"__env").lower(); n=n if re.match(r"^[a-z0-9]",n) else "0"+n; print(re.sub(r"[^a-z0-9_-]","-",n))' "$(basename "$t")")
      ids=$(docker ps -q --filter "label=com.docker.compose.project=$project" 2>/dev/null)
      [ -n "$ids" ] && { say "stopping $project"; docker stop $ids >> "$LOG" 2>&1; }
    done
  done
  say "=== stopped $(date -u +%FT%TZ)"
  exit 0
}
say "=== guarding ${#groups[@]} process group(s) (${groups[*]}), ${#jobs[@]} job folder(s), ${#graded[@]} grading folder(s), stop line \$$STOP $(date -u +%FT%TZ)"
[ "$rc" -eq 3 ] && stop_run "STOP LINE \$$STOP REACHED"
failed=0
while true; do
  alive || { say "=== every guarded process has ended; guard ends $(date -u +%FT%TZ)"; exit 0; }
  sleep "$EVERY_S"
  tally; rc=$?
  [ "$rc" -eq 3 ] && stop_run "STOP LINE \$$STOP REACHED"
  if [ "$rc" -ne 0 ]; then
    failed=$((failed + 1))
    say "=== the spend tally failed (exit $rc), $failed time(s) in a row $(date -u +%FT%TZ)"
    [ "$failed" -ge 2 ] && stop_run "THE SPEND COULD NOT BE READ TWICE"
  else
    failed=0
  fi
done
