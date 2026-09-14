"""Run builder + verifier over a batch of entries and record what happened.

One entry is one unit of work: fetch, let the builder predict, run the three
graded states, write a result row, delete the scratch directory. Entries are
independent, so they run concurrently -- bounded, because each slot holds a
checkout and may hold a container, and the machine has 24 GB.

Every result is written as it completes. A crash halfway through a batch leaves
the finished rows on disk.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import workspace
from .builder import run_builder
from .corpus import Entry
from .spec import Spec
from .verifier import Outcome, RunResult, Verdict, Verifier  # noqa: F401


@dataclass
class Result:
    """One entry's outcome, start to finish."""

    repo_id: str
    child_sha: str
    language: str | None

    # what the builder predicted
    predicted: bool | None = None
    spec: dict | None = None
    builder_error: str = ""
    builder_seconds: float = 0.0

    # what the verifier found
    verified: bool | None = None
    diagnosis: str = ""
    control: str = ""
    fail_run: str = ""
    pass_run: str = ""
    verifier_seconds: float = 0.0
    verifier_error: str = ""

    # evidence quality
    uncited: list[str] = field(default_factory=list)
    unresolved_citations: list[str] = field(default_factory=list)
    # tail of the install output when preparation failed, so the cause is
    # diagnosable without re-running the entry
    prepare_output: str = ""
    # tails of the graded runs. Without these a control=fail is undiagnosable:
    # it could be the harness, a test file absent at the parent, or a genuinely
    # broken parent -- and telling them apart meant paying for the entry twice.
    control_output: str = ""
    fail_output: str = ""
    pass_output: str = ""

    @property
    def agrees(self) -> bool | None:
        if self.predicted is None or self.verified is None:
            return None
        return self.predicted == self.verified

    def to_json(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["agrees"] = self.agrees
        return d


async def process_entry(
    entry: Entry,
    *,
    verifier: Verifier,
    scratch_base: Path | None = None,
    builder_timeout_s: int = 300,
) -> Result:
    """Fetch, predict, verify, clean up. Never raises: failures become rows."""
    res = Result(
        repo_id=entry.repo_id, child_sha=entry.commit_sha, language=entry.language
    )

    try:
        with workspace.workspace(entry.commit_sha, base=scratch_base) as ws:
            # --- fetch -------------------------------------------------
            try:
                co = workspace.fetch(entry.repo_url, entry.commit_sha, ws / "repo")
            except Exception as e:  # unreachable commit, private repo, no parent
                res.builder_error = f"fetch: {type(e).__name__}: {e}"
                return res

            child_tree = co.export_tree(co.child_sha, ws / "child")

            # --- builder predicts --------------------------------------
            t0 = time.monotonic()
            try:
                spec: Spec = await asyncio.wait_for(
                    run_builder(
                        entry,
                        tree=child_tree,
                        repo=co.root,
                        parent_sha=co.parent_sha,
                    ),
                    timeout=builder_timeout_s,
                )
            except Exception as e:
                res.builder_error = f"{type(e).__name__}: {e}"
                res.builder_seconds = time.monotonic() - t0
                return res
            res.builder_seconds = time.monotonic() - t0

            res.predicted = spec.reproducible
            res.spec = spec.to_json()
            res.uncited = spec.uncited_fields()
            res.unresolved_citations = [
                name
                for name, c in spec.citations.items()
                if not c.resolves_in(child_tree)
            ]

            if not spec.reproducible:
                res.diagnosis = f"builder declined: {spec.blocked_reason}"
                return res
            if spec.missing_fields():
                res.builder_error = f"incomplete spec: {spec.missing_fields()}"
                return res

            # --- verifier decides --------------------------------------
            t1 = time.monotonic()
            try:
                ctrl = co.export_tree(co.parent_sha, ws / "ctrl")
                fail = co.export_tree(co.parent_sha, ws / "fail")
                for tf in spec.test_files:
                    co.write_file_from(co.child_sha, tf, fail)
                pas = co.export_tree(co.child_sha, ws / "pass")

                cache = ws / "cache"
                # Dependencies come down once per tree, with the network on,
                # using the commands the builder read out of the repo. Graded
                # runs are then offline: a build that never happened prints
                # FAIL exactly like a failing test.
                # Prepare once, on the child tree, and commit the result to an
                # image. The graded runs start from that image so they inherit
                # apt packages and globally installed tools, none of which live
                # in the mounted tree. Each graded run still mounts its own
                # tree, so the three source states stay distinct.
                tag = f"eb-prep-{entry.commit_sha[:12].lower()}"
                prep, prepared_image = verifier.prepare(
                    tree=pas,
                    image=spec.image,
                    toolchain=spec.toolchain,
                    install_command=spec.install_command,
                    setup_commands=spec.setup_commands,
                    cache_host=cache,
                    workdir=spec.work_dir or None,
                    tag=tag,
                )
                try:
                    if prep.outcome is not Outcome.PASS or not prepared_image:
                        res.verified = False
                        res.diagnosis = (
                            "prepare-failed: dependency install did not succeed, "
                            "so no conclusion can be drawn about this entry"
                        )
                        res.prepare_output = prep.stdout[-1500:]
                        res.verifier_seconds = time.monotonic() - t1
                        return res

                    # The image carries global tooling, but anything the install
                    # wrote under /w is masked when a different tree is mounted.
                    # ctrl and fail are different trees, so the install runs
                    # again in each. Cheap when it is a no-op (Python installs
                    # to site-packages); necessary for node_modules.
                    inst_kw = dict(
                        image=prepared_image,
                        toolchain=spec.toolchain,
                        install_command=spec.install_command,
                        cache_host=cache,
                        workdir=spec.work_dir or None,
                    )
                    for label, t in (("control", ctrl), ("fail", fail)):
                        r = verifier.install_in_tree(tree=t, **inst_kw)
                        if r.outcome is not Outcome.PASS:
                            res.verified = False
                            res.diagnosis = (
                                f"prepare-failed: install into the {label} tree "
                                "did not succeed, so no conclusion can be drawn"
                            )
                            res.prepare_output = r.stdout[-1500:]
                            res.verifier_seconds = time.monotonic() - t1
                            return res

                    kw = dict(
                        image=prepared_image,
                        toolchain=spec.toolchain,
                        test_command=spec.test_command,
                        cache_host=cache,
                    )
                    # The control proves the parent tree builds and its own
                    # tests pass. When the commit ADDS a test file, the child's
                    # command names a path the parent never had, so the runner
                    # errors -- which looks like a broken parent but is only an
                    # impossible request. Run the control only when every test
                    # file it names exists at the parent.
                    ctrl_ok = all(
                        (ctrl / tf).is_file() for tf in spec.test_files
                    ) if spec.test_files else True

                    control_run = (
                        verifier.run_test(tree=ctrl, **kw)
                        if ctrl_ok
                        else RunResult(Outcome.ERROR, None, "control not applicable", 0.0)
                    )
                    verdict = Verdict(
                        control=control_run,
                        fail_run=verifier.run_test(tree=fail, **kw),
                        pass_run=verifier.run_test(tree=pas, **kw),
                        prepare=prep,
                        control_applicable=ctrl_ok,
                    )
                finally:
                    # Never leave a prepared image behind, on any exit path.
                    if prepared_image and prepared_image != spec.image:
                        verifier.discard_image(prepared_image)
            except Exception as e:
                res.verifier_error = f"{type(e).__name__}: {e}"
                res.verifier_seconds = time.monotonic() - t1
                return res

            res.verifier_seconds = time.monotonic() - t1
            res.verified = verdict.reproducible
            res.diagnosis = verdict.diagnosis
            res.control = verdict.control.outcome.value
            res.fail_run = verdict.fail_run.outcome.value
            res.pass_run = verdict.pass_run.outcome.value
            # Keep the tails only where they explain something: a passing
            # control needs no explanation, a failing one always does.
            if verdict.control.outcome is not Outcome.PASS:
                res.control_output = verdict.control.stdout[-2000:]
            if verdict.fail_run.outcome is not Outcome.FAIL:
                res.fail_output = verdict.fail_run.stdout[-2000:]
            if verdict.pass_run.outcome is not Outcome.PASS:
                res.pass_output = verdict.pass_run.stdout[-2000:]
            return res

    except Exception:
        res.builder_error = f"unexpected: {traceback.format_exc(limit=3)}"
        return res


async def run_batch(
    entries: list[Entry],
    *,
    concurrency: int = 4,
    out_path: Path,
    scratch_base: Path | None = None,
    timeout_s: int = 900,
) -> list[Result]:
    """Process entries concurrently, writing each result as it lands."""
    verifier = Verifier(timeout_s=timeout_s)
    sem = asyncio.Semaphore(concurrency)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    done = 0

    async def one(entry: Entry) -> Result:
        nonlocal done
        async with sem:
            res = await process_entry(
                entry, verifier=verifier, scratch_base=scratch_base
            )
        async with lock:
            done += 1
            with out_path.open("a") as fh:
                fh.write(json.dumps(res.to_json()) + "\n")
            mark = (
                "?" if res.verified is None else ("OK" if res.verified else "no")
            )
            print(
                f"[{done}/{len(entries)}] {mark:2s} {res.repo_id[:34]:34s} "
                f"{res.child_sha[:10]} pred={res.predicted} ver={res.verified} "
                f"{res.diagnosis[:60]}",
                flush=True,
            )
        return res

    started = datetime.now(timezone.utc).isoformat()
    print(f"batch of {len(entries)} at concurrency {concurrency}, started {started}")
    return list(await asyncio.gather(*(one(e) for e in entries)))


def summarise(results: list[Result]) -> str:
    n = len(results)
    got_verdict = [r for r in results if r.verified is not None]
    repro = [r for r in got_verdict if r.verified]
    agreed = [r for r in got_verdict if r.agrees]
    declined = [r for r in results if r.predicted is False]
    errors = [r for r in results if r.builder_error or r.verifier_error]

    lines = [
        "",
        "=" * 62,
        f"entries                {n}",
        f"builder said yes       {sum(1 for r in results if r.predicted)}",
        f"builder declined       {len(declined)}",
        f"errors                 {len(errors)}",
        f"reached a verdict      {len(got_verdict)}",
        f"VERIFIED REPRODUCIBLE  {len(repro)}"
        + (f"  ({len(repro)/n:.0%} of batch)" if n else ""),
    ]
    if got_verdict:
        lines.append(
            f"builder agreed         {len(agreed)}/{len(got_verdict)} "
            f"({len(agreed)/len(got_verdict):.0%})"
        )
    # The expensive error: builder said yes, verifier disagreed, and why.
    wrong = [r for r in got_verdict if r.predicted and not r.verified]
    if wrong:
        lines.append("")
        lines.append("builder said yes but verifier disagreed:")
        for r in wrong[:12]:
            lines.append(f"   {r.repo_id[:32]:32s} {r.child_sha[:10]} {r.diagnosis[:64]}")
    if errors:
        lines.append("")
        lines.append("errors:")
        for r in errors[:12]:
            msg = (r.builder_error or r.verifier_error).replace("\n", " ")[:70]
            lines.append(f"   {r.repo_id[:32]:32s} {r.child_sha[:10]} {msg}")
    lines.append("=" * 62)
    return "\n".join(lines)
