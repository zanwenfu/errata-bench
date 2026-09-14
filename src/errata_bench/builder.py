"""The builder: one agent per entry, reading a real repo to produce a Spec.

It gets the commit's diff and a checkout it can explore with ordinary file
tools, the way a developer would. What it must not do is invent a value it
could not find: a guessed test command makes the verifier report FAIL, and a
FAIL from a wrong command is indistinguishable from a genuinely unusable entry.
So every load-bearing field carries a citation, and an uncited field is treated
as a guess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents import Agent, Runner, function_tool
from agents.run_context import RunContextWrapper
from pydantic import BaseModel, Field

from .corpus import Entry
from .spec import Spec, parse_citation

MODEL = "gpt-6-astra"


class SpecCitations(BaseModel):
    """Where each load-bearing value was read from. Empty string = not found."""

    test_command: str = Field(
        description="file path + line/key you read the test command from, or '' if none exists"
    )
    image: str = Field(
        description="file path + line/key stating the language version, or ''"
    )
    toolchain: str = Field(
        description="file identifying the ecosystem (go.mod, package.json, pyproject.toml), or ''"
    )


class SpecOutput(BaseModel):
    """What the builder must produce. Structured output, enforced by the SDK."""

    reproducible: bool = Field(
        description=(
            "True only if you found, by reading this repository, everything needed "
            "to run its tests: which test, the exact command, and the image and "
            "toolchain. False if anything had to be guessed."
        )
    )
    reasoning: str = Field(
        description="Two or three sentences: what the commit changes, which test covers it, how you determined the command."
    )
    test_files: list[str] = Field(
        default_factory=list,
        description="Repo-relative path(s) of the test file(s) covering this change.",
    )
    test_command: list[str] = Field(
        default_factory=list,
        description="argv to run in the container, e.g. ['go','test','./pkg/gsync/']. Scope to the relevant package, not the whole suite.",
    )
    image: str = Field(
        default="", description="Docker image at the version the repo declares, e.g. 'golang:1.25'."
    )
    toolchain: str = Field(
        default="", description="One of: go, node, python, or '' if none fits."
    )
    setup_commands: list[list[str]] = Field(
        default_factory=list,
        description="Commands needed before tests beyond dependency install. Usually empty.",
    )
    citations: SpecCitations
    blocked_reason: str = Field(
        default="",
        description="If reproducible is false, the specific thing you could not determine. Empty otherwise.",
    )


INSTRUCTIONS = """\
You are examining one commit from a real repository to decide whether it can \
become a benchmark task for coding agents.

The construction is:

    parent source + the child commit's test  ->  the test must FAIL
    child source  + the child commit's test  ->  the test must PASS

An agent is then given the parent's code plus that test, and scored on whether \
it makes the test pass. Your job is to determine how to run that test, by \
reading the repository.

You have a checkout of the repository at the child commit. Explore it as a \
developer would: look at the layout, find the build files, read the CI \
configuration, open the test file. Take as long as you need.

Determine:
  1. Which test file covers the behaviour this commit changed.
  2. The exact command that runs it -- scoped to the relevant package or file, \
not the entire suite.
  3. The Docker image, using the language version the repository declares.

CRITICAL RULE: report only what you actually read in this repository. Do not \
guess a test command because it is conventional for the language. If the repo \
does not state how it runs tests, say so and set reproducible to false -- that \
is a useful, honest answer. A guessed command produces a failure that looks \
exactly like an unusable entry, which silently destroys a good benchmark case.

For every field you report, cite the file and the line or key you read it from. \
Prefer CI workflow files and Makefiles: those state how the project actually \
tests itself. When several sources agree, cite the most specific.

Also set reproducible to false when the tests plainly need something a sealed \
container cannot provide -- a live database, network access, credentials, a \
browser -- and name that in blocked_reason.
"""


def _safe_join(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError("path escapes the checkout")
    return p


@function_tool
def list_dir(ctx: RunContextWrapper, path: str = ".") -> str:
    """List files and directories at a path inside the repository checkout."""
    root: Path = ctx.context["tree"]
    try:
        target = _safe_join(root, path)
        if not target.is_dir():
            return f"not a directory: {path}"
        rows = []
        for child in sorted(target.iterdir())[:200]:
            if child.name == ".git":
                continue
            kind = "dir " if child.is_dir() else "file"
            size = "" if child.is_dir() else f"  {child.stat().st_size}b"
            rows.append(f"  {kind} {child.name}{size}")
        return f"{path}:\n" + "\n".join(rows) if rows else f"{path}: (empty)"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def read_file(ctx: RunContextWrapper, path: str, max_bytes: int = 40_000) -> str:
    """Read a file from the repository checkout."""
    root: Path = ctx.context["tree"]
    try:
        target = _safe_join(root, path)
        if not target.is_file():
            return f"not a file: {path}"
        text = target.read_text(errors="replace")[:max_bytes]
        return f"--- {path} ---\n{text}"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def find_files(ctx: RunContextWrapper, pattern: str, limit: int = 60) -> str:
    """Find files by glob pattern, e.g. '**/*_test.go' or '**/package.json'."""
    root: Path = ctx.context["tree"]
    try:
        hits = [
            str(p.relative_to(root))
            for p in sorted(root.glob(pattern))
            if p.is_file() and ".git/" not in str(p)
        ][:limit]
        return "\n".join(hits) if hits else f"no files match {pattern}"
    except (OSError, ValueError) as e:
        return f"error: {e}"


@function_tool
def git_log(ctx: RunContextWrapper, path: str = "", count: int = 5) -> str:
    """Recent commit subjects, optionally for one path. Read-only history."""
    root: Path = ctx.context["repo"]
    argv = ["git", "log", f"-{min(count, 20)}", "--format=%h %s"]
    if path:
        argv += ["--", path]
    try:
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=30)
        return proc.stdout or proc.stderr or "(no output)"
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {e}"


def build_agent() -> Agent:
    return Agent(
        name="builder",
        instructions=INSTRUCTIONS,
        model=MODEL,
        tools=[list_dir, read_file, find_files, git_log],
        output_type=SpecOutput,
    )


def _prompt(entry: Entry, parent_sha: str) -> str:
    files = "\n".join(f"  {status}  {path}" for status, path in entry.files_changed)
    return f"""\
Repository: {entry.repo_id}   ({entry.language or 'language unknown'})
Child commit:  {entry.commit_sha}
Parent commit: {parent_sha}

Commit message:
{entry.commit_message.strip()[:2000]}

Files changed:
{files}

Diff:
{entry.patch[:60_000]}

The checkout in your tools is at the CHILD commit. Explore it, then report the
spec. Remember: cite what you read; never guess a command.
"""


async def run_builder(
    entry: Entry, *, tree: Path, repo: Path, parent_sha: str, max_turns: int = 40
) -> Spec:
    """Run the builder agent over one entry. Returns its predicted Spec."""
    result = await Runner.run(
        build_agent(),
        _prompt(entry, parent_sha=parent_sha),
        context={"tree": tree, "repo": repo},
        max_turns=max_turns,
    )

    out: SpecOutput = result.final_output
    cites = {}
    for name, value in out.citations.model_dump().items():
        parsed = parse_citation(value)
        if parsed is not None:
            cites[name] = parsed
    return Spec(
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        child_sha=entry.commit_sha,
        parent_sha=parent_sha,
        reproducible=out.reproducible,
        reasoning=out.reasoning,
        test_files=out.test_files,
        test_command=out.test_command,
        image=out.image,
        toolchain=out.toolchain,
        setup_commands=out.setup_commands,
        citations=cites,
        blocked_reason=out.blocked_reason,
    )
