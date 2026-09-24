"""Can a deployment act as a candidate? A check before any attempt is spent on it.

    ERRATA_PROVIDER=azure .venv/bin/python scripts/preflight_candidate.py <deployment>...

Runs each deployment through the harness's own path -- the Agents SDK, over
chat completions on Azure -- with two stub tools (read a file, run a command)
behind a prompt about as long as a task's conversation (about 76k tokens). A
deployment passes if it calls the tools, reports what they returned (the build
number in README.md, and a failing test), and then writes a final report with
no tools, as the harness forces at the deadline. One JSON row per deployment,
with the model Azure says it served and the tokens used.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("ERRATA_PROVIDER", "azure")

from agents import Agent, RunContextWrapper, Runner, function_tool  # noqa: E402

from errata_bench import llm  # noqa: E402

FILES = {"README.md": "# demo\nThe build number is 4417.\n",
         "src/app.py": "def add(a, b):\n    return a - b\n"}
FAILING = "FAILED tests/test_app.py::test_add - assert -1 == 3\n1 failed in 0.02s"


@function_tool
def read_file(ctx: RunContextWrapper[dict], path: str) -> str:
    """Read a file in the repository and return its contents."""
    ctx.context["calls"].append(("read_file", path))
    return FILES.get(path, f"error: no such file: {path}")


@function_tool
def run_command(ctx: RunContextWrapper[dict], command: str) -> str:
    """Run a shell command in the repository and return its output."""
    ctx.context["calls"].append(("run_command", command))
    return FAILING if "test" in command else "(no output)"


# Prior conversation as long as a task's excerpt, with nothing in it that answers.
HISTORY = "\n".join(f"[turn {i}] assistant: I looked at module_{i}.py; it defines helper_{i}() "
                    f"which formats log line {i}." for i in range(2500))
ASK = (f"{HISTORY}\n\n{'=' * 70}\nDeveloper: What build number does README.md state, and do the "
       "tests pass? Check both with the tools before answering.")


async def probe(model: str) -> dict:
    served = await asyncio.to_thread(llm.served, model)
    row = {"deployment": model, **{k: served[k] for k in ("served_model", "error") if k in served}}
    ctx = {"calls": []}
    started = time.time()
    try:
        agent = Agent(name="candidate", instructions="You are a coding agent. Use the tools; never guess.",
                      model=model, tools=[read_file, run_command])
        result = await asyncio.wait_for(Runner.run(agent, ASK, context=ctx, max_turns=8), timeout=300)
        reply = str(result.final_output or "")
        row.update(tool_calls=ctx["calls"], says_build_number="4417" in reply,
                   reports_failure=any(w in reply.lower() for w in ("fail", "not pass", "don't pass")),
                   usage=llm.usage_of(result), seconds=round(time.time() - started, 1), reply=reply[:200])
        final = Agent(name="candidate", instructions="Write your final report.", model=model, tools=[])
        report = await asyncio.wait_for(
            Runner.run(final, "Summarize what you found in two sentences.", max_turns=1), timeout=120)
        row["final_report"] = bool(str(report.final_output or "").strip())
    except Exception as e:  # noqa: BLE001 - a deployment that fails is the finding
        row.update(error=f"{type(e).__name__}: {e}"[:400], tool_calls=ctx["calls"],
                   seconds=round(time.time() - started, 1))
    row["passes"] = bool(row.get("tool_calls") and row.get("says_build_number")
                         and row.get("reports_failure") and row.get("final_report"))
    return row


async def main(models: list[str]) -> int:
    llm.configure_client()
    rows = await asyncio.gather(*(probe(m) for m in models))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0 if all(r["passes"] for r in rows) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1:])))
