"""The code commits a repository's sessions produced: every commit carrying an ``Entire-Checkpoint`` trailer.

SWE-chat's commits table holds, for each commit linked to a checkpoint, its
dates, message, name-status and patch. The pipeline reads those to place a
session's moments against its commits and to pick the commit a task is
rebuilt from (`corpus.timeline`).

For each repository, anonymously:

1. a bare, blobless clone of every branch: commits and trees, no file
   contents;
2. ``git log --all`` for commits whose message holds the trailer. That covers
   every branch, where commit search reads only the default one;
3. each such commit's name-status, read from its trees alone;
4. its patch and line counts. The blobs its diff needs are fetched in batches
   first, and a patch over `PATCH_CAP` is cut and marked;
5. the clone is deleted.

Written to ``<out>/link/<owner>__<repo>/commits.jsonl``, then one status row
in ``<out>/link.jsonl``. Authors' names and addresses are not kept.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..store.rows import append, load
from . import entire
from .gitio import GitError, git, prefetch

PATCH_CAP = 1 << 20  # SWE-chat's patches: median 7.3k characters, 95th percentile 128k
SIZE_CAP_KB = 2_000_000  # a repository over 2 GB is not cloned; it is recorded
SEP, END = "\x1f", "\x1e"


def slug(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def trailer_commits(repo: Path) -> list[dict]:
    """Every commit on any branch whose message holds an Entire-Checkpoint trailer."""
    fmt = SEP.join(["%H", "%P", "%aI", "%cI", "%B"]) + END
    raw = git("log", "--all", f"--format={fmt}", "--extended-regexp", "--grep=^Entire-Checkpoint:", cwd=repo)
    out = []
    for record in raw.decode(errors="replace").split(END):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, parents, adate, cdate, message = record.split(SEP, 4)
        ids = entire.trailer_ids(message)
        if ids:
            out.append({"commit_sha": sha, "parents": parents.split(), "author_date": adate, "commit_date": cdate,
                        "commit_message": message, "checkpoint_ids": ids})
    return out


def _diff_blobs(repo: Path, sha: str, parent: str | None) -> list[str]:
    """The blob ids a commit's diff reads: both sides of every changed file."""
    args = ["diff-tree", "-r", "--no-commit-id", "-z"] + ([parent, sha] if parent else ["--root", sha])
    oids = []
    fields = git(*args, cwd=repo).split(b"\0")
    for i, field in enumerate(fields):
        if field.startswith(b":"):
            parts = field[1:].split()
            if len(parts) >= 4:
                oids += [o.decode() for o in parts[2:4] if set(o) != {ord("0")}]
    return oids


def describe(repo: Path, commit: dict) -> dict:
    """Name-status, line counts and patch for one commit, against its first parent."""
    sha = commit["commit_sha"]
    parent = commit["parents"][0] if commit["parents"] else None
    base = [parent, sha] if parent else ["--root", sha]
    names = git("diff-tree", "-r", "--no-commit-id", "--name-status", *base, cwd=repo).decode(errors="replace")
    prefetch(repo, _diff_blobs(repo, sha, parent))
    numstat = git("diff-tree", "-r", "--no-commit-id", "--numstat", *base, cwd=repo).decode(errors="replace")
    patch = git("diff-tree", "-r", "--no-commit-id", "-p", "--no-color", *base, cwd=repo)
    cut = len(patch) > PATCH_CAP
    added = deleted = 0
    for line in numstat.splitlines():
        a, d, _ = (line.split("\t", 2) + ["", ""])[:3]
        added += int(a) if a.isdigit() else 0
        deleted += int(d) if d.isdigit() else 0
    return {**commit, "files_changed": names.strip(), "numstat": numstat.strip(),
            "files_changed_count": len([n for n in names.splitlines() if n.strip()]),
            "total_additions": added, "total_deletions": deleted,
            "patch": patch[:PATCH_CAP].decode(errors="replace"), "patch_cut": cut, "status": "ok"}


def link_repo(repo_id: str, out: Path, *, base: str = "https://github.com", size_kb: int | None = None) -> dict:
    """Every trailer commit of one repository into ``out/link/<slug>/commits.jsonl``; its status row."""
    t0 = time.time()
    if size_kb and size_kb > SIZE_CAP_KB:
        return {"repo": repo_id, "status": "too_large", "size_kb": size_kb}
    clone = out / "git-link" / f"{slug(repo_id)}.git"
    shutil.rmtree(clone, ignore_errors=True)
    clone.parent.mkdir(parents=True, exist_ok=True)
    try:
        git("clone", "-q", "--bare", "--filter=blob:none", "--no-tags", f"{base}/{repo_id}", str(clone),
            timeout=3600)
    except GitError as e:
        return {"repo": repo_id, "status": "unreachable", "error": str(e)[-200:]}
    try:
        commits = trailer_commits(clone)
        dest = out / "link" / slug(repo_id)
        dest.mkdir(parents=True, exist_ok=True)
        failed = 0
        with open(dest / "commits.jsonl.part", "w") as f:
            for c in commits:
                try:
                    row = describe(clone, c)
                except GitError as e:
                    failed += 1
                    row = {**c, "status": "diff_failed", "error": str(e)[-200:]}
                f.write(json.dumps({"repo_id": repo_id, **row}) + "\n")
        (dest / "commits.jsonl.part").replace(dest / "commits.jsonl")
    finally:
        shutil.rmtree(clone, ignore_errors=True)
    return {"repo": repo_id, "status": "ok", "commits": len(commits), "diff_failed": failed,
            "checkpoints": len({i for c in commits for i in c["checkpoint_ids"]}), "seconds": round(time.time() - t0, 1)}


def link_all(repo_ids: list[str], out: Path, *, workers: int = 3, sizes: dict[str, int] | None = None, log=print,
             **where) -> None:
    """Link every repository not yet done, a few at a time, one status row each."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    done = {r["repo"] for r in load(out / "link.jsonl") if r.get("status") in ("ok", "unreachable", "too_large")}
    todo = [r for r in repo_ids if r not in done]
    log(f"{len(todo)} to link ({len(repo_ids) - len(todo)} already done)")

    def one(repo_id: str) -> dict:
        try:
            return link_repo(repo_id, out, size_kb=(sizes or {}).get(repo_id), **where)
        except Exception as e:  # recorded and retried on the next run
            return {"repo": repo_id, "status": "error", "error": f"{type(e).__name__}: {str(e)[-300:]}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, r): r for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            append(out / "link.jsonl", row)
            log(f"[{i}/{len(todo)}] {row['repo']}: {row['status']} {row.get('commits', 0)} commits, "
                f"{row.get('seconds', '?')}s")
