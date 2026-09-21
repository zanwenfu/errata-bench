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

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Concurrency and size, chosen for a 24GB laptop with Docker Desktop holding 8GB
# and other work already running. Raise deliberately, not by default.
def max_containers() -> int:
    """How many containers may run at once, per process.

    Two is right for one run on this laptop: Docker Desktop holds 8 GB and each
    container takes 2. Three candidate models running as three processes would
    take six between them, so ERRATA_MAX_CONTAINERS lowers it to one each and
    the total stays where it was.
    """
    import os

    try:
        return max(1, int(os.environ.get("ERRATA_MAX_CONTAINERS") or MAX_CONTAINERS))
    except ValueError:
        return MAX_CONTAINERS


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
    """Remove containers this run, or a dead one, left behind.

    Worth calling at startup. An interrupted run leaves containers holding
    memory on a machine that has little to spare, and the developer should not
    have to find them by hand.

    It removes only containers belonging to this process or to a process that
    is no longer running. Three candidate models are run at once, one process
    each, and a sweep that took every `errata-` container would kill a peer's
    live container in the middle of its attempt -- which the stage would record
    as that candidate failing. The risk grew when grading moved to its own
    stage, because the closing sweep now fires as soon as the candidates
    finish rather than after the last judge call.
    """
    # Anchored. Docker's `name=` is a substring match, so `name=errata-`
    # also selects the developer's own `my-errata-cache` or `team-errata-db-1`
    # -- and `_abandoned` then answered True for them, because they are not in
    # our `errata-<pid>-<random>` form. Between them that is `docker rm -f` on
    # an unrelated container on someone's laptop.
    p = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{prefix}", "--format", "{{.ID}} {{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    mine = os.getpid()
    removed = 0
    for line in (p.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        container_id, name = parts[0], parts[1]
        # Belt and braces: the filter is anchored above, but this function is
        # the one that decides to destroy something.
        if not name.startswith(prefix) or not _abandoned(name, mine):
            continue
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=30)
        removed += 1
    return removed


def _abandoned(name: str, mine: int) -> bool:
    """Whether this container is ours, or belongs to a process that has gone.

    Names are `errata-<pid>-<random>`, and nothing else is touched. This used
    to answer True for any name not in that form, on the grounds that it must
    predate the pid -- which also made `my-errata-cache`, `team-errata-db-1`
    and a plain `errata-cache` read as abandoned runs of this benchmark and
    handed them to `docker rm -f`. The pre-pid form is extinct, and a
    container this cannot identify is better left alone than destroyed: the
    cost of being wrong one way is some memory, and the other way is
    somebody's data.
    """
    bits = name.split("-")
    if len(bits) < 3 or bits[0] != "errata" or not bits[1].isdigit():
        return False
    owner = int(bits[1])
    if owner == mine:
        return True
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # alive, owned by someone else
    return False
