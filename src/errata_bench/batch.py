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
from .verifier import Outcome, Verdict, Verifier


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
                # Dependencies come down once, with the network on. Graded runs
                # are offline; a build that never happened prints FAIL exactly
                # like a failing test.
                verifier.warm_cache(
                    tree=pas,
                    image=spec.image,
                    toolchain=spec.toolchain,
                    cache_host=cache,
                )
                kw = dict(
                    image=spec.image,
                    toolchain=spec.toolchain,
                    test_command=spec.test_command,
                    cache_host=cache,
                )
                verdict = Verdict(
                    control=verifier.run_test(tree=ctrl, **kw),
                    fail_run=verifier.run_test(tree=fail, **kw),
                    pass_run=verifier.run_test(tree=pas, **kw),
                )
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
