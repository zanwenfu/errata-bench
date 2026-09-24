"""Why the judge reads the rejected tasks' accepted answers as making an unverified claim: one reading each, kept."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, "src")
from errata_bench import llm
from errata_bench.store import Paths, load
from errata_bench.score.judge import can_be_scored, judge
from errata_bench.score.attempt import control_conversations_for
from errata_bench.spec import read
llm.configure_client()
runs = ["later-sample", "later-cap20", "step2-later", "step2-later-vps", "step2-first-vps"]
todo = []
for name in runs:
    p = Paths(Path("runs") / name)
    bad = {r["task_id"] for r in load(p.calibration) if not r.get("error") and not can_be_scored(r)}
    todo += [(name, t) for t in read(p.tasks) if t.task_id in bad]
conv = control_conversations_for([t for _, t in todo])
sem = asyncio.Semaphore(3)
async def one(name, t):
    async with sem:
        j = await llm.resilient(lambda: judge(t, t.criterion, model="gpt-6-astra", tool_calls=t.criterion_calls,
                                              context=conv[t.task_id]["resolution"]))
        return {"run": name, "task_id": t.task_id, "outcome": j.outcome, "unverified": j.makes_unverified_claim,
                "defect_remains": j.defect_remains, "quote": j.quote, "reasoning": j.reasoning}
async def main():
    out = await asyncio.gather(*(one(n, t) for n, t in todo))
    json.dump(out, open(sys.argv[1], "w"), indent=1)
    for o in out:
        print(f"{o['task_id']:42s} {o['outcome']:30s} quote: {o['quote'][:110]!r}")
asyncio.run(main())
