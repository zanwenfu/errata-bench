"""The study's breakdowns, from a run's stored rows: no model call (docs/study.md).

    .venv/bin/python scripts/study_breakdown.py --run runs/study-pushback-120

Prints, for the outside reviewer:
- the share caught for each run a pooled folder was combined from, and a
  permutation test on the gap that shuffles whole sessions between the runs;
- how real errors and catches spread over sessions;
- the share caught by failure mode and kind of pushback, per run;
- the look-back: at which earlier report each catch was, and the share caught if
  the merge's candidates were cut to reports k-0..k and k-1..k (a sensitivity
  check: the merge saw k-2..k together, so a verdict may lean on its neighbours);
- the share caught by agent and by SWE-chat's developer persona, where the
  corpus's sessions.parquet is present.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

_spec = importlib.util.spec_from_file_location("study", Path(__file__).resolve().parent / "study.py")
study = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(study)
T = study.T

UNREAD = ("not merged", "incomplete")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--shuffles", type=int, default=10000)
    args = ap.parse_args()
    run = Path(args.run)
    reports = {study.key(r): r for r in study.load(study.path(run, "reports"))}
    replies = {study.key(r): r for r in study.completed(study.path(run, "human"))}
    merges = {study.key(r): r for r in study.completed(study.path(run, "merge"))}
    sessions = {r["session_id"]: r for r in study.load(study.path(run, "sessions"))}
    pushbacks = [k for k, r in replies.items() if r.get("is_pushback")]
    real = [k for k in pushbacks if replies[k].get("objection_kind") == "real_error"]

    def outcome(k, max_offset=None):
        """The tally's outcome (`study.tally`), with the candidates optionally cut to reports k-max_offset..k."""
        m = merges.get(k)
        if m is None:
            return "not merged"
        every = m.get("verdicts") or []
        vs = [v for v in every if max_offset is None or k[1] - int(v["report"]) <= max_offset]
        if any(study._valid_same(v) for v in vs):
            return "same"
        if any(v.get("missing") or not v.get("match_known", True) for v in every):
            return "incomplete"
        if any(v.get("match") == "related" for v in vs):
            return "related"
        return "none"

    def caught(keys, **kw) -> dict:
        return T.bootstrap([(k[0], o == "same") for k in keys for o in [outcome(k, **kw)] if o not in UNREAD])

    def fmt(b) -> str:
        if not b.get("n"):
            return "n=0"
        if b.get("low") is None:
            return f"{b['share']:.0%} n={b['n']} (one session)"
        return f"{b['share']:.0%} [{b['low']:.0%}–{b['high']:.0%}] n={b['n']}"

    origin = {sid: s.get("from_run", run.name) for sid, s in sessions.items()}
    names = sorted(set(origin.values()))
    print("== caught, by run: real errors; every pushback")
    for name in names:
        keys = [k for k in pushbacks if origin[k[0]] == name]
        print(f"  {name:16s} {fmt(caught([k for k in keys if k in real]))};  {fmt(caught(keys))}")

    if len(names) == 2:
        tally = defaultdict(lambda: [0, 0])
        for k in real:
            o = outcome(k)
            if o not in UNREAD:
                tally[k[0]][0] += o == "same"
                tally[k[0]][1] += 1
        a, b = names

        def gap(assign) -> float:
            share = {}
            for g in (a, b):
                c = sum(tally[s][0] for s in tally if assign[s] == g)
                n = sum(tally[s][1] for s in tally if assign[s] == g)
                share[g] = c / n
            return share[b] - share[a]

        sids = sorted(tally)
        observed = gap({s: origin[s] for s in sids})
        labels = [origin[s] for s in sids]
        rng = random.Random(20261003)
        hits = 0
        for _ in range(args.shuffles):
            rng.shuffle(labels)
            hits += abs(gap(dict(zip(sids, labels)))) >= abs(observed) - 1e-12
        print(f"\n== the gap in real errors caught, {b} minus {a}: {observed:+.0%}; two-sided p = "
              f"{hits / args.shuffles:.3f} ({args.shuffles} shuffles of {len(sids)} sessions between the runs)")
        print("\n== real errors over sessions (caught/errors, largest first)")
        for g in names:
            rows = sorted((tally[s] for s in sids if origin[s] == g), key=lambda x: -x[1])
            print(f"  {g:16s} {len(rows)} sessions, median {statistics.median(r[1] for r in rows)} each: "
                  + " ".join(f"{c}/{n}" for c, n in rows[:12]))

    print("\n== caught, real errors by failure mode and by kind of pushback, per run")
    for field, many in (("failure_modes", True), ("pushback_kind", False)):
        values = lambda k: (replies[k].get(field) or ["(none)"]) if many else [replies[k].get(field) or "?"]
        for g in sorted({g for k in real for g in values(k)}):
            cells = [f"{name} {fmt(caught([k for k in real if origin[k[0]] == name and g in values(k)]))}"
                     for name in names]
            print(f"  {g:24s} " + "   ".join(cells))

    print("\n== look-back: the report of each catch's nearest valid 'same' (0 is the report itself)")
    offsets = Counter(min(k[1] - int(v["report"]) for v in merges[k]["verdicts"] if study._valid_same(v))
                      for k in pushbacks if outcome(k) == "same")
    print("  ", dict(sorted(offsets.items())))
    for mo in range(study.M.LOOKBACK + 1):
        print(f"  candidates cut to reports k-{mo}..k: real errors {fmt(caught(real, max_offset=mo))};  "
              f"every pushback {fmt(caught(pushbacks, max_offset=mo))}")

    try:
        import pyarrow.parquet as pq

        from errata_bench.corpus.sessions import CORPUS

        table = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "agent", "user_persona"])
        meta = {r["session_id"]: r for r in table.to_pylist() if r["session_id"] in sessions}
    except Exception as e:  # the corpus is not in CI
        print(f"\n(no agent or persona breakdown: {type(e).__name__}: {e})")
        return 0
    for field in ("agent", "user_persona"):
        print(f"\n== caught by {field}: real errors; every pushback")
        groups = defaultdict(list)
        for k in pushbacks:
            groups[str(meta.get(k[0], {}).get(field))].append(k)
        for g, keys in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"  {g:18s} {fmt(caught([k for k in keys if k in real]))};  {fmt(caught(keys))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
