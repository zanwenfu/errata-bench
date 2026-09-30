"""The recovery readers, changed for newer Claude Code (#16), read SWE-chat exactly as before: every transcript.

    .venv/bin/python checks/recover_over_corpus.py     # needs data/, a few minutes

`recover.has_transcript` now also recognizes Claude Code by its entries' shape,
and `recover.subagent_edits` also reads the collector's sub-agent transcripts.
Neither may change a thing for SWE-chat. Its corpus has no sub-agent
transcripts, and its transcripts must read the same as with the readers as they
were at 8a7817dc1 (copied below, unchanged). Checked on all of them, not a
sample.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from errata_bench.corpus import recover  # noqa: E402
from errata_bench.corpus.sessions import CORPUS  # noqa: E402

FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


def has_transcript_before(path: Path) -> bool:
    """`recover.has_transcript` as it was at 8a7817dc1."""
    if not path.is_file():
        return False
    with path.open() as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("type") is not None:
                return entry["type"] in recover.CLAUDE_CODE_TYPES
    return False


def subagent_edits_before(path: Path) -> list[dict]:
    """`recover.subagent_edits` as it was at 8a7817dc1."""
    if not path.is_file():
        return []
    parent, edits = {}, []
    with path.open() as fh:
        for line in fh:
            if '"agent_progress"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            data = entry.get("data") if isinstance(entry, dict) else None
            if not isinstance(data, dict) or data.get("type") != "agent_progress":
                continue
            outer = data.get("message")
            message = outer.get("message") if isinstance(outer, dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")):
                    continue
                parent[block["id"]] = entry.get("parentToolUseID")
                if block.get("name") in recover.SUBAGENT_WRITES:
                    given = block.get("input") if isinstance(block.get("input"), dict) else {}
                    edits.append({"id": block["id"], "tool": block["name"],
                                  "file_path": given.get("file_path") or given.get("notebook_path"),
                                  "parent": entry.get("parentToolUseID")})
    for e in edits:
        top, seen = e["parent"], set()
        while top in parent and top not in seen:
            seen.add(top)
            top = parent[top]
        e["spawned_by"] = top
    return edits


paths = sorted((CORPUS / "transcripts").glob("*.jsonl"))
check(len(paths) > 5000, f"the corpus's transcripts are here: {len(paths)}")
check(not (CORPUS / "subagents").exists(), "SWE-chat's corpus keeps no sub-agent transcripts, so none are read")
differ, edits_differ, sessions_with_edits, recognized = [], [], 0, 0
for p in paths:
    sid = p.stem
    now, before = recover.has_transcript(sid), has_transcript_before(p)
    recognized += now
    if now != before:
        differ.append(sid)
    if '"agent_progress"' in p.read_text(errors="replace"):
        e_now, e_before = recover.subagent_edits(sid), subagent_edits_before(p)
        sessions_with_edits += bool(e_before)
        if e_now != e_before:
            edits_differ.append(sid)
    check_unrecorded = recover.unrecorded_subagents(sid, [{"turn_type": "tool_use", "tool_name": "Agent",
                                                          "tool_call_id": "x", "turn_number": 0}])
    if check_unrecorded:
        FAIL.append(f"unrecorded_subagents fired on SWE-chat: {sid}")
check(not differ, f"has_transcript reads every transcript as before: {len(paths) - len(differ)} of {len(paths)} "
                  f"the same, {recognized} Claude Code" + (f"; differ: {differ[:5]}" if differ else ""))
check(not edits_differ, f"subagent_edits reads every transcript as before, {sessions_with_edits} with sub-agent edits"
                        + (f"; differ: {edits_differ[:5]}" if edits_differ else ""))
check(not any("unrecorded_subagents" in f for f in FAIL), "the new sub-agent gate never fires on SWE-chat")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
