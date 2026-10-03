"""The study's rules, checked with the model faked: what each side sees, the quote checks, the merge, the runner.

The study (docs/study.md) compares an AI reviewer's list with the developer's
pushback. Its result is only as good as three rules, so each is checked here
on a session built for the purpose:

  - the reviewer never reads what the developer wrote after the work it
    judges, and no row the developer did not write is shown as theirs or ends
    a report (G-90's rows: notices, compaction summaries, command output);
  - a quote that is not in the text it claims to come from is marked, never
    trusted;
  - the runner resumes, asks errored rows again, and stops at its spend line.

No network, no corpus, no model calls: the three readings are replaced by
stand-ins that return the real pydantic models and record that they ran, and
the client's set-up raises if anything reaches it.

    .venv/bin/python checks/study_holds.py
"""

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
ROOT = Path(__file__).resolve().parent.parent
# Never the project's .env, whatever a stand-in misses (B-264, as guards_hold.py does).
os.environ["ERRATA_DOTENV"] = "0"

import errata_bench.llm as llm_mod  # noqa: E402
from errata_bench.store.rows import append, load  # noqa: E402
from errata_bench.study import human as B  # noqa: E402
from errata_bench.study import merge as M  # noqa: E402
from errata_bench.study import review as A  # noqa: E402
from errata_bench.study import sessions as S  # noqa: E402

FAIL = []
CALLED = {}
RULES_SEEN = []   # the rules version each faked merge call was asked with


def check(ok, message):
    print(f"  {'ok  ' if ok else 'FAIL'}  {message}")
    if not ok:
        FAIL.append(message)


def records(name):
    CALLED[name] = CALLED.get(name, 0) + 1


def no_network(*a, **k):
    raise AssertionError("a study check reached the model client")


llm_mod.configure_client = no_network


def row(n, kind, content="", **extra):
    return {"session_id": "s1", "turn_number": n, "role": "user" if kind == "user_prompt" else "assistant",
            "turn_type": kind, "content": content, "tool_name": extra.pop("tool_name", None), "command": None,
            "file_path": None, "prompt_pushback": extra.pop("label", None), "is_conversational": True,
            "tool_call_id": extra.pop("call", None), "is_continuation": extra.pop("cont", False), **extra}


SESSION = [
    row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."),
    row(2, "assistant_response", "Looking into the timeout."),
    row(3, "tool_use", '{"command": "pytest"}', tool_name="Bash", call="c1"),
    row(4, "tool_result", "1 failed, 12 passed", call="c1"),
    row(5, "assistant_response", "Fixed it: all tests pass REPORT-ONE."),
    row(6, "user_prompt", "<system-reminder>NOTICE-TOKEN</system-reminder>"),
    row(7, "user_prompt", "No, the tests still fail REPLY-ONE.", label="failure_report"),
    row(8, "assistant_response", "Sorry, checking again PREVIOUS-TOKEN."),
    row(9, "tool_use", '{"command": "pytest -x"}', tool_name="Bash", call="c2"),
    row(10, "tool_result", "13 passed", call="c2"),
    row(11, "assistant_response", "Now everything passes REPORT-TWO."),
    row(12, "user_prompt", "[Request interrupted by user]"),
    row(13, "user_prompt", "Thanks REPLY-TWO."),
    row(14, "user_prompt", "Also add a retry REQUEST-THREE."),
    row(15, "assistant_response", "Added the retry WORK-THREE."),
    row(16, "user_prompt", "This session is being continued from a previous conversation COMPACT-TOKEN"),
    row(17, "assistant_response", "Done with the retry REPORT-THREE."),
    row(18, "user_prompt", "ok REPLY-THREE", label="correction"),
    row(19, "assistant_response", "AFTER-TOKEN"),
]


def section_kinds():
    print("\n1. which rows are the developer writing")
    kinds = {r["turn_number"]: S.prompt_kind(r) for r in SESSION if r["turn_type"] == "user_prompt"}
    check(kinds == {1: "developer", 6: None, 7: "developer", 12: None, 13: "developer", 14: "developer",
                    16: None, 18: "developer"}, f"notices, interruptions and compaction summaries are not: {kinds}")
    check(S.prompt_kind(row(1, "user_prompt", "Implement the following plan:\n# Plan")) == "plan",
          "an approved plan is a plan, not the developer's words")
    check(S.prompt_kind(row(1, "user_prompt", "   ")) is None and S.prompt_kind(row(1, "user_prompt", "x", cont=True))
          is None and S.prompt_kind(row(1, "user_prompt", "<bash-input>ls</bash-input>")) is None,
          "an empty row, a continuation and a shell command are not")


def section_reports():
    print("\n2. reports: the agent's work between two developer messages")
    reps = S.reports("s1", SESSION, "r/r")
    got = [(r.index, r.request_turn, r.handoff_turn, r.reply_label, r.interrupted) for r in reps]
    check(got == [(1, 1, 7, "failure_report", False), (2, 7, 13, None, True), (3, 13, 18, "correction", False)],
          f"three reports, a notice ends none, two messages in a row are one, an interruption is marked: {got}")
    check(reps[1].reply == "Thanks REPLY-TWO.\n\nAlso add a retry REQUEST-THREE." and reps[2].request == reps[1].reply
          and reps[2].request_end == 14, "two developer rows with no work between are one message, the reply and the "
          "next request")

    # With the raw transcript: what the developer typed decides, in order.
    long_msg = "Part one of a long pasted plan with many words. Part two of the same plan, split by the table."
    typed = S.Typed(typed=[S._norm("Fix the login timeout TASK-TOKEN."), S._norm(long_msg), S._norm("ok REPLY-THREE")],
                    commands={S._norm("/review check the tests")},
                    queued={S._norm("wait, use port 8080 QUEUED-TOKEN")},
                    other={S._norm("Base directory for this skill: /x # A skill's instructions for the agent"),
                           S._norm("Continue from where you left off.")})
    split = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."),
             row(2, "assistant_response", "Working on it."),
             row(3, "user_prompt", "Base directory for this skill: /x # A skill's instructions for the agent"),
             row(4, "assistant_response", "Using the skill, done SKILL-WORK."),
             row(5, "user_prompt", "Part one of a long pasted plan with many words."),
             row(6, "user_prompt", "Part two of the same plan, split by the table."),
             row(7, "assistant_response", "Done PLAN-WORK."),
             row(8, "user_prompt", "Part two of the same plan, split by the table."),
             row(9, "user_prompt", '<teammate-message teammate_id="w">done</teammate-message>'),
             row(10, "user_prompt", "a short reply nowhere in the transcript"),
             row(11, "user_prompt", "Continue from where you left off."),
             row(12, "assistant_response", "More work MORE-WORK."),
             row(13, "user_prompt", "ok REPLY-THREE")]
    said = S.row_kinds(split, typed)
    check(sorted(said) == [1, 5, 6, 13] and said[5][0] == said[6][0] == "developer",
          f"the transcript decides, in order: a skill's text, a later copy of a typed message, a teammate's message, "
          f"a row found nowhere and a meta entry are not the developer's; a split message's parts are: {sorted(said)}")
    rs = S.reports("s1", split, transcript=typed)
    check([(r.request_turn, r.request_end, r.handoff_turn) for r in rs] == [(1, 1, 5), (5, 6, 13)]
          and "SKILL-WORK" in S.window(rs[0], split)[0] and "Base directory" not in S.window(rs[0], split)[0]
          and "MORE-WORK" in S.window(rs[1], split)[0] and "teammate" not in S.window(rs[1], split)[0],
          "rows that are not the developer's end no report and are not shown; the agent's work around them stays")

    # The developer speaking inside a stretch: a refused tool call, a message queued while the agent worked, a command.
    inner = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."),
             row(2, "tool_use", '{"command": "git checkout main"}', tool_name="Bash", call="g"),
             row(3, "tool_result", "The user doesn't want to proceed with this tool use. The tool use was rejected "
                                   "(eg. if it was a file edit, the new_string was NOT written to the file). To tell you "
                                   "how to proceed, the user said:\nno, not git checkout REJECT-WORDS", call="g"),
             row(4, "assistant_response", "Understood, using a branch instead."),
             row(5, "queue_operation", '{"task_id": "x", "description": "a background task"}'),
             row(6, "queue_operation", "wait, use port 8080 QUEUED-TOKEN"),
             row(7, "tool_use", '{"command": "ls"}', tool_name="Bash", call="l"),
             row(8, "tool_result", "a b c", call="l"),
             row(9, "user_prompt", "<command-message>review</command-message>\n<command-name>/review</command-name>\n"
                                   "<command-args>check the tests</command-args>")]
    said = {n: v[:2] for n, v in S.row_kinds(inner, typed).items()}
    check([said.get(n) for n in (3, 5, 6, 9)] == [("rejection", "no, not git checkout REJECT-WORDS"), None,
                                                  ("queued", "wait, use port 8080 QUEUED-TOKEN"),
                                                  ("command", "/review check the tests")],
          f"a refused call carries the developer's words, a queued message and a command are theirs, a background "
          f"task is not: {[said.get(n) for n in (3, 5, 6, 9)]}")
    ri = S.reports("s1", inner, transcript=typed)
    w0 = S.window(ri[0], inner)[0]
    check([r.reply_kind for r in ri] == ["rejection", "queued", "command"] and "git checkout main" in w0
          and "REJECT-WORDS" not in w0 and "doesn't want to proceed" not in w0 and not ri[0].ends_with_report
          and "had not written a report" in w0 and ri[1].ends_with_report,
          "a refused call ends the report: the call is shown, the refusal is not, and the window says no report came")
    delivered = inner[:8] + [row(8.5, "assistant_response", "Port changed."),
                             row(8.7, "user_prompt", "wait, use port 8080 QUEUED-TOKEN")] + inner[8:]
    typed2 = S.Typed(typed=typed.typed[:1] + [S._norm("wait, use port 8080 QUEUED-TOKEN")] + typed.typed[1:],
                     commands=typed.commands, queued=typed.queued, other=typed.other)
    said2 = {n: v[:2] for n, v in S.row_kinds(delivered, typed2).items()}
    check(6 in said2 and 8.7 not in said2 and said2.get(9) == ("command", "/review check the tests"),
          "a queued message delivered later as a prompt is one message, counted where it was typed")
    loop = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."), row(2, "assistant_response", "w"),
            row(3, "queue_operation", "Check the run log for new progress since last check"),
            row(4, "assistant_response", "w"), row(5, "queue_operation", "Check the run log for new progress since last check"),
            row(6, "assistant_response", "w"), row(7, "queue_operation", "a real thought LOOP-TYPED")]
    typed3 = S.Typed(typed=typed.typed, commands={S._norm("/loop 10m Check the run log for new progress since last check")},
                     queued={S._norm("Check the run log for new progress since last check"), S._norm("a real thought LOOP-TYPED")},
                     other=set(), automatic={S._norm("Check the run log for new progress since last check")})
    said3 = S.row_kinds(loop, typed3)
    no_tr = S.row_kinds(loop)
    check(sorted(said3) == [1, 7] and sorted(no_tr) == [1, 3, 7],
          f"a /loop's timed prompt is not the developer typing; without a transcript only its repeats are dropped: "
          f"{sorted(said3)}, {sorted(no_tr)}")
    cmds = [row(1, "user_prompt", "<command-name>/loop</command-name><command-args>10m check CMD-LOOP</command-args>"),
            row(2, "assistant_response", "w"),
            row(3, "user_prompt", "<command-name>/loop</command-name><command-args>10m check CMD-LOOP</command-args>"),
            row(4, "assistant_response", "w"), row(5, "user_prompt", "<command-name>/commit</command-name>"),
            row(6, "assistant_response", "w"), row(7, "user_prompt", "<command-name>/commit</command-name>")]
    said4 = S.row_kinds(cmds)
    check(sorted(said4) == [1, 5, 7], f"a /loop's re-runs are not typed, but a /commit typed twice is: {sorted(said4)}")
    # An anonymised copy: the email the transcript replaced, an environment line the table redacted.
    anon = S.Typed(typed=[S._key("Fix the login timeout TASK-TOKEN."),
                          S._key("can you send a terse email to <PRESIDIO_ANONYMIZED_EMAIL_ADDRESS> about the release"),
                          S._key("Implement the following plan: set VITE_ANTHROPIC_API_KEY=abc and also update every "
                                 "config file in the repository so the client reads it at start"),
                          S._key("a message much further ahead that is ninety percent alike to the next row we send")],
                   commands=set(), queued=set(), other=set(), automatic=set())
    arows = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."), row(2, "assistant_response", "w"),
             row(3, "user_prompt", "can you send a terse email to someone@example.com about the release"),
             row(4, "assistant_response", "w"),
             row(5, "user_prompt", "Implement the following plan: set [REDACTED:ENV]=abc and also update every config "
                                   "file in the repository so the client reads it at start"),
             row(6, "assistant_response", "w")]
    said5 = S.row_kinds(arows, anon)
    check(sorted(said5) == [1, 3, 5] and said5[5][0] == "plan",
          f"a copy that differs only where one side was anonymised is still the developer's: {sorted(said5)}")
    far = S.Typed(typed=anon.typed[:1] + [S._key("an unrelated message in between")] + anon.typed[3:],
                  commands=set(), queued=set(), other=set(), automatic=set())
    said6 = S.row_kinds([arows[0], arows[1], row(3, "user_prompt",
                         "a message much further ahead that is ninety percent alike to the next row we sent")], far)
    check(sorted(said6) == [1], f"a near match is tried against the next expected entry only, never further ahead: "
          f"{sorted(said6)}")

    # A message that is only an image: kept when it opens a message the transcript holds as empty, or joins one.
    img = S.Typed(typed=[S._key("Fix the login timeout TASK-TOKEN."), "", S._key("and? IMG-REPLY")],
                  commands=set(), queued=set(), other={""}, automatic=set())
    irows = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."), row(2, "assistant_response", "w"),
             row(3, "user_prompt", "[Image: image/png]"), row(4, "assistant_response", "w"),
             row(5, "user_prompt", "and? IMG-REPLY"), row(6, "user_prompt", "[Image: image/png]"),
             row(7, "assistant_response", "w")]
    said7 = S.row_kinds(irows, img)
    check(sorted(said7) == [1, 3, 5, 6], f"an image-only message is the developer's, alone or as part of one: "
          f"{sorted(said7)}")

    # Queued: the table's whole queue entry as JSON; Claude Code's own notices and UI commands; a label carried.
    qtyped = S.Typed(typed=[S._key("Fix the login timeout TASK-TOKEN."), S._key("you didnt check the issue QJSON")],
                     commands=set(), queued={S._key("you didnt check the issue QJSON"), S._key("/model"),
                                             S._key("◇ ultraplan Starting Claude Code on the web")},
                     other=set(), automatic=set())
    qrows = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."), row(2, "assistant_response", "w"),
             row(3, "queue_operation", json.dumps({"type": "queue-operation", "operation": "enqueue",
                                                   "content": "you didnt check the issue QJSON"})),
             row(4, "queue_operation", "/model"), row(5, "queue_operation", "◇ ultraplan Starting Claude Code on the web"),
             row(6, "assistant_response", "w"),
             row(7, "user_prompt", "you didnt check the issue QJSON", label="correction"),
             row(8, "assistant_response", "w")]
    said8 = S.row_kinds(qrows, qtyped)
    check(sorted(said8) == [1, 3] and said8[3] == ("queued", "you didnt check the issue QJSON", "correction"),
          f"a queued message kept as JSON is the developer's and takes its delivery's label; a queued UI command and "
          f"Claude Code's notice are not: {said8}")

    # Refusals: Claude Code's appended note is not the developer's words. A synthetic message is not a report.
    note = S.row_kinds([row(1, "tool_result", "The user doesn't want to proceed with this tool use. To tell you how to "
                                              "proceed, the user said:\nuse main NOTE-WORDS\n\nNote: The user's next "
                                              "message may contain a correction or preference.", call="z")])
    words = note.get(1, (None, None, None))[1]
    check(words == "use main NOTE-WORDS", f"a refusal's words stop before Claude Code's note: {words!r}")
    ultra = S.row_kinds([row(1, "tool_result", "The user doesn't want to proceed with this tool use. To tell you how "
                                               "to proceed, the user said:\nPlan being refined via Ultraplan, please "
                                               "wait", call="u"),
                         row(2, "assistant_response", "w"),
                         row(3, "queue_operation", "Ultraplan approved in browser. Here is the plan: ULTRA-PLAN")])
    check(sorted(ultra) == [3] and ultra[3][0] == "plan",
          f"Claude Code's own Ultraplan refusal is not the developer's, and a plan approved in the browser is a plan: "
          f"{ultra}")
    syn = [row(1, "user_prompt", "go"), row(2, "tool_use", '{"command": "x"}', tool_name="Bash", call="s"),
           row(3, "tool_result", "ok", call="s"), row(4, "assistant_response", "Prompt is too long", model="<synthetic>"),
           row(5, "user_prompt", "and?")]
    rsyn = S.reports("s1", syn)
    check(len(rsyn) == 1 and not rsyn[0].ends_with_report and "Prompt is too long" not in S.window(rsyn[0], syn)[0],
          "Claude Code's own message is neither the agent's work nor its report")
    check(not S._automatic(S._key("continue, but use the v2 API"), {"continue"})
          and S._automatic(S._key("Check the run log for new progress since last check, then report"),
                           {S._key("Check the run log for new progress since last check")}),
          "a short automatic text drops only itself; a long one also its longer firings")

    # transcript_prompts reads Claude Code's own marks: a team session's developer, the agent's scheduled prompts.
    import errata_bench.corpus.recover as recover_mod
    entries = [
        {"type": "user", "message": {"content": "Fix it TEAM-DEV"}, "teamName": "t", "permissionMode": "default",
         "uuid": "1"},
        {"type": "user", "message": {"content": '<teammate-message teammate_id="w">done</teammate-message>'},
         "teamName": "t", "uuid": "2"},
        {"type": "user", "message": {"content": "This session is being continued SUMMARY"}, "isCompactSummary": True,
         "uuid": "3"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "ScheduleWakeup", "id": "w1",
                                                        "input": {"prompt": "/loop Check the run log for new progress"}}]},
         "uuid": "4"},
        {"type": "queue-operation", "operation": "enqueue", "content": "typed while busy"},
    ]
    with tempfile.TemporaryDirectory() as tdir:
        tpath = Path(tdir) / "t.jsonl"
        tpath.write_text("\n".join(json.dumps(e) for e in entries))
        kept_paths = (recover_mod.transcript_path, recover_mod.has_transcript)
        recover_mod.transcript_path, recover_mod.has_transcript = (lambda sid: tpath), (lambda sid: True)
        try:
            tp = S.transcript_prompts("s1")
        finally:
            recover_mod.transcript_path, recover_mod.has_transcript = kept_paths
    check(tp.typed == [S._key("Fix it TEAM-DEV")] and S._key("typed while busy") in tp.queued
          and S._key("Check the run log for new progress") in tp.automatic
          and S._key("/loop Check the run log for new progress") in tp.automatic
          and not any("SUMMARY" in x for x in tp.typed),
          "in a team session the developer's own message is typed and a teammate's is not; a summary is never "
          "typed; a wake-up the agent scheduled is nobody's typing")

    plain = S.row_kinds([row(1, "user_prompt", "<teammate-message teammate_id='a'>hi</teammate-message>"),
                         row(2, "user_prompt", "<command-name>/clear</command-name>")])
    check({n: v[:2] for n, v in plain.items()} == {2: ("command", "/clear")},
          f"without a transcript, a teammate is not the developer and a command is: {plain}")
    return reps


def section_windows(reps):
    print("\n3. the window: never the reply or anything after, never a notice as the developer's")
    w1, s1 = S.window(reps[0], SESSION)
    w3, s3 = S.window(reps[2], SESSION)
    check("REPORT-ONE" in w1 and "TASK-TOKEN" in w1 and "REPLY-ONE" not in w1 and "NOTICE-TOKEN" not in w1
          and "AFTER-TOKEN" not in w1, "report 1 shows the request and the work, not the reply or the notice")
    check(w1.count("TASK-TOKEN") == 1, "when the request is the first message it is shown once")
    check(all(t in w3 for t in ("TASK-TOKEN", "REPORT-TWO", "REPLY-TWO", "REQUEST-THREE", "WORK-THREE",
                                 "REPORT-THREE", "rows between them not shown"))
          and not any(t in w3 for t in ("REPLY-THREE", "COMPACT-TOKEN", "AFTER-TOKEN", "REPLY-ONE",
                                        "PREVIOUS-TOKEN")),
          "report 3 shows the task, the agent's last message before the request, the request and the work; "
          "no reply, no summary, no earlier work")
    work3 = S.work_part(w3)
    check(work3.startswith(S.WORK_HEADER) and "WORK-THREE" in work3 and "REPORT-TWO" not in work3
          and "TASK-TOKEN" not in work3, "the judged work is the part after the request, without the context above it")
    check(w3.rstrip().endswith("The developer has not replied yet.]"), "the window says where it stops")

    long_request = row(1, "user_prompt", "R" * (S.REQUEST_CHARS + 500))
    big = [long_request, row(2, "tool_use", '{"command": "cat big"}', tool_name="Bash", call="b"),
           row(3, "tool_result", "x" * 200_000, call="b"), row(4, "assistant_response", "FINAL-REPORT"),
           row(5, "user_prompt", "a reply")]
    rep = S.reports("s1", big)[0]
    wb, sb = S.window(rep, big)
    check("500 more characters of this message not shown" in wb, "a long request is cut, and says so")
    check(sb["tool_cap"] == S.TOOL_CAPS[0] and "FINAL-REPORT" in wb and len(wb) < 20_000,
          f"a huge result is cut to the tool cap and the report survives: cap {sb['tool_cap']}, {len(wb):,} chars")

    many = [row(1, "user_prompt", "go")]
    for n in range(2, 400):
        many.append(row(n, "assistant_response", f"message {n} " + "m" * 400))
    many += [row(400, "assistant_response", "LAST-WORDS"), row(401, "user_prompt", "a reply")]
    wm, sm = S.window(S.reports("s1", many)[0], many)
    check(sm["work_chars_left_out"] > 0 and "characters of this stretch not shown" in wm and "LAST-WORDS" in wm
          and sm["chars"] < S.WORK_CHARS + 2_000,
          f"past the smallest tool cap, the stretch's start goes, the report stays: {sm['work_chars_left_out']:,} out")


def section_quotes():
    print("\n4. every quote is checked against the text it claims to come from")
    window = "AGENT: All tests pass and the build is green."
    rv = A.Review(problems=[
        A.Problem(what_is_wrong="claims tests pass", kind="false_claim", turn_number=5, quote="All tests pass"),
        A.Problem(what_is_wrong="invented", kind="made_up_kind", turn_number=5, quote="I ran the full suite"),
    ])
    window = f"THE AGENT'S LAST MESSAGE BEFORE THE REQUEST:\nThe build is green.\n\n{S.WORK_HEADER} (turns after 1):\n" \
             "AGENT: All tests pass."
    rv.problems.append(A.Problem(what_is_wrong="the earlier claim", kind="false_claim", turn_number=1,
                                 quote="The build is green."))
    got = [(p["quote_found"], p["quote_in_work"], p["kind_known"]) for p in A.checked(rv, window)]
    check(got == [(True, True, True), (False, False, False), (True, False, True)],
          f"a quote in the work counts; an invented one, an unknown kind, and one quoting only the context do not: {got}")
    rp = B.Reply(pushback_kind="failure_report", what_developer_objects_to="tests fail", developer_quote="still fail",
                 objection_kind="real_error", failure_modes=["false_claim"], target_shown=True, target_turn=5,
                 target_quote="tests are green")
    c = B.checked(rp, window, "No, the tests still fail.")
    check(c["developer_quote_found"] and not c["target_quote_found"] and c["labels_known"],
          "the developer's quote is checked in the reply, the target in the window")
    bad = B.checked(B.Reply(pushback_kind="failure_report", objection_kind="annoyed"), window, "x")
    odd = B.checked(B.Reply(pushback_kind="complaint"), window, "x")
    check(not bad["labels_known"] and not odd["labels_known"] and not odd["is_pushback"]
          and B.checked(B.Reply(pushback_kind="non_pushback"), window, "x")["labels_known"],
          "an objection kind outside the four, or a pushback kind outside SWE-chat's, is marked, and is not pushback")
    cands = [{"problem_id": "r1p0", "report": 1, "what_is_wrong": "claims tests pass", "quote": "All tests pass"},
             {"problem_id": "r1p1", "report": 1, "what_is_wrong": "no retry", "quote": "x"}]
    mg = M.Merge(verdicts=[M.Verdict(problem_id="r1p0", match="same", developer_words="", reviewer_words="tests pass",
                                     reason="r")])
    out = M.checked(mg, cands, "the tests still fail")
    check(out[0]["match"] == "same" and not out[0]["developer_words_found"] and out[0]["reviewer_words_found"]
          and out[1]["missing"], "a 'same' with no developer words is marked, a skipped candidate is recorded")


def section_candidates():
    print("\n5. the merge compares a pushback with the problems of reports k-2..k, quotes found only")
    p = lambda found: {"what_is_wrong": "w", "quote": "q", "quote_found": found, "quote_in_work": found}  # noqa: E731
    by = {1: [p(True)], 2: [p(False), p(True)], 3: [p(True)], 4: [p(True)]}
    ids = [c["problem_id"] for c in M.candidates(by, 4)]
    check(ids == ["r2p1", "r3p0", "r4p0"], f"report 1 is too early, an unfound quote is left out, ids keep their place: {ids}")


class Result(SimpleNamespace):
    pass


def fake_result(output, inp=1000, out=200):
    usage = SimpleNamespace(requests=1, input_tokens=inp, output_tokens=out, total_tokens=inp + out,
                            input_tokens_details=SimpleNamespace(cached_tokens=0),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=0))
    return Result(final_output=output, context_wrapper=SimpleNamespace(usage=usage))


def load_script():
    spec = importlib.util.spec_from_file_location("study_script", Path("scripts/study.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def section_runner(reps):
    print("\n6. the runner: resumes, asks errored rows again, stops at its spend line")
    study = load_script()
    kept = (A.review, B.classify, M.merge)
    flaky = {"left": 1}

    async def fake_review(window_text, *, model, framing="outside", max_turns=3):
        records("review")
        records(f"review-{framing}")
        if flaky["left"] and "REPORT-TWO" in window_text:
            flaky["left"] -= 1
            raise RuntimeError("a transient failure")
        return fake_result(A.Review(problems=[A.Problem(what_is_wrong="claims success unchecked", kind="false_claim",
                                                        turn_number=5, quote="all tests pass")]))

    async def fake_classify(window_text, reply, handoff_turn, *, model, max_turns=3):
        records("classify")
        assert "PLAN-REPLY" not in reply, "an approved plan was sent to the classifier"
        push = "REPLY-ONE" in reply or "REPLY-THREE" in reply
        return fake_result(B.Reply(pushback_kind="failure_report" if push else "non_pushback",
                                   what_developer_objects_to="tests fail" if push else "",
                                   developer_quote="tests still fail" if "REPLY-ONE" in reply else "",
                                   objection_kind="real_error" if push else ""))

    async def fake_merge(reply_row, reply_text, cands, *, model, max_turns=3, rules=None):
        records("merge")
        RULES_SEEN.append(rules)
        return fake_result(M.Merge(verdicts=[M.Verdict(problem_id=c["problem_id"], match="same",
                                                        developer_words=" ".join(reply_text.split()[:2]),
                                                        reviewer_words="claims success", reason="r")
                                             for c in cands]))

    A.review, B.classify, M.merge = fake_review, fake_classify, fake_merge
    try:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            for r in reps:
                text, stats = S.window(r, SESSION)
                append(run / "reports.jsonl", S.report_row(r, text, stats))
            plan = {**S.report_row(reps[0], *S.window(reps[0], SESSION)), "index": 4, "reply_kind": "plan",
                    "reply": "Implement the following plan: PLAN-REPLY"}
            append(run / "reports.jsonl", plan)
            args = SimpleNamespace(run=str(run), max_usd=100.0, concurrency=2, limit=0)
            quiet = io.StringIO()
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rc1 = study.review(args)
                rows1 = load(run / "reviews.jsonl")
                rc2 = study.review(args)
                rows2 = load(run / "reviews.jsonl")
            errored = [r for r in rows1 if r.get("error")]
            check(rc1 == 1 and len(rows1) == 4 and len(errored) == 1,
                  f"an errored call is written as an errored row and the stage says so: rc {rc1}, {len(errored)} errored")
            ok_rows = [r for r in rows2 if not r.get("error")]
            check(rc2 == 0 and len(ok_rows) == 4 and all(r["usage"] and r["model"] for r in ok_rows),
                  f"run again, only the errored row is asked again, and every row keeps its usage: rc {rc2}")
            found = {r["index"]: r["problems"][0]["quote_found"] for r in ok_rows if r["index"] < 4}
            inwork = {r["index"]: r["problems"][0]["quote_in_work"] for r in ok_rows if r["index"] < 4}
            check(found == {1: True, 2: True, 3: False} and inwork == {1: True, 2: False, 3: False},
                  f"report 2 shows report 1's claim as context: found there, but not in its work: {found}, {inwork}")
            n_before = CALLED.get("review", 0)
            with contextlib.redirect_stdout(quiet):
                rc3 = study.review(args)
            check(CALLED.get("review", 0) == n_before and rc3 == 0, "a finished stage asks nothing again")

            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rch = study.human(args)
                rcm = study.merge(args)
            replies = load(run / "replies.jsonl")
            merges = load(run / "merges.jsonl")
            check(rch == 0 and len(replies) == 4 and sum(r["is_pushback"] for r in replies) == 2
                  and all(r.get("reply_label", "unset") != "unset" for r in replies)
                  and [r.get("approved_plan") for r in replies if r["index"] == 4] == [True]
                  and CALLED.get("classify") == 3,
                  "thread B reads every reply but an approved plan, which needs no call, and keeps SWE-chat's label")
            check(rcm == 0 and len(merges) == 2 and CALLED.get("merge", 0) == 2,
                  f"the merge asks once for each pushback, by either reading, with problems to compare: "
                  f"{len(merges)} rows")
            check(all(r.get("rules") == M.RULES for r in merges) and RULES_SEEN[-2:] == [M.RULES, M.RULES],
                  f"each merge row records the version of the rules it was asked under: "
                  f"{[r.get('rules') for r in merges]}, asked {RULES_SEEN[-2:]}")
            n_rows = len(merges)
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcmix = study.merge(SimpleNamespace(run=str(run), max_usd=100.0, concurrency=1, limit=0, rules=1))
            check(rcmix == 2 and len(load(run / "merges.jsonl")) == n_rows,
                  "a folder of merges under one version of the rules refuses a run under another")
            with contextlib.redirect_stdout(quiet):
                rct = study.tally(SimpleNamespace(run=str(run)))
            t = json.loads((run / "tally.json").read_text())
            check(rct == 0 and t["pushbacks"] == 2 and t["pushback_outcomes"] == {"same": 2}
                  and t["caught_same"]["real_error"]["share"] == 1.0,
                  f"the tally counts each pushback's best match: {t['pushback_outcomes']}")
            # A 'same' whose overlap words were not found does not count; an unmerged pushback is left out, not missed.
            mrows = load(run / "merges.jsonl")
            mrows[0]["verdicts"][0]["developer_words_found"] = False
            from errata_bench.store.rows import replace as replace_rows
            replace_rows(run / "merges.jsonl", mrows[:1])
            with contextlib.redirect_stdout(quiet):
                study.tally(SimpleNamespace(run=str(run)))
            t2 = json.loads((run / "tally.json").read_text())
            check(t2["pushback_outcomes"] == {"same, words not found": 1, "not merged": 1}
                  and t2["caught_same"]["real_error"]["n"] == 1 and t2["caught_same"]["real_error"]["share"] == 0.0,
                  f"a 'same' without its words is not caught, and an unmerged pushback is not counted: "
                  f"{t2['pushback_outcomes']}, n={t2['caught_same']['real_error']['n']}")
            replace_rows(run / "merges.jsonl", mrows)
            # A merge whose verdicts all lost their ids is incomplete: left out of the share, not a miss.
            mrows2 = load(run / "merges.jsonl")
            for v in mrows2[0]["verdicts"]:
                v["missing"], v["match"] = True, None
            replace_rows(run / "merges.jsonl", mrows2)
            with contextlib.redirect_stdout(quiet):
                study.tally(SimpleNamespace(run=str(run)))
            t3 = json.loads((run / "tally.json").read_text())
            check(t3["pushback_outcomes"].get("incomplete") == 1 and t3["caught_same"]["real_error"]["n"] == 1,
                  f"a merge whose verdicts went missing is incomplete, not a miss: {t3['pushback_outcomes']}")
            replace_rows(run / "merges.jsonl", mrows)
            real_merge = M.merge

            async def wrong_ids(reply_row, reply_text, cands, *, model, max_turns=3, rules=None):
                return fake_result(M.Merge(verdicts=[M.Verdict(problem_id="p0", match="same", reason="r")]))
            M.merge = wrong_ids
            try:
                (run / "merges.jsonl").unlink()
                with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    rcw = study.merge(SimpleNamespace(run=str(run), max_usd=100.0, concurrency=1, limit=0))
            finally:
                M.merge = real_merge
            wrong = load(run / "merges.jsonl")
            check(rcw == 1 and wrong and all(r.get("error") for r in wrong),
                  "a merge that names no problem by its id is written as an errored row, asked again on resume")
            # --limit caps the merge as it caps the other stages: a smoke merge once ran 66 calls (10-03).
            (run / "merges.jsonl").unlink()
            n_before = CALLED.get("merge", 0)
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcl = study.merge(SimpleNamespace(run=str(run), max_usd=100.0, concurrency=1, limit=1, rules=1))
            limited = load(run / "merges.jsonl")
            check(rcl == 0 and len(limited) == 1 and CALLED.get("merge", 0) == n_before + 1,
                  "--limit caps the merge's calls, as it caps the review's and the reply reader's")
            check(limited[0].get("rules") == 1 and RULES_SEEN[-1] == 1,
                  "--rules 1 asks under the 10-03 instructions and records it")
            replace_rows(run / "merges.jsonl", mrows)
            with contextlib.redirect_stdout(quiet):
                study.sheet(SimpleNamespace(run=str(run), matches=150, alone=50))
            alone_md = (run / "alone.md").read_text()
            check((run / "labels.csv").exists() and (run / "key.csv").exists()
                  and "same" not in (run / "labels.csv").read_text().split("\n", 1)[1]
                  and "~~~~" in alone_md and "open only after you decide" in alone_md,
                  "the hand-label sheet is written blind, the model's verdicts apart, and alone.md fences each window")
            check(study._fenced("a\n~~~~~\nb").startswith("~~~~~~\n"), "a fence is longer than any run of tildes it holds")

            # The spend line: every call priced at the guard's rate, stopping before the next once reached.
            run2 = Path(tmp) / "run2"
            run2.mkdir()
            for i in range(10):
                r = reps[0]
                text, stats = S.window(r, SESSION)
                append(run2 / "reports.jsonl", {**S.report_row(r, text, stats), "index": i + 1})
            one = study._spend.priced(study._model(), {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 0},
                                      set())
            line = SimpleNamespace(run=str(run2), max_usd=one * 2.5, concurrency=1, limit=0)
            flaky["left"] = 0
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcs = study.review(line)
            n = len(load(run2 / "reviews.jsonl"))
            check(rcs == 3 and n == 3, f"the stage stops starting calls once the spend reaches --max-usd: {n} calls, rc {rcs}")
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcs2 = study.review(line)
            check(rcs2 == 3 and len(load(run2 / "reviews.jsonl")) == 3, "run again over the line, it starts nothing")

            # The self-review framing: its own files, its own merge, and the same spend line.
            spent_before = study._spent(run)
            selfargs = SimpleNamespace(run=str(run), max_usd=100.0, concurrency=2, limit=0, framing="self")
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcf = study.review(selfargs)
                rcfm = study.merge(selfargs)
                study.tally(SimpleNamespace(run=str(run), framing="self"))
            selfrows = load(run / "reviews-self.jsonl")
            check(rcf == 0 and len(selfrows) == 4 and all(r["framing"] == "self" for r in selfrows)
                  and len(load(run / "reviews.jsonl")) == 4 and CALLED.get("review-self") == 4
                  and (run / "merges-self.jsonl").exists() and (run / "tally-self.json").exists()
                  and study._spent(run) > spent_before,
                  "the self framing writes its own reviews, merges and tally, and its spend joins the run's")
            check(A.SELF_INSTRUCTIONS.startswith("You are the coding agent") and "your work" in A.SELF_INSTRUCTIONS
                  and A.SELF_INSTRUCTIONS.split("You are shown", 1)[1] == A.INSTRUCTIONS.split("You are shown", 1)[1]
                  .replace("problem in this work", "problem in your work"),
                  "the self framing differs from the outside one only in who the work belongs to")

            # Pooling runs: every stage's rows, and never one session in two runs.
            pooled = Path(tmp) / "pooled"
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcc = study.combine(SimpleNamespace(into=str(pooled), sources=[str(run)]))
                rcc2 = study.combine(SimpleNamespace(into=str(Path(tmp) / "pooled2"), sources=[str(run), str(run)]))
            check(rcc == 0 and len(load(pooled / "reviews.jsonl")) == len(load(run / "reviews.jsonl")) and rcc2 == 2,
                  "combine pools a run's stages, and refuses two runs sharing a session")
            # One process per stage: a second one is refused, before any call.
            lockargs = SimpleNamespace(run=str(run2), max_usd=100.0, concurrency=1, limit=0)
            from errata_bench.store.rows import only_one
            refused = False
            with only_one(run2, "a stand-in holding the lock", name="review-outside.lock"):
                try:
                    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                        study.review(lockargs)
                except SystemExit:
                    refused = True
            check(refused, "a second review over the same run is refused while one holds its lock")
            # Re-read before every call: spend another process writes mid-run stops this one at the line.
            run3 = Path(tmp) / "run3"
            run3.mkdir()
            for i in range(6):
                text, stats = S.window(reps[0], SESSION)
                append(run3 / "reports.jsonl", {**S.report_row(reps[0], text, stats), "index": i + 1})
            real_review = A.review

            async def spending_review(window_text, *, model, framing="outside", max_turns=3):
                # Another process's row lands while this call is in flight, worth the whole line.
                append(run3 / "replies.jsonl", {"session_id": "x", "index": 99, "model": study._model(),
                                                "usage": {"input_tokens": 10_000_000, "output_tokens": 0,
                                                          "cached_tokens": 0}})
                return await real_review(window_text, model=model, framing=framing, max_turns=max_turns)
            A.review = spending_review
            try:
                with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    rcr = study.review(SimpleNamespace(run=str(run3), max_usd=50.0, concurrency=1, limit=0))
            finally:
                A.review = real_review
            check(rcr == 3 and len(load(run3 / "reviews.jsonl")) == 1,
                  f"spend another process writes mid-run stops the stage at the line: "
                  f"{len(load(run3 / 'reviews.jsonl'))} call(s), rc {rcr}")
            # The line is shared by the stages: with the line spent by the reviews, the classifier starts nothing.
            n_classify = CALLED.get("classify", 0)
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rcl = study.human(line)
            check(rcl == 3 and CALLED.get("classify", 0) == n_classify,
                  "the spend line is the run's: another stage over a spent line starts nothing")

            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                rcp = study.prepare(SimpleNamespace(out=str(run), session=None, sessions=1, per_repo=1, max_reports=0,
                                                    random=0, batch=0))
            check(rcp == 2 and "refused" in out.getvalue(), "a prepared run is never prepared over")
    finally:
        A.review, B.classify, M.merge = kept
    # The merge and the person checking it by hand draw the line between 'same' and 'related' from one text.
    flat = lambda text: " ".join(text.split())
    guide = flat((ROOT / "docs" / "study.md").read_text())
    examples = ['Developer: "the tests still fail". Reviewer: "claims the tests pass but never ran them".',
                "the reviewer flagged the very claim the developer found false.",
                'Developer: "the button does nothing on mobile". Reviewer: "the click handler was not tested on '
                'touch devices".', "the same work, but the reviewer did not say the button fails."]
    check(all(e in flat(M.INSTRUCTIONS[M.RULES]) and e in guide for e in examples)
          and not any(e in flat(M.INSTRUCTIONS[1]) for e in examples),
          "the merge's rules give the hand-check guide's worked examples word for word; version 1 has none")
    asked = {v: flat(M.agent_for("stand-in", v).instructions) for v in M.INSTRUCTIONS}
    check(all(e in asked[2] for e in examples) and not any(e in asked[1] for e in examples),
          "the merge's agent is built from the version of the rules it is asked for")
    # The real merge call passes its version on: the model is never reached, the agent is caught on its way.
    import agents
    seen = {}

    async def caught_run(agent, text, max_turns=3):
        seen["instructions"] = flat(agent.instructions)
        return fake_result(M.Merge(verdicts=[]))
    real_run, real_configure = agents.Runner.run, llm_mod.configure_client
    agents.Runner.run, llm_mod.configure_client = caught_run, (lambda: None)
    try:
        asyncio.run(M.merge({}, "the tests still fail", [], model="stand-in", rules=1))
        v1 = seen.pop("instructions", "")
        asyncio.run(M.merge({}, "the tests still fail", [], model="stand-in", rules=2))
        v2 = seen.pop("instructions", "")
    finally:
        agents.Runner.run, llm_mod.configure_client = real_run, real_configure
    check(bool(v1 and v2) and not any(e in v1 for e in examples) and all(e in v2 for e in examples),
          "a merge call asks under the version of the rules it is given")
    check(all(CALLED.get(k) for k in ("review", "classify", "merge")), f"every stand-in ran: {CALLED}")


def section_agreement():
    print("\n7. the hand check's numbers: the person's labels against the merge, and their calls")
    import csv
    study = load_script()
    with tempfile.TemporaryDirectory() as tmp:
        run, other = Path(tmp) / "run", Path(tmp) / "other"
        run.mkdir()
        other.mkdir()
        # Five merge decisions; the person labels four, one of them unreadably.
        labels = [("1", "Same "), ("2", "related"), ("3", "maybe"), ("4", ""), ("5", "different.")]
        model = {"1": "same", "2": "different", "3": "same", "4": "related", "5": "different"}
        with open(run / "labels.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["item", "developer_reply", "reviewer_problem", "reviewer_quote", "in English",
                        "your_label (same / related / different)", "note"])
            for item, label in labels:
                w.writerow([item, "reply", "problem", "quote", "", label, ""])
        with open(run / "key.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["item", "session", "handback", "problem_id", "model_match", "model_reason"])
            for item, _ in labels:
                w.writerow([item, f"s{item}", 3, "r3p1", model[item], "why"])
        for item in ("1", "2", "3", "4", "5"):
            append(run / "merges.jsonl", {"session_id": f"s{item}", "index": 3, "verdicts": [
                {"problem_id": "r3p1", "report": 3, "match": model[item], "missing": False}], "model": "m", "usage": None})
        # The same pushbacks under rules 2: item 2 now 'related'; item 1's other problem says 'different'; item 5 absent.
        append(other / "merges.jsonl", {"session_id": "s1", "index": 3, "rules": 2, "verdicts": [
            {"problem_id": "r3p1", "report": 3, "match": "same", "missing": False},
            {"problem_id": "r3p0", "report": 3, "match": "different", "missing": False}], "model": "m", "usage": None})
        append(other / "merges.jsonl", {"session_id": "s2", "index": 3, "rules": 2, "verdicts": [
            {"problem_id": "r3p1", "report": 3, "match": "related", "missing": False}], "model": "m", "usage": None})
        item = lambda n, sid, call: (f"\n---\n\n## {n}. session {sid}, handback 3 (r3p1)\n\n**The reviewer:** x\n\n"
                                     f"**Your call:** {call}\n\n<details><summary>The work it read</summary>\n\n"
                                     f"~~~~\nwork\n~~~~\n\n</details>\n")
        (run / "alone.md").write_text("# The reviewer's problems that no pushback matched\n" + item(1, "aaaa", "real")
                                      + item(2, "bbbb", "\n\nFalse alarm.") + item(3, "cccc", "maybe")
                                      + item(4, "dddd", "can\u2019t tell") + item(5, "eeee", ""))
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            rc = study.agreement(SimpleNamespace(run=str(run), also=[str(other)], framing="outside"))
        out = json.loads((run / "agreement.json").read_text())
        first, second = out["merge"]["run (rules [1])"], out["merge"]["other (rules [2])"]
        check(rc == 0 and out["labelled"] == 3 and out["unreadable_labels"] == ["3"],
              f"labels are read whatever their case, spaces or full stop, and an unreadable one is named: "
              f"{out['labelled']} labelled, unreadable {out['unreadable_labels']}")
        check(first["items"] == 3 and abs(first["agreement"] - 2 / 3) < 0.01 and first["trusted (kappa at least 0.7)"] is False,
              f"the person against this run's merge, item by item: {first['items']} items, agreement {first['agreement']}")
        check(second["items"] == 2 and second["agreement"] == 1.0,
              f"against another run's merge of the same pushbacks, by the same problem id, on the items it holds: "
              f"{second['items']} items, agreement {second['agreement']}")
        a = out["alone"]
        check(a["counts"] == {"real": 1, "false alarm": 1, "can't tell": 1} and a["unreadable_calls"] == ["3"]
              and a["real_of_decided"]["n"] == 2 and a["real_of_decided"]["share"] == 0.5,
              f"the calls on unmatched problems, on the line or the next, and can't tell left out of the share: "
              f"{a['counts']}, unreadable {a['unreadable_calls']}, {a['real_of_decided']}")


def main():
    section_kinds()
    reps = section_reports()
    section_windows(reps)
    section_quotes()
    section_candidates()
    section_runner(reps)
    section_agreement()
    total = sum(1 for _ in FAIL)
    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{total} FAILED"))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
