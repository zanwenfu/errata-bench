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

import re

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..spec import within


@dataclass
class Presence:
    """Whether a task's defect is in the tree, and how that was established."""

    task_id: str
    present: bool
    probeable: bool
    detail: str
    # How much the check actually established. "token" found the defect's own
    # literal string in the tree. "file" found only the file it lives in, which
    # is all that can be checked for a behavioural defect. "declared" means the
    # setup is correct by the task's shape rather than by inspection, as for an
    # introduced defect. Recorded rather than flattened into the boolean,
    # because a benchmark that cannot say how strongly each task was validated
    # is asking to be trusted on that point.
    strength: str = "token"

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


def _is_distinctive(token: str) -> bool:
    """Whether a token means one thing wherever it is found.

    A tree-wide search is only evidence if a hit can be attributed to the
    defect. ``convert_skips_tool_result_entries`` and
    ``rossjrw/pr-preview-deploy-action`` identify themselves; ``3.2.2`` and
    ``4107`` appear in lockfiles and configs that have nothing to do with the
    case. The test is structural -- length, and whether the token carries
    identifying characters rather than being a bare number or version.
    """
    t = token.strip()
    if len(t) < 8:
        return False
    # A bare version or number, possibly with punctuation: 3.2.2, 4107, v1.2.3.
    if re.fullmatch(r"v?[\d.]+", t):
        return False
    # Needs some identifier-like content, not only digits and separators.
    letters = sum(c.isalpha() for c in t)
    return letters >= 4


def contains(path: str, token: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is a string, expected in a named file but searched for anywhere.

    The named file is a hint, not a boundary. lightfastai/lightfast's signature
    names ``.mcp/start-lightfast.sh`` for the stale port 4107 -- a fair reading
    of the defect, and that is where the fix landed -- but in the pre-session
    tree the port sits in ``.coderabbit.yaml``. Searching only the named file
    rejected a sound task.

    The defect is present if it is anywhere in the tree. Which file holds it is
    a detail of how the repository happened to be arranged that day, and the
    reader is describing the defect rather than auditing the tree.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        # A signature often names a bare filename -- nosman-gossamer-33's says
        # `server.ts` where the repository holds `src/server.ts` -- and matching
        # it only from the repository root reported the file "not in the tree"
        # while the candidate was reading it. file_exists already searched by
        # name; this one did not, and the two disagreed about the same tree.
        # `within`, not a bare join: `path` comes from a model reading a
        # transcript, and an absolute one replaces the tree. Probed with
        # `/etc/hosts` this reported the token present, then raised
        # `ValueError: not in the subpath` out of `build()` on the very next
        # line, after every clone of the run had been paid for.
        here = within(tree, path)
        if here is None:
            return False, f"{path} is not a path inside the tree"
        targets = [here]
        if not targets[0].is_file() and "/" not in path:
            targets = [q for q in tree.rglob(path) if q.is_file()][:20]
        for target in targets:
            if target.is_file() and token in target.read_text(errors="replace"):
                where = target.relative_to(tree)
                return True, f"{where} contains {token!r}"
        target = targets[0] if targets else here
        # Widening the search past the named file is only safe for a token
        # distinctive enough to mean one thing. desplega-ai/agent-swarm's defect
        # is @sentry/cli pinned to 3.2.2; searching the whole tree found "3.2.2"
        # in bun.lock, belonging to some unrelated transitive dependency, and
        # passed a task whose real defect is in no commit at all. A bare version
        # number matches anything.
        if _is_distinctive(token):
            found, detail = anywhere(token)(tree)
            if found:
                return True, f"{detail} (signature named {path})"
            return False, f"no file in the tree contains {token!r} (signature named {path})"
        if not target.is_file():
            return False, f"{path} is not in the tree, and {token!r} is too generic to search for"
        return False, f"{path} does not contain {token!r} (too generic to search elsewhere)"

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


def file_exists(path: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is a behaviour in a named file, so only the file can be checked.

    Weaker than a token, and honest about it. "deltaPeriodLabel() retained a
    one-week alignment tolerance" and "the polling loop never exited when
    export_zip reached failed" are real defects with no literal signature; the
    file they live in is the only part a setup check can confirm.

    What this establishes is that the task is coherent -- the file the defect
    concerns is in the tree the candidate gets. What it cannot establish is that
    the defect is still in that file. The caller is told which, through
    ``Presence.strength``.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        target = within(tree, path)
        if target is None:
            return False, f"{path} is not a path inside the tree"
        if target.is_file():
            return True, f"{path} is present (behavioural defect, not text-matchable)"
        # A basename can still be located when the signature gives no directory.
        if "/" not in path:
            hits = [p for p in tree.rglob(path) if p.is_file()]
            if hits:
                return True, f"{hits[0].relative_to(tree)} is present (matched by name)"
        return False, f"{path} is not in the tree"

    probe.weak = True  # type: ignore[attr-defined]
    return probe


def symlinks_in(path: str) -> Callable[[Path], tuple[bool, str]]:
    """The defect is that entries are symlinks where real directories were asked for.

    obsessiondb/rudel's defect is a filesystem property, not text: the agent
    reported five skills "copied and symlinked" when the request was for local
    copies. A string search cannot see that, so the probe reads link status.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        target = within(tree, path)
        if target is None:
            return False, f"{path} is not a path inside the tree"
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


def repo_url(repo_id: str, recorded: str) -> str:
    """The URL to clone from.

    Renamed repositories are not special-cased. obsessiondb/rudel is now
    opalinehq/cli, and a hand-maintained table of such moves would have exactly
    the defect the probe table had: it only knows the repositories someone
    happened to hit. Git follows GitHub's redirect on fetch, so the recorded URL
    keeps working, and a rename that GitHub stops redirecting will surface as a
    fetch failure -- which is the honest outcome, not a silent wrong tree.
    """
    return recorded


def _introduced_but_verified(sig) -> Callable[[Path], tuple[bool, str]]:
    """An introduced defect whose absence from the starting tree can be checked.

    The task's own premise is that the defect is not there yet. Where the
    signature gives a distinctive token, that premise is testable, and a task
    failing it is rejected rather than shipped: its classification and its
    signature disagree, so one of them is wrong.
    """

    def probe(tree: Path) -> tuple[bool, str]:
        found, detail = anywhere(sig.token)(tree)
        if found:
            return False, (
                f"classified as introduced, meaning the starting tree should not "
                f"contain the defect, but {detail}"
            )
        return True, (
            f"introduced-defect task: {sig.token!r} is correctly absent from the "
            f"starting tree"
        )

    probe.introduced = True  # type: ignore[attr-defined]
    probe.verified = True  # type: ignore[attr-defined]
    return probe


def probe_for(sig) -> Callable[[Path], tuple[bool, str]]:
    """Build a probe from a derived :class:`~errata_bench.signature.Signature`.

    This replaced a dictionary keyed by repository -- nine hand-written entries
    covering eight repositories and no others. A lookup table cannot run at
    corpus scale, because every new session needs new code before it can be
    checked, so the signature is read from the case instead.
    """
    if sig.kind == "none":
        return unprobeable(
            f"the defect is behavioural and leaves no trace in the tree: {sig.reasoning[:160]}"
        )
    if sig.kind == "introduced":
        # An introduced-defect task asserts the tree is clean, and the presence
        # probe is skipped on that assertion. When the signature also names a
        # distinctive token, the claim is checkable and worth checking: two
        # tasks reached candidates as "introduced" while their token sat in the
        # starting tree -- basher83-lunar-claude's `pre-commit-run` in
        # mise.toml, maoxiaoke-nazha's `translate-x`. Either the classification
        # is wrong or the token is, and both make the task unsound.
        if sig.token and _is_distinctive(sig.token):
            return _introduced_but_verified(sig)
        return introduced(
            f"the agent creates this defect; a clean starting tree is correct: {sig.reasoning[:160]}"
        )
    if sig.is_symlink_defect:
        # A symlink defect needs a directory to inspect. Defaulting to the repo
        # root when none is named listed the top level, found no symlinks, and
        # rejected obsessiondb/rudel -- a wrong answer delivered confidently.
        # Without a directory there is nothing to check, and saying so is honest.
        if not sig.path:
            return unprobeable(
                "the defect is that paths are symlinks, but no directory was named "
                "to inspect"
            )
        return symlinks_in(sig.path)

    # Token and path are different strengths of evidence, and demanding the
    # stronger one throws away most real tasks. Across nineteen held-out
    # trajectories only two defects had a literal token, while twelve named a
    # file: most defects are behaviours, not strings. "The polling loop never
    # exited", "formatting violations remained", "a one-week alignment tolerance"
    # -- none of these can be grepped for, and all of them are real.
    #
    # An early version required a token and would have discarded five of the
    # seven present-kind defects in that set. So the gate checks at whatever
    # strength the signature supports and records which, leaving the caller to
    # decide what is strong enough rather than deciding it here by silence.
    if sig.token and sig.path:
        return contains(sig.path, sig.token)
    if sig.token:
        return anywhere(sig.token)
    if sig.path:
        return file_exists(sig.path)
    return unprobeable("the signature names neither a file nor a token to check")


def check(task_id: str, sig, tree: Path) -> Presence:
    """Ask whether this task's defect is in this tree, given its signature."""
    probe = probe_for(sig)
    present, detail = probe(tree)
    if getattr(probe, "introduced", False):
        # An introduced defect is absent by definition, so `present` cannot mean
        # what it means elsewhere. Two cases:
        #
        #   declared  no token to check. The task's premise is taken on trust,
        #             as it always was, and the setup counts as sound.
        #   verified  a distinctive token was available and the premise held,
        #             or it did not -- in which case the task contradicts
        #             itself and must be rejected.
        #
        # Conflating them rejected four sound tasks whose only fault was having
        # no token to check.
        if getattr(probe, "verified", False):
            return Presence(task_id, present, True, detail, "verified")
        return Presence(task_id, True, True, f"introduced-defect task: {detail}", "declared")
    probeable = not getattr(probe, "unprobeable", False)
    strength = "file" if getattr(probe, "weak", False) else "token"
    return Presence(task_id, present, probeable, detail, strength)
