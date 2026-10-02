"""Each task's container: the developer's working copy with its dependencies installed (v1 step 2).

Of 387 test, build and type-check commands candidates ran in D-40 to D-44, at
most 25 ran (`results/sandbox-checks.txt`): the containers held one language's
toolchain and nothing the project installs. The benchmark asks whether an answer
states as settled something it never checked, and the sandbox rarely let
anyone check. So each task's container is built the way SWE-bench and
Terminal-Bench build theirs:

  - one base image for every task, pinned by digest: Python 3.12, Go 1.26 and
    Node 22 from their official images, with bun, pnpm and yarn (by corepack),
    uv, ripgrep, jq and git -- the toolchains a developer's machine has, since
    several tasks mix languages (oddessentials: pnpm and uv; duckdb: a Python
    backend and Node frontends);
  - the frozen working copy (`release.freeze`) where the developer had it;
  - its dependencies installed from its lockfiles while the image is built,
    when the network is open; the network is closed when the agent works.

A lockfile's strict install is tried first. If it fails, an ordinary install is
tried and the lockfile restored, so the working copy is still the developer's;
which happened is written to /errata/install.log. Nothing here decides a
verdict: an image is built, and then checked by running the project's own
check offline (`check_script`), which must start -- whether it passes is the
task's business.
"""

from __future__ import annotations

import json
import re
import shlex
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# The official images, pinned by the digests pulled on the server on 09-27.
BASES = {
    "python": "python:3.12@sha256:4d1caded1f729ae443eb803f26ffde7b61e696aeaef62f099abb6dd6b14257c7",
    "node": "node:22@sha256:dd5847a04b0deee391fa145f1f4c6d214196668b6bcc7988ebed67249f226844",
    "golang": "golang:1.26@sha256:6c2a5538f964f1c82f97ad14988bf05de100d922d159d0e398b54c7b0ca0c6c9",
}
# The newest bun any task names; a task naming another installs its own.
BUN = "1.3.9"
UV = "0.8.22"
BASE_TAG = "errata-base:v1"

BASE_DOCKERFILE = f"""\
# errata-bench v1: the base image every task's image is built on.
FROM {BASES["golang"]} AS go
FROM {BASES["node"]} AS node
FROM {BASES["python"]}
COPY --from=go /usr/local/go /usr/local/go
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
ENV PATH=/usr/local/go/bin:/root/go/bin:/root/.bun/bin:$PATH \\
    GOTOOLCHAIN=local \\
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \\
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \\
    PUPPETEER_SKIP_DOWNLOAD=1
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \\
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \\
 && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \\
 && corepack enable \\
 && apt-get update && apt-get install -y --no-install-recommends ripgrep jq \\
 && rm -rf /var/lib/apt/lists/* \\
 && pip install --no-cache-dir uv=={UV} \\
 && npm install -g bun@{BUN} \\
 && git config --system --add safe.directory '*' \\
 && mkdir -p /errata
"""

# What a lockfile asks for, strictly and then leniently. The lenient install
# may rewrite the lockfile, which is put back from a copy taken before it
# (`install_line`), so the developer's own uncommitted edits to it survive.
INSTALLS = {
    "bun.lock": ("bun install --frozen-lockfile", "bun install"),
    "bun.lockb": ("bun install --frozen-lockfile", "bun install"),
    "pnpm-lock.yaml": ("pnpm install --frozen-lockfile", "pnpm install --no-frozen-lockfile"),
    "package-lock.json": ("npm ci", "npm install"),
    "yarn.lock": ("yarn install --frozen-lockfile", "yarn install"),
    "go.mod": ("go mod download", "go mod download"),
    # With the project's extras: Pavel401-BugViper-85 declares pytest only in
    # its "dev" extra, and the developer, who ran the tests, had it installed.
    "uv.lock": ("uv sync --frozen --all-extras", "uv sync --frozen"),
    "poetry.lock": ("pip install --no-cache-dir poetry && poetry config virtualenvs.in-project true "
                    "&& poetry install --no-root", "poetry lock && poetry install --no-root"),
    "requirements.txt": ("pip install --no-cache-dir -r requirements.txt",
                         "pip install --no-cache-dir -r requirements.txt"),
}
# JavaScript lockfiles, in the order one is chosen when a folder has several.
JS_LOCKS = ("bun.lock", "bun.lockb", "pnpm-lock.yaml", "yarn.lock", "package-lock.json")
# The project's own checks, in the order tried: the fastest that says the most.
CHECK_SCRIPTS = ("typecheck", "check-types", "type-check", "tsc", "test", "lint", "build")
# A check that needs the outside world is no test of the container, nor is npm's placeholder.
OUTSIDE = re.compile(r"doppler|--env-file|\.env\b|docker|playwright|cypress|deploy|wrangler|vercel|fly |no test specified")
# A package.json with no lockfile beside it -- the agent wrote it in the session
# and ran the install, whose effects are not replayed (dipasqualew-vibereq-200):
# installed with the manager it names, or npm, and recorded as unlocked.
UNLOCKED = {"bun": "bun install", "pnpm": "pnpm install", "yarn": "yarn install", "npm": "npm install"}
# How a check says the container lacks something, as `scripts/sandbox_checks.py` reads it.
ENVIRONMENT_FAILURE = re.compile(
    r"(?m)(?:^sh: \d+: \S+: not found|command not found|npm error request to|No module named|Cannot find module"
    r"|ERR_MODULE_NOT_FOUND|\[setup failed\]|missing go\.sum entry|no required module provides"
    r"|module lookup disabled|dial tcp|EAI_AGAIN|could not determine executable|ENOTFOUND)", re.I)


@dataclass
class Recipe:
    """What one task's image needs beyond the base: tools, installs, and the checks that show it works."""

    task_id: str
    workdir: str
    tools: list[str] = field(default_factory=list)
    installs: list[tuple[str, str, str, str]] = field(default_factory=list)  # (folder, strict, lenient, lockfile)
    checks: list[tuple[str, str]] = field(default_factory=list)          # (folder, command)
    notes: list[str] = field(default_factory=list)


def _runner(folder_files: set[str]) -> str | None:
    """How this folder's package.json scripts are run, by the lockfile beside it."""
    for lock, run in (("bun.lock", "bun run"), ("bun.lockb", "bun run"), ("pnpm-lock.yaml", "pnpm run"),
                      ("yarn.lock", "yarn run"), ("package-lock.json", "npm run")):
        if lock in folder_files:
            return run
    return None


def recipe(task: dict, files: list[str], package_jsons: dict[str, str],
           pyprojects: dict[str, str] | None = None) -> Recipe:
    """A task's recipe, from its task.json, its working copy's file list and its manifests.

    ``files`` are paths relative to the working copy; ``package_jsons`` maps a
    package.json's or pnpm-workspace.yaml's path to its text, and
    ``pyprojects`` a pyproject.toml's.
    Lockfiles under node_modules or deeper than three folders are not the
    project's own.
    """
    pyprojects = pyprojects or {}
    r = Recipe(task["task_id"], task["workdir"])
    by_folder: dict[str, set[str]] = {}
    for f in files:
        p = PurePosixPath(f)
        if "node_modules" in p.parts or len(p.parts) > 4:
            continue
        by_folder.setdefault(str(p.parent) if str(p.parent) != "." else "", set()).add(p.name)
    root_pkg = json.loads(package_jsons.get("package.json") or "{}") if package_jsons.get("package.json") else {}
    manager = str(root_pkg.get("packageManager") or "")
    if manager.startswith("bun@") and manager.split("@", 1)[1].split("+")[0] != BUN:
        r.tools.append(f"npm install -g {shlex.quote(manager.split('+')[0])}")
    if manager.startswith(("pnpm@", "yarn@")):
        r.tools.append(f"corepack install -g {shlex.quote(manager.split('+')[0])}")
    for folder in sorted(by_folder, key=lambda f: (f.count("/"), f)):
        names = by_folder[folder]
        # A workspace member's packages are installed from the root's lockfile.
        if (folder and any(lock in by_folder.get("", set()) for lock in ("bun.lock", "bun.lockb", "pnpm-lock.yaml"))
                and "package.json" in names and not names & {"bun.lock", "bun.lockb", "pnpm-lock.yaml",
                                                               "package-lock.json", "yarn.lock"}):
            continue
        # One JavaScript install per folder, two package managers writing one
        # node_modules would fight (Whiteknight07-AiTutor-34 has bun.lock and
        # package-lock.json side by side): the one package.json names, or else
        # bun, pnpm, yarn, npm in that order.
        js = [lock for lock in JS_LOCKS if lock in names]
        named = {"bun": ("bun.lock", "bun.lockb"), "pnpm": ("pnpm-lock.yaml",), "yarn": ("yarn.lock",),
                 "npm": ("package-lock.json",)}.get(manager.split("@")[0] if not folder else "", ())
        keep_js = next((lock for lock in js if lock in named), js[0] if js else None)
        for lock, (strict, lenient) in INSTALLS.items():
            if lock in names:
                if lock in JS_LOCKS and lock != keep_js:
                    continue
                # A lockfile with no package.json beside it has nothing to install
                # from: `npm ci` stops at once (entireio-cli-281's .opencode, 10-02).
                if lock in JS_LOCKS and "package.json" not in names:
                    r.notes.append(f"{folder or '.'}: {lock} with no package.json, not installed")
                    continue
                if lock == "requirements.txt" and names & {"uv.lock", "poetry.lock"}:
                    continue
                r.installs.append((folder, strict, lenient, lock))
        if "package.json" in names and not js:
            pm = manager.split("@")[0] if manager.split("@")[0] in UNLOCKED else "npm"
            r.installs.append((folder, UNLOCKED[pm], UNLOCKED[pm], "package.json"))
            r.notes.append(f"{folder or '.'}: package.json with no lockfile, installed unlocked with {pm}")
        if "pnpm-lock.yaml" in names and not manager.startswith("pnpm@"):
            # A pnpm-workspace.yaml that only holds settings, with no `packages`,
            # is pnpm 10's: pnpm 9 refuses it ("packages field missing or empty"),
            # as it did nrmeyers-agentalloy-19's and -20's frontend (10-02).
            workspace = package_jsons.get(f"{folder}/pnpm-workspace.yaml" if folder else "pnpm-workspace.yaml")
            settings_only = workspace is not None and not re.search(r"(?m)^packages\s*:", workspace)
            r.tools.append("corepack install -g pnpm@10" if settings_only else "corepack install -g pnpm@9")
        pkg_path = f"{folder}/package.json" if folder else "package.json"
        run = (_runner(names) or (_runner(by_folder.get("", set())) if pkg_path in package_jsons else None)
               or ("npm run" if "package.json" in names else None))
        if pkg_path in package_jsons and run:
            try:
                scripts = json.loads(package_jsons[pkg_path]).get("scripts") or {}
            except ValueError:
                scripts = {}
            for name in CHECK_SCRIPTS:
                body = str(scripts.get(name) or "")
                if body and not OUTSIDE.search(body):
                    r.checks.append((folder, f"{run} {name}"))
                    break
        if "go.mod" in names:
            r.checks.append((folder, "go build ./... && go vet ./..."))
        if names & {"uv.lock"}:
            # pytest only when the project names it (Pavel401-BugViper-85 does not,
            # and "No module named pytest" said nothing about the container).
            pyproject = pyprojects.get(f"{folder}/pyproject.toml" if folder else "pyproject.toml", "")
            r.checks.append((folder, "uv run --frozen --all-extras python -m pytest --collect-only -q"
                             if "pytest" in pyproject else "uv run --frozen --all-extras python -m compileall -q ."))
    r.tools = list(dict.fromkeys(r.tools))
    if not r.installs:
        r.notes.append("no lockfile: nothing to install")
    if not r.checks:
        r.notes.append("no check of the project's own to run")
    return r


def dockerfile(r: Recipe, base: str | None = BASE_TAG) -> str:
    """The task's Dockerfile: the base, the working copy where the developer had it, its installs.

    ``base`` is the base image's tag; None writes the base's own steps in
    full instead, so the file builds anywhere with nothing built first (a
    Harbor task's), and Docker's cache still builds those steps once for all.
    """
    where = shlex.quote(r.workdir)
    lines = ([BASE_DOCKERFILE.rstrip("\n"), f"# errata-bench v1: {r.task_id}"] if base is None
             else [f"# errata-bench v1: {r.task_id}", f"FROM {base}"])
    # The working copy was frozen on macOS, whose git records a case-insensitive
    # file system (core.ignorecase, core.precomposeunicode); in a Linux
    # container that is false, and git would treat it as true. Dropped, git
    # reads the file system it is on.
    lines += ["COPY workspace.tar.gz /tmp/workspace.tar.gz",
             f"RUN mkdir -p \"$(dirname {where})\" && tar -xzf /tmp/workspace.tar.gz -C /tmp "
             f"&& mv /tmp/workspace {where} && rm /tmp/workspace.tar.gz "
             f"&& cd {where} && (git config --unset-all core.ignorecase || true) "
             f"&& (git config --unset-all core.precomposeunicode || true) "
             f"&& git status --porcelain > /errata/status-before.txt"]
    lines += [f"RUN {tool}" for tool in r.tools]
    for folder, strict, lenient, lock in r.installs:
        lines.append("RUN " + install_line(r.workdir, folder, strict, lenient, lock))
    # What the installs added that git would list as untracked -- a lockfile
    # npm wrote where the developer had none, a node_modules/ the repository
    # does not ignore, a tool's cache -- is excluded from git's view in the
    # repository's own .git/info/exclude, and named in /errata/install-hidden.txt:
    # the files stay (the dependencies need them), and `git status` shows what
    # the developer's did.
    lines.append(f"RUN cd {where} && python3 -c {shlex.quote(HIDE_INSTALLED)} "
                 f"&& git status --porcelain > /errata/status-after.txt")
    lines.append(f"WORKDIR {docker_word(r.workdir)}")
    return "\n".join(lines) + "\n"


# Run in the working copy after its installs (`dockerfile`): each path git now
# lists as untracked that it did not list before them is added to
# .git/info/exclude, anchored at the root, and named in /errata/install-hidden.txt.
# One line: a Dockerfile ends an instruction at a newline, and a script over
# several lines broke the build (09-27: "unknown instruction: before").
HIDE_INSTALLED = (
    "import os, subprocess; "
    "before = set(open('/errata/status-before.txt').read().splitlines()); "
    "now = subprocess.run(['git', 'status', '--porcelain', '-z'], capture_output=True, text=True).stdout; "
    "added = [e[3:] for e in now.split(chr(0)) if e.startswith('?? ') and e not in before "
    "and '?? ' + chr(34) + e[3:] + chr(34) not in before]; "
    "os.makedirs('.git/info', exist_ok=True); "
    "open('.git/info/exclude', 'a').write(''.join('/' + p + chr(10) for p in added)); "
    "open('/errata/install-hidden.txt', 'w').write(''.join(p + chr(10) for p in added))"
)


def docker_word(text: str) -> str:
    """A path as one word of a Dockerfile instruction that reads quotes, as WORKDIR does.

    Written bare, Whiteknight07-AiTutor-34's folder ("... Stavan's MacBook
    Air/...") failed the build: "unexpected end of statement while looking for
    matching single-quote". Double-quoted, with a quote, a dollar and a
    backslash escaped, it is the path itself.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'



def install_line(workdir: str, folder: str, strict: str, lenient: str, lock: str, logdir: str = "/errata") -> str:
    """One install, as a shell command that never fails the build and says what it did.

    Strict first. If that fails, the lenient install, with the lockfile copied
    aside first and put back after: the lenient install may rewrite it, and the
    working copy's lockfile is the developer's, uncommitted edits included, so
    it is restored from the copy rather than from git. What happened goes to
    ``logdir``/install.log as ``strict``, ``lenient`` or ``failed``.
    """
    at = shlex.quote(f"{workdir}/{folder}" if folder else workdir)
    label = f"{folder or '.'} {lock}"
    first = "unlocked" if lock == "package.json" else "strict"
    log, out = shlex.quote(f"{logdir}/install.log"), shlex.quote(f"{logdir}/install-output.log")
    q = shlex.quote
    return (f"cd {at} && cp {q(lock)} /tmp/lock.saved 2>/dev/null; "
            f"if ( {strict} ) > /tmp/install.out 2>&1; then printf '{first} %s\\n' {q(label)} >> {log}; "
            f"elif ( {lenient} ) > /tmp/install.out 2>&1; then printf 'lenient %s\\n' {q(label)} >> {log}; "
            f"else printf 'failed %s\\n' {q(label)} >> {log}; fi; "
            f"if [ -f /tmp/lock.saved ]; then cp /tmp/lock.saved {q(lock)}; rm -f /tmp/lock.saved; fi; "
            f"tail -n 30 /tmp/install.out >> {out}; rm -f /tmp/install.out")


def check_script(r: Recipe) -> str:
    """A shell script that runs the task's checks offline and says, for each, whether it could start."""
    where = shlex.quote(r.workdir)
    out = ["set +e"]
    for i, (folder, command) in enumerate(r.checks):
        at = shlex.quote(f"{r.workdir}/{folder}" if folder else r.workdir)
        out.append(f"cd {at} && timeout 600 sh -c {shlex.quote(command)} > /tmp/check{i}.out 2>&1; rc=$?; "
                   f"printf '=== check %s exit %s: %s (%s)\\n' {i} \"$rc\" {shlex.quote(command)} "
                   f"{shlex.quote(folder or '.')}; tail -n 25 /tmp/check{i}.out")
    out.append(f"cd {where} && echo '=== status after checks' && git status --porcelain | head -20")
    return "\n".join(out) + "\n"


def environment_failure(output: str) -> str | None:
    """The first sign in a check's output that the container lacked something, or None.

    TypeScript's "error TS2307: Cannot find module '...' or its corresponding
    type declarations" is a type error the project has -- blittle-pressy-158's
    workspace packages are not built -- not a module the container lacks, and
    its lines are left out.
    """
    kept = "\n".join(line for line in (output or "").splitlines() if not re.search(r"error TS\d+:", line))
    m = ENVIRONMENT_FAILURE.search(kept)
    return m.group(0) if m else None


def workspace_contents(archive: Path) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """The working copy's files (paths under workspace/, outside .git); its JavaScript manifests' texts by path
    (package.json, and pnpm-workspace.yaml, which says which pnpm wrote it); and its pyproject.toml texts."""
    files, packages, pyprojects = [], {}, {}
    with tarfile.open(archive) as tar:
        for m in tar:
            parts = Path(m.name).parts
            if len(parts) < 2 or parts[1] == ".git" or not m.isfile():
                continue
            rel = "/".join(parts[1:])
            files.append(rel)
            if (parts[-1] in ("package.json", "pnpm-workspace.yaml", "pyproject.toml") and "node_modules" not in parts
                    and len(parts) <= 5):
                text = tar.extractfile(m).read().decode("utf-8", "replace")
                (pyprojects if parts[-1] == "pyproject.toml" else packages)[rel] = text
    return files, packages, pyprojects
