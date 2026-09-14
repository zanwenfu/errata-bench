"""Run a candidate spec in a container and report what actually happened.

Three runs per entry, and the control is not optional:

    control  parent source + parent's own test   -> must PASS
    fail     parent source + child's test        -> must FAIL
    pass     child source  + child's test        -> must PASS

The control exists because a broken harness and an unusable entry look
identical: both print FAIL. Proving this construction by hand on
Obmondo/gfetch, running with ``--network=none`` made all three states fail --
Go could not download dependencies, so nothing compiled. Without the control
that reads as "this entry is not reproducible", which is wrong and silently
loses a good case.

Preparation is therefore explicit and comes from the repository, not from
convention. Batch 1 lost 11 of 30 entries to preparation this module got wrong:

  * it ran the dependency install at the tree root, while the module actually
    lived in a subdirectory (``cd api && go test ...``);
  * it hardcoded one install command per language, which cannot express
    ``pnpm``, ``yarn``, ``uv run`` or ``corepack``;
  * it ignored ``setup_commands`` entirely -- the builder had already reported
    ``corepack enable`` and ``yarn circuits build`` and they were never run.

So the install command and the working directory now come from the spec, the
builder reads both out of the repo, and everything runs with the network on
before the graded runs go offline.
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
    ERROR = "error"  # container/setup broke; not a statement about the entry


@dataclass
class RunResult:
    outcome: Outcome
    exit_code: int | None
    stdout: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class Verdict:
    control: RunResult
    fail_run: RunResult
    pass_run: RunResult
    prepare: RunResult | None = None

    @property
    def reproducible(self) -> bool:
        """True only for the full red->green transition with a sound harness."""
        return (
            self.control.outcome is Outcome.PASS
            and self.fail_run.outcome is Outcome.FAIL
            and self.pass_run.outcome is Outcome.PASS
        )

    @property
    def diagnosis(self) -> str:
        if self.prepare is not None and self.prepare.outcome is not Outcome.PASS:
            return (
                "prepare-failed: dependency install did not succeed, so no "
                "conclusion can be drawn about this entry"
            )
        if self.control.outcome is not Outcome.PASS:
            return (
                "harness-broken: parent's own tests do not pass, so nothing can "
                "be concluded about this entry"
            )
        if self.fail_run.outcome is not Outcome.FAIL:
            return (
                "no-signal: the child's test already passes against the parent "
                "source, so there is no bug for an agent to fix"
            )
        if self.pass_run.outcome is not Outcome.PASS:
            return "unstable: the test does not pass even at the child commit"
        return "reproducible: red at parent, green at child"


# Where each ecosystem keeps downloaded dependencies. Mounted so the warm run's
# downloads survive into the offline graded runs. The install *command* is not
# here on purpose -- it comes from the repository, via the spec.
CACHE_DIRS: dict[str, str] = {
    "go": "/go/pkg/mod",
    "node": "/root/.cache",  # covers npm/pnpm/yarn store locations under one mount
    "python": "/root/.cache",  # pip, uv
}


def split_workdir(test_command: list[str]) -> tuple[str | None, list[str]]:
    """Pull a leading ``cd <dir> &&`` out of a shell-wrapped command.

    The builder legitimately emits ``sh -c 'cd api && go test ./...'`` for
    monorepos. Preparation has to happen in that same directory, or the install
    runs somewhere with no manifest -- which is exactly how two Go entries were
    lost in batch 1.
    """
    if len(test_command) >= 3 and test_command[0] in ("sh", "bash") and test_command[1] == "-c":
        script = test_command[2]
        parts = script.split("&&", 1)
        head = parts[0].strip()
        if len(parts) == 2 and head.startswith("cd "):
            return shlex.split(head[3:].strip())[0], test_command
    return None, test_command


class Verifier:
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
        if cache_dir and cache_host:
            cache_host.mkdir(parents=True, exist_ok=True)
            argv += ["-v", f"{cache_host}:{cache_dir}"]
        if not network:
            argv += ["--network=none"]
        argv += [image] + command

        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s or self.timeout_s,
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
    ) -> RunResult:
        """Everything the repo says it needs, with the network ON, once.

        setup_commands run first (``corepack enable``, a codegen step), then the
        install command the builder read out of the repo. Any failure here is
        reported as prepare-failed rather than being allowed to masquerade as a
        failing test.
        """
        cache_dir = CACHE_DIRS.get(toolchain)
        last = RunResult(Outcome.PASS, 0, "(nothing to prepare)", 0.0)
        for cmd in [*setup_commands, install_command]:
            if not cmd:
                continue
            last = self._docker(
                tree=tree,
                image=image,
                command=cmd,
                cache_dir=cache_dir,
                cache_host=cache_host,
                network=True,
                workdir=workdir,
                timeout_s=max(self.timeout_s, 900),
            )
            if last.outcome is not Outcome.PASS:
                return last
        return last

    def run_test(
        self,
        *,
        tree: Path,
        image: str,
        toolchain: str,
        test_command: list[str],
        cache_host: Path,
    ) -> RunResult:
        """A graded run: network OFF, against the prepared tree and warm cache."""
        return self._docker(
            tree=tree,
            image=image,
            command=test_command,
            cache_dir=CACHE_DIRS.get(toolchain),
            cache_host=cache_host,
            network=False,
        )
