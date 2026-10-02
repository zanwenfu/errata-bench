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


# Why a fetch failed, in the remote's own words. A task lost because the code
# is gone is not the same as one lost to a flaky network, and reporting both as
# "could not build the tree" hid the difference: three rejected rows read like
# a hiccup worth retrying, and all three were permanent -- two repositories
# that had gone private or been deleted, and one whose entire pre-session
# history, all twenty-eight commits, had been removed from the remote by a
# force-push. Chasing them cost an hour that the message should have saved.
GONE = (
    "repository not found",          # deleted, renamed, or now private
    "not our ref",                   # the commit is reachable from no branch
    "could not read username",       # private: git is asking for credentials
    "permission denied",
    "access denied",
    "does not appear to be a git repository",
)


def is_permanent(error: object) -> bool:
    """Whether refetching this later could ever succeed."""
    return any(sign in str(error).lower() for sign in GONE)


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



def fetch(repo_url: str, child_sha: str, dest: Path) -> Checkout:
    """Fetch just enough history to have the commit and its parent.

    depth=2 gets the commit and its parent without the full history. A commit
    may be unreachable -- repos go private or are force-pushed -- which raises
    GitError; that is a fact about availability, not about the entry's quality.

    A repository's first commit has no parent, and is fetched all the same: a
    session in a young repository starts from it (G-86). Refusing it, for a
    construction no caller uses any more, refused such a task.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=dest)
    _git("remote", "add", "origin", repo_url, cwd=dest)
    _git("fetch", "-q", "--depth=2", "origin", child_sha, cwd=dest)

    parents = _git("rev-list", "--parents", "-n", "1", child_sha, cwd=dest).split()
    return Checkout(root=dest, child_sha=child_sha, parent_sha=parents[1] if len(parents) > 1 else "")


@dataclass
class History:
    """A repository's commits without their files: enough to say which came before a moment (G-86).

    `branch_on_remote` says whether the remote still has the branch the session
    started on; `missing` names the commits asked for that it would not serve.
    """

    root: Path
    branch_on_remote: bool
    missing: tuple[str, ...] = ()

    def last_before(self, ref: str, iso: str, skip: frozenset[str] = frozenset()) -> str | None:
        """The newest commit on `ref`'s own line that entered the history before `iso`, less `skip`.

        By commit date: a commit rebased or amended after the start was not in
        the tree the session began with. Along first parents only: walking
        every parent, a branch merged after the start handed over its own
        commits from before it, which were never on this line then (10-02
        review: main's tip on 07-04 was A, and the walk gave a feature commit
        of 07-03 merged on 07-05).
        """
        try:
            out = _git("rev-list", "--first-parent", f"--before={iso}", "--max-count=200", ref, cwd=self.root).split()
        except GitError:
            return None
        return next((sha for sha in out if sha not in skip), None)

    def made_before(self, sha: str, iso: str) -> bool:
        """Whether a commit this history holds entered it before `iso`."""
        try:
            return _git("rev-list", "--max-count=1", f"--before={iso}", sha, cwd=self.root).strip() == sha
        except GitError:
            return False

    def resolve(self, abbreviated: str) -> str | None:
        """The full name of a commit printed in short, if this history holds exactly one."""
        try:
            return _git("rev-parse", "--verify", "--quiet", f"{abbreviated}^{{commit}}", cwd=self.root).strip() or None
        except GitError:
            return None

    def parent(self, sha: str) -> str | None:
        """The first parent of a commit this history holds, or None for a first commit or one it lacks."""
        try:
            return _git("rev-parse", "--verify", "--quiet", f"{sha}^1^{{commit}}", cwd=self.root).strip() or None
        except GitError:
            return None


def history(repo_url: str, dest: Path, *, branch: str | None, shas: list[str], pulls: bool = False) -> History:
    """The remote's default branch, the session's branch and the given commits' ancestry, commits only.

    `--filter=tree:0` leaves out every tree and file, so a repository of 20,000
    commits arrives in seconds. With `pulls`, the heads of its pull requests too,
    which keep a branch's commits after the branch is deleted.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=dest)
    _git("remote", "add", "origin", repo_url, cwd=dest)
    _git("config", "remote.origin.promisor", "true", cwd=dest)
    _git("config", "remote.origin.partialclonefilter", "tree:0", cwd=dest)
    on_remote = bool(branch) and bool(_git("ls-remote", "--heads", "origin", f"refs/heads/{branch}",
                                          cwd=dest, timeout=120).strip())
    # A remote may have no default branch at all; asked for, "+HEAD:" fails as
    # if the network had, and the moment was retried for ever (10-02 review).
    has_head = bool(_git("ls-remote", "origin", "HEAD", cwd=dest, timeout=120).strip())
    specs = ["+HEAD:refs/remotes/origin/default"] if has_head else []
    if on_remote:
        specs.append(f"+refs/heads/{branch}:refs/remotes/origin/branch")
    if pulls:
        specs.append("+refs/pull/*/head:refs/remotes/origin/pull/*")
    if specs:
        _git("fetch", "-q", "--filter=tree:0", "origin", *specs, cwd=dest, timeout=900)
    missing = []
    for sha in dict.fromkeys(s for s in shas if s):
        try:
            _git("fetch", "-q", "--filter=tree:0", "origin", sha, cwd=dest, timeout=900)
        except GitError:
            missing.append(sha)
    return History(root=dest, branch_on_remote=on_remote, missing=tuple(missing))
