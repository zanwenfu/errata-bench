"""What each side of the study sees: the developer's messages, the agent's reports, and a reviewer's window.

One place for the rules, because the reviewer (thread A), the classifier of the
developer's replies (thread B) and the merge must read the same text, and the
reviewer must never read anything the developer wrote after the work it judges.

- A **developer message** is a `user_prompt` row the developer typed. SWE-chat's
  table also files the agent's compaction summaries, Claude Code's notices,
  command echoes and shell output under `user_prompt` (G-90): those are not the
  developer writing, so they neither end a report nor appear in a window. A
  plan the developer approved ("Implement the following plan:") ends a report
  but is never pushback.
- A **report** is the agent's work between two developer messages, ending where
  the developer replies. Report k is judged by the reviewer and answered by
  reply k.
- A **window** is what the reviewer reads at report k: the session's first
  developer message (the task), the agent's last message before the request, the
  request itself, and the agent's work since then, ending with its report. A
  whole history is too long to read: on 40 sessions the history before a report
  had a median of 187,000 tokens (10-03, docs/study.md). The window's median is
  about 2,000.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from ..corpus.recover import NOT_A_PROMPT
from ..corpus.turns import COMPACTED, TURN_COLUMNS, build_excerpt

# SWE-chat's labels for a developer message that pushes back (run.py's PUSHBACK_KINDS).
PUSHBACK_KINDS = ("failure_report", "rejection", "correction", "takeover")
PLAN = "Implement the following plan"
# Rows that are neither a message nor work (as build_excerpt skips them).
NOISE = ("progress", "file_snapshot", "system_event", "queue_operation")
COLUMNS = [*TURN_COLUMNS, "is_continuation"]

# The window's parts, in characters (about 3.5 to a token).
TASK_CHARS = 6_000        # the session's first developer message
PREVIOUS_CHARS = 4_000    # the agent's last message before the request
REQUEST_CHARS = 8_000     # the developer message the agent was answering
WORK_CHARS = 60_000       # the agent's work since the request, its report included
# Each call's input and each result is cut to the first of these that fits the
# work into WORK_CHARS; every message stays whole. Past the last, the start of
# the stretch is left out and the window says how much.
TOOL_CAPS = (4_000, 2_000, 800, 300, 100)
# Where the work the reviewer judges begins. Above it is context: the task, the
# agent's previous message and the request. A problem is the reviewer's for this
# report only if its quote is below it (`work_part`), or the previous report's
# problem, quoted again from its last message, would count twice.
WORK_HEADER = "WHAT THE AGENT DID NEXT"


def load_turns(session_ids, corpus: Path | None = None) -> dict[str, list[dict]]:
    """Every turn of the given sessions, as `load_session_turns` returns them, plus `is_continuation`.

    Reads only the row groups that hold a wanted session: a row group's session
    ids first, then its other columns if it holds one. For 40 sessions this
    peaked at 135 MB, where streaming every batch of the 1.3 GB file did not
    fit the laptop's free memory (10-03).
    """
    import pyarrow.parquet as pq

    from ..corpus.sessions import CORPUS

    pf = pq.ParquetFile((corpus or CORPUS) / "conversations.parquet")
    want = set(session_ids)
    out: dict[str, list[dict]] = {s: [] for s in want}
    for i in range(pf.metadata.num_row_groups):
        ids = pf.read_row_group(i, columns=["session_id"]).column(0).to_pylist()
        if want.isdisjoint(ids):
            continue
        for row in pf.read_row_group(i, columns=COLUMNS).to_pylist():
            if row["session_id"] in want:
                out[row["session_id"]].append(row)
    for s in out:
        out[s].sort(key=lambda t: t["turn_number"] or 0)
    return out


def view_turns(session_id: str, turns: list[dict]) -> list[dict]:
    """The session as v1.1's candidates read it: lost calls and text put back, results whole, no thinking.

    The same three steps as `score.attempt.candidate_turns` for a task built
    with both (record 3): `recover` (G-76), `with_text` (G-79, which drops the
    agent's thinking: the developer did not see it, so the reviewer does not),
    then `whole_results`. Each returns the turns unchanged when the session has
    no transcript here.
    """
    from ..corpus.recover import recover, whole_results, with_text

    return whole_results(session_id, with_text(session_id, recover(session_id, turns)))


def prompt_kind(t: dict) -> str | None:
    """What a row is, if the developer wrote it: 'developer', 'plan' (approved), or None (not the developer)."""
    if t.get("turn_type") != "user_prompt":
        return None
    text = str(t.get("content") or "").lstrip()
    if not text or t.get("is_continuation") or text.startswith(COMPACTED) or text.startswith(NOT_A_PROMPT):
        return None
    return "plan" if text.startswith(PLAN) else "developer"


@dataclass
class Report:
    """One handback: the agent's work between the developer's request and the developer's reply."""

    session_id: str
    repo_id: str
    index: int                # 1 for the first report after the session's first developer message
    task_turn: float          # the session's first developer message
    request_turn: float       # the developer message the agent was answering
    handoff_turn: float       # the developer's reply, which ends the report
    reply_kind: str           # 'developer' or 'plan'
    reply: str                # the reply's text
    reply_label: str | None   # SWE-chat's pushback label on the reply, if any
    work_turns: int           # rows of the agent's work in the stretch
    interrupted: bool         # the developer interrupted the agent during the stretch


def reports(session_id: str, turns: list[dict], repo_id: str = "") -> list[Report]:
    """The session's reports, in order. Two developer messages with no work between them make none."""
    marks = [(t, prompt_kind(t)) for t in turns]
    marks = [(t, k) for t, k in marks if k]
    if not marks:
        return []
    task_turn = marks[0][0]["turn_number"]
    out: list[Report] = []
    for (request, _), (reply, kind) in zip(marks, marks[1:]):
        lo, hi = request["turn_number"], reply["turn_number"]
        inside = [t for t in turns if lo < (t.get("turn_number") or 0) < hi]
        work = [t for t in inside if t.get("turn_type") not in NOISE and t.get("turn_type") != "user_prompt"]
        if not work:
            continue
        out.append(Report(
            session_id=session_id, repo_id=repo_id, index=len(out) + 1, task_turn=task_turn,
            request_turn=lo, handoff_turn=hi, reply_kind=kind, reply=str(reply.get("content") or ""),
            reply_label=reply.get("prompt_pushback"), work_turns=len(work),
            interrupted=any(str(t.get("content") or "").lstrip().startswith("[Request interrupted")
                            for t in inside if t.get("turn_type") == "user_prompt")))
    return out


def _capped(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... {len(text) - limit:,} more characters of this message not shown]"


def window(report: Report, turns: list[dict]) -> tuple[str, dict]:
    """What the reviewer reads at a report, and how much of the stretch it shows.

    ``turns`` are the session's view turns (`view_turns`). Nothing at or after
    the developer's reply is rendered, and no row the developer did not write is
    shown as theirs: rows that are not the developer writing (notices, command
    output, compaction summaries) are left out of the stretch.
    """
    by_turn = {t.get("turn_number"): t for t in turns}
    parts: list[str] = []
    task = by_turn.get(report.task_turn)
    if task is not None and report.task_turn != report.request_turn:
        parts.append(f"THE DEVELOPER'S FIRST MESSAGE IN THIS SESSION (turn {report.task_turn:g}):\n"
                     f"{_capped(str(task.get('content') or ''), TASK_CHARS)}")
        earlier = [t for t in turns if report.task_turn < (t.get("turn_number") or 0) < report.request_turn
                   and t.get("turn_type") not in NOISE]
        if earlier:
            last = next((t for t in reversed(earlier) if t.get("turn_type") == "assistant_response"
                         and str(t.get("content") or "").strip()), None)
            parts.append(f"[... {len(earlier):,} rows between them not shown: the session's earlier work ...]")
            if last is not None:
                parts.append(f"THE AGENT'S LAST MESSAGE BEFORE THE REQUEST (turn {last.get('shown_as', last['turn_number']):g}):\n"
                             f"{_capped(str(last.get('content') or ''), PREVIOUS_CHARS)}")
    request = by_turn[report.request_turn]
    parts.append(f"THE DEVELOPER'S REQUEST (turn {report.request_turn:g}):\n"
                 f"{_capped(str(request.get('content') or ''), REQUEST_CHARS)}")

    work = [t for t in turns if report.request_turn < (t.get("turn_number") or 0) < report.handoff_turn
            and t.get("turn_type") != "user_prompt"]
    assert all((t.get("turn_number") or 0) < report.handoff_turn for t in work)
    cut = max((t["turn_number"] for t in work), default=report.request_turn)
    stats = {"tool_cap": None, "work_chars_left_out": 0, "rows_left_out_before_request": 0}
    shown = ""
    for cap in TOOL_CAPS:
        shown = build_excerpt(work, cut, max_chars=10**12, record=3, tool_cap=cap).strip()
        stats["tool_cap"] = cap
        if len(shown) <= WORK_CHARS:
            break
    if len(shown) > WORK_CHARS:
        stats["work_chars_left_out"] = len(shown) - WORK_CHARS
        shown = (f"[... the first {len(shown) - WORK_CHARS:,} characters of this stretch not shown ...]\n"
                 + shown[-WORK_CHARS:])
    parts.append(f"{WORK_HEADER} (turns after {report.request_turn:g}), ending with its report:\n{shown}")
    parts.append("[The agent's turn ends here. The developer has not replied yet.]")
    text = "\n\n".join(parts)
    stats["chars"] = len(text)
    return text, stats


def work_part(window_text: str) -> str:
    """The part of a window the reviewer judges: from WORK_HEADER on. Empty if the window has none."""
    at = window_text.rfind(WORK_HEADER)
    return window_text[at:] if at >= 0 else ""


def pilot_sessions(n: int = 40, per_repo: int = 2, seed: int = 20261003, runs: Path = Path("runs")) -> list[dict]:
    """The pilot's sessions: ones with a developer pushback read as a real agent error, at most `per_repo` a repository.

    Drawn from every reading under runs/ (a moment read twice counts once), so
    every session has at least one human pushback to compare against. The seed is
    fixed: the same call gives the same sessions (10-03, docs/study.md).
    """
    seen: dict[tuple, dict] = {}
    for p in sorted(runs.glob("*/readings.jsonl")):
        if any(s in p.parent.name for s in (".pre", "backup", "placeholder")):
            continue
        for line in p.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            reading = r.get("reading")
            if isinstance(reading, dict) and reading.get("objection_kind") == "real_error":
                seen.setdefault((r["session_id"], r.get("turn_number")), r)
    by_repo: dict[str, list[str]] = defaultdict(list)
    for (sid, _), r in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        if sid not in by_repo[r.get("repo_id") or ""]:
            by_repo[r.get("repo_id") or ""].append(sid)
    rng = random.Random(seed)
    pool = []
    for repo in sorted(by_repo):
        ids = by_repo[repo][:]
        rng.shuffle(ids)
        pool += [(sid, repo) for sid in ids[:per_repo]]
    rng.shuffle(pool)
    return [{"session_id": sid, "repo_id": repo} for sid, repo in pool[:n]]


def report_row(report: Report, text: str, stats: dict) -> dict:
    """A report as stored: its facts, the window the reviewer reads, and the window's measures."""
    return {**asdict(report), "window": text, "window_stats": stats}
