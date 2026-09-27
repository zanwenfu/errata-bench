#!/usr/bin/env python3
"""Whether candidates could run their project's own checks in the sandbox. No model calls.

    scripts/sandbox_checks.py runs/d40-grok-4.6 runs/d44-grok-4.6 ...

A candidate is graded on whether it states as settled something it did not
establish, and running the project's tests, build or type-check is how an agent
establishes most things. The sandbox installs no dependencies and has no
network, so this counts how often such a command could run at all.

For every `run_command` call whose program (after any leading `cd <dir> &&` and
`NAME=value` assignments) is a test, build or type-check tool, the output is
put in one class:
  - out of time: the attempt's time was already spent, so nothing ran;
  - tool missing: the shell could not find the program (`bun: not found`);
  - package missing: the program ran but a dependency was not installed, or
    it tried to fetch one (`npx` asking the registry, `No module named`, a Go
    package `[setup failed]`);
  - network refused: the harness's own refusal of a network command;
  - timed out: the command outlived its own time limit;
  - path missing: a directory from the developer's machine (`can't cd to`);
  - ran: none of the above. Its exit code is shown, but a command piped
    through `tail` exits 0 whatever happened, so it is not used to classify.

Git is counted apart. The working copy is an export with no `.git`, so every
git command is counted as failing for want of a repository when git says so,
or when it prints its usage and exits 129 (`git diff` outside a repository).

The classes come from the command's name and the output's error text, not
from reading each call, so a few examples of each are printed for checking.
Answers collected before 25 September kept at most 4,000 characters of each
output, with the head and tail kept; the error lines searched for are short
and sit at one end.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

LEAD = re.compile(
    r"^\s*(?:cd\s+\S+\s*&&\s*)*(?:[A-Z_][A-Z0-9_]*=\S+\s+)*"
    r"(npm|npx|pnpm|yarn|bun|bunx|vitest|jest|tsc|\S*node_modules/\.bin/\S+|go|pytest|python3?|uv|make|astro)\b"
    r"(\s+\S+)?")

CLASSES = (
    ("out of time", re.compile(r"^refused: this attempt has run out of time")),
    ("network refused", re.compile(r"^refused: the network is unavailable")),
    ("path missing", re.compile(r"can't cd to")),
    ("timed out", re.compile(r"timed out after \d+s")),
    ("tool missing", re.compile(r"(?m)(?:^sh: \d+: \S+: not found|command not found)")),
    ("package missing", re.compile(
        r"npm error request to|No module named|Cannot find module|ERR_MODULE_NOT_FOUND|"
        r"\[setup failed\]|missing go\.sum entry|no required module provides|module lookup disabled|"
        r"dial tcp|EAI_AGAIN|could not determine executable", re.I)),
)


GIT = re.compile(r"^\s*(?:cd\s+\S+\s*&&\s*)*git\s+(\S+)")
HOME = re.compile(r"/(Users|home)/[^/\s'\"]+")


def no_repository(result: str) -> bool:
    """Whether a git command failed because the working copy is not a repository."""
    return "not a git repository" in result or result.startswith("exit 129")


def is_check(command: str) -> bool:
    """Whether a command runs the project's tests, build or type-check."""
    m = LEAD.match(command)
    if not m:
        return False
    tool, arg = m.group(1), (m.group(2) or "").strip()
    if tool == "go":
        return arg in ("test", "build", "vet", "run")
    if tool.startswith("python"):
        return "pytest" in command or "unittest" in command
    if tool == "uv":
        return arg == "run"
    if tool == "npm":
        return arg in ("test", "t", "run")
    return True


def classify(result: str) -> str:
    for name, pattern in CLASSES:
        if pattern.search(result):
            return name
    return "ran"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--examples", type=int, default=3, help="examples printed per class")
    args = ap.parse_args()

    calls = Counter()
    per_run = defaultdict(Counter)
    tools = Counter()
    examples = defaultdict(list)
    answers = tried = ran = 0
    tasks, tasks_tried, tasks_ran = set(), set(), set()
    git = Counter()
    git_answers = 0
    for run in args.runs:
        for line in open(run / "answers.jsonl"):
            a = json.loads(line)
            if a.get("error"):
                continue
            answers += 1
            tasks.add(a["task_id"])
            any_check = any_ran = any_git = False
            for c in a.get("tool_calls") or []:
                command = c.get("command") or ""
                if c.get("name") != "run_command":
                    continue
                result = str(c.get("result") or "")
                if GIT.match(command):
                    any_git = True
                    git["no repository" if no_repository(result) else
                        "path missing" if "can't cd to" in result else "other"] += 1
                    continue
                if not is_check(command):
                    continue
                kind = classify(result)
                calls[kind] += 1
                per_run[run.name][kind] += 1
                any_check = True
                if kind == "tool missing":
                    m = re.search(r"(?m)^sh: \d+: (\S+): not found", result)
                    tools[m.group(1) if m else "?"] += 1
                if kind == "ran":
                    any_ran = True
                    tasks_ran.add(a["task_id"])
                if len(examples[kind]) < args.examples:
                    first = (result.strip().splitlines() or [""])[0][:40]
                    # A developer's home directory names a person; the example needs only its shape.
                    shown = HOME.sub(r"/\1/...", command)[:70]
                    examples[kind].append(f"{a['task_id']}: {shown!r} -> {first!r}")
            if any_check:
                tried += 1
                tasks_tried.add(a["task_id"])
            ran += any_ran
            git_answers += any_git

    total = sum(calls.values())
    print(f"{answers} answers to {len(tasks)} tasks, from {len(args.runs)} run directories\n")
    print(f"test, build and type-check commands: {total}")
    for kind, _ in (*CLASSES, ("ran", None)):
        print(f"  {kind:16} {calls[kind]:4}  ({100 * calls[kind] / total:.0f}%)" if total else f"  {kind}")
    print(f"\nanswers that ran such a command: {tried} of {answers}; "
          f"of those, answers where at least one of them ran: {ran}")
    print(f"tasks where a candidate tried one: {len(tasks_tried)} of {len(tasks)}; "
          f"tasks where one ever ran: {len(tasks_ran)}")
    print(f"programs the shell could not find: {dict(tools.most_common())}")
    print(f"\ngit commands: {sum(git.values())}, in {git_answers} answers: {dict(git)}")
    print("\nper run directory:")
    for name, c in per_run.items():
        print(f"  {name:28} {dict(c)}")
    print("\nexamples:")
    for kind, lines in examples.items():
        print(f"  {kind}:")
        for x in lines:
            print(f"    {x}")


if __name__ == "__main__":
    main()
