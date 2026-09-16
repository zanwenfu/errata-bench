"""Verify a defect is actually present in the tree a candidate will be given.

A task is only worth running if the thing it measures is really there. That
sounds obvious, and it was still violated twice: ``runs/run_candidates.py`` took
the *last* commit a session produced, which for moltis is the turn-500 fix
itself, so candidates were handed a tree with the defect already repaired and
asked whether they would catch it.

The subtler failure is that no commit need contain the defect at all. In
blittle/pressy the broken action name ``rossjrw/pr-preview-deploy-action``
appears in the agent's own reads at turns 18, 46 and 49, and in no commit in the
repository -- it lived in the developer's uncommitted working tree. A task built
on any sha there measures nothing, because the file the candidate opens is
already correct.

So presence is checked against the materialised tree, and nothing else. An
earlier probe compared "token in the commit" against "token in the agent's turns"
and called the disagreement divergence; two of its five rows were artifacts.
Lightprotocol "diverged" because the agent legitimately *added* the token during
the session, and moltis because the scan window missed a token that is in the
commit. Neither said anything about the tree. This module therefore asks one
question -- is it in the tree the candidate gets -- and infers nothing.

Not every defect has a file signature. FSM1/cipher-box's is behavioural: the
agent handed a verification checklist back to the user instead of driving a
browser. There is no token to find, so it cannot pass a presence gate, and it is
declared unprobeable rather than quietly passed or quietly failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Presence:
    """Whether a task's defect is in the tree, and how that was established."""

    task_id: str
    present: bool
    probeable: bool
    detail: str

    @property
    def usable(self) -> bool:
        """Whether this task's setup was actually established to be sound.

        Two shapes qualify, and they mean different things by ``present``. For a
        defect the candidate must *notice*, it has to be in the tree, and
        ``present`` records that it was found. For a defect the candidate must
        avoid *producing*, there is nothing to find and a clean tree is the
        correct setup; ``introduced`` declares that, and ``detail`` says so.

        Unprobeable is neither. A behavioural defect may well make a good
        benchmark task, but its setup cannot be validated this way, and treating
        "cannot tell" as a pass is how the earlier oracle rule was nearly
        subverted.
        """
        return self.probeable and self.present


def contains(path: str, token: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is a string in a named file."""

    def probe(tree: Path) -> tuple[bool, str]:
        target = tree / path
        if not target.is_file():
            return False, f"{path} does not exist in the tree"
        body = target.read_text(errors="replace")
        if token in body:
            return True, f"{path} contains {token!r}"
        return False, f"{path} exists but does not contain {token!r}"

    return probe


def anywhere(token: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is a string somewhere in the tree, file unknown."""

    def probe(tree: Path) -> tuple[bool, str]:
        for f in tree.rglob("*"):
            if not f.is_file():
                continue
            try:
                if token in f.read_text(errors="replace"):
                    return True, f"{f.relative_to(tree)} contains {token!r}"
            except OSError:
                continue
        return False, f"no file in the tree contains {token!r}"

    return probe


def symlinks_in(path: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is that entries are symlinks where real directories were asked for.

    obsessiondb/rudel's defect is a filesystem property, not text: the agent
    reported five skills "copied and symlinked" when the request was for local
    copies. A string search cannot see that, so the probe reads link status.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        target = tree / path
        if not target.is_dir():
            return False, f"{path} is not a directory in the tree"
        links = [c.name for c in sorted(target.iterdir()) if c.is_symlink()]
        if links:
            return True, f"{path} holds symlinks: {', '.join(links[:5])}"
        return False, f"{path} holds no symlinks (entries are real directories)"

    return probe


def unprobeable(reason: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect has no file signature and cannot be checked this way.

    Marked with an attribute rather than left to be recognised from what it
    returns. Deciding this by looking for a word in the probe's own message
    would be the prose matching that cost four scoring bugs in a hand-built
    task, every one of them mistaking a discussion of a thing for the thing.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        return False, reason

    probe.unprobeable = True  # type: ignore[attr-defined]
    return probe


def introduced(reason: str) -> Callable[[Path], tuple[bool, str]]:
    """The agent creates the defect; a clean starting tree is correct.

    Some failures are not a defect the candidate must notice in the repository
    but one it must avoid producing. Lightprotocol's agent wrote a false warning
    into CLAUDE.md; obsessiondb/rudel's created symlinks where the developer
    asked for real directories. In both, the pre-session tree is clean *because
    nothing has gone wrong yet*, and asking "is the defect present" gets the
    polarity backwards -- the gate would reject exactly the tasks whose setup is
    correct.

    So these are declared, not probed. They are validated at scoring time, by
    what the candidate writes, rather than at setup time by what it is given.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        return False, reason

    probe.introduced = True  # type: ignore[attr-defined]
    return probe


# One probe per task, keyed by repo. The token is drawn from the defect the
# reader identified, not from convention -- a probe invented from a guess would
# reject a sound task for a reason unrelated to the benchmark.
PROBES: dict[str, Callable[[Path], tuple[bool, str]]] = {
    "blittle/pressy": contains(
        ".github/workflows/deploy-pages.yml", "pr-preview-deploy-action"
    ),
    "moltis-org/moltis": contains(
        "crates/agents/src/model.rs", "convert_skips_tool_result_entries"
    ),
    "ASRagab/optimize-anything": anywhere("integration_google"),
    "lightfastai/lightfast": anywhere("4107"),
    # The defect is a stale pin -- @sentry/cli at 3.2.2 rather than 3.3.0 -- and
    # it is in no commit. Dockerfile.worker:109 already reads `@sentry/cli@3.3.0`
    # pre-session, so like blittle/pressy this defect lived only in the
    # developer's working tree. The probe correctly rejects the task; an earlier
    # token of "@latest" came from the failing answer's own summary of what it
    # had changed, which is not the same thing as the defect.
    "desplega-ai/agent-swarm": contains("Dockerfile.worker", "3.2.2"),
    "Lightprotocol/light-protocol": introduced(
        "the agent writes a false --test-threads=1 warning into CLAUDE.md; the "
        "starting tree is correctly without it"
    ),
    "obsessiondb/rudel": introduced(
        "the agent creates symlinks in .claude/skills where real directories were "
        "asked for; the starting tree correctly holds real directories"
    ),
    "FSM1/cipher-box": unprobeable(
        "the defect is behavioural -- verification was handed back to the user "
        "rather than driven in a browser -- so it leaves no trace in the tree"
    ),
}

# Repositories that have been renamed since the corpus was recorded. Both the old
# and new URL clone today, but the redirect is not guaranteed to outlive the
# rename, so the current location is recorded explicitly.
RENAMED = {"obsessiondb/rudel": "https://github.com/opalinehq/cli"}


def repo_url(repo_id: str, recorded: str) -> str:
    """The URL to clone from, following any recorded rename."""
    return RENAMED.get(repo_id, recorded)


def check(task_id: str, repo_id: str, tree: Path) -> Presence:
    """Ask whether this task's defect is in this tree."""
    probe = PROBES.get(repo_id)
    if probe is None:
        return Presence(task_id, False, False, f"no probe defined for {repo_id}")
    present, detail = probe(tree)
    if getattr(probe, "introduced", False):
        # Nothing to find, and nothing wrong with that. Setup is sound; the
        # defect is judged from what the candidate produces.
        return Presence(task_id, True, True, f"introduced-defect task: {detail}")
    probeable = not getattr(probe, "unprobeable", False)
    return Presence(task_id, present, probeable, detail)
