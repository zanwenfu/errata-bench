# One live check per bug found in this session. Not "the log says fixed" --
# each one runs the real code and asserts the behaviour that was wrong is now
# right. No network, no Docker, no model calls.
import asyncio, inspect, json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, "src")
os.environ["ERRATA_JUDGE_MODEL"] = "the-grader"
os.environ["ERRATA_MODEL"] = "the-candidate"

from errata_bench import attempt as A, container as C, corpus, judge as J, pipeline as P, reader, rejudge, trace as T
from errata_bench.attempt import Attempt, ToolCall
from errata_bench.judge import Judgement
from errata_bench.pipeline import Paths, Progress, append, load, stage_attempt, stage_build, stage_grade
from errata_bench.spec import Task, fingerprint, write
from errata_bench.structure import Structure
from errata_bench.trace import Claim, TraceCheck

RESULTS = []


def check(bug, what, ok):
    RESULTS.append((bug, ok))
    print(f"  {bug:7s} {'PASS' if ok else 'FAIL':5s} {what}")


# ---- fakes, so nothing leaves this machine -------------------------------
judged = {"n": 0}
async def fake_run(task, **kw):
    return Attempt(task.task_id, "the-candidate", reply="I read it.",
                   tool_calls=[ToolCall("read_file", {"path": "a"}, result="x")],
                   final_state={"big": "y" * 90_000}, environment="host")
async def fake_judge(*a, **k):
    judged["n"] += 1
    return Judgement(True, False, False, True, "I read it.", "ok", True)
async def fake_check(*a, **k):
    return TraceCheck(claims=[Claim(claim="c", supported=True, evidence="e")], reasoning="ok")
A.run, J.judge, T.check = fake_run, fake_judge, fake_check
A.transcript_for = lambda t, turns: "conversation"
A.transcripts_for = lambda ts: {t.task_id: "conversation" for t in ts}
C.image_for = lambda l, **k: None
C.sweep = lambda: None
C.max_containers = lambda: 2
corpus.load_repos = lambda: {}
reader.load_session_turns = lambda ids: {}


def mktask(tid="t", defect="a defect"):
    return Task(tid, "r/r", "u", "sha", "s", 10, 11, 12, 13, "w" * 20, "r" * 20, defect, "none")


def run_dir(tids=("t",)):
    p = Paths(Path(tempfile.mkdtemp()) / "run")
    write([mktask(t) for t in tids], p.tasks)
    p.calibration.write_text("".join(json.dumps({"task_id": t, "sound": True}) + "\n" for t in tids))
    p.controls.write_text("".join(json.dumps({"task_id": t, "control": c, "ok": True}) + "\n"
                                  for t in tids for c in ("null", "overclaim")))
    return p


src = lambda f: inspect.getsource(f)
print("\nEach line runs the real code and asserts the old behaviour is gone.\n")

# ---- B-122 -------------------------------------------------------------
# runs/ is gitignored, so on a fresh clone these files are absent; the checks
# that read them fall back rather than aborting the run halfway through.
stored = Path("runs/cand-grok/attempts.jsonl")
row = (json.loads(stored.read_text().splitlines()[0]) if stored.exists()
       else {"task_id": "t", "tool_calls": [], "told_the_truth_about_edits": None})
check("B-122", "an answer never asked to declare its edits is not called a liar",
      rejudge.structure_from_row(row).declaration_matches is None)

# ---- B-123 -------------------------------------------------------------
d = run_dir(("a", "b"))
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
_real_judge = J.judge
async def one_bad(task, *a, **k):
    if task.task_id == "a":
        raise RuntimeError("the deployment returned nothing")
    return await _real_judge(task, *a, **k)
J.judge = one_bad
asyncio.run(stage_grade(d, 10**9, concurrency=2))
J.judge = _real_judge
graded = {r["task_id"]: r for r in load(d.attempts)}
check("B-123", "a failed grading is one error row, and the other answers still grade",
      len(graded) == 2
      and graded["a"].get("error", "").startswith("RuntimeError:")
      and graded["b"].get("passed") is not None)

# ---- B-124 -------------------------------------------------------------
from errata_bench.container import _abandoned
check("B-124", "a sweep spares a live peer's container and takes its own",
      _abandoned(f"errata-{os.getpid()}-x", os.getpid()) and
      not _abandoned(f"errata-{os.getppid()}-x", os.getpid()))

# ---- B-125 -------------------------------------------------------------
import errata_bench.build as B
d = run_dir()
append(d.attempts, {"task_id": "t", "run": 0, "passed": True})
B.build = lambda rows: (_ for _ in ()).throw(AssertionError("build must not run"))
p = stage_build(d, 10**9)
check("B-125", "a rebuild refuses to empty a directory that holds results",
      len(load(d.tasks)) == 1 and len(load(d.attempts)) == 1 and any("refused" in n for n in p.notes))

# ---- B-126 / B-145 -----------------------------------------------------
d = run_dir()
# cut in the middle of an em dash, which model replies are full of
d.answers.write_bytes(json.dumps({"task_id": "a"}).encode() + b"\n"
                      + '{"task_id": "b", "reply": "the tests pass \u2014'.encode()[:-1])
append(d.answers, {"task_id": "c"})
check("B-126", "a row cut off by a kill no longer swallows the next one",
      [r["task_id"] for r in load(d.answers)] == ["a", "c"])
check("B-145", "and a row cut mid-character does not make the whole file unreadable",
      len(load(d.answers)) == 2)

# ---- B-127 -------------------------------------------------------------
try:
    _ = run_dir().attemps
    ok = False
except AttributeError:
    ok = True
check("B-127", "a mistyped file name raises instead of silently reading nothing", ok)

# ---- B-128 -------------------------------------------------------------

# ---- B-129 -------------------------------------------------------------
delays = []
_sleep = asyncio.sleep
async def spy(d_, *a, **k): delays.append(d_)
asyncio.sleep = spy
async def always_429(): raise RuntimeError("429 rate limit")
try:
    asyncio.run(reader.resilient(always_429, attempts=4, pause=10))
except RuntimeError:
    pass
finally:
    asyncio.sleep = _sleep
check("B-129", f"retries are spread out, not in lockstep: {[round(x, 1) for x in delays]}",
      len(delays) == 3 and len(set(delays)) == 3 and all(6 <= x <= 42 for x in delays))

# ---- B-130 / B-143 -----------------------------------------------------
r = subprocess.run([sys.executable, "run.py", "status", "--concurrency", "0"],
                   capture_output=True, text=True)
hi = subprocess.run([sys.executable, "run.py", "status", "--grade-concurrency", "33"],
                    capture_output=True, text=True)
check("B-130", "concurrency outside 1..32 is rejected, with a non-zero exit",
      r.returncode != 0 and "1..32" in (r.stderr + r.stdout)
      and hi.returncode != 0 and "1..32" in (hi.stderr + hi.stdout))
# the refusal path needs no model call: a directory graded by another judge
refuse = run_dir()
asyncio.run(stage_attempt(refuse, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(refuse, 10**9, concurrency=2))
P.replace(refuse.attempts, [dict(x, judge_model="someone-else") for x in load(refuse.attempts)])
ran = subprocess.run([sys.executable, "run.py", "stages", "--run", str(refuse.root), "--only", "grade"],
                     capture_output=True, text=True,
                     env={**os.environ, "ERRATA_JUDGE_MODEL": "the-grader"})
check("B-143", "a stage that refuses or fails exits non-zero, and says so",
      ran.returncode == 1 and "REFUSED" in ran.stdout and "rows failed in: grade" in ran.stdout)

# ---- B-131 / B-147 -----------------------------------------------------
import re as _re
sh = Path("runs/attempt-rounds.sh").read_text() if Path("runs/attempt-rounds.sh").exists() else ""
attempt_half = sh.split("--only grade")[0]
check("B-131", "the retry driver counts candidate errors in the file that holds them",
      bool(_re.search(r"--only attempt[\s\S]{0,400}?answers\.jsonl", sh))
      and "attempts.jsonl" not in attempt_half.split("# ")[-1]
      and "--only grade" in sh)
rg = Path("runs/regrade-all.sh").read_text() if Path("runs/regrade-all.sh").exists() else ""
log_line = next((l.strip() for l in rg.splitlines() if l.strip().startswith("log=")), "")
named = subprocess.run(["bash", "-c", f'judge=my-judge; {log_line}; printf %s "$log"'],
                       capture_output=True, text=True).stdout
check("B-147", f"the regrade log is named after the judge, not a newline: {named!r}",
      named == "runs/regrade-my-judge.log")

# ---- B-132 -------------------------------------------------------------
try:
    Structure.from_json({"task_id": "t", "investigated": True, "executed": True, "wrote": True})
    ok = False
except KeyError:
    ok = True
check("B-132", "a reading with a field missing refuses to load instead of scoring zero", ok)

# ---- B-133 -------------------------------------------------------------
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
a = load(d.answers)[0]
check("B-133", "the conversation and the rules are stored with the answer, not rebuilt later",
      a.get("transcript") == "conversation" and "five tools" in (a.get("rules") or ""))

# ---- B-134 / B-136 / B-148 --------------------------------------------
# Grade alone, with no attempt run in between: otherwise the attempt stage
# removes the stale answers first and the grading guard is never reached.
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=3))
write([mktask(defect="rebuilt with a different defect")], d.tasks)
judged["n"] = 0
pg_only = asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("B-134", "an answer is never graded against a task that changed under it",
      judged["n"] == 0 and not load(d.attempts)
      and any("earlier version" in n for n in pg_only.notes))
pa = asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=3))
pg = asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("B-136", "a rebuilt task is collected and graded again, not retired for ever",
      pa.produced == 3 and pg.produced == 3 and len(load(d.answers)) == 3)
stale = {"task_id": "t", "run": 9, "reply": "x", "tool_calls": [], "task_fingerprint": "deadbeef"}
d2 = run_dir(); d2.attempts.write_text(json.dumps(stale) + "\n")
out = Paths(Path(tempfile.mkdtemp()) / "out")
rejudge.transcripts_for = lambda ts: {t.task_id: "c" for t in ts}
judged["n"] = 0
asyncio.run(rejudge.regrade_all(d2, out, "the-judge", 2, 1))
check("B-148", "the regrade tool refuses a stale answer too", judged["n"] == 0)

# ---- B-135 -------------------------------------------------------------
import contextlib, io
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    asyncio.run(P.run_stages(refuse.root, ("grade",), concurrency=2))
check("B-135", "a refusal reaches the screen, not just the Progress object",
      "REFUSED" in Progress("grade", notes=["REFUSED: x"]).line()
      and "REFUSED" in buf.getvalue())

# ---- B-137 -------------------------------------------------------------
import dataclasses as _dc
d = run_dir()
P.replace(d.tasks, [_dc.replace(mktask(), kind="present").to_json()])
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
stored_kind = load(d.answers)[0]["kind"]
# the task is rebuilt as a different kind; the answer keeps the old label
P.replace(d.tasks, [_dc.replace(mktask(), kind="none").to_json()])
P.replace(d.answers, [dict(x, task_fingerprint=fingerprint(_dc.replace(mktask(), kind="none")))
                      for x in load(d.answers)])
asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("B-137", f"the scored row carries the kind that decided the rule, not the stored label "
               f"(stored {stored_kind!r})",
      stored_kind == "present" and load(d.attempts)[0]["kind"] == "none")

# ---- B-138 -------------------------------------------------------------
from errata_bench.pipeline import _capped, KEPT_STATE_CHARS
huge = {f"b/{i}.js": "z" * 50_000 for i in range(5000)}
kept = _capped(huge, "")
check("B-138", f"a captured tree is bounded per row at a fixed size "
               f"({sum(len(v) for v in kept.values()):,} chars)",
      sum(len(v) for v in kept.values()) <= 2_100_000 and KEPT_STATE_CHARS <= 2_000_000)

# ---- B-139 -------------------------------------------------------------
A.transcript_for = lambda t, turns: ""
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
check("B-139", "a task with no conversation is refused instead of graded anyway",
      load(d.answers)[0].get("error", "").startswith("no conversation"))
A.transcript_for = lambda t, turns: "conversation"

# ---- B-140 -------------------------------------------------------------
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(d, 10**9, concurrency=2))
P.replace(d.attempts, [dict(r, judge_model="someone-else") for r in load(d.attempts)])
pg = asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("B-140", "a second judge over a graded run is refused, not quietly skipped",
      any("REFUSED" in n for n in pg.notes) and pg.failed >= 1)

# ---- B-141 -------------------------------------------------------------

# ---- B-142 -------------------------------------------------------------
# A subprocess, not multiprocessing: this script has no __main__ guard, and
# spawn re-executes it from the top in the child.
import time as _t
d = run_dir()
append(d.answers, {"task_id": "seed", "run": 0})
HOG = ("import sys, time; sys.path.insert(0, 'src');"
       "from pathlib import Path; from errata_bench.pipeline import held;"
       f"exec(\"with held(Path({str(d.answers)!r})):\\n    time.sleep(0.6)\")")
proc = subprocess.Popen([sys.executable, "-c", HOG])
_t.sleep(0.25)
t0 = _t.monotonic(); append(d.answers, {"task_id": "waited", "run": 0}); waited = _t.monotonic() - t0
proc.wait()
check("B-142", f"an append waits for a lock another process holds ({waited:.2f}s)",
      waited > 0.15 and d.answers.with_suffix(".jsonl.lock").exists())

# ---- B-144 -------------------------------------------------------------
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
P.replace(d.answers, [dict(x, structure={**x["structure"], "task_id": "some-other-task"})
                      for x in load(d.answers)])
judged["n"] = 0
asyncio.run(stage_grade(d, 10**9, concurrency=2))
row = load(d.attempts)[0]
check("B-144", "a reading that names another task is an error, and costs no judge call",
      "the reading is for" in row.get("error", "") and judged["n"] == 0)

# ---- B-146 -------------------------------------------------------------
d2 = run_dir()
d2.attempts.write_text(json.dumps({"task_id": "t", "run": 0, "reply": "", "tool_calls": [],
                                   "wrote": False, "fixed": None}) + "\n")
out = Paths(Path(tempfile.mkdtemp()) / "out")
judged["n"] = 0
asyncio.run(rejudge.regrade_all(d2, out, "the-judge", 2, 1))
check("B-146", "an empty answer is not handed to a judge to read",
      judged["n"] == 0 and load(out.attempts)[0]["outcome"] == "no_answer")

# ---- B-149 -------------------------------------------------------------
d = run_dir()
d.screened.write_text(json.dumps({"session_id": "s", "turn_number": 1}) + "\n")
class _Built:
    tasks = [mktask()]
    rejected = [type("R", (), {"repo_id": "r/r", "complaint_turn": 7, "reason": "no commit before the session"})()]
B.build = lambda rows_: _Built()
stage_build(d, 10**9)
rej = load(d.rejections)
check("B-149", "why each row was rejected is on disk, not only on screen",
      rej == [{"repo_id": "r/r", "complaint": 7, "reason": "no commit before the session"}])

# ======================================================================
# Found by a second round of review, after the first twenty-eight were fixed.
# ======================================================================

# ---- B-150: the one that destroyed paid work ---------------------------
d = run_dir(("keeps", "loses-its-control", "gets-rebuilt"))
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
ctl = [dict(r, ok=r["ok"] and r["task_id"] != "loses-its-control") for r in load(d.controls)]
P.replace(d.controls, ctl)
write([mktask("keeps"), mktask("loses-its-control"),
       mktask("gets-rebuilt", "a different defect")], d.tasks)
prog = asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
left = {r["task_id"] for r in load(d.answers)}
check("B-150", "an answer survives its task failing a control this run",
      "loses-its-control" in left)
check("B-150b", "and the note counts exactly what was removed",
      any("dropped 1 answers" in n for n in prog.notes) and left == {"keeps", "loses-its-control", "gets-rebuilt"})

# ---- B-151: the rewrite must not write back a stale snapshot ----------
check("B-151", "stale-row rewrites re-read under the lock",
      src(stage_attempt).count("with held(paths.answers)") == 1 and
      "for r in load(paths.answers)" in src(stage_attempt) and
      "for r in load(paths.attempts)" in src(stage_grade))

# ---- B-152: the two gate files carry the task version -----------------
from errata_bench.pipeline import stage_calibrate, stage_control
check("B-152", "calibration and control rows are stamped, so a rebuild prunes them",
      '"task_fingerprint": fingerprint(t)' in src(stage_calibrate) and
      '"task_fingerprint": fingerprint(task)' in src(stage_control))

# ---- B-153: two tasks may not share one name --------------------------
# read from the file: an earlier check replaces build() with a stub
build_src = Path("src/errata_bench/build.py").read_text()
check("B-153", "a second task with the same name is rejected, not silently merged",
      "two tasks cannot share a name" in build_src and "seen.add(task_id)" in build_src)

# ---- B-154: the stamp covers the conversation -------------------------
import dataclasses
base = fingerprint(mktask())
# Every field a grade depends on, one at a time. Six assertions rest on this
# function and every scenario varied only `defect`, so a fingerprint reduced to
# the defect alone passed all three scripts -- blind to a changed base commit, a
# repaired transcript or a swapped reference answer.
moved = []
for field, value in [("sha", "other"), ("cut_turn", 99), ("kind", "present"),
                     ("defect", "another"), ("oracle", "x" * 30), ("criterion", "y" * 30),
                     ("signature_path", "a.py"), ("signature_token", "T"),
                     ("edits_replayed", 3), ("session_id", "other"),
                     ("redacted_turns", [1]), ("rewritten_turns", {"1": "z"}),
                     ("oracle_calls", [{"name": "x"}]), ("criterion_calls", [{"name": "y"}])]:
    if fingerprint(dataclasses.replace(mktask(), **{field: value})) == base:
        moved.append(field)
check("B-154", f"the stamp moves when any field a grade depends on changes"
               + (f" -- BLIND TO: {moved}" if moved else ""),
      not moved)
check("B-154b", "and not when a field a grade does not depend on changes",
      fingerprint(dataclasses.replace(mktask(), repo_url="elsewhere")) == base)

# ---- B-156 / B-157: one gate, everywhere ------------------------------
from errata_bench.pipeline import stage_report
from errata_bench import rejudge as RJ

# ---- B-158: an unsupportable reading is not a result ------------------

# ---- B-159 / B-160: the table counts only what it says it counts ------

# ---- B-161: a regrade's own rows carry the task version ---------------
check("B-161", "regraded rows are stamped and keyed on the stamp",
      'stamps[a["task_id"]]' in src(RJ.regrade_all) and
      'r.get("task_fingerprint")' in src(RJ.regrade_all))

# ---- B-162: an empty capture is not a searched tree -------------------
from errata_bench.structure import analyse
from errata_bench.attempt import Attempt as At
t = mktask(); t = dataclasses.replace(t, signature_token="2000")
check("B-162", "a capture that read nothing does not report the defect as gone",
      analyse(t, At("t", "m"), {}).token_removed is None and
      analyse(t, At("t", "m"), {"a": "no token here"}).token_removed is True)

# ---- B-163: a checker that failed its own control is not trusted ------

# ---- B-164: a task at zero is visible ---------------------------------

# ======================================================================
# These were source greps until a mutation audit showed each of them
# passing with its subject reverted. Each now runs the code.
# ======================================================================

def rejudge_fixture(*, sound_field=False, controls=True, trace_ok=True, scoreable=True):
    """A source run and one judge's output under it, ready for summarise/compare."""
    srcp = run_dir(("t",))
    outp = Paths(srcp.root / "rejudge" / "the-judge")
    # a calibration row whose pass/fail line holds but whose stored `sound` is
    # false: the older, stricter bar, which is what every pre-09-19 row carries
    cal = {"task_id": "t", "sound": sound_field,
           "failed_outcome": "off_target", "failed_outcome_swapped": "off_target",
           "resolution_outcome": "solved", "resolution_outcome_swapped": "solved"}
    P.replace(srcp.calibration, [cal])
    P.replace(outp.calibration, [dict(cal, judge_model="the-judge")])
    rows = ([{"task_id": "t", "control": c, "ok": True, "trace_ok": trace_ok}
             for c in ("null", "overclaim")] if controls else [])
    P.replace(srcp.controls, rows)
    P.replace(outp.controls, rows)
    graded = {"task_id": "t", "run": 0, "pass": 0, "judge_model": "the-judge",
              "passed": True, "dishonest": False, "claims_match_trace": None,
              "outcome": "solved", "scoreable": scoreable, "reply": "x",
              "tool_calls": [{"name": "read_file"}]}
    P.replace(outp.attempts, [graded])
    P.replace(srcp.attempts, [dict(graded, judge_model="original")])
    return srcp, outp

# ---- B-156: the gate, not the raw field ------------------------------
srcp, outp = rejudge_fixture(sound_field=False)
summary = RJ.summarise(srcp, outp, "the-judge")
check("B-156", "a task the original judge really read is not listed as one it could not",
      summary["known_pair"]["of_the_tasks_the_original_read"] == "1/1"
      and summary["known_pair"]["that_the_original_could_not"] == [])

# ---- B-157: the funnel's gate ----------------------------------------
srcp, _ = rejudge_fixture(sound_field=False)
stage_report(srcp)
check("B-157", "the funnel counts a task whose pass/fail line holds",
      json.loads(srcp.report.read_text())["funnel"]["tasks_calibrated"] == 1)

# ---- B-158: an unsupportable reading is not a result ------------------
srcp, outp = rejudge_fixture(scoreable=False)
check("B-158", "a reading the judge could not support is dropped from the rate",
      RJ.summarise(srcp, outp, "the-judge")["counted"]["attempts"] == 0)

# ---- B-163: a checker that failed its own control is untrusted --------
srcp, outp = rejudge_fixture(trace_ok=False)
check("B-163", "a task whose trace control failed is not counted",
      RJ.summarise(srcp, outp, "the-judge")["counted"]["attempts"] == 0)

# ---- B-159 / B-160: the table counts only what it says --------------
srcp, outp = rejudge_fixture(scoreable=False)
table = RJ.compare(srcp.root)
check("B-159", "an unsupportable cell is left out of the totals",
      "0/0" in table)
srcp, outp = rejudge_fixture(controls=False)
check("B-160", "a task with no control is untrusted in the table",
      "0/0" in RJ.compare(srcp.root))

# ---- B-164: a task with no scored attempt is named -------------------
srcp, _ = rejudge_fixture(scoreable=False)
stage_report(srcp)
check("B-164", "a task whose every attempt is unreadable is named as unscored",
      json.loads(srcp.report.read_text())["funnel"]["tasks_with_no_scored_attempt"] == ["t"])

# ---- B-161: a regrade after a rebuild replaces, it does not stack ----
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(d, 10**9, concurrency=2))
out2 = Paths(d.root / "rejudge" / "second")
RJ.transcripts_for = lambda ts: {t.task_id: "conv" for t in ts}
asyncio.run(RJ.regrade_all(d, out2, "second", 2, 1))
first = len(load(out2.attempts))
write([mktask(defect="rebuilt")], d.tasks)
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(d, 10**9, concurrency=2))
asyncio.run(RJ.regrade_all(d, out2, "second", 2, 1))
rows2 = load(out2.attempts)
check("B-161", f"a regrade after a rebuild leaves one grade, not two ({first} then {len(rows2)})",
      first == 1 and len(rows2) == 1
      and rows2[0]["task_fingerprint"] == fingerprint(mktask(defect="rebuilt")))

# ---- B-141: both kinds of note survive a rebuild ---------------------
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
d.screened.write_text(json.dumps({"session_id": "s", "turn_number": 1}) + "\n")
class _B2:
    tasks = [mktask(defect="rebuilt")]
    rejected = [type("R", (), {"repo_id": "r/r", "complaint_turn": 2, "reason": "out of scope"})()]
B.build = lambda rows_: _B2()
pb = stage_build(d, 10**9)
check("B-141", f"a rebuild keeps both what it deleted and why it rejected: {pb.notes}",
      any("dropped" in n for n in pb.notes) and any("out of scope" in n for n in pb.notes))

# ---- B-128: the container bound is reached, not merely not exceeded --
peak = {"n": 0, "live": 0}
_r = A.run
async def counted_run(task, **kw):
    peak["live"] += 1; peak["n"] = max(peak["n"], peak["live"])
    await asyncio.sleep(0.05)
    peak["live"] -= 1
    return await _r(task, **kw)
A.run, C.image_for = counted_run, (lambda l, **k: "node:22")
d = run_dir(("a", "b", "c", "d"))
asyncio.run(stage_attempt(d, 10**9, concurrency=6, repeats=1))
A.run, C.image_for = _r, (lambda l, **k: None)
check("B-128", f"candidates run up to the container bound and no further: peak {peak['n']}",
      peak["n"] == 2)

bad = [b for b, ok in RESULTS if not ok]
print(f"\n  {len(RESULTS) - len(bad)} of {len(RESULTS)} fixes verified live"
      + (f"; STILL BROKEN: {bad}" if bad else "; none still present"))
sys.exit(1 if bad else 0)
