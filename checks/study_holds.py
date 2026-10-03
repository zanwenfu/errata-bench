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
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")

import errata_bench.llm as llm_mod  # noqa: E402
from errata_bench.store.rows import append, load  # noqa: E402
from errata_bench.study import human as B  # noqa: E402
from errata_bench.study import merge as M  # noqa: E402
from errata_bench.study import review as A  # noqa: E402
from errata_bench.study import sessions as S  # noqa: E402

FAIL = []
CALLED = {}


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

    # With the raw transcript: a skill's text is not the developer's; parts of one typed message are.
    typed = S.Typed(typed=[S._norm("Fix the login timeout TASK-TOKEN."), S._norm("No, the tests still fail REPLY-ONE."),
                           S._norm("Part one of a long pasted plan with many words. Part two of the same plan, "
                                   "split by the table."), S._norm("ok REPLY-THREE")],
                    other=[S._norm("Base directory for this skill: /x # A skill's instructions for the agent")])
    split = [row(1, "user_prompt", "Fix the login timeout TASK-TOKEN."),
             row(2, "assistant_response", "Working on it."),
             row(3, "user_prompt", "Base directory for this skill: /x # A skill's instructions for the agent"),
             row(4, "assistant_response", "Using the skill, done SKILL-WORK."),
             row(5, "user_prompt", "Part one of a long pasted plan with many words."),
             row(6, "user_prompt", "Part two of the same plan, split by the table."),
             row(7, "assistant_response", "Done PLAN-WORK."),
             row(8, "user_prompt", "a short reply nowhere in the transcript")]
    kinds = [S.prompt_kind(t, typed) for t in split if t["turn_type"] == "user_prompt"]
    check(kinds == ["developer", None, "developer", "developer", "developer"],
          f"the transcript decides: a skill's text is not the developer's, a split message's parts are, a row in "
          f"neither keeps the table's word: {kinds}")
    rs = S.reports("s1", split, transcript=typed)
    check([(r.request_turn, r.request_end, r.handoff_turn) for r in rs] == [(1, 1, 5), (5, 6, 8)]
          and "SKILL-WORK" in S.window(rs[0], split)[0] and "Base directory" not in S.window(rs[0], split)[0],
          "the skill's text ends no report and is not shown; its work stays in the stretch")
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

    async def fake_review(window_text, *, model, max_turns=3):
        records("review")
        if flaky["left"] and "REPORT-TWO" in window_text:
            flaky["left"] -= 1
            raise RuntimeError("a transient failure")
        return fake_result(A.Review(problems=[A.Problem(what_is_wrong="claims success unchecked", kind="false_claim",
                                                        turn_number=5, quote="all tests pass")]))

    async def fake_classify(window_text, reply, handoff_turn, *, model, max_turns=3):
        records("classify")
        push = "REPLY-ONE" in reply or "REPLY-THREE" in reply
        return fake_result(B.Reply(pushback_kind="failure_report" if push else "non_pushback",
                                   what_developer_objects_to="tests fail" if push else "",
                                   developer_quote="tests still fail" if "REPLY-ONE" in reply else "",
                                   objection_kind="real_error" if push else ""))

    async def fake_merge(reply_row, reply_text, cands, *, model, max_turns=3):
        records("merge")
        return fake_result(M.Merge(verdicts=[M.Verdict(problem_id=c["problem_id"], match="same",
                                                        developer_words="tests still fail",
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
            args = SimpleNamespace(run=str(run), max_usd=100.0, concurrency=2, limit=0)
            quiet = io.StringIO()
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                rc1 = study.review(args)
                rows1 = load(run / "reviews.jsonl")
                rc2 = study.review(args)
                rows2 = load(run / "reviews.jsonl")
            errored = [r for r in rows1 if r.get("error")]
            check(rc1 == 1 and len(rows1) == 3 and len(errored) == 1,
                  f"an errored call is written as an errored row and the stage says so: rc {rc1}, {len(errored)} errored")
            ok_rows = [r for r in rows2 if not r.get("error")]
            check(rc2 == 0 and len(ok_rows) == 3 and all(r["usage"] and r["model"] for r in ok_rows),
                  f"run again, only the errored row is asked again, and every row keeps its usage: rc {rc2}")
            found = {r["index"]: r["problems"][0]["quote_found"] for r in ok_rows}
            inwork = {r["index"]: r["problems"][0]["quote_in_work"] for r in ok_rows}
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
            check(rch == 0 and len(replies) == 3 and sum(r["is_pushback"] for r in replies) == 2
                  and all(r.get("reply_label", "unset") != "unset" for r in replies),
                  "thread B reads every reply, not only the labelled ones, and keeps SWE-chat's label")
            check(rcm == 0 and len(merges) == 2 and CALLED.get("merge", 0) == 2,
                  f"the merge asks once for each pushback with problems to compare: {len(merges)} rows")
            with contextlib.redirect_stdout(quiet):
                rct = study.tally(SimpleNamespace(run=str(run)))
            t = json.loads((run / "tally.json").read_text())
            check(rct == 0 and t["pushbacks"] == 2 and t["pushbacks_by_kind_and_best_match"]["real_error"].get("same")
                  == 2, f"the tally counts each pushback's best match: {t['pushbacks_by_kind_and_best_match']}")
            with contextlib.redirect_stdout(quiet):
                study.sheet(SimpleNamespace(run=str(run), matches=150, alone=50))
            check((run / "labels.csv").exists() and (run / "key.csv").exists() and (run / "alone.md").exists()
                  and "same" not in (run / "labels.csv").read_text().split("\n", 1)[1],
                  "the hand-label sheet is written blind; the model's verdicts are in a separate file")

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

            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                rcp = study.prepare(SimpleNamespace(out=str(run), session=None, sessions=1, per_repo=1, max_reports=0))
            check(rcp == 2 and "refused" in out.getvalue(), "a prepared run is never prepared over")
    finally:
        A.review, B.classify, M.merge = kept
    check(all(CALLED.get(k) for k in ("review", "classify", "merge")), f"every stand-in ran: {CALLED}")


def main():
    section_kinds()
    reps = section_reports()
    section_windows(reps)
    section_quotes()
    section_candidates()
    section_runner(reps)
    total = sum(1 for _ in FAIL)
    print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{total} FAILED"))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
