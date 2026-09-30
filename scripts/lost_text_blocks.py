"""Agent text the conversations table lost: measured on a corpus sample and on the released tasks (G-79, #17).

    .venv/bin/python scripts/lost_text_blocks.py release/v1.0.2-dataset

Claude Code writes each content block of an assistant message as its own
transcript entry, sharing the message id, and SWE-chat's conversations table
keeps only the last block of each message. G-76 found it for parallel calls;
this counts the text it drops.

1. A corpus sample: 150 Claude Code sessions drawn with seed 0. Each assistant
   text block of 20 or more characters is classed by whether a later block of
   the same message follows it, and looked for in the session's table rows.
2. The released tasks: the agent text blocks the raw transcript holds between
   a task's first and last shown call, looked for in its `conversation.txt`
   and `shown_turns.json`.
3. How much text that is: the absent blocks' characters against the
   characters of the conversation shown (both whitespace-collapsed), per task
   and in all; how many absent blocks are long (300+ characters); and how many
   hold a completion word (fixed, passes, deployed, done, ...), a rough marker
   of the claims a candidate might have read.

A block counts as present when its first 30 characters, or 30 from its middle,
appear in the text searched, whitespace collapsed. A block repeated elsewhere
in the session therefore counts as present; the counts of absent blocks are the
floor. Reads the corpus and the dataset; writes nothing; no model calls.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from errata_bench.corpus.sessions import CORPUS  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402

MIN_CHARS = 20
SAMPLE = 150
SEED = 0
LONG = 300
COMPLETION = re.compile(r"\b(fixed|passes|passing|tests pass|works now|deployed|verified|confirmed|succeeded|success|"
                        r"done|completed?|resolved|healthy)\b", re.I)


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def present(text: str, haystack: str) -> bool:
    t = norm(text)
    return t[:30] in haystack or t[len(t) // 2:len(t) // 2 + 30] in haystack


def entries(session_id: str) -> list[dict]:
    out = []
    for line in open(CORPUS / "transcripts" / f"{session_id}.jsonl", encoding="utf-8", errors="replace"):
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({})
    return out


def texts_by_message(raw: list[dict]) -> list[tuple[str, bool]]:
    """(text, followed by a later block of its message) for each long enough text block."""
    messages: OrderedDict[str, list[dict]] = OrderedDict()
    for i, x in enumerate(raw):
        m = x.get("message") or {}
        if x.get("type") != "assistant" or not isinstance(m.get("content"), list):
            continue
        messages.setdefault(m.get("id") or f"line-{i}", []).extend(b for b in m["content"] if isinstance(b, dict))
    out = []
    for blocks in messages.values():
        for i, b in enumerate(blocks):
            if b.get("type") == "text" and len(norm(b.get("text"))) >= MIN_CHARS:
                out.append((b["text"], i < len(blocks) - 1))
    return out


def corpus_sample() -> Counter:
    import pyarrow.parquet as pq

    rows = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "agent"]).to_pylist()
    ids = sorted(r["session_id"] for r in rows if r["agent"] in ("Claude Code", "claude-code")
                 and (CORPUS / "transcripts" / f"{r['session_id']}.jsonl").is_file())
    sample = random.Random(SEED).sample(ids, SAMPLE)
    turns = load_session_turns(set(sample))
    counts: Counter = Counter()
    for sid in sample:
        table = norm(" ".join(t.get("content") or "" for t in turns[sid]))
        for text, followed in texts_by_message(entries(sid)):
            kind = "followed by another block of its message" if followed else "last block of its message"
            counts[(kind, "present" if present(text, table) else "absent")] += 1
    return counts


def released_tasks(dataset: Path) -> tuple[int, int, int, int, int, Counter, dict]:
    total = absent_text = absent_rows = affected = 0
    shown_as: Counter = Counter()
    volume: dict = {"absent_chars": 0, "shown_chars": 0, "shares": [], "long": 0, "completion": 0,
                    "completion_tasks": 0}
    tasks = sorted(p for p in (dataset / "tasks").iterdir() if (p / "shown_turns.json").is_file())
    for task in tasks:
        shown = json.loads((task / "shown_turns.json").read_text())
        text = (task / "conversation.txt").read_text()
        for label in re.findall(r"^\[turn [0-9.]+\] (AGENT calls|-> result|AGENT \(thinking\)|AGENT|USER)", text, flags=re.M):
            shown_as[label] += 1
        conversation = norm(text)
        volume["shown_chars"] += len(conversation)
        absent_here = completion_here = 0
        rows = norm(" ".join(s.get("content") or "" for s in shown))
        ids = {s["tool_call_id"] for s in shown if s.get("tool_call_id")}
        raw = entries(shown[0]["session_id"])

        def ids_in(x: dict) -> set:
            c = (x.get("message") or {}).get("content")
            return ({b.get("id") or b.get("tool_use_id") for b in c if isinstance(b, dict)} - {None}
                    if isinstance(c, list) else set())

        at = [i for i, x in enumerate(raw) if ids_in(x) & ids]
        blocks = texts_by_message(raw[at[0]:at[-1] + 1]) if at else []
        lost_here = 0
        for block, _ in blocks:
            total += 1
            if not present(block, conversation):
                absent_text += 1
                lost_here += 1
                absent_here += len(norm(block))
                volume["long"] += len(norm(block)) >= LONG
                completion_here += bool(COMPLETION.search(block))
            absent_rows += not present(block, rows)
        affected += lost_here > 0
        volume["absent_chars"] += absent_here
        volume["shares"].append(absent_here / (absent_here + len(conversation)) if conversation else 0.0)
        volume["completion"] += completion_here
        volume["completion_tasks"] += completion_here > 0
    return len(tasks), total, absent_text, absent_rows, affected, shown_as, volume


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    counts = corpus_sample()
    print(f"Corpus sample: {SAMPLE} Claude Code sessions, seed {SEED}; agent text blocks of {MIN_CHARS}+ characters:")
    for kind in ("followed by another block of its message", "last block of its message"):
        a, p = counts[(kind, "absent")], counts[(kind, "present")]
        print(f"  {kind}: {a} of {a + p} absent from the table")
    n, total, absent_text, absent_rows, affected, shown_as, volume = released_tasks(Path(argv[0]))
    print(f"Released tasks: {n}; agent text blocks inside the shown span: {total}; absent from conversation.txt: "
          f"{absent_text} ({absent_text / total:.0%}); absent from shown_turns.json: {absent_rows}; "
          f"tasks with any absent: {affected}")
    print(f"Their conversations show {shown_as['AGENT calls']} calls, {shown_as['-> result']} results, "
          f"{shown_as['AGENT']} agent messages, {shown_as['AGENT (thinking)']} thinking and {shown_as['USER']} developer messages")
    shares = sorted(volume["shares"])
    quartiles = [shares[int(len(shares) * q)] for q in (0.25, 0.5, 0.75)]
    print(f"Absent text: {volume['absent_chars']:,} characters, against {volume['shown_chars']:,} characters of conversation "
          f"shown ({volume['absent_chars'] / (volume['absent_chars'] + volume['shown_chars']):.1%} of the two); "
          f"per task, the absent share's quartiles: {', '.join(f'{q:.1%}' for q in quartiles)}")
    print(f"Absent blocks of {LONG}+ characters: {volume['long']}; absent blocks with a completion word: "
          f"{volume['completion']}, in {volume['completion_tasks']} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
