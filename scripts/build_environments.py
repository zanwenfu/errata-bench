#!/usr/bin/env python3
"""Build and check each frozen task's container (v1 step 2).

    scripts/build_environments.py <release dir> [--only <task id>...] [--min-free-gb 30] [--keep]

For each task in <release dir>/tasks/ (`scripts/freeze_tasks.py`): its recipe is
read from the frozen working copy (`errata_bench.release.environment`) and
written beside it, in environment/ -- the Dockerfile, the check script and the
recipe -- then the image is built, once on the shared base (`errata-base:v1`,
built first if missing), with the network open for the installs. The image is
then run twice with the network closed: once to read what the installs did
(/errata/install.log, and whether they left anything in `git status`), and
once to run the project's own checks, each read for whether it could start.
Everything goes to <release dir>/environments.json. The task's image is removed
afterwards unless --keep, so the disk holds the base and one task at a time.

The server is shared. Images are named errata-base:v1 and errata-v1/<task>,
tasks are built one at a time, and no build starts while the disk has less than
--min-free-gb free. No model calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.project import code_version  # noqa: E402
from errata_bench.release.environment import (  # noqa: E402
    BASE_DOCKERFILE, BASE_TAG, check_script, dockerfile, environment_failure, recipe)


def docker(*args: str, stdin: str | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout)


def free_gb() -> float:
    return shutil.disk_usage("/").free / 2**30


def contents(archive: Path) -> tuple[list[str], dict[str, str]]:
    """The working copy's files (paths under workspace/, outside .git) and its package.json texts."""
    files, packages = [], {}
    with tarfile.open(archive) as tar:
        for m in tar:
            parts = Path(m.name).parts
            if len(parts) < 2 or parts[1] == ".git" or not m.isfile():
                continue
            rel = "/".join(parts[1:])
            files.append(rel)
            if parts[-1] == "package.json" and "node_modules" not in parts and len(parts) <= 5:
                packages[rel] = tar.extractfile(m).read().decode("utf-8", "replace")
    return files, packages


def build_base() -> str:
    if docker("image", "inspect", BASE_TAG).returncode != 0:
        done = docker("build", "-t", BASE_TAG, "-", stdin=BASE_DOCKERFILE, timeout=3600)
        if done.returncode != 0:
            raise SystemExit(f"the base image did not build:\n{done.stderr[-3000:]}")
    return docker("image", "inspect", BASE_TAG, "--format", "{{.Id}}").stdout.strip()


def one(task_dir: Path, keep: bool) -> dict:
    meta = json.loads((task_dir / "task.json").read_text())
    files, packages = contents(task_dir / "workspace.tar.gz")
    r = recipe(meta, files, packages)
    env = task_dir / "environment"
    env.mkdir(exist_ok=True)
    (env / "Dockerfile").write_text(dockerfile(r))
    (env / "check.sh").write_text(check_script(r))
    (env / "recipe.json").write_text(json.dumps(r.__dict__, indent=1) + "\n")
    context = env / "workspace.tar.gz"
    if not context.exists():
        os.link(task_dir / "workspace.tar.gz", context)
    image = f"errata-v1/{meta['task_id'].lower()}:latest"
    row = {"task_id": meta["task_id"], "image": image, "recipe": r.__dict__}
    t0 = time.monotonic()
    built = docker("build", "-t", image, str(env), timeout=3600)
    row["build_seconds"] = round(time.monotonic() - t0)
    if built.returncode != 0:
        row["built"], row["build_error"] = False, (built.stderr or built.stdout)[-3000:]
        return row
    row["built"] = True
    row["image_mb"] = round(int(docker("image", "inspect", image, "--format", "{{.Size}}").stdout.strip() or 0) / 2**20)
    info = docker("run", "--rm", "--network", "none", image, "sh", "-c",
                  "cat /errata/install.log 2>/dev/null; echo '=== output'; tail -n 40 /errata/install-output.log "
                  "2>/dev/null; echo '=== new in git status'; "
                  "diff /errata/status-before.txt /errata/status-after.txt | grep '^>' | head -20", timeout=300)
    head, _, rest = info.stdout.partition("=== output")
    row["installs"] = [line for line in head.splitlines() if line.strip()]
    output, _, noise = rest.partition("=== new in git status")
    row["install_output_tail"] = output.strip()[-2000:]
    row["install_left_in_git_status"] = [line[2:] for line in noise.splitlines() if line.startswith(">")]
    t1 = time.monotonic()
    ran = docker("run", "--rm", "-i", "--network", "none", "--cpus", "2", "--memory", "4g", image, "sh", "-s",
                 stdin=check_script(r), timeout=3600)
    row["check_seconds"] = round(time.monotonic() - t1)
    checks = []
    for block in ran.stdout.split("=== check ")[1:]:
        header, _, tail = block.partition("\n")
        if header.startswith("status after checks"):
            continue
        tail = tail.split("=== status after checks")[0]
        exit_code = header.split(" exit ", 1)[1].split(":", 1)[0].strip() if " exit " in header else "?"
        checks.append({"check": header.split(": ", 1)[-1], "exit": exit_code,
                       "could_not_start": environment_failure(tail), "tail": tail.strip()[-1500:]})
    row["checks"] = checks
    if not keep:
        docker("rmi", image, timeout=600)
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--min-free-gb", type=float, default=30.0)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    tasks = sorted(d for d in (args.release / "tasks").iterdir() if (d / "task.json").exists())
    if args.only:
        tasks = [d for d in tasks if d.name in set(args.only)]
    out = args.release / "environments.json"
    done = {r["task_id"]: r for r in (json.loads(out.read_text())["tasks"] if out.exists() else [])}
    if free_gb() < args.min_free_gb:
        raise SystemExit(f"{free_gb():.0f} GB free, under the {args.min_free_gb:.0f} GB floor: nothing built")
    base = build_base()
    for d in tasks:
        if free_gb() < args.min_free_gb:
            print(f"stopping: {free_gb():.0f} GB free, under the {args.min_free_gb:.0f} GB floor", flush=True)
            break
        row = one(d, args.keep)
        done[row["task_id"]] = row
        started = sum(1 for c in row.get("checks", []) if not c["could_not_start"])
        print(f"  {'built' if row.get('built') else 'FAILED'} {row['task_id']:42} "
              f"{row.get('image_mb', 0):6} MB  installs: {', '.join(i.split()[0] for i in row.get('installs', [])) or '-'}"
              f"  checks started {started}/{len(row.get('checks', []))}  {free_gb():.0f} GB free", flush=True)
        out.write_text(json.dumps({"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "code_version": code_version(), "base": {"tag": BASE_TAG, "id": base},
                                   "tasks": sorted(done.values(), key=lambda r: r["task_id"])}, indent=1) + "\n")
    rows = list(done.values())
    print(f"{sum(bool(r.get('built')) for r in rows)} of {len(rows)} built; checks that could start: "
          f"{sum(1 for r in rows for c in r.get('checks', []) if not c['could_not_start'])} of "
          f"{sum(len(r.get('checks', [])) for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
