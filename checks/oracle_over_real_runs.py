"""A behavioural fingerprint of the read-only code paths, over real run data.

The restructure moves every module. The three check suites in checks/ say the
guards still hold, but they run on fixtures. This runs the real reporting and
admission code over the real run directories on disk and prints a fingerprint
of everything it produces. Run it before the move and after; the two must be
identical, character for character.

It calls nothing that costs money and starts no container: only load, admission,
tallying, summarising and comparing, which are pure functions of rows already
written.

    .venv/bin/python checks/oracle_over_real_runs.py > before.txt
    ... make the change ...
    .venv/bin/python checks/oracle_over_real_runs.py > after.txt
    diff before.txt after.txt

Unlike the other three checks this one reads `runs/`, which is gitignored, so
on a fresh clone it prints ABSENT for every directory and proves nothing. It
is the right tool before a refactor and the wrong one in CI.

It found no difference across the 09-20 restructure, in which every module in
the package moved.
"""
import hashlib
import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, "src")

# rebuild-after is the directory the current scoreable set (R-26) comes from;
# it was missing from this list while three older directories were in it.
RUNS = ["cand-grok", "cand-kimi", "cand-deepseek", "rebuild-final", "rebuild-after"]


def canonical(x):
    """A form whose repr does not depend on this process's hash seed.

    Sets and dicts keyed by strings iterate in an order Python randomises per
    process, so digesting `repr(a_set)` reports a difference on every run and
    the oracle cries wolf. Only the contents are the observation.
    """
    if isinstance(x, (set, frozenset)):
        return ["<set>"] + sorted(canonical(v) for v in x)
    if isinstance(x, dict):
        return ["<dict>"] + sorted((str(k), canonical(v)) for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return [canonical(v) for v in x]
    return x


def digest(x) -> str:
    return hashlib.blake2b(repr(canonical(x)).encode(), digest_size=8).hexdigest()


def attempt(label, fn):
    """Run one probe, printing its result or the exception it raised.

    An exception is a legitimate observation -- some directories are partial --
    so it is recorded rather than swallowed. What must not change across the
    move is *which* probes raise and what they raise.
    """
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            value = fn()
        printed = out.getvalue()
        print(f"  {label}")
        print(f"    value  {digest(value)}  {str(canonical(value))[:110]}")
        if printed.strip():
            print(f"    stdout {digest(printed)}  ({len(printed)} chars)")
    except Exception as e:
        print(f"  {label}")
        print(f"    raised {type(e).__name__}: {str(e)[:110]}")


def main() -> int:
    from errata_bench.score.judge import PASSING, PASSING_WITH_HEDGE, can_be_scored, line_holds
    from errata_bench.instrument.control import controlled
    from errata_bench.store import Paths, finished, load, sort_answers
    from errata_bench.score.rejudge import (
        across, admitted, compare, judge_paths, structure_from_row, summarise, tally_of,
    )
    from errata_bench.spec import read as read_tasks
    from errata_bench.instrument.gate import observations, stable

    print("=" * 72)
    print("ROW-LEVEL: every file every run directory holds")
    print("=" * 72)
    for name in RUNS:
        run = Path("runs") / name
        if not run.exists():
            print(f"\n{name}: ABSENT")
            continue
        print(f"\n{name}")
        for f in sorted(run.glob("*.jsonl")):
            rows = load(f)
            print(f"  {f.name:22s} rows={len(rows):5d} digest={digest(rows)}")
        for sub in sorted(p for p in (run / "rejudge").glob("*") if p.is_dir()):
            for f in sorted(sub.glob("*.jsonl")):
                rows = load(f)
                print(f"  rejudge/{sub.name}/{f.name:14s} rows={len(rows):5d} digest={digest(rows)}")

    print()
    print("=" * 72)
    print("ADMISSION: the gate, under both standards")
    print("=" * 72)
    for name in RUNS:
        run = Path("runs") / name
        if not run.exists():
            continue
        print(f"\n{name}")
        for model in ("gpt-6-astra", "gpt-6-astra-starved"):
            attempt(f"observations({model})", lambda r=run, m=model: observations(r, m))
            for label, p in (("clean", PASSING), ("hedged", PASSING_WITH_HEDGE)):
                attempt(
                    f"stable({model}, {label})",
                    lambda r=run, m=model, p=p: stable(r, m, passing=p),
                )
                attempt(
                    f"admitted({model}, {label})",
                    lambda r=run, m=model, p=p: sorted(
                        admitted(r, judge_paths(r, m), m, p)
                    ),
                )

    print()
    print("=" * 72)
    print("REPORTING: summarise, tally, controls, sorting")
    print("=" * 72)
    for name in RUNS:
        run = Path("runs") / name
        if not run.exists():
            continue
        print(f"\n{name}")
        paths = Paths(run)
        attempt("controlled(paths)", lambda p=paths: sorted(controlled(p)))
        attempt("finished(attempts)", lambda p=paths: len(finished(p.attempts)))
        attempt("tally_of(attempts)", lambda p=paths: tally_of(load(p.attempts)))
        attempt("read_tasks(tasks)", lambda p=paths: [t.task_id for t in read_tasks(p.tasks)])
        attempt(
            "sort_answers(answers)",
            lambda p=paths: len(sort_answers(load(p.answers), {}) or []),
        )
        for model in ("gpt-6-astra", "gpt-6-astra-starved"):
            attempt(
                f"summarise({model})",
                lambda r=run, m=model, p=paths: summarise(p, judge_paths(r, m), m),
            )
        attempt("compare(run)", lambda r=run: compare(r))

    print()
    print("=" * 72)
    print("PER-ROW PREDICATES: the pass rules applied to every stored reading")
    print("=" * 72)
    for name in RUNS:
        run = Path("runs") / name
        if not run.exists():
            continue
        for f in list(run.glob("calibration.jsonl")) + list(run.glob("gate.jsonl")) + \
                 list((run / "rejudge").glob("*/calibration.jsonl")):
            rows = load(f)
            for label, p in (("clean", PASSING), ("hedged", PASSING_WITH_HEDGE)):
                holds = [line_holds(r, passing=p) for r in rows]
                scored = [can_be_scored(r, passing=p) for r in rows]
                rel = f.relative_to(Path("runs"))
                print(f"  {str(rel):46s} {label:6s} holds={sum(holds):4d}/{len(rows):4d} "
                      f"scored={sum(scored):4d} d={digest((holds, scored))}")

    print()
    print("=" * 72)
    print("STRUCTURE ROWS: the None-preserving rebuild (B-122's subject)")
    print("=" * 72)
    for name in RUNS:
        run = Path("runs") / name
        if not (run / "answers.jsonl").exists():
            continue
        rows = load(run / "answers.jsonl")
        built = []
        for r in rows:
            try:
                built.append(repr(structure_from_row(r)))
            except Exception as e:
                built.append(f"{type(e).__name__}")
        print(f"  {name:18s} rows={len(rows):4d} digest={digest(built)}")

    print()
    print("=" * 72)
    print("ACROSS: the two-column comparison on the common task set")
    print("=" * 72)
    present = [Path("runs") / n for n in ("cand-grok", "cand-kimi", "cand-deepseek")
               if (Path("runs") / n).exists()]
    for model in ("gpt-6-astra", "gpt-6-astra-starved"):
        attempt(f"across({model})", lambda m=model: across(present, m))

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
