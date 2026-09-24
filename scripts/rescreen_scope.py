"""Re-run the scope gate (B-252, `SCOPE_GATE` 2) over rows a run has already screened.

    .venv/bin/python scripts/rescreen_scope.py OUT.jsonl RUN... [--apply] [--concurrency N]

Asks the gate exactly as the screen stage now does -- the developer's last
message before the cut, read in the conversation the agent had, three readings
settled by majority -- and writes one row per screened row to OUT.jsonl: the
old verdict beside the new. Nothing in the run changes unless --apply is given.
Then each run's screened.jsonl is backed up to screened.pre-scope2.jsonl and its
rows take the new verdict, keeping the old one as `within_scope_v1`,
`within_scope_held_v1` and `scope_reason_v1`. Only the scope verdict moves: the
other gates' readings are left as they were, so their noise is not re-drawn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench import llm  # noqa: E402
from errata_bench.construct.build import last_user_message  # noqa: E402
from errata_bench.corpus.recover import recovered  # noqa: E402
from errata_bench.corpus.turns import build_excerpt, load_session_turns  # noqa: E402
from errata_bench.find.scope import SCOPE_GATE, in_scope  # noqa: E402
from errata_bench.stages.screening import _agree  # noqa: E402
from errata_bench.store import load, replace  # noqa: E402


async def judge_run(run: Path, sem: asyncio.Semaphore) -> list[dict]:
    rows = [r for r in load(run / "screened.jsonl") if not r.get("error")]
    turns = recovered(load_session_turns({r["session_id"] for r in rows}))

    async def one(r):
        ts = turns.get(r["session_id"]) or []
        message = last_user_message(ts, r["cut"])
        request = (message or {}).get("content") or ""
        out = {"run": run.name, "session_id": r["session_id"], "complaint": r["complaint"],
               "repo_id": r["repo_id"], "old": r.get("within_scope"), "old_held": r.get("within_scope_held")}
        if not request:
            return {**out, "new": r.get("within_scope"), "new_held": None, "new_reason": "no request: unchanged"}
        excerpt = build_excerpt(ts, r["cut"])
        async with sem:
            verdict, tally, scope = await _agree(
                lambda: in_scope(request, r.get("defect", ""), conversation=excerpt), 3, keep_on=True,
                reading=lambda x: x.within_scope)
        return {**out, "new": verdict, "new_held": tally, "new_reason": scope.reason}

    return await asyncio.gather(*(one(r) for r in rows))


def apply(run: Path, results: list[dict]) -> int:
    new = {(x["session_id"], str(x["complaint"])): x for x in results if x["run"] == run.name}
    path = run / "screened.jsonl"
    shutil.copy2(path, run / "screened.pre-scope2.jsonl")
    rows, moved = load(path), 0
    for r in rows:
        x = new.get((r["session_id"], str(r["complaint"])))
        if r.get("error") or x is None or x["new_held"] is None:
            continue
        r.update({"within_scope_v1": r.get("within_scope"), "within_scope_held_v1": r.get("within_scope_held"),
                  "scope_reason_v1": r.get("scope_reason"), "within_scope": x["new"],
                  "within_scope_held": x["new_held"], "scope_reason": x["new_reason"], "scope_gate": SCOPE_GATE})
        moved += x["new"] != x["old"]
    replace(path, rows)
    return moved


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args(argv)
    llm.configure_client()
    sem = asyncio.Semaphore(args.concurrency)
    if args.out.exists():
        results = load(args.out)
    else:
        results = [x for run in args.runs for x in await judge_run(run, sem)]
        args.out.write_text("".join(json.dumps(x) + "\n" for x in results))
    for run in args.runs:
        mine = [x for x in results if x["run"] == run.name]
        into = sum(1 for x in mine if x["old"] is False and x["new"] is True)
        out_ = sum(1 for x in mine if x["old"] is True and x["new"] is False)
        print(f"{run.name}: {len(mine)} rows, into scope {into}, out of scope {out_}")
        if args.apply:
            print(f"  applied: {apply(run, results)} verdicts changed; backup screened.pre-scope2.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
