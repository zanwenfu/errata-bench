#!/usr/bin/env python3
"""The Entire runs' admitted tasks, one per session, by v1's tally (`scripts/step2_tally.py`). No model calls.

    python scripts/tally_entire.py <out.json> <run>:<first|later>[:dev] ...

Admission is `score.rejudge.admitted` under gpt-6-astra, as v1's: the task held all seven of the gate's readings of
its known pair (`run.py gate --passes 7`), and all three controls behaved at three readings each. A run the gate has
never read is admitted on nothing, and said so: v1's admission is the gate's, not the single calibration reading.

- A session with two admitted tasks keeps its first pushback (decided 09-24 for v1, and 10-03 for Entire, before
  any was read).
- A task name used twice stops the tally (exit 1): names carry their session (`construct.build.task_name`), so two
  tasks under one name are one moment twice, or two sessions sharing their first eight characters.
- `dev` marks a run whose tasks shaped the rules -- the finding stages' instructions, the judge's or a gate's
  wording -- as v1's first grid did: its tasks are reported apart, and the counts are given without them too.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from errata_bench.instrument.gate import stable  # noqa: E402
from errata_bench.score.judge import PASSING, can_be_scored  # noqa: E402
from errata_bench.score.rejudge import admitted  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths, load  # noqa: E402

JUDGE = "gpt-6-astra"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out, specs = Path(argv[0]), argv[1:]
    rows, funnel = [], []
    for spec in specs:
        parts = spec.split(":")
        if len(parts) not in (2, 3) or parts[1] not in ("first", "later") or (len(parts) == 3 and parts[2] != "dev"):
            print(f"refused: {spec!r} is not <run>:<first|later>[:dev]")
            return 2
        run, kind, dev = Path(parts[0]), parts[1], len(parts) == 3
        p = Paths(run)
        tasks = {t.task_id: t for t in read(p.tasks)} if p.tasks.exists() else {}
        sound = {r["task_id"] for r in load(p.calibration) if not r.get("error") and can_be_scored(r)} & set(tasks)
        steady, tally = stable(run, JUDGE, passing=PASSING)
        # Seven readings, as v1's gate asked: `admitted` calls a task read
        # twice steady, so the count is required here too.
        gated = {t for t, x in tally.items() if x["asked"] >= 7} & set(tasks)
        held = {t for t in gated if tally[t]["held"] == tally[t]["asked"]}
        adm = (admitted(run, p, JUDGE, PASSING) & held) if gated else set()
        n = lambda f: sum(1 for _ in open(run / f)) if (run / f).exists() else 0
        funnel.append({"run": run.name, "kind": kind, "dev": dev, "moments": n("moments.jsonl"),
                       "screened": n("screened.jsonl"), "built": len(tasks), "sound": len(sound),
                       "gated": len(gated), "held the gate": len(held), "admitted": len(adm)})
        if tasks and not gated:
            print(f"note: {run.name} has {len(tasks)} built tasks and no gate readings: nothing is admitted")
        for tid in sorted(adm):
            t = tasks[tid]
            rows.append({"run": run.name, "kind": kind, "dev": dev, "task_id": tid, "session_id": t.session_id,
                         "repo_id": t.repo_id})
    keys = ("moments", "screened", "built", "sound", "gated", "held the gate", "admitted")
    print(f"{'run':26s} {'kind':6s} {'dev':4s} " + " ".join(f"{k:>13s}" for k in keys))
    for f in funnel:
        print(f"{f['run']:26s} {f['kind']:6s} {'dev' if f['dev'] else '':4s} " + " ".join(f"{f[k]:13d}" for k in keys))
    by_session = defaultdict(list)
    for r in rows:
        by_session[r["session_id"]].append(r)
    kept, dropped = [], []
    for rs in by_session.values():
        rs.sort(key=lambda r: (r["kind"] != "first", r["run"], r["task_id"]))   # the first pushback wins
        kept.append(rs[0])
        dropped += rs[1:]
    twice = [name for name, c in Counter(r["task_id"] for r in kept).items() if c > 1]
    dev = [r for r in kept if r["dev"]]
    repos = Counter(r["repo_id"] for r in kept)
    print(f"\nadmitted: {len(rows)} | sessions with two admitted tasks: {len(dropped)} "
          f"(dropped: {[d['task_id'] + ' in ' + d['run'] for d in dropped]})")
    print(f"kept: {len(kept)}; development tasks among them: {len(dev)}; without them: {len(kept) - len(dev)}")
    print(f"repositories: {len(repos)}; most tasks: {repos.most_common(6)}")
    json.dump({"funnel": funnel, "kept": kept, "dropped": dropped}, open(out, "w"), indent=1)
    if twice:
        print(f"refused: task names used twice: {twice}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
