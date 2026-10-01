"""The collector's rows against SWE-chat's own, for the same Claude Code sessions (#16).

    .venv/bin/python scripts/crawl_shape_vs_swechat.py [--sessions 300] [--seed 0]

`crawl.shape.claude_code_rows` is run on SWE-chat's raw transcripts and
compared with SWE-chat's conversations rows for the same sessions:

- every SWE-chat call, result and agent message must be among ours, with the
  same fields: a result may be longer in ours, where SWE-chat cut it;
- a user message is compared by its text, and each disagreement on who wrote
  it is counted by kind;
- ours may hold more rows, the blocks SWE-chat dropped (G-76, G-79), and they
  are counted.

Streams the corpus in batches and holds only the sampled sessions' rows.
Reads the corpus; writes nothing; no network and no model calls.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.compute as pc  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from errata_bench.corpus.sessions import CORPUS  # noqa: E402
from errata_bench.crawl.shape import claude_code_rows, read_entries  # noqa: E402

COLS = ["session_id", "turn_number", "role", "turn_type", "content", "tool_name", "tool_call_id", "file_path",
        "command", "pattern", "tool_input_json", "is_conversational", "model"]


def tag(text: str) -> str:
    m = re.match(r"\s*(<[a-z-]+>|\[Request interrupted|This session is being continued)", text or "")
    return m.group(1) if m else "plain"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    meta = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "agent", "repo_id"]).to_pylist()
    ids = sorted(r["session_id"] for r in meta if r["agent"] == "Claude Code"
                 and (CORPUS / "transcripts" / f"{r['session_id']}.jsonl").is_file())
    sample = sorted(random.Random(args.seed).sample(ids, args.sessions))
    theirs: dict[str, list[dict]] = defaultdict(list)
    wanted = pa.array(sample)
    for batch in pq.ParquetFile(CORPUS / "conversations.parquet").iter_batches(batch_size=200_000, columns=COLS):
        mask = pc.is_in(batch.column("session_id"), value_set=wanted)
        if pc.any(mask).as_py():
            for r in batch.filter(mask).to_pylist():
                theirs[r["session_id"]].append(r)

    c: Counter = Counter()
    user_kinds: Counter = Counter()
    extra: Counter = Counter()
    field_diffs: Counter = Counter()
    for sid in sample:
        ours = claude_code_rows(sid, "r", "r#x", read_entries(CORPUS / "transcripts" / f"{sid}.jsonl"))
        calls = {r["tool_call_id"]: r for r in ours if r["turn_type"] == "tool_use"}
        results = defaultdict(list)
        for r in ours:
            if r["turn_type"] == "tool_result":
                results[r["tool_call_id"]].append(r)
        agent_text = {r["content"] for r in ours if r["turn_type"] == "assistant_response"}
        user_by_text = {}
        for r in ours:
            if r["role"] == "user":
                user_by_text.setdefault((r["content"] or "").strip(), r["turn_type"])
        user_texts = [(r["content"] or "", r["turn_type"]) for r in ours if r["role"] == "user"]
        matched_calls, matched_results, matched_text = set(), set(), set()
        for t in theirs[sid]:
            kind = t["turn_type"]
            if kind == "tool_use":
                c["their calls"] += 1
                o = calls.get(t["tool_call_id"])
                if o is None:
                    c["their calls missing from ours"] += 1
                    continue
                matched_calls.add(t["tool_call_id"])
                for f in ("tool_name", "file_path", "command", "pattern"):
                    if (t[f] or None) != (o[f] or None):
                        field_diffs[f] += 1
                try:
                    same_input = json.loads(t["tool_input_json"] or "{}") == json.loads(o["tool_input_json"] or "{}")
                except ValueError:
                    same_input = False
                field_diffs["tool_input (as JSON)"] += not same_input
                field_diffs["tool_input (as text)"] += (t["tool_input_json"] or "") != (o["tool_input_json"] or "")
            elif kind == "tool_result":
                c["their results"] += 1
                cands = results.get(t["tool_call_id"]) or []
                theirs_text = t["content"] or ""
                if any(o["content"] == theirs_text for o in cands):
                    c["results identical"] += 1
                elif any(o["content"].startswith(theirs_text[:2000]) and len(o["content"]) > len(theirs_text)
                         for o in cands):
                    c["results longer in ours (SWE-chat cut them)"] += 1
                elif cands:
                    c["results differing"] += 1
                else:
                    c["their results missing from ours"] += 1
                if cands:
                    matched_results.add(t["tool_call_id"])
                    if t["tool_name"] and cands[0]["tool_name"] != t["tool_name"]:
                        field_diffs["result tool_name"] += 1
            elif kind == "assistant_response":
                c["their agent messages"] += 1
                if (t["content"] or "") in agent_text:
                    matched_text.add(t["content"])
                else:
                    c["their agent messages missing from ours"] += 1
            elif t["role"] == "user":
                c["their user rows"] += 1
                text = (t["content"] or "").strip()
                mine = user_by_text.get(text)
                if mine is None and text:  # SWE-chat split some messages; look for it inside one of ours
                    mine = next((k for u, k in user_texts if text in u), None)
                    c["their user rows found only inside a longer row of ours"] += mine is not None
                user_kinds[(tag(t["content"]), kind, mine or "absent")] += 1
        extra["calls"] += sum(1 for k in calls if k not in matched_calls)
        extra["agent messages"] += sum(1 for r in ours if r["turn_type"] == "assistant_response"
                                       and r["content"] not in matched_text)
        extra["thinking"] += sum(1 for r in ours if r["turn_type"] == "assistant_thinking")
        # Copies, which matching by text cannot see (G-84): a developer message
        # SWE-chat holds once and ours twice is a message said twice in ours.
        mine_n = Counter((r["content"] or "").strip() for r in ours if r["turn_type"] == "user_prompt")
        theirs_n = Counter((t["content"] or "").strip() for t in theirs[sid] if t["turn_type"] == "user_prompt")
        for text in mine_n.keys() & theirs_n.keys():
            c["developer messages held more times in ours"] += mine_n[text] > theirs_n[text]
            c["developer messages held fewer times in ours"] += mine_n[text] < theirs_n[text]
        c["our rows"] += len(ours)
        c["their rows"] += len(theirs[sid])

    print(f"{args.sessions} Claude Code sessions, seed {args.seed}")
    for k in ("their rows", "our rows", "their calls", "their calls missing from ours", "their results",
              "results identical", "results longer in ours (SWE-chat cut them)", "results differing",
              "their results missing from ours", "their agent messages", "their agent messages missing from ours",
              "their user rows", "their user rows found only inside a longer row of ours",
              "developer messages held more times in ours", "developer messages held fewer times in ours"):
        print(f"  {k}: {c[k]}")
    print("  fields differing on matched calls:", dict(field_diffs) or "none")
    print("  ours only (what SWE-chat dropped):", dict(extra))
    print("  user messages, SWE-chat's type vs ours, by opening tag:")
    for (t, theirs_kind, ours_kind), n in sorted(user_kinds.items(), key=lambda kv: -kv[1]):
        mark = "" if theirs_kind == ours_kind else "   <- differs"
        print(f"    {n:6d}  {t:28s} {theirs_kind:16s} -> {ours_kind}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
