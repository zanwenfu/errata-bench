"""Fetch one repository's Entire sessions: metadata for every checkpoint, the latest transcript of each session.

For each repository, anonymously:

1. `git ls-remote` tells which of Entire's layouts it holds (`entire.py`), and
   its `.entire/settings.json` whether it names a public checkpoint
   repository. Each place holding checkpoints is a *source*.
2. A blobless, tips-only fetch of only Entire's refs gives every checkpoint's
   tree; the metadata files are then fetched in batches and read.
3. Each session's latest copy is kept (`entire.latest_copies`), and only its
   `full.jsonl` is fetched, streamed straight to
   ``<out>/raw/<owner>__<repo>/transcripts/<session_id>.jsonl``.

Written per repository: ``sessions.jsonl`` (one row per session kept) and
``checkpoints.jsonl`` (one per checkpoint), then one status row in
``<out>/fetch.jsonl``, last, so a repository with a status row is complete. A
session left out is counted with its reason: imported history (anchored to the
default branch's head, not the commit it started from), an id unsafe as a file
name, a copy with no transcript.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..store.rows import append, load
from . import entire
from .gitio import GitError, bare_promisor, blob_ids, fetch_trees, ls_remote, prefetch, read_small, write_blobs

# A session's own metadata fields kept on its row: enough to join, filter and
# describe it. The transcript holds the rest.
SESSION_FIELDS = ("session_id", "agent", "model", "created_at", "branch", "cli_version", "strategy", "kind",
                  "checkpoints_count", "files_touched", "token_usage", "session_metrics", "initial_attribution",
                  "turn_id", "checkpoint_transcript_start", "transcript_lines_at_start")
TERMINAL = {"ok", "no_checkpoints", "unreachable"}


def slug(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def settings_of(repo_id: str, timeout: int = 30) -> str | None:
    """The default branch's `.entire/settings.json`, or None when it has none."""
    url = f"https://raw.githubusercontent.com/{repo_id}/HEAD/.entire/settings.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read(1 << 20).decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _copies(repo: Path, layout: entire.Layout, source: str) -> tuple[list[entire.Copy], list[dict]]:
    """Every session copy in a source's checkpoints, and one row per checkpoint."""
    folders = []  # (checkpoint id, layout, rev, prefix, {path: oid})
    if layout.v1_branch:
        ids = blob_ids(repo, "refs/crawl/v1")
        grouped: dict[str, dict[str, str]] = {}
        for path, oid in ids.items():  # one pass: a large branch holds tens of thousands of files
            parts = path.split("/", 2)
            if len(parts) == 3:
                grouped.setdefault(f"{parts[0]}/{parts[1]}/", {})[parts[2]] = oid
        for cid, prefix in entire.v1_checkpoints(list(ids)):
            folders.append((cid, "v1", "refs/crawl/v1", prefix, grouped.get(prefix, {})))
    for cid in layout.per_checkpoint:
        rev = f"refs/crawl/cp/{cid[-2:]}/{cid}"
        folders.append((cid, "refs", rev, "", blob_ids(repo, rev)))

    wanted = []
    for cid, _, _, _, files in folders:
        wanted += [o for p, o in files.items() if p == "metadata.json" or (p.count("/") == 1 and p.endswith("/metadata.json"))]
    prefetch(repo, wanted)
    blobs = read_small(repo, sorted(set(wanted)))

    copies, checkpoints = [], []
    for cid, kind, rev, prefix, files in folders:
        root = _json(blobs.get(files.get("metadata.json", "")))
        indexes = sorted({p.split("/")[0] for p in files if p.split("/")[0].isdigit()}, key=int)
        checkpoints.append({"checkpoint_id": cid, "source": source, "layout": kind,
                            "cli_version": (root or {}).get("cli_version"), "branch": (root or {}).get("branch"),
                            "sessions": len(indexes), "files_touched": len((root or {}).get("files_touched") or []),
                            "metadata_readable": root is not None})
        for n in indexes:
            meta = _json(blobs.get(files.get(f"{n}/metadata.json", "")))
            if meta is None:
                continue
            meta = {**meta, "_transcript_oid": files.get(f"{n}/full.jsonl"), "_source": source, "_layout": kind}
            copies.append(entire.Copy(checkpoint_id=cid, tree=f"{rev}:{prefix}{n}", metadata=meta))
    return copies, checkpoints


def _sha256(path: Path) -> str:
    """A file's digest, read in pieces: a transcript can run to tens of megabytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def _json(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def fetch_repo(repo_id: str, out: Path, *, keep_git: bool = False, base: str = "https://github.com",
               settings=settings_of) -> dict:
    """Fetch one repository's sessions into ``out/raw/<slug>/``; its status row.

    `base` and `settings` are where repositories and settings files are read
    from: GitHub, or local fixtures in the checks.
    """
    t0 = time.time()
    status = {"repo": repo_id, "sources": [], "checkpoints": 0, "sessions": 0, "skipped": {}, "bytes": 0}
    try:
        refs = ls_remote(f"{base}/{repo_id}")
    except GitError as e:
        return {**status, "status": "unreachable", "error": str(e)[-200:], "seconds": round(time.time() - t0, 1)}

    sources = [(repo_id, entire.layout_of(refs))]
    try:
        remote = entire.checkpoint_remote(settings(repo_id))
    except (urllib.error.URLError, TimeoutError) as e:
        remote, status["settings_error"] = None, str(e)[-200:]
    if remote and remote.lower() != repo_id.lower():
        try:
            sources.append((remote, entire.layout_of(ls_remote(f"{base}/{remote}"))))
        except GitError:
            status["checkpoint_remote"] = {"repo": remote, "reachable": False}
        else:
            status["checkpoint_remote"] = {"repo": remote, "reachable": True}

    dest = out / "raw" / slug(repo_id)
    copies, checkpoints = [], []
    for source, layout in sources:
        if not layout.has_checkpoints:
            continue
        status["sources"].append({"repo": source, "v1_branch": layout.v1_branch,
                                  "per_checkpoint": len(layout.per_checkpoint), "v2": layout.v2})
        # One directory per repository and source: two repositories can name
        # the same checkpoint repository, and a shared directory would be
        # fetched into and deleted under each other's feet.
        git_dir = out / "git" / slug(repo_id) / f"{slug(source)}.git"
        bare_promisor(git_dir, f"{base}/{source}")
        specs = ([f"+{entire.V1_BRANCH}:refs/crawl/v1"] if layout.v1_branch else []) + \
                [f"+{entire.REF_PREFIX}{c[-2:]}/{c}:refs/crawl/cp/{c[-2:]}/{c}" for c in layout.per_checkpoint]
        for i in range(0, len(specs), 500):
            fetch_trees(git_dir, specs[i:i + 500])
        got, rows = _copies(git_dir, layout, source)
        copies += [(git_dir, c) for c in got]
        checkpoints += rows

    if not status["sources"]:
        return {**status, "status": "no_checkpoints", "seconds": round(time.time() - t0, 1)}

    chosen = entire.latest_copies([c for _, c in copies])
    git_of = {id(c): g for g, c in copies}
    skipped: dict[str, int] = {}
    keep = []
    for sid, (copy, seen_in) in chosen.items():
        reason = ("imported" if copy.imported else "unsafe_session_id" if not entire.SAFE_ID.match(sid)
                  else "no_transcript" if not copy.metadata.get("_transcript_oid") else None)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        keep.append((sid, copy, seen_in))

    by_git: dict[Path, list[tuple[str, Path]]] = {}
    for sid, copy, _ in keep:
        by_git.setdefault(git_of[id(copy)], []).append((copy.metadata["_transcript_oid"], dest / "transcripts" / f"{sid}.jsonl"))
    total = 0
    for git_dir, targets in by_git.items():
        prefetch(git_dir, [o for o, _ in targets])
        total += write_blobs(git_dir, targets)

    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "sessions.jsonl.part", "w") as f:
        for sid, copy, seen_in in keep:
            path = dest / "transcripts" / f"{sid}.jsonl"
            digest = _sha256(path)
            row = {k: copy.metadata.get(k) for k in SESSION_FIELDS}
            row.update({"repo": repo_id, "source": copy.metadata["_source"], "layout": copy.metadata["_layout"],
                        "checkpoint_id": copy.checkpoint_id, "checkpoint_ids": seen_in,
                        "transcript_bytes": path.stat().st_size, "transcript_sha256": digest})
            f.write(json.dumps(row) + "\n")
    (dest / "sessions.jsonl.part").replace(dest / "sessions.jsonl")
    with open(dest / "checkpoints.jsonl.part", "w") as f:
        for row in checkpoints:
            f.write(json.dumps(row) + "\n")
    (dest / "checkpoints.jsonl.part").replace(dest / "checkpoints.jsonl")

    if not keep_git:
        shutil.rmtree(out / "git" / slug(repo_id), ignore_errors=True)
    return {**status, "status": "ok", "checkpoints": len(checkpoints), "sessions": len(keep), "skipped": skipped,
            "bytes": total, "seconds": round(time.time() - t0, 1)}


def done(out: Path) -> set[str]:
    """Repositories with a terminal status row; an error is tried again."""
    return {r["repo"] for r in load(out / "fetch.jsonl") if r.get("status") in TERMINAL}


def fetch_all(repo_ids: list[str], out: Path, *, workers: int = 4, keep_git: bool = False, log=print,
              **where) -> None:
    """Fetch every repository not yet done, a few at a time, one status row each."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    todo = [r for r in repo_ids if r not in done(out)]
    log(f"{len(todo)} to fetch ({len(repo_ids) - len(todo)} already done)")

    def one(repo_id: str) -> dict:
        try:
            return fetch_repo(repo_id, out, keep_git=keep_git, **where)
        except Exception as e:  # recorded and retried on the next run, never silently dropped
            return {"repo": repo_id, "status": "error", "error": f"{type(e).__name__}: {str(e)[-300:]}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, r): r for r in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            append(out / "fetch.jsonl", row)
            log(f"[{i}/{len(todo)}] {row['repo']}: {row['status']} {row.get('sessions', 0)} sessions, "
                f"{row.get('bytes', 0) / 1e6:.1f} MB, {row.get('seconds', '?')}s")
