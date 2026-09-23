#!/usr/bin/env python3
"""The benchmark's funnel, counted: from every turn in SWE-chat to the frozen task list.

    scripts/funnel.py

Two parts, both counted rather than estimated.

The pool: the corpus filtered exactly as `run.py moments` collects moments --
the first developer message in a session that SWE-chat labels as pushing back
(one of four kinds), in a repository the corpus names, with at least three
agent turns before it, in a language the benchmark has a container for.

What was drawn: every run directory under runs/, each stage's rows restricted
to moments in that pool, counted as distinct moments (or, once one moment per
session has been kept, distinct sessions) that passed the stage in any run.
Older runs collected moments under looser rules before the pool's filters
existed; they are not counted. A screening gate passed means held on every
reading ("3/3"). Needs the full corpus (data/swe-chat/); prints, writes nothing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pyarrow.parquet as pq  # noqa: E402

from errata_bench.construct.container import can_be_sandboxed  # noqa: E402
from errata_bench.corpus.sessions import CORPUS, load_repos  # noqa: E402

KINDS = {"failure_report", "rejection", "correction", "takeover"}
MIN_AGENT_TURNS = 3


def rows(p: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in p.open() if line.strip()]
    except FileNotFoundError:
        return []


def yes(v) -> bool:
    """True, "True", or a gate held on every reading: "3/3"."""
    if v is True or str(v).lower() == "true":
        return True
    s = str(v)
    if "/" in s:
        k, _, n = s.partition("/")
        return k.isdigit() and n.isdigit() and int(n) > 0 and k == n
    return False


def pool() -> tuple[dict, dict]:
    repo_of = dict(zip(*[pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "repo_id"])
                         .column(c).to_pylist() for c in ("session_id", "repo_id")]))
    languages = {rid: repo.language for rid, repo in load_repos().items()}
    counts = defaultdict(int)
    first: dict[str, tuple[int, str]] = {}
    conv = pq.ParquetFile(CORPUS / "conversations.parquet")
    for b in conv.iter_batches(batch_size=250_000, columns=["session_id", "turn_number", "turn_type", "prompt_pushback"]):
        s, n, t, k = (b.column(i).to_pylist() for i in range(4))
        counts["turns"] += len(s)
        for i in range(len(s)):
            if t[i] != "user_prompt":
                continue
            counts["developer messages"] += 1
            if k[i] not in KINDS:
                continue
            counts["labelled as pushing back"] += 1
            if s[i] not in first or n[i] < first[s[i]][0]:
                first[s[i]] = (n[i], k[i])
    counts["the first in its session"] = len(first)
    named = {sid: v for sid, v in first.items() if repo_of.get(sid)}
    counts["in a repository the corpus names"] = len(named)
    acted = defaultdict(int)
    for b in conv.iter_batches(batch_size=250_000, columns=["session_id", "turn_number", "turn_type"]):
        s, n, t = (b.column(i).to_pylist() for i in range(3))
        for i in range(len(s)):
            m = named.get(s[i])
            if m and n[i] < m[0] and t[i] in ("assistant_response", "tool_use"):
                acted[s[i]] += 1
    enough = {sid: v for sid, v in named.items() if acted[sid] >= MIN_AGENT_TURNS}
    counts["with 3 or more agent turns before it"] = len(enough)
    runnable = {sid: v for sid, v in enough.items() if can_be_sandboxed(languages.get(repo_of[sid]))}
    counts["in a language with a container"] = len(runnable)
    counts["repositories, addressable"] = len({repo_of[s] for s in runnable})
    counts["sessions in the corpus"] = len(repo_of)
    return counts, {(sid, int(v[0])) for sid, v in runnable.items()}


def main() -> int:
    counts, addressable = pool()
    print("The pool")
    for k in ("sessions in the corpus", "turns", "developer messages", "labelled as pushing back",
              "the first in its session", "in a repository the corpus names",
              "with 3 or more agent turns before it", "in a language with a container",
              "repositories, addressable"):
        print(f"  {k:40s} {counts[k]:>10,}")
    sessions = {s for s, _ in addressable}
    key = lambda r: (r.get("session_id"), int(r["turn_number"])) if r.get("turn_number") is not None else None
    stage = defaultdict(set)
    for d in sorted(p for p in (ROOT / "runs").iterdir() if p.is_dir()):
        for f in ("moments", "triaged", "readings"):
            for r in rows(d / f"{f}.jsonl"):
                k = key(r)
                if k not in addressable:
                    continue
                stage["drawn"].add(k)
                if f == "triaged":
                    stage["triaged"].add(k)
                    if yes(r.get("worth_reading")):
                        stage["worth reading"].add(k)
                if f == "readings" and isinstance(r.get("reading"), dict) and r["reading"]:
                    stage["read"].add(k)
                    if yes(r["reading"].get("benchmark_viable")):
                        stage["a genuine agent error"].add(k)
        for r in rows(d / "trajectories.jsonl"):
            if r.get("session_id") in sessions:
                stage["four turns sought"].add(r["session_id"])
                if yes(r.get("usable")):
                    stage["located, and resolved"].add(r["session_id"])
        for r in rows(d / "signatures.jsonl"):
            if r.get("session_id") in sessions and yes(r.get("usable")):
                stage["a defect signature"].add(r["session_id"])
        for r in rows(d / "screened.jsonl"):
            if r.get("session_id") in sessions and all(
                    yes(r.get(g)) for g in ("asks_for_something_held", "within_scope_held", "clean_held")):
                stage["all three screening gates"].add(r["session_id"])
        for r in rows(d / "tasks.jsonl"):
            if r.get("session_id") in sessions:
                stage["built"].add(r["task_id"])
    print("\nWhat was drawn from it, in any run")
    for k in ("drawn", "triaged", "worth reading", "read", "a genuine agent error", "four turns sought",
              "located, and resolved", "a defect signature", "all three screening gates", "built"):
        print(f"  {k:40s} {len(stage[k]):>10,}")
    frozen = set(json.loads((ROOT / "runs" / "grid1-TASKS.json").read_text()))
    print(f"\n  frozen for the first grid               {len(frozen):>10,}  (all built: {frozen <= stage['built']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
