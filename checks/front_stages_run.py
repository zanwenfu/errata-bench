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
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import errata_bench.corpus.sessions as sessions_mod
import errata_bench.corpus.turns as turns_mod
import errata_bench.find.answerable as answerable_mod
import errata_bench.find.leakage as leakage_mod
import errata_bench.find.reading as reading_mod
import errata_bench.find.scope as scope_mod
import errata_bench.find.signature as signature_mod
import errata_bench.find.trajectory as trajectory_mod
import errata_bench.find.triage as triage_mod
from errata_bench.find.answerable import Answerable

# The stages ask the corpus for its list of edited sessions (G-95), which reads
# `CORPUS`: a folder that does not exist here, as in CI, so whatever ERRATA_CORPUS names -- a collected
# corpus assembled before the list, say -- neither stops nor steers this run.
sessions_mod.CORPUS = Path(tempfile.mkdtemp()) / "no-corpus"
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
# Kept for section 9, whose releases are rendered again for real.
REAL_BUILD_EXCERPT = turns_mod.build_excerpt


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
        pushback_is_the_developers=True,
        knowable_at_the_failing_turn=True,
        visible_from_the_repository=True,
        consistent_with_instructions=True,
        benchmark_viable=True,
        context_sufficient=True,
    )


async def fake_locate(turns, turn, **kw):
    records("locate")
    return Trajectory(
        request_turn=1, failed_turn=6, complaint_turn=7, objection=True, knowable=True, resolution_fixes_it=True,
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

    print("\n1b. each stage's row says which model read it, and what its calls used, a failed one's too")
    # A build from a new corpus is paid at these stages first; their rows were
    # priced nowhere (10-01 review). Metered, a row's usage is a dict, filled by
    # what its calls used (none: the readers here are fakes), where a row written
    # outside a meter says None.
    from errata_bench.llm import model_name as model_name1b

    field1b = lambda label: "screen_model" if label == "screen" else "find_model"
    named1b = {label: load(out) for label, out in (("triage", p.triaged), ("read", p.readings),
                                                    ("locate", p.trajectories), ("signature", p.signatures),
                                                    ("screen", p.screened))}
    check(all(rows and all(r.get(field1b(label)) == model_name1b() and isinstance(r.get("usage"), dict)
                           for r in rows) for label, rows in named1b.items()),
          f"every stage's row names the model that read it and is metered: "
          f"{ {k: [(r.get(field1b(k)), type(r.get('usage')).__name__) for r in v] for k, v in named1b.items()} }")

    async def broken1b(*a, **k):
        raise RuntimeError("the reader failed")

    # Each stage reads rows an earlier stage wrote, under another model and with
    # its own usage, as a run resumed under another ERRATA_MODEL leaves them: a
    # row carried forward kept the earlier model's name, and was priced by it
    # (10-01: a mutant dropping a stage's own name survived, the carried one in
    # its place). A row's model and usage are its own, a failed one's too.
    own1b = {}
    for label, module1b, attr1b, stage1b, source1b, out1b in (
            ("triage", triage_mod, "triage", stage_triage, "moments", "triaged"),
            ("read", reading_mod, "read_pushback", stage_read, "triaged", "readings"),
            ("locate", trajectory_mod, "locate", stage_locate, "readings", "trajectories"),
            ("signature", signature_mod, "derive", stage_signature, "trajectories", "signatures"),
            ("screen", leakage_mod, "signals_trouble", stage_screen, "signatures", "screened")):
        for outcome in ("read", "failed"):
            q1b = Paths(Path(tempfile.mkdtemp()) / "run")
            carried1b = [dict(r, find_model="another-model", screen_model="another-model", usage={"requests": 7})
                         for r in load(getattr(p, source1b))]
            getattr(q1b, source1b).write_text("".join(json.dumps(r) + "\n" for r in carried1b))
            kept1b = getattr(module1b, attr1b)
            if outcome == "failed":
                setattr(module1b, attr1b, broken1b)
            try:
                asyncio.run(stage1b(q1b, 10**9, concurrency=1))
            finally:
                setattr(module1b, attr1b, kept1b)
            rows1b = load(getattr(q1b, out1b))
            own1b[(label, outcome)] = (
                len(rows1b) == 1 and rows1b[0].get(field1b(label)) == model_name1b()
                and rows1b[0].get("usage") == {}
                and ("the reader failed" in json.dumps(rows1b[0])) == (outcome == "failed"))
    check(all(own1b.values()),
          f"and a row says its own model and usage, read after rows another model wrote, a failed one's too: "
          f"{[k for k, v in own1b.items() if not v]}")

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
                  "redaction_worked", "screen_passes", "calls_recovered", "text_recovered", "usage",
                  "screen_model"):
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
    # Not without being asked: each reads its whole conversation again.
    unasked_before = CALLED.get("signals_trouble", 0)
    unasked = asyncio.run(stage_screen(d, 10**9, concurrency=1))
    check(CALLED.get("signals_trouble", 0) == unasked_before and load(d.screened) == old
          and any("wait for --rescreen-old" in n or "waits for --rescreen-old" in n for n in unasked.notes),
          f"an old row is not screened again unasked, and the stage says why: {[n[:70] for n in unasked.notes]}")
    os.environ["ERRATA_RESCREEN_OLD"] = "1"
    asked_before = CALLED.get("signals_trouble", 0)
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    again_rows = load(d.screened)
    asked_again = CALLED.get("signals_trouble", 0) - asked_before
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    check(len(old) == 1 and asked_again == 1 and len(again_rows) == 1 and "text_recovered" in again_rows[0]
          and CALLED.get("signals_trouble", 0) - asked_before == 1,
          f"a row screened before the gates read the candidate's view is screened again, once, and replaced: "
          f"gates asked {asked_again}x, {len(again_rows)} row(s), flag {'text_recovered' in (again_rows or [{}])[0]}")
    # And when the old row was screened more times than the new one is: three
    # passes before, the default one now. Ranked on passes alone, the old row
    # won, the new one was dropped, and every run paid for it again.
    old3 = [{**r, "screen_passes": 3} for r in old]
    d.screened.write_text("".join(json.dumps(r) + "\n" for r in old3))
    asked_before3 = CALLED.get("signals_trouble", 0)
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    asyncio.run(stage_screen(d, 10**9, concurrency=1))
    kept3 = load(d.screened)
    check(len(kept3) == 1 and "text_recovered" in kept3[0] and kept3[0].get("screen_passes") == 1
          and CALLED.get("signals_trouble", 0) - asked_before3 == 1,
          f"and when the old row had more passes than the new: replaced, and asked once, not every run: "
          f"{[(r.get('screen_passes'), 'text_recovered' in r) for r in kept3]}, gates asked "
          f"{CALLED.get('signals_trouble', 0) - asked_before3}x")
    os.environ.pop("ERRATA_RESCREEN_OLD", None)

    # A row the free pre-check set aside (`scripts/prescreen_buildable.py`) is
    # done at any pass count: read as one pass, `--passes 3` screened every one
    # again, paying back what the pre-check saved, and the build refused them
    # all the same (review, 10-03).
    dp = Paths(Path(tempfile.mkdtemp()) / "run")
    append(dp.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                        "kind": "correction", "agent_turns_before": 4})
    for stage in (stage_triage, stage_read, stage_locate, stage_signature):
        asyncio.run(stage(dp, 10**9, concurrency=1))
    aside = [{**r, "usable": False, "reason": "set aside before screening, as the build would refuse it: no commit",
              "prescreened": True, "usage": {}, "screen_passes": 1, "text_recovered": True}
             for r in load(dp.signatures)]
    for r in aside:
        append(dp.screened, r)
    asked_before_p = CALLED.get("signals_trouble", 0)
    asyncio.run(stage_screen(dp, 10**9, concurrency=1, passes=3))
    writes = Path("scripts/prescreen_buildable.py").read_text()
    check(len(aside) == 1 and load(dp.screened) == aside and CALLED.get("signals_trouble", 0) == asked_before_p
          and '"prescreened": True' in writes and '"screen_passes": 1' in writes,
          f"a row the pre-check set aside is not screened again at --passes 3: "
          f"{len(load(dp.screened))} row(s), gates asked {CALLED.get('signals_trouble', 0) - asked_before_p}x")

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
    check(len(long5) == 1 and str(long5[0].get("provider_refused", "")).startswith("too long: RuntimeError")
          and not long5[0].get("error") and asked5 == 1,
          f"a conversation too long for the gate is recorded as such, once, not retried on every run: "
          f"{[(r.get('provider_refused', '')[:30], r.get('error')) for r in long5]}, asked {asked5}x")
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

    print("\n9. a frozen release screened again on what its candidates are shown, and the verdicts applied")
    # v1.0's leak gate read 60,000 characters of conversations its candidates read
    # whole, and 5 of its 7 repairs removed the request (G-81, G-83). The re-screen
    # asks the gates again of each frozen task, from before its repair; applying
    # it keeps, repairs again, undoes or sets aside each, and says why (09-30).
    import importlib.util
    import shutil
    import errata_bench.score.attempt as attempt_mod9
    from errata_bench.find.redact import apply as redact_apply9
    from errata_bench.spec import Task

    def script9(name):
        spec = importlib.util.spec_from_file_location(f"{name}9", f"scripts/{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    rescreen9, apply9 = script9("rescreen_release"), script9("apply_rescreen")
    release9 = Path(tempfile.mkdtemp()) / "release"
    # A clean task; a leak typed before the request; v1's repair that removed the
    # request; and a v1 repair of a conversation that no longer leaks.
    fixtures9 = {"t-clean": ("s-1", []), "t-prose": ("s-prose", []), "t-asked": ("s-asked", [3]),
                 "t-undo": ("s-undo", [2])}
    for tid, (sid, removed) in fixtures9.items():
        task = Task(tid, "acme/up", "https://github.com/acme/up", "0" * 40, sid, 5, 6, 7, 9,
                    "Done — retries are in and the tests pass.", "You're right. I added the sleep and verified it.",
                    "the backoff is never exercised", "present", redacted_turns=list(removed))
        d = release9 / "tasks" / tid
        (d / "grading").mkdir(parents=True)
        (d / "grading" / "task.json").write_text(json.dumps(task.to_json()))
        (d / "task.json").write_text(json.dumps({"task_id": tid}))
        turns9 = list(SESSIONS.get(sid, SESSION))
        view9 = redact_apply9(turns9, removed, {}) if removed else turns9
        (d / "conversation.txt").write_bytes(fake_build_excerpt(view9, 5).encode("utf-8"))
    out9 = Path(tempfile.mkdtemp()) / "rescreen"
    saved9 = {k: os.environ.get(k) for k in ("ERRATA_MODEL", "ERRATA_PROVIDER", "AZURE_OPENAI_BASE_URL",
                                              "ERRATA_TIMEOUT", "ERRATA_MAX_RETRIES")}
    kept9 = (leakage_mod.signals_trouble, redact_mod.survey)
    env_seen9 = set()

    async def recording9(excerpt, **kw):
        # Each gate's call is given the long timeout and the retries grading has.
        env_seen9.add((os.environ.get("ERRATA_TIMEOUT"), os.environ.get("ERRATA_MAX_RETRIES")))
        return await leaks_where_it_says(excerpt, **kw)

    leakage_mod.signals_trouble, redact_mod.survey = recording9, fake_survey
    try:
        os.environ.pop("ERRATA_TIMEOUT", None)
        os.environ.pop("ERRATA_MAX_RETRIES", None)
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        os.environ["ERRATA_PROVIDER"] = "openai"
        os.environ.pop("ERRATA_MODEL", None)
        unnamed9 = rescreen9.main([str(release9), "--out", str(out9)])
        # Refused before anything is read: an unnamed provider beside Azure's
        # settings, a Claude model on Azure, an even or no number of passes.
        before_refusals9 = CALLED.get("signals_trouble", 0)
        os.environ.update({"ERRATA_MODEL": "gpt-6-astra", "AZURE_OPENAI_BASE_URL": "https://example.invalid/v1"})
        os.environ.pop("ERRATA_PROVIDER", None)
        refusals9 = {"unnamed provider": rescreen9.main([str(release9), "--out", str(out9)])}
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        os.environ.update({"ERRATA_PROVIDER": "azure", "ERRATA_MODEL": "claude-opus-5"})
        refusals9["a Claude model on Azure"] = rescreen9.main([str(release9), "--out", str(out9)])
        os.environ.update({"ERRATA_PROVIDER": "openai", "ERRATA_MODEL": "gpt-6-astra"})
        refusals9["two passes"] = rescreen9.main([str(release9), "--out", str(out9), "--passes", "2"])
        refusals9["no passes"] = rescreen9.main([str(release9), "--out", str(out9), "--passes", "0"])
        # Odd, so only the rule against fewer than one refuses it.
        refusals9["minus one pass"] = rescreen9.main([str(release9), "--out", str(out9), "--passes", "-1"])
        empty9 = Path(tempfile.mkdtemp()) / "release"
        (empty9 / "tasks").mkdir(parents=True)
        refusals9["no frozen task"] = rescreen9.main([str(empty9), "--out", str(Path(tempfile.mkdtemp()) / "e")])
        # A task whose session the corpus here lacks cannot be shown to render.
        kept_load9 = turns_mod.load_session_turns
        turns_mod.load_session_turns = lambda ids: {}
        try:
            with __import__("contextlib").redirect_stderr(__import__("io").StringIO()) as said9:
                refusals9["no session in the corpus"] = rescreen9.main(
                    [str(release9), "--out", str(Path(tempfile.mkdtemp()) / "c")])
        finally:
            turns_mod.load_session_turns = kept_load9
        refusals9["no session in the corpus"] = (refusals9["no session in the corpus"]
                                                 if "its session is not in the corpus" in said9.getvalue() else -1)
        refused_asked9 = CALLED.get("signals_trouble", 0) - before_refusals9
        out9_made = out9.exists()
        os.environ["ERRATA_MODEL"] = "gpt-6-astra"
        bad9 = Path(tempfile.mkdtemp()) / "release"
        shutil.copytree(release9, bad9)
        (bad9 / "tasks" / "t-clean" / "conversation.txt").write_bytes(b"[turn 1] something else")
        before9 = CALLED.get("signals_trouble", 0)
        refused9 = rescreen9.main([str(bad9), "--out", str(Path(tempfile.mkdtemp()) / "r")])
        asked_refused9 = CALLED.get("signals_trouble", 0) - before9
        ran9 = rescreen9.main([str(release9), "--out", str(out9)])
        stale9 = rescreen9.main([str(release9), "--out", str(out9), "--only", "t-clean"])
        # One re-screen per folder: a second into it would pay for every row again.
        from errata_bench.store import only_one as only_one9
        with only_one9(out9, "a test holding it"):
            try:
                rescreen9.main([str(release9), "--out", str(out9)])
                locked9 = "ran"
            except SystemExit as e:
                locked9 = "refused" if "another process" in str(e.code) else f"exit {e.code}"
    finally:
        leakage_mod.signals_trouble, redact_mod.survey = kept9
        for k, v in saved9.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    rows9 = {r["task_id"]: r for r in load(out9 / "screened.jsonl")}
    check(unnamed9 == 2 and set(refusals9.values()) == {2} and refused_asked9 == 0 and not out9_made
          and refused9 == 2 and asked_refused9 == 0 and stale9 == 2 and locked9 == "refused",
          f"the re-screen is refused without its model named, before asking anything when a task's view does not "
          f"render the conversation its candidate is shown, into a folder of other tasks, and into one another "
          f"re-screen holds, as it is for an unnamed provider, a Claude model on Azure and an even or no number of "
          f"passes: {unnamed9}, {refusals9}, {refused9} (gates asked {asked_refused9}x), {stale9}, {locked9}")
    seen9 = {t: (r.get('signals_trouble'), r.get('redacted_turns'), str(r.get('redaction_outcome'))[:30])
             for t, r in rows9.items()}
    check(ran9 == 0 and set(rows9) == set(fixtures9)
          and not rows9["t-clean"].get("signals_trouble") and not rows9["t-undo"].get("signals_trouble")
          and rows9["t-prose"].get("redaction_worked") is True and rows9["t-prose"].get("redacted_turns") == [1]
          # The repair checked again as often as the leak was asked (09-30 review).
          and rows9["t-prose"].get("redaction_recheck_held") == "3/3"
          and str(rows9["t-asked"].get("redaction_outcome", "")).startswith(
              "not repairable: the leak is the developer's request itself")
          and rows9["t-asked"].get("released_redacted_turns") == [3] and env_seen9 == {("900", "5")}
          and all(r.get("screen_model") == "gpt-6-astra" and "usage" in r and r.get("screen_passes") == 3
                  and r.get("task_fingerprint") and r.get("conversation_sha256")
                  # And the session data it was screened on, which the apply checks (10-01).
                  and r.get("session_sha256") == recover_mod.session_fingerprint(
                      r["session_id"], list(SESSIONS.get(r["session_id"], SESSION)))
                  for r in rows9.values()),
          f"each frozen task is screened from before its repair, three times over, its rows priced by their model: "
          f"{seen9}")
    # Each decision, and its reason, read off its row (10-01: a mutant setting a
    # refused conversation aside for "no request" survived, the decision alike).
    asked9 = {"asks_for_something": True, "within_scope": True}
    decided9 = {
        "refused": apply9.decide({"provider_refused": "too long: the context window"}),
        "no request": apply9.decide({"asks_for_something": False, "within_scope": True,
                                     "request_reason": "a bare yes"}),
        "out of scope": apply9.decide({"asks_for_something": True, "within_scope": False, "scope_reason": "CI"}),
        "leaks": apply9.decide({**asked9, "signals_trouble": True, "redaction_worked": False,
                                "redaction_outcome": "not repairable"}),
        "repaired": apply9.decide({**asked9, "signals_trouble": True, "redaction_worked": True}),
        "clean": apply9.decide({**asked9, "signals_trouble": False}),
    }
    wanted9 = {"refused": ("set aside", "the screening model would not read"),
               "no request": ("set aside", "no request for the candidate"),
               "out of scope": ("set aside", "the defect is outside what was asked"),
               "leaks": ("set aside", "its conversation already signals the trouble"),
               "repaired": ("kept", "its leak repaired"), "clean": ("kept", "no leak")}
    repairs9 = (apply9.repair_of({"redaction_worked": False, "redacted_turns": [1], "rewritten_turns": {"2": "x"}}),
                apply9.repair_of({"redaction_worked": True, "redacted_turns": [1], "rewritten_turns": {"2": "x"}}))
    check(all(decided9[k][0] == d and decided9[k][1].startswith(w) for k, (d, w) in wanted9.items())
          and repairs9 == (([], {}), ([1], {"2": "x"})),
          f"each row decides its task for its own reason, and only a repaired leak brings its repair: "
          f"{ {k: (v[0], v[1][:30]) for k, v in decided9.items()} }, {repairs9}")
    # A re-screen whose gate calls failed exits 1, its rows kept as errors to ask again.
    out9e = Path(tempfile.mkdtemp()) / "rescreen"

    async def failing9(excerpt, **kw):
        raise RuntimeError("Connection reset by peer")

    kept9e = leakage_mod.signals_trouble
    leakage_mod.signals_trouble = failing9
    saved9e = {k: os.environ.get(k) for k in ("ERRATA_MODEL", "ERRATA_PROVIDER", "AZURE_OPENAI_BASE_URL")}
    try:
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        os.environ["ERRATA_PROVIDER"], os.environ["ERRATA_MODEL"] = "openai", "gpt-6-astra"
        failed9e = rescreen9.main([str(release9), "--out", str(out9e), "--only", "t-clean"])
    finally:
        leakage_mod.signals_trouble = kept9e
        for k, v in saved9e.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    check(failed9e == 1 and all(r.get("error") for r in load(out9e / "screened.jsonl")),
          f"and a re-screen whose gate calls failed exits 1, the row kept as an error to ask again: {failed9e}")
    # Applied for real: the tasks whose repair changes are rendered again.
    kept9b = (turns_mod.build_excerpt, attempt_mod9.build_excerpt)
    turns_mod.build_excerpt = attempt_mod9.build_excerpt = REAL_BUILD_EXCERPT
    try:
        shown9 = {t: (release9 / "tasks" / t / "conversation.txt").read_bytes() for t in fixtures9}
        dry9 = apply9.main([str(release9), str(out9), "--dry-run"])
        dry_same9 = ({t: (release9 / "tasks" / t / "conversation.txt").read_bytes() for t in fixtures9} == shown9
                     and not (release9 / "manifest.json").exists())
        unscreened9 = Path(tempfile.mkdtemp()) / "release"
        shutil.copytree(release9, unscreened9)
        shutil.copytree(release9 / "tasks" / "t-clean", unscreened9 / "tasks" / "t-new")
        none9 = apply9.main([str(unscreened9), str(out9)])
        applied9 = apply9.main([str(release9), str(out9)])
        files9 = lambda: {f.relative_to(release9 / "tasks").as_posix(): f.read_bytes()
                          for f in sorted((release9 / "tasks").rglob("*")) if f.is_file()}
        first9 = files9()
        again9 = apply9.main([str(release9), str(out9)])
        same9 = files9() == first9
    finally:
        turns_mod.build_excerpt, attempt_mod9.build_excerpt = kept9b
    grading9 = {t: json.loads((release9 / "tasks" / t / "grading" / "task.json").read_text())
                for t in ("t-clean", "t-prose", "t-undo") if (release9 / "tasks" / t).exists()}
    runs9 = (json.loads((release9 / "manifest.json").read_text())["rescreened"]["runs"]
             if (release9 / "manifest.json").exists() else [])
    said9 = {p["task_id"]: p["decision"] for p in (runs9[0]["tasks"] if runs9 else [])}
    # What each decision rests on is kept with it, and a task set aside keeps the
    # repair it has, not one it never took (10-01: mutants of both survived).
    entry9 = {p["task_id"]: p for p in (runs9[0]["tasks"] if runs9 else [])}
    recorded9 = (entry9.get("t-asked", {}).get("redacted_turns") == [3]
                 and all(p.get("verdicts", {}).get("screen_model") == "gpt-6-astra"
                         and "signals_trouble" in p.get("verdicts", {}) for p in entry9.values()))
    prose9 = (release9 / "tasks" / "t-prose" / "conversation.txt").read_bytes().decode("utf-8")
    undo9 = (release9 / "tasks" / "t-undo" / "conversation.txt").read_bytes().decode("utf-8")
    check(dry9 == 0 and dry_same9 and none9 == 2 and applied9 == 0 and recorded9
          and said9 == {"t-clean": "kept", "t-prose": "repaired again", "t-asked": "set aside",
                        "t-undo": "repair undone"}
          and (release9 / "set-aside" / "t-asked" / "grading" / "task.json").is_file()
          and not (release9 / "tasks" / "t-asked").exists()
          and grading9["t-prose"]["redacted_turns"] == [1] and "you keep getting this wrong" not in prose9
          and "Add retry with backoff" in prose9
          and grading9["t-undo"]["redacted_turns"] == [] and "I'll add it." in undo9
          and (release9 / "tasks" / "t-clean" / "conversation.txt").read_bytes() == shown9["t-clean"],
          f"applied, a task is kept, repaired again, its old repair undone, or set aside with its request's leak "
          f"-- after a dry run that changed nothing, and never with a task left unscreened: dry {dry9}, unscreened "
          f"{none9}, applied {applied9}: {said9}")
    # Every reason a re-screen cannot be applied, each on its own row (10-01: a
    # mutant of each survived): screened twice, failed, before the gates read the
    # candidate's view, fewer passes than asked, another version of the task, or
    # another conversation -- the last only while the task's repair is the one
    # it was screened with, since applying a repair changes its conversation.
    tasks9 = {t: (Task.from_json(json.loads((release9 / "tasks" / t / "grading" / "task.json").read_text())),
                  release9 / "tasks" / t) for t in ("t-clean", "t-prose")}
    good9 = rows9["t-clean"]
    prose9 = rows9["t-prose"]
    said9p = {
        "twice": apply9.problems_of({"t-clean": tasks9["t-clean"]}, [good9, good9], 3),
        "failed": apply9.problems_of({"t-clean": tasks9["t-clean"]}, [dict(good9, error="reset")], 3),
        "before G-81": apply9.problems_of({"t-clean": tasks9["t-clean"]},
                                          [{k: v for k, v in good9.items() if k != "text_recovered"}], 3),
        "one pass": apply9.problems_of({"t-clean": tasks9["t-clean"]}, [dict(good9, screen_passes=1)], 3),
        "another version": apply9.problems_of({"t-clean": tasks9["t-clean"]},
                                              [dict(good9, task_fingerprint="0" * 16)], 3),
        "another conversation": apply9.problems_of({"t-clean": tasks9["t-clean"]},
                                                   [dict(good9, conversation_sha256="0" * 64)], 3),
        "repaired since": apply9.problems_of({"t-prose": tasks9["t-prose"]},
                                             [dict(prose9, conversation_sha256="0" * 64)], 3),
        "fine": apply9.problems_of({"t-clean": tasks9["t-clean"]}, [good9], 3),
    }
    wanted9p = {"twice": "screened 2 times over", "failed": "its screening failed",
                "before G-81": "screened before the gates read", "one pass": "fewer than --passes 3",
                "another version": "screened as another version", "another conversation": "another conversation"}
    check(all(len(said9p[k]) == 1 and w in said9p[k][0] for k, w in wanted9p.items())
          and said9p["repaired since"] == [] and said9p["fine"] == [],
          f"a re-screen is applied only to the task as it was screened, each reason named: "
          f"{ {k: (v[0][:40] if v else None) for k, v in said9p.items()} }")
    # Each decision is recorded against the release as screened, the run that
    # rendered it named by `rendered` (10-01 review: read off the task as an
    # earlier run left it, a repair undone was recorded as kept).
    said9 = [{p["task_id"]: (p["decision"], p.get("rendered")) for p in r["tasks"]} for r in runs9] + [{}, {}]
    first_said9, again_said9 = said9[0], said9[1]
    check(again9 == 0 and same9 and len(runs9) == 2
          and again_said9 == {"t-clean": ("kept", None), "t-prose": ("repaired again", False),
                              "t-undo": ("repair undone", False)}
          and {k: v for k, v in first_said9.items() if k != "t-asked"}
          == {"t-clean": ("kept", None), "t-prose": ("repaired again", True),
              "t-undo": ("repair undone", True)},
          f"and applied again it changes no file, records each decision against the release as screened, "
          f"rendered by the first run, and keeps the record of what the first run set aside: {again9}, {same9}, "
          f"{first_said9}, {again_said9}")

    print("\n10. applying a re-screen writes again each Harbor task that no longer gives this code's instruction")
    # v1.1's 17 long tasks were exported before the note on cut conversations was
    # reworded, and an apply stopped before writing a task left it stale: every
    # trial of such a task is an error row once paid for (10-01 review). Export
    # is stood in for: what matters here is which tasks are written, and when.
    import errata_bench.release.harbor as harbor10

    release10 = Path(tempfile.mkdtemp()) / "release"
    fixtures10 = dict(fixtures9, **{"t-fresh": ("s-fresh", [])})
    for tid, (sid, removed) in fixtures10.items():
        task = Task(tid, "acme/up", "https://github.com/acme/up", "0" * 40, sid, 5, 6, 7, 9,
                    "Done — retries are in and the tests pass.", "You're right. I added the sleep and verified it.",
                    "the backoff is never exercised", "present", redacted_turns=list(removed))
        d = release10 / "tasks" / tid
        (d / "grading").mkdir(parents=True)
        (d / "grading" / "task.json").write_text(json.dumps(task.to_json()))
        (d / "task.json").write_text(json.dumps({"task_id": tid}))
        turns10 = list(SESSIONS.get(sid, SESSION))
        view10 = redact_apply9(turns10, removed, {}) if removed else turns10
        (d / "conversation.txt").write_bytes(fake_build_excerpt(view10, 5).encode("utf-8"))
        (release10 / "harbor" / tid).mkdir(parents=True)
        (release10 / "harbor" / tid / "instruction.md").write_text("fresh" if tid == "t-fresh" else "old")
    (release10 / "harbor" / "export.json").write_text(json.dumps({"tasks": [{"task_id": t} for t in fixtures10]}))
    (release10 / "harbor" / "digests.json").write_text(json.dumps({t: "sha256:x" for t in fixtures10}))
    out10 = Path(tempfile.mkdtemp()) / "rescreen"
    saved10 = {k: os.environ.get(k) for k in ("ERRATA_MODEL", "ERRATA_PROVIDER", "AZURE_OPENAI_BASE_URL")}
    kept10 = (leakage_mod.signals_trouble, redact_mod.survey)
    leakage_mod.signals_trouble, redact_mod.survey = leaks_where_it_says, fake_survey
    try:
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        os.environ["ERRATA_PROVIDER"], os.environ["ERRATA_MODEL"] = "openai", "gpt-6-astra"
        screened10 = rescreen9.main([str(release10), "--out", str(out10)])
    finally:
        leakage_mod.signals_trouble, redact_mod.survey = kept10
        for k, v in saved10.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    written10, how10 = [], {"t-clean": KeyboardInterrupt}

    def export10(folder, out, *rest):
        if how10.get(folder.name):
            raise how10[folder.name]("stopped here")
        written10.append(folder.name)
        out.mkdir(parents=True, exist_ok=True)
        (out / "instruction.md").write_text("fresh")
        return {"task_id": folder.name, "written": True}

    def stale10(folder, out):
        return not (out / "instruction.md").is_file() or (out / "instruction.md").read_text() != "fresh"

    import errata_bench.release.environment as environment10

    kept10b = (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
               environment10.workspace_contents)
    turns_mod.build_excerpt = attempt_mod9.build_excerpt = REAL_BUILD_EXCERPT
    harbor10.export, apply9.stale = export10, stale10
    environment10.workspace_contents = lambda path: ([], {}, {})
    runs10 = {}
    try:
        try:
            apply9.main([str(release10), str(out10)])
            runs10["killed"] = "ran on"
        except KeyboardInterrupt:
            runs10["killed"] = "stopped"
        stale_after_kill10 = stale10(release10 / "tasks" / "t-clean", release10 / "harbor" / "t-clean")
        digests_after_kill10 = (release10 / "harbor" / "digests.json").exists()
        how10.clear()
        runs10["again"] = apply9.main([str(release10), str(out10)])
        written_again10 = sorted(written10)
        written10.clear()
        runs10["third"] = apply9.main([str(release10), str(out10)])
        written_third10 = sorted(written10)
        (release10 / "harbor" / "t-fresh" / "instruction.md").write_text("old")
        how10["t-fresh"] = RuntimeError
        runs10["failing"] = apply9.main([str(release10), str(out10)])
    finally:
        (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
         environment10.workspace_contents) = kept10b
    record10 = json.loads((release10 / "manifest.json").read_text())["rescreened"]["runs"]
    listing10 = json.loads((release10 / "harbor" / "export.json").read_text())
    check(screened10 == 0 and runs10["killed"] == "stopped" and stale_after_kill10 and not digests_after_kill10
          and [(r["complete"], [p["task_id"] for p in r["tasks"]]) for r in record10[:2]]
          == [(False, ["t-asked"]), (True, ["t-clean", "t-fresh", "t-prose", "t-undo"])]
          and runs10["again"] == 0 and written_again10 == ["t-clean", "t-prose", "t-undo"]
          and not (release10 / "harbor" / "digests.json").exists()
          and [r["task_id"] for r in listing10["tasks"]] == ["t-clean", "t-fresh", "t-prose", "t-undo"]
          and not (release10 / "harbor" / "t-asked").exists()
          and listing10.get("rescreened_at") and listing10.get("rescreened_code_version")
          and runs10["third"] == 0 and written_third10 == [],
          f"a Harbor task out of date is written again, a kept one whose instruction is current is not, a run "
          f"stopped part way keeps the record of what it did and the next run finishes it: {runs10}, "
          f"written {written_again10} then {written_third10}")
    # A run whose only change is a task set aside drops the digests as well; and a
    # task whose render fails, or raises, is refused and left as it was.
    import shutil as shutil10
    import types as types10

    def pristine10(dest, tids, harbor):
        for tid in tids:
            sid, removed = fixtures10[tid]
            task = Task(tid, "acme/up", "https://github.com/acme/up", "0" * 40, sid, 5, 6, 7, 9,
                        "Done — retries are in and the tests pass.",
                        "You're right. I added the sleep and verified it.",
                        "the backoff is never exercised", "present", redacted_turns=list(removed))
            d = dest / "tasks" / tid
            (d / "grading").mkdir(parents=True)
            (d / "grading" / "task.json").write_text(json.dumps(task.to_json()))
            (d / "task.json").write_text(json.dumps({"task_id": tid}))
            turns = list(SESSIONS.get(sid, SESSION))
            view = redact_apply9(turns, removed, {}) if removed else turns
            (d / "conversation.txt").write_bytes(fake_build_excerpt(view, 5).encode("utf-8"))
            if harbor:
                (dest / "harbor" / tid).mkdir(parents=True)
                (dest / "harbor" / tid / "instruction.md").write_text("fresh")
        if harbor:
            (dest / "harbor" / "digests.json").write_text("{}")

    release10b, release10c = (Path(tempfile.mkdtemp()) / "release" for _ in range(2))
    pristine10(release10b, ("t-asked", "t-fresh"), harbor=True)
    pristine10(release10c, ("t-prose", "t-undo"), harbor=False)
    kept10c = (apply9._rerender_module, apply9.stale)
    apply9.stale = stale10

    def failing10(task, turns, folder):
        if task.task_id == "t-prose":
            return False, "its replayed edits would change"
        raise RuntimeError("the render broke")

    try:
        alone10 = apply9.main([str(release10b), str(out10)])
        apply9._rerender_module = lambda: types10.SimpleNamespace(rerender=failing10)
        bad10 = apply9.main([str(release10c), str(out10)])
    finally:
        apply9._rerender_module, apply9.stale = kept10c
    said10c = {p["task_id"]: (p["decision"], p["why"], p.get("rendered"))
               for p in json.loads((release10c / "manifest.json").read_text())["rescreened"]["runs"][-1]["tasks"]}
    check(alone10 == 0 and not (release10b / "harbor" / "digests.json").exists()
          and (release10b / "set-aside" / "t-asked").is_dir()
          and bad10 == 1 and {k: v[0] for k, v in said10c.items()} == {"t-prose": "refused", "t-undo": "refused"}
          and said10c["t-prose"][1].endswith("its replayed edits would change")
          and said10c["t-undo"][1].endswith("RuntimeError: the render broke")
          and said10c["t-undo"][1].startswith("not rendered again, left as it was: ")
          and {v[2] for v in said10c.values()} == {False}
          and json.loads((release10c / "tasks" / "t-undo" / "grading" / "task.json").read_text())["redacted_turns"]
          == [2],
          f"a run that only sets a task aside drops the digests too, and a task whose render fails or raises is "
          f"refused, left as it was, and the run exits 1: {alone10}, {bad10}, {said10c}")
    # A render whose files were moved into place only in part is recorded as
    # neither version, to be copied again (10-01 review).
    release10i = Path(tempfile.mkdtemp()) / "release"
    pristine10(release10i, ("t-undo",), harbor=False)

    class Partly10(RuntimeError):
        pass

    def partly10(task, turns, folder):
        raise Partly10("['conversation.txt'] moved into place and the rest not: OSError: [Errno 5] I/O error")

    kept10i = apply9._rerender_module
    apply9._rerender_module = lambda: types10.SimpleNamespace(rerender=partly10, PartlyRendered=Partly10)
    try:
        partly_rc10 = apply9.main([str(release10i), str(out10)])
    finally:
        apply9._rerender_module = kept10i
    said10i = json.loads((release10i / "manifest.json").read_text())["rescreened"]["runs"][-1]["tasks"][0]
    check(partly_rc10 == 1 and said10i["decision"] == "refused" and said10i["rendered"] is False
          and said10i["why"].startswith("rendered only in part, so make the copy again: ['conversation.txt']"),
          f"a task rendered only in part is refused and recorded so: {partly_rc10}, {said10i['why'][:70]}")
    # A set-aside stopped between its record and its move keeps the record, and the
    # next run, finding the task still there, finishes it.
    release10d = Path(tempfile.mkdtemp()) / "release"
    pristine10(release10d, ("t-asked",), harbor=False)

    def stopped10(src, dst):
        raise KeyboardInterrupt

    kept10d = apply9.shutil
    apply9.shutil = types10.SimpleNamespace(move=stopped10, rmtree=shutil10.rmtree)
    try:
        try:
            apply9.main([str(release10d), str(out10)])
            moved10 = "ran on"
        except KeyboardInterrupt:
            moved10 = "stopped"
    finally:
        apply9.shutil = kept10d
    first10 = json.loads((release10d / "manifest.json").read_text())["rescreened"]["runs"]
    again10 = apply9.main([str(release10d), str(out10)])
    both10 = json.loads((release10d / "manifest.json").read_text())["rescreened"]["runs"]
    check(moved10 == "stopped" and [p["decision"] for p in first10[0]["tasks"]] == ["set aside"]
          and again10 == 0 and (release10d / "set-aside" / "t-asked").is_dir()
          and not (release10d / "tasks" / "t-asked").exists() and len(both10) == 2,
          f"a set-aside stopped before its task was moved keeps its record, and the next run moves it: {moved10}, "
          f"{[(r['complete'], [p['decision'] for p in r['tasks']]) for r in both10]}")
    # The digests go at the first change of any kind: a run whose only change is a
    # render, and one whose only change is a Harbor task written again (10-01: a
    # mutant of each survived, a set-aside in every run hiding it).
    release10e, release10f = (Path(tempfile.mkdtemp()) / "release" for _ in range(2))
    pristine10(release10e, ("t-prose", "t-undo"), harbor=True)
    pristine10(release10f, ("t-clean",), harbor=True)
    (release10f / "harbor" / "t-clean" / "instruction.md").write_text("old")
    kept10e = (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
               environment10.workspace_contents)
    turns_mod.build_excerpt = attempt_mod9.build_excerpt = REAL_BUILD_EXCERPT
    harbor10.export, apply9.stale = export10, stale10
    environment10.workspace_contents = lambda path: ([], {}, {})
    how10.clear()
    try:
        render_only10 = apply9.main([str(release10e), str(out10)])
        export_only10 = apply9.main([str(release10f), str(out10)])
    finally:
        (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
         environment10.workspace_contents) = kept10e
    check(render_only10 == 0 and not (release10e / "harbor" / "digests.json").exists()
          and export_only10 == 0 and not (release10f / "harbor" / "digests.json").exists(),
          f"the digests go when a task is only rendered again, and when a Harbor task is only written again: "
          f"{render_only10}, {export_only10}")
    check(runs10["failing"] == 1 and str(record10[-1]["tasks"][1].get("exported", "")).startswith("failed:")
          and record10[-1]["complete"] is True,
          f"and a Harbor task that cannot be written is recorded as such, and the run exits 1: {runs10['failing']}, "
          f"{record10[-1]['tasks'][1].get('exported')}")
    # Stopped after a task was rendered again and before it was recorded, the
    # next run records the change against the release as screened, and does not
    # render it again: read off the task as the stopped run left it, the undone
    # repair was recorded as kept, v1's repair gone from the record (10-01 review).
    release10g = Path(tempfile.mkdtemp()) / "release"
    pristine10(release10g, ("t-undo",), harbor=True)
    (release10g / "harbor" / "t-undo" / "instruction.md").write_text("old")
    real_rr10 = apply9._rerender_module()
    rendered10 = []

    def counting10(task, turns, folder):
        rendered10.append(task.task_id)
        return real_rr10.rerender(task, turns, folder)

    kept10g = (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
               environment10.workspace_contents, apply9._rerender_module)
    turns_mod.build_excerpt = attempt_mod9.build_excerpt = REAL_BUILD_EXCERPT
    harbor10.export, apply9.stale = export10, stale10
    environment10.workspace_contents = lambda path: ([], {}, {})
    apply9._rerender_module = lambda: types10.SimpleNamespace(rerender=counting10,
                                                              PartlyRendered=real_rr10.PartlyRendered)
    how10.clear()
    how10["t-undo"] = KeyboardInterrupt
    try:
        try:
            apply9.main([str(release10g), str(out10)])
            stopped10g = "ran on"
        except KeyboardInterrupt:
            stopped10g = "stopped"
        how10.clear()
        again10g = apply9.main([str(release10g), str(out10)])
    finally:
        (turns_mod.build_excerpt, attempt_mod9.build_excerpt, harbor10.export, apply9.stale,
         environment10.workspace_contents, apply9._rerender_module) = kept10g
    runs10g = json.loads((release10g / "manifest.json").read_text())["rescreened"]["runs"]
    undone10 = {p["task_id"]: p for r in runs10g for p in r["tasks"]}.get("t-undo") or {}
    check(stopped10g == "stopped" and again10g == 0 and rendered10 == ["t-undo"]
          and [r["tasks"] for r in runs10g[:1]] == [[]]
          and (undone10.get("decision"), undone10.get("released_redacted_turns"), undone10.get("redacted_turns"),
               undone10.get("exported")) == ("repair undone", [2], [], True),
          f"a run stopped after rendering a task again, before recording it, is finished by the next, which records "
          f"the change against the release as screened and does not render it twice: {stopped10g}, {again10g}, "
          f"rendered {rendered10}, recorded {[(undone10.get('decision'), undone10.get('released_redacted_turns'))]}")
    # A surveyor's answer that did not parse is asked once more, and the row is
    # finished, where it failed and the next run asked every gate again (10-01).
    import contextlib as contextlib10
    import io as io10

    release10h = Path(tempfile.mkdtemp()) / "release"
    pristine10(release10h, ("t-prose",), harbor=False)
    out10h = Path(tempfile.mkdtemp()) / "rescreen"
    surveyed10 = []

    async def unparsed_once10(*a, **k):
        surveyed10.append(1)
        if len(surveyed10) == 1:
            from agents.exceptions import ModelBehaviorError
            raise ModelBehaviorError("Invalid JSON when parsing {\"removed_turns\": [ for type Redaction")
        return await fake_survey(*a, **k)

    kept10h = (leakage_mod.signals_trouble, redact_mod.survey)
    saved10h = {k: os.environ.get(k) for k in ("ERRATA_MODEL", "ERRATA_PROVIDER", "AZURE_OPENAI_BASE_URL")}
    leakage_mod.signals_trouble, redact_mod.survey = leaks_where_it_says, unparsed_once10
    try:
        os.environ.pop("AZURE_OPENAI_BASE_URL", None)
        os.environ["ERRATA_PROVIDER"], os.environ["ERRATA_MODEL"] = "openai", "gpt-6-astra"
        with contextlib10.redirect_stdout(io10.StringIO()):
            screened10h = rescreen9.main([str(release10h), "--out", str(out10h)])
    finally:
        leakage_mod.signals_trouble, redact_mod.survey = kept10h
        for k, v in saved10h.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    rows10h = load(out10h / "screened.jsonl")
    check(screened10h == 0 and len(surveyed10) == 2 and len(rows10h) == 1 and not rows10h[0].get("error")
          and rows10h[0].get("redaction_outcome"),
          f"a surveyor's answer that did not parse is asked once more and the row finished: {screened10h}, "
          f"surveyed {len(surveyed10)} times, {[(r.get('error') or r.get('redaction_outcome'))[:50] for r in rows10h]}")
    # A task is rendered again only from the session data its re-screen read: from
    # another copy of the corpus it could show a conversation the leak check never
    # read. Refused, changing nothing (10-01).
    release10j = Path(tempfile.mkdtemp()) / "release"
    pristine10(release10j, ("t-undo",), harbor=False)
    files10j = lambda: {p.relative_to(release10j).as_posix(): p.read_bytes()
                        for p in sorted(release10j.rglob("*")) if p.is_file()}
    before10j = files10j()

    def elsewhere10(ids):
        return {sid: [dict(t, content=str(t.get("content") or "") + " ") for t in SESSIONS.get(sid, SESSION)]
                for sid in ids}

    kept10j = (turns_mod.build_excerpt, attempt_mod9.build_excerpt, turns_mod.load_session_turns)
    turns_mod.build_excerpt = attempt_mod9.build_excerpt = REAL_BUILD_EXCERPT
    turns_mod.load_session_turns = elsewhere10
    try:
        with contextlib10.redirect_stderr(io10.StringIO()) as said10j:
            elsewhere_rc10 = apply9.main([str(release10j), str(out10)])
    finally:
        turns_mod.build_excerpt, attempt_mod9.build_excerpt, turns_mod.load_session_turns = kept10j
    check(elsewhere_rc10 == 2 and files10j() == before10j and "other session data" in said10j.getvalue()
          and "t-undo" in said10j.getvalue(),
          f"a task is not rendered again from other session data than its re-screen read, and nothing changes: "
          f"{elsewhere_rc10}, {said10j.getvalue().strip()[:100]!r}")

    print("\n11. triage reads the end of the conversation with every message whole (G-92)")
    kept11 = turns_mod.build_excerpt
    turns_mod.build_excerpt = fake_build_excerpt
    EXCERPT_KW.clear()
    d11 = Paths(Path(tempfile.mkdtemp()) / "run")
    try:
        append(d11.moments, {"session_id": "s-1", "turn_number": 7, "repo_id": "acme/up",
                             "kind": "correction", "agent_turns_before": 4})
        asyncio.run(stage_triage(d11, 10**9, concurrency=1))
    finally:
        turns_mod.build_excerpt = kept11
    check(EXCERPT_KW and all(k == {"record": 2, "whole_messages": True} for k in EXCERPT_KW)
          and len(load(d11.triaged)) == 1,
          f"the triage stage asks for its view with every message whole and every cut said: {EXCERPT_KW}")

    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
