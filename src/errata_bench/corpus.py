"""Read candidate entries out of the SWE-chat corpus.

The corpus stores diffs only -- no repository file contents. Everything here is
local parquet reading; seeing a repo means cloning it (see workspace.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import pyarrow.parquet as pq

CORPUS = Path(__file__).resolve().parents[2] / "data" / "swe-chat"

# Paths that look like tests. Deliberately broad: this only decides what the
# builder agent looks at, never whether an entry is usable. The gfetch run
# showed the real question ("do these assertions exercise the change?") is only
# answerable by running the tests, so nothing here is treated as a verdict.
TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__)/"
    r"|_test\.(go|py|rb|ex|exs)$"
    r"|\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$"
    r"|(^|/)test_[^/]*\.py$"
    r"|Test\.(java|kt|cs)$"
    r"|_spec\.rb$"
    r"|(^|/)conftest\.py$",
    re.I,
)


@dataclass
class Entry:
    """One commit from the corpus, before anyone has looked at the repo."""

    commit_sha: str
    repo_id: str
    repo_url: str
    language: str | None
    license_type: str | None
    commit_message: str
    files_changed: list[tuple[str, str]]  # (git status letter, path)
    patch: str

    @property
    def test_files(self) -> list[tuple[str, str]]:
        return [(s, p) for s, p in self.files_changed if TEST_PATH.search(p)]

    @property
    def source_files(self) -> list[tuple[str, str]]:
        return [(s, p) for s, p in self.files_changed if not TEST_PATH.search(p)]

    def to_json(self) -> dict:
        d = asdict(self)
        d["patch"] = self.patch[:200_000]  # keep prompts bounded
        return d


def _parse_files_changed(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    out = []
    for line in raw.split("\n"):
        if "\t" not in line:
            continue
        parts = line.split("\t")
        out.append((parts[0].strip(), parts[-1].strip()))
    return out


def load_entries(
    *,
    require_test_change: bool = True,
    max_files: int | None = None,
    languages: set[str] | None = None,
    limit: int | None = None,
    per_repo_cap: int | None = None,
) -> list[Entry]:
    """Load candidate commits.

    Only ``status == 'ok'`` rows carry a patch -- 5,205 of 14,459 rows are
    ``commit_not_found`` with empty patches and nothing to clone.

    ``per_repo_cap`` matters because the corpus is lopsided: five repos supply
    41% of usable commits, so an uncapped sample would mostly measure one Go CLI.
    """
    repos = pq.read_table(
        CORPUS / "repositories.parquet",
        columns=["repo_id", "url", "license_type", "repo_github_metadata"],
    )
    meta: dict[str, tuple[str, str | None, str | None]] = {}
    for i in range(repos.num_rows):
        rid = repos.column("repo_id")[i].as_py()
        gh = repos.column("repo_github_metadata")[i].as_py()
        lang = None
        if gh:
            try:
                lang = json.loads(gh).get("language")
            except (ValueError, TypeError):
                pass
        meta[rid] = (
            repos.column("url")[i].as_py(),
            repos.column("license_type")[i].as_py(),
            lang,
        )

    commits = pq.read_table(
        CORPUS / "commits.parquet",
        columns=[
            "commit_sha",
            "repo_id",
            "status",
            "commit_message",
            "files_changed",
            "patch",
        ],
    )

    entries: list[Entry] = []
    seen_per_repo: dict[str, int] = {}
    seen_sha: set[str] = set()

    for i in range(commits.num_rows):
        if commits.column("status")[i].as_py() != "ok":
            continue
        sha = commits.column("commit_sha")[i].as_py()
        rid = commits.column("repo_id")[i].as_py()
        if not sha or not rid or rid not in meta:
            continue  # some commit_sha values are null

        # One commit can appear on several rows: a session spans checkpoint
        # boundaries, and commits.parquet carries a row per (commit, checkpoint).
        # Without this, the same commit is triaged repeatedly and any per-repo
        # cap counts one commit as several.
        if sha in seen_sha:
            continue
        seen_sha.add(sha)

        files = _parse_files_changed(commits.column("files_changed")[i].as_py())
        if not files:
            continue
        if max_files is not None and len(files) > max_files:
            continue

        url, lic, lang = meta[rid]
        if languages and lang not in languages:
            continue

        entry = Entry(
            commit_sha=sha,
            repo_id=rid,
            repo_url=url,
            language=lang,
            license_type=lic,
            commit_message=commits.column("commit_message")[i].as_py() or "",
            files_changed=files,
            patch=commits.column("patch")[i].as_py() or "",
        )

        if require_test_change and not entry.test_files:
            continue
        if per_repo_cap is not None:
            n = seen_per_repo.get(rid, 0)
            if n >= per_repo_cap:
                continue
            seen_per_repo[rid] = n + 1

        entries.append(entry)
        if limit is not None and len(entries) >= limit:
            break

    return entries
