"""Assemble the collected sessions into SWE-chat's six tables, so the pipeline reads them as it reads SWE-chat (#16).

Reads what `discover`, `fetch` and `link` wrote under ``<out>`` and writes
``<out>/corpus/``, the same files SWE-chat ships, in the same schemas:
``conversations``, ``sessions``, ``checkpoints``, ``commits``,
``repositories`` and ``session_logs`` (parquet), and
``transcripts/<session_id>.jsonl``. Beside them, what SWE-chat's period did
not need: ``subagents/<session_id>/<call id>/``, each subagent's own
transcript, since Claude Code 2.1 no longer writes a subagent's calls into its
parent's. Files are hard-linked from the fetch, not copied.

Only Claude Code sessions are written for now. The pipeline replays Claude
Code's edit tools when it rebuilds a task, and the other agents' edits need
the same first (#16, "Scope"). Every session left out is counted, with its
reason, in ``corpus/left_out.json``.

A session fetched under several repositories is written once. That happens
when they name one checkpoint repository: each is given all of its sessions.
The session is credited to a repository whose commits carry the trailer of
its latest checkpoint, if any does; else to one whose commits carry an earlier
checkpoint's; else to any holder. Within the first of those groups that has
one, it goes to the first by discovery's rank, as a commit's owner is chosen:
not a fork, then earliest created (`discover.rank`), and the holder's own copy
of the session is the one written. Name order alone credited Entire's own
sessions to a copy of entireio/cli that kept its settings and its trailers
(G-80). A copy is still credited over its original when the original is a
GitHub fork, or has no creation date on record; neither is true of any
repository selected so far.

Everything is streamed: conversations are written a session at a time in row
groups, so memory holds one session's rows, not the corpus.
"""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..store.rows import load
from .discover import rank
from .label import digest
from .shape import CONVERSATIONS, claude_code_rows, is_claude_code, read_entries

S = pa.large_string()
TS_NS, TS_US = pa.timestamp("ns", tz="UTC"), pa.timestamp("us", tz="UTC")
SESSIONS = pa.schema([
    ("session_id", S), ("repo_id", S), ("owner_id", S), ("user_id", S), ("checkpoint_ids", S),
    ("canonical_checkpoint_pk", S), ("agent", S), ("strategy", S), ("branch", S), ("created_at", TS_NS),
    ("cli_version", S), ("files_touched", S), ("files_touched_count", pa.int64()), ("checkpoints_count", pa.int64()),
    ("input_tokens", pa.int64()), ("output_tokens", pa.int64()), ("cache_creation_tokens", pa.int64()),
    ("cache_read_tokens", pa.int64()), ("api_call_count", pa.int64()), ("agent_lines", pa.float64()),
    ("human_added", pa.float64()), ("human_modified", pa.float64()), ("human_removed", pa.float64()),
    ("total_committed", pa.float64()), ("agent_percentage", pa.float64()), ("attribution_calculated_at", TS_NS),
    ("transcript_identifier_at_start", S), ("transcript_path", S), ("tool_call_count", pa.float64()),
    ("unique_tools_count", pa.float64()), ("research_count", pa.float64()), ("action_count", pa.float64()),
    ("first_write_position", pa.float64()), ("duration_seconds", pa.float64()), ("turn_count", pa.float64()),
    ("prompt_count", pa.int64()), ("content_hash", S), ("user_persona", S), ("session_success", S),
])
CHECKPOINTS = pa.schema([
    ("checkpoint_pk", S), ("checkpoint_id", S), ("repo_id", S), ("session_pks", S), ("session_count", pa.int64()),
    ("commit_shas", S), ("commit_count", pa.int64()), ("author_user_ids", S), ("unique_author_count", pa.float64()),
    ("user_id", S), ("cli_version", S), ("strategy", S), ("branch", S), ("checkpoints_count", pa.int64()),
    ("files_touched", S), ("files_touched_count", pa.int64()), ("cp_input_tokens", pa.int64()),
    ("cp_output_tokens", pa.int64()), ("cp_cache_creation_tokens", pa.int64()), ("cp_cache_read_tokens", pa.int64()),
    ("cp_api_call_count", pa.int64()), ("total_additions", pa.int64()), ("total_deletions", pa.int64()),
    ("checkpoint_metadata_raw", S),
])
COMMITS = pa.schema([
    ("commit_sha", S), ("checkpoint_pk", S), ("repo_id", S), ("commit_index", pa.int64()), ("num_commits", pa.int64()),
    ("user_id", S), ("github_username", S), ("author_name", S), ("author_email", S), ("author_date", TS_US),
    ("commit_date", TS_US), ("commit_message", S), ("branch", S), ("is_agent_author", pa.bool_()),
    ("files_changed_count", pa.int64()), ("total_additions", pa.int64()), ("total_deletions", pa.int64()),
    ("files_changed", S), ("numstat", S), ("patch", S), ("agent_changes", S), ("file_attribution", S), ("status", S),
])
REPOSITORIES = pa.schema([
    ("repo_id", S), ("owner_id", S), ("name", S), ("url", S), ("is_fork", pa.bool_()), ("settings", S),
    ("num_checkpoints", pa.int64()), ("num_sessions", pa.int64()), ("num_commits", pa.int64()),
    ("num_contributors_in_dataset", pa.int64()), ("total_additions_in_dataset", pa.int64()),
    ("total_deletions_in_dataset", pa.int64()), ("total_repo_commits_ever", pa.int64()),
    ("total_repo_additions_ever", pa.int64()), ("total_repo_deletions_ever", pa.int64()),
    ("total_agent_commits_ever", pa.int64()), ("total_agent_additions_ever", pa.int64()),
    ("total_agent_deletions_ever", pa.int64()), ("last_scraped_at", TS_US), ("license_type", S),
    ("repo_github_metadata", S), ("repo_type_domain", S), ("repo_type_audience", S),
])
SESSION_LOGS = pa.schema([("session_id", S), ("transcript_path", S), ("context_md", S), ("session_metadata_raw", S)])
ROW_GROUP = 50_000


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _write(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    tmp = path.with_name(path.name + ".part")
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp)
    tmp.replace(path)


def assemble(out: Path, *, log=print) -> dict:
    """Write ``out/corpus/``; counts of what was written and left out."""
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    meta = {r["query_repo"]: r for r in load(out / "discover" / "repos.jsonl")}
    fetched = {r["repo"]: r for r in load(out / "fetch.jsonl") if r.get("status") == "ok"}
    # Pushback labels (`crawl.label`), each put on the row it was made for.
    labels = {(r["session_id"], r["turn_number"]): (r["label"], r["digest"])
              for r in load(out / "labels.jsonl") if r.get("label") and not r.get("error")}
    labelled: Counter = Counter()
    developer_turns: set[tuple[str, int]] = set()

    # Commits, and which checkpoint each links to: from `link` when it ran,
    # else from discovery's trailer commits (default branches only, no patch).
    commits_by_repo: dict[str, list[dict]] = {}
    for repo in fetched:
        linked = out / "link" / repo.replace("/", "__") / "commits.jsonl"
        if linked.exists():
            commits_by_repo[repo] = load(linked)
    discovered: dict[str, dict[str, dict]] = {}
    for c in load(out / "discover" / "commits.jsonl"):
        if c["repo"] in fetched and c["repo"] not in commits_by_repo:
            discovered.setdefault(c["repo"], {}).setdefault(c["sha"], {
                "commit_sha": c["sha"], "author_date": c["author_date"], "commit_date": c["committer_date"],
                "checkpoint_ids": c["checkpoint_ids"], "status": "discovered_only"})
    for repo, cs in discovered.items():
        commits_by_repo[repo] = list(cs.values())
    trailer_repos: dict[str, set[str]] = {}
    for repo, cs in commits_by_repo.items():
        for c in cs:
            for cid in c.get("checkpoint_ids") or []:
                trailer_repos.setdefault(cid, set()).add(repo)

    left_out: Counter = Counter()
    agents_left: Counter = Counter()
    # Which repository a session several hold is credited to (the module's
    # docstring, G-80). Metadata is keyed by the name search found a repository
    # under; a repository is fetched under its full name.
    named = {**{m["full_name"]: m for m in meta.values() if m.get("full_name")}, **meta}
    holders: dict[str, list[tuple[str, dict]]] = {}
    for repo in sorted(fetched):
        for s in load(out / "raw" / repo.replace("/", "__") / "sessions.jsonl"):
            holders.setdefault(s["session_id"], []).append((repo, s))

    def link(held: tuple[str, dict]) -> int:
        """0: the holder's commits carry its copy's latest checkpoint's trailer; 1: an earlier one's; 2: none."""
        repo, row = held
        if repo in trailer_repos.get(row.get("checkpoint_id"), ()):
            return 0
        return 1 if any(repo in trailer_repos.get(c, ()) for c in row.get("checkpoint_ids") or []) else 2

    owner_of: dict[str, tuple[str, dict]] = {}
    for sid, held in holders.items():
        owner_of[sid] = min(held, key=lambda h: (link(h), rank(h[0], named)))
        if len(held) > 1:
            left_out["the same session under a second repository"] += len(held) - 1

    sessions, logs, checkpoint_sessions = [], [], {}
    writer = pq.ParquetWriter(corpus / "conversations.parquet.part", CONVERSATIONS)
    batch: list[dict] = []
    try:
        for sid, (repo, s) in sorted(owner_of.items()):
            src = out / "raw" / repo.replace("/", "__") / "transcripts" / f"{sid}.jsonl"
            if not src.exists():
                left_out["transcript missing"] += 1
                continue
            entries = read_entries(src)
            if not is_claude_code(entries):
                left_out["not a Claude Code transcript (for later)"] += 1
                agents_left[str(s.get("agent"))] += 1
                continue
            pk = f"{repo}#{s['checkpoint_id']}"
            rows = claude_code_rows(sid, repo, pk, entries, strategy=s.get("strategy"))
            for r in rows:
                if r["turn_type"] == "user_prompt":
                    developer_turns.add((sid, r["turn_number"]))
                got = labels.get((sid, r["turn_number"])) if r["turn_type"] == "user_prompt" else None
                if got and got[1] == digest(r["content"]):
                    r["prompt_pushback"] = got[0]
                    labelled["put on its message"] += 1
                elif got:
                    labelled["not put: the message changed since it was labelled"] += 1
            if not any(r["turn_type"] == "user_prompt" for r in rows):
                left_out["no developer message"] += 1
                continue
            batch += rows
            if len(batch) >= ROW_GROUP:
                writer.write_table(pa.Table.from_pylist(batch, schema=CONVERSATIONS))
                batch = []
            _link(src, corpus / "transcripts" / f"{sid}.jsonl")
            # Its subagents' transcripts, where Claude Code 2.1 writes what they
            # did: subagents/<session>/<call that spawned it>/agent-<id>.jsonl.
            sub = out / "raw" / repo.replace("/", "__") / "subagents" / sid
            for f in sorted(sub.rglob("*")) if sub.is_dir() else []:
                if f.is_file():
                    _link(f, corpus / "subagents" / sid / f.relative_to(sub))
            usage = s.get("token_usage") or {}
            attr = s.get("initial_attribution") or {}
            metrics = s.get("session_metrics") or {}
            for cid in s.get("checkpoint_ids") or [s["checkpoint_id"]]:
                checkpoint_sessions.setdefault(f"{repo}#{cid}", []).append(sid)
            sessions.append({
                "session_id": sid, "repo_id": repo, "owner_id": None, "user_id": None,
                "checkpoint_ids": json.dumps([f"{repo}#{c}" for c in s.get("checkpoint_ids") or [s["checkpoint_id"]]]),
                "canonical_checkpoint_pk": pk, "agent": "Claude Code", "strategy": s.get("strategy"),
                "branch": s.get("branch"), "created_at": _time(s.get("created_at")), "cli_version": s.get("cli_version"),
                "files_touched": json.dumps(s.get("files_touched") or []),
                "files_touched_count": len(s.get("files_touched") or []), "checkpoints_count": s.get("checkpoints_count"),
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                "cache_creation_tokens": usage.get("cache_creation_tokens"),
                "cache_read_tokens": usage.get("cache_read_tokens"), "api_call_count": usage.get("api_call_count"),
                "agent_lines": attr.get("agent_lines"), "human_added": attr.get("human_added"),
                "human_modified": attr.get("human_modified"), "human_removed": attr.get("human_removed"),
                "total_committed": attr.get("total_committed"), "agent_percentage": attr.get("agent_percentage"),
                "attribution_calculated_at": _time(attr.get("calculated_at")),
                "transcript_identifier_at_start": None, "transcript_path": f"transcripts/{sid}.jsonl",
                "tool_call_count": float(sum(r["turn_type"] == "tool_use" for r in rows)),
                "unique_tools_count": float(len({r["tool_name"] for r in rows if r["turn_type"] == "tool_use"})),
                "research_count": None, "action_count": None, "first_write_position": None,
                "duration_seconds": (metrics.get("duration_ms") or 0) / 1000 or None,
                "turn_count": float(metrics["turn_count"]) if metrics.get("turn_count") is not None else None,
                "prompt_count": sum(r["turn_type"] == "user_prompt" for r in rows),
                "content_hash": s.get("transcript_sha256"), "user_persona": None, "session_success": None,
            })
            logs.append({"session_id": sid, "transcript_path": f"transcripts/{sid}.jsonl", "context_md": None,
                         "session_metadata_raw": json.dumps({k: v for k, v in s.items()})})
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=CONVERSATIONS))
    finally:
        writer.close()
    (corpus / "conversations.parquet.part").replace(corpus / "conversations.parquet")

    kept_repos = sorted({s["repo_id"] for s in sessions})
    # One row per commit, filed under its first trailer's checkpoint; every
    # checkpoint a commit names lists it among its commits.
    commit_rows, checkpoint_commits = [], {}
    for repo in kept_repos:
        cs = commits_by_repo.get(repo, [])
        for i, c in enumerate(sorted(cs, key=lambda c: c.get("commit_date") or "")):
            ids = c.get("checkpoint_ids") or []
            for cid in ids:
                checkpoint_commits.setdefault(f"{repo}#{cid}", []).append(c["commit_sha"])
            if ids:
                pk = f"{repo}#{ids[0]}"
                commit_rows.append({
                    "commit_sha": c["commit_sha"], "checkpoint_pk": pk, "repo_id": repo, "commit_index": i,
                    "num_commits": len(cs), "user_id": None, "github_username": None, "author_name": None,
                    "author_email": None, "author_date": _time(c.get("author_date")),
                    "commit_date": _time(c.get("commit_date")), "commit_message": c.get("commit_message"),
                    "branch": None, "is_agent_author": None, "files_changed_count": c.get("files_changed_count"),
                    "total_additions": c.get("total_additions"), "total_deletions": c.get("total_deletions"),
                    "files_changed": c.get("files_changed"), "numstat": c.get("numstat"), "patch": c.get("patch"),
                    "agent_changes": None, "file_attribution": None, "status": c.get("status"),
                })

    checkpoints = []
    for repo in kept_repos:
        for row in load(out / "raw" / repo.replace("/", "__") / "checkpoints.jsonl"):
            pk = f"{repo}#{row['checkpoint_id']}"
            if pk not in checkpoint_sessions:
                continue
            shas = sorted(set(checkpoint_commits.get(pk, [])))
            checkpoints.append({
                "checkpoint_pk": pk, "checkpoint_id": row["checkpoint_id"], "repo_id": repo,
                "session_pks": json.dumps(sorted(set(checkpoint_sessions[pk]))),
                "session_count": len(set(checkpoint_sessions[pk])), "commit_shas": json.dumps(shas),
                "commit_count": len(shas), "author_user_ids": None, "unique_author_count": None, "user_id": None,
                "cli_version": row.get("cli_version"), "strategy": None, "branch": row.get("branch"),
                "checkpoints_count": None, "files_touched": None, "files_touched_count": row.get("files_touched"),
                "cp_input_tokens": None, "cp_output_tokens": None, "cp_cache_creation_tokens": None,
                "cp_cache_read_tokens": None, "cp_api_call_count": None, "total_additions": None,
                "total_deletions": None, "checkpoint_metadata_raw": None,
            })

    repositories = []
    for repo in kept_repos:
        m = meta.get(repo) or next((v for v in meta.values() if v.get("full_name") == repo), {})
        repositories.append({
            "repo_id": repo, "owner_id": None, "name": repo.split("/", 1)[1], "url": f"https://github.com/{repo}",
            "is_fork": m.get("fork"), "settings": None,
            "num_checkpoints": sum(1 for c in checkpoints if c["repo_id"] == repo),
            "num_sessions": sum(1 for s in sessions if s["repo_id"] == repo),
            "num_commits": len(commits_by_repo.get(repo, [])), "num_contributors_in_dataset": None,
            "total_additions_in_dataset": None, "total_deletions_in_dataset": None, "total_repo_commits_ever": None,
            "total_repo_additions_ever": None, "total_repo_deletions_ever": None, "total_agent_commits_ever": None,
            "total_agent_additions_ever": None, "total_agent_deletions_ever": None,
            "last_scraped_at": _time((fetched.get(repo) or {}).get("fetched_at")) or datetime.now(timezone.utc),
            "license_type": m.get("license"),
            "repo_github_metadata": json.dumps({k: m.get(k) for k in ("language", "license", "stars", "created_at",
                                                                      "pushed_at", "default_branch", "fork", "archived")}),
            "repo_type_domain": None, "repo_type_audience": None,
        })

    _write(corpus / "sessions.parquet", sessions, SESSIONS)
    _write(corpus / "session_logs.parquet", logs, SESSION_LOGS)
    _write(corpus / "checkpoints.parquet", checkpoints, CHECKPOINTS)
    _write(corpus / "commits.parquet", commit_rows, COMMITS)
    _write(corpus / "repositories.parquet", repositories, REPOSITORIES)
    # A label whose turn holds no developer message now -- the corpus re-assembled
    # with turns moved, or the session left out -- is counted, not dropped unseen.
    unplaced = sum(1 for key in labels if key not in developer_turns)
    if unplaced:
        labelled["not put: no developer message at that turn"] = unplaced
    summary = {"sessions": len(sessions), "repositories": len(repositories), "checkpoints": len(checkpoints),
               "commit_rows": len(commit_rows), "left_out": dict(left_out), "agents_left_for_later": dict(agents_left),
               "labels": dict(labelled)}
    (corpus / "left_out.json").write_text(json.dumps(summary, indent=1) + "\n")
    log(json.dumps(summary))
    return summary
