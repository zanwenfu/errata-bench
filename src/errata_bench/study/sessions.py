"""What each side of the study sees: the developer's messages, the agent's reports, and a reviewer's window.

One place for the rules, because the reviewer (thread A), the classifier of the
developer's replies (thread B) and the merge must read the same text, and the
reviewer must never read anything the developer wrote after the work it judges.

- A **developer message** is something the developer said to the agent: a
  prompt they typed, a slash command with its arguments, a message typed while
  the agent was working (queued), or a tool call they rejected, with the words
  they gave. SWE-chat's table also files compaction summaries, Claude Code's
  notices, command and skill expansions, teammate agents' messages, sub-agent
  prompts, tool output and re-inserted copies of earlier messages under
  `user_prompt` (G-90). With the raw transcript, a prompt is the developer's only
  if it matches, in order, an entry the developer typed (`row_kinds`). A plan
  the developer approved ends a report but is never pushback.
- A **report** is the agent's work between two developer messages, ending where
  the developer speaks. Report k is judged by the reviewer and answered by reply k.
- A **window** is what the reviewer reads at report k: the session's first
  developer message (the task), the agent's last message before the request, the
  request itself, and the agent's work since then. A whole history is too long to
  read: on 40 sessions the history before a report had a median of 187,000 tokens
  (10-03, docs/study.md).
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
# Rows that are neither a message nor work. build_excerpt renders none of them;
# `system_injected` (local-command echoes) and `summary` were missing at first and
# would have counted as work (10-03 review).
NOISE = ("progress", "file_snapshot", "system_event", "queue_operation", "system_injected", "summary")
# Prompt rows that are never the developer speaking: Claude Code's notices and
# command echoes (NOT_A_PROMPT), and another agent's message to this one.
NOT_THE_DEVELOPER = (*NOT_A_PROMPT, "<teammate-message")
# A tool call the developer refused, as Claude Code writes the call's result, and
# the note newer versions append to the developer's words (10-03 second review).
REJECTED = "The user doesn't want to"
SAID = "To tell you how to proceed, the user said:"
NOTE = "Note: The user's next message may contain"
# Claude Code's own text, filed as the developer's or the agent's: an Ultraplan
# notice, and a message it writes for the agent ("Prompt is too long", a rate limit,
# "No response requested."), which the table records under model "<synthetic>".
NOTICES = ("◇ ultraplan", "Plan being refined via Ultraplan")
PLANS = (PLAN, "Ultraplan approved in browser")
SYNTHETIC = "<synthetic>"


def _synthetic(t: dict) -> bool:
    """A message Claude Code wrote, not the agent's model: never the agent's work or its report."""
    return t.get("turn_type") == "assistant_response" and t.get("model") == SYNTHETIC
COLUMNS = [*TURN_COLUMNS, "is_continuation", "model"]

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
# Two copies of one message can differ only where one was anonymised: SWE-chat's
# table keeps an email the transcript anonymised, or redacts an environment line the
# transcript keeps (515dde7d's task, 95583c67's email, 10-03 second review).
PLACEHOLDER = re.compile(r"<PRESIDIO_ANONYMIZED_[A-Z_]+>|\[REDACTED(?::[^\]]*)?\]|\bREDACTED\b|[\w.+-]+@[\w-]+\.[\w.-]+")


def _norm(text: str) -> str:
    """A prompt's words: image markers out, whitespace collapsed."""
    return " ".join(IMAGE.sub(" ", text or "").split())


def _key(text: str) -> str:
    """What two copies of one message share: `_norm`, with every anonymised span one token."""
    return PLACEHOLDER.sub("<X>", _norm(text))


@dataclass
class Typed:
    """What a session's raw transcript says the developer said."""

    typed: list[str]      # prompts the developer typed (`recover._prompt`), as `_key`, in the transcript's order
    commands: set[str]    # slash commands they typed, as name and arguments (`_command`), `_key`
    queued: set[str]      # messages they typed while the agent was working (enqueued), `_key`
    other: set[str]       # every other user entry, `_key`: meta, notices, results, summaries, teammates, sub-agents
    automatic: set[str] = None   # prompts nobody typed: a /loop's or a scheduled wake-up's, one enqueued again and again


def transcript_prompts(session_id: str) -> Typed | None:
    """What the developer said, from the raw transcript's own marks. None when the session has no transcript here.

    A typed prompt is a main-thread user entry `recover._prompt` accepts (not
    meta, not a result, not a notice), and not a compaction summary
    (`isCompactSummary`, `isVisibleInTranscriptOnly`) or a teammate agent's message
    (wrapped in `<teammate-message`, or written while a team runs, `teamName`,
    without the `permissionMode` every typed entry carries: the developer's own
    messages in a team session have `teamName` too).
    """
    from ..corpus.recover import _prompt, has_transcript, transcript_path

    if not has_transcript(session_id):
        return None
    typed: list[str] = []
    commands: set[str] = set()
    queued: set[str] = set()
    other: set[str] = set()
    scheduled: set[str] = set()
    times: dict[str, int] = defaultdict(int)
    with transcript_path(session_id).open(errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get("type") == "queue-operation":
                if e.get("operation") == "enqueue" and isinstance(e.get("content"), str):
                    queued.add(_key(e["content"]))
                    times[_key(e["content"])] += 1
                continue
            message = e.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if e.get("type") == "assistant" and isinstance(content, list):
                # The agent can schedule its own prompt: a wake-up or a cron job, often a /loop (bb028fb8).
                for b in content:
                    if (isinstance(b, dict) and b.get("type") == "tool_use"
                            and b.get("name") in ("ScheduleWakeup", "CronCreate")
                            and isinstance((b.get("input") or {}).get("prompt"), str)):
                        scheduled.add(b["input"]["prompt"])
                continue
            if e.get("type") != "user":
                continue
            if isinstance(content, list):
                text = " ".join(str(b.get("text") or b.get("content") or "") for b in content if isinstance(b, dict))
            else:
                text = content if isinstance(content, str) else ""
            key = _key(text)
            teammate = key.startswith("<teammate-message") or (e.get("teamName") and not e.get("permissionMode"))
            if e.get("isSidechain") or e.get("isCompactSummary") or e.get("isVisibleInTranscriptOnly") or teammate:
                other.add(key)
            elif _prompt(e):
                typed.append(key)
            elif not e.get("isMeta") and "<command-name>" in text:
                commands.add(_key(_command(text)))
            else:
                other.add(key)
    # A /loop re-sends its prompt on a timer, and the agent can schedule its own: those
    # firings are not the developer typing. The /loop the developer typed is.
    loops = {c.split(" ", 1)[1] for c in (*commands, *typed, *queued) if c.startswith("/loop ") and " " in c}
    loops |= {_key(x) for x in scheduled}
    loops |= {_key(x.split(" ", 1)[1]) for x in scheduled if x.startswith("/loop ") and " " in x}
    loops = {re.sub(r"^\d+\s*[smhd]\w*\s+", "", x) for x in loops} | loops
    automatic = {q for q, k in times.items() if k > 1} | {x for x in loops if x.strip()}
    return Typed(typed, commands, queued, other, automatic)


def _command(raw: str) -> str:
    """A slash command as the developer typed it: its name and its arguments."""
    name = re.search(r"<command-name>(.*?)</command-name>", raw, re.S)
    args = re.search(r"<command-args>(.*?)</command-args>", raw, re.S)
    return " ".join(x for x in ((name.group(1).strip() if name else ""), (args.group(1).strip() if args else "")) if x)


def _match(key: str, typed: list[str], after: int, same_message: bool) -> int | None:
    """The typed entry, in order, that this prompt row is or is part of. None if there is none.

    In order, each entry used by one message only: SWE-chat's table re-inserts an
    earlier message later, cut into sections (49 rows in the pilot's sessions,
    10-03 review), and a copy matches only an entry an earlier message used. Rows of
    one typed message, with no work between them, may share its entry (a split).
    Exactly, or as part of an entry, searching ahead; failing that, the next
    expected entry alone, if it opens the same way and is at least 90% alike (an
    anonymisation differing). Never a near match further ahead.
    """
    for j in range(after if same_message else after + 1, len(typed)):
        if j < 0:
            continue
        if key == typed[j] or (len(key) >= 30 and key in typed[j]):
            return j
    j = after + 1
    if 0 <= j < len(typed) and len(key) >= 30 and key[:30] == typed[j][:30]:
        from difflib import SequenceMatcher

        if SequenceMatcher(None, key[:3000], typed[j][:3000], autojunk=False).ratio() >= 0.9:
            return j
    return None


def _automatic(key: str, automatic: set[str]) -> bool:
    """Whether a prompt is one nobody typed. Exactly, or for texts of 30 characters or more, by their opening."""
    for a in automatic:
        if not a:
            continue
        if a == key:
            return True
        if len(a) >= 30 and len(key) >= 30 and (a.startswith(key[:60]) or key.startswith(a[:60])):
            return True
    return False


def row_kinds(turns: list[dict], transcript: Typed | None = None) -> dict:
    """Which rows are the developer speaking: {turn_number: (kind, text, SWE-chat's label)}.

    Kinds: 'developer' (a typed prompt), 'plan' (a plan approved), 'command' (a
    slash command and its arguments), 'queued' (typed while the agent worked),
    'rejection' (a tool call refused, with any words given). With a transcript a
    prompt, a command or a queued message counts only if the transcript holds it
    as the developer's; without one, the table's row and its text decide.
    """
    out: dict = {}
    at = -1               # the last typed entry a message used
    open_typed = False    # the developer's last row was a typed prompt, and no agent work since
    open_message = False  # the developer spoke, and no agent work since
    # A message queued while the agent worked is counted where it was typed. Delivered
    # later as a prompt or a command, its second row is not a second message (13 of 49
    # queued messages in the pilot's sessions came again that way, 10-03); its label is.
    waiting: dict[str, float] = {}
    fired: set[str] = set()   # queued texts and /loop commands already counted
    typed_keys = set(transcript.typed) if transcript is not None else set()
    automatic = (transcript.automatic or set()) if transcript is not None else set()
    for t in turns:
        n, kind, raw = t.get("turn_number"), t.get("turn_type"), str(t.get("content") or "")
        if kind == "tool_result" and raw.startswith(REJECTED):
            said = raw.split(SAID, 1)[1] if SAID in raw else ""
            said = said.split(NOTE, 1)[0].strip()
            if said.startswith(NOTICES):
                continue   # Claude Code refused it for its own flow (Ultraplan), not the developer (9b0c44bf)
            out[n] = ("rejection", said or "[The developer rejected this tool call.]", None)
            open_message, open_typed = True, False
            continue
        if kind == "queue_operation":
            text = raw.strip()
            if text.startswith("{"):
                try:
                    obj = json.loads(text)
                except ValueError:
                    obj = None
                # The table can keep the whole queue entry; only an enqueue's content was typed.
                text = (obj.get("content") or "").strip() if (isinstance(obj, dict) and obj.get("type") == "queue-operation"
                                                              and obj.get("operation") == "enqueue") else ""
            key = _key(text)
            if (not text or text.startswith(("<", *NOTICES)) or key in fired or re.fullmatch(r"/[\w:.-]+", text)
                    or _automatic(key, automatic)):
                continue
            if transcript is not None and (key not in transcript.queued or (key in transcript.other
                                                                           and key not in typed_keys)):
                continue   # not enqueued, or enqueued by Claude Code itself and delivered as meta
            fired.add(key)
            out[n] = ("plan" if text.startswith(PLANS) else "queued", text, None)
            waiting[key] = n
            open_message, open_typed = True, False
            continue
        if kind != "user_prompt":
            if kind not in NOISE and not _synthetic(t):
                open_message = open_typed = False
            continue
        s = raw.strip()
        if not s or t.get("is_continuation") or s.startswith(COMPACTED):
            continue
        label = t.get("prompt_pushback")
        if "<command-name>" in s[:300]:
            said = _command(s)
            key = _key(said)
            if key in waiting:   # the delivery of a command queued while the agent worked
                q = waiting.pop(key)
                out[q] = (out[q][0], out[q][1], out[q][2] or label)
                continue
            if not said or (said.startswith("/loop") and key in fired) or _automatic(key, automatic):
                continue
            if transcript is None or key in transcript.commands:
                fired.add(key)
                out[n] = ("command", said, label)
                open_message, open_typed = True, False
            continue
        if s.startswith((*NOT_THE_DEVELOPER, *NOTICES)):
            continue
        key = _key(s)
        if key == "":
            # A message that is only an image: part of the message the developer is
            # typing, or the next typed entry if that is empty too (3c9c6a1b, 10-03).
            if transcript is not None and not open_typed:
                if not (at + 1 < len(transcript.typed) and transcript.typed[at + 1] == ""):
                    continue
                at += 1
        else:
            if key in waiting:   # the delivery of a message queued while the agent worked
                q = waiting.pop(key)
                out[q] = (out[q][0], out[q][1], out[q][2] or label)
                if transcript is not None:
                    j = _match(key, transcript.typed, at, open_typed)
                    at = j if j is not None else at
                continue
            if transcript is not None:
                if key in transcript.other and key not in typed_keys:
                    continue
                j = _match(key, transcript.typed, at, open_typed)
                if j is None:
                    continue
                at = j
        out[n] = ("plan" if s.startswith(PLANS) else "developer", s, label)
        open_message = open_typed = True
    return out


def prompt_kind(t: dict, transcript: Typed | None = None) -> str | None:
    """One row's kind on its own, without the order a whole session gives (`row_kinds` decides for a session)."""
    got = row_kinds([t], transcript)
    return got[t.get("turn_number")][0] if t.get("turn_number") in got else None


@dataclass
class Message:
    """One time the developer spoke: one row, or several with no agent work between them."""

    first: float
    last: float
    kinds: list[str]
    text: str
    label: str | None

    @property
    def kind(self) -> str:
        return "+".join(dict.fromkeys(self.kinds))


def messages(turns: list[dict], transcript: Typed | None = None) -> list[Message]:
    """The developer's messages in order. Rows with no agent work between them are one message."""
    said = row_kinds(turns, transcript)
    out: list[Message] = []
    since_work = True
    for t in turns:
        n = t.get("turn_number")
        if n in said:
            kind, text, label = said[n]
            if out and not since_work:
                m = out[-1]
                m.last, m.text = n, f"{m.text}\n\n{text}"
                m.kinds.append(kind)
                if m.label not in PUSHBACK_KINDS and label:
                    m.label = label
            else:
                out.append(Message(n, n, [kind], text, label))
            since_work = False
        elif t.get("turn_type") not in NOISE and t.get("turn_type") != "user_prompt" and not _synthetic(t):
            since_work = True
    return out


@dataclass
class Report:
    """One handback: the agent's work between the developer's request and what the developer said next."""

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
    reply_kind: str           # its rows' kinds: 'developer', 'plan', 'command', 'queued', 'rejection', joined by +
    reply: str                # the reply's text
    reply_label: str | None   # SWE-chat's pushback label on the reply (any of its prompt rows), if any
    work_turns: int           # rows of the agent's work in the stretch
    ends_with_report: bool    # the stretch's last row is the agent writing, not a call or a result
    interrupted: bool         # the developer interrupted the agent during the stretch


def reports(session_id: str, turns: list[dict], repo_id: str = "", transcript: Typed | None = None) -> list[Report]:
    """The session's reports, in order: one for each time the developer spoke after the first."""
    msgs = messages(turns, transcript)
    if not msgs:
        return []
    task = msgs[0]
    said = {n for m in msgs for n in (m.first, m.last)}
    out: list[Report] = []
    for request, reply in zip(msgs, msgs[1:]):
        inside = [t for t in turns if request.last < (t.get("turn_number") or 0) < reply.first]
        work = [t for t in inside if t.get("turn_type") not in NOISE and t.get("turn_type") != "user_prompt"
                and t.get("turn_number") not in said and not _synthetic(t)]
        out.append(Report(
            session_id=session_id, repo_id=repo_id, index=len(out) + 1, task=task.text, task_turn=task.first,
            task_end=task.last, request=request.text, request_turn=request.first, request_end=request.last,
            handoff_turn=reply.first, reply_kind=reply.kind, reply=reply.text, reply_label=reply.label,
            work_turns=len(work), ends_with_report=bool(work) and work[-1].get("turn_type") == "assistant_response",
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
                         and str(t.get("content") or "").strip() and not _synthetic(t)), None)
            parts.append(f"[... {len(earlier):,} rows between them not shown: the session's earlier work ...]")
            if last is not None:
                parts.append(f"THE AGENT'S LAST MESSAGE BEFORE THE REQUEST "
                             f"(turn {last.get('shown_as', last['turn_number']):g}):\n"
                             f"{_capped(str(last.get('content') or ''), PREVIOUS_CHARS)}")
    parts.append(f"THE DEVELOPER'S REQUEST (turn {report.request_turn:g}):\n{_capped(report.request, REQUEST_CHARS)}")

    work = [t for t in turns if report.request_end < (t.get("turn_number") or 0) < report.handoff_turn
            and t.get("turn_type") != "user_prompt" and not _synthetic(t)]
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
    ending = "ending with its report" if report.ends_with_report else "ending before it wrote a report"
    parts.append(f"{WORK_HEADER} (turns after {report.request_end:g}), {ending}:\n{shown}")
    if not report.ends_with_report:
        parts.append("[The agent had not written a report: its last step above is a tool call or its result.]")
    parts.append("[The agent's turn ends here. The developer has not replied yet.]")
    text = "\n\n".join(parts)
    stats["chars"] = len(text)
    return text, stats


def work_part(window_text: str) -> str:
    """The part of a window the reviewer judges: from WORK_HEADER on. Empty if the window has none."""
    at = window_text.rfind(WORK_HEADER)
    return window_text[at:] if at >= 0 else ""


def pilot_sessions(n: int = 40, per_repo: int = 2, seed: int = 20261003, runs: Path = Path("runs"),
                   skip: int = 0) -> list[dict]:
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
    return [{"session_id": sid, "repo_id": repo} for sid, repo in pool[skip:skip + n]]


def random_sessions(n: int = 40, per_repo: int = 2, seed: int = 20261004, exclude=(), corpus: Path | None = None
                    ) -> list[dict]:
    """Sessions drawn without regard to pushback: the comparison arm (docs/study.md).

    Claude Code sessions (SWE-chat's `agent`) with at least two developer
    prompts and a transcript here, at most `per_repo` a repository, none in
    `exclude`. The pilot's sessions were chosen for holding a real error, so its
    rates hold for those; this arm's hold for sessions in general. Of SWE-chat's
    4,852 Claude Code sessions, 3,857 have two prompts or more (10-03).
    """
    import pyarrow.parquet as pq

    from ..corpus.recover import has_transcript
    from ..corpus.sessions import CORPUS

    rows = pq.read_table((corpus or CORPUS) / "sessions.parquet",
                         columns=["session_id", "repo_id", "agent", "prompt_count"]).to_pylist()
    skip = set(exclude)
    pool = sorted((r for r in rows if r["agent"] == "Claude Code" and (r["prompt_count"] or 0) >= 2
                   and r["session_id"] not in skip), key=lambda r: r["session_id"])
    rng = random.Random(seed)
    rng.shuffle(pool)
    out, per = [], defaultdict(int)
    for r in pool:
        repo = r["repo_id"] or ""
        if per[repo] >= per_repo or not has_transcript(r["session_id"]):
            continue
        out.append({"session_id": r["session_id"], "repo_id": repo})
        per[repo] += 1
        if len(out) == n:
            break
    return out


def report_row(report: Report, text: str, stats: dict) -> dict:
    """A report as stored: its facts, the window the reviewer reads, and the window's measures."""
    return {**asdict(report), "window": text, "window_stats": stats}
