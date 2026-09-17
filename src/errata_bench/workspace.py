"""Per-entry scratch directory: fetch a repo at a commit, build trees, clean up.

Checkouts live on disk, not in RAM. Measured repo sizes run from under 1 MB to
142 MB, so a RAM disk on a 24 GB machine would compete with the containers that
actually need the memory -- and a full RAM disk fails rather than spilling.

Nothing accumulates: each entry's directory is removed when that entry
finishes, however it finishes, so peak disk stays near
(concurrency x repo size) instead of the total across all entries.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path, timeout: int = 300) -> str:
    """Run one git command, turning every failure into a GitError.

    A timeout used to escape as TimeoutExpired, which callers do not catch
    because they are handling GitError. One slow fetch -- five minutes on a
    single commit -- killed a whole rebuild partway through, losing the model
    calls already spent on every task before it. A repository that will not
    fetch is an ordinary rejection, not a reason to abandon the run.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args[:2])}: timed out after {timeout}s") from None
    except OSError as e:
        raise GitError(f"git {' '.join(args[:2])}: {e}") from None
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()[:400]}")
    return proc.stdout


@dataclass
class Checkout:
    """A fetched repo plus the trees built from it."""

    root: Path  # the bare-ish repo we fetch into
    child_sha: str
    parent_sha: str

    def export_tree(self, sha: str, dest: Path) -> Path:
        """Materialise one commit's full tree at ``dest``."""
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["git", "archive", sha], cwd=self.root, stdout=subprocess.PIPE
        )
        tar = subprocess.run(
            ["tar", "-x", "-C", str(dest)], stdin=proc.stdout, capture_output=True
        )
        proc.wait()
        if proc.returncode != 0 or tar.returncode != 0:
            raise GitError(f"export {sha[:12]} failed: {tar.stderr.decode()[:300]}")
        return dest

    def file_at(self, sha: str, path: str) -> str:
        return _git("show", f"{sha}:{path}", cwd=self.root)

    def write_file_from(self, sha: str, path: str, dest_tree: Path) -> None:
        """Copy one file out of ``sha`` into an already-exported tree.

        This is the whole construction: the child's test, dropped onto the
        parent's source.
        """
        content = self.file_at(sha, path)
        target = dest_tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


@contextmanager
def workspace(entry_id: str, *, base: Path | None = None):
    """Scratch directory for one entry, removed on every exit path."""
    base = base or Path(tempfile.gettempdir()) / "errata-bench"
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix=f"{entry_id[:12]}-", dir=base))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sweep_orphans(base: Path | None = None) -> int:
    """Remove directories a crashed run left behind. Call at startup."""
    base = base or Path(tempfile.gettempdir()) / "errata-bench"
    if not base.exists():
        return 0
    n = 0
    for child in base.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            n += 1
    return n


def fetch(repo_url: str, child_sha: str, dest: Path) -> Checkout:
    """Fetch just enough history to have the commit and its parent.

    depth=2 gets the commit and its parent without the full history. A commit
    may be unreachable -- repos go private or are force-pushed -- which raises
    GitError; that is a fact about availability, not about the entry's quality.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=dest)
    _git("remote", "add", "origin", repo_url, cwd=dest)
    _git("fetch", "-q", "--depth=2", "origin", child_sha, cwd=dest)

    parents = _git("rev-list", "--parents", "-n", "1", child_sha, cwd=dest).split()
    if len(parents) < 2:
        raise GitError(f"{child_sha[:12]} has no parent (root commit?)")
    return Checkout(root=dest, child_sha=child_sha, parent_sha=parents[1])
