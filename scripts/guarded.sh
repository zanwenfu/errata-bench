#!/bin/bash
# Run a command in a process group of its own, with that group's id written to
# <pidfile> from inside it, for `scripts/harbor-guard.sh` to stop.
#
#   nohup scripts/guarded.sh runs/b1-grok.pid harbor run ... > runs/b1-grok.log 2>&1 < /dev/null &
#
# `setsid cmd & echo $! > pidfile` records the wrong id in a shell with job
# control: the background job leads its own group, so setsid forks and `$!` is
# the parent that exits (09-28 review). Here the id is the one the command
# itself runs under: setsid makes the shell below a group's leader, and the
# command replaces it, keeping that id. Nothing runs unless the id was written.
#
# A command started in the background by a shell without job control -- a
# script, `ssh host '... &'` -- inherits SIGINT ignored, and nothing can undo
# that from bash; the guard therefore stops a run with SIGTERM, which Harbor
# handles as an interrupt (09-28 review).
set -u
pidfile="${1:?usage: guarded.sh <pidfile> <command>...}"; shift
[ "$#" -gt 0 ] || { echo "guarded.sh: no command given" >&2; exit 2; }
exec setsid --wait bash -c 'echo "$$" > "$0" && exec "$@"' "$pidfile" "$@"
