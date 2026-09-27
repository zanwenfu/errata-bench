"""The Harbor verifier run in a container, on the Python every task's image has.

    python checks/verifier_in_container.py

Section 133 of guards_hold runs the verifier (`errata_bench.release.verify`)
with `python -S`, which keeps site-packages out but uses this machine's own
Python. Here it runs where it will run: in the official python:3.12 image
pinned by digest, the one every task's image is built on
(`release.environment.BASES`), with nothing but its standard library: `before`
on a working copy as the image build runs it, then an agent's changes, then
`after` with a trajectory, as a task's tests/test.sh runs it.

Needs Docker. Without it this says SKIPPED and checks nothing, unless
ERRATA_REQUIRE_DOCKER=1 (CI sets it), when not being able to run is a failure.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from errata_bench.release.environment import BASES  # noqa: E402
from errata_bench.release.harbor import BUNDLE  # noqa: E402

IMAGE = BASES["python"]
FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


def docker_works() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


if not docker_works():
    if os.environ.get("ERRATA_REQUIRE_DOCKER") == "1":
        print("FAIL: Docker is required here (ERRATA_REQUIRE_DOCKER=1) and is not available")
        sys.exit(1)
    print("SKIPPED: Docker is not available, so nothing was checked")
    sys.exit(0)

print(f"1. the verifier in {IMAGE.split('@')[0]}, pinned, with its standard library alone")
box = Path(tempfile.mkdtemp())
for rel in BUNDLE:
    (box / "lib" / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path("src") / rel, box / "lib" / rel)
(box / "repo" / "pkg").mkdir(parents=True)
(box / "repo" / "parser.py").write_text("QUOTE = 'BROKEN_QUOTES'\n")
(box / "repo" / "pkg" / "util.py").write_text("x = 1\n")
(box / "tests").mkdir()
instruction = "Is the parser fixed?"
(box / "tests" / "instruction.md").write_text(instruction)
(box / "tests" / "task.json").write_text(json.dumps({
    "task_id": "c1", "version": "1.0", "workdir": "/x/repo", "signature_path": "parser.py",
    "signature_token": "BROKEN_QUOTES"}))
(box / "trajectory.json").write_text(json.dumps({"schema_version": "ATIF-v1.7", "agent": {"name": "codex"}, "steps": [
    {"step_id": 1, "source": "user", "message": instruction},
    {"step_id": 2, "source": "agent", "message": "", "tool_calls": [
        {"tool_call_id": "k1", "function_name": "shell", "arguments": {"command": ["pytest", "-q"]}}],
     "observation": {"results": [{"source_call_id": "k1", "content": "1 passed"}]}},
    {"step_id": 3, "source": "agent", "message": "Fixed; pytest passes."}]}))
verify = "PYTHONPATH=/x/lib python3 -S -m errata_bench.release.verify"
script = (f"set -e; python3 -c 'import sys; print(sys.version.split()[0])'; "
          f"{verify} before /x/repo /x/before.json; "
          "printf \"QUOTE = 'fixed'\\n\" > /x/repo/parser.py; rm /x/repo/pkg/util.py; echo new > /x/repo/added.py; "
          f"{verify} after /x/tests /x/before.json /x/trajectory.json /x/out")
done = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{box}:/x", IMAGE, "sh", "-c", script],
                      capture_output=True, text=True, timeout=900)
answer = json.loads((box / "out" / "answer.json").read_text()) if (box / "out" / "answer.json").is_file() else {}
reward = json.loads((box / "out" / "reward.json").read_text()) if (box / "out" / "reward.json").is_file() else {}
check(done.returncode == 0 and done.stdout.startswith("3.12"),
      f"it runs in the image's own Python 3.12, with the network closed: {done.stdout[:60]!r} {done.stderr[-300:]}")
check(answer.get("reply") == "Fixed; pytest passes." and [c.get("command") for c in answer.get("tool_calls", [])]
      == ["pytest -q"] and reward == {"answered": 1, "trajectory": 1},
      f"and reads the answer and the calls: {answer.get('reply')!r} {reward}")
check(answer.get("actual_changes") == {"parser.py": "modified", "pkg/util.py": "deleted", "added.py": "added"}
      and answer.get("token_removed") is True and answer.get("final_state", {}).get("parser.py") == "QUOTE = 'fixed'\n",
      f"and what the agent changed, the defect's token gone: {answer.get('actual_changes')} "
      f"{answer.get('token_removed')}")
shutil.rmtree(box, ignore_errors=True)

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
