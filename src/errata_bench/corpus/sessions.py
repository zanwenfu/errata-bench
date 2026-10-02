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
import os
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from ..project import ROOT

# Located, not counted. `parents[2]` was right while this file was
# errata_bench/corpus.py and pointed at src/data/swe-chat once it became
# errata_bench/corpus/sessions.py -- taking every corpus-reading stage
# with it.
#
# ERRATA_CORPUS names another corpus in SWE-chat's shape, such as the one the
# Entire collector assembles (`crawl.corpus`, #16). It is read once, here, and
# every reader takes CORPUS from this module, so one process reads one corpus.
# Unset, it is SWE-chat, as before.
CORPUS = (Path(os.environ["ERRATA_CORPUS"]).expanduser().resolve() if os.environ.get("ERRATA_CORPUS")
          else ROOT / "data" / "swe-chat")


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


#: How `crawl.shape.rewound` counts, stamped on a collected corpus's
#: `rewound.json`. 2 (10-02): a compaction's boundary is followed to the entry it
#: names, or to the entry before it when that is not in the file (491 of the
#: corpus's 1,624 boundaries name one that is not). Lists counted otherwise held
#: 62 sessions wrongly, and are refused.
REWOUND_RULES = 2


def edited_sessions() -> set[str]:
    """The sessions the collector lists as holding an abandoned branch (`crawl.shape.rewound`, G-95): no moment
    is drawn or read from them.

    None from a corpus the collector did not assemble (SWE-chat's has no
    list). A collected corpus (it has `left_out.json`) with no list, a list
    that cannot be read, or one counted under other rules is refused: each
    would leave out nothing, or the wrong sessions, and say nothing (review,
    10-02).
    """
    listed = CORPUS / "rewound.json"
    if not listed.exists():
        if (CORPUS / "left_out.json").exists():
            raise SystemExit(f"{CORPUS} was assembled before the sessions holding an abandoned branch were listed "
                             "(rewound.json): assemble it again")
        return set()
    try:
        data = json.loads(listed.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"{listed} cannot be read ({e}): assemble the corpus again") from e
    if not isinstance(data, dict) or data.get("rules") != REWOUND_RULES or not isinstance(data.get("sessions"), dict):
        raise SystemExit(f"{listed} was not counted under rules {REWOUND_RULES}: assemble the corpus again")
    return set(data["sessions"])


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



