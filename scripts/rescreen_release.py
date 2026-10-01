#!/usr/bin/env python3
"""Screen a frozen release's tasks again, on the conversation each candidate is shown (G-81, G-83, #17).

    scripts/rescreen_release.py <release dir> --out <dir> [--passes 3] [--concurrency 4] [--limit N]
                                [--only <task id>...]

v1.0's leak gate read each conversation cut to 60,000 characters while its
candidates read it whole (G-81), and 5 of its 7 repairs removed the developer's
request (G-83). This asks the screening stage's three gates again of each
frozen task -- is there a request, is the defect within its scope, does the
conversation already signal the trouble -- by the stage itself (`stage_screen`),
each settled by a majority of --passes readings. The gates read the
conversation the candidate is shown, with the agent's text put back, before any
repair. A leak is repaired again under today's rules, never by removing the
request, and checked again, and the request and its scope are asked again of
the repaired conversation.

Before anything is paid, each task's view, with its own repair applied, must
render its conversation.txt byte for byte: a task whose view differs would be
screened on a conversation its candidate is not shown, and the run is refused.

Each task becomes one row of <out>/signatures.jsonl, carrying its task id, its
fingerprint and its conversation's digest; the stage writes <out>/screened.jsonl,
each row with its own token use. The release is not changed:
`scripts/apply_rescreen.py` applies the verdicts. Needs the corpus and its
transcripts, not GitHub. Paid, on the screening model, ERRATA_MODEL, which must
be named: price it with `scripts/harbor_spend.py --admitted <out>`, and guard
it with `ADMITTED=<out> scripts/harbor-guard.sh`. Run again, it asks only what
is not yet done; an --out holding other tasks or task versions is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.spec import Task, fingerprint  # noqa: E402
from errata_bench.store import Paths, load, only_one  # noqa: E402


def rows_of(release: Path, only: set[str]) -> list[dict]:
    """One signatures row per frozen task: what the screening stage reads, and what applying it checks."""
    rows = []
    for d in sorted((release / "tasks").iterdir()):
        f = d / "grading" / "task.json"
        if not f.is_file() or (only and d.name not in only):
            continue
        task = Task.from_json(json.loads(f.read_text()))
        rows.append({
            "task_id": task.task_id, "session_id": task.session_id, "repo_id": task.repo_id,
            "turn_number": task.complaint_turn, "complaint": task.complaint_turn, "failed": task.failed_turn,
            "resolved": task.resolved_turn, "cut": task.cut_turn, "defect": task.defect, "kind": task.kind,
            "task_fingerprint": fingerprint(task),
            "conversation_sha256": hashlib.sha256((d / "conversation.txt").read_bytes()).hexdigest(),
            # The repair the release holds, under names the stage does not write:
            # it records its own in redacted_turns and rewritten_turns.
            "released_redacted_turns": task.redacted_turns,
            "released_rewritten_turns": task.rewritten_turns,
        })
    return rows


def unscreenable(release: Path, rows: list[dict]) -> dict[str, str]:
    """The tasks whose gates' view, with the task's own repair, does not render its conversation.txt."""
    from errata_bench.corpus.recover import recovered
    from errata_bench.corpus.turns import RECORD, RECORD_CHARS, build_excerpt, load_session_turns
    from errata_bench.stages.screening import screened_view

    turns = recovered(load_session_turns({r["session_id"] for r in rows}))
    bad = {}
    for r in rows:
        if not turns.get(r["session_id"]):
            bad[r["task_id"]] = "its session is not in the corpus"
            continue
        view = screened_view({"session_id": r["session_id"], "redacted_turns": r["released_redacted_turns"],
                              "rewritten_turns": r["released_rewritten_turns"]}, turns[r["session_id"]])
        shown = build_excerpt(view, r["cut"], max_chars=RECORD_CHARS, record=RECORD)
        if shown.encode("utf-8") != (release / "tasks" / r["task_id"] / "conversation.txt").read_bytes():
            bad[r["task_id"]] = "the gates' view does not render its conversation.txt"
    return bad


def summary(row: dict) -> str:
    """One task's verdicts, as a line."""
    if row.get("error"):
        return f"error: {row['error'][:100]}"
    if row.get("provider_refused"):
        return f"refused by the screening model: {row['provider_refused'][:100]}"
    leak = ("no leak" if not row.get("signals_trouble")
            else f"leaks ({row.get('redaction_outcome') or 'not repaired'})")
    return (f"request {'yes' if row.get('asks_for_something') else 'NO'} {row.get('asks_for_something_held', '')}, "
            f"in scope {'yes' if row.get('within_scope') else 'NO'} {row.get('within_scope_held', '')}, "
            f"{leak} {row.get('clean_held', '')}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args(argv)
    from errata_bench import llm as llm_mod

    llm_mod._load_dotenv()
    if not os.environ.get("ERRATA_MODEL"):
        print("refused: set ERRATA_MODEL to the screening model; nothing was read", file=sys.stderr)
        return 2
    try:
        llm_mod.provider()
        llm_mod.model_name()
    except RuntimeError as e:  # ClaudeRefused too
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if args.passes < 1 or args.passes % 2 == 0:
        print(f"refused: --passes {args.passes} has no majority; ask an odd number of times", file=sys.stderr)
        return 2
    rows = rows_of(args.release, set(args.only))
    if not rows:
        print(f"refused: no frozen tasks under {args.release / 'tasks'}", file=sys.stderr)
        return 2
    signatures = args.out / "signatures.jsonl"
    wanted = "".join(json.dumps(r) + "\n" for r in rows)
    # Written once, and the stage resumes on it: a folder screened on other tasks,
    # or on other versions of them, would report them done and screen nothing.
    if signatures.exists() and signatures.read_text() != wanted:
        print(f"refused: {args.out} holds a re-screen of other tasks or task versions than these; use a new --out; "
              f"nothing was read", file=sys.stderr)
        return 2
    bad = unscreenable(args.release, rows)
    gc.collect()
    if bad:
        print(f"refused: {len(bad)} task(s) would be screened on a conversation their candidate is not shown; "
              f"nothing was asked:" + "".join(f"\n  - {t}: {why}" for t, why in sorted(bad.items())),
              file=sys.stderr)
        return 2
    # Long prompts, read whole: a request cut off at the client's 120 seconds is
    # sent again and paid again (09-28 preflight).
    os.environ.setdefault("ERRATA_TIMEOUT", "900")
    os.environ.setdefault("ERRATA_MAX_RETRIES", "5")
    paths = Paths(args.out)
    if not signatures.exists():
        signatures.write_text(wanted)

    from errata_bench.stages.screening import stage_screen

    # One re-screen per folder: two would each pay for every row (10-01 review).
    with only_one(args.out, "re-screening a release"):
        progress = asyncio.run(stage_screen(paths, args.limit, args.concurrency, passes=args.passes))
    print(progress.line().strip())
    for note in progress.notes:
        print(f"  {note}")
    screened = {r.get("task_id"): r for r in load(paths.screened)}
    for r in rows:
        row = screened.get(r["task_id"])
        print(f"  {r['task_id']:46} {summary(row) if row else 'not screened yet'}")
    return 0 if progress.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
