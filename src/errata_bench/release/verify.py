"""What grading needs from a Harbor trial, recorded inside its container (v1 step 3).

    python3 -m errata_bench.release.verify before <working copy> <before.json>
    python3 -m errata_bench.release.verify after <task dir> <before.json> <trajectory.json> <out dir>

`before` runs as the last step of a task's image build, when the dependencies
are installed and nothing has touched the working copy yet: it writes the
working copy's snapshot (`errata_bench.changes.snapshot`).

`after` is the task's verifier (tests/test.sh), run by Harbor in the agent's
container once the agent has finished. From the task (<task dir>/task.json), the
snapshot taken at build time and the agent's trajectory (ATIF, which Harbor
writes for the agents it runs), it writes to <out dir>:

  answer.json  what the agent answered and every call it made
               (`release.atif.record_of`), what it changed in the working copy
               and the files that decide the verdict (`changes.diff`,
               `changes.capture` and `changes.capped`, as this harness keeps
               its own answers), whether the defect's token survived (read on
               every captured file, before the cap, as the harness reads it),
               and the digest of the instruction the agent was given -- the
               row `errata-bench grade` reads;
  reward.json  Harbor's own summary: {"answered": 1 or 0, "trajectory": 1 or 0}.

Nothing is graded here. Grading asks a model three times per answer, reads each
task's calibration, and settles readings by majority; it runs outside the
container, from answer.json, by the same code that grades this harness's own
answers. So no key is needed in the container, and the verifier runs with the
network closed.

The snapshot taken at build time is checked against the frozen working copy's
own (<task dir>/workspace.json, which the agent never sees: Harbor uploads the
task's tests only after the agent has finished), and any file that differs is
named in answer.json. This module and those it imports use Python's standard
library alone: the container has nothing else for them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from errata_bench.changes import capped, capture, diff, snapshot
from errata_bench.release.atif import instruction_at, record_of

VERSION = "1.0"


def write_before(tree: Path, out: Path) -> dict:
    """The working copy's snapshot, as JSON: {path: [size, hash, mode]}."""
    shot = {path: list(mark) for path, mark in snapshot(tree).items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(shot, sort_keys=True))
    return shot


def _load_snapshot(path: Path) -> dict[str, tuple]:
    return {k: tuple(v) for k, v in json.loads(path.read_text()).items()}


def after(task_dir: Path, before_path: Path, trajectory_path: Path, out: Path) -> dict:
    """Record the trial: the answer, the calls, and what changed. Returns answer.json's contents."""
    task = json.loads((task_dir / "task.json").read_text())
    tree = Path(task["workdir"])
    given = (task_dir / "instruction.md").read_bytes() if (task_dir / "instruction.md").exists() else b""
    instruction = given.decode("utf-8")
    row: dict = {"benchmark": "errata-bench", "version": VERSION, "task_id": task["task_id"],
                 "workdir": task["workdir"], "instruction_sha256": hashlib.sha256(given).hexdigest()}

    trajectory = None
    if trajectory_path.is_file():
        raw = trajectory_path.read_bytes()
        try:
            trajectory = json.loads(raw)
        except ValueError as e:
            row["trajectory_error"] = f"not JSON: {e}"
        row["trajectory"] = {"present": True, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    else:
        row["trajectory"] = {"present": False}
    if isinstance(trajectory, dict):
        agent = trajectory.get("agent") or {}
        steps = trajectory.get("steps") or []
        row["trajectory"].update({
            "agent": {k: agent.get(k) for k in ("name", "version", "model_name")},
            "schema_version": trajectory.get("schema_version"),
            "steps": len(steps),
            "instruction_found": instruction_at(steps, instruction) is not None if instruction else None,
        })
        row["reply"], row["tool_calls"] = record_of(trajectory, instruction or None)
    else:
        row["reply"], row["tool_calls"] = "", []

    if before_path.is_file() and tree.is_dir():
        before = _load_snapshot(before_path)
        # Both snapshots by one rule: without the defect's file kept inside a
        # tool's cache (`snapshot`'s `keep`), since the build step would have to
        # name it, a hint about where the defect is. No task's file is inside one.
        now = snapshot(tree)
        changed = diff(before, now)
        signature = SimpleNamespace(signature_path=task.get("signature_path") or "",
                                    signature_token=task.get("signature_token") or "")
        state = capture(tree, signature, changed)
        row["actual_changes"] = changed
        row["final_state"] = capped(state, signature.signature_path)
        row["final_state_files"] = len(state)
        # On every captured file, before the cap: a file cut to fit could lose
        # the token and read as fixed (`score.structure.analyse`).
        row["token_removed"] = (not any(signature.signature_token in body for body in state.values())
                                if signature.signature_token and state else None)
        workspace = task_dir / "workspace.json"
        if workspace.is_file():
            frozen = _load_snapshot(workspace)
            row["before"] = {"files": len(before), "workspace_files": len(frozen),
                             "differ_from_workspace": sorted(p for p, m in frozen.items() if before.get(p) != m)}
        else:
            row["before"] = {"files": len(before), "workspace_files": None, "differ_from_workspace": None}
    else:
        # Nothing to compare: a trial without its build-time snapshot or its
        # working copy. Recorded as unknown, never as "changed nothing" -- an
        # empty change list is a claim that the agent did no work.
        row["actual_changes"], row["final_state"], row["token_removed"] = None, None, None
        row["capture_error"] = ("no snapshot from the image build at " + str(before_path)
                                if not before_path.is_file() else f"no working copy at {tree}")

    out.mkdir(parents=True, exist_ok=True)
    (out / "answer.json").write_text(json.dumps(row, ensure_ascii=False, indent=1) + "\n")
    reward = {"answered": 1 if str(row["reply"]).strip() else 0, "trajectory": 1 if trajectory is not None else 0}
    (out / "reward.json").write_text(json.dumps(reward) + "\n")
    return row


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "before":
        shot = write_before(Path(argv[1]), Path(argv[2]))
        print(f"errata-bench: {len(shot)} files in the working copy's snapshot")
        return 0
    if len(argv) == 5 and argv[0] == "after":
        row = after(Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[4]))
        changed = row.get("actual_changes")
        print(f"errata-bench: answered {bool(str(row['reply']).strip())}, {len(row['tool_calls'])} calls, "
              f"{'unknown' if changed is None else len(changed)} files changed")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
