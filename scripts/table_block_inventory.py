"""Every kind of content the conversations table can lose, counted: G-79's preflight (#17).

    .venv/bin/python scripts/table_block_inventory.py release/v1.0.2-dataset

G-76 found SWE-chat's conversations table keeping one block of each assistant
message, and G-79 that the same rule drops the agent's text. Neither asked what
else the rule drops. This counts every kind of content a Claude Code transcript
holds for the main agent, by where it sits in its message, and whether the
stored rows have it:

- the agent's text, thinking and calls, by message (Claude Code writes each
  block as its own entry, sharing the message id), and by whether a later block
  of the same message follows;
- the developer's side: text (a plain string, or text blocks), images, and
  tool results.

1. A corpus sample of Claude Code sessions (seed 0): against the session's
   table rows.
2. The released tasks, from the session's start to its cut: against the task's
   `shown_turns.json`, what its conversation is rendered from.
3. The released tasks whose session is not Claude Code's: which agent, and
   whether its transcript can be read here at all.

A call or a result is present when a row of its kind carries its id. Text is
present when a row of its kind holds it, whitespace collapsed: exactly, or
containing it (a row may hold more than one block's text). Reads the corpus and
the dataset; writes nothing; no model calls.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from errata_bench.corpus.recover import has_transcript, transcript_path  # noqa: E402
from errata_bench.corpus.sessions import CORPUS  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402

SAMPLE = 300
SEED = 0
# The row kinds each block is looked for in. What a tool or Claude Code wrote
# into the user's turn (command output, task notifications, IDE context, system
# reminders) is kept as `system_injected` or `queue_operation`: 717 of the 720
# "absent" user texts of the first run were there (09-30).
ROW_KIND = {"text": ("assistant_response",), "thinking": ("assistant_thinking",),
            "user text": ("user_prompt", "system_injected", "queue_operation", "system_event")}


def norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def entries(session_id: str) -> list[dict]:
    out = []
    for line in open(transcript_path(session_id), encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def blocks_of(raw: list[dict]) -> list[dict]:
    """Each main-agent block in order: kind, position in its message, text or id, entry index."""
    out = []
    messages: OrderedDict[str, list[tuple[int, dict]]] = OrderedDict()
    for i, e in enumerate(raw):
        m = e.get("message")
        if e.get("isSidechain") or not isinstance(m, dict):
            continue
        content = m.get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            key = m.get("id") or f"entry-{i}"
            for b in content:
                if isinstance(b, dict):
                    messages.setdefault(key, []).append((i, b))
        elif e.get("type") == "user" and not e.get("isMeta"):
            if isinstance(content, str):
                if norm(content):
                    out.append({"kind": "user text", "form": "string", "text": content, "at": i})
                continue
            parts = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            texts = [b for b in parts if b.get("type") == "text" and norm(b.get("text"))]
            for b in parts:
                if b.get("type") == "tool_result":
                    out.append({"kind": "tool result", "id": b.get("tool_use_id"), "at": i})
                elif b.get("type") == "image":
                    out.append({"kind": "image", "with_text": bool(texts), "at": i})
            for j, b in enumerate(texts):
                out.append({"kind": "user text", "form": f"block {j + 1} of {len(texts)}"
                            + (" beside an image" if any(p.get("type") == "image" for p in parts) else ""),
                            "text": b.get("text"), "at": i})
    for key, blocks in messages.items():
        seen, unique = set(), []
        for i, b in blocks:
            ident = (b.get("type"), b.get("id") or norm(b.get("text") or b.get("thinking")))
            if ident in seen:
                continue
            seen.add(ident)
            unique.append((i, b))
        for j, (i, b) in enumerate(unique):
            kind = {"text": "text", "thinking": "thinking", "redacted_thinking": "thinking",
                    "tool_use": "call"}.get(b.get("type"), f"other: {b.get('type')}")
            out.append({"kind": kind, "last": j == len(unique) - 1, "at": i, "message": key,
                        "id": b.get("id"), "text": b.get("text") if kind == "text" else b.get("thinking")})
    return out


def present(block: dict, rows: list[dict]) -> str:
    """'exact', 'contained' or 'absent': whether ``rows`` hold this block."""
    kind = block["kind"]
    if kind in ("call", "tool result"):
        want = "tool_use" if kind == "call" else "tool_result"
        return "exact" if any(r.get("turn_type") == want and r.get("tool_call_id") == block["id"] for r in rows) \
            else "absent"
    if kind == "image":
        return "n/a"
    text = norm(block.get("text"))
    if not text:
        return "empty"
    kinds = ROW_KIND.get(kind, ())
    held = [norm(r.get("content")) for r in rows if r.get("turn_type") in kinds]
    if any(h == text for h in held):
        return "exact"
    if any(text in h for h in held):
        return "contained"
    return "absent"


def tally(blocks: list[dict], rows: list[dict], counts: Counter) -> None:
    for b in blocks:
        where = ("last in its message" if b.get("last") else "followed by another block") if "last" in b \
            else b.get("form") or ("beside text" if b.get("with_text") else "alone")
        counts[(b["kind"], where, present(b, rows))] += 1


def report(title: str, counts: Counter) -> None:
    print(title)
    groups: dict[tuple[str, str], Counter] = {}
    for (kind, where, state), n in counts.items():
        groups.setdefault((kind, where), Counter())[state] += n
    for (kind, where), c in sorted(groups.items()):
        total = sum(c.values())
        states = ", ".join(f"{s} {n}" for s, n in sorted(c.items()))
        print(f"  {kind:11s} {where:34s} {total:6d}: {states}")


def corpus_sample() -> Counter:
    import pyarrow.parquet as pq

    rows = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "agent"]).to_pylist()
    ids = sorted(r["session_id"] for r in rows if transcript_path(r["session_id"]).is_file()
                 and has_transcript(r["session_id"]))
    sample = random.Random(SEED).sample(ids, SAMPLE)
    turns = load_session_turns(set(sample))
    counts: Counter = Counter()
    for sid in sample:
        tally(blocks_of(entries(sid)), turns[sid], counts)
    return counts


def released_tasks(dataset: Path) -> tuple[Counter, list[str]]:
    counts: Counter = Counter()
    foreign = []
    for task in sorted(p for p in (dataset / "tasks").iterdir() if (p / "shown_turns.json").is_file()):
        shown = json.loads((task / "shown_turns.json").read_text())
        sid = shown[0]["session_id"]
        if not has_transcript(sid):
            foreign.append(task.name)
            continue
        raw = entries(sid)
        blocks = blocks_of(raw)
        # The span up to the cut. Anchored by ids: the last transcript entry
        # holding a call or result the task shows. Then on to the cut's own row
        # when it is text, the first match after that anchor; matching text
        # anywhere ran past the cut, since a short message ("yes") recurs.
        ids = {r.get("tool_call_id") for r in shown if r.get("tool_call_id")}
        end = max([b["at"] for b in blocks if b.get("id") in ids] or [-1])
        last = max(shown, key=lambda r: r.get("turn_number") or 0)
        if last.get("turn_type") in ("user_prompt", "assistant_response") and norm(last.get("content")):
            after = [b["at"] for b in blocks if b["at"] > end and b.get("text")
                     and norm(b["text"]) == norm(last.get("content"))]
            end = min(after) if after else end
        tally([b for b in blocks if b["at"] <= end], shown, counts)
    return counts, foreign


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    report(f"Corpus sample: {SAMPLE} Claude Code sessions, seed {SEED}; main agent only; against the table rows:",
           corpus_sample())
    counts, foreign = released_tasks(Path(argv[0]))
    report("Released tasks, from each session's start to its cut; against shown_turns.json:", counts)
    import pyarrow.parquet as pq

    agents = {r["session_id"]: r["agent"] for r in
              pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "agent"]).to_pylist()}
    for name in foreign:
        sid = json.loads((Path(argv[0]) / "tasks" / name / "shown_turns.json").read_text())[0]["session_id"]
        path = transcript_path(sid)
        print(f"Not Claude Code's: {name}: agent {agents.get(sid)!r}; transcript "
              f"{'present, ' + str(path.stat().st_size) + ' bytes' if path.is_file() else 'absent'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
