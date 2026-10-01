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


# More for section 5: one where the words that leak sit in a tool result, one
# where the developer says them before making the request, and one where they
# are the request itself.
SESSION_TOOL = SESSION[:3] + [
    {"turn_number": 5, "turn_type": "tool_result",
     "content": "File has not been read yet. Read it first before writing to it."},
] + SESSION[3:]
SESSION_PROSE = [
    {"turn_number": 1, "turn_type": "user_prompt",
     "content": "you keep getting this wrong — it still drops on 503"},
    {"turn_number": 2, "turn_type": "assistant_response", "content": "Sorry. Starting again."},
    {"turn_number": 3, "turn_type": "user_prompt",
     "content": "Add retry with backoff to the uploader — it drops on 503."},
] + SESSION[2:]
SESSION_ASKED = SESSION[:2] + [
    {"turn_number": 3, "turn_type": "user_prompt",
     "content": "you keep getting this wrong — it still drops on 503"},
] + SESSION[2:]
SESSIONS = {"s-tool": SESSION_TOOL, "s-prose": SESSION_PROSE, "s-stuck": SESSION_PROSE, "s-asked": SESSION_ASKED}


def fake_load_session_turns(ids):
    records("load_session_turns")
    return {sid: list(SESSIONS.get(sid, SESSION)) for sid in ids}


# How each excerpt was asked for, so a gate read at the wrong record is seen.
EXCERPT_KW: list = []


def fake_build_excerpt(turns, cut, **kw):
    records("build_excerpt")
    EXCERPT_KW.append(kw)
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
    SEEN_ASKS["kwargs"] = dict(kw)
    return Answerable(asks_for_something=True, request=message[:40], reasoning="a request")


# What the answerable gate was handed, for section 3 (B-253).
SEEN_ASKS: dict = {}


# What the scope and leak gates were handed, for section 3 (B-252).
SEEN: dict = {}


async def fake_in_scope(request, defect, **kw):
    records("in_scope")
    SEEN["scope_conversation"] = kw.get("conversation")
    return Scope(within_scope=True, reason="the defect is inside the requested work")


async def fake_signals_trouble(excerpt, **kw):
    records("signals_trouble")
    SEEN["leak_conversation"] = excerpt
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
                  "redaction_worked", "screen_passes", "calls_recovered", "text_recovered"):
        check(field in row, f"{field} is on the row -> {row.get(field)!r}")
    check(row["calls_recovered"] is False,
          "and a session with no raw transcript here says its lost calls were not put back")
    check(row["asks_for_something"] is True and row["within_scope"] is True
          and row["signals_trouble"] is False,
          "and the three gate verdicts are the ones the fakes gave")
    # B-252: the scope gate read only the developer's last message, so a bare
    # "yes" to the agent's proposal asked for nothing. It now reads the same
    # conversation the leak gate does, and the row says which gate judged it.
    check(bool(SEEN.get("scope_conversation"))
          and SEEN.get("scope_conversation") == SEEN.get("leak_conversation"),
          f"the scope gate is handed the conversation the leak gate reads: "
          f"{str(SEEN.get('scope_conversation'))[:40]!r}")
    check(row.get("scope_gate") == 2, f"and the row records the scope gate's version: {row.get('scope_gate')!r}")
    # B-253: the answerable gate read the developer's message alone, and a bare
    # "yes" to the agent's proposal asked for nothing. It is now handed the
    # agent's message before it -- here there is none, the request being the
    # conversation's first message -- and the row says which gate judged it.
    check(isinstance((SEEN_ASKS.get("kwargs") or {}).get("before"), str),
          f"the answerable gate is handed the agent's message before the request: {SEEN_ASKS.get('kwargs')}")
    check(row.get("answerable_gate") == 2, f"and the row records its version: {row.get('answerable_gate')!r}")

    print("\n4. the gates are asked --passes times and settled by majority")
    # A gate that changes its mind is settled by what most of its readings say,
    # not by whichever way is more cautious. This is the behaviour `_agree`
    # exists for, exercised through the real stage rather than alone. It used
    # to require unanimity, which does not reduce a gate's noise -- it moves
    # the mean toward rejection, so asking more times could only remove rows
    # (D-34, measured in R-30).
    flips = {"n": 0}

    async def out_of_scope_once(request, defect, **kw):
        records("in_scope")
        flips["n"] += 1
        return Scope(within_scope=flips["n"] != 2, reason="changed its mind")

    async def out_of_scope_twice(request, defect, **kw):
        records("in_scope")
        flips["n"] += 1
        return Scope(within_scope=flips["n"] == 1, reason="changed its mind")

    q = Paths(Path(tempfile.mkdtemp()) / "run")
    append(q.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                       "kind": "correction", "agent_turns_before": 4})
    try:
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(q, 10**9, concurrency=1))
        scope_mod.in_scope = out_of_scope_once
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
    check(row["within_scope"] is True and row.get("within_scope_held") == "2/3",
          f"one 'out of scope' in three does not keep the row out, and the tally says "
          f"it was not unanimous: {row.get('within_scope')} held {row.get('within_scope_held')}")

    # Two of three does.
    flips["n"] = 0
    q2 = Paths(Path(tempfile.mkdtemp()) / "run")
    append(q2.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                        "kind": "correction", "agent_turns_before": 4})
    for stage in (stage_triage, stage_read, stage_locate, stage_signature):
        asyncio.run(stage(q2, 10**9, concurrency=1))
    scope_mod.in_scope = out_of_scope_twice
    asyncio.run(stage_screen(q2, 10**9, concurrency=1, passes=3))
    scope_mod.in_scope = fake_in_scope
    row2 = load(q2.screened)[0]
    check(row2["within_scope"] is False and row2.get("within_scope_held") == "1/3",
          f"but two of three does: {row2.get('within_scope')} held {row2.get('within_scope_held')}")

    print("\n5. a leak says where it is carried, and a repair says how it ended")
    # G-56 / G-45. Fourteen rows leaked across the stored runs, none was
    # repaired, and not one row said why. The four distinct ones all leak
    # through tool output, which the surveyor is never shown.
    import errata_bench.find.redact as redact_mod
    from errata_bench.find.redact import Redaction

    surveyed = []

    async def fake_survey(turns, cut, **kw):
        records("survey")
        surveyed.append((cut, kw))
        at = next((t["turn_number"] for t in turns if "you keep getting this wrong" in (t.get("content") or "")), 3)
        return Redaction(removed_turns=[at], reason=f"turn {at} is a complaint",
                         quotes={at: "you keep getting this wrong"})

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
                          ("s-stuck", always_leaks), ("s-asked", leaks_where_it_says)):
            d = Paths(Path(tempfile.mkdtemp()) / "run")
            append(d.moments, {"session_id": sid, "turn_number": 7, "repo_id": "acme/up",
                               "kind": "correction", "agent_turns_before": 4})
            for stage in (stage_triage, stage_read, stage_locate, stage_signature):
                asyncio.run(stage(d, 10**9, concurrency=1))
            leakage_mod.signals_trouble = gate
            before, gated = len(surveyed), CALLED.get("signals_trouble", 0)
            asyncio.run(stage_screen(d, 10**9, concurrency=1))
            rows = load(d.screened)
            got[sid] = (rows[0] if rows else {}, len(surveyed) - before, surveyed[before:],
                        CALLED.get("signals_trouble", 0) - gated)
    except Exception as e:
        check(False, f"a stage raised before the rows could be read: {type(e).__name__}: {e}")
        got = {}
    finally:
        leakage_mod.signals_trouble = fake_signals_trouble
    if got:
        row, asked, _, _ = got["s-tool"]
        check(row.get("signals_trouble") is True and row.get("leak_carried_by") == "elsewhere" and asked == 0
              and str(row.get("redaction_outcome", "")).startswith("not attempted") and not row.get("error"),
              f"a leak in a tool result is named as one, and no surveyor is paid to miss it: "
              f"{row.get('leak_carried_by')!r}, surveyed {asked}x, {str(row.get('redaction_outcome'))[:40]!r} {row.get('error') or ''}")
        row, asked, calls, _ = got["s-prose"]
        check(row.get("leak_carried_by") == "prose" and asked == 1 and row.get("redaction_worked") is True
              and row.get("redacted_turns") == [1] and row.get("redaction_outcome") == "repaired",
              f"a leak the developer typed is repaired, and the row says so: "
              f"{row.get('leak_carried_by')!r}, turns {row.get('redacted_turns')}, {row.get('redaction_outcome')!r} {row.get('error') or ''}")
        told = calls[0][1] if calls else {}
        check(told.get("request_turn") == 3 and set(told.get("must_show") or ()) == {1},
              f"and the surveyor is told which turn is the request, and shown the turn that carries the quote: "
              f"request_turn {told.get('request_turn')!r}, must_show {sorted(told.get('must_show') or ())}")
        row, asked, _, _ = got["s-stuck"]
        check(row.get("redaction_worked") is False and asked == 1
              and str(row.get("redaction_outcome", "")).startswith("still leaks after editing turns [1]")
              and row.get("redaction_touched") == [1] and row.get("redaction_reason") == "turn 1 is a complaint",
              f"and one that still leaks afterwards says what was tried: {str(row.get('redaction_outcome'))[:60]!r}")
        # 09-30: in 5 of v1's 7 redactions the turn removed was the developer's
        # request itself, which the task's reference answers answer. A repair
        # that removes the request is refused, and nothing is paid to re-check it.
        row, asked, _, gated = got["s-asked"]
        check(row.get("redaction_worked") is False and asked == 1 and not row.get("redacted_turns")
              and str(row.get("redaction_outcome", "")).startswith(
                  "not repairable: the leak is the developer's request itself")
              and gated == got["s-prose"][3] - 1 and not row.get("error"),
              f"and a repair that would remove the developer's request is refused, not taken or re-checked: "
              f"{str(row.get('redaction_outcome'))[:70]!r}, turns {row.get('redacted_turns')}, leak gate asked "
              f"{gated}x against {got['s-prose'][3]}x for a repair {row.get('error') or ''}")

    # A row screened before the gates read the candidate's view carries no
    # `text_recovered`, and the build refuses it: the next run screens it again,
    # and its new reading replaces the old (09-30 review: "screen it again" was a
    # dead end, since a completed row at the same passes counted as done).
    d = Paths(Path(tempfile.mkdtemp()) / "run")
    append(d.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                       "kind": "correction", "agent_turns_before": 4})
    for stage in (stage_triage, stage_read, stage_locate, stage_signature, stage_screen):
        asyncio.run(stage(d, 10**9, concurrency=1))
    old = [{k: v for k, v in r.items() if k != "text_recovered"} for r in load(d.screened)]
    d.screened.write_text("".join(json.dumps(r) + "\n" for r in old))
    asked_before = CALLED.get("signals_trouble", 0)
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    again_rows = load(d.screened)
    asked_again = CALLED.get("signals_trouble", 0) - asked_before
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    check(len(old) == 1 and asked_again == 1 and len(again_rows) == 1 and "text_recovered" in again_rows[0]
          and CALLED.get("signals_trouble", 0) - asked_before == 1,
          f"a row screened before the gates read the candidate's view is screened again, once, and replaced: "
          f"gates asked {asked_again}x, {len(again_rows)} row(s), flag {'text_recovered' in (again_rows or [{}])[0]}")

    # A repair that rewrites the request changes what the candidate is asked: the
    # request and scope gates are asked again on the repaired conversation, not
    # left with their reading of the words the repair took out (09-30 review).
    async def rewrites_request(turns, cut, **kw):
        records("survey")
        return Redaction(rewritten={3: "it still drops on 503"}, reason="the complaint, taken out of the request",
                         quotes={3: "you keep getting this wrong"})

    async def asks_if_it_complains(message, **kw):
        records("asks_for_something")
        return Answerable(asks_for_something="wrong" in message, request=message[:40], reasoning="stand-in")

    kept_gates = (redact_mod.survey, answerable_mod.asks_for_something)
    redact_mod.survey, answerable_mod.asks_for_something = rewrites_request, asks_if_it_complains
    rewritten = {}
    try:
        d = Paths(Path(tempfile.mkdtemp()) / "run")
        append(d.moments, {"session_id": "s-asked", "turn_number": 7, "repo_id": "acme/up",
                           "kind": "correction", "agent_turns_before": 4})
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(d, 10**9, concurrency=1))
        leakage_mod.signals_trouble = leaks_where_it_says
        SEEN.pop("scope_conversation", None)
        asyncio.run(stage_screen(d, 10**9, concurrency=1))
        rewritten = (load(d.screened) or [{}])[0]
    except Exception as e:
        check(False, f"a stage raised: {type(e).__name__}: {e}")
    finally:
        redact_mod.survey, answerable_mod.asks_for_something = kept_gates
        leakage_mod.signals_trouble = fake_signals_trouble
    scoped = SEEN.get("scope_conversation") or ""
    check(rewritten.get("redaction_outcome") == "repaired" and rewritten.get("rewritten_turns") == {"3": "it still drops on 503"}
          and rewritten.get("asks_for_something") is False and rewritten.get("gated_after_repair") is True
          and "it still drops on 503" in scoped and "you keep getting this wrong" not in scoped,
          f"a repair that rewrites the request is followed by the request and scope gates, asked again on the "
          f"repaired conversation: {rewritten.get('redaction_outcome')!r}, asks {rewritten.get('asks_for_something')}, "
          f"scope read the rewrite: {'it still drops on 503' in scoped}")

    # G-81. A leak in the agent's text the table lost (G-79) is found where the
    # candidate reads it, and a repair is checked again on that view: removing
    # another turn leaves it in place, which a re-check of the table's turns,
    # which lack it, would take for a repair.
    import errata_bench.corpus.recover as recover5
    dir5 = Path(tempfile.mkdtemp())
    (dir5 / "s-said.jsonl").write_text(json.dumps(
        {"type": "assistant", "isSidechain": False, "message": {"id": "m1", "content": [
            {"type": "text", "text": "Sorry, I got this wrong again. Reading the helper."},
            {"type": "tool_use", "id": "said1", "name": "Read",
             "input": {"file_path": "/Users/d/code/up/src/retry.py"}}]}}) + "\n")
    SESSIONS["s-said"] = SESSION[:2] + [
        {"turn_number": 4, "turn_type": "tool_use", "tool_name": "Read", "tool_call_id": "said1",
         "content": json.dumps({"file_path": "/Users/d/code/up/src/retry.py"})},
        {"turn_number": 5, "turn_type": "tool_result", "tool_call_id": "said1", "content": "def retry(): pass"},
    ] + SESSION[3:]

    read5 = []

    async def leaks_in_its_text(excerpt, **kw):
        records("signals_trouble")
        read5.append(excerpt)
        if "I got this wrong again" in excerpt:
            return Leakage(signals_trouble=True, quote="Sorry, I got this wrong again.",
                           reasoning="the agent apologises for failing again")
        return Leakage(signals_trouble=False, quote="", reasoning="nothing is given away")

    kept5 = recover5.transcript_path
    recover5.transcript_path = lambda sid: dir5 / f"{sid}.jsonl"
    said5 = {}
    try:
        d = Paths(Path(tempfile.mkdtemp()) / "run")
        append(d.moments, {"session_id": "s-said", "turn_number": 7, "repo_id": "acme/up",
                           "kind": "correction", "agent_turns_before": 4})
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(d, 10**9, concurrency=1))
        leakage_mod.signals_trouble = leaks_in_its_text
        EXCERPT_KW.clear()
        asyncio.run(stage_screen(d, 10**9, concurrency=1))
        asked_for5 = list(EXCERPT_KW)
        said5 = (load(d.screened) or [{}])[0]
        # What a task built from this row shows its candidate, with the flags the
        # row carries: the leak gate must have read exactly that. Rendered by this
        # suite's stand-in, as the gate's excerpt was, so the rows are compared.
        from errata_bench.score.attempt import candidate_turns
        from errata_bench.spec import Task
        built5 = Task("t5", "acme/up", "u", "sha", "s-said", said5.get("cut"), 6, 7, 9, "o" * 50, "c" * 50,
                      "d", "present", calls_recovered=bool(said5.get("calls_recovered")),
                      text_recovered=bool(said5.get("text_recovered")))
        shown5 = fake_build_excerpt(candidate_turns(built5, recover5.recover("s-said", SESSIONS["s-said"])),
                                    built5.cut_turn)
    except Exception as e:
        check(False, f"a stage raised before the row could be read: {type(e).__name__}: {e}")
        shown5 = None
    finally:
        recover5.transcript_path = kept5
        leakage_mod.signals_trouble = fake_signals_trouble
    check(said5.get("calls_recovered") is True and said5.get("text_recovered") is True and read5
          and read5[0] == shown5 and "Sorry, I got this wrong again." in (shown5 or ""),
          f"the leak gate reads the rows a task built from its row shows the candidate, and no others: "
          f"{len(read5[0]) if read5 else None} and {len(shown5 or '')} characters as this suite renders them")
    from errata_bench.corpus.turns import RECORD, RECORD_CHARS
    check(len(asked_for5) >= 2 and all(k.get("record") == RECORD and k.get("max_chars") == RECORD_CHARS
                                       for k in asked_for5),
          f"and at the candidate's record and length, the re-check after a repair too: "
          f"{[(k.get('record'), k.get('max_chars')) for k in asked_for5]}")

    # And the surveyor reads that view, so it can name a text put back, and the
    # repair holds where the candidate reads.
    shown5 = {}

    async def finds_the_quote(turns, cut, **kw):
        records("survey")
        shown5["must_show"] = set(kw.get("must_show") or ())
        at = next((t["turn_number"] for t in turns if "I got this wrong again" in (t.get("content") or "")), None)
        return Redaction(removed_turns=[] if at is None else [at], reason="the agent's apology")

    kept5b = (recover5.transcript_path, redact_mod.survey)
    recover5.transcript_path = lambda sid: dir5 / f"{sid}.jsonl"
    redact_mod.survey = finds_the_quote
    found5 = {}
    try:
        d = Paths(Path(tempfile.mkdtemp()) / "run")
        append(d.moments, {"session_id": "s-said", "turn_number": 7, "repo_id": "acme/up",
                           "kind": "correction", "agent_turns_before": 4})
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(d, 10**9, concurrency=1))
        leakage_mod.signals_trouble = leaks_in_its_text
        asyncio.run(stage_screen(d, 10**9, concurrency=1))
        found5 = (load(d.screened) or [{}])[0]
    except Exception as e:
        check(False, f"a stage raised before the row could be read: {type(e).__name__}: {e}")
    finally:
        recover5.transcript_path, redact_mod.survey = kept5b
        leakage_mod.signals_trouble = fake_signals_trouble
    check(found5.get("redaction_outcome") == "repaired" and found5.get("redacted_turns") == [2.5]
          and found5.get("redaction_worked") is True and shown5.get("must_show") == {2.5},
          f"the surveyor is shown the text put back, names it by its own turn, and removing it repairs the row: "
          f"{found5.get('redaction_outcome')!r}, turns {found5.get('redacted_turns')} {found5.get('error') or ''}")

    # The answerable gate reads the developer's request after the agent's last
    # message, and that message can be a text put back: here the agent asked,
    # made a call in the same message, and the developer answered "yes".
    (dir5 / "s-yes.jsonl").write_text(json.dumps(
        {"type": "assistant", "isSidechain": False, "message": {"id": "m1", "content": [
            {"type": "text", "text": "Should I also raise the retry limit to 5?"},
            {"type": "tool_use", "id": "yes1", "name": "Read",
             "input": {"file_path": "/Users/d/code/up/src/retry.py"}}]}}) + "\n")
    SESSIONS["s-yes"] = SESSION[:1] + [
        {"turn_number": 2, "turn_type": "tool_use", "tool_name": "Read", "tool_call_id": "yes1",
         "content": json.dumps({"file_path": "/Users/d/code/up/src/retry.py"})},
        {"turn_number": 3, "turn_type": "tool_result", "tool_call_id": "yes1", "content": "def retry(): pass"},
        {"turn_number": 4, "turn_type": "user_prompt", "content": "yes, do that"},
    ] + SESSION[3:]
    kept5c = recover5.transcript_path
    recover5.transcript_path = lambda sid: dir5 / f"{sid}.jsonl"
    SEEN_ASKS.clear()
    try:
        d = Paths(Path(tempfile.mkdtemp()) / "run")
        append(d.moments, {"session_id": "s-yes", "turn_number": 7, "repo_id": "acme/up",
                           "kind": "correction", "agent_turns_before": 4})
        for stage in (stage_triage, stage_read, stage_locate, stage_signature, stage_screen):
            asyncio.run(stage(d, 10**9, concurrency=1))
    except Exception as e:
        check(False, f"a stage raised before the gate could be read: {type(e).__name__}: {e}")
    finally:
        recover5.transcript_path = kept5c
    check((SEEN_ASKS.get("kwargs") or {}).get("before") == "Should I also raise the retry limit to 5?",
          f"a bare \"yes\" is read after the agent's question, though the table lost it: "
          f"{(SEEN_ASKS.get('kwargs') or {}).get('before')!r}")

    # A conversation longer than the gate's model reads: the gates read it whole
    # and cannot be shown less than the candidate, so the row says so and is not
    # asked again on every run.
    async def refuses_for_length(excerpt, **kw):
        records("signals_trouble")
        raise RuntimeError("Error code: 400 - This model's maximum context length is 128000 tokens.")

    long5 = []
    try:
        d = Paths(Path(tempfile.mkdtemp()) / "run")
        append(d.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                           "kind": "correction", "agent_turns_before": 4})
        for stage in (stage_triage, stage_read, stage_locate, stage_signature):
            asyncio.run(stage(d, 10**9, concurrency=1))
        leakage_mod.signals_trouble = refuses_for_length
        before5 = CALLED.get("signals_trouble", 0)
        asyncio.run(stage_screen(d, 10**9, concurrency=1))
        asyncio.run(stage_screen(d, 10**9, concurrency=1))
        long5 = load(d.screened)
        asked5 = CALLED.get("signals_trouble", 0) - before5
    except Exception as e:
        check(False, f"a stage raised: {type(e).__name__}: {e}")
        asked5 = None
    finally:
        leakage_mod.signals_trouble = fake_signals_trouble
    check(len(long5) == 1 and str(long5[0].get("too_long", "")).startswith("RuntimeError: Error code: 400")
          and not long5[0].get("error") and asked5 == 1,
          f"a conversation too long for the gate is recorded as such, once, not retried on every run: "
          f"{[(r.get('too_long', '')[:30], r.get('error')) for r in long5]}, asked {asked5}x")
    check(said5.get("signals_trouble") is True and said5.get("leak_carried_by") == "prose"
          and said5.get("redaction_worked") is False
          and str(said5.get("redaction_outcome", "")).startswith("still leaks after editing turns [3]"),
          f"a leak in the agent's put-back text is found where its candidate reads it, and a repair that "
          f"leaves it is not taken for one: {said5.get('leak_carried_by')!r}, "
          f"{str(said5.get('redaction_outcome'))[:60]!r} {said5.get('error') or ''}")

    print("\n6. a moment whose task could never be run is not read at full price")
    # G-48 / D-31. `find_moments` asks whether the repository's language has a
    # container, but nothing asked again when the rows were read, and
    # `can_be_sandboxed` appeared in exactly one place in the tree -- so every
    # moments file written before the filter existed was still read at about
    # eight model calls apiece for tasks the attempt stage then refuses. Of the
    # 2,199 moments on disk, 563 are in a language we know we cannot sandbox.
    import errata_bench.corpus.sessions as sessions_mod
    from errata_bench.corpus.sessions import Repo

    _langs = {"acme/up": "TypeScript", "acme/kt": "Kotlin", "acme/mystery": None}
    _kept_load = sessions_mod.load_repos
    sessions_mod.load_repos = lambda: {
        rid: Repo(repo_id=rid, url="u", license_type="mit", language=lang)
        for rid, lang in _langs.items()
    }
    try:
        r = Paths(Path(tempfile.mkdtemp()) / "run")
        for repo in ("acme/up", "acme/kt", "acme/mystery"):
            append(r.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": repo,
                               "kind": "correction", "agent_turns_before": 4})
        CALLED.clear()
        prog = asyncio.run(stage_triage(r, 10**9, concurrency=1))
        seen_repos = sorted({row.get("repo_id") for row in load(r.triaged)})
        check(seen_repos == ["acme/mystery", "acme/up"] and CALLED.get("triage", 0) == 2,
              f"the Kotlin moment is not triaged, and the one with no recorded language is: "
              f"{seen_repos}, {CALLED.get('triage', 0)} calls")
        check(any("no container here" in n for n in prog.notes),
              f"and the stage says how many it passed over: {[n[:60] for n in prog.notes]}")
        CALLED.clear()
        asyncio.run(stage_read(r, 10**9, concurrency=1))
        check(CALLED.get("read_pushback", 0) == 2,
              f"and the read stage asks the same question: {CALLED.get('read_pushback', 0)} calls")
    except Exception as e:
        check(False, f"a stage raised: {type(e).__name__}: {e}")
    finally:
        sessions_mod.load_repos = _kept_load

    print("\n7. a paid reading is not deleted because the triage before it failed")
    # `stage_read` wrote `{**triaged_row, "reading": ...}`. A moment whose
    # triage hit a 429 carries `error`, so the reading -- which succeeded, and
    # was paid for -- inherited it, and `completed()` deleted it as failed work
    # on the next run. The read is bought twice at best; if the retried triage
    # answers `worth_reading=False` the row is never rebuilt and the paid
    # reading is simply gone, with every counter still calling it produced.
    from errata_bench.store import completed as _completed7

    r7 = Paths(Path(tempfile.mkdtemp()) / "run")
    append(r7.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                        "kind": "correction", "agent_turns_before": 4})
    # The shape triage leaves behind when its own call raised.
    append(r7.triaged, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                        "kind": "correction", "agent_turns_before": 4,
                        "worth_reading": True, "triage_reason": "error: RuntimeError: 429",
                        "error": "RuntimeError: 429"})
    CALLED.clear()
    asyncio.run(stage_read(r7, 10**9, concurrency=1))
    _rows7 = load(r7.readings)
    check(len(_rows7) == 1 and _rows7[0].get("reading") and not _rows7[0].get("error"),
          f"the reading is written without the triage row's error: "
          f"keys {sorted(k for k in (_rows7[0] if _rows7 else {}))}")
    check(len(_completed7(r7.readings)) == 1,
          f"and it survives the next run rather than being deleted as failed work: "
          f"{len(_completed7(r7.readings))} of {len(_rows7)} kept")
    # The reading's own failure is still recorded as one.
    r7b = Paths(Path(tempfile.mkdtemp()) / "run")
    append(r7b.triaged, {"session_id": "s-boom", "turn_number": 1, "repo_id": "acme/up",
                         "kind": "correction", "agent_turns_before": 4, "worth_reading": True})

    async def _boom7(turns, turn, **kw):
        raise RuntimeError("429 from the reader itself")

    _kept7 = reading_mod.read_pushback
    reading_mod.read_pushback = _boom7
    try:
        asyncio.run(stage_read(r7b, 10**9, concurrency=1))
    finally:
        reading_mod.read_pushback = _kept7
    check(load(r7b.readings) and load(r7b.readings)[0].get("error")
          and not _completed7(r7b.readings),
          "while a reading that really failed is still written with an error and re-asked")

    print("\n8. the screening stages read the record with SWE-chat's lost calls put back")
    # G-76, phase B. Of a batch of parallel calls the conversations table keeps
    # only the last. The reader, locate and the leak gate must see what a
    # candidate built from this moment will be shown: the whole batch. Each
    # stage is checked, since each loads the turns for itself.
    import errata_bench.corpus.recover as recover_mod
    dir8 = Path(tempfile.mkdtemp())
    # And what the agent wrote before those calls, which the table drops too
    # (G-79): only what a candidate is shown, and the gates that read it, get it
    # back (G-81); every stage that picks turns reads the table's turns.
    (dir8 / "s-rec.jsonl").write_text("\n".join([json.dumps(
        {"type": "assistant", "isSidechain": False, "message": {"id": "m1", "content": [
            {"type": "text", "text": "I'll read the retry helper first."}]}})] + [json.dumps(
        {"type": "assistant", "isSidechain": False, "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": cid, "name": "Read",
             "input": {"file_path": f"/Users/d/code/up/src/{name}"}}]}})
        for cid, name in (("lost", "retry.py"), ("kept", "upload.py"))]) + "\n")
    SESSIONS["s-rec"] = SESSION[:2] + [
        {"turn_number": 4, "turn_type": "tool_use", "tool_name": "Read", "tool_call_id": "kept",
         "content": json.dumps({"file_path": "/Users/d/code/up/src/upload.py"})},
        {"turn_number": 5, "turn_type": "tool_result", "tool_call_id": "lost",
         "content": "def retry(): pass"},
    ] + SESSION[3:]
    saw8: dict[str, bool] = {}
    stage8 = {"now": ""}
    seen8 = lambda turns: any(t.get("recovered") and t.get("turn_type") == "tool_use" for t in turns)
    said8: dict[str, bool] = {}
    text8 = lambda turns: any(t.get("recovered") and t.get("turn_type") == "assistant_response" for t in turns)
    gates8: dict = {}

    def excerpt8(turns, cut, **kw):
        saw8[stage8["now"]] = saw8.get(stage8["now"], False) or seen8(turns)
        said8[stage8["now"]] = said8.get(stage8["now"], False) or text8(turns)
        if stage8["now"] == "screen":
            gates8.setdefault("kw", kw)
        return fake_build_excerpt(turns, cut, **kw)

    async def read8(turns, turn, **kw):
        saw8["read"], said8["read"] = seen8(turns), text8(turns)
        return await fake_read_pushback(turns, turn, **kw)

    async def locate8(turns, turn, **kw):
        saw8["locate"], said8["locate"] = seen8(turns), text8(turns)
        return await fake_locate(turns, turn, **kw)

    kept8 = (recover_mod.transcript_path, turns_mod.build_excerpt, reading_mod.read_pushback,
             trajectory_mod.locate)
    recover_mod.transcript_path = lambda sid: dir8 / f"{sid}.jsonl"
    turns_mod.build_excerpt, reading_mod.read_pushback, trajectory_mod.locate = excerpt8, read8, locate8
    p8 = Paths(Path(tempfile.mkdtemp()) / "run")
    append(p8.moments, {"session_id": "s-rec", "turn_number": 7, "repo_id": "acme/up",
                        "kind": "correction", "agent_turns_before": 4})
    try:
        for stage, label in ((stage_triage, "triage"), (stage_read, "read"), (stage_locate, "locate"),
                             (stage_signature, "signature"), (stage_screen, "screen")):
            stage8["now"] = label
            asyncio.run(stage(p8, 10**9, concurrency=1))
    finally:
        (recover_mod.transcript_path, turns_mod.build_excerpt, reading_mod.read_pushback,
         trajectory_mod.locate) = kept8
    screened8 = load(p8.screened)
    check(all(saw8.get(k) for k in ("triage", "read", "locate", "screen")),
          f"triage, the reader, locate and the leak gate each saw the recovered call: {saw8}")
    check(bool(screened8) and screened8[0].get("calls_recovered") is True,
          f"and the screened row says so, for the build to pass on: "
          f"{screened8[0].get('calls_recovered') if screened8 else 'no row'}")
    from errata_bench.corpus.turns import RECORD as RECORD8, RECORD_CHARS as RECORD_CHARS8
    check(said8.get("screen") is True and gates8.get("kw", {}).get("record") == RECORD8
          and gates8.get("kw", {}).get("max_chars") == RECORD_CHARS8
          and not any(said8.get(k) for k in ("triage", "read", "locate")),
          f"the gates read the conversation as its candidate is shown it, the agent's lost text put back and "
          f"nothing cut (G-81), while the stages that pick turns read the table's: {said8}, {gates8.get('kw')}")
    check(bool(screened8) and screened8[0].get("text_recovered") is True,
          "and the screened row says the text was put back, for the build to pass on")

    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
