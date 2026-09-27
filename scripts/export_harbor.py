#!/usr/bin/env python3
"""Write the frozen tasks as Harbor tasks (v1 step 3).

    scripts/export_harbor.py <release dir> [--out <dir>] [--only <task id>...]

Each task frozen in <release dir>/tasks/ (`scripts/freeze_tasks.py`) is written
to <out>/<task id>/ (default <release dir>/harbor) by
`errata_bench.release.harbor.export`: instruction.md, task.toml, environment/
and tests/. The directory is then a Harbor dataset:

    harbor run -p <out> -a <agent> -m <model> --ak disable_web_search=true

<out>/export.json lists what was written. Needs neither the corpus nor GitHub,
and makes no model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.project import code_version  # noqa: E402
from errata_bench.release.environment import workspace_contents  # noqa: E402
from errata_bench.release.harbor import VERSION, export  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args(argv)
    out = args.out or args.release / "harbor"
    tasks = sorted(d for d in (args.release / "tasks").iterdir() if (d / "task.json").exists())
    if args.only:
        tasks = [d for d in tasks if d.name in set(args.only)]
    if not tasks:
        ap.error(f"no frozen tasks in {args.release / 'tasks'}")
    rows = []
    for d in tasks:
        row = export(d, out / d.name, *workspace_contents(d / "workspace.tar.gz"))
        rows.append(row)
        cut = "" if row["tool_cap"] is None else f", tool traffic cut to {row['tool_cap']:,}"
        print(f"  {row['task_id']:42} {row['instruction_chars']:8,} characters{cut}  "
              f"{row['workspace_files']:5} files  installs: {', '.join(row['installs']) or '-'}", flush=True)
    listing = out / "export.json"
    kept = [r for r in (json.loads(listing.read_text())["tasks"] if listing.exists() else [])
            if r["task_id"] not in {x["task_id"] for x in rows}]
    listing.write_text(json.dumps({
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_version": code_version(),
        "benchmark_version": VERSION, "from": str(args.release),
        "tasks": sorted(kept + rows, key=lambda r: r["task_id"])}, indent=1) + "\n")
    print(f"{len(rows)} tasks written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
