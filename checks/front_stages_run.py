"""The five stages that read the corpus, run end to end with the model faked.

Everything before `build` had no test at all. The check suites cover attempt,
grade, build and report, and stub the corpus so they need none of it -- which
is exactly why the 09-20 restructure broke `stage_triage` and `stage_locate`
(B-212, a deferred import pointed at the wrong module) and `CORPUS` itself
(B-213, a path built by counting parent directories) with every suite passing.

So this runs moments -> triage -> read -> locate -> signature -> screen against
fake readers and a fake corpus, and asserts a row survives all five and arrives
in screened.jsonl with the fields `build` needs. No network, no Docker, no
model calls.

What it covers and what it does not, checked by putting both bugs back:
B-212 turns it red, because a deferred import resolves when the stage runs.
B-213 does NOT, and cannot -- this check stubs `load_session_turns` precisely
so it needs no corpus, so it never touches `CORPUS`. That one belongs to
`checks/imports_resolve.py`, which asserts the corpus is where the code looks.
Neither check subsumes the other.

Two rules it follows, both learned the hard way here:

  - the fakes return the REAL pydantic models. A fake shaped unlike the thing
    it stands for tests the fake: `_agree`'s guard passed for weeks against a
    stand-in carrying a `.value` no real gate has, while all three screening
    gates were constants in production.
  - every stub asserts it was actually called. After the restructure, a name
    bound at import time is not patched by replacing it on its source module,
    so a stub can silently never run and the test still passes (B-151).

    .venv/bin/python checks/front_stages_run.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import errata_bench.corpus.turns as turns_mod
import errata_bench.find.answerable as answerable_mod
import errata_bench.find.leakage as leakage_mod
import errata_bench.find.reading as reading_mod
import errata_bench.find.scope as scope_mod
import errata_bench.find.signature as signature_mod
import errata_bench.find.trajectory as trajectory_mod
import errata_bench.find.triage as triage_mod
from errata_bench.find.answerable import Answerable
from errata_bench.find.leakage import Leakage
from errata_bench.find.reading import Reading
from errata_bench.find.scope import Scope
from errata_bench.find.signature import Signature
from errata_bench.find.trajectory import Trajectory
from errata_bench.find.triage import Triage
from errata_bench.stages import (
    stage_locate, stage_read, stage_screen, stage_signature, stage_triage,
)
from errata_bench.store import Paths, append, load

FAIL = []
CALLED = {}


def check(ok, message):
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        FAIL.append(message)


def records(name):
    """Mark a stub as reached, so a stub that never runs cannot pass quietly."""
    CALLED[name] = CALLED.get(name, 0) + 1


# ---- a session, as the corpus stores one ---------------------------------
# Turn numbers are sparse, as in the corpus. With the readers faked, the one
# real function that sees them is `last_user_message`, and what the sparseness
# exercises there is a cut (5) that is not itself a turn number. The text is
# non-ASCII because real replies are.
SESSION = [
    {"turn_number": 1, "turn_type": "user_prompt",
     "content": "Add retry with backoff to the uploader — it drops on 503."},
    {"turn_number": 2, "turn_type": "assistant_response", "content": "I'll add it."},
    {"turn_number": 4, "turn_type": "tool_use", "tool_name": "Read",
     "content": json.dumps({"file_path": "/Users/d/code/up/src/upload.py"})},
    {"turn_number": 6, "turn_type": "assistant_response",
     "content": "Done — retries are in and the tests pass."},
    {"turn_number": 7, "turn_type": "user_prompt",
     "content": "no, you never checked whether the backoff fires at all"},
    {"turn_number": 9, "turn_type": "assistant_response",
     "content": "You're right. I added the sleep and verified it."},
]


# Two more, for section 5: one where the words that leak sit in a tool result,
# and one where the developer says them.
SESSION_TOOL = SESSION[:3] + [
    {"turn_number": 5, "turn_type": "tool_result",
     "content": "File has not been read yet. Read it first before writing to it."},
] + SESSION[3:]
SESSION_PROSE = SESSION[:2] + [
    {"turn_number": 3, "turn_type": "user_prompt",
     "content": "you keep getting this wrong — it still drops on 503"},
] + SESSION[2:]
SESSIONS = {"s-tool": SESSION_TOOL, "s-prose": SESSION_PROSE, "s-stuck": SESSION_PROSE}


def fake_load_session_turns(ids):
    records("load_session_turns")
    return {sid: list(SESSIONS.get(sid, SESSION)) for sid in ids}


def fake_build_excerpt(turns, cut, **kw):
    records("build_excerpt")
    return "\n".join(f"[turn {t['turn_number']}] {t.get('content','')}"
                     for t in turns if t["turn_number"] <= cut)


async def fake_triage(excerpt, **kw):
    records("triage")
    return Triage(worth_reading=True, agent_has_acted=True,
                  objects_to_that_work=True, reason="the agent claimed a check it skipped")


async def fake_read_pushback(turns, turn, **kw):
    records("read_pushback")
    return Reading(
        what_user_asked="retry with backoff",
        what_agent_did="added retries and said the tests pass",
        what_user_objected_to="it never checked the backoff fires",
        objection_kind="unverified_claim",
        benchmark_viable=True,
        context_sufficient=True,
    )


async def fake_locate(turns, turn, **kw):
    records("locate")
    return Trajectory(
        request_turn=1, failed_turn=6, complaint_turn=7,
        defect="the backoff is never exercised", resolved=True,
        later_turns_are_new_work=False, resolved_turn=9,
        resolution="added the sleep and verified it", rounds=1,
    )


async def fake_derive(defect, resolution, *, repo_id="", **kw):
    records("derive")
    return Signature(kind="present", reasoning="a literal the tree still holds",
                     token="time.sleep", path="src/upload.py")


async def fake_asks(message, **kw):
    records("asks_for_something")
    return Answerable(asks_for_something=True, request=message[:40], reasoning="a request")


async def fake_in_scope(request, defect, **kw):
    records("in_scope")
    return Scope(within_scope=True, reason="the defect is inside the requested work")


async def fake_signals_trouble(excerpt, **kw):
    records("signals_trouble")
    return Leakage(signals_trouble=False, quote="", reasoning="nothing is given away")


def main() -> int:
    turns_mod.load_session_turns = fake_load_session_turns
    turns_mod.build_excerpt = fake_build_excerpt
    triage_mod.triage = fake_triage
    reading_mod.read_pushback = fake_read_pushback
    trajectory_mod.locate = fake_locate
    signature_mod.derive = fake_derive
    answerable_mod.asks_for_something = fake_asks
    scope_mod.in_scope = fake_in_scope
    leakage_mod.signals_trouble = fake_signals_trouble

    p = Paths(Path(tempfile.mkdtemp()) / "run")
    append(p.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                       "kind": "correction", "agent_turns_before": 4})

    print("\n1. a moment survives all five stages")
    for stage, out, label in (
        (stage_triage, p.triaged, "triage"),
        (stage_read, p.readings, "read"),
        (stage_locate, p.trajectories, "locate"),
        (stage_signature, p.signatures, "signature"),
        (stage_screen, p.screened, "screen"),
    ):
        # Caught, not raised: a stage that cannot even start -- a deferred
        # import pointing at the wrong module, which is B-212 -- should read as
        # this stage failing, not as the whole check dying at the first one.
        try:
            asyncio.run(stage(p, 10**9, concurrency=2))
        except Exception as e:
            check(False, f"{label}: raised {type(e).__name__}: {str(e)[:80]}")
            continue
        rows = load(out)
        bad = [r for r in rows if r.get("error")]
        check(len(rows) == 1 and not bad,
              f"{label}: {len(rows)} row, {len(bad)} errored"
              + (f" -- {bad[0]['error'][:70]}" if bad else ""))
        # Not every stage marks failure with an `error` key. triage's except
        # branch writes worth_reading=True and triage_reason="error: ...", so
        # the moment flows on; locate's writes usable=False, reason="error:".
        # A raise inserted right after each stage's model call left both
        # "1 row, 0 errored" and the whole check green.
        if rows and label == "triage":
            check(not str(rows[0].get("triage_reason", "")).startswith("error:"),
                  f"triage did not hide a failure behind worth_reading: {rows[0].get('triage_reason', '')[:50]!r}")
        if rows and label == "locate":
            check(rows[0].get("usable") is True,
                  f"locate produced a usable trajectory: {rows[0].get('reason', '')[:50]!r}")

    print("\n2. every fake was actually reached")
    # Without this the stages could be skipping the work entirely and each
    # assertion above would still hold.
    for name in ("load_session_turns", "build_excerpt", "triage", "read_pushback",
                 "locate", "derive", "asks_for_something", "in_scope", "signals_trouble"):
        check(CALLED.get(name, 0) > 0, f"{name} ran ({CALLED.get(name, 0)}x)")

    print("\n3. the screened row carries what build needs")
    screened = load(p.screened)
    if not screened:
        check(False, "no screened row to inspect -- an earlier stage failed")
        print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
        for f in FAIL:
            print("  -", f)
        return 1
    row = screened[0]
    for field in ("session_id", "cut", "kind", "defect", "resolution",
                  "asks_for_something", "within_scope", "signals_trouble",
                  "redaction_worked", "screen_passes"):
        check(field in row, f"{field} is on the row -> {row.get(field)!r}")
    check(row["asks_for_something"] is True and row["within_scope"] is True
          and row["signals_trouble"] is False,
          "and the three gate verdicts are the ones the fakes gave")

    print("\n4. the gates are asked --passes times and answer conservatively")
    # A gate that changes its mind keeps the row out. This is the behaviour
    # `_agree` exists for, exercised through the real stage rather than alone.
    flips = {"n": 0}

    async def sometimes_out_of_scope(request, defect, **kw):
        records("in_scope")
        flips["n"] += 1
        return Scope(within_scope=flips["n"] != 2, reason="changed its mind")

    q = Paths(Path(tempfile.mkdtemp()) / "run")
    append(q.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                       "kind": "correction", "agent_turns_before": 4})
    try:
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(q, 10**9, concurrency=1))
        scope_mod.in_scope = sometimes_out_of_scope
        asyncio.run(stage_screen(q, 10**9, concurrency=1, passes=3))
    except Exception as e:
        # Caught for the same reason section 1 catches: the stage failure this
        # section exists to notice would otherwise abort the script before its
        # own assertion ran, and an aborted script reports nothing.
        check(False, f"a stage raised before the gates could be read: {type(e).__name__}: {e}")
        print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
        for f in FAIL:
            print("  -", f)
        return 1
    rows = load(q.screened)
    check(len(rows) == 1 and not rows[0].get("error"),
          f"the row screened without error: {rows[0].get('error') if rows else 'no rows'}")
    row = rows[0]
    check(row["within_scope"] is False and row.get("within_scope_held") == "2/3",
          f"one 'out of scope' in three keeps the row out: "
          f"{row.get('within_scope')} held {row.get('within_scope_held')}")

    print("\n5. a leak says where it is carried, and a repair says how it ended")
    # G-56 / G-45. Fourteen rows leaked across the stored runs, none was
    # repaired, and not one row said why. The four distinct ones all leak
    # through tool output, which the surveyor is never shown.
    import errata_bench.find.redact as redact_mod
    from errata_bench.find.redact import Redaction

    surveyed = []

    async def fake_survey(turns, cut, **kw):
        records("survey")
        surveyed.append(cut)
        return Redaction(removed_turns=[3], reason="turn 3 is a complaint", quotes={3: "you keep getting this wrong"})

    async def leaks_where_it_says(excerpt, **kw):
        records("signals_trouble")
        if "File has not been read yet" in excerpt:
            return Leakage(signals_trouble=True, quote="File has not been read  yet. READ it first",
                           reasoning="repeated tool rejections show the agent struggling")
        if "you keep getting this wrong" in excerpt:
            return Leakage(signals_trouble=True, quote="you keep getting this wrong",
                           reasoning="the developer says the agent keeps failing")
        return Leakage(signals_trouble=False, quote="", reasoning="nothing is given away")

    async def always_leaks(excerpt, **kw):
        records("signals_trouble")
        return Leakage(signals_trouble=True, quote="you keep getting this wrong",
                       reasoning="it still reads as a conversation going badly")

    scope_mod.in_scope = fake_in_scope
    redact_mod.survey = fake_survey
    got = {}
    try:
        for sid, gate in (("s-tool", leaks_where_it_says), ("s-prose", leaks_where_it_says),
                          ("s-stuck", always_leaks)):
            d = Paths(Path(tempfile.mkdtemp()) / "run")
            append(d.moments, {"session_id": sid, "turn_number": 7, "repo_id": "acme/up",
                               "kind": "correction", "agent_turns_before": 4})
            for stage in (stage_triage, stage_read, stage_locate, stage_signature):
                asyncio.run(stage(d, 10**9, concurrency=1))
            leakage_mod.signals_trouble = gate
            before = len(surveyed)
            asyncio.run(stage_screen(d, 10**9, concurrency=1))
            rows = load(d.screened)
            got[sid] = (rows[0] if rows else {}, len(surveyed) - before)
    except Exception as e:
        check(False, f"a stage raised before the rows could be read: {type(e).__name__}: {e}")
        got = {}
    finally:
        leakage_mod.signals_trouble = fake_signals_trouble
    if got:
        row, asked = got["s-tool"]
        check(row.get("signals_trouble") is True and row.get("leak_carried_by") == "elsewhere" and asked == 0
              and str(row.get("redaction_outcome", "")).startswith("not attempted") and not row.get("error"),
              f"a leak in a tool result is named as one, and no surveyor is paid to miss it: "
              f"{row.get('leak_carried_by')!r}, surveyed {asked}x, {str(row.get('redaction_outcome'))[:40]!r} {row.get('error') or ''}")
        row, asked = got["s-prose"]
        check(row.get("leak_carried_by") == "prose" and asked == 1 and row.get("redaction_worked") is True
              and row.get("redacted_turns") == [3] and row.get("redaction_outcome") == "repaired",
              f"a leak the developer typed is repaired, and the row says so: "
              f"{row.get('leak_carried_by')!r}, turns {row.get('redacted_turns')}, {row.get('redaction_outcome')!r} {row.get('error') or ''}")
        row, asked = got["s-stuck"]
        check(row.get("redaction_worked") is False and asked == 1
              and str(row.get("redaction_outcome", "")).startswith("still leaks after editing turns [3]")
              and row.get("redaction_touched") == [3] and row.get("redaction_reason") == "turn 3 is a complaint",
              f"and one that still leaks afterwards says what was tried: {str(row.get('redaction_outcome'))[:60]!r}")

    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
