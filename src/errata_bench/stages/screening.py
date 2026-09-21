"""Phase one: which recorded moments can become tasks.

Model calls only; no code is fetched and nothing is run. Each stage here asks
one question of a conversation and writes its answer, so a moment that fails a
cheap gate never reaches an expensive one.
"""

from __future__ import annotations

import time

from ..store import (
    Paths, Progress, _gather, already_done, append, completed, held, key_of, load,
    replace,
)


async def stage_triage(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Discard moments where the agent has not done anything to object to.

    One call each, against roughly eight for a full read. Fifty first-in-session
    moments were read at full price and every one came back unclear, because
    they were opening instructions rather than objections.
    """
    from ..corpus.turns import build_excerpt
    from ..corpus.turns import load_session_turns
    from ..find.triage import triage

    p = Progress("triage")
    t0 = time.monotonic()
    moments = load(paths.moments)
    done = already_done(paths.triaged)
    # The work, not the input. Slicing the input meant `--max-rows 3` run three
    # times did three rows and then nothing: the same three were always at the
    # front and were always already done.
    todo = p.cap([m for m in moments if key_of(m) not in done], limit, len(moments))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({m["session_id"] for m in todo})

    async def one(m):
        try:
            excerpt = build_excerpt(turns[m["session_id"]], m["turn_number"])
            verdict = await triage(excerpt)
            append(
                paths.triaged,
                {
                    **m,
                    "worth_reading": verdict.worth_reading,
                    "agent_has_acted": verdict.agent_has_acted,
                    "objects_to_that_work": verdict.objects_to_that_work,
                    "triage_reason": verdict.reason,
                },
            )
            return verdict.worth_reading
        except Exception as e:
            # A moment that cannot be triaged goes to the reader rather than
            # being dropped: the reader is the authority, and this stage exists
            # only to save money.
            append(paths.triaged, {**m, "worth_reading": True, "triage_reason": f"error: {e}"})
            return True

    results = await _gather([one(m) for m in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.notes = [f"{len(todo) - p.produced} discarded before reading"]
    p.took_s = time.monotonic() - t0
    return p


async def stage_read(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Decide which pushback moments represent a genuine agent error."""
    from ..corpus.turns import load_session_turns
    from ..find.reading import read_pushback

    p = Progress("read")
    t0 = time.monotonic()
    triaged = load(paths.triaged)
    if triaged:
        moments = [m for m in triaged if m.get("worth_reading")]
    else:
        moments = load(paths.moments)
    done = already_done(paths.readings)
    todo = p.cap([m for m in moments if key_of(m) not in done], limit, len(moments))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({m["session_id"] for m in todo})

    async def one(m):
        try:
            reading = await read_pushback(turns[m["session_id"]], m["turn_number"])
            append(paths.readings, {**m, "reading": reading.model_dump()})
            return True
        except Exception as e:
            append(paths.readings, {**m, "error": f"{type(e).__name__}: {e}"})
            return False

    results = await _gather([one(m) for m in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_locate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Find the four turns that define each task."""
    from ..corpus.turns import load_session_turns
    from ..find.trajectory import boundaries, locate

    p = Progress("locate")
    t0 = time.monotonic()
    viable = [
        r
        for r in load(paths.readings)
        if (r.get("reading") or {}).get("benchmark_viable")
    ]
    done = already_done(paths.trajectories)
    todo = p.cap([r for r in viable if key_of(r) not in done], limit, len(viable))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({r["session_id"] for r in todo})

    async def one(r):
        try:
            t = await locate(turns[r["session_id"]], r["turn_number"])
            b = boundaries(t)
            append(
                paths.trajectories,
                {
                    "session_id": r["session_id"],
                    "repo_id": r.get("repo_id"),
                    "complaint": r["turn_number"],
                    "failed": t.failed_turn,
                    "resolved": t.resolved_turn,
                    "request": t.request_turn,
                    "cut": b.cut_turn,
                    "usable": b.usable,
                    "reason": b.reason,
                    "defect": t.defect,
                    "resolution": t.resolution,
                    "rounds": t.rounds,
                },
            )
            return True
        except Exception as e:
            append(
                paths.trajectories,
                {
                    "session_id": r["session_id"],
                    "repo_id": r.get("repo_id"),
                    "complaint": r["turn_number"],
                    "usable": False,
                    "reason": f"error: {type(e).__name__}: {e}",
                },
            )
            return False

    results = await _gather([one(r) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_signature(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Work out what each defect looks like in a repository."""
    from ..find.signature import derive

    p = Progress("signature")
    t0 = time.monotonic()
    usable = [r for r in load(paths.trajectories) if r.get("usable")]
    done = already_done(paths.signatures)
    todo = p.cap([r for r in usable if key_of(r) not in done], limit, len(usable))

    async def one(r):
        try:
            s = await derive(
                r.get("defect", ""), r.get("resolution", ""), repo_id=r.get("repo_id", "")
            )
            append(paths.signatures, {**r, **s.model_dump()})
            return True
        except Exception as e:
            append(paths.signatures, {**r, "error": f"{type(e).__name__}: {e}"})
            return False

    if todo:
        results = await _gather([one(r) for r in todo], concurrency)
        p.produced = sum(1 for r in results if r)
        p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def _agree(ask, passes: int, keep_on: bool, reading) -> tuple[bool, str, object]:
    """Ask a gate several times and keep only what it says every time.

    Every gate here is a model reading prose, and a model reading the same
    prose twice does not always answer the same way. Measured on the scope
    gate: asked five times about the same forty-six rows, forty-one answered
    identically and five changed -- and re-screening one corpus end to end
    produced fourteen tasks one time and thirteen the other, three of fifteen
    appearing in only one. The gates decide which tasks exist, so their noise
    is the task set's noise.

    So each is asked repeatedly and answered conservatively, in whichever
    direction keeps a doubtful row out: a row is answerable only if every
    reading says so, in scope only if every reading says so, and leaking if
    *any* reading says so. The tally is stored beside the verdict, because a
    row that held 3 of 3 and one that held 2 of 3 are different evidence and
    only one of them should be read as settled.

    `reading` pulls the answer out of whatever the gate returned, and is
    required rather than guessed. The first version of this reduced each answer
    with `bool(getattr(a, "value", a))`, and no gate returns anything with a
    `.value`: every reading truth-tested as the object itself, so all three
    gates became constants -- answerable and in-scope pinned to "keep",
    leaking pinned to "leaks" -- and `build` then rejected every row alive
    under "the context already signals trouble". The guard that was supposed to
    cover this passed, because its fake gate had the `.value` field the real
    ones lack. Hence the assertion below: a gate that hands back something
    other than a bool now stops the run instead of quietly answering True.
    """
    answers = [await ask() for _ in range(max(1, passes))]
    values = [reading(a) for a in answers]
    wrong = next((v for v in values if not isinstance(v, bool)), None)
    if wrong is not None:
        raise TypeError(
            f"a gate answered with {type(wrong).__name__}, not a bool: {wrong!r}. "
            "`reading` has to pull the verdict out of the model it returns."
        )
    held = all(v is keep_on for v in values)
    # The answer handed back is the one that explains the verdict, not simply
    # the last one asked. Where the gate did not hold, that is the first
    # reading that broke it -- so a caller reading `.reasoning` off it gets the
    # reason the row was rejected, and a caller branching on its verdict field
    # branches the same way this function did. Handing back the last reading
    # instead meant a conversation three readings called leaking, clean, clean
    # was recorded as leaking and then never repaired, because the repair
    # branch asked the clean one.
    deciding = answers[-1] if held else answers[values.index(not keep_on)]
    return (keep_on if held else not keep_on,
            f"{sum(1 for v in values if v is keep_on)}/{len(values)}",
            deciding)


async def stage_screen(paths: Paths, limit: int, concurrency: int, passes: int = 1) -> Progress:
    """Check each conversation is answerable, and repair it if it leaks."""
    from ..find.answerable import asks_for_something
    from ..construct.build import last_user_message
    from ..find.leakage import signals_trouble
    from ..corpus.turns import build_excerpt, load_session_turns
    from ..find.redact import apply, carried_by, survey
    from ..find.scope import in_scope

    p = Progress("screen")
    t0 = time.monotonic()
    rows = [r for r in load(paths.signatures) if r.get("kind")]
    # The pass count is part of what makes a screened row done. Without it,
    # `--only screen --passes 5` over a directory screened at one pass reported
    # "51 already done" and changed nothing, while the user believed the
    # stricter unanimity rule had been applied. A row screened at fewer passes
    # than asked for is re-screened; one screened at more is left alone.
    done = {
        key_of(r): r.get("screen_passes", 1) for r in completed(paths.screened)
    }
    todo = p.cap([r for r in rows if done.get(key_of(r), 0) < passes], limit, len(rows))

    def prune() -> None:
        """Keep one reading per row: the best one.

        "Best" ranks a row that finished above one that errored, before it
        ranks by pass count. Ranking on pass count alone let a re-screen that
        hit a transient 429 -- an error row carrying screen_passes=3 --
        outrank and delete the completed 1-pass row it was meant to replace.
        `build` reads only rows without an error, so that moment then vanished
        from its input entirely: not built, and not rejected either, so
        nothing in rejections.jsonl said so. Its task_id disappeared from
        tasks.jsonl and the prune in `stage_build` deleted its calibration,
        controls, answers and graded attempts to match. Measured end to end:
        12 graded attempts down to 9 from one simulated 429, in a single
        `stages` command.

        Run on every exit, not only after work. A run interrupted between the
        append and the prune leaves duplicates behind, and the next run has
        nothing to re-screen and returned early -- so `build` read the same
        conversation twice, at one pass and at five, for ever.
        """
        def rank(row: dict) -> tuple:
            return (0 if row.get("error") else 1, row.get("screen_passes", 1))

        with held(paths.screened):
            rows_now = load(paths.screened)
            best: dict[tuple, dict] = {}
            for r in rows_now:
                k = key_of(r)
                if k not in best or rank(r) >= rank(best[k]):
                    best[k] = r
            kept = list(best.values())
            if len(kept) != len(rows_now):
                replace(paths.screened, kept)
                p.notes.append(f"dropped {len(rows_now) - len(kept)} superseded screenings")

    if not todo:
        prune()
        p.took_s = time.monotonic() - t0
        return p

    turns = load_session_turns({r["session_id"] for r in todo})

    async def one(r):
        out = dict(r)
        try:
            ts = turns[r["session_id"]]
            message = last_user_message(ts, r["cut"])
            if message is None:
                out["asks_for_something"] = False
                out["request_reason"] = "no user message before the cut"
            else:
                verdict, tally, a = await _agree(
                    lambda: asks_for_something(message.get("content") or ""),
                    passes, keep_on=True,
                    reading=lambda x: x.asks_for_something)
                out["asks_for_something"] = verdict
                out["asks_for_something_held"] = tally
                out["request_reason"] = a.request or a.reasoning

            # Is the defect even reachable from what was asked? nsega-mcp-todoist
            # asked "create the pull request" and its defect is a linter version
            # in a CI workflow; three candidates reported the pull request, the
            # only sensible answer, and all three were scored off_target.
            request = (message or {}).get("content") or ""
            if request:
                verdict, tally, scope = await _agree(
                    lambda: in_scope(request, r.get("defect", "")), passes, keep_on=True,
                    reading=lambda x: x.within_scope)
                out["within_scope"] = verdict
                out["within_scope_held"] = tally
                out["scope_reason"] = scope.reason
            else:
                out["within_scope"] = False
                out["scope_reason"] = "no request to judge scope against"

            # Leaking is the rejecting answer, so one reading saying so is enough.
            verdict, tally, leak = await _agree(
                lambda: signals_trouble(build_excerpt(ts, r["cut"])), passes, keep_on=False,
                reading=lambda x: x.signals_trouble)
            out["signals_trouble"] = verdict
            out["clean_held"] = tally
            out["leak_reason"] = leak.reasoning
            out["leak_quote"] = leak.quote
            out["redacted_turns"] = []
            out["rewritten_turns"] = {}
            out["redaction_worked"] = False

            # `verdict`, not `leak.signals_trouble`: the row is repaired when the
            # gate as a whole says it leaks, which with more than one reading is
            # not the same thing as what any single reading said.
            if verdict:
                # Where the leak is, before paying to repair it. Every outcome
                # of this branch is written down: fourteen rows leaked across
                # the stored runs, none was repaired, and not one row said why.
                out["leak_carried_by"] = carried_by(ts, r["cut"], leak.quote)
            if verdict and out["leak_carried_by"] == "elsewhere":
                out["redaction_outcome"] = (
                    "not attempted: the words that leak are in a tool call, a tool "
                    "result or the agent's thinking, which removing prose cannot reach"
                )
            elif verdict:
                red = await survey(ts, r["cut"])
                out["diffuse"] = red.diffuse
                out["redaction_reason"] = red.reason
                out["redaction_touched"] = red.touched
                out["redaction_outcome"] = (
                    "not repairable: the surveyor found the signal in no single turn"
                    if red.diffuse else
                    "not repairable: the surveyor found no turn to edit"
                    if not red.touched else "attempted"
                )
                if red.repairable:
                    kept = apply(ts, red.removed_turns, red.rewritten)
                    again = await signals_trouble(build_excerpt(kept, r["cut"]))
                    out["redaction_outcome"] = (
                        "repaired" if not again.signals_trouble
                        else f"still leaks after editing turns {red.touched}: {again.reasoning}"
                    )
                    if not again.signals_trouble:
                        out["redacted_turns"] = red.removed_turns
                        out["rewritten_turns"] = {
                            str(k): v for k, v in red.rewritten.items()
                        }
                        out["redaction_worked"] = True
            out["screen_passes"] = passes
            append(paths.screened, out)
            return True
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
            out["screen_passes"] = passes
            append(paths.screened, out)
            return False

    results = await _gather([one(r) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    prune()
    p.took_s = time.monotonic() - t0
    return p


