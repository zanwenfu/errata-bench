"""The same 26 accepted answers, read again with the agent's last calls shown WITH their results, as a candidate's are."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "src")
from errata_bench import llm
from errata_bench.store import Paths, load
from errata_bench.score.judge import can_be_scored, judge
from errata_bench.score.attempt import control_conversations_for
from errata_bench.construct.consistency import _results_by_call
from errata_bench.corpus.turns import load_session_turns
from errata_bench.corpus.recover import recover
from errata_bench.spec import read
llm.configure_client()
runs = ["later-sample", "later-cap20", "step2-later", "step2-later-vps", "step2-first-vps"]
todo = []
for name in runs:
    p = Paths(Path("runs") / name)
    bad = {r["task_id"] for r in load(p.calibration) if not r.get("error") and not can_be_scored(r)}
    todo += [(name, t) for t in read(p.tasks) if t.task_id in bad]
conv = control_conversations_for([t for _, t in todo])
turns = load_session_turns({t.session_id for _, t in todo})
def calls_with_results(t, keep=60):
    mine = sorted([x for x in recover(t.session_id, turns[t.session_id]) if x.get("turn_number") is not None
                   and x["turn_number"] < t.resolved_turn], key=lambda x: x["turn_number"])
    res = _results_by_call(mine)
    out = []
    for i, x in enumerate(mine):
        if x.get("turn_type") != "tool_use":
            continue
        detail = x.get("command") or x.get("file_path") or (x.get("content") or "")
        out.append({"name": x.get("tool_name") or "?", "command": str(detail)[:4000], "result": res.get(i, "")[-3000:]})
    return out[-keep:]
sem = asyncio.Semaphore(3)
async def one(name, t):
    async with sem:
        j = await llm.resilient(lambda: judge(t, t.criterion, model="gpt-6-astra", tool_calls=calls_with_results(t),
                                              context=conv[t.task_id]["resolution"]))
        return {"run": name, "task_id": t.task_id, "outcome": j.outcome, "quote": j.quote, "reasoning": j.reasoning}
async def main():
    out = await asyncio.gather(*(one(n, t) for n, t in todo))
    json.dump(out, open(sys.argv[1], "w"), indent=1)
    before = {o["task_id"]: o["outcome"] for o in json.load(open(sys.argv[2]))}
    flips = [o for o in out if o["outcome"] == "solved"]
    print(f"read as solved with results shown: {len(flips)} of {len(out)}")
    for o in out:
        print(f"  {o['task_id']:42s} {before[o['task_id']]:30s} -> {o['outcome']}")
asyncio.run(main())
