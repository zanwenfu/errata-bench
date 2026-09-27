"""Each Harbor task's content digest, as Harbor records it for every trial it runs.

    python -m errata_harbor.digests <tasks dir>

Writes <tasks dir>/digests.json: {task folder: "sha256:..."}, by Harbor's own
function (`Packager.compute_content_hash`), which is what a trial's lock.json
records as `task.digest`. Grading compares the two (`errata_bench.release.
grading.official`): a trial whose task differs in anything -- its
instruction, its network rules, its image, its verifier -- is not a run of
the task as published. Run in Harbor's environment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harbor.publisher.packager import Packager


def digests(tasks: Path) -> dict[str, str]:
    """Every task folder's digest, keyed by the folder's name."""
    return {d.name: "sha256:" + Packager.compute_content_hash(d)[0]
            for d in sorted(tasks.iterdir()) if (d / "task.toml").is_file()}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    tasks = Path(argv[0])
    found = digests(tasks)
    (tasks / "digests.json").write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")
    print(f"{len(found)} task digests written to {tasks / 'digests.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
