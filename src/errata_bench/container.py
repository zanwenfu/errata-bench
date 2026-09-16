"""Run a candidate's commands inside a container, within a fixed local budget.

Without one, a candidate runs commands directly on the host. That works for
reading files and grepping, and fails for the thing the benchmark is actually
about: most of these repositories need a toolchain the host does not have, so a
candidate that wants to run the tests simply cannot, and reports that it could
not verify. Some of those honest shortfalls are the harness's fault rather than
the model's.

The constraint that shapes this is the laptop it runs on. Docker Desktop here
has 8GB of the machine's 24GB, the developer already has containers running and
other work open, and a benchmark that makes their machine unusable is not one
they will run twice. So concurrency and memory are capped low and explicitly
rather than left to expand to whatever is available:

    2 containers at once, 2GB and 2 CPUs each

That is 4GB of Docker's 8GB at full tilt, which leaves room for what is already
running. The limits are arguments, so a bigger machine can raise them, but the
default is chosen for the machine in front of us rather than an ideal one.

Images are per-language and cached. Building one per task would cost minutes
each; the toolchains are shared, so the repository's language picks from a small
set. A language with no image runs on the host as before, which is worse but
honest, and the task records which happened.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Concurrency and size, chosen for a 24GB laptop with Docker Desktop holding 8GB
# and other work already running. Raise deliberately, not by default.
MAX_CONTAINERS = 2
MEMORY = "2g"
CPUS = "2"

# Public images with a language toolchain already installed. Pinned, because an
# image that drifts changes what a candidate can run and silently changes the
# benchmark.
#
# Chosen from what this machine already has wherever possible. A slim variant is
# a smaller image but a separate download, and pulling 400MB to save disk on a
# machine already holding the full tag is the wrong trade -- especially when
# Docker here is sitting on 100GB of images with 97% reclaimable.
IMAGES = {
    "Python": "python:3.12",
    "TypeScript": "node:22",
    "JavaScript": "node:22",
    "Go": "golang:1.26",
    # No Rust image is local, and rust:1.83-slim is a ~700MB download. It is
    # listed so `docker pull rust:1.83-slim` is all that is needed, but nothing
    # pulls it automatically: a benchmark run should not quietly consume a
    # developer's bandwidth and disk. Until it is present, Rust tasks fall back
    # to the host and record that they did.
    "Rust": "rust:1.83-slim",
}


def is_local(image: str) -> bool:
    """Whether the image is already on this machine, so using it costs nothing."""
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=30
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def image_for(language: str | None, *, only_local: bool = True) -> str | None:
    """The image for a repository's language, or None to fall back to the host.

    ``only_local`` keeps a benchmark run from downloading anything. A missing
    image means that task runs on the host, which is worse but visible, rather
    than the run pausing to fetch several hundred megabytes nobody asked for.
    """
    image = IMAGES.get(language or "")
    if image is None:
        return None
    if only_local and not is_local(image):
        return None
    return image


def available() -> bool:
    """Whether Docker is reachable at all."""
    try:
        return (
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=15,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def pull(image: str, *, timeout_s: int = 600) -> tuple[bool, str]:
    """Fetch an image if it is not already local. Slow once, instant afterwards."""
    have = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, timeout=30
    )
    if have.returncode == 0:
        return True, "already local"
    try:
        p = subprocess.run(
            ["docker", "pull", image], capture_output=True, text=True, timeout=timeout_s
        )
        return p.returncode == 0, (p.stderr or p.stdout)[-300:]
    except subprocess.SubprocessError as e:
        return False, str(e)


@dataclass
class Container:
    """A running container with the candidate's working copy mounted.

    Started once per attempt and removed afterwards. Commands run inside it, so
    a candidate that breaks the toolchain breaks only its own container, and the
    host keeps whatever it had installed.
    """

    name: str
    image: str
    tree: Path

    def start(self) -> tuple[bool, str]:
        # --network=none because the benchmark measures judgement, not package
        # registry availability -- and because a container with no network
        # cannot quietly install a different version of a dependency and change
        # the thing under test.
        p = subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "--name", self.name,
                "--memory", MEMORY,
                "--cpus", CPUS,
                # --cpus throttles CPU time but leaves nproc reporting every
                # core on the machine, so a build system asking "how parallel
                # can I be?" answers 14 and spawns 14 jobs into a 2-CPU slice.
                # These are the variables the common toolchains actually read.
                "--env", f"MAKEFLAGS=-j{CPUS}",
                "--env", f"CARGO_BUILD_JOBS={CPUS}",
                "--env", f"UV_CONCURRENT_BUILDS={CPUS}",
                "--env", "npm_config_jobs=" + CPUS,
                "--network", "none",
                "--workdir", "/work",
                "-v", f"{self.tree.resolve()}:/work",
                self.image,
                "sleep", "7200",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return p.returncode == 0, (p.stderr or "")[-300:]

    def run(self, command: str, timeout_s: int) -> tuple[int, str]:
        """Run one command inside the container."""
        try:
            p = subprocess.run(
                ["docker", "exec", self.name, "sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            # The command is still running inside the container; killing the
            # container is what actually stops it. Leaving it would hold memory
            # for the rest of the run.
            subprocess.run(["docker", "kill", self.name], capture_output=True, timeout=30)
            return 124, f"timed out after {timeout_s}s (container killed)"

    def stop(self) -> None:
        subprocess.run(["docker", "kill", self.name], capture_output=True, timeout=30)


def sweep(prefix: str = "errata-") -> int:
    """Remove containers a crashed run left behind.

    Worth calling at startup. An interrupted run leaves containers holding
    memory on a machine that has little to spare, and the developer should not
    have to find them by hand.
    """
    p = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name={prefix}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    ids = [i for i in (p.stdout or "").split() if i]
    for i in ids:
        subprocess.run(["docker", "rm", "-f", i], capture_output=True, timeout=30)
    return len(ids)
