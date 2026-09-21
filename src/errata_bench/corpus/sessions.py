"""Locate things in the SWE-chat corpus.

The corpus is six parquet tables and 5,850 raw transcripts. It stores diffs, not
repository file contents, so seeing a repository at a commit means cloning it --
see :mod:`workspace`.

This module is deliberately small: it says where the corpus is and resolves the
identifiers other modules join on. Selection of candidate moments lives in
:mod:`reader`, and time and repository joins live in :mod:`timeline`, because
both of those carry traps worth documenting where they are used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from ..project import ROOT

# Located, not counted. `parents[2]` was right while this file was
# errata_bench/corpus.py and pointed at src/data/swe-chat once it became
# errata_bench/corpus/sessions.py -- taking every corpus-reading stage
# with it.
CORPUS = ROOT / "data" / "swe-chat"


@dataclass
class Repo:
    """A repository as the corpus records it."""

    repo_id: str  # "owner/name"
    url: str
    license_type: str | None
    language: str | None

    COPYLEFT = {"AGPL-3.0", "GPL-3.0", "GPL-3.0-or-later"}

    @property
    def is_copyleft(self) -> bool:
        """Whether redistributing this repository's code carries obligations.

        This is recorded, not enforced at selection. A task stores a URL and a
        commit sha; whoever runs the benchmark clones and builds the environment
        themselves, which is ordinary use rather than distribution -- the same
        model SWE-bench uses. The obligation attaches only if prepared images
        are published, which is a downstream decision.

        Excluding copyleft at selection cost two of the nine viable cases,
        including the clearest example of the failure mode we most want to
        measure, for a restriction that does not apply to how tasks are built.
        """
        return self.license_type in self.COPYLEFT

    @property
    def license_known(self) -> bool:
        """An unknown licence is a genuine unknown, and worth flagging."""
        return self.license_type is not None


def load_repos() -> dict[str, Repo]:
    """Every repository in the corpus, keyed by ``owner/name``."""
    table = pq.read_table(
        CORPUS / "repositories.parquet",
        columns=["repo_id", "url", "license_type", "repo_github_metadata"],
    )
    out: dict[str, Repo] = {}
    for rid, url, lic, meta in zip(
        table.column("repo_id").to_pylist(),
        table.column("url").to_pylist(),
        table.column("license_type").to_pylist(),
        table.column("repo_github_metadata").to_pylist(),
    ):
        language = None
        if meta:
            try:
                language = json.loads(meta).get("language")
            except (ValueError, TypeError):
                pass
        out[rid] = Repo(repo_id=rid, url=url, license_type=lic, language=language)
    return out


def session_commits() -> dict[str, list[str]]:
    """Commit shas produced by each session.

    A session reaches its commits through checkpoints, and both sides of that
    link are JSON arrays rather than foreign keys, so it has to be unpacked
    rather than joined.
    """
    table = pq.read_table(
        CORPUS / "checkpoints.parquet", columns=["session_pks", "commit_shas"]
    )
    out: dict[str, list[str]] = {}
    for sessions_raw, commits_raw in zip(
        table.column("session_pks").to_pylist(),
        table.column("commit_shas").to_pylist(),
    ):
        if not sessions_raw or not commits_raw:
            continue
        try:
            sessions = json.loads(sessions_raw)
            commits = json.loads(commits_raw)
        except (ValueError, TypeError):
            continue
        for sid in sessions:
            out.setdefault(sid, []).extend(commits)
    return out



