# Splitting grading out of the attempt stage must change how fast the work runs
# and nothing else.
#
# What this isolates is `pipeline.py` alone: the old module is loaded into the
# current package, so its relative imports resolve to today's structure.py,
# judge.py and spec.py, and a change confined to those files is invisible here
# by construction. Some of the assertions below do reach them -- dropping
# files_changed from Structure.to_json, or turning an unasked edit declaration
# into False, both fail here -- but that is incidental, not coverage.
# `judge.can_be_scored` and `judge.line_holds` are covered in
# fixes_are_still_in.py under GATE-1 and GATE-2, and nowhere else. This runs both the old combined stage (taken from git) and
# the new pair against the same fakes, and compares the rows they produce field
# by field. Fakes stand in for the model and the container, so it runs in
# seconds with no network and no Docker.
import asyncio, importlib.util, json, os, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, "src")
os.environ["ERRATA_JUDGE_MODEL"] = "the-grader"
os.environ["ERRATA_MODEL"] = "the-candidate"

from errata_bench.score import attempt as attempt_mod
from errata_bench.construct import container as container_mod
from errata_bench.corpus import sessions as corpus
from errata_bench.score import judge as judge_mod
from errata_bench import llm as reader
from errata_bench.corpus import turns as turns_mod
from errata_bench.score import trace as trace_mod
from errata_bench.score.attempt import Attempt, ToolCall
from errata_bench.score.judge import Judgement
from errata_bench.stages import stage_attempt, stage_build, stage_grade
from errata_bench.store import Paths, load
# from the code, not a copy: a control added there must appear in every
# fixture, or the fixture quietly stops admitting its tasks.
from errata_bench.instrument.control import CONTROLS
CONTROL_NAMES = tuple(c.name for c in CONTROLS)
from errata_bench.spec import Task, write
from errata_bench.score.structure import Structure
from errata_bench.score.trace import Claim, TraceCheck

FAIL = []


def check(ok, message):
    if not ok:
        FAIL.append(message)
        print(f"  FAIL  {message}")
    else:
        print(f"  ok    {message}")


# ---------------------------------------------------------------- the fakes

live = {"run": 0, "grade": 0}
peak = {"run": 0, "grade": 0}
seen = {"judge": [], "trace": [], "graders": set(), "candidate_calls": 0}
grading_during_attempt = {"count": 0}
in_attempt_stage = {"now": False}


def note(kind, delta):
    live[kind] += delta
    peak[kind] = max(peak[kind], live[kind])


# Two tasks answer, one answers with nothing, one fails outright, one grades
# badly -- every path a row can take.
REPLIES = {
    "task-0": "I read the config and the value is wrong.",
    "task-1": "I ran the tests and they pass.",
    "task-2": "",                       # used every turn, never reported
    "task-3": "This one breaks the candidate.",
    "task-4": "This one breaks the judge.",
}


async def fake_run(task, *, image=None, turns=None, **kw):
    seen["candidate_calls"] += 1
    note("run", +1)
    await asyncio.sleep(0.10)
    note("run", -1)
    if task.task_id == "task-3":
        return Attempt(task.task_id, "the-candidate", error="Timeout: the container died")
    return Attempt(
        task.task_id,
        "the-candidate",
        reply=REPLIES[task.task_id],
        out_of_time=(task.task_id == "task-2"),
        tool_calls=[
            ToolCall("read_file", {"path": f"{task.task_id}.py"}, result="x = 1"),
            ToolCall("run_command", {"command": "npm test"}, result="2 passing"),
        ],
        actual_changes={f"{task.task_id}.py": "modified"},
        final_state={f"{task.task_id}.py": "x = 1"},
        environment=image or "host",
    )


async def fake_judge(task, answer, *, model=None, swap_references=False, tool_calls=None):
    if in_attempt_stage["now"]:
        grading_during_attempt["count"] += 1
    seen["graders"].add(model)
    seen["judge"].append((task.task_id, model, swap_references, answer,
                          json.dumps(tool_calls, sort_keys=True)))
    note("grade", +1)
    await asyncio.sleep(0.25)
    note("grade", -1)
    if task.task_id == "task-4":
        raise RuntimeError("the judge deployment returned nothing")
    return Judgement(
        addresses_defect=True,
        defect_remains=(task.task_id == "task-1"),
        makes_unverified_claim=(task.task_id == "task-1"),
        reports_limits=False,
        quote=answer[:20],
        reasoning=f"read {task.task_id}",
        quote_found=True,
    )


async def fake_check(answer, calls, *, model=None, context="", given=""):
    if in_attempt_stage["now"]:
        grading_during_attempt["count"] += 1
    seen["graders"].add(model)
    seen["trace"].append((answer, model, json.dumps(calls, sort_keys=True), context, given))
    note("grade", +1)
    await asyncio.sleep(0.25)
    note("grade", -1)
    supported = "ran the tests" not in answer
    return TraceCheck(
        claims=[Claim(claim="ran the tests", supported=supported, evidence="npm test")],
        reasoning="checked",
    )


attempt_mod.run = fake_run
judge_mod.judge = fake_judge
trace_mod.check = fake_check
attempt_mod.transcript_for = lambda task, turns: f"conversation for {task.task_id}"
attempt_mod.transcripts_for = lambda tasks: {t.task_id: f"conversation for {t.task_id}" for t in tasks}
container_mod.image_for = lambda lang, **kw: "node:22"
container_mod.sweep = lambda: None
container_mod.max_containers = lambda: 2
corpus.load_repos = lambda: {}
turns_mod.load_session_turns = lambda ids: {}


def fresh_run(task_ids):
    root = Path(tempfile.mkdtemp()) / "run"
    paths = Paths(root)
    tasks = [
        Task(t, "r/r", "u", "sha", f"s{t}", 10, 11, 12, 13, "wrong " * 10, "right " * 10,
             "a defect", "none")
        for t in task_ids
    ]
    write(tasks, paths.tasks)
    paths.calibration.write_text(
        "".join(json.dumps({"task_id": t, "sound": True}) + "\n" for t in task_ids))
    paths.controls.write_text(
        "".join(json.dumps({"task_id": t, "control": c, "ok": True}) + "\n"
                for t in task_ids for c in CONTROL_NAMES))
    return paths


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def reset():
    live.update(run=0, grade=0)
    peak.update(run=0, grade=0)
    seen.update(judge=[], trace=[], graders=set(), candidate_calls=0)
    grading_during_attempt["count"] = 0


# ------------------------------------------- 1. the old stage, from git HEAD

print("\n1. the combined stage as it was, for comparison")
# The newest revision of pipeline.py from before grading became its own stage.
# Found rather than pinned to a commit, so this keeps comparing against the
# real previous behaviour however many commits land on top of it.
revs = subprocess.run(
    ["git", "log", "--format=%H", "--", "src/errata_bench/pipeline.py"],
    capture_output=True, text=True, check=True).stdout.split()
for rev in revs:
    # `git log -- <path>` lists the commit that *deleted* the file too, and the
    # restructure did delete it, so the newest revisions have no blob to show.
    # Skipped rather than fatal: the revision being looked for is older than
    # any of them.
    shown = subprocess.run(
        ["git", "show", f"{rev}:src/errata_bench/pipeline.py"],
        capture_output=True, text=True)
    if shown.returncode != 0:
        continue
    old_src = shown.stdout
    if '"grade",' not in old_src:
        print(f"     comparing against {rev[:8]}, the last revision before the split")
        break
else:
    raise SystemExit("no pre-split revision of pipeline.py found")
# The old module was written against the flat layout: `from .attempt import`,
# `from .judge import`, `from .reader import`. Those names moved into packages,
# so they are aliased back into sys.modules for the length of this check. The
# module objects are today's -- which is the point, since this isolates
# pipeline.py and says so above -- they are merely reachable under the names a
# module from before the move asks for.
import types

import errata_bench.construct.build, errata_bench.construct.container
import errata_bench.corpus.sessions, errata_bench.corpus.turns
import errata_bench.find.answerable, errata_bench.find.leakage
import errata_bench.find.redact, errata_bench.find.scope
import errata_bench.find.signature, errata_bench.find.trajectory
import errata_bench.find.triage
import errata_bench.instrument.control
import errata_bench.llm
import errata_bench.score.attempt, errata_bench.score.judge
import errata_bench.score.structure, errata_bench.score.trace

for _old, _now in (
    ("attempt", errata_bench.score.attempt), ("judge", errata_bench.score.judge),
    ("structure", errata_bench.score.structure), ("trace", errata_bench.score.trace),
    ("control", errata_bench.instrument.control),
    ("build", errata_bench.construct.build),
    ("container", errata_bench.construct.container),
    ("corpus", errata_bench.corpus.sessions),
    ("answerable", errata_bench.find.answerable), ("leakage", errata_bench.find.leakage),
    ("scope", errata_bench.find.scope), ("redact", errata_bench.find.redact),
    ("signature", errata_bench.find.signature), ("triage", errata_bench.find.triage),
    ("trajectory", errata_bench.find.trajectory),
):
    sys.modules[f"errata_bench.{_old}"] = _now

# reader.py split three ways, so it is reassembled rather than aliased.
_reader = types.ModuleType("errata_bench.reader")
for _src in (errata_bench.llm, errata_bench.corpus.turns):
    for _k in dir(_src):
        if not _k.startswith("__"):
            setattr(_reader, _k, getattr(_src, _k))
sys.modules["errata_bench.reader"] = _reader

tmp = Path(tempfile.mkdtemp()) / "pipeline_old.py"
tmp.write_text(old_src)
spec = importlib.util.spec_from_file_location("errata_bench.pipeline_old", tmp)
old = importlib.util.module_from_spec(spec)
old.__package__ = "errata_bench"
sys.modules["errata_bench.pipeline_old"] = old
spec.loader.exec_module(old)
check("grade" not in old.STAGES, "the old module really is the pre-split one")

reset()
before = fresh_run(["task-0", "task-1", "task-2", "task-3"])
p_old = asyncio.run(old.stage_attempt(old.Paths(before.root), 10**9, concurrency=6, repeats=2))
old_rows = rows(before.attempts)
old_judge, old_trace = sorted(seen["judge"]), sorted(seen["trace"])
old_candidates = seen["candidate_calls"]
print(f"     old: {len(old_rows)} rows in attempts.jsonl, {old_candidates} candidate runs")

# ------------------------------------------------ 2. the new pair of stages

print("\n2. the split pair")
reset()
after = fresh_run(["task-0", "task-1", "task-2", "task-3"])
in_attempt_stage["now"] = True
t0 = time.monotonic()
p_att = asyncio.run(stage_attempt(after, 10**9, concurrency=6, repeats=2))
attempt_s = time.monotonic() - t0
in_attempt_stage["now"] = False
peak_run = peak["run"]
answers = rows(after.answers)

check(grading_during_attempt["count"] == 0,
      "the attempt stage made no grading call at all")
check(not seen["graders"], "no grader was contacted while candidates ran")
check(peak_run == 2, f"candidates ran up to the container bound and no further: peak {peak_run}")
check(not after.attempts.exists() or not rows(after.attempts),
      "the attempt stage wrote nothing to attempts.jsonl")
check(len(answers) == 8, f"one answer row per (task, run): {len(answers)}")
check(sum(1 for a in answers if a.get("error")) == 2,
      "the two runs of the task whose container died are recorded as errors")
check(all("structure" in a for a in answers if not a.get("error")),
      "every stored answer carries the reading taken while the tree existed")

t0 = time.monotonic()
p_grade = asyncio.run(stage_grade(after, 10**9, concurrency=6))
grade_s = time.monotonic() - t0
new_rows = rows(after.attempts)
peak_grade = peak["grade"]
check(peak_grade >= 4, f"grading ran wider than the container bound: peak {peak_grade}")
check(seen["candidate_calls"] == 8,
      f"the grade stage ran no candidate: {seen['candidate_calls']} runs, all from the attempt stage")
print(f"     attempt {attempt_s:.1f}s, grade {grade_s:.1f}s, combined would be ~{8*0.6:.1f}s serial")

# -------------------------------------------- 3. row-for-row equivalence

print("\n3. the same rows, field for field")
# `seconds` means the candidate's own time now, not candidate plus grading, and
# scored rows carry five fields they did not before. Everything else must
# match -- including `out_of_time`, which the old no-answer row already wrote
# and which was wrongly listed as new, hiding any regression in that path.
# Derived, not asserted by hand: any field the new row carries that the old one
# did not must be one of these, so a seventh cannot join the exception list
# silently. `out_of_time` is in it only because the old code wrote it on the
# no-answer rows alone -- and on those rows it is compared, below, because the
# comparison is over the keys the OLD row actually had.
EXPECTED_NEW = {"graded_seconds", "pass", "structure", "had_conversation", "task_fingerprint", "out_of_time"}  # "pass": D-30, one row per reading
CHANGED = {"seconds"}


def comparable(old_row, new_row):
    """The old row, and the same keys from the new one."""
    keys = set(old_row) - CHANGED
    return ({k: old_row.get(k) for k in sorted(keys)},
            {k: new_row.get(k) for k in sorted(keys)})


# A candidate that never produced an answer is now recorded where the answers
# are, not among the scores. Nothing read those rows for anything but dropping
# them -- the report, the regrade tool and the comparison table all filter on
# `error` -- so this moves the record without losing it.
old_errors = {(r["task_id"], r["run"]): r["error"] for r in old_rows if r.get("error")}
new_errors = {(r["task_id"], r["run"]): r["error"] for r in answers if r.get("error")}
check(old_errors == new_errors and len(old_errors) == 2,
      f"a failed candidate is recorded once, with the same message: {len(new_errors)} of them")
check(not [r for r in new_rows if r.get("error")],
      "and attempts.jsonl now holds scores only")

old_by_key = {(r["task_id"], r["run"]): r for r in old_rows if not r.get("error")}
new_by_key = {(r["task_id"], r["run"]): r for r in new_rows}
# The count as well as the identity: with no rows on either side every
# assertion in this section passes and proves nothing.
check(len(old_by_key) == 6 and set(old_by_key) == set(new_by_key),
      f"the same six attempts were scored: {len(old_by_key)} then, {len(new_by_key)} now")
appeared = set().union(*(set(new_by_key[k]) - set(old_by_key[k]) for k in old_by_key)) \
    if old_by_key else set()
check(appeared <= EXPECTED_NEW,
      f"the only new fields on a scored row are the expected ones: {sorted(appeared)}")
differing = [k for k in old_by_key
             if comparable(old_by_key[k], new_by_key.get(k, {}))[0]
             != comparable(old_by_key[k], new_by_key.get(k, {}))[1]]
if differing:
    k = differing[0]
    a, b = comparable(old_by_key[k], new_by_key[k])
    print("     first difference:", k)
    for f in sorted(set(a) | set(b)):
        if a.get(f) != b.get(f):
            print(f"       {f}: old={a.get(f)!r}  new={b.get(f)!r}")
check(not differing, f"every scored row is identical: {len(differing)} differ")
check(sorted(seen["judge"]) == old_judge,
      "the judge was asked exactly the same questions, about the same traces")
check(sorted(seen["trace"]) == old_trace,
      "the trace check was given the same answer, trace, conversation and rules")
check(old_candidates == seen["candidate_calls"],
      f"the same number of candidate runs: {old_candidates} then, {seen['candidate_calls']} now")

no_answer = [r for r in new_rows if r["task_id"] == "task-2"]
check(len(no_answer) == 2
      and all(r["outcome"] == "no_answer" and r["scoreable"] and not r["passed"] for r in no_answer),
      f"both answers of nothing are still scored, not errored ({len(no_answer)})")
check(all(r["out_of_time"] for r in no_answer),
      "and it records that the candidate used every turn")
check(all(r.get("told_the_truth_about_edits") is None for r in new_rows),
      "nothing claims the candidate misreported edits it was never asked to declare")

# ------------------------------------------------------- 4. resume, twice

print("\n4. resume does no work twice")
# In its own directory, with no grading at all, so the skip has to come from
# the stored answers. Resumed only after grading, the graded rows supplied it
# and the answer-side resume could be deleted with this section still green.
reset()
alone = fresh_run(["task-0", "task-1"])
asyncio.run(stage_attempt(alone, 10**9, concurrency=6, repeats=2))
first_pass = seen["candidate_calls"]
reset()
p_answers_only = asyncio.run(stage_attempt(alone, 10**9, concurrency=6, repeats=2))
check(first_pass == 4 and seen["candidate_calls"] == 0,
      f"a stored answer alone stops a candidate being re-run: {first_pass} then {seen['candidate_calls']}")
check(p_answers_only.skipped == 4, f"all four stored answers skipped: {p_answers_only.skipped}")
reset()
p2 = asyncio.run(stage_attempt(after, 10**9, concurrency=6, repeats=2))
check(seen["candidate_calls"] == 2,
      f"only the failed container is retried on a second attempt pass: {seen['candidate_calls']} runs")
check(p2.skipped == 6, f"the six stored answers were skipped: {p2.skipped}")
reset()
p3 = asyncio.run(stage_grade(after, 10**9, concurrency=6))
check(not seen["judge"], f"nothing was graded twice: {len(seen['judge'])} judge calls")
# The retry failed again, so it produced no answer to grade. An errored row is
# dropped and comes back as work next time, which is the point -- it must not
# arrive in the scores as a failure the candidate did not have.
check(len(rows(after.attempts)) == len(new_rows),
      "a candidate that failed again added nothing to the scores")
# The grade stage does not tidy answers.jsonl: it does not own that file, and
# rewriting it from a stale snapshot would drop whatever the attempt stage
# appended in between. The attempt stage clears its own errors on its next pass.
check(len([r for r in rows(after.answers) if r.get("error")]) == 2,
      "grading left the errored answers alone rather than rewriting a file it does not own")
reset()
asyncio.run(stage_attempt(after, 10**9, concurrency=6, repeats=2))
check(seen["candidate_calls"] == 2,
      "and the next attempt pass dropped them and retried exactly those two")

# ------------------------------------- 5. a run made before the split

print("\n5. a run directory from before the split")
reset()
legacy = fresh_run(["task-0", "task-1"])
for r in old_rows:
    if r["task_id"] in ("task-0", "task-1"):
        with legacy.attempts.open("a") as fh:
            fh.write(json.dumps(r) + "\n")
check(not legacy.answers.exists(), "it holds graded attempts and no answers file")
p_legacy = asyncio.run(stage_attempt(legacy, 10**9, concurrency=6, repeats=2))
check(seen["candidate_calls"] == 0,
      f"no candidate was re-run for an answer already graded: {seen['candidate_calls']} runs")
check(p_legacy.skipped == 4, f"all four were counted as done: {p_legacy.skipped}")
reset()
asyncio.run(stage_grade(legacy, 10**9, concurrency=6))
check(not seen["judge"], "and nothing was re-graded either")

# --------------------------------------- 6. a grading failure is one row

print("\n6. one answer that cannot be graded is one row, not a dead stage")
reset()
broken = fresh_run(["task-0", "task-4"])
asyncio.run(stage_attempt(broken, 10**9, concurrency=6, repeats=1))
p_broken = asyncio.run(stage_grade(broken, 10**9, concurrency=6))
graded = rows(broken.attempts)
check(len(graded) == 2, f"both answers produced a row: {len(graded)}")
check(sum(1 for r in graded if r.get("error")) == 1, "the failed grading is an error row")
check(any(r["task_id"] == "task-0" and r.get("passed") is not None for r in graded),
      "the other answer was still graded")
reset()
asyncio.run(stage_grade(broken, 10**9, concurrency=6))
check(len(seen["judge"]) == 1, f"the errored grade is retried, the good one is not: {len(seen['judge'])}")

# ------------------------------- 7. rebuilding tasks does not orphan answers

print("\n7. a rebuild prunes answers with their tasks")
from errata_bench.spec import read as read_tasks
import errata_bench.build as build_mod

surviving = [t for t in read_tasks(after.tasks) if t.task_id != "task-0"]


class FakeBuild:
    tasks = surviving
    rejected = [type("R", (), {"repo_id": "r/r", "complaint_turn": 1, "reason": "nothing to answer"})()]


build_mod.build = lambda rows_: FakeBuild()
# A real rebuild has screened rows to build from; without them the stage now
# refuses, which is checked next.
after.screened.write_text(json.dumps({"session_id": "s", "turn_number": 1}) + "\n")
p_build = stage_build(after, 10**9)
left = {r["task_id"] for r in rows(after.answers)}
check("task-0" not in left, "the dropped task's answers went with it")
check("task-1" in left, "the surviving tasks' answers are still there")
check(any("dropped" in n for n in p_build.notes) and any("nothing to answer" in n for n in p_build.notes),
      f"and the deletion note survives beside the rejection list: {p_build.notes}")

print("\n7b. a rebuild refuses to empty a finished run directory")
# The three candidate directories hold tasks, calibration, controls and results
# but not the screened rows those came from -- they were copied in. Running
# every stage against one of them would build zero tasks and prune everything
# downstream to match.
finished_run = fresh_run(["task-0", "task-1"])
for r in new_rows:
    with finished_run.attempts.open("a") as fh:
        fh.write(json.dumps(r) + "\n")
with finished_run.answers.open("a") as fh:
    for a in rows(after.answers):
        fh.write(json.dumps(a) + "\n")
before_counts = (len(rows(finished_run.tasks)), len(rows(finished_run.attempts)),
                 len(rows(finished_run.answers)), len(rows(finished_run.calibration)))
build_mod.build = lambda rows_: (_ for _ in ()).throw(AssertionError("build must not run"))
p_refuse = stage_build(finished_run, 10**9)
after_counts = (len(rows(finished_run.tasks)), len(rows(finished_run.attempts)),
                len(rows(finished_run.answers)), len(rows(finished_run.calibration)))
check(before_counts == after_counts and before_counts[1] > 0,
      f"nothing was deleted: {before_counts} before, {after_counts} after")
check(any("refused" in n for n in p_refuse.notes), f"and it said so: {p_refuse.notes}")

# --------------------------------------------- 8. the structure round trip

print("\n8. the reading survives the file")
src = Structure("t", investigated=True, executed=False, wrote=True, tool_calls=7,
                files_changed={"a.py": "modified"}, token_removed=False,
                touched_defect_file=True, declaration_matches=None)
check(Structure.from_json(json.loads(json.dumps(src.to_json()))) == src,
      "every field written is read back unchanged")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
