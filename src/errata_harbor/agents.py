"""Agents for errata-bench's Harbor tasks (`errata_bench.release.harbor`).

    harbor run -p <tasks> -a errata_harbor.agents:StandIn

`StandIn` calls no model. It looks at the repository, writes one file and
reports what it did, and writes its trajectory in ATIF, as Harbor's own
agents do, so a task's image, its network, its verifier and the record the
graders read can all be tried before any model is paid for.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Agent, Observation, ObservationResult, Step, ToolCall, Trajectory

# The file the stand-in writes into the working copy, so the verifier has a change to find.
WRITTEN = "ERRATA_STAND_IN.md"
REPLY = ("I am errata-bench's stand-in agent, not a model. I listed the repository, read `git status`, and "
         f"wrote {WRITTEN}. I checked nothing else.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StandIn(BaseAgent):
    """No model: a fixed look, one write, and a report of exactly that, with its trajectory in ATIF."""

    capabilities = AgentCapabilities(atif=True)

    @staticmethod
    def name() -> str:
        return "errata-stand-in"

    def version(self) -> str:
        return "1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        commands = [("c1", "ls -1a"), ("c2", "git status --short"),
                    ("c3", f"printf 'Written by the stand-in agent.\\n' > {WRITTEN} && echo written")]
        results = {}
        for call_id, command in commands:
            done = await environment.exec(command=command, timeout_sec=60)
            results[call_id] = (f"exit {done.return_code}\n" + (done.stdout or "") + (done.stderr or ""))
        steps = [Step(step_id=1, timestamp=_now(), source="user", message=instruction)]
        for n, (call_id, command) in enumerate(commands, start=2):
            steps.append(Step(
                step_id=n, timestamp=_now(), source="agent", message="",
                tool_calls=[ToolCall(tool_call_id=call_id, function_name="run_command",
                                     arguments={"command": command})],
                observation=Observation(results=[ObservationResult(source_call_id=call_id,
                                                                   content=results[call_id])])))
        steps.append(Step(step_id=len(steps) + 1, timestamp=_now(), source="agent", message=REPLY))
        trajectory = Trajectory(schema_version="ATIF-v1.7", session_id=str(uuid.uuid4()),
                                agent=Agent(name=self.name(), version=self.version()), steps=steps)
        (self.logs_dir / "trajectory.json").write_text(json.dumps(trajectory.to_json_dict(), indent=1))
