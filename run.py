#!/usr/bin/env python
"""Run the benchmark.

    python run.py moments --limit 50        find pushback moments to work on
    python run.py stages --through screen   the cheap stages, no containers
    python run.py stages                    everything, including candidates
    python run.py status                    what exists so far

Stages are resumable. Each writes its own file and skips rows already recorded,
so an interrupted run continues where it stopped and a stage can be re-run alone
after its code changes.

The cheap stages cost about eight model calls per moment and touch no
containers. `attempt` is the expensive one -- a container and a judge call per
run, several runs per task -- so `--through screen` exists to establish the
yield before committing to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from errata_bench.pipeline import STAGES, Paths, append, load, run_stages  # noqa: E402


def find_moments(limit: int, out: Path, *, skip_seen: Path | None = None) -> int:
    """Collect pushback moments from the corpus, first-in-session only.

    Only the first pushback in a session is taken. A later one sits in a
    conversation already full of friction, and a candidate reading that does not
    have to be careful -- it only has to take the hint. Of the filtered pushback
    moments in this corpus, 8,643 have six or more complaints before them and
    756 are the first in their session.
    """
    import pyarrow.parquet as pq

    from errata_bench.corpus import CORPUS

    seen = set()
    if skip_seen and skip_seen.exists():
        seen = {
            (r.get("session_id"), r.get("turn_number"))
            for r in load(skip_seen)
        }

    repo_of = {}
    table = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "repo_id"])
    for s, r in zip(
        table.column("session_id").to_pylist(), table.column("repo_id").to_pylist()
    ):
        repo_of[s] = r

    first_in_session: dict[str, dict] = {}
    conv = pq.ParquetFile(CORPUS / "conversations.parquet")
    for batch in conv.iter_batches(
        batch_size=200_000,
        columns=["session_id", "turn_number", "turn_type", "prompt_pushback"],
    ):
        cols = {n: batch.column(n).to_pylist() for n in batch.schema.names}
        for i in range(len(cols["session_id"])):
            if not cols["prompt_pushback"][i]:
                continue
            if cols["turn_type"][i] != "user_prompt":
                continue
            sid = cols["session_id"][i]
            turn = cols["turn_number"][i]
            if sid in first_in_session and first_in_session[sid]["turn_number"] <= turn:
                continue
            first_in_session[sid] = {
                "session_id": sid,
                "turn_number": turn,
                "repo_id": repo_of.get(sid),
            }

    fresh = [
        m
        for m in first_in_session.values()
        if (m["session_id"], m["turn_number"]) not in seen and m["repo_id"]
    ]

    # Spread across repositories rather than taking the first N of a sorted
    # list. Sorting by repo_id and slicing gave fifty moments from a single
    # repository, which measures that project rather than anything general: one
    # codebase's conventions, one developer's habits, one language's toolchain.
    # Round-robin instead, so a fifty-moment probe covers as many projects as it
    # can before taking a second from any of them.
    by_repo: dict[str, list[dict]] = {}
    for m in fresh:
        by_repo.setdefault(m["repo_id"], []).append(m)
    for moments in by_repo.values():
        moments.sort(key=lambda m: m["session_id"])

    spread: list[dict] = []
    while len(spread) < limit and by_repo:
        for repo in sorted(by_repo):
            if not by_repo[repo]:
                continue
            spread.append(by_repo[repo].pop(0))
            if len(spread) >= limit:
                break
        by_repo = {r: ms for r, ms in by_repo.items() if ms}

    for m in spread:
        append(out, m)
    return len(spread)


def show_status(paths: Paths) -> None:
    rows = [
        ("moments", paths.moments),
        ("readings", paths.readings),
        ("trajectories", paths.trajectories),
        ("signatures", paths.signatures),
        ("screened", paths.screened),
        ("tasks", paths.tasks),
        ("calibration", paths.calibration),
        ("attempts", paths.attempts),
    ]
    print(f"  {'file':16s} {'rows':>7s}")
    for name, path in rows:
        n = len(load(path)) if path.exists() else 0
        print(f"  {name:16s} {n:>7,}")
    if paths.report.exists():
        print("\n  report:")
        print("   ", json.dumps(json.loads(paths.report.read_text()), indent=2).replace("\n", "\n    "))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["moments", "stages", "status"])
    ap.add_argument("--run", default="runs/current", help="directory for this run's files")
    ap.add_argument("--limit", type=int, default=50, help="how many moments to collect")
    ap.add_argument("--through", choices=STAGES, help="stop after this stage")
    ap.add_argument("--only", choices=STAGES, help="run just this stage")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--repeats", type=int, default=3, help="attempts per task")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="collect moments not present in an earlier run's moments file",
    )
    ap.add_argument("--exclude", help="a moments file whose rows to skip")
    args = ap.parse_args()

    root = Path(args.run)
    paths = Paths(root)

    if args.command == "status":
        show_status(paths)
        return

    if args.command == "moments":
        skip = Path(args.exclude) if args.exclude else None
        n = find_moments(args.limit, paths.moments, skip_seen=skip)
        print(f"  collected {n} moments -> {paths.moments}")
        return

    stages = STAGES
    if args.only:
        stages = (args.only,)
    elif args.through:
        stages = STAGES[: STAGES.index(args.through) + 1]

    print(f"  run: {root}")
    print(f"  stages: {', '.join(stages)}\n")
    asyncio.run(
        run_stages(
            root,
            stages,
            limit=args.limit,
            concurrency=args.concurrency,
            repeats=args.repeats,
        )
    )


if __name__ == "__main__":
    main()
