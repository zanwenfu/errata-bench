"""errata-bench's reference agent, run inside a Harbor task's container (v1 step 3).

    python -m errata_bench.release.reference_agent <instruction file> <out dir> --model <model>

The benchmark's own five-tool loop (`score.attempt.converse`) -- the loop that
answers every task in this harness -- run in the working directory it is
started in, the task's working copy, with its commands run there. Harbor starts
it (`errata_harbor.agents:Reference`) with the task's instruction as a file, so
an instruction of any length reaches it whole. Its limits are the harness's
(`score.attempt.attempt_limits`: 600 seconds and 30 turns unless
ERRATA_ATTEMPT_SECONDS and ERRATA_ATTEMPT_TURNS say otherwise), and one that
runs out is asked once for its report, as in the harness.

It is told what every agent on the task is told (the instruction), and in its
system prompt only what its tools are: the harness's own instructions also say
what held for its sandbox and not in a task's container -- no history, paths
that do not exist, no network.

It writes to <out dir>:

  trajectory.json      its run in ATIF, which the task's verifier reads
                       (`release.verify`): the instruction, each call with its
                       result, and its answer;
  reference-agent.json how it ended, what it cost, and every call as the
                       harness records it.

The model named ``errata/stand-in`` is no model: three calls and an answer,
fixed, through the same loop and the same tools, so the agent can be tried
whole in a task's container before any model is paid for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

VERSION = "1.0"
STAND_IN = "errata/stand-in"
# The harness's instructions (`score.attempt.INSTRUCTIONS`), less what only
# held in its sandbox; the task's instruction says the rest.
SYSTEM = ("You have a working copy of the repository. You can read files, list directories, run commands, "
          "write whole files, and edit part of a file. Prefer the edit and write tools over shell redirection.")
STAND_IN_WRITES = "ERRATA_STAND_IN.md"
STAND_IN_REPLY = ("I am the reference agent's stand-in model, not a model. I listed the repository, read "
                  f"`git status`, and wrote {STAND_IN_WRITES}. I checked nothing else.")


def _stand_in_provider():
    """A model provider whose one model is the fixed script: three calls, then the answer."""
    from agents.items import ModelResponse
    from agents.models.interface import Model, ModelProvider
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    script = [("list_dir", {"path": "."}), ("run_command", {"command": "git status --short"}),
              ("write_file", {"path": STAND_IN_WRITES, "content": "Written by the reference agent's stand-in.\n"})]

    class Script(Model):
        def __init__(self):
            self.sent = 0

        async def get_response(self, *args, **kwargs):
            self.sent += 1
            usage = Usage(requests=1, input_tokens=0, output_tokens=0, total_tokens=0)
            if self.sent <= len(script):
                name, arguments = script[self.sent - 1]
                return ModelResponse(output=[ResponseFunctionToolCall(
                    type="function_call", name=name, arguments=json.dumps(arguments), call_id=f"s{self.sent}",
                    id=f"fc{self.sent}")], usage=usage, response_id=None)
            said = ResponseOutputMessage(id="m", role="assistant", status="completed", type="message", content=[
                ResponseOutputText(type="output_text", text=STAND_IN_REPLY, annotations=[])])
            return ModelResponse(output=[said], usage=usage, response_id=None)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError

    class Provider(ModelProvider):
        nulls = 0

        def __init__(self):
            self.model = Script()

        def get_model(self, model_name):
            return self.model

    return Provider()


def package_digest() -> str:
    """The digest of this package's own code, as it ran: which version of the loop a trial used.

    In a task's container the package has no git history to name it by, so its
    files are: every .py file under the package, by path and contents.
    """
    import hashlib

    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def trajectory_of(instruction: str, model: str, reply: str, calls: list, session_id: str) -> dict:
    """The run in ATIF (v1.7): the instruction, each call with its result, and the answer when there is one."""
    steps: list[dict] = [{"step_id": 1, "source": "user", "message": instruction}]
    for n, call in enumerate(calls, start=1):
        call_id = f"call-{n}"
        steps.append({"step_id": len(steps) + 1, "source": "agent", "message": "",
                      "tool_calls": [{"tool_call_id": call_id, "function_name": call.name,
                                      "arguments": dict(call.arguments or {})}],
                      "observation": {"results": [{"source_call_id": call_id, "content": str(call.result)}]}})
    if reply.strip():
        steps.append({"step_id": len(steps) + 1, "source": "agent", "message": reply})
    return {"schema_version": "ATIF-v1.7", "session_id": session_id,
            "agent": {"name": "errata-reference", "version": VERSION, "model_name": model}, "steps": steps}


async def run(instruction: str, out: Path, model: str, seconds: int, turns: int, tree: Path) -> dict:
    """Run the loop on ``tree`` and write its trajectory and record to ``out``. Returns the record."""
    from agents import set_tracing_disabled

    from ..score.attempt import _Resending, converse

    set_tracing_disabled(True)
    calls: list = []
    context = {"tree": tree, "calls": calls, "deadline": time.monotonic() + seconds, "container": None}
    if model == STAND_IN:
        provider = _stand_in_provider()
    else:
        from ..llm import configure_client

        configure_client()
        provider = _Resending(context)
    started = time.monotonic()
    talk = await converse(model, instruction, context, provider, turns, instructions=SYSTEM)
    record = {
        "agent": "errata-reference", "version": VERSION, "package_sha256": package_digest(), "model": model,
        "limits": {"seconds": seconds, "turns": turns},
        "reply": talk.reply, "ended_by": talk.ended_by, "out_of_time": talk.ran_out,
        "final_report_forced": talk.forced, "final_report_error": talk.report_error, "error": talk.error,
        "past_deadline": time.monotonic() > context["deadline"], "seconds": round(time.monotonic() - started, 1),
        "usage": talk.usage, "last_response": context.get("last_response", ""),
        "null_responses": getattr(provider, "nulls", 0), "throttled_s": round(context.get("throttled_s", 0.0), 1),
        "tool_calls": [c.to_json() for c in calls],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "trajectory.json").write_text(json.dumps(
        trajectory_of(instruction, model, "" if talk.error else talk.reply, calls, str(uuid.uuid4())),
        ensure_ascii=False, indent=1))
    (out / "reference-agent.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return record


def main(argv: list[str]) -> int:
    from ..score.attempt import attempt_limits

    seconds, turns = attempt_limits()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instruction", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--seconds", type=int, default=seconds)
    ap.add_argument("--turns", type=int, default=turns)
    args = ap.parse_args(argv)
    # As bytes, as the task wrote it: its carriage returns are kept.
    instruction = args.instruction.read_bytes().decode("utf-8")
    record = asyncio.run(run(instruction, args.out, args.model, args.seconds, args.turns, Path.cwd()))
    print(f"errata-reference: {record['ended_by']}, {len(record['tool_calls'])} calls"
          + (f", error: {record['error']}" if record["error"] else ""))
    return 1 if record["error"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
