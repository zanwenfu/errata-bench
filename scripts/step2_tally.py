"""Step 2's admitted tasks, one per session: the funnel of every later-pushback run and the tally.

    .venv/bin/python scripts/step2_tally.py results/step2-tally.json > results/step2-admission.txt

Admission is `score.rejudge.admitted` under gpt-6-astra: calibration read
correctly on every one of the gate's seven readings, and all three controls
behaved. A session that produced two admitted tasks keeps its first pushback
(decided on 09-24 before any was read; none did). Reads only the run
directories, not the corpus.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from errata_bench.store import Paths, load
from errata_bench.score.judge import PASSING, can_be_scored
from errata_bench.score.rejudge import admitted
from errata_bench.spec import read

RUNS = [("later-sample", "later"), ("later-cap20", "later"), ("step2-later", "later"),
        ("step2-later-vps", "later"), ("step2-first-vps", "first"),
        # The first grid's 21 moments through the current pipeline (09-24): first
        # pushbacks, and the tasks the checker's rules were developed on.
        ("grid1-reprocess", "first")]
grid = {t.session_id: t.repo_id for t in read(Path("runs/phaseA-grid1-DeepSeek-V4-Pro/tasks.jsonl"))}
grid_repos = set(grid.values())
rows, funnel = [], []
for name, kind in RUNS:
    p = Paths(Path("runs") / name)
    tasks = {t.task_id: t for t in read(p.tasks)}
    sound = {r["task_id"] for r in load(p.calibration) if not r.get("error") and can_be_scored(r)} & set(tasks)
    adm = admitted(p.root, p, "gpt-6-astra", PASSING) & set(tasks)
    n = lambda f: sum(1 for _ in open(p.root / f)) if (p.root / f).exists() else 0
    funnel.append((name, n("moments.jsonl"), n("screened.jsonl"), len(tasks), len(sound), len(adm)))
    for tid in sorted(adm):
        t = tasks[tid]
        rows.append({"run": name, "kind": kind, "task_id": tid, "session_id": t.session_id, "repo_id": t.repo_id})
print(f"{'run':18s} {'moments':>8s} {'screened':>9s} {'built':>6s} {'sound':>6s} {'admitted':>9s}")
for f in funnel:
    print(f"{f[0]:18s} {f[1]:8d} {f[2]:9d} {f[3]:6d} {f[4]:6d} {f[5]:9d}")
by_session = defaultdict(list)
for r in rows:
    by_session[r["session_id"]].append(r)
kept, dropped = [], []
for sid, rs in by_session.items():
    rs.sort(key=lambda r: r["kind"] != "first")          # the first pushback wins
    kept.append(rs[0]); dropped += rs[1:]
names = Counter(r["task_id"] for r in kept)
print(f"\nadmitted: {len(rows)} | sessions with two admitted tasks: {len(dropped)} "
      f"(dropped: {[d['task_id'] + ' in ' + d['run'] for d in dropped]})")
print(f"task names used twice across runs: {[n for n, c in names.items() if c > 1]}")
print(f"in the first grid's sessions: {sum(1 for r in kept if r['session_id'] in grid)} "
      f"(from grid1-reprocess: {sum(1 for r in kept if r['run'] == 'grid1-reprocess')})")
groups = {"the first grid, reprocessed": [r for r in kept if r["run"] == "grid1-reprocess"],
          "later-sample": [r for r in kept if r["run"] == "later-sample"],
          "new in step 2 (with later-cap20)": [r for r in kept if r["run"] not in ("grid1-reprocess", "later-sample")]}
print("kept: " + str(len(kept)) + " = " + " + ".join(f"{len(v)} {k}" for k, v in groups.items()))
fresh = [r for r in kept if r["repo_id"] not in grid_repos]
print(f"outside the first grid's 18 repositories (eligible for D-39's fresh set): {len(fresh)}")
repos = Counter(r["repo_id"] for r in kept)
print(f"repositories: {len(repos)}; most tasks: {repos.most_common(6)}")
json.dump({"funnel": funnel, "kept": kept, "dropped": dropped}, open(sys.argv[1], "w"), indent=1)
