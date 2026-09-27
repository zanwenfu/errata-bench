#!/usr/bin/env python3
"""What building every task's image found, from scripts/build_environments.py's record (v1 step 2).

    scripts/environments_summary.py <release dir>/environments.json > results/v1-environments.md

Per task: whether its image built, its size and build time, how each install
went (strict: the lockfile as it stands; lenient: an ordinary install, the
lockfile put back; unlocked: no lockfile to follow; failed), whether the
installs left anything in `git status` (they must not: the working copy is the
developer's), and whether each of the project's own checks could start with
the network closed (a check that fails is the task's business; one that cannot
start means the image lacks something). Counts and task names only: no
project's output is copied here.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    record = json.loads(Path(argv[0]).read_text())
    rows = record["tasks"]
    built = [r for r in rows if r.get("built")]
    modes = Counter(line.split()[0] for r in built for line in r.get("installs", []))
    left = [(r["task_id"], r["install_left_in_git_status"]) for r in built if r.get("install_left_in_git_status")]
    checks = [(r["task_id"], c) for r in built for c in r.get("checks", [])]
    stuck = [(t, c) for t, c in checks if c.get("could_not_start")]
    passed = sum(1 for _, c in checks if c.get("exit") == "0")
    sizes = [r["image_mb"] for r in built if r.get("image_mb")]
    seconds = [r["build_seconds"] for r in rows if r.get("build_seconds") is not None]
    print(f"# Every task's image, built and checked (v1 step 2)\n")
    print(f"Built {record.get('built_at', '?')} by code `{str(record.get('code_version', '?'))[:12]}` "
          f"on the base `{record.get('base', {}).get('tag', '?')}`.\n")
    print(f"- **Images built:** {len(built)} of {len(rows)}"
          + (f"; not built: {', '.join(r['task_id'] for r in rows if not r.get('built'))}" if len(built) < len(rows) else "")
          + ".")
    if sizes:
        print(f"- **Size:** median {statistics.median(sizes):,.0f} MB, largest {max(sizes):,} MB; "
              f"build median {statistics.median(seconds):,.0f} s, longest {max(seconds):,} s.")
    print(f"- **Installs:** {sum(modes.values())} in all: "
          + ", ".join(f"{n} {m}" for m, n in sorted(modes.items(), key=lambda kv: -kv[1])) + ".")
    no_install = [r["task_id"] for r in built if not r.get("installs")]
    if no_install:
        print(f"- **Nothing to install:** {len(no_install)} tasks ({', '.join(no_install)}).")
    print(f"- **Left in `git status` by the installs:** "
          + ("nothing, in every task." if not left else
             "; ".join(f"{t}: {', '.join(v[:3])}" + (" ..." if len(v) > 3 else "") for t, v in left)))
    hidden = [(r["task_id"], r["install_hidden_from_git"]) for r in built if r.get("install_hidden_from_git")]
    if hidden:
        print(f"- **Added by the installs and hidden from git's view** (in `.git/info/exclude`, the files kept): "
              + "; ".join(f"{t}: {', '.join(v[:3])}" + (" ..." if len(v) > 3 else "") for t, v in hidden) + ".")
    print(f"- **The projects' own checks, run offline:** {len(checks)} in {len({t for t, _ in checks})} tasks; "
          f"{passed} passed, {len(checks) - passed - len(stuck)} ran and failed (the task's business), "
          f"{len(stuck)} could not start.")
    no_check = [r["task_id"] for r in built if not r.get("checks")]
    if no_check:
        print(f"- **No check of their own:** {len(no_check)} tasks ({', '.join(no_check)}).")
    if stuck:
        print("\n## Checks that could not start\n")
        print("| task | check | why |\n|---|---|---|")
        for t, c in stuck:
            print(f"| {t} | `{c['check']}` | {c['could_not_start']} |")
    lenient = [(r["task_id"], line) for r in built for line in r.get("installs", [])
               if line.split()[0] in ("lenient", "failed")]
    if lenient:
        print("\n## Installs that did not follow their lockfile\n")
        print("| task | install |\n|---|---|")
        for t, line in lenient:
            print(f"| {t} | `{line}` |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
