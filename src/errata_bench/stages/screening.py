"""Phase one: which recorded moments can become tasks.

Model calls only; no code is fetched and nothing is run. Each stage here asks
one question of a conversation and writes its answer, so a moment that fails a
cheap gate never reaches an expensive one.
"""

from __future__ import annotations

import dataclasses
import os
import time

from ..store import (
    Paths, Progress, _gather, already_done, append, append_used, completed, held, key_of, load,
    replace,
)


#: The keys that mean "the stage that wrote this row did not finish". `_succeeded`
#: reads both, and `completed()` deletes any row carrying either, so a stage that
#: copies a previous row forward must not copy these with it.
FAILURE_KEYS = ("error",)


def carried_forward(row: dict) -> dict:
    """A previous stage's row, without its verdict about its own failure.

    `stage_read` wrote `{**triaged_row, "reading": ...}`. A moment whose triage
    hit a 429 carries `error`, so the *reading* -- which succeeded, and was paid
    for -- was written with an `error` key it had no business carrying. On the
    next run `completed()` deleted it as failed work. Best case that is one read
    bought twice; worst case triage answers `worth_reading=False` on the retry,
    the reading is never rebuilt, and the paid row is simply gone with every
    counter still reporting it as produced.

    Error rows do not survive a re-run, so this cannot be counted from disk --
    `completed()` has already removed them. It is bounded by how often the
    reader is asked after a triage failure, which is exactly what a long run
    against a throttling endpoint produces.
    """
    return {k: v for k, v in row.items() if k not in FAILURE_KEYS}


def runnable(moments: list[dict]) -> tuple[list[dict], int]:
    """The moments whose task could actually be attempted, and how many were not.

    `find_moments` asks this when it collects, but nothing asked it again when
    the rows were read -- and `can_be_sandboxed` appeared in exactly one place
    in the tree. So every moments file written before the filter existed, and
    every one written by an older checkout, was still read at about eight model
    calls apiece for tasks the attempt stage then refuses to run (G-48, D-31).
    Of the 2,199 moments on disk, 658 are in a language with no container.

    Read here rather than filtered out at collection time, because the rows are
    paid data: nothing is deleted, it is simply not read further.

    Only where the language is known and has no image. `find_moments` refuses
    on uncertainty, which is right when choosing what to spend on; here the row
    already exists, so a repository the corpus has no row for, or records no
    language for, is read as before. Fail-closed here dropped every moment in
    the check suites, whose fixture repositories are not in the corpus at all
    -- and would drop real rows the moment `load_repos` came back thin.
    """
    from ..construct.container import can_be_sandboxed
    from ..corpus.sessions import load_repos

    try:
        languages = {rid: repo.language for rid, repo in load_repos().items()}
    except Exception:  # noqa: BLE001 - no corpus is not a reason to skip work
        return list(moments), 0
    keep = [m for m in moments
            if not languages.get(m.get("repo_id"))
            or can_be_sandboxed(languages[m["repo_id"]])]
    return keep, len(moments) - len(keep)


def unbranched(moments: list[dict]) -> tuple[list[dict], int]:
    """The moments not in a session the corpus lists as holding an abandoned branch (`crawl.shape.rewound`,
    G-95), and how many were.

    `find_moments` leaves them out when it collects; asked again here, as
    `runnable` is, because a moments file drawn before the list existed is
    read too (review, 10-02), at triage, reading and locate. The list is read
    by `corpus.sessions.edited_sessions`, which refuses a stale one.
    """
    from ..corpus.sessions import edited_sessions

    branched = edited_sessions()
    keep = [m for m in moments if m.get("session_id") not in branched]
    return keep, len(moments) - len(keep)


async def stage_triage(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Discard moments where the agent has not done anything to object to.

    One call each, against roughly eight for a full read. Fifty first-in-session
    moments were read at full price and every one came back unclear, because
    they were opening instructions rather than objections.
    """
    from ..corpus.turns import load_session_turns
    from ..corpus.recover import recovered
    from ..find.triage import triage, view as triage_view
    from ..llm import metering, model_name

    p = Progress("triage")
    t0 = time.monotonic()
    moments, unrunnable = runnable(load(paths.moments))
    if unrunnable:
        p.notes.append(f"{unrunnable} moments are not read: their repository's language "
                       "has no container here, so no candidate could be run against the "
                       "task even if one were built")
    moments, branched = unbranched(moments)
    if branched:
        p.notes.append(f"{branched} moments are not read: their session holds a message the developer edited and "
                       "sent again, and its rows still hold the abandoned branch")
    done = already_done(paths.triaged)
    # The work, not the input. Slicing the input meant `--max-rows 3` run three
    # times did three rows and then nothing: the same three were always at the
    # front and were always already done.
    todo = p.cap([m for m in moments if key_of(m) not in done], limit, len(moments))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = recovered(load_session_turns({m["session_id"] for m in todo}))
    # A shell command the developer ran is no message to the agent: passed
    # over when moments are drawn, and here for a moments file drawn before.
    shell = {key_of(m) for m in todo if any(
        t.get("turn_number") == m["turn_number"] and t.get("turn_type") == "user_prompt"
        and (t.get("content") or "").lstrip().startswith("<bash-input>") for t in turns.get(m["session_id"], []))}
    if shell:
        todo = [m for m in todo if key_of(m) not in shell]
        p.notes.append(f"{len(shell)} moments are not read: each is a shell command the developer ran, not a "
                       "message to the agent")

    async def one(m):
        try:
            excerpt = triage_view(turns[m["session_id"]], m["turn_number"])
            verdict = await triage(excerpt)
            append_used(
                paths.triaged,
                {
                    **m,
                    "worth_reading": verdict.worth_reading,
                    "agent_has_acted": verdict.agent_has_acted,
                    "objects_to_that_work": verdict.objects_to_that_work,
                    "triage_reason": verdict.reason,
                    "find_model": model_name(),
                },
            )
            return verdict.worth_reading
        except Exception as e:
            # A moment that cannot be triaged still goes to the reader rather
            # than being dropped: the reader is the authority, and this stage
            # exists only to save money. But failing open is not the same as
            # deciding, and with the reason under `triage_reason` alone nothing
            # could tell them apart: `_succeeded` looks for an `error` key or
            # "error:" in `reason`, found neither, and `already_done` counted
            # the row finished, so the question was never asked again. That is
            # the accident `completed` was written for, at the one stage it had
            # not reached -- 73 of the 922 rows in runs/scale900/triaged.jsonl
            # are `worth_reading=True` with "Error code: 429 ... no credits
            # remaining" as their reason, a verdict nobody gave. 28 went on to
            # the reader at about eight calls each; a resumed run would step
            # straight over the other 45. Under `error` the row is work again
            # (G-19).
            append_used(paths.triaged, {**m, "worth_reading": True,
                                        "error": f"{type(e).__name__}: {e}",
                                        "triage_reason": f"error: {e}", "find_model": model_name()})
            return e

    # Each row with its own calls' token use, as screening's (09-30 review): a
    # build from a new corpus is paid here first, and was priced nowhere.
    results = await _gather([metering(one(m)) for m in todo], concurrency)
    # Three outcomes, counted apart, because two of them were one number
    # before: a moment kept for the reader, one this stage decided against, and
    # one it could not read at all. Folding the third into "discarded before
    # reading" would say this stage turned away a moment it never judged --
    # the shape of B-222 and B-223, twice over in one day.
    # Disjoint, as every other stage's pair is: produced, failed and discarded
    # sum to the work attempted. An errored moment is counted as failed and
    # not as produced, even though its row does go on to the reader -- the
    # note below says so, because a number that means two things is how the
    # last three misreporting bugs happened.
    errored = [r for r in results if isinstance(r, BaseException)]
    p.produced = sum(1 for r in results if r is True)
    p.failed = len(errored)
    # Appended, not assigned. The last stage that assigned over its notes threw
    # away the one line explaining why it had done less work than asked
    # (B-222); this one was still doing it, and swallowed the count of moments
    # passed over for having no container.
    p.notes.append(f"{sum(1 for r in results if r is False)} discarded before reading")
    if errored:
        p.notes.append(f"{len(errored)} could not be triaged: kept as work to retry "
                       f"rather than as a verdict, and still passed to the reader in "
                       f"this run -- {str(errored[0])[:80]}")
    p.took_s = time.monotonic() - t0
    return p


async def stage_read(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Decide which pushback moments represent a genuine agent error."""
    from ..corpus.turns import load_session_turns
    from ..corpus.recover import recovered
    from ..find.reading import read_pushback
    from ..llm import metering, model_name

    p = Progress("read")
    t0 = time.monotonic()
    triaged = load(paths.triaged)
    if triaged:
        moments = [m for m in triaged if m.get("worth_reading")]
    else:
        moments = load(paths.moments)
    moments, unrunnable = runnable(moments)
    if unrunnable:
        p.notes.append(f"{unrunnable} moments are not read: their repository's language "
                       "has no container here")
    moments, branched = unbranched(moments)
    if branched:
        p.notes.append(f"{branched} moments are not read: their session's rows hold an abandoned branch")
    done = already_done(paths.readings)
    todo = p.cap([m for m in moments if key_of(m) not in done], limit, len(moments))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = recovered(load_session_turns({m["session_id"] for m in todo}))

    async def one(m):
        try:
            reading = await read_pushback(turns[m["session_id"]], m["turn_number"])
            append_used(paths.readings, {**carried_forward(m), "reading": reading.model_dump(),
                                         "find_model": model_name()})
            return True
        except Exception as e:
            append_used(paths.readings, {**carried_forward(m), "error": f"{type(e).__name__}: {e}",
                                         "find_model": model_name()})
            return False

    results = await _gather([metering(one(m)) for m in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


def failures_held(paths: Paths) -> dict[tuple[str, int], tuple[str, int]]:
    """The failed answers that usable trajectories of the current rules already hold, and which moment holds
    each: in this run folder and its siblings, as `run.py moments --fresh` reads them, less backups
    (``.pre-``). Keyed by (session, failed turn); the holder is (folder, complaint turn).

    Rows of earlier rules hold nothing (`trajectory.RULES`): what was usable
    then is not what is usable now -- the pilot's cyc-seattle-isthmia-74 was
    usable on a status summary -- and the runs folder holds SWE-chat's
    experiments, judged under every rule there has been (review, 10-02).
    """
    from ..find.trajectory import RULES

    taken: dict[tuple[str, int], tuple[str, int]] = {}
    for f in sorted(paths.root.parent.glob("*/trajectories.jsonl")):
        if ".pre-" in f.parent.name:
            continue
        for r in load(f):
            # A malformed row of another folder holds nothing, and stops nothing.
            if (isinstance(r, dict) and r.get("usable") and r.get("rules") == RULES
                    and isinstance(r.get("session_id"), str) and isinstance(r.get("failed"), int)
                    and r["failed"] >= 0):
                taken.setdefault((r["session_id"], r["failed"]), (f.parent.name, r.get("complaint")))
    return taken


async def stage_locate(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Find the four turns that define each task."""
    from ..corpus.turns import load_session_turns
    from ..corpus.recover import recovered
    from ..find.trajectory import boundaries, locate
    from ..llm import metering, model_name

    p = Progress("locate")
    t0 = time.monotonic()
    # One row a moment: a reading written twice would be located twice, and
    # the same moment is never refused against itself (review, 10-02).
    viable = list({
        key_of(r): r
        for r in load(paths.readings)
        if (r.get("reading") or {}).get("benchmark_viable")
    }.values())
    viable, branched = unbranched(viable)
    if branched:
        p.notes.append(f"{branched} viable readings are not located: their session's rows hold an abandoned branch")
    done = already_done(paths.trajectories)
    todo = p.cap([r for r in viable if key_of(r) not in done], limit, len(viable))
    if not todo:
        p.took_s = time.monotonic() - t0
        return p

    turns = recovered(load_session_turns({r["session_id"] for r in todo}))
    # One failure, one task (pilot audit, 10-02): two objections to one answer
    # -- a session's first pushback and a later one, drawn into two runs --
    # were both located as usable. A usable trajectory whose failed answer
    # another moment's already holds is not usable; the same moment located
    # again, in another folder, is not refused. Read for each usable answer,
    # so a sibling run going at the same time is seen, and this run's own
    # rows: nothing is awaited between the reading and the writing.
    from ..find.trajectory import RULES

    async def one(r):
        try:
            t = await locate(turns[r["session_id"]], r["turn_number"])
            b = boundaries(t)
            held_by = None
            if b.usable:
                holder = failures_held(paths).get((r["session_id"], t.failed_turn))
                if holder and holder[1] != r["turn_number"]:
                    held_by = f"{holder[0]}:{holder[1]}"
                    b = dataclasses.replace(b, usable=False,
                                            reason=f"the same failed answer as the trajectory at {held_by}")
            append_used(
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
                    "resolution_fixes_it": t.resolution_fixes_it,
                    "looked_to": getattr(t, "_looked_to", -1),
                    "held_by": held_by,
                    "rules": RULES,
                    "find_model": model_name(),
                },
            )
            return True
        except Exception as e:
            append_used(
                paths.trajectories,
                {
                    "session_id": r["session_id"],
                    "repo_id": r.get("repo_id"),
                    "complaint": r["turn_number"],
                    "usable": False,
                    "reason": f"error: {type(e).__name__}: {e}",
                    "find_model": model_name(),
                },
            )
            return False

    results = await _gather([metering(one(r)) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def stage_signature(paths: Paths, limit: int, concurrency: int) -> Progress:
    """Work out what each defect looks like in a repository."""
    from ..find.signature import derive
    from ..llm import metering, model_name

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
            append_used(paths.signatures, {**r, **s.model_dump(), "find_model": model_name()})
            return True
        except Exception as e:
            append_used(paths.signatures, {**r, "error": f"{type(e).__name__}: {e}", "find_model": model_name()})
            return False

    if todo:
        results = await _gather([metering(one(r)) for r in todo], concurrency)
        p.produced = sum(1 for r in results if r)
        p.failed = sum(1 for r in results if not r)
    p.took_s = time.monotonic() - t0
    return p


async def _once_more(ask):
    """One reading, asked once more if the model's answer did not parse.

    A row is what a run saves. One call failing late in it -- an answer that
    did not parse as the gate's form, after the request, scope and leak gates
    were all read -- threw the row away, and the next run paid for each of them
    again: up to 18 whole-conversation calls at three passes (10-01 review).
    Asked again, such an answer usually parses. Nothing else is asked again
    here: a refusal (`llm.refusal`) answers the same, and a throttle or a
    dropped connection has had its retries (`llm.resilient`). An answer that
    fails to parse twice fails the row.
    """
    import re

    from ..llm import refusal

    try:
        return await ask()
    except Exception as e:  # noqa: BLE001 - raised again unless it is an answer that did not parse
        said = f"{type(e).__name__}: {e}"
        if (refusal(said) or "no choices" in said.lower()
                or not re.search(r"ModelBehaviorError|ValidationError|JSONDecodeError|Invalid JSON", said)):
            raise
    return await ask()


async def _agree(ask, passes: int, keep_on: bool, reading) -> tuple[bool, str, object]:
    """Ask a gate an odd number of times and keep what most of the readings say.

    Every gate here is a model reading prose, and a model reading the same
    prose twice does not always answer the same way. Measured on the scope
    gate: asked five times about the same forty-six rows, forty-one answered
    identically and five changed -- and re-screening one corpus end to end
    produced fourteen tasks one time and thirteen the other, three of fifteen
    appearing in only one. The gates decide which tasks exist, so their noise
    is the task set's noise.

    So each is asked repeatedly and settled by majority: a row is answerable
    if most readings say so, in scope if most say so, and leaking if most say
    so. Majority rather than unanimity because these three decide whether a
    task exists at all, and an estimator for that should be unbiased -- see
    the note at the count below, and D-34. The tally is stored beside the
    verdict, because a row that held 3 of 3 and one that held 2 of 3 are
    different evidence and only one of them should be read as settled.

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
    # An even number of readings has no majority to settle a tie with, and the
    # rule below resolves one by refusing. That is the bias D-34 removed --
    # asking more times could then only ever remove rows, never add one --
    # reintroduced by nothing more than someone choosing two passes because
    # three cost more. Refused here rather than documented, because a silent
    # return to the old behaviour is exactly what this is about.
    if passes > 1 and passes % 2 == 0:
        raise ValueError(
            f"a gate asked {passes} times has no majority to settle a tie, and a tie "
            "resolved by refusing is the rejection bias D-34 removed. Ask an odd "
            "number of times."
        )
    answers = [await _once_more(ask) for _ in range(max(1, passes))]
    values = [reading(a) for a in answers]
    # Collected, not sought with a default: written `next(..., None)` this let
    # through the one wrong answer most worth catching. `None` is what a
    # `reading` returns when it reaches for a field the model does not have,
    # and it was also the sentinel for "nothing wrong", so a gate answering
    # `None` every time raised nothing, matched neither branch below, and
    # silently rejected the row -- the same silent-constant failure the
    # docstring above is about, one layer up.
    wrong = [v for v in values if not isinstance(v, bool)]
    if wrong:
        raise TypeError(
            f"a gate answered with {type(wrong[0]).__name__}, not a bool: {wrong[0]!r}. "
            "`reading` has to pull the verdict out of the model it returns."
        )
    # Most of them, not all of them. Unanimity does not reduce a gate's noise;
    # it reduces variance by moving the mean toward rejection, so asking a
    # screening gate more times could only ever remove rows and never add one.
    # These three gates decide whether a task EXISTS, and for that question the
    # estimator should be unbiased. Measured over 765 readings (R-30): 7 of 152
    # (row, gate) sets disagree with themselves, and of 50 rows the rule keeps
    # 34.8 asked once, 33.1 unanimous of three, 34.3 by majority -- so
    # unanimity was costing about 5% of everything reaching `build` and
    # majority recovers two thirds of that. The rows it moves are genuinely
    # borderline: three sat at one or two keeps of five, two at three, one at
    # four, and majority keeps the last three while dropping the first three.
    #
    # Unanimity is still right where the question is whether a task can be
    # SCORED -- calibration and the controls -- because there a task we cannot
    # prove is measurable should not count. `stable()` and `controlled()` keep
    # it (D-34).
    #
    # An even number of readings that ties has no majority, and ties the
    # conservative way: half is not most.
    kept = sum(1 for v in values if v is keep_on)
    held = kept * 2 > len(values)
    # The answer handed back is the one that explains the verdict, not simply
    # the last one asked. Where the gate did not hold, that is the first
    # reading that broke it -- so a caller reading `.reasoning` off it gets the
    # reason the row was rejected, and a caller branching on its verdict field
    # branches the same way this function did. Handing back the last reading
    # instead meant a conversation three readings called leaking, clean, clean
    # was recorded as leaking and then never repaired, because the repair
    # branch asked the clean one.
    # The answer handed back is one that voted with the verdict, so a caller
    # reading `.reasoning` off it gets a reason for the decision that was
    # actually made rather than for the minority's.
    deciding = answers[values.index(keep_on if held else not keep_on)]
    return (keep_on if held else not keep_on,
            f"{sum(1 for v in values if v is keep_on)}/{len(values)}",
            deciding)


def gate_view(session_id: str, turns: list[dict]) -> list[dict]:
    """The conversation a task built from this session shows its candidate, before any redaction (G-81).

    ``turns`` with their lost calls already put back (`recovered`); here the
    agent's lost text, without thinking (G-79), and each result whole under
    record 3 -- what `attempt.candidate_turns` shows a task built with them.
    Every gate that reads the conversation reads this, `rescreen_scope` too.
    """
    from ..corpus.recover import whole_results, with_text
    from ..corpus.turns import RECORD

    view = with_text(session_id, turns)
    return whole_results(session_id, view) if RECORD >= 3 else view


def screened_view(row: dict, turns: list[dict]) -> list[dict]:
    """What a screened row's task shows its candidate: `gate_view`, with the row's own repair applied.

    The re-screen scripts read a repaired row on this: on the conversation
    before its repair, the request they sent was the removed turn itself in 4
    of the 6 repaired rows of one run (09-30 review).
    """
    from ..find.redact import apply, turn_number

    view = gate_view(row["session_id"], turns)
    if row.get("redacted_turns") or row.get("rewritten_turns"):
        view = apply(view, row.get("redacted_turns") or [],
                     {turn_number(k): v for k, v in (row.get("rewritten_turns") or {}).items()})
    return view


async def stage_screen(paths: Paths, limit: int, concurrency: int, passes: int = 1) -> Progress:
    """Check each conversation is answerable, and repair it if it leaks."""
    from ..find.answerable import ANSWERABLE_GATE, agent_message_before, asks_for_something
    from ..construct.build import last_user_message
    from ..find.leakage import signals_trouble
    from ..corpus.turns import RECORD, RECORD_CHARS, build_excerpt, load_session_turns
    from ..corpus.recover import has_transcript, recovered
    from ..find.redact import apply, carried_by, carrying, survey
    from ..find.scope import SCOPE_GATE, in_scope
    from ..llm import metering, model_name, refusal

    p = Progress("screen")
    t0 = time.monotonic()
    rows = [r for r in load(paths.signatures) if r.get("kind")]
    # The pass count is part of what makes a screened row done. Without it,
    # `--only screen --passes 5` over a directory screened at one pass reported
    # "51 already done" and changed nothing, while the user believed the
    # stricter unanimity rule had been applied. A row screened at fewer passes
    # than asked for is re-screened; one screened at more is left alone.
    # And a row screened before the gates read the candidate's view (G-81), which
    # carries no `text_recovered`, is screened again: the build refuses it.
    done = {
        key_of(r): r.get("screen_passes", 1) for r in completed(paths.screened) if "text_recovered" in r
    }
    due = [r for r in rows if done.get(key_of(r), 0) < passes]
    # Only when asked: each such row is read again whole, at the gates' price,
    # and resuming an old run with this code re-screened all of them without a
    # word (51 of 51 and 43 of 43 rows on two copies, 09-30 review).
    old = {key_of(r) for r in completed(paths.screened) if "text_recovered" not in r}
    stale = [r for r in due if key_of(r) in old]
    if stale and os.environ.get("ERRATA_RESCREEN_OLD") != "1":
        p.notes.append(f"{len(stale)} rows screened before the gates read the candidate's view (G-81) are left as "
                       f"they are: screening them again reads each whole conversation, so it waits for "
                       f"--rescreen-old, and the build refuses them until then")
        due = [r for r in due if key_of(r) not in old]
    todo = p.cap(due, limit, len(rows))

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
            # A reading of the candidate's view (G-81) outranks one of the table's,
            # whatever the pass counts: a row screened 3 times before outranked a
            # new one screened once, so a re-screen paid for every row and kept
            # none of it, run after run (09-30 review, after this was removed as
            # unreachable on reasoning about equal pass counts only).
            return (0 if row.get("error") else 1, "text_recovered" in row, row.get("screen_passes", 1))

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

    turns = recovered(load_session_turns({r["session_id"] for r in todo}))

    async def one(r):
        out = dict(r)
        # Which model screened, for the price of what it read.
        out["screen_model"] = model_name()
        try:
            ts = turns[r["session_id"]]
            # The conversation as its candidate will be shown it (G-81): the calls
            # and the agent's text the table lost put back (G-76, G-79), no
            # thinking, each result whole. Every gate below reads this: they read
            # record 1 -- 60,000 characters, each message cut at 4,000 -- while
            # v1's candidates read record 3 whole, so on 25 of its 55 tasks part
            # of what a candidate saw was never screened for a leak.
            # `candidate_turns` builds the same view.
            view = gate_view(r["session_id"], ts)
            # Whether these gates read the record with SWE-chat's lost calls and
            # the agent's lost text put back (G-76, G-79). The build shows them
            # to a candidate only if so, so the candidate never sees a call or a
            # word the leak gate did not. On every row, one a gate could not
            # finish included: its absence marks a row screened before (G-81).
            out["calls_recovered"] = has_transcript(r["session_id"])
            out["text_recovered"] = has_transcript(r["session_id"])
            message = last_user_message(ts, r["cut"])
            before = ""
            if message is None:
                out["asks_for_something"] = False
                out["request_reason"] = "no user message before the cut"
            else:
                # Read after the agent's last message, as the candidate sees it:
                # a bare "yes" answers it (B-253).
                before = agent_message_before(view, message)
                verdict, tally, a = await _agree(
                    lambda: asks_for_something(message.get("content") or "", before=before),
                    passes, keep_on=True,
                    reading=lambda x: x.asks_for_something)
                out["asks_for_something"] = verdict
                out["asks_for_something_held"] = tally
                out["request_reason"] = a.request or a.reasoning
                out["answerable_gate"] = ANSWERABLE_GATE

            # Is the defect even reachable from what was asked? nsega-mcp-todoist
            # asked "create the pull request" and its defect is a linter version
            # in a CI workflow; three candidates reported the pull request, the
            # only sensible answer, and all three were scored off_target.
            request = (message or {}).get("content") or ""
            # The conversation the agent had, which both gates below read: a
            # bare "yes" asks for whatever the agent had just proposed (B-252).
            excerpt = build_excerpt(view, r["cut"], max_chars=RECORD_CHARS, record=RECORD)
            if request:
                verdict, tally, scope = await _agree(
                    lambda: in_scope(request, r.get("defect", ""), conversation=excerpt), passes, keep_on=True,
                    reading=lambda x: x.within_scope)
                out["within_scope"] = verdict
                out["within_scope_held"] = tally
                out["scope_reason"] = scope.reason
                out["scope_gate"] = SCOPE_GATE
            else:
                out["within_scope"] = False
                out["scope_reason"] = "no request to judge scope against"

            # Leaking is the rejecting answer, so one reading saying so is enough.
            verdict, tally, leak = await _agree(
                lambda: signals_trouble(excerpt), passes, keep_on=False,
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
                out["leak_carried_by"] = carried_by(view, r["cut"], leak.quote)
            if verdict and out["leak_carried_by"] == "elsewhere":
                out["redaction_outcome"] = (
                    "not attempted: the words that leak are in a tool call, a tool "
                    "result or the agent's thinking, which removing prose cannot reach"
                )
            elif verdict:
                # The surveyor reads the same view, so it can name a text put back
                # from the transcript (a fractional turn), and is always shown the
                # turns that carry the gate's quote, however far back they are.
                asked = (message or {}).get("turn_number")
                red = await _once_more(lambda: survey(view, r["cut"], must_show=carrying(view, r["cut"], leak.quote),
                                                      request_turn=asked))
                out["diffuse"] = red.diffuse
                out["redaction_reason"] = red.reason
                out["redaction_touched"] = red.touched
                out["redaction_outcome"] = (
                    "not repairable: the surveyor found the signal in no single turn"
                    if red.diffuse else
                    "not repairable: the surveyor found no turn to edit"
                    if not red.touched else "attempted"
                )
                if red.repairable and asked is not None and asked in red.removed_turns:
                    # A repair that drops the request leaves the candidate nothing to
                    # answer: in 5 of v1's 7 redactions the turn dropped was the
                    # request itself, and its reference answers answer it (09-30).
                    out["redaction_outcome"] = (
                        "not repairable: the leak is the developer's request itself, which the candidate must answer")
                elif red.repairable:
                    # Checked again on the view the candidate would be shown.
                    kept = apply(view, red.removed_turns, red.rewritten)
                    # As often as the leak was asked: a repair accepted on one reading
                    # while the leak took three was the weaker test (09-30 review).
                    repaired_view = build_excerpt(kept, r["cut"], max_chars=RECORD_CHARS, record=RECORD)
                    leaks, recheck_held, again = await _agree(
                        lambda: signals_trouble(repaired_view), passes, keep_on=False,
                        reading=lambda x: x.signals_trouble)
                    out["redaction_recheck_held"] = recheck_held
                    out["redaction_outcome"] = (
                        "repaired" if not leaks
                        else f"still leaks after editing turns {red.touched}: {again.reasoning}"
                    )
                    if not leaks:
                        out["redacted_turns"] = red.removed_turns
                        out["rewritten_turns"] = {
                            str(k): v for k, v in red.rewritten.items()
                        }
                        out["redaction_worked"] = True
                        # And the request and its scope, as the repaired conversation
                        # shows them: a rewrite of the request, or a removed message a
                        # bare "yes" answered, changes what the candidate is asked, and
                        # the scope gate reads a conversation the repair changed.
                        asked_now = last_user_message(kept, r["cut"])
                        request_now = (asked_now or {}).get("content") or ""
                        before_now = agent_message_before(kept, asked_now) if asked_now else ""
                        if (request_now, before_now) != (request, before):
                            verdict, tally, a = await _agree(
                                lambda: asks_for_something(request_now, before=before_now), passes, keep_on=True,
                                reading=lambda x: x.asks_for_something)
                            out["asks_for_something"] = verdict
                            out["asks_for_something_held"] = tally
                            out["request_reason"] = a.request or a.reasoning
                        if request_now:
                            conversation_now = build_excerpt(kept, r["cut"], max_chars=RECORD_CHARS, record=RECORD)
                            verdict, tally, scope = await _agree(
                                lambda: in_scope(request_now, r.get("defect", ""), conversation=conversation_now),
                                passes, keep_on=True, reading=lambda x: x.within_scope)
                            out["within_scope"] = verdict
                            out["within_scope_held"] = tally
                            out["scope_reason"] = scope.reason
                        out["gated_after_repair"] = True
            out["screen_passes"] = passes
            append_used(paths.screened, out)
            return True
        except Exception as e:
            # A conversation the gate's model refuses for good -- longer than it
            # reads, or caught by its content filter: recorded, not an error retried
            # on every run. The gates read it whole (G-81) and cannot be shown less
            # than the candidate, so the build sets it aside.
            refused = refusal(f"{type(e).__name__}: {e}")
            if refused:
                out["provider_refused"] = f"{refused}: {type(e).__name__}: {e}"[:300]
                out["screen_passes"] = passes
                append_used(paths.screened, out)
                return True
            out["error"] = f"{type(e).__name__}: {e}"
            out["screen_passes"] = passes
            append_used(paths.screened, out)
            return False

    results = await _gather([metering(one(r)) for r in todo], concurrency)
    p.produced = sum(1 for r in results if r)
    p.failed = sum(1 for r in results if not r)
    prune()
    p.took_s = time.monotonic() - t0
    return p


