"""Reconstruct a repository at a commit inside a container, and run commands in it.

This is the environment half of the benchmark. A task puts a candidate model in
the repository as it stood when the developer was working, so the model can
actually look at files and run commands rather than reason about a transcript in
the abstract.

Preparation is explicit and read from the repository, never assumed. An earlier
version of this code hardcoded one install command per language and lost 11 of
30 entries to it: ``npm install`` against a pnpm workspace, ``pip install -e .``
against a project with no installable package. What a repository needs is
something the repository states, in a lockfile, a CI workflow or a Makefile.

Three behaviours here exist because getting them wrong produces silent wrong
answers rather than errors:

  * Everything in preparation runs chained in ONE container, which is then
    committed to an image. Measured: ``apt-get update`` in one container then
    ``apt-get install`` in the next fails with "Unable to locate package", and a
    pip-installed ``uv`` is gone from the following container.

  * The install also runs once per tree, because a mounted tree masks anything
    preparation wrote under ``/w``. ``node_modules`` created during preparation
    is simply absent when a different tree is mounted.

  * Dependencies are fetched with the network ON, once, before any graded run
    goes offline. A build that never happened prints FAIL exactly like a failing
    test.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"  # the container or setup broke; says nothing about the task


@dataclass
class RunResult:
    outcome: Outcome
    exit_code: int | None
    stdout: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


# Where each ecosystem keeps downloaded dependencies, mounted so a warm fetch
# survives into later offline runs. The install *command* is deliberately not
# here: it comes from the repository.
CACHE_DIRS: dict[str, str] = {
    "go": "/go/pkg/mod",
    "node": "/root/.cache",  # npm, pnpm and yarn stores all live under this
    "python": "/root/.cache",  # pip, uv
}


def split_workdir(command: list[str]) -> tuple[str | None, list[str]]:
    """Pull a leading ``cd <dir> &&`` out of a shell-wrapped command.

    Monorepos legitimately need ``sh -c 'cd api && go test ./...'``. Preparation
    has to happen in that same directory or the install runs somewhere with no
    manifest, which is how two Go entries were lost before this existed.
    """
    if len(command) >= 3 and command[0] in ("sh", "bash") and command[1] == "-c":
        script = command[2]
        parts = script.split("&&", 1)
        head = parts[0].strip()
        if len(parts) == 2 and head.startswith("cd "):
            return shlex.split(head[3:].strip())[0], command
    return None, command


class Sandbox:
    """Runs commands against a materialised repository tree."""

    def __init__(self, *, timeout_s: int = 600):
        self.timeout_s = timeout_s

    def _docker(
        self,
        *,
        tree: Path,
        image: str,
        command: list[str],
        cache_dir: str | None,
        cache_host: Path | None,
        network: bool,
        workdir: str | None = None,
        timeout_s: int | None = None,
    ) -> RunResult:
        work = "/w" if not workdir else f"/w/{workdir.strip('/')}"
        argv = ["docker", "run", "--rm", "-v", f"{tree}:/w", "-w", work]
        # Trees are `git archive` exports with no .git directory, so any build
        # backend deriving a version from git history aborts. setuptools-scm
        # fails an editable install outright without this.
        argv += ["-e", "SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0"]
        if cache_dir and cache_host:
            cache_host.mkdir(parents=True, exist_ok=True)
            argv += ["-v", f"{cache_host}:{cache_dir}"]
        if not network:
            argv += ["--network=none"]
        argv += [image] + command

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s or self.timeout_s
            )
        except subprocess.TimeoutExpired:
            return RunResult(Outcome.ERROR, None, "timed out", time.monotonic() - start)

        out = (proc.stdout or "") + (proc.stderr or "")
        outcome = Outcome.PASS if proc.returncode == 0 else Outcome.FAIL
        return RunResult(outcome, proc.returncode, out[-20_000:], time.monotonic() - start)

    def prepare(
        self,
        *,
        tree: Path,
        image: str,
        toolchain: str,
        install_command: list[str],
        setup_commands: list[list[str]],
        cache_host: Path,
        workdir: str | None,
        tag: str,
    ) -> tuple[RunResult, str | None]:
        """Install what the repository says it needs, then commit it to an image.

        Returns the result and the image tag to use afterwards (None on failure,
        so nothing dangling is left behind).
        """
        cache_dir = CACHE_DIRS.get(toolchain)
        commands = [c for c in [*setup_commands, install_command] if c]
        if not commands:
            return RunResult(Outcome.PASS, 0, "(nothing to prepare)", 0.0), image

        work = "/w" if not workdir else f"/w/{workdir.strip('/')}"
        script = " && ".join(shlex.join(c) for c in commands)

        argv = ["docker", "run", "-w", work, "-v", f"{tree}:/w"]
        argv += ["-e", "SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0"]
        if cache_dir:
            cache_host.mkdir(parents=True, exist_ok=True)
            argv += ["-v", f"{cache_host}:{cache_dir}"]
        argv += ["--name", tag, image, "sh", "-c", script]

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=max(self.timeout_s, 900)
            )
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", tag], capture_output=True)
            return RunResult(Outcome.ERROR, None, "prepare timed out", time.monotonic() - start), None

        out = (proc.stdout or "") + (proc.stderr or "")
        elapsed = time.monotonic() - start
        if proc.returncode != 0:
            subprocess.run(["docker", "rm", "-f", tag], capture_output=True)
            return RunResult(Outcome.FAIL, proc.returncode, out[-20_000:], elapsed), None

        commit = subprocess.run(
            ["docker", "commit", tag, tag], capture_output=True, text=True, timeout=300
        )
        subprocess.run(["docker", "rm", "-f", tag], capture_output=True)
        if commit.returncode != 0:
            return RunResult(
                Outcome.ERROR, commit.returncode, f"commit failed: {commit.stderr[:500]}", elapsed
            ), None
        return RunResult(Outcome.PASS, 0, out[-20_000:], elapsed), tag

    def install_in_tree(
        self,
        *,
        tree: Path,
        image: str,
        toolchain: str,
        install_command: list[str],
        cache_host: Path,
        workdir: str | None,
    ) -> RunResult:
        """Re-run the install against one tree, for artifacts that live inside it."""
        if not install_command:
            return RunResult(Outcome.PASS, 0, "(no install)", 0.0)
        return self._docker(
            tree=tree,
            image=image,
            command=install_command,
            cache_dir=CACHE_DIRS.get(toolchain),
            cache_host=cache_host,
            network=True,
            workdir=workdir,
            timeout_s=max(self.timeout_s, 900),
        )

    def run(
        self,
        *,
        tree: Path,
        image: str,
        toolchain: str,
        command: list[str],
        cache_host: Path,
        network: bool = False,
        workdir: str | None = None,
    ) -> RunResult:
        """Run one command against a prepared tree. Offline by default."""
        return self._docker(
            tree=tree,
            image=image,
            command=command,
            cache_dir=CACHE_DIRS.get(toolchain),
            cache_host=cache_host,
            network=network,
            workdir=workdir,
        )

    @staticmethod
    def discard_image(tag: str) -> None:
        """Remove a prepared image. Safe for a tag that never existed."""
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)
