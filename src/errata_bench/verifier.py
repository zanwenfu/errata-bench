"""Run a candidate spec in a container and report what actually happened.

Three runs per entry, and the control is not optional:

    control  parent source + parent's own test   -> must PASS
    fail     parent source + child's test        -> must FAIL
    pass     child source  + child's test        -> must PASS

The control exists because a broken harness and an unusable entry look
identical: both print FAIL. While proving this construction on Obmondo/gfetch,
running with ``--network=none`` made all three states fail -- Go could not
download its dependencies, so nothing compiled. Without the control that reads
as "this entry is not reproducible", which is wrong and silently loses a good
case. Dependencies are therefore warmed with the network ON, once, and the
graded runs happen with it OFF against that warm cache.
"""

from __future__ import annotations

import subprocess
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


# Language -> (image, dependency-cache path inside the container, warm command).
# The warm step runs with the network ON; graded runs mount the same cache with
# the network OFF.
TOOLCHAINS: dict[str, dict] = {
    "go": {
        "cache_dir": "/go/pkg/mod",
        "warm": ["go", "mod", "download"],
    },
    "node": {
        "cache_dir": "/root/.npm",
        "warm": ["npm", "install", "--no-audit", "--no-fund"],
    },
    "python": {
        "cache_dir": "/root/.cache/pip",
        "warm": ["pip", "install", "-e", "."],
    },
}


class Verifier:
    def __init__(self, *, timeout_s: int = 600, cache_root: Path | None = None):
        self.timeout_s = timeout_s
        self.cache_root = cache_root

    def _docker(
        self,
        *,
        tree: Path,
        image: str,
        command: list[str],
        cache_dir: str | None,
        cache_host: Path | None,
        network: bool,
    ) -> RunResult:
        import time

        argv = ["docker", "run", "--rm", "-v", f"{tree}:/w", "-w", "/w"]
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
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RunResult(Outcome.ERROR, None, "timed out", time.monotonic() - start)

        out = (proc.stdout or "") + (proc.stderr or "")
        outcome = Outcome.PASS if proc.returncode == 0 else Outcome.FAIL
        return RunResult(outcome, proc.returncode, out[-20_000:], time.monotonic() - start)

    def warm_cache(
        self, *, tree: Path, image: str, toolchain: str, cache_host: Path
    ) -> RunResult:
        """Fetch dependencies with the network ON, once, before any graded run."""
        tc = TOOLCHAINS.get(toolchain)
        if not tc:
            return RunResult(Outcome.ERROR, None, f"unknown toolchain {toolchain}", 0.0)
        return self._docker(
            tree=tree,
            image=image,
            command=tc["warm"],
            cache_dir=tc["cache_dir"],
            cache_host=cache_host,
            network=True,
        )

    def run_test(
        self,
        *,
        tree: Path,
        image: str,
        toolchain: str,
        test_command: list[str],
        cache_host: Path,
    ) -> RunResult:
        """A graded run: network OFF, against the warm cache."""
        tc = TOOLCHAINS.get(toolchain, {})
        return self._docker(
            tree=tree,
            image=image,
            command=test_command,
            cache_dir=tc.get("cache_dir"),
            cache_host=cache_host,
            network=False,
        )
