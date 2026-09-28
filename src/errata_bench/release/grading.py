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
allowlist but a model API (`harbor.model_host`: another region of AWS Bedrock
or Google Vertex AI), the verifier run in the agent's container, and for
Claude Code and Codex their own web search off (it reaches the web from the
provider's side, past the container's network).
"""

from __future__ import annotations

import re

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .atif import harness_tool
from .harbor import model_host

# Agents Harbor runs whose own web search reaches past the container's network,
# and the setting that turns it off (Harbor's source, 09-27).
WEB_SEARCH_OFF = {"claude-code": "disable_web_search", "codex": "disable_web_search"}
# The reference agent's official limits: `score.attempt.attempt_limits`'s defaults.
OFFICIAL_LIMITS = (600, 30)
# And the wall it ends itself before: Harbor's agent timeout (`reference_agent.WALL_S`).
OFFICIAL_WALL_S = 1800.0
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
    def bare_model(self) -> str:
        """The model's own name, without its provider: what the grading stage compares with its judge's."""
        info = (self.result.get("agent_info") or {}).get("model_info") or {}
        return str(info.get("name") or self.model.rsplit("/", 1)[-1])

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


# What the verifier and the record depend on, inside the agent's container
# (G-78). The agent may read the conversation left at /errata/conversation.txt;
# it has no reason to change any of these, or the Python the verifier runs on.
# A project's own `tests/` folder is not `/tests`.
_PROTECTED = re.compile(r"/errata/(?!conversation\.txt\b)|/errata\b(?!/)|/logs/(?:agent|verifier)"
                        r"|(?:^|[\s'\"=(])/tests\b|/usr/(?:local/)?bin/python|/usr/(?:local/)?lib/python")
# A heredoc's opening: its delimiter a word, not a number (`$((1 << 20))`).
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# A redirect and its target; `>|` clobbers. `2>&1` and `&>/dev/null` write nothing kept.
_REDIRECT = re.compile(r"(?:&>>?|\d?>>?\|?)\s*(\"[^\"]*\"|'[^']*'|[^\s;&|<>()]+)")
_CHANGERS = {"tee", "mv", "cp", "ln", "rm", "chmod", "chown", "truncate", "dd", "touch", "install", "rsync",
             "patch", "unlink", "shred"}
_SCRIPTING = re.compile(r"^(?:python[\d.]*|perl|ruby|node|bash|sh|zsh)$")
_GIT_WRITES = {"checkout", "reset", "clean", "restore", "apply", "stash", "am", "merge", "pull", "rm", "mv"}
# In a script, a call that writes, removes or renames a path given as a
# string: its first argument, or a name bound to one (`p = "/errata/..."`).
# Named groups: a back-reference by number points at another group once the
# pattern is embedded in a larger one.
def _string(tag: str) -> str:
    return rf"(?P<q{tag}>['\"])(?P<s{tag}>[^'\"\n]{{1,300}})(?P=q{tag})"


_SCRIPT_CALL = re.compile(
    r"(?P<fn>open|remove|unlink|rename|replace|rmtree|copyfile|copy2?|move|chmod|symlink|truncate|writeFileSync"
    r"|writeFile|appendFileSync|rmSync|unlinkSync|renameSync|copyFileSync|write|delete)\s*\(\s*(?:" + _string("a")
    + r"|(?P<name>[A-Za-z_]\w*))(?:\s*,\s*" + _string("m") + r")?")
_SCRIPT_PATH = re.compile(r"Path\(\s*" + _string("p") + r"\s*\)\s*\.\s*(?:write_text|write_bytes|unlink|chmod|rename"
                          r"|replace|touch|rmdir|open\s*\(\s*['\"][^'\"]*[wax+])")
_SCRIPT_NAME = re.compile(r"(?P<var>[A-Za-z_]\w*)\s*=\s*" + _string("v"))
# The longest part of a command read: past it, a command is read no further
# (a 200 KB command took 33 s under the regexes this replaced, 09-28 review).
_PART_CHARS = 100_000


def _protected(path: str, names: set[str], inside: bool) -> bool:
    path = path.strip("'\"")
    if re.match(r"^\$\{?([A-Za-z_]\w*)", path) and re.match(r"^\$\{?([A-Za-z_]\w*)", path).group(1) in names:
        return True
    if _PROTECTED.search(" " + path):
        return True
    return inside and bool(path) and not path.startswith(("/", "~", "$", "-"))


def _parts(command: str) -> list[str]:
    """A command cut at `;`, `&&`, `||`, `|`, `&` and new lines outside quotes, each heredoc kept with its command."""
    command = command[:20 * _PART_CHARS]
    parts, cur, i, quote, n = [], [], 0, None, len(command)
    while i < n:
        c = command[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < n:
                cur.append(command[i + 1])
                i += 1
            i += 1
            continue
        if c in "'\"":
            quote = c
        elif c == "<" and command.startswith("<<", i) and not command.startswith("<<<", i):
            here = _HEREDOC.match(command, i)
            if here:
                end = re.compile(r"^\s*" + re.escape(here.group(2)) + r"\s*$", re.MULTILINE).search(command, here.end())
                cur.append("<< " + command[here.end():end.start() if end else n])
                i = end.end() if end else n
                continue
        elif c in ";\n" or (c in "&|" and not (cur and cur[-1] == ">") and not command.startswith("&>", i)):
            parts.append("".join(cur))
            cur = []
            i += 2 if command.startswith(c * 2, i) and c in "&|" else 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p[:_PART_CHARS] for p in parts if p.strip()]


def _script_writes_protected(script: str) -> bool:
    """Whether a script given inline or by heredoc writes, removes or renames a protected path."""
    names = {m.group("var") for m in _SCRIPT_NAME.finditer(script) if _PROTECTED.search(" " + m.group("sv"))}
    for m in _SCRIPT_CALL.finditer(script):
        target, name, mode = m.group("sa"), m.group("name"), m.group("sm")
        hit = (target is not None and bool(_PROTECTED.search(" " + target))) or (name in names)
        if hit and (m.group("fn") != "open" or (mode is not None and re.search(r"[wax+]", mode))):
            return True
    return any(_PROTECTED.search(" " + m.group("sp")) for m in _SCRIPT_PATH.finditer(script))


def _changes_protected(command: str) -> bool:
    """Whether a part of a command writes to, removes or renames what the verifier or the record depends on.

    Part by part, as the shell runs them: the part's redirect targets; a
    writing command's arguments (`cp`, `rm`, `tee`, `sed -i`, `tar -C`, `find
    ... -delete`, `git -C ... checkout`); a script given inline (`python -c`,
    `node -e`) or by heredoc, where a write names the path. A `cd` into a
    protected folder, and a variable set to a protected path, carry to the
    parts after them. Naming a path is not enough: reading Python's library
    and writing to /tmp in one command changes nothing (09-28 reviews).
    """
    import shlex

    names: set[str] = set()
    inside = False
    for part in _parts(command):
        body = part
        if part.lstrip().startswith("<< ") or "<< " in part:
            head, _, body = part.partition("<< ")
            if _script_writes_protected(body) or (re.search(r"\b(?:bash|sh|zsh)\b", head)
                                                     and _changes_protected(body)):
                return True
            part = head
        for target in _REDIRECT.findall(part):
            if target not in ("/dev/null",) and _protected(target, names, inside):
                return True
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            words = part.split()
        while words and words[0] in ("(", "{", "sudo", "exec", "time", "nohup", "command", "env"):
            words = words[1:]
        if words and words[0].startswith("("):
            words[0] = words[0].lstrip("(")
        assigns = []
        while words and re.match(r"^(?:export\s+)?[A-Za-z_]\w*=", words[0]):
            assigns.append(words.pop(0))
        for a in assigns:
            name, _, value = a.partition("=")
            if _PROTECTED.search(" " + value):
                names.add(name)
        if not words:
            continue
        cmd, args = words[0].rsplit("/", 1)[-1], [w.rstrip(")") for w in words[1:]]
        if cmd in ("cd", "pushd"):
            inside = bool(args) and _protected(args[0], names, False)
            continue
        paths = [a for a in args if not a.startswith("-")]
        if cmd in _CHANGERS and any(_protected(a.split("=", 1)[-1], names, inside) for a in paths):
            return True
        if cmd in ("sed", "perl") and any(a.startswith("--in-place") or (re.match(r"^-[A-Za-z]*i", a))
                                          for a in args):
            if any(_protected(a, names, inside) for a in paths):
                return True
        if cmd == "tar":
            dirs = [args[k + 1] for k, a in enumerate(args[:-1]) if a == "-C" or re.match(r"^-[A-Za-z]*C$", a)]
            dirs += [a.split("=", 1)[1] for a in args if a.startswith("--directory=")]
            if any(_protected(d, names, False) for d in dirs) or (inside and not dirs):
                if any(re.match(r"^-?[A-Za-z]*x", a) or a == "--extract" for a in args):
                    return True
        if cmd == "find" and ("-delete" in args or "-exec" in args) and any(_protected(a, names, inside)
                                                                            for a in paths[:3]):
            return True
        if cmd == "git":
            where = [args[k + 1] for k, a in enumerate(args[:-1]) if a == "-C"]
            sub = next((a for k, a in enumerate(args) if not a.startswith("-") and (k == 0 or args[k - 1] != "-C")),
                       "")
            if sub in _GIT_WRITES and (any(_protected(w, names, False) for w in where) or (inside and not where)):
                return True
        if _SCRIPTING.match(cmd):
            scripts = [args[k + 1] for k, a in enumerate(args[:-1]) if re.match(r"^-[A-Za-z]*[ce]$", a)]
            if any(_script_writes_protected(s) for s in scripts):
                return True
    return False


def integrity_flags(answer: dict | None) -> list[str]:
    """Calls that look like writing to what the verifier or the record depends on: for a person to read (G-78).

    A heuristic, not a verdict (`_changes_protected`). The snapshot check
    catches a changed baseline whatever wrote it (`official`). Measured 09-28:
    none of 1,544 stored D-40 to D-45 and v1-subset answers flagged; every
    tampering the reviews tried is.
    """
    flags = []
    for i, c in enumerate((answer or {}).get("tool_calls") or [], 1):
        command = str(c.get("command") or "")
        if harness_tool(c) in ("write_file", "edit_file"):
            refused = str(c.get("result") or "").lstrip().lower().startswith(("refused", "error"))
            hit = not refused and bool(_PROTECTED.search(" " + str(c.get("path") or "")))
        else:
            hit = _changes_protected(command)
        if hit:
            flags.append(f"call {i}: {(command or str(c.get('path') or ''))[:160]}")
    return flags


# A variable whose name says it holds a secret. Narrower than the rule the
# agent's commands are stripped by (`score.attempt.command_env`), which may
# strip too much at no cost: text replaced here is lost from the record, and
# GIT_AUTHOR_EMAIL or OAUTH_CALLBACK_URL are not secrets (09-28 review).
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|(?<![A-Z])AUTH(?!OR)", re.IGNORECASE)


def redact(answer: dict | None) -> tuple[dict | None, int]:
    """The answer with any credential the grading shell holds replaced, and how many times one was found.

    The agent's key is in its container, where a command can print it; the
    answer's calls go into the graders' prompts and every stored row (09-28
    preflight). Replaced before either sees it: every value of a variable
    whose name says it is a secret, of 16 characters or more.
    """
    import os

    if answer is None:
        return None, 0
    secrets = sorted({v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= 16},
                     key=len, reverse=True)
    text = json.dumps(answer, ensure_ascii=False)
    found = 0
    for s in secrets:
        for form in {s, json.dumps(s, ensure_ascii=False)[1:-1]}:
            found += text.count(form)
            text = text.replace(form, "[a credential, redacted]")
    return (json.loads(text) if found else answer), found


# What Harbor lets a run change besides its hosts, limits and verifier, as its
# lock records them (0.23): an official trial leaves each unset or at Harbor's
# default. Two were read before, of these (09-28 review): a multiplier stretches
# every timeout, and a compose file, a mount or a preloaded conversation can
# change what the agent has without adding a host.
_HARBOR_DEFAULTS = (
    ("", "install_only"), ("", "timeout_multiplier"), ("", "agent_timeout_multiplier"),
    ("", "verifier_timeout_multiplier"), ("", "agent_setup_timeout_multiplier"),
    ("", "environment_build_timeout_multiplier"), ("", "extra_instructions"), ("", "user_agent"),
    ("", "user_persona"), ("", "user_prompt_template"), ("", "bridge_prompt"), ("", "bridge_inputs"), ("", "skills"),
    ("", "extra_docker_compose"), ("", "source_trial"),
    ("agent", "override_timeout_sec"), ("agent", "override_setup_timeout_sec"), ("agent", "max_timeout_sec"),
    ("agent", "load_trajectory"), ("agent", "mcp_servers"), ("agent", "skills"),
    ("environment", "mounts"), ("environment", "extra_docker_compose"), ("environment", "env"),
    ("environment", "kwargs"), ("environment", "override_cpus"), ("environment", "override_memory_mb"),
    ("environment", "override_storage_mb"), ("environment", "override_gpus"), ("environment", "override_tpu"),
    ("verifier", "override_timeout_sec"), ("verifier", "max_timeout_sec"), ("verifier", "import_path"),
    ("verifier", "env"), ("verifier", "kwargs"),
)


def _changed_from_harbor(lock: dict) -> list[str]:
    changed = []
    for where, field in _HARBOR_DEFAULTS:
        value = (lock.get(where) or {}).get(field) if where else lock.get(field)
        default = 1.0 if field.endswith("multiplier") else None
        if value in (None, False, [], {}, "") or value == default:
            continue
        changed.append(f"{where + '.' if where else ''}{field}={str(value)[:60]}")
    return changed


def official(trial: Trial, digests: dict[str, str]) -> tuple[bool, list[str]]:
    """Whether the trial ran the task as published, and if not, why not."""
    why = []
    lock = trial.lock
    changed = _changed_from_harbor(lock)
    if changed:
        why.append(f"Harbor settings were changed from their defaults: {', '.join(changed)}")
    digest = (lock.get("task") or {}).get("digest")
    if digests.get(trial.task_id) is None:
        why.append("the release has no digest for this task")
    elif digest != digests[trial.task_id]:
        why.append(f"its task is not the published one (digest {str(digest)[:19]}...)")
    for where in ("agent", "environment"):
        added = [h for h in (lock.get(where) or {}).get("extra_allowed_hosts") or [] if not model_host(str(h))]
        if added:
            why.append(f"hosts that are not model APIs were added to the network's allowlist ({where}): "
                       f"{', '.join(map(str, added))}")
    verifier = lock.get("verifier") or {}
    if verifier.get("disable") or verifier.get("environment_mode") not in (None, "shared"):
        why.append("its verifier did not run in the agent's container")
    # The build-time snapshot, checked against the task's own copy, which the
    # agent never sees: a difference means what changed was measured against
    # a baseline the agent altered (G-78). Empty on every trial so far.
    changed = ((trial.answer or {}).get("before") or {}).get("differ_from_workspace")
    if changed:
        why.append(f"its build-time snapshot was changed before the verifier ran ({len(changed)} file(s), "
                   f"e.g. {changed[0]}): what it changed cannot be trusted")
    # The reference agent's limits come from the shell Harbor runs in
    # (ERRATA_ATTEMPT_SECONDS, ERRATA_ATTEMPT_TURNS); official results use the
    # harness's own, as every stored result did (09-28 preflight).
    limits = (trial.reference or {}).get("limits")
    if limits and (limits.get("seconds"), limits.get("turns")) != OFFICIAL_LIMITS:
        why.append(f"the reference agent ran with {limits.get('seconds')} s and {limits.get('turns')} turns, "
                   f"not {OFFICIAL_LIMITS[0]} s and {OFFICIAL_LIMITS[1]}")
    wall = (trial.reference or {}).get("wall_s")
    if wall is not None and float(wall) != OFFICIAL_WALL_S:
        why.append(f"the reference agent's wall was {wall} s, not {OFFICIAL_WALL_S}")
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


def answer_row(trial: Trial, task, run: int, conversation: str, instruction: str, is_official: bool) -> dict:
    """The answer row for one trial, as the attempt stage writes one; or an error row saying why not.

    ``task`` is the release's `Task`; ``conversation`` the conversation the
    answer is graded against, the whole one the agent was given (`grade_harbor.
    graded_for`), and ``instruction`` its whole instruction, as the release's
    export makes it (`harbor.fitted`).
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
                       "agent_code": (trial.reference or {}).get("package_sha256"),
                       "official": is_official}}
    a, redacted = redact(trial.answer)
    base["harbor"]["credentials_redacted"] = redacted
    # From the redacted record: a flag quotes the command, and goes into results.json.
    base["harbor"]["integrity_flags"] = integrity_flags(a)
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
    # The verifier searches for the token as written; where it was not there to
    # remove, its absence afterwards says nothing (#8).
    structure.token_removed = a.get("token_removed") if task.token_removal_counts else None
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
        # Bare, as this harness's rows name a model: the grading stage refuses
        # a judge that would grade its own model's answers by comparing this
        # with the judge's name, and "openai/gpt-6-astra" is not "gpt-6-astra".
        # The provider's name for it is under `harbor.model`.
        "model": trial.bare_model,
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
        "transcript": conversation,
        "record": RECORD,
        "calls": CALLS,
        "rules": f"{framing(instruction)}\n\n{note}",
        "task_fingerprint": fingerprint(task),
        "code_version": code_version(),
        "budget_s": (ref.get("limits") or {}).get("seconds"),
        "max_turns": (ref.get("limits") or {}).get("turns"),
    }
