"""A Harbor job's trials read into the answers this harness grades (v1 step 4).

Each trial of an errata-bench task that Harbor ran leaves, in its folder:

  result.json                 which task and agent, when, and any exception;
  lock.json                   what exactly ran: the task's content digest, the
                              agent and its settings, and any host added to the
                              network's allowlist for the run;
  verifier/answer.json        the answer, every call and what changed
                              (`release.verify`);
  agent/reference-agent.json  for errata-bench's reference agent only: how it
                              ended and what it cost.

`answer_row` makes of these the row the attempt stage writes for this
harness's own candidates (`stages.scoring.stage_attempt`), so the grading
stage grades it unchanged: three readings, settled by majority, on the tasks
the judge is admitted to. What differs is said on the row: the environment
(`harbor:<agent>`), what the agent was told it had (`rules`), and where it
came from (`harbor`).

A trial is graded only when it ran to its verifier and the agent's run is on
record. One whose agent ran out of time is a result -- it did not answer in
time -- as in the harness. One that failed for any other reason (the agent
would not start, its provider refused, the container would not build) is not:
it is recorded as an error, to be run again, and counted nowhere. So is an
agent that wrote no trajectory: its answer cannot be read, which is not the
same as an agent that gave none.

`official` says whether a trial ran the task as published: its content
digest the release's (`errata_harbor.digests`), no host added to the network's
allowlist, the verifier run in the agent's container, and for Claude Code and
Codex their own web search off (it reaches the web from the provider's side,
past the container's network).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .atif import harness_tool

# Agents Harbor runs whose own web search reaches past the container's network,
# and the setting that turns it off (Harbor's source, 09-27).
WEB_SEARCH_OFF = {"claude-code": "disable_web_search", "codex": "disable_web_search"}
TIMED_OUT = "AgentTimeoutError"
NOTE = ("The agent was {agent}, with its own tools, not this harness's. It worked in a container built for this "
        "task: the repository at the path the developer had it, with its git history and its dependencies "
        "installed. {network}")
CLOSED = "The network was closed while it worked, apart from model APIs."
UNKNOWN = ("The task was not run as published, so its network may have been open while it worked: nothing here "
           "says which.")


@dataclass
class Trial:
    """One Harbor trial's folder, read."""

    path: Path
    result: dict
    lock: dict
    answer: dict | None
    reference: dict | None
    task_id: str = ""
    started: str = ""

    @property
    def name(self) -> str:
        return str(self.result.get("trial_name") or self.path.name)

    @property
    def agent(self) -> str:
        return str((self.result.get("agent_info") or {}).get("name") or "?")

    @property
    def model(self) -> str:
        info = (self.result.get("agent_info") or {}).get("model_info") or {}
        name, provider = info.get("name"), info.get("provider")
        return (f"{provider}/{name}" if provider and name else str(name or
                (self.lock.get("agent") or {}).get("model_name") or "?"))

    @property
    def exception(self) -> str:
        return str((self.result.get("exception_info") or {}).get("exception_type") or "")


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_trials(jobs: list[Path]) -> list[Trial]:
    """Every trial in the given job folders (or trial folders), in the order they started."""
    found = []
    for job in jobs:
        # A trial's result.json names its task; a job's, beside its trials'
        # folders, does not (it holds the job's totals).
        own = _json(job / "result.json") or {}
        folders = [job] if "task_name" in own else sorted(
            d for d in job.iterdir() if d.is_dir() and (d / "result.json").is_file())
        for d in folders:
            result = own if d == job else (_json(d / "result.json") or {})
            if "task_name" not in result:
                continue
            trial = Trial(path=d, result=result, lock=_json(d / "lock.json") or {},
                          answer=_json(d / "verifier" / "answer.json"),
                          reference=_json(d / "agent" / "reference-agent.json"))
            trial.task_id = str(result["task_name"]).split("/", 1)[-1]
            trial.started = str(result.get("started_at") or "")
            found.append(trial)
    return sorted(found, key=lambda t: (t.started, t.name))


def official(trial: Trial, digests: dict[str, str]) -> tuple[bool, list[str]]:
    """Whether the trial ran the task as published, and if not, why not."""
    why = []
    lock = trial.lock
    digest = (lock.get("task") or {}).get("digest")
    if digests.get(trial.task_id) is None:
        why.append("the release has no digest for this task")
    elif digest != digests[trial.task_id]:
        why.append(f"its task is not the published one (digest {str(digest)[:19]}...)")
    for where in ("agent", "environment"):
        if (lock.get(where) or {}).get("extra_allowed_hosts"):
            why.append(f"hosts were added to the network's allowlist ({where}): "
                       f"{', '.join(lock[where]['extra_allowed_hosts'])}")
    verifier = lock.get("verifier") or {}
    if verifier.get("disable") or verifier.get("environment_mode") not in (None, "shared"):
        why.append("its verifier did not run in the agent's container")
    setting = WEB_SEARCH_OFF.get(trial.agent)
    if setting and str(((lock.get("agent") or {}).get("kwargs") or {}).get(setting)).lower() != "true":
        why.append(f"{trial.agent}'s own web search was not turned off (--ak {setting}=true)")
    return not why, why


def _seconds(result: dict) -> float | None:
    span = result.get("agent_execution") or {}
    try:
        started = datetime.fromisoformat(str(span["started_at"]).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(span["finished_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return round((finished - started).total_seconds(), 1)


def framing(instruction: str) -> str:
    """What the task told the agent above the conversation (`harbor.FRAMING`)."""
    from .harbor import RULE

    return instruction.split(f"\n\n{RULE}\n\n", 1)[0]


def answer_row(trial: Trial, task, run: int, shown: str, instruction: str, is_official: bool) -> dict:
    """The answer row for one trial, as the attempt stage writes one; or an error row saying why not.

    ``task`` is the release's `Task`; ``shown`` the conversation the agent was
    shown and ``instruction`` its whole instruction, both as the release's
    export makes them (`harbor.fitted`).
    """
    import hashlib

    from ..corpus.turns import RECORD
    from ..project import code_version
    from ..score.attempt import CALLS
    from ..score.structure import analyse
    from ..spec import fingerprint

    base = {"task_id": trial.task_id, "run": run,
            "harbor": {"job": trial.path.parent.name, "trial": trial.name, "agent": trial.agent,
                       "agent_version": (trial.result.get("agent_info") or {}).get("version"),
                       "model": trial.model, "task_digest": (trial.lock.get("task") or {}).get("digest"),
                       "official": is_official}}
    a = trial.answer
    if trial.exception and trial.exception != TIMED_OUT:
        return {**base, "error": f"the trial failed: {trial.exception}"}
    if a is None:
        return {**base, "error": "the trial has no verifier record (verifier/answer.json)"}
    if not (a.get("trajectory") or {}).get("present"):
        return {**base, "error": "the agent wrote no trajectory (ATIF), so its answer cannot be read"}
    if a.get("instruction_sha256") != hashlib.sha256(instruction.encode("utf-8")).hexdigest():
        return {**base, "error": "the trial was given another instruction than the release's for this task"}
    if a.get("actual_changes") is None:
        return {**base, "error": f"what the agent changed could not be read: {a.get('capture_error', '?')}"}
    calls = a.get("tool_calls") or []
    attempt = SimpleNamespace(
        tool_calls=[SimpleNamespace(name=harness_tool(c), failed=False) for c in calls],
        actual_changes=a["actual_changes"], declared_changes=[])
    structure = analyse(task, attempt, None)
    structure.token_removed = a.get("token_removed")
    ref = trial.reference or {}
    out_of_time = bool(ref.get("out_of_time")) or trial.exception == TIMED_OUT
    usage = ref.get("usage")
    if usage is None:
        agent_result = trial.result.get("agent_result") or {}
        counted = {k: agent_result.get(f"n_{k}_tokens") for k in ("input", "output", "cache")}
        usage = ({"input_tokens": counted["input"], "output_tokens": counted["output"],
                  "cached_tokens": counted["cache"], "cost_usd": agent_result.get("cost_usd")}
                 if any(v is not None for v in counted.values()) else None)
    note = NOTE.format(agent=f"{trial.agent} ({trial.model})", network=CLOSED if is_official else UNKNOWN)
    return {
        **base,
        "kind": task.kind,
        "model": trial.model,
        "environment": f"harbor:{trial.agent}",
        "seconds": _seconds(trial.result) if ref.get("seconds") is None else ref["seconds"],
        "reply": a.get("reply") or "",
        "out_of_time": out_of_time,
        "ended_by": ref.get("ended_by") or ("time limit" if trial.exception == TIMED_OUT
                                            else "answered" if (a.get("reply") or "").strip() else "no answer"),
        "final_report_forced": bool(ref.get("final_report_forced")),
        "final_report_error": ref.get("final_report_error") or "",
        "past_deadline": bool(ref.get("past_deadline")),
        "usage": usage,
        "last_response": ref.get("last_response") or "",
        "null_responses": ref.get("null_responses") or 0,
        "throttled_s": ref.get("throttled_s") or 0.0,
        "tool_calls": calls,
        "actual_changes": a["actual_changes"],
        "declared_changes": [],
        "structure": structure.to_json(),
        "final_state": a.get("final_state") or {},
        "final_state_files": a.get("final_state_files") or 0,
        "transcript": shown,
        "record": RECORD,
        "calls": CALLS,
        "rules": f"{framing(instruction)}\n\n{note}",
        "task_fingerprint": fingerprint(task),
        "code_version": code_version(),
        "budget_s": (ref.get("limits") or {}).get("seconds"),
        "max_turns": (ref.get("limits") or {}).get("turns"),
    }
