"""errata_harbor's agents, where Harbor is installed.

    python checks/harbor_agents.py

Run with Harbor's environment's Python (`errata_harbor` says how it is set up):
Harbor cannot share errata-bench's locked environment, so where Harbor is not
installed this check says it was skipped, never that it passed, unless
ERRATA_REQUIRE_HARBOR=1 (CI's Harbor job sets it), when that is a failure. Each agent is run
against a fake environment: its trajectory must be valid ATIF by Harbor's own
validator, and read by `errata_bench.release.atif` into the answer and calls.
The reference agent's runner needs errata-bench's own dependencies, and runs
with errata-bench's Python: ERRATA_BENCH_PYTHON, or this repository's .venv.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

try:
    import harbor  # noqa: F401
except ImportError:
    if __import__("os").environ.get("ERRATA_REQUIRE_HARBOR") == "1":
        print("FAIL: Harbor is required here (ERRATA_REQUIRE_HARBOR=1) and is not installed in this Python")
        sys.exit(1)
    print("SKIPPED: Harbor is not installed in this Python, so nothing was checked "
          "(run it with Harbor's environment's Python; see src/errata_harbor/__init__.py)")
    sys.exit(0)

from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from errata_bench.release import atif
from errata_harbor.agents import REPLY, WRITTEN, StandIn

FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


class FakeEnvironment:
    """What an agent sees of a task's container: commands run, and what they print."""

    def __init__(self):
        self.ran = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.ran.append(command)
        return ExecResult(stdout=f"ran: {command}\n", stderr="", return_code=0)


print("1. the stand-in agent: no model, a trajectory Harbor accepts, read into the graders' record")
logs = Path(tempfile.mkdtemp())
env = FakeEnvironment()
agent = StandIn(logs_dir=logs)
asyncio.run(agent.run("Answer the developer.", env, AgentContext()))
raw = (logs / "trajectory.json").read_text()
valid = Trajectory.model_validate_json(raw)
answer, calls = atif.record_of(json.loads(raw), "Answer the developer.")
check(valid.agent.name == "errata-stand-in" and len(env.ran) == 3 and any(WRITTEN in c for c in env.ran),
      f"it runs three commands, one of them writing {WRITTEN}: {env.ran}")
check(answer == REPLY and [c["name"] for c in calls] == ["run_command"] * 3
      and [atif.harness_tool(c) for c in calls] == ["run_command"] * 3
      and calls[1]["result"] == "exit 0\nran: git status --short\n",
      f"and its trajectory reads as its answer and its three calls, with their results: {answer[:60]!r}")

print("\n2. the reference agent: installed from this package and its lock, handed the instruction as a file")
import os
import subprocess
from errata_bench.release import reference_agent as ref
from errata_harbor.agents import Reference


class RecordingEnvironment(FakeEnvironment):
    """A fake container that also records what is uploaded into it."""

    def __init__(self):
        super().__init__()
        self.uploads, self.envs = [], []

    async def upload_dir(self, source_dir, target_dir):
        self.uploads.append((Path(source_dir), target_dir))

    async def upload_file(self, source_path, target_path):
        self.uploads.append((Path(source_path).name, target_path, Path(source_path).read_bytes()))

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.envs.append(dict(env or {}))
        return await super().exec(command, cwd=cwd, env=env, timeout_sec=timeout_sec, user=user)


long = "x" * 300_000
fake_key = "not-a-real-key-for-this-check"
os.environ["OPENAI_API_KEY"] = fake_key
os.environ.pop("ERRATA_TIMEOUT", None)   # as a run that sets none
renv = RecordingEnvironment()
agent = Reference(logs_dir=Path(tempfile.mkdtemp()), model_name="openai/some-model")
asyncio.run(agent.install(renv))
asyncio.run(agent.run(long, renv, AgentContext()))
del os.environ["OPENAI_API_KEY"]
package, lock = Reference.source()
dirs = [u for u in renv.uploads if len(u) == 2]
files = {u[1]: u[2] for u in renv.uploads if len(u) == 3}
check(dirs == [(package, f"{Reference.HOME}/src/errata_bench")] and package.name == "errata_bench"
      and not list(package.rglob(".env")) and files.get(f"{Reference.HOME}/requirements-lock.txt") == lock.read_bytes(),
      "it uploads errata_bench's package folder alone (no .env anywhere in it) and this repository's lock")
check(renv.ran[0].endswith("mkdir -p /installed-agent/errata/src")
      and any("uv pip install" in c and c.endswith("-r /installed-agent/errata/requirements-lock.txt")
              for c in renv.ran),
      f"and installs the whole lock, the set every check runs with: {[c[-70:] for c in renv.ran[:2]]}")
check(files.get(f"{Reference.HOME}/instruction.md") == long.encode()
      and any("-m errata_bench.release.reference_agent /installed-agent/errata/instruction.md /logs/agent "
              "--model openai/some-model" in c for c in renv.ran)
      and all(long not in c for c in renv.ran),
      "the instruction goes as a file, never on a command line, whatever its length")
check(renv.envs[-1].get("OPENAI_API_KEY") == fake_key and renv.envs[-1].get("ERRATA_DOTENV") == "0"
      and renv.envs[-1].get("PYTHONPATH") == f"{Reference.HOME}/src",
      "and the model's key is passed from where Harbor runs, with no .env read inside")
# One request may take as long as D-40 to D-45's and grading's did, unless the
# run sets its own: at the client's 120 s a slow one was sent again and paid
# again, unrecorded (09-28 review).
os.environ["ERRATA_TIMEOUT"] = "300"
renv2 = RecordingEnvironment()
asyncio.run(agent.run("Answer.", renv2, AgentContext()))
del os.environ["ERRATA_TIMEOUT"]
check(renv.envs[-1].get("ERRATA_TIMEOUT") == "900" and renv2.envs[-1].get("ERRATA_TIMEOUT") == "300",
      f"and a request waits 900 s unless the run forwards its own: {renv.envs[-1].get('ERRATA_TIMEOUT')}, "
      f"{renv2.envs[-1].get('ERRATA_TIMEOUT')}")
# The runner, as it runs in the container: its trajectory must be valid ATIF.
work = Path(tempfile.mkdtemp())
(work / "a.py").write_text("x = 1\n")
subprocess.run(["git", "init", "-q"], cwd=work, check=True)
(work.parent / "instruction.md").write_text("Is x set?")
# absolute, not resolved: resolved, the link leads past the virtual environment to its base Python.
bench_python = os.environ.get("ERRATA_BENCH_PYTHON") or str(Path(".venv/bin/python").absolute())
done = subprocess.run([bench_python, "-m", "errata_bench.release.reference_agent", str(work.parent / "instruction.md"),
                       str(work.parent / "logs"), "--model", ref.STAND_IN], cwd=work, capture_output=True, text=True,
                      env={**os.environ, "PYTHONPATH": str(Path("src").resolve()), "ERRATA_DOTENV": "0"}, timeout=120)
raw = (work.parent / "logs" / "trajectory.json").read_text() if done.returncode == 0 else "{}"
try:
    Trajectory.model_validate_json(raw)
    valid = True
except Exception as e:  # noqa: BLE001 - the failure is the assertion
    valid = f"{type(e).__name__}: {str(e)[:200]}"
check(done.returncode == 0 and valid is True,
      f"and the runner's trajectory is valid ATIF by Harbor's own validator: {valid} {done.stderr[-300:]}")

print("\n3. the network check: which hosts the agent's network rule lets through, no model")
from errata_harbor.agents import NetworkCheck


class NetEnvironment:
    """A container whose network lets through only api.openai.com."""

    def __init__(self):
        self.ran = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.ran.append(command)
        if "api.openai.com" in command:
            return ExecResult(stdout="http 401 exit 0", stderr="", return_code=0)
        return ExecResult(stdout="curl: (7) Failed to connect\nhttp 000 exit 7", stderr="", return_code=0)


nlogs = Path(tempfile.mkdtemp())
nenv = NetEnvironment()
asyncio.run(NetworkCheck(logs_dir=nlogs, hosts="api.openai.com, github.com").run("check", nenv, AgentContext()))
ntraj = Trajectory.model_validate_json((nlogs / "trajectory.json").read_text())
nanswer, ncalls = atif.record_of(json.loads((nlogs / "trajectory.json").read_text()), "check")
check(nanswer == "Reached: api.openai.com. Blocked: github.com." and len(ncalls) == 2 and len(nenv.ran) == 2
      and len(NetworkCheck(logs_dir=nlogs).hosts) == len(NetworkCheck.HOSTS),
      f"an HTTP answer of any status is reached, a failed connection blocked, and its hosts are asked for: {nanswer!r}")

print("\n4. a task's network as Harbor reads it: the model APIs as written, and the verifier's closed")
from harbor.models.task.config import NetworkAllowlistEntryType as Entry
from harbor.models.task.config import TaskConfig, classify_network_allowlist_entry, normalize_allowed_hosts

from errata_bench.release import harbor as hb

config = TaskConfig.model_validate_toml(hb.task_toml(
    {"task_id": "t-1", "repo_id": "o/r", "sha": "0" * 40, "workdir": "/home/dev/r"}))
policy = config.agent.explicit_phase_policy()
kinds = {classify_network_allowlist_entry(h) for h in policy.allowed_hosts}
check(policy.network_mode.value == "allowlist" and policy.allowed_hosts == list(hb.MODEL_HOSTS)
      and kinds == {Entry.HOSTNAME, Entry.WILDCARD_HOSTNAME}
      and config.verifier.explicit_phase_policy().network_mode.value == "no-network",
      f"Harbor takes each of the {len(hb.MODEL_HOSTS)} model API hosts as written, names and leading wildcards only, "
      f"and closes the verifier's network: {sorted(k.value for k in kinds)}")
regional = ["bedrock-runtime.eu-west-1.amazonaws.com", "europe-west4-aiplatform.googleapis.com"]
check(normalize_allowed_hosts(regional) == regional and all(hb.model_host(h) for h in regional),
      "and another region's Bedrock or Vertex AI endpoint is one Harbor adds to a run as written "
      "(--allow-agent-host), and one that keeps the run official")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
