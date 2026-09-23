"""Rebuild each task's tree as an attempt would, and check it against its conversation.

    .venv/bin/python scripts/consistency.py runs/grid1-grok-4.6 --out consistency.json

D-36 A5 (G-71). For each task: the base commit fetched and exported, the agent's
edits up to the cut replayed -- the same three steps `attempt.run` takes -- and
then `construct.consistency.check` against the conversation. Needs the network
(git fetch) and the corpus's turns for the tasks' sessions. Writes one row per
task and prints a table. Changes nothing in the run directory.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.construct import consistency  # noqa: E402
from errata_bench.construct.edits import edits_before, replay  # noqa: E402
from errata_bench.construct.workspace import fetch  # noqa: E402
from errata_bench.corpus.recover import recover, transcript_path  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths  # noqa: E402


def one(task, turns) -> dict:
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        try:
            tree = fetch(task.repo_url, task.sha, work / "repo").export_tree(task.sha, work / "tree")
        except Exception as e:  # noqa: BLE001 - reported per task
            return {"task_id": task.task_id, "error": f"could not build the tree: {type(e).__name__}: {e}"[:300]}
        rep = replay(tree, edits_before(turns, task.cut_turn), task.repo_id)
        # Where the raw transcript is here, the edits the table lost are
        # counted too (G-76); where it is not, `lost_edits` stays None.
        here = transcript_path(task.session_id).is_file()
        row = consistency.check(tree, turns, task.cut_turn, task.sha,
                                recovered=recover(task.session_id, turns) if here else None)
        return {"task_id": task.task_id, "replay_ok": bool(getattr(rep, "ok", True)), **row}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if not (args.run / "tasks.jsonl").is_file():
        ap.error(f"not a run directory (no tasks.jsonl): {args.run}")
    tasks = read(Paths(args.run).tasks)
    turns = load_session_turns({t.session_id for t in tasks})
    rows = [one(t, turns.get(t.session_id) or []) for t in tasks]
    args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{'task':34s} {'files':>5s} {'differ':>6s} {'head':>5s} {'mutating':>8s} {'lost':>5s}  consistent")
    for r in rows:
        if r.get("error"):
            print(f"{r['task_id'][-34:]:34s}  {r['error'][:60]}")
            continue
        print(f"{r['task_id'][-34:]:34s} {r['files_compared']:5d} {r['files_differing']:6d} "
              f"{('BAD' if r['head_contradicts_base'] else ('ok' if r['heads_printed'] else '-')):>5s} "
              f"{len(r['mutating_commands']):8d} "
              f"{('-' if r.get('lost_edits') is None else str(len(r['lost_edits']))):>5s}  {r['consistent']}")
    ok = sum(1 for r in rows if r.get("consistent"))
    quiet = sum(1 for r in rows if r.get("consistent") and not r.get("mutating_commands"))
    print(f"\n{ok} of {len(rows)} consistent; {quiet} of those with no state-changing command before the cut")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
