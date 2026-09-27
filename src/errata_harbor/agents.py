"""Agents for errata-bench's Harbor tasks (`errata_bench.release.harbor`).

    harbor run -p <tasks> -a errata_harbor.agents:Reference -m <model>
    harbor run -p <tasks> -a errata_harbor.agents:StandIn

`Reference` is errata-bench's reference agent: the benchmark's own five-tool
loop (`errata_bench.release.reference_agent`), installed in the task's
container with the dependencies this repository locks, and handed the
instruction as a file, so an instruction of any length reaches it whole.
The model's credentials are passed from where Harbor runs (the names in
`Reference.FORWARDED`) or with `--ae`. The model `errata/stand-in` is a
fixed script, no model, through the same loop and tools.

`StandIn` calls no model and installs nothing. It looks at the repository,
writes one file and reports what it did, and writes its trajectory in ATIF,
as Harbor's own agents do, so a task's image, its network, its verifier and
the record the graders read can all be tried before any model is paid for.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
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


class Reference(BaseInstalledAgent):
    """errata-bench's five-tool loop, installed in the task's container and run on its working copy."""

    capabilities = AgentCapabilities(atif=True)
    # Where it is installed in the container.
    HOME = "/installed-agent/errata"
    # The model provider's settings, passed from where Harbor runs when they are set there.
    FORWARDED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ERRATA_PROVIDER", "AZURE_OPENAI_BASE_URL",
                 "AZURE_OPENAI_API_KEY", "ERRATA_ATTEMPT_SECONDS", "ERRATA_ATTEMPT_TURNS")

    @staticmethod
    def name() -> str:
        return "errata-reference"

    def version(self) -> str:
        from errata_bench.release.reference_agent import VERSION

        return VERSION

    @staticmethod
    def source() -> tuple[Path, Path]:
        """errata_bench's package folder, and the lock of this repository it came from."""
        import errata_bench

        package = Path(errata_bench.__file__).resolve().parent
        return package, package.parents[1] / "requirements-lock.txt"

    async def install(self, environment: BaseEnvironment) -> None:
        # The package alone: never the repository around it, whose .env holds keys.
        package, lock = self.source()
        if not lock.is_file():
            raise RuntimeError(f"no requirements-lock.txt beside {package}: install errata-bench from its repository")
        # The folders first: Harbor's copy into a container needs the parent to exist.
        await self.exec_as_agent(environment, command=f"mkdir -p {shlex.quote(self.HOME)}/src")
        await environment.upload_dir(package, f"{self.HOME}/src/errata_bench")
        await environment.upload_file(lock, f"{self.HOME}/requirements-lock.txt")
        home = shlex.quote(self.HOME)
        # The whole lock, the set every check here runs with: the loop's import
        # chain reaches beyond the model libraries (pyarrow, through the
        # harness's edit replay), and a hand-picked subset missed it (09-27).
        await self.exec_as_agent(
            environment,
            command=(f"find {home}/src -name __pycache__ -prune -exec rm -rf {{}} + ; "
                     f"uv venv -q {home}/venv --python python3 && "
                     f"uv pip install -q --python {home}/venv/bin/python -r {home}/requirements-lock.txt"),
            timeout_sec=900)

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        with tempfile.TemporaryDirectory(prefix="errata-reference-") as tmp:
            path = Path(tmp) / "instruction.md"
            path.write_bytes(instruction.encode("utf-8"))
            await environment.upload_file(path, f"{self.HOME}/instruction.md")
        env = {k: os.environ[k] for k in self.FORWARDED if os.environ.get(k)}
        env.update({"PYTHONPATH": f"{self.HOME}/src", "ERRATA_DOTENV": "0", "PYTHONDONTWRITEBYTECODE": "1"})
        home, logs = shlex.quote(self.HOME), shlex.quote(str(self.environment_logs_dir))
        await self.exec_as_agent(
            environment,
            command=(f"{home}/venv/bin/python -m errata_bench.release.reference_agent {home}/instruction.md "
                     f"{logs} --model {shlex.quote(self.model_name or '')}"),
            env=env)
