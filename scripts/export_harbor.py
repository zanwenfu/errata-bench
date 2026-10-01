#!/usr/bin/env python3
"""Write the frozen tasks as Harbor tasks (v1 step 3).

    scripts/export_harbor.py <release dir> [--out <dir>] [--only <task id>...] [--check]

Each task frozen in <release dir>/tasks/ (`scripts/freeze_tasks.py`) is written
to <out>/<task id>/ (default <release dir>/harbor) by
`errata_bench.release.harbor.export`: instruction.md, task.toml, environment/
and tests/. The directory is then a Harbor dataset:

    harbor run -p <out> -a <agent> -m <model> --ak disable_web_search=true

<out>/export.json lists what is there: a task no longer frozen in <release
dir>/tasks/ (set aside, `scripts/apply_rescreen.py`) loses its row. Writing a
task changes its digest, so <out>/digests.json is removed when any task is
written: record the digests again (`python -m errata_harbor.digests <out>`, in
Harbor's environment). A task folder in <out> with no frozen task would be run
as a task of the release, so the export exits 1 naming it. --check writes
nothing: it exits 1 naming each Harbor task that no longer gives the
instruction this code builds for its frozen task (`harbor.stale`), and each
folder with no frozen task. Run it before recording digests, and before any
trial. Needs neither the corpus nor GitHub, and makes no model calls.
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
from errata_bench.release.harbor import VERSION, export, stale  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if any Harbor task is out of date or has no frozen task")
    args = ap.parse_args(argv)
    out = args.out or args.release / "harbor"
    tasks = sorted(d for d in (args.release / "tasks").iterdir() if (d / "task.json").exists())
    if args.check:
        frozen = {d.name for d in tasks}
        old = [d.name for d in tasks if stale(d, out / d.name)]
        orphans = sorted(d.name for d in out.iterdir() if (d / "task.toml").is_file() and d.name not in frozen) \
            if out.is_dir() else []
        for name in old:
            print(f"  out of date: {name} (write it again: scripts/export_harbor.py {args.release} --only {name})")
        for name in orphans:
            print(f"  no frozen task: {name} (remove {out / name})")
        print(f"{len(tasks) - len(old)} of {len(tasks)} Harbor tasks give the instruction this code builds"
              + (f"; {len(orphans)} folder(s) with no frozen task" if orphans else ""))
        return 1 if old or orphans else 0
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
    # Rows of the tasks frozen now: one set aside since kept its row (09-30 review).
    frozen = {d.name for d in (args.release / "tasks").iterdir() if (d / "task.json").exists()}
    kept = [r for r in (json.loads(listing.read_text())["tasks"] if listing.exists() else [])
            if r["task_id"] not in {x["task_id"] for x in rows} and r["task_id"] in frozen]
    listing.write_text(json.dumps({
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_version": code_version(),
        "benchmark_version": VERSION, "from": str(args.release),
        "tasks": sorted(kept + rows, key=lambda r: r["task_id"])}, indent=1) + "\n")
    print(f"{len(rows)} tasks written to {out}")
    if (out / "digests.json").is_file():
        (out / "digests.json").unlink()
        print(f"  {out / 'digests.json'} removed: it described the tasks as they were; record the digests again "
              f"(python -m errata_harbor.digests {out}, in Harbor's environment)")
    orphans = sorted(d.name for d in out.iterdir() if (d / "task.toml").is_file() and d.name not in frozen)
    if orphans:
        print(f"  {len(orphans)} Harbor task folder(s) in {out} have no frozen task, and `harbor run -p {out}` would "
              f"run them: {', '.join(orphans[:5])}; remove them", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
