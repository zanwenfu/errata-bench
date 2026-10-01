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
Run again with OUT there, only the rows whose calls failed are asked again.
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
from errata_bench.corpus.turns import RECORD, RECORD_CHARS, build_excerpt, load_session_turns  # noqa: E402
from errata_bench.find.scope import SCOPE_GATE, in_scope  # noqa: E402
from errata_bench.stages.screening import _agree, screened_view  # noqa: E402
from errata_bench.store import load, replace  # noqa: E402


async def judge_run(run: Path, sem: asyncio.Semaphore, sessions: set[str] | None = None,
                    only: set[tuple] | None = None) -> list[dict]:
    rows = [r for r in load(run / "screened.jsonl")
            if not r.get("error") and (not sessions or r["session_id"] in sessions)
            and (only is None or (r["session_id"], str(r["complaint"])) in only)]
    turns = recovered(load_session_turns({r["session_id"] for r in rows}))

    async def one(r):
        # The conversation the row's task shows, its own repair applied: the
        # request is read there, as the candidate reads it (09-30 review).
        view = screened_view(r, turns.get(r["session_id"]) or [])
        message = last_user_message(view, r["cut"])
        request = (message or {}).get("content") or ""
        out = {"run": run.name, "session_id": r["session_id"], "complaint": r["complaint"],
               "repo_id": r["repo_id"], "old": r.get("within_scope"), "old_held": r.get("within_scope_held")}
        if not request:
            return {**out, "new": r.get("within_scope"), "new_held": None, "new_reason": "no request: unchanged"}
        # The candidate's view, as the screen stage reads it (G-81).
        excerpt = build_excerpt(view, r["cut"], max_chars=RECORD_CHARS, record=RECORD)
        async with sem:
            verdict, tally, scope = await _agree(
                lambda: in_scope(request, r.get("defect", ""), conversation=excerpt), 3, keep_on=True,
                reading=lambda x: x.within_scope)
        return {**out, "new": verdict, "new_held": tally, "new_reason": scope.reason}

    # Each row with its own calls' token use, and a row whose calls failed kept
    # as an error, not lost with every other paid row of the run (10-01 review).
    async def metered_one(r):
        try:
            x = await one(r)
        except Exception as e:  # noqa: BLE001 - recorded on its row, the others kept
            x = {"run": run.name, "session_id": r["session_id"], "complaint": r["complaint"],
                 "repo_id": r.get("repo_id"), "old": r.get("within_scope"), "new": r.get("within_scope"),
                 "new_held": None, "new_reason": "not asked again: the gate's call failed",
                 "error": f"{type(e).__name__}: {e}"}
        return {**x, "usage": llm.current_usage(), "screen_model": llm.model_name()}

    return await asyncio.gather(*(llm.metering(metered_one(r)) for r in rows))


def apply(run: Path, results: list[dict]) -> int:
    new = {(x["session_id"], str(x["complaint"])): x for x in results if x["run"] == run.name}
    path = run / "screened.jsonl"
    # Applied again, after the rows whose calls failed were asked, the backup and
    # each row's old verdict would have been overwritten with this one's: the
    # first backup is kept, and a row already moved is left (10-01 review).
    if not (run / "screened.pre-scope2.jsonl").exists():
        shutil.copy2(path, run / "screened.pre-scope2.jsonl")
    rows, moved = load(path), 0
    for r in rows:
        x = new.get((r["session_id"], str(r["complaint"])))
        if r.get("error") or x is None or x["new_held"] is None or "within_scope_v1" in r:
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
    ap.add_argument("--session", nargs="*", default=[], help="only these sessions' rows")
    args = ap.parse_args(argv)
    llm.configure_client()
    sem = asyncio.Semaphore(args.concurrency)
    if args.out.exists():
        # Only the rows whose calls failed are asked again, each in its run: the
        # rest are kept. Asked to remove OUT instead, a user paid for every row
        # again (10-01 review).
        results = load(args.out)
        again = {(x["run"], x["session_id"], str(x["complaint"])) for x in results if x.get("error")}
        if again:
            fresh = [x for run in args.runs
                     for x in await judge_run(run, sem, set(args.session),
                                              only={(s, c) for r, s, c in again if r == run.name})]
            asked = {(x["run"], x["session_id"], str(x["complaint"])) for x in fresh}
            results = [x for x in results
                       if (x["run"], x["session_id"], str(x["complaint"])) not in asked] + fresh
            replace(args.out, results)
    else:
        results = [x for run in args.runs for x in await judge_run(run, sem, set(args.session))]
        args.out.write_text("".join(json.dumps(x) + "\n" for x in results))
    failed = 0
    for run in args.runs:
        mine = [x for x in results if x["run"] == run.name]
        into = sum(1 for x in mine if x["old"] is False and x["new"] is True)
        out_ = sum(1 for x in mine if x["old"] is True and x["new"] is False)
        print(f"{run.name}: {len(mine)} rows, into scope {into}, out of scope {out_}")
        failed += (lost := sum(1 for x in mine if x.get("error")))
        if lost:
            print(f"  {lost} rows not asked again (their calls failed): left as they were; run this again to "
                  f"ask only them")
        if args.apply:
            print(f"  applied: {apply(run, results)} verdicts changed; backup screened.pre-scope2.jsonl")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
