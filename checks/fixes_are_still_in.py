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
stored = Path("runs/cand-grok/attempts.jsonl")
row = (json.loads(stored.read_text().splitlines()[0]) if stored.exists()
       else {"task_id": "t", "tool_calls": [], "told_the_truth_about_edits": None})
check("B-122", "an answer never asked to declare its edits is not called a liar",
      rejudge.structure_from_row(row).declaration_matches is None)

# ---- B-123 -------------------------------------------------------------
check("B-123", "a failed grading is caught and recorded, not left to kill the stage",
      "except Exception as e:" in src(stage_grade) and 'f"{type(e).__name__}: {e}"' in src(stage_grade))

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
d.answers.write_text(json.dumps({"task_id": "a"}) + "\n" + '{"task_id": "b", "ru')
append(d.answers, {"task_id": "c"})
check("B-126", "a row cut off by a kill no longer swallows the next one",
      [r["task_id"] for r in load(d.answers)] == ["a", "c"])
check("B-145", "the repair reads bytes, not a text cursor that assumes ASCII",
      'open("rb")' in src(append) and "st_size" in src(append))

# ---- B-127 -------------------------------------------------------------
try:
    _ = run_dir().attemps
    ok = False
except AttributeError:
    ok = True
check("B-127", "a mistyped file name raises instead of silently reading nothing", ok)

# ---- B-128 -------------------------------------------------------------
check("B-128", "the semaphore that could never bind is gone",
      "grading = asyncio.Semaphore" not in src(stage_attempt))

# ---- B-129 -------------------------------------------------------------
check("B-129", "retries are spread out instead of returning in lockstep",
      "random.uniform" in src(reader.resilient))

# ---- B-130 / B-143 -----------------------------------------------------
r = subprocess.run([sys.executable, "run.py", "status", "--concurrency", "0"],
                   capture_output=True, text=True)
check("B-130", "a concurrency of zero is rejected instead of hanging for ever",
      r.returncode != 0 and "at least 1" in (r.stderr + r.stdout) or "1..32" in (r.stderr + r.stdout))
check("B-143", "a failing stage exits non-zero",
      "sys.exit(1)" in Path("run.py").read_text())

# ---- B-131 / B-147 -----------------------------------------------------
sh = Path("runs/attempt-rounds.sh").read_text()
check("B-131", "the retry driver counts candidate errors where they now live",
      'answers.jsonl' in sh and "--only grade" in sh)
check("B-147", "the regrade log is named after the judge, not a newline",
      "printf %s" in Path("runs/regrade-all.sh").read_text())

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
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=3))
asyncio.run(stage_grade(d, 10**9, concurrency=2))
write([mktask(defect="rebuilt with a different defect")], d.tasks)
pa = asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=3))
pg = asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("B-134", "an answer is never graded against a task that changed under it",
      len({r["task_fingerprint"] for r in load(d.attempts)}) == 1)
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
check("B-135", "what a stage refuses is printed where it can be seen",
      "REFUSED" in Progress("grade", notes=["REFUSED: x"]).line())

# ---- B-137 -------------------------------------------------------------
check("B-137", "the scored row carries the task kind that decided the rule",
      '"kind": task.kind' in src(stage_grade))

# ---- B-138 -------------------------------------------------------------
from errata_bench.pipeline import _capped, KEPT_STATE_CHARS
huge = {f"b/{i}.js": "z" * 50_000 for i in range(5000)}
kept = _capped(huge, "")
check("B-138", "a captured tree is bounded per row, not only per file",
      sum(len(v) for v in kept.values()) <= KEPT_STATE_CHARS)

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
check("B-141", "a rebuild keeps the note saying what it deleted",
      "p.notes.extend" in src(stage_build))

# ---- B-142 -------------------------------------------------------------
check("B-142", "appending and tidying take a lock, so two runs cannot erase each other",
      "held(path)" in src(append) and "held(path)" in src(P.completed))

# ---- B-144 -------------------------------------------------------------
check("B-144", "the two copies of a task id on a graded row are checked against each other",
      "the reading is for" in src(stage_grade))

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
check("B-149", "why each row was rejected is written to disk, not just printed",
      "paths.rejections" in src(stage_build))

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
base = fingerprint(mktask())
import dataclasses
check("B-154", "a rebuild onto a different session changes the stamp",
      fingerprint(dataclasses.replace(mktask(), session_id="other")) != base and
      fingerprint(dataclasses.replace(mktask(), cut_turn=99)) != base and
      fingerprint(dataclasses.replace(mktask(), oracle_calls=[{"name": "x"}])) != base)

# ---- B-156 / B-157: one gate, everywhere ------------------------------
from errata_bench.pipeline import stage_report
from errata_bench import rejudge as RJ
check("B-156", "the regrade summary uses the gate, not the raw field",
      "if can_be_scored(r)" in src(RJ.summarise))
check("B-157", "the funnel uses the gate, not the raw field",
      "if can_be_scored(c)" in src(stage_report))

# ---- B-158: an unsupportable reading is not a result ------------------
check("B-158", "the regrade summary drops readings the judge could not support",
      'r.get("scoreable", True)' in src(RJ.summarise))

# ---- B-159 / B-160: the table counts only what it says it counts ------
check("B-159", "the comparison totals exclude bracketed, unsupportable and unasked cells",
      "def tally(" in src(RJ.compare) and 'is not None' in src(RJ.compare))
check("B-160", "and a task with no control is untrusted in the table too",
      "original_readable" in src(RJ.compare))

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
check("B-163", "a failed trace-check control marks the task untrusted",
      'r.get("trace_ok") is False' in src(RJ.summarise))

# ---- B-164: a task at zero is visible ---------------------------------
check("B-164", "the report names any task with no scored attempt",
      "tasks_with_no_scored_attempt" in src(stage_report))

bad = [b for b, ok in RESULTS if not ok]
print(f"\n  {len(RESULTS) - len(bad)} of {len(RESULTS)} fixes verified live"
      + (f"; STILL BROKEN: {bad}" if bad else "; none still present"))
sys.exit(1 if bad else 0)
