#!/usr/bin/env python3
"""The Entire runs' admitted tasks, one per session, by v1's tally (`scripts/step2_tally.py`). No model calls.

    python scripts/tally_entire.py <out.json> <run>:<first|later>[:dev] ...
    python scripts/tally_entire.py --short <run> <calibration|gate|controls>

Admission is `score.rejudge.admitted` under gpt-6-astra, as v1's: the task held all seven of the gate's readings of
its known pair (`run.py gate --passes 7`), and all three controls behaved at three readings each
(`--only control --passes 3`). Both counts are required here, since `admitted` calls a task read twice steady and a
control read once complete. A run the gate has never read is admitted on nothing, and said so: v1's admission is
the gate's, not the single calibration reading.

- A run that is not `dev` and whose admission steps are part done is refused, naming what is short: a task with no
  calibration reading by the judge, a built task read fewer than seven times by the gate, or a calibration-sound
  task with a control read fewer than three times. A task short of its readings would otherwise go unadmitted
  without a word. `--short` prints one step's shortfall and exits 1 if there is any (`scripts/run_entire.sh`).

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
GATE_READINGS, CONTROL_READINGS = 7, 3      # v1's: `run.py gate --passes 7`, `--only control --passes 3`


def shortfall(run: Path) -> dict[str, list[str]]:
    """What a run's admission steps have not yet read as often as v1 asked, by step; all empty when complete."""
    from errata_bench.instrument.control import CONTROLS

    p = Paths(run)
    tasks = {t.task_id for t in read(p.tasks)} if p.tasks.exists() else set()
    cal = [r for r in load(p.calibration) if not r.get("error") and r.get("judge_model") == JUDGE]
    sound = {r["task_id"] for r in cal if can_be_scored(r)} & tasks
    _, tally = stable(run, JUDGE, passing=PASSING)
    readings = Counter((r.get("task_id"), r.get("control")) for r in load(p.controls)
                       if not r.get("error") and r.get("judge_model") == JUDGE
                       and not str(r.get("control", "")).startswith("probe:"))
    return {
        "calibration": sorted(tasks - {r["task_id"] for r in cal}),
        "gate": sorted(t for t in tasks if tally.get(t, {}).get("asked", 0) < GATE_READINGS),
        "controls": sorted(t for t in sound if any(readings[(t, c.name)] < CONTROL_READINGS for c in CONTROLS)),
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["--short"]:
        if len(argv) != 3 or argv[2] not in ("calibration", "gate", "controls"):
            print("usage: tally_entire.py --short <run> <calibration|gate|controls>")
            return 2
        run = Path(argv[1])
        if not (run / "moments.jsonl").exists():
            print(f"refused: {run} holds no moments.jsonl; is the name right?")      # Paths() would create it
            return 2
        short = shortfall(run)[argv[2]]         # a run that built nothing is short of nothing
        print(f"{run.name}: {len(short)} task(s) short of their {argv[2]} readings"
              + (f": {', '.join(short[:6])}{' ...' if len(short) > 6 else ''}" if short else ""))
        return 1 if short else 0
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
        if not (run / "moments.jsonl").exists():
            print(f"refused: {run} holds no moments.jsonl; is the name right?")      # Paths() would create it
            return 2
        p = Paths(run)
        tasks = {t.task_id: t for t in read(p.tasks)} if p.tasks.exists() else {}
        sound = {r["task_id"] for r in load(p.calibration) if not r.get("error") and can_be_scored(r)} & set(tasks)
        steady, tally = stable(run, JUDGE, passing=PASSING)
        # Seven readings, as v1's gate asked: `admitted` calls a task read
        # twice steady, so the count is required here too; and three of each
        # control, which `admitted` takes from what the rows say was asked.
        gated = {t for t, x in tally.items() if x["asked"] >= GATE_READINGS} & set(tasks)
        held = {t for t in gated if tally[t]["held"] == tally[t]["asked"]}
        short = shortfall(run) if tasks else {"calibration": [], "gate": [], "controls": []}
        if not dev and any(short.values()):
            print(f"refused: {run.name} is part done: " + "; ".join(
                f"{len(v)} short of their {k} readings ({', '.join(v[:3])}{' ...' if len(v) > 3 else ''})"
                for k, v in short.items() if v) + ". Finish it first: scripts/run_entire.sh resumes")
            return 1
        adm = (admitted(run, p, JUDGE, PASSING) & held) - set(short["controls"]) if gated else set()
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
    if twice:
        print(f"refused: task names used twice: {twice}; nothing was written")
        return 1
    json.dump({"funnel": funnel, "kept": kept, "dropped": dropped}, open(out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
