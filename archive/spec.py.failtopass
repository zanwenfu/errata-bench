"""What the builder produces: an executable description of one benchmark task.

A spec is a *prediction*, not a verdict. The verifier runs it and decides. The
citation fields exist so a wrong spec can be diagnosed: a spec that merely says
``go test ./...`` is unfalsifiable, while one that says it came from
``.github/workflows/test.yml:24`` can be checked against the repo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class Citation:
    """Where a value came from. No citation means the builder invented it."""

    path: str
    detail: str  # line number, key, or quoted fragment

    def __str__(self) -> str:
        return f"{self.path} ({self.detail})"

    def resolves_in(self, tree: "Path") -> bool:
        """Does the cited file actually exist in the checkout?

        Verifying citations is the point of having them, so this must not be
        fooled by formatting. The first attempt split on whitespace and tested
        paths like 'go.mod:3' and 'AGENTS.md,' for existence -- every real
        citation read as missing, which would have looked like the builder
        fabricating sources.
        """
        return bool(self.path) and (tree / self.path).is_file()


_PATH_RE = re.compile(r"[A-Za-z0-9_.\-/]+\.[A-Za-z0-9_\-]+|[A-Za-z0-9_.\-/]*Makefile")


def parse_citation(raw: str) -> "Citation | None":
    """Pull a usable file path out of whatever prose the model returned.

    Citations arrive in many shapes -- 'go.mod:3', 'AGENTS.md, Build & Test
    section', '.github/workflows/test.yml, jobs.test.steps[...]'. Take the first
    token that looks like a file path, strip any ':line' suffix and trailing
    punctuation, and keep the full string as the detail.
    """
    if not raw or not raw.strip():
        return None
    m = _PATH_RE.search(raw)
    if not m:
        return None
    path = m.group(0).rstrip(".,;:")
    return Citation(path=path, detail=raw.strip())


@dataclass
class Spec:
    # identity
    repo_id: str
    repo_url: str
    child_sha: str
    parent_sha: str

    # the prediction
    reproducible: bool
    reasoning: str

    # how to run it -- required when reproducible is True
    test_files: list[str] = field(default_factory=list)
    test_command: list[str] = field(default_factory=list)
    image: str = ""
    toolchain: str = ""  # key into verifier.CACHE_DIRS
    setup_commands: list[list[str]] = field(default_factory=list)
    # How this project installs its own dependencies, read from the repo. The
    # base image carries only a bare runtime, so without this pytest/jest are
    # simply absent -- which is how 11 of 30 entries were lost in batch 1.
    install_command: list[str] = field(default_factory=list)
    # Repo-relative directory the project lives in; "" means the repo root.
    # Preparation must happen here, not at the tree root, for monorepos.
    work_dir: str = ""

    # evidence
    citations: dict[str, Citation] = field(default_factory=dict)

    # why not, when reproducible is False
    blocked_reason: str = ""

    def missing_fields(self) -> list[str]:
        """Fields a runnable spec needs but this one lacks."""
        if not self.reproducible:
            return []
        gaps = []
        for name in ("test_files", "test_command", "image", "toolchain", "install_command"):
            if not getattr(self, name):
                gaps.append(name)
        return gaps

    def uncited_fields(self) -> list[str]:
        """Claims with no stated source. These are guesses, and guesses are
        exactly what we refuse: a wrong guess makes the verifier report FAIL,
        which is indistinguishable from a genuinely unusable entry."""
        if not self.reproducible:
            return []
        return [
            name
            for name in ("test_command", "install_command", "image", "toolchain")
            if getattr(self, name) and name not in self.citations
        ]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["citations"] = {k: str(v) for k, v in self.citations.items()}
        return d
