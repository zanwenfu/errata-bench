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
containers. `attempt` is the expensive one -- a container per run, several runs per task
-- so `--through screen` exists to establish the yield before committing to it.
`grade` reads those answers and costs no containers, so it takes its own, wider
--grade-concurrency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from errata_bench.stages import run_stages
from errata_bench.store import STAGES, Paths, append, load  # noqa: E402


# The values prompt_pushback actually takes. It is a string, not a boolean, and
# 58% of user prompts carry the literal "non_pushback" -- which is truthy in
# Python. Testing `if not row["prompt_pushback"]` therefore kept exactly the
# turns the corpus had labelled as not being pushbacks, and a four-hundred-moment
# sample came back with a median turn number of 2: session openings such as
# "Implement the following plan:" and "can you check if Gemma 4 is present in the
# JSON files". Nothing had happened yet for anyone to object to.
PUSHBACK_KINDS = ("failure_report", "rejection", "correction", "takeover")

# Moments are NOT ordered by pushback kind. Three measurements of viability by
# kind disagreed with each other: corrections came out worst at 18%, then best
# at 52%, then middling at 33%, while failure reports moved the other way. All
# three kinds sit near 35-40% once the noise is allowed for, and takeover is too
# rare to rank at all. Sorting by a number that reorders itself every time is
# fitting noise, and it makes a sample harder to reason about rather than
# easier -- a run's composition then depends on which measurement was current.


def find_moments(
    limit: int,
    out: Path,
    *,
    skip_seen: Path | Sequence[Path] | None = None,
    kinds: tuple[str, ...] = PUSHBACK_KINDS,
    min_agent_turns: int = 3,
    max_per_repo: int = 0,
    later: bool = False,
) -> int:
    """Collect pushback moments from the corpus.

    ``later`` takes, instead of each session's first pushback, its earliest
    later one with at least ``min_agent_turns`` agent turns since the pushback
    before it: new work, objected to again. One per session, so tasks stay
    independent, and the earliest, so the conversation carries as little
    friction as a later moment can. The first pushbacks are exhausted (all
    1,808 drawn); these are 10,511 more moments in 2,043 sessions.

    Takes the first genuine pushback in each session. A later one sits in a
    conversation already full of friction, and a candidate reading that does not
    have to be careful -- it only has to take the hint. The leakage gate rejects
    such conversations, so collecting them wastes the reading.

    Moments are ordered by the measured viability of their kind and spread across
    repositories, so a sample of any size covers as many projects as it can and
    reads the most promising moments first.
    """
    import pyarrow.parquet as pq

    from errata_bench.corpus.sessions import CORPUS

    # One file or many: `--exclude` names one, `--fresh` hands over every
    # moments file already on disk.
    sources = [skip_seen] if isinstance(skip_seen, Path) else list(skip_seen or [])
    seen = {
        (r.get("session_id"), r.get("turn_number"))
        for f in sources if f.exists() for r in load(f)
    }

    # Only repositories the benchmark can run a candidate in. A moment from any
    # other costs eight model calls to read and then cannot be attempted: a
    # task with no container is not run (G-48), and Rust was left out on
    # purpose (D-31). 4,669 of the corpus's 5,851 sessions pass this.
    from errata_bench.construct.container import can_be_sandboxed
    from errata_bench.corpus.sessions import load_repos

    languages = {rid: repo.language for rid, repo in load_repos().items()}

    repo_of = {}
    table = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "repo_id"])
    for session, repo in zip(
        table.column("session_id").to_pylist(), table.column("repo_id").to_pylist()
    ):
        repo_of[session] = repo

    wanted = set(kinds)
    first: dict[str, dict] = {}
    pushes: dict[str, list[tuple]] = {}
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
            if later:
                pushes.setdefault(session, []).append((turn, kind))
                continue
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
    if later:
        first = _later_moments(conv, pushes, repo_of, min_agent_turns)
    need = {m["session_id"]: m["turn_number"] for m in first.values()}
    acted: dict[str, int] = {s: 0 for s in need}
    # And whether any turn of the session carries a timestamp. The build finds
    # the commit a session started from by its first timestamp and rejects a
    # session with none -- after triage, reading, locating and screening have
    # spent their calls on it. None of the 623 OpenCode sessions has one (nor
    # a tool result), and they were 173 of the next batches' 1,774 moments.
    stamped: set[str] = set()
    for batch in conv.iter_batches(
        batch_size=200_000, columns=["session_id", "turn_number", "turn_type", "timestamp"]
    ):
        cols = {n: batch.column(n).to_pylist() for n in ("session_id", "turn_number", "turn_type")}
        valid = batch.column("timestamp").is_valid().to_pylist()
        for i in range(len(cols["session_id"])):
            session = cols["session_id"][i]
            if valid[i] and session in need:
                stamped.add(session)
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
        and can_be_sandboxed(languages.get(m["repo_id"]))
    ]
    unstamped = sum(1 for m in fresh if m["session_id"] not in stamped)
    fresh = [m for m in fresh if m["session_id"] in stamped]
    if unstamped:
        print(f"  {unstamped} moments left out: no turn in their session carries a timestamp, "
              "so the build could not find the commit it started from", flush=True)
    # Counted over the same rows `fresh` considers -- moments not already
    # collected. Counted over all of them it reported every moment any earlier
    # run had taken, every time: 456 printed where 68 were newly withheld, a
    # nearly sevenfold overstatement of what this run passed over.
    #
    # And the two reasons are said apart. `languages.get` answers None both for
    # a repository whose language the corpus does not record and for one it has
    # no row for at all, and "in a language we have no container for" is false
    # of the second -- 16% of what is dropped.
    withheld = [
        m for m in first.values()
        if (m["session_id"], m["turn_number"]) not in seen
        and m["repo_id"] and m["agent_turns_before"] >= min_agent_turns
        and not can_be_sandboxed(languages.get(m["repo_id"]))
    ]
    unknown = sum(1 for m in withheld if m["repo_id"] not in languages)
    unrecorded = sum(1 for m in withheld
                     if m["repo_id"] in languages and not languages[m["repo_id"]])
    if withheld:
        why = [f"{len(withheld) - unknown - unrecorded} in a language the benchmark "
               "has no container for"]
        if unrecorded:
            why.append(f"{unrecorded} whose language the corpus does not record")
        if unknown:
            why.append(f"{unknown} whose repository the corpus has no row for")
        print(f"  {len(withheld)} moments left out: " + ", ".join(why), flush=True)

    # Spread across repositories rather than taking the first N of a sorted list.
    # Sorting by repo_id and slicing gave fifty moments from a single repository,
    # which measures that project rather than anything general. Within a
    # repository the order is by session id: stable, arbitrary, and not a
    # judgement about which moments are worth more.
    by_repo: dict[str, list[dict]] = {}
    for m in fresh:
        by_repo.setdefault(m["repo_id"], []).append(m)
    for moments in by_repo.values():
        moments.sort(key=lambda m: m["session_id"])

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


def _later_moments(conv, pushes: dict, repo_of: dict, min_agent_turns: int) -> dict[str, dict]:
    """Each session's earliest later pushback with enough new agent work before it.

    Agent turns are counted between consecutive pushbacks, so "3 turns since
    the previous objection" means the agent did new work that was objected to
    again -- not that a pile of turns from before the first objection carried
    over. A session with one pushback has no later moment.
    """
    from bisect import bisect_right

    order = {s: sorted(p) for s, p in pushes.items() if len(p) > 1}
    turns = {s: [t for t, _ in p] for s, p in order.items()}
    between: dict[str, list[int]] = {s: [0] * len(p) for s, p in order.items()}
    for batch in conv.iter_batches(batch_size=200_000, columns=["session_id", "turn_number", "turn_type"]):
        cols = {n: batch.column(n).to_pylist() for n in batch.schema.names}
        for i in range(len(cols["session_id"])):
            session = cols["session_id"][i]
            if session not in turns or cols["turn_type"][i] not in ("assistant_response", "tool_use"):
                continue
            k = bisect_right(turns[session], cols["turn_number"][i])
            if k < len(between[session]):
                between[session][k] += 1   # agent work done before pushback k
    out = {}
    for session, p in order.items():
        for k in range(1, len(p)):
            if between[session][k] >= min_agent_turns:
                out[session] = {"session_id": session, "turn_number": p[k][0],
                                "repo_id": repo_of.get(session), "kind": p[k][1],
                                "later": True, "nth_pushback": k + 1,
                                "agent_turns_since_previous": between[session][k]}
                break
    return out


def show_status(paths: Paths) -> None:
    rows = [
        ("moments", paths.moments),
        ("triaged", paths.triaged),
        ("readings", paths.readings),
        ("trajectories", paths.trajectories),
        ("signatures", paths.signatures),
        ("screened", paths.screened),
        ("tasks", paths.tasks),
        ("rejections", paths.rejections),
        ("calibration", paths.calibration),
        ("controls", paths.controls),
        ("answers", paths.answers),
        ("attempts", paths.attempts),
        ("gate", paths.gate),
    ]
    print(f"  {'file':16s} {'rows':>7s}")
    for name, path in rows:
        n = len(load(path)) if path.exists() else 0
        print(f"  {name:16s} {n:>7,}")
    if paths.report.exists():
        try:
            json.loads(paths.report.read_text())
        except ValueError:
            print("\n  report.json could not be read; it was probably written by a run "
                  "that was interrupted. Re-run `--only report`.")
            return
        print("\n  report:")
        print("   ", json.dumps(json.loads(paths.report.read_text()), indent=2).replace("\n", "\n    "))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["moments", "stages", "status", "rejudge", "judges", "gate"])
    ap.add_argument("--run", default="runs/current", help="directory for this run's files")
    # `default=None`, so the parser itself says whether this was passed. Asked
    # as `"--limit" in sys.argv` the refusal below missed `--limit=50`, which
    # argparse accepts and which is the spelling that then bought every row at
    # full price in silence -- the exact accident the refusal exists to stop,
    # let through by the test written for it.
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="how many moments to collect (the `moments` command only; default 50)",
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
    ap.add_argument(
        "--grade-concurrency",
        type=int,
        default=0,
        help="how many answers to grade at once; 0 means the same as --concurrency. "
             "Grading waits on the provider rather than on this laptop, so it can run "
             "far wider than the candidates -- but only as wide as the judge allows",
    )
    ap.add_argument("--repeats", type=int, default=3, help="attempts per task")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="collect moments not present in an earlier run's moments file",
    )
    ap.add_argument("--exclude", help="a moments file whose rows to skip")
    ap.add_argument(
        "--later",
        action="store_true",
        help="collect each session's earliest later pushback with new agent work before it, "
             "instead of its first (the `moments` command only)",
    )
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
    ap.add_argument(
        "--passes",
        type=int,
        default=1,
        help="how many times to ask each question: grading an answer (`rejudge`), "
             "reading a task's known pair (`gate`), each screening gate (`stages "
             "--only screen`), each control (`stages --only control`) or each stored "
             "answer (`stages --only grade`). Must be odd. Two rules, by what the "
             "question decides: a gate that decides whether a task EXISTS is settled "
             "by majority, since unanimity there does not reduce noise, it moves the "
             "mean toward rejection; a gate that decides whether a task can be SCORED "
             "is settled by unanimity, so a pass must pass every time and one reading "
             "calling a claim unsupported is enough (D-34)",
    )
    ap.add_argument(
        "--judge",
        help="the model (on Azure, the deployment) to grade with (the `rejudge` command only)",
    )
    args = ap.parse_args()

    # A ceiling of zero is a semaphore nothing can pass: the run starts, prints
    # its stages and hangs for ever with no output and no work done.
    if not 1 <= args.concurrency <= 32 or not 0 <= args.grade_concurrency <= 32:
        ap.error("--concurrency must be 1..32, and --grade-concurrency 0..32 (0 means match it)")

    # `--limit` caps the `moments` command and nothing else, and its default is
    # 50. Passed to `stages` it was read, ignored, and never mentioned: the
    # obvious flag for "only do a few of these" silently bought all 850 rows at
    # full price. The cap for a stage is `--max-rows`. Refused rather than
    # quietly honoured, because the two mean different things -- one caps what
    # is collected, the other what each stage processes -- and guessing which
    # was meant is how an expensive run goes wrong quietly.
    if args.command != "moments" and args.limit is not None:
        ap.error(f"--limit caps the `moments` command only; for {args.command} use --max-rows")

    # A screening gate settled by majority needs an odd number of readings, and
    # `_agree` refuses an even one. Caught here it costs nothing; caught there
    # it is one error row per moment and a directory of failures to clean up.
    if args.passes < 1 or (args.passes > 1 and args.passes % 2 == 0):
        ap.error(f"--passes must be odd (a majority needs one), not {args.passes}")

    root = Path(args.run)
    paths = Paths(root)

    if args.command == "status":
        show_status(paths)
        return

    if args.command == "rejudge":
        # Grades what the run already holds with another model; runs no
        # candidate and writes only under <run>/rejudge/<judge>/.
        if not args.judge:
            ap.error("rejudge needs --judge <model or deployment name>")
        from errata_bench.store import only_one
        from errata_bench.score.rejudge import judge_paths, rejudge

        # One per judge, not one per run: two judges write different
        # directories and may run side by side, while two of the same judge
        # re-do every paid call and race the prune inside `regrade_all`.
        with only_one(judge_paths(root, args.judge).root, f"regrading with {args.judge}"):
            summary = asyncio.run(
                rejudge(root, args.judge, concurrency=args.concurrency, passes=args.passes)
            )
        shown = {k: v for k, v in summary.items() if k != "disagreements"}
        print("\n   ", json.dumps(shown, indent=2).replace("\n", "\n    "))
        print(f"\n  {len(summary['disagreements'])} attempts graded differently from the original")
        return

    if args.command == "gate":
        # How reliably this judge can read each task's known pair. A task is
        # admitted on one yes/no decision that turns out not to be
        # reproducible, and it carries three attempts with it, so the decision
        # is taken repeatedly instead of once. No candidate runs.
        if not args.judge:
            ap.error("gate needs --judge <model or deployment name>")
        from errata_bench.store import only_one
        from errata_bench.instrument.gate import measure, report

        # gate.jsonl is one file for every judge, and `completed` rewrites it,
        # so this one is per run directory rather than per judge.
        with only_one(root, "reading known pairs", name="gate.lock"):
            prog = asyncio.run(
                measure(root, args.judge, passes=args.passes, concurrency=args.concurrency)
            )
        print(prog.line())
        print(report(root, args.judge))
        return

    if args.command == "judges":
        from errata_bench.score.rejudge import compare

        print(compare(root))
        return

    if args.command == "moments":
        # `--fresh` was parsed and never read: two runs of `moments --limit 3`,
        # one with it and one without, produced identical files, and two of
        # those three moments had already been read at roughly eight model
        # calls each. It names no file of its own, so it means every moments
        # file already on disk, this run's included.
        skip: list[Path] = [Path(args.exclude)] if args.exclude else []
        if args.fresh:
            skip += sorted(p for p in Path("runs").glob("*/moments.jsonl"))
            skip += [Path("runs/moments.jsonl")]
        kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
        n = find_moments(50 if args.limit is None else args.limit,
                         paths.moments, skip_seen=skip, kinds=kinds,
                         max_per_repo=args.max_per_repo, later=args.later)
        if args.fresh:
            print(f"  skipping moments already in {len([p for p in skip if p.exists()])} file(s)")
        print(f"  collected {n} moments -> {paths.moments}")
        return

    stages = STAGES
    if args.only:
        stages = (args.only,)
    elif args.through:
        stages = STAGES[: STAGES.index(args.through) + 1]

    print(f"  run: {root}")
    print(f"  stages: {', '.join(stages)}\n")
    done = asyncio.run(
        run_stages(
            root,
            stages,
            limit=args.max_rows or 10**9,
            concurrency=args.concurrency,
            repeats=args.repeats,
            grade_concurrency=args.grade_concurrency or None,
            passes=args.passes,
        )
    )
    # A stage that wrote eighty-one error rows and a stage that graded
    # eighty-one answers were both worth exit code 0, so the shell loop driving
    # the runs could not tell them apart and neither could anyone reading a log
    # afterwards.
    failed = [p for p in done if p.failed]
    if failed:
        print(f"\n  {sum(p.failed for p in failed)} rows failed in: "
              f"{', '.join(p.stage for p in failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
