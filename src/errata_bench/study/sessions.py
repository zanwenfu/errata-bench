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
import re
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


IMAGE = re.compile(r"\[Image[^\]]*\]")


def _norm(text: str) -> str:
    """A prompt's words, for matching the table against the transcript: image markers out, whitespace collapsed."""
    return " ".join(IMAGE.sub(" ", text or "").split())


@dataclass
class Typed:
    """What a session's raw transcript says about its user entries."""

    typed: list[str]   # entries the developer typed (`recover._prompt`), normalised
    other: list[str]   # user entries that are not: meta (a skill's or a command's text, a hook), results, sub-agents'


def transcript_prompts(session_id: str) -> Typed | None:
    """Which user entries the developer typed, from the raw transcript. None when the session has none here.

    SWE-chat's table files a skill's text, a command's expansion, a hook's
    message, a sub-agent's prompt and a tool's output under `user_prompt` too. In
    the 40 pilot sessions, 156 of the 1,070 rows the table alone would have taken
    for the developer's matched no typed entry (10-03): 78 were meta entries
    (skills, commands, hooks), 3 results, 15 nowhere in the transcript, and 60
    parts of one long typed message that the table splits into several rows. The
    transcript's own marks decide (`_prompt`: not meta, not a result, not a notice).
    """
    from ..corpus.recover import _prompt, has_transcript, transcript_path

    if not has_transcript(session_id):
        return None
    typed, other = [], []
    with transcript_path(session_id).open(errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("type") != "user":
                continue
            content = (e.get("message") or {}).get("content")
            if isinstance(content, list):
                text = " ".join(str(b.get("text") or b.get("content") or "") for b in content if isinstance(b, dict))
            else:
                text = content if isinstance(content, str) else ""
            (typed if not e.get("isSidechain") and _prompt(e) else other).append(_norm(text))
    return Typed(typed, other)


def prompt_kind(t: dict, transcript: Typed | None = None) -> str | None:
    """What a row is, if the developer wrote it: 'developer', 'plan' (approved), or None (not the developer).

    Without a transcript, the table's row and its text decide. With one, a row is
    the developer's only if its words are a typed entry's, or part of one (the
    table splits a long message); a row whose opening is in an entry that is not
    typed (a skill's text, a command's expansion, a result) is not. A row in
    neither keeps the table's word.
    """
    if t.get("turn_type") != "user_prompt":
        return None
    raw = str(t.get("content") or "").lstrip()
    if not raw or t.get("is_continuation") or raw.startswith(COMPACTED) or raw.startswith(NOT_A_PROMPT):
        return None
    kind = "plan" if raw.startswith(PLAN) else "developer"
    if transcript is None:
        return kind
    text = _norm(raw)
    if any(text == p or (len(text) >= 30 and text in p) for p in transcript.typed):
        return kind
    head = text[:60]
    if head and any(head in o for o in transcript.other):
        return None
    return kind


@dataclass
class Message:
    """One message from the developer: one row, or several in a row with no work between (a split or a queue)."""

    first: float
    last: float
    kind: str
    text: str
    label: str | None


def messages(turns: list[dict], transcript: Typed | None = None) -> list[Message]:
    """The developer's messages in order. Rows with no agent work between them are one message."""
    out: list[Message] = []
    since_work = True
    for t in turns:
        kind = prompt_kind(t, transcript)
        if kind:
            text, label = str(t.get("content") or "").strip(), t.get("prompt_pushback")
            if out and not since_work:
                m = out[-1]
                m.last, m.text = t["turn_number"], f"{m.text}\n\n{text}"
                m.kind = "developer" if "developer" in (m.kind, kind) else m.kind
                if m.label not in PUSHBACK_KINDS and label:
                    m.label = label
            else:
                out.append(Message(t["turn_number"], t["turn_number"], kind, text, label))
            since_work = False
        elif t.get("turn_type") not in NOISE and t.get("turn_type") != "user_prompt":
            since_work = True
    return out


@dataclass
class Report:
    """One handback: the agent's work between the developer's request and the developer's reply."""

    session_id: str
    repo_id: str
    index: int                # 1 for the first report after the session's first developer message
    task: str                 # the session's first developer message
    task_turn: float
    task_end: float
    request: str              # the developer message the agent was answering
    request_turn: float
    request_end: float
    handoff_turn: float       # where the developer's reply begins, which ends the report
    reply_kind: str           # 'developer' or 'plan'
    reply: str                # the reply's text
    reply_label: str | None   # SWE-chat's pushback label on the reply (any of its rows), if any
    work_turns: int           # rows of the agent's work in the stretch
    interrupted: bool         # the developer interrupted the agent during the stretch


def reports(session_id: str, turns: list[dict], repo_id: str = "", transcript: Typed | None = None) -> list[Report]:
    """The session's reports, in order: one for each developer message after the first."""
    msgs = messages(turns, transcript)
    if not msgs:
        return []
    task = msgs[0]
    out: list[Report] = []
    for request, reply in zip(msgs, msgs[1:]):
        inside = [t for t in turns if request.last < (t.get("turn_number") or 0) < reply.first]
        work = [t for t in inside if t.get("turn_type") not in NOISE and t.get("turn_type") != "user_prompt"]
        out.append(Report(
            session_id=session_id, repo_id=repo_id, index=len(out) + 1, task=task.text, task_turn=task.first,
            task_end=task.last, request=request.text, request_turn=request.first, request_end=request.last,
            handoff_turn=reply.first, reply_kind=reply.kind, reply=reply.text, reply_label=reply.label,
            work_turns=len(work),
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
    output, compaction summaries, a skill's text) are left out of the stretch.
    """
    parts: list[str] = []
    if report.task_turn != report.request_turn:
        parts.append(f"THE DEVELOPER'S FIRST MESSAGE IN THIS SESSION (turn {report.task_turn:g}):\n"
                     f"{_capped(report.task, TASK_CHARS)}")
        earlier = [t for t in turns if report.task_end < (t.get("turn_number") or 0) < report.request_turn
                   and t.get("turn_type") not in NOISE]
        if earlier:
            last = next((t for t in reversed(earlier) if t.get("turn_type") == "assistant_response"
                         and str(t.get("content") or "").strip()), None)
            parts.append(f"[... {len(earlier):,} rows between them not shown: the session's earlier work ...]")
            if last is not None:
                parts.append(f"THE AGENT'S LAST MESSAGE BEFORE THE REQUEST "
                             f"(turn {last.get('shown_as', last['turn_number']):g}):\n"
                             f"{_capped(str(last.get('content') or ''), PREVIOUS_CHARS)}")
    parts.append(f"THE DEVELOPER'S REQUEST (turn {report.request_turn:g}):\n{_capped(report.request, REQUEST_CHARS)}")

    work = [t for t in turns if report.request_end < (t.get("turn_number") or 0) < report.handoff_turn
            and t.get("turn_type") != "user_prompt"]
    assert all((t.get("turn_number") or 0) < report.handoff_turn for t in work)
    cut = max((t["turn_number"] for t in work), default=report.request_end)
    stats = {"tool_cap": None, "work_chars_left_out": 0}
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
    parts.append(f"{WORK_HEADER} (turns after {report.request_end:g}), ending with its report:\n{shown}")
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
