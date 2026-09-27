"""errata_harbor's agents, where Harbor is installed.

    python checks/harbor_agents.py

Harbor is not one of errata-bench's own dependencies (its extra, `harbor`), so
CI does not install it and this check says it was skipped, never that it
passed. Where it is installed, each agent is run against a fake environment:
its trajectory must be valid ATIF by Harbor's own validator, and read by
`errata_bench.release.atif` into the answer and the calls it made.
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
    print("SKIPPED: Harbor is not installed here, so nothing was checked (pip install -e '.[harbor]')")
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

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
