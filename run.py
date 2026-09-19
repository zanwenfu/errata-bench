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


# The values prompt_pushback actually takes. It is a string, not a boolean, and
# 58% of user prompts carry the literal "non_pushback" -- which is truthy in
# Python. Testing `if not row["prompt_pushback"]` therefore kept exactly the
# turns the corpus had labelled as not being pushbacks, and a four-hundred-moment
# sample came back with a median turn number of 2: session openings such as
# "Implement the following plan:" and "can you check if Gemma 4 is present in the
# JSON files". Nothing had happened yet for anyone to object to.
PUSHBACK_KINDS = ("failure_report", "rejection", "correction", "takeover")

# Measured viability, over the ninety-five moments read so far. The spread is
# real and worth ordering by, but none of these are so weak that they should be
# excluded outright.
KIND_YIELD = {
    "rejection": 0.58,
    "failure_report": 0.39,
    "correction": 0.18,
    "takeover": 0.14,
}


def find_moments(
    limit: int,
    out: Path,
    *,
    skip_seen: Path | None = None,
    kinds: tuple[str, ...] = PUSHBACK_KINDS,
    min_agent_turns: int = 3,
    max_per_repo: int = 0,
) -> int:
    """Collect pushback moments from the corpus.

    Takes the first genuine pushback in each session. A later one sits in a
    conversation already full of friction, and a candidate reading that does not
    have to be careful -- it only has to take the hint. The leakage gate rejects
    such conversations, so collecting them wastes the reading.

    Moments are ordered by the measured viability of their kind and spread across
    repositories, so a sample of any size covers as many projects as it can and
    reads the most promising moments first.
    """
    import pyarrow.parquet as pq

    from errata_bench.corpus import CORPUS

    seen = set()
    if skip_seen and skip_seen.exists():
        seen = {(r.get("session_id"), r.get("turn_number")) for r in load(skip_seen)}

    repo_of = {}
    table = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "repo_id"])
    for session, repo in zip(
        table.column("session_id").to_pylist(), table.column("repo_id").to_pylist()
    ):
        repo_of[session] = repo

    wanted = set(kinds)
    first: dict[str, dict] = {}
    conv = pq.ParquetFile(CORPUS / "conversations.parquet")
    for batch in conv.iter_batches(
        batch_size=200_000,
        columns=["session_id", "turn_number", "turn_type", "prompt_pushback"],
    ):
        cols = {n: batch.column(n).to_pylist() for n in batch.schema.names}
        for i in range(len(cols["session_id"])):
            kind = cols["prompt_pushback"][i]
            if kind not in wanted:
                continue
            if cols["turn_type"][i] != "user_prompt":
                continue
            session = cols["session_id"][i]
            turn = cols["turn_number"][i]
            if session in first and first[session]["turn_number"] <= turn:
                continue
            first[session] = {
                "session_id": session,
                "turn_number": turn,
                "repo_id": repo_of.get(session),
                "kind": kind,
            }

    # Count the agent turns preceding each candidate moment. A complaint with
    # nothing before it is about work from a session we cannot see: AI-Stats
    # opens at turn 0 with "The tiering doesn't seem to work for model providers
    # now?", which is a real bug report and an impossible task, because there is
    # no failing answer in this transcript to cut before. Triage catches these
    # for the price of a model call; counting rows costs nothing.
    need = {m["session_id"]: m["turn_number"] for m in first.values()}
    acted: dict[str, int] = {s: 0 for s in need}
    for batch in conv.iter_batches(
        batch_size=200_000, columns=["session_id", "turn_number", "turn_type"]
    ):
        cols = {n: batch.column(n).to_pylist() for n in batch.schema.names}
        for i in range(len(cols["session_id"])):
            session = cols["session_id"][i]
            cutoff = need.get(session)
            if cutoff is None or cols["turn_number"][i] >= cutoff:
                continue
            if cols["turn_type"][i] in ("assistant_response", "tool_use"):
                acted[session] += 1
    for session, m in first.items():
        m["agent_turns_before"] = acted.get(session, 0)

    fresh = [
        m
        for m in first.values()
        if (m["session_id"], m["turn_number"]) not in seen
        and m["repo_id"]
        and m["agent_turns_before"] >= min_agent_turns
    ]

    # Spread across repositories rather than taking the first N of a sorted list.
    # Sorting by repo_id and slicing gave fifty moments from a single repository,
    # which measures that project rather than anything general. Within each
    # repository the most promising kind comes first, so a short run does not
    # spend its budget on corrections when rejections are available.
    by_repo: dict[str, list[dict]] = {}
    for m in fresh:
        by_repo.setdefault(m["repo_id"], []).append(m)
    for moments in by_repo.values():
        moments.sort(key=lambda m: (-KIND_YIELD.get(m["kind"], 0), m["session_id"]))

    # A per-repository cap, because round-robin alone stops being diverse once
    # the small repositories are exhausted. Asking for 1,600 moments from the
    # unprocessed pool returned 61% of them from ten repositories, with 202 from
    # one -- a benchmark built on that measures a handful of codebases and their
    # conventions. Capping at twenty drops the top-ten share to 19% and keeps all
    # 109 repositories represented, at the cost of a smaller pool.
    if max_per_repo:
        by_repo = {r: ms[:max_per_repo] for r, ms in by_repo.items()}

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
    ap.add_argument(
        "--limit",
        type=int,
        default=50,
        help="how many moments to collect (the `moments` command only)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="cap how many rows each stage processes; 0 means all of them",
    )
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
    ap.add_argument(
        "--max-per-repo",
        type=int,
        default=0,
        help="cap moments taken from any one repository; 0 means no cap",
    )
    ap.add_argument(
        "--kinds",
        default=",".join(PUSHBACK_KINDS),
        help="comma-separated pushback kinds to collect",
    )
    args = ap.parse_args()

    root = Path(args.run)
    paths = Paths(root)

    if args.command == "status":
        show_status(paths)
        return

    if args.command == "moments":
        skip = Path(args.exclude) if args.exclude else None
        kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
        n = find_moments(args.limit, paths.moments, skip_seen=skip, kinds=kinds,
                         max_per_repo=args.max_per_repo)
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
            limit=args.max_rows or 10**9,
            concurrency=args.concurrency,
            repeats=args.repeats,
        )
    )


if __name__ == "__main__":
    main()
