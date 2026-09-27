#!/usr/bin/env python3
"""Freeze a run's tasks, so they can be run without the corpus or GitHub (v1 step 2).

    scripts/freeze_tasks.py <run dir> <out dir> [--history 100] [--max-git-mb 200] [--only <task id>...]
                            [--check-against <run dir>] [--scratch <dir>]

Each task in <run dir>/tasks.jsonl is frozen into <out dir>/tasks/<task id>/ by
`errata_bench.release.freeze`, which checks the working copy against the tree
every attempt has started from, file by file. <out dir>/manifest.json lists what
was frozen and what could not be, with why; freezing some tasks again (--only) updates
their rows and keeps the rest. With --check-against, each frozen
conversation is compared with the one that run's candidates were shown, as
stored on its answer rows. Needs the corpus and GitHub; makes no model calls.
The exit code is 0 only if every task was frozen and every check matched.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.corpus.sessions import CORPUS, load_repos  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402
from errata_bench.project import code_version  # noqa: E402
from errata_bench.release.freeze import HISTORY, MAX_GIT_MB, freeze  # noqa: E402
from errata_bench.score.attempt import turns_of  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, completed  # noqa: E402


def branches(session_ids: set[str]) -> dict[str, str]:
    """Each session's branch, as the corpus records it."""
    import pyarrow.parquet as pq

    table = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "branch"])
    return {s: b for s, b in zip(table.column("session_id").to_pylist(), table.column("branch").to_pylist())
            if s in session_ids and b}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--history", type=int, default=HISTORY)
    ap.add_argument("--max-git-mb", type=float, default=MAX_GIT_MB)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--check-against", type=Path)
    ap.add_argument("--scratch", type=Path)
    args = ap.parse_args(argv)
    tasks = read(Paths(args.run).tasks)
    if args.only:
        tasks = [t for t in tasks if t.task_id in set(args.only)]
    if not tasks:
        ap.error(f"no tasks to freeze in {args.run}")
    seen = {}
    if args.check_against:
        for a in completed(Paths(args.check_against).answers):
            if a.get("transcript") and a.get("task_id") not in seen:
                seen[a["task_id"]] = a["transcript"]
    turns = load_session_turns({t.session_id for t in tasks})
    named = branches({t.session_id for t in tasks})
    repos = load_repos()
    rows, bad = [], 0
    for t in tasks:
        f = freeze(t, turns_of(turns, t), args.out / "tasks" / t.task_id, branch=named.get(t.session_id),
                   history=args.history, max_git_mb=args.max_git_mb, scratch=args.scratch,
                   language=getattr(repos.get(t.repo_id), "language", None))
        row = {"task_id": t.task_id, "ok": f.ok, "files": f.files, "branch": f.branch, "workdir": f.workdir}
        if not f.ok:
            row["reason"], row["differ"] = f.reason, f.differ[:20]
        elif t.task_id in seen:
            # As bytes: a text-mode read turns the carriage returns some outputs hold into newlines.
            shown = (args.out / "tasks" / t.task_id / "conversation.txt").read_bytes().decode("utf-8")
            row["conversation_as_shown"] = shown == seen[t.task_id]
        bad += (not f.ok) or row.get("conversation_as_shown") is False
        rows.append(row)
        print(f"  {'ok  ' if f.ok else 'FAIL'} {t.task_id:42} {f.files:6} files  {f.branch[:28]:28} {f.workdir[:48]}"
              + ("" if f.ok else f"\n       {f.reason}")
              + ("\n       the conversation differs from the one its candidates were shown"
                 if row.get("conversation_as_shown") is False else ""))
    manifest = args.out / "manifest.json"
    kept = [r for r in (json.loads(manifest.read_text())["tasks"] if manifest.exists() else [])
            if r["task_id"] not in {x["task_id"] for x in rows}]
    manifest.write_text(json.dumps({
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_version": code_version(),
        "from_run": str(args.run), "history": args.history, "max_git_mb": args.max_git_mb,
        "tasks": sorted(kept + rows, key=lambda r: r["task_id"])}, indent=1) + "\n")
    print(f"{sum(r['ok'] for r in rows)} of {len(rows)} tasks frozen into {args.out / 'tasks'}"
          + (f"; {sum(r.get('conversation_as_shown') is True for r in rows)} conversations match what "
             f"{args.check_against.name}'s candidates were shown" if args.check_against else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
