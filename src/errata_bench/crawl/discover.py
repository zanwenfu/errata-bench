"""Find the repositories: GitHub's commit search for the ``Entire-Checkpoint`` trailer, sliced by date.

Code search finds few of them. It reads only default branches, knows 221
repositories with `.entire/settings.json`, and half the repositories with
sessions commit no settings file (the 09-29 survey, #16). Commit search finds
every repository whose default branch has a trailer commit.

It returns at most 1,000 results a query, so the date range is halved until a
slice holds no more than that, then read page by page. A commit pushed to
several unrelated repositories is found in each (one was in 694), so every
commit gets one *owner*: its holder that is not a fork, earliest created.

Written under ``<out>/discover/``:
- ``slices.jsonl``: one row per slice read, so a stopped run resumes;
- ``commits.jsonl``: one row per (repository, commit) with a trailer;
- ``repos.jsonl``: each repository's metadata;
- ``selected.jsonl``: every repository, whether it is collected, and why not.

Commit authors' names and addresses are not kept: the trailer, the dates and
the repository are all this needs.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..store.rows import append, load
from . import entire

QUERY = '"Entire-Checkpoint"'
CAP = 1000
PER_PAGE = 100
SEARCH_GAP_S = 2.5  # the search API allows 30 requests a minute
CORE_GAP_S = 0.8  # the core API allows 5,000 an hour
PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense", "MPL-2.0",
              "CC0-1.0", "Zlib", "BSL-1.0", "MIT-0", "Artistic-2.0", "UPL-1.0", "PostgreSQL", "BlueOak-1.0.0"}
COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-2.1", "LGPL-3.0", "EUPL-1.2", "MPL-2.0", "EPL-2.0"}
POLICIES = {"permissive": PERMISSIVE, "v1": PERMISSIVE | COPYLEFT}


class GhError(RuntimeError):
    pass


def gh_get(path: str, fields: dict[str, str] | None = None, *, retries: int = 6) -> dict:
    """One GET through the `gh` CLI (its own login), waiting out rate limits."""
    args = ["gh", "api", "-X", "GET", path]
    for k, v in (fields or {}).items():
        args += ["-f", f"{k}={v}"]
    for attempt in range(retries):
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if p.returncode == 0:
            return json.loads(p.stdout)
        err = (p.stderr or p.stdout)[-400:]
        if "404" in err and "Not Found" in err:
            raise GhError("404")
        # Only a limit or a server fault is waited out. Any other refusal (a
        # blocked repository, a query the API rejects) will not change on the
        # next try, and six waits would cost twenty minutes each time.
        if "rate limit" in err.lower() or "HTTP 429" in err or "HTTP 50" in err:
            time.sleep(min(60 * (attempt + 1), 300))
            continue
        raise GhError(err)
    raise GhError(f"gave up after {retries} tries: {path}")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Searcher:
    """Commit search, paced to the search API's limit."""

    def __init__(self, get: Callable[[str, dict], dict] = gh_get, gap_s: float = SEARCH_GAP_S):
        self.get, self.gap_s, self.last = get, gap_s, 0.0

    def page(self, start: datetime, end: datetime, page: int) -> dict:
        wait = self.gap_s - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        q = f"{QUERY} committer-date:{_iso(start)}..{_iso(end)}"
        return self.get("search/commits", {"q": q, "per_page": str(PER_PAGE), "page": str(page),
                                           "sort": "committer-date", "order": "asc"})


def _row(item: dict) -> dict | None:
    commit = item.get("commit") or {}
    ids = entire.trailer_ids(commit.get("message") or "")
    if not ids:
        return None  # "Entire-Checkpoint" in prose, not a trailer
    repo = item.get("repository") or {}
    return {"repo": repo.get("full_name"), "fork": repo.get("fork"), "private": repo.get("private"),
            "sha": item.get("sha"), "committer_date": (commit.get("committer") or {}).get("date"),
            "author_date": (commit.get("author") or {}).get("date"), "checkpoint_ids": ids,
            "parents": len(item.get("parents") or [])}


def read_slice(searcher: Searcher, start: datetime, end: datetime, out: Path, log=print) -> None:
    """Every trailer commit committed in [start, end], bisecting while a slice holds more than the cap."""
    known = {(r["start"], r["end"]): r for r in load(out / "slices.jsonl")}
    seen = known.get((_iso(start), _iso(end)))
    if seen and not seen.get("split"):
        return
    if seen:  # halved before: its halves follow from its bounds, no query needed
        total, first = seen["total_count"], None
    else:
        first = searcher.page(start, end, 1)
        total = int(first.get("total_count") or 0)
    if total > CAP and end - start > timedelta(seconds=1):
        if not seen:
            append(out / "slices.jsonl", {"start": _iso(start), "end": _iso(end), "total_count": total, "split": True})
        mid = (start + (end - start) / 2).replace(microsecond=0)
        read_slice(searcher, start, mid, out, log)
        read_slice(searcher, mid + timedelta(seconds=1), end, out, log)
        return
    items, pages, incomplete = list(first.get("items") or []), 1, bool(first.get("incomplete_results"))
    while len(items) < min(total, CAP) and pages < CAP // PER_PAGE:
        pages += 1
        more = searcher.page(start, end, pages)
        incomplete |= bool(more.get("incomplete_results"))
        got = more.get("items") or []
        if not got:
            break
        items += got
    rows = [r for r in (_row(i) for i in items) if r]
    for r in rows:
        append(out / "commits.jsonl", r)
    append(out / "slices.jsonl", {"start": _iso(start), "end": _iso(end), "total_count": total,
                                  "fetched": len(items), "with_trailer": len(rows), "pages": pages,
                                  "incomplete": incomplete, "capped": total > CAP})
    log(f"  {_iso(start)}..{_iso(end)}: {total} found, {len(items)} read, {len(rows)} with a trailer")


def discover(out: Path, start: datetime, end: datetime, *, searcher: Searcher | None = None, log=print) -> None:
    """Read [start, end) month by month into ``out/discover/``."""
    d = out / "discover"
    d.mkdir(parents=True, exist_ok=True)
    searcher = searcher or Searcher()
    t = start
    while t < end:
        nxt = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
        stop = min(nxt, end) - timedelta(seconds=1)
        log(f"{t:%Y-%m}")
        read_slice(searcher, t, stop, d, log)
        t = nxt


def fetch_meta(out: Path, *, get: Callable[[str, dict], dict] = gh_get, gap_s: float = CORE_GAP_S, log=print) -> None:
    """Each repository's metadata, once."""
    d = out / "discover"
    have = {r["query_repo"] for r in load(d / "repos.jsonl")}
    repos = sorted({r["repo"] for r in load(d / "commits.jsonl")} - have)
    log(f"{len(repos)} repositories to describe")
    for i, repo in enumerate(repos, 1):
        try:
            m = get(f"repos/{repo}", {})
            row = {"query_repo": repo, "full_name": m.get("full_name"), "private": m.get("private"),
                   "fork": m.get("fork"), "parent": (m.get("parent") or {}).get("full_name"),
                   "archived": m.get("archived"), "disabled": m.get("disabled"), "created_at": m.get("created_at"),
                   "pushed_at": m.get("pushed_at"), "default_branch": m.get("default_branch"),
                   "license": (m.get("license") or {}).get("spdx_id"), "language": m.get("language"),
                   "stars": m.get("stargazers_count"), "size_kb": m.get("size"), "missing": False}
        except GhError as e:
            row = {"query_repo": repo, "missing": str(e) == "404", "error": None if str(e) == "404" else str(e)[-200:]}
        append(d / "repos.jsonl", row)
        if i % 200 == 0:
            log(f"  {i}/{len(repos)}")
        time.sleep(gap_s)


def owners(commits: list[dict], meta: dict[str, dict]) -> dict[str, str]:
    """Each commit's owner among the repositories holding it: not a fork, then earliest created, then by name."""
    holders: dict[str, set[str]] = {}
    for c in commits:
        holders.setdefault(c["sha"], set()).add(c["repo"])

    def rank(repo: str) -> tuple:
        m = meta.get(repo) or {}
        return (bool(m.get("fork")), m.get("created_at") or "9999", repo)

    return {sha: min(repos, key=rank) for sha, repos in holders.items()}


def select(out: Path, since: str, policy: str = "v1") -> list[dict]:
    """Every repository that owns a trailer commit, whether it is collected, and why not."""
    d = out / "discover"
    commits = load(d / "commits.jsonl")
    meta = {r["query_repo"]: r for r in load(d / "repos.jsonl")}
    owner = owners(commits, meta)
    owned: dict[str, set[str]] = {}
    recent: dict[str, set[str]] = {}
    span: dict[str, list[str]] = {}
    for c in commits:
        if owner.get(c["sha"]) != c["repo"]:
            continue
        owned.setdefault(c["repo"], set()).add(c["sha"])
        s = span.setdefault(c["repo"], [c["committer_date"], c["committer_date"]])
        s[0], s[1] = min(s[0], c["committer_date"]), max(s[1], c["committer_date"])
        if entire.created(c["committer_date"]) >= entire.created(since):
            recent.setdefault(c["repo"], set()).add(c["sha"])
    allowed = POLICIES[policy]
    rows = []
    for repo in sorted(owned):
        m = meta.get(repo) or {}
        why = ("no metadata" if not m else "gone" if m.get("missing") else "private" if m.get("private")
               else "no commit since " + since if repo not in recent
               else "no licence" if m.get("license") in (None, "NOASSERTION")
               else f"licence {m.get('license')} outside the {policy} policy" if m.get("license") not in allowed
               else None)
        rows.append({"repo": m.get("full_name") or repo, "query_repo": repo, "selected": why is None, "why_not": why,
                     "license": m.get("license"), "language": m.get("language"), "stars": m.get("stars"),
                     "owned_commits": len(owned[repo]), "owned_since": len(recent.get(repo, ())),
                     "first_seen": span[repo][0], "last_seen": span[repo][1]})
    tmp = d / "selected.jsonl.part"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(d / "selected.jsonl")
    return rows
