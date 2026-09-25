# One live check per bug found in this session. Not "the log says fixed" --
# each one runs the real code and asserts the behaviour that was wrong is now
# right. No network, no Docker, no model calls.
import asyncio, inspect, json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, "src")
os.environ["ERRATA_JUDGE_MODEL"] = "the-grader"
os.environ["ERRATA_MODEL"] = "the-candidate"
# These fixtures have no container image, and `run` is faked, so nothing here
# reaches the host: the opt-in is set so the stage still hands them to it.
os.environ["ERRATA_ALLOW_HOST"] = "1"

from errata_bench.score import attempt as A
from errata_bench.construct import container as C
from errata_bench.corpus import sessions as corpus
from errata_bench.score import judge as J
from errata_bench import store as P
from errata_bench import llm as reader
from errata_bench.corpus import turns as turns_mod
from errata_bench.score import rejudge
from errata_bench.score import trace as T
from errata_bench.score.attempt import Attempt, ToolCall
from errata_bench.score.judge import Judgement
from errata_bench.stages import run_stages, stage_attempt, stage_build, stage_grade
from errata_bench.store import Paths, Progress, append, load
# from the code, not a copy: a control added there must appear in every
# fixture, or the fixture quietly stops admitting its tasks.
from errata_bench.instrument.control import CONTROLS
CONTROL_NAMES = tuple(c.name for c in CONTROLS)
from errata_bench.spec import Task, fingerprint, write
from errata_bench.score.structure import Structure
from errata_bench.score.trace import Claim, TraceCheck

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
# Spelled out, not `(*a, **k)`. A stand-in that swallows every argument cannot
# notice when the real reader grows one: `judge` gained `changed` on 09-21 and
# this file would have gone on grading without it, silently, while the three
# files with explicit signatures broke at once and said so.
async def fake_judge(task, answer, *, model=None, swap_references=False, tool_calls=None,
                     changed=None, context=""):
    judged["n"] += 1
    return Judgement(True, False, False, True, "I read it.", "ok", True)
# `tool_calls`, the name the real `check` uses. Named `calls` here, every
# caller happened to pass it positionally, so nothing broke -- and the first
# caller to pass it by keyword would have broken every stand-in at once with
# a TypeError naming the wrong thing.
async def fake_check(answer, tool_calls, *, model=None, context="", given=""):
    return TraceCheck(claims=[Claim(claim="c", supported=True, evidence="e")], reasoning="ok")
A.run, J.judge, T.check = fake_run, fake_judge, fake_check
A.transcript_for = lambda t, turns: "conversation"
A.transcripts_for = lambda ts: {t.task_id: "conversation" for t in ts}
# The served-model probe calls the network (D-36 A6); never from a check.
import errata_bench.llm as _llm_served
_served_calls: list[str] = []
_llm_served.served = lambda model: _served_calls.append(model) or {"deployment": model, "served_model": "stand-in"}
# The control stage reads what each control is read against (D-36 A3). Without
# this stand-in the stage read the real corpus wherever one was present -- a
# 1.3 GB file, silently, on a laptop -- and failed where none was, in CI.
A.control_conversations_for = lambda ts: {
    t.task_id: {"cut": "conversation", "resolution": "conversation", "last_action": None} for t in ts}
C.image_for = lambda l, **k: None
C.sweep = lambda: None
C.max_containers = lambda: 2
corpus.load_repos = lambda: {}
# One stand-in turn per session asked for: a session the corpus lacks is
# refused since B-263 (guards_hold section 113).
turns_mod.load_session_turns = lambda ids: {i: [{"turn_number": 0, "turn_type": "user_prompt",
                                                  "content": "a stand-in turn"}] for i in ids}


def mktask(tid="t", defect="a defect"):
    return Task(tid, "r/r", "u", "sha", "s", 10, 11, 12, 13, "w" * 20, "r" * 20, defect, "none")


def run_dir(tids=("t",)):
    p = Paths(Path(tempfile.mkdtemp()) / "run")
    write([mktask(t) for t in tids], p.tasks)
    p.calibration.write_text("".join(json.dumps({"task_id": t, "sound": True}) + "\n" for t in tids))
    p.controls.write_text("".join(json.dumps({"task_id": t, "control": c, "ok": True}) + "\n"
                                  for t in tids for c in CONTROL_NAMES))
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
from errata_bench.construct.container import _abandoned
check("B-124", "a sweep spares a live peer's container and takes its own",
      _abandoned(f"errata-{os.getpid()}-x", os.getpid()) and
      not _abandoned(f"errata-{os.getppid()}-x", os.getpid()))

# ---- B-125 -------------------------------------------------------------
import errata_bench.construct.build as B
d = run_dir()
append(d.attempts, {"task_id": "t", "run": 0, "passed": True})
B.build = lambda rows: (_ for _ in ()).throw(AssertionError("build must not run"))
p = stage_build(d, 10**9)
check("B-125", "a rebuild refuses to empty a directory that holds results",
      len(load(d.tasks)) == 1 and len(load(d.attempts)) == 1 and any("refused" in n for n in p.notes))

# ---- B-210: the same wipe, reached from the other side ------------------
# B-125's guard only asks whether there was anything to build FROM. The likelier
# accident is a full screened.jsonl every row of which fails for a reason that
# has nothing to do with the tasks: an unreachable remote, an absent git, an
# expired token. Measured against a copy of runs/scale400c with a git that
# cannot resolve its host: 11 tasks, 11 calibrations, 12 controls and 18 graded
# attempts to zero, from a command that exits 0 and prints "0 produced, 51
# already done".
from errata_bench.spec import BuildResult, Rejection

d = run_dir()
append(d.attempts, {"task_id": "t", "run": 0, "passed": True})
append(d.screened, {"session_id": "s", "complaint": 1})
B.build = lambda rows, **kw: BuildResult(
    tasks=[],
    rejected=[Rejection(repo_id="r", complaint_turn=1,
                        reason="could not build the tree: unable to access")],
)
p = stage_build(d, 10**9)
check("B-210", "a rebuild that builds nothing from something prunes nothing",
      len(load(d.tasks)) == 1 and len(load(d.attempts)) == 1
      and len(load(d.calibration)) == 1 and len(load(d.controls)) == len(CONTROL_NAMES)
      and any("refused" in n for n in p.notes))

# and a capped rebuild still rebuilds everything, and says so. It was briefly
# refused instead: nothing is ever truncated -- build() takes no cap and is
# handed every row -- so the refusal only blocked correct runs, and it broke
# the incremental `--max-rows N` workflow from the second pass on, for ever.
d = run_dir()
append(d.attempts, {"task_id": "t", "run": 0, "passed": True})
for i in range(3):
    append(d.screened, {"session_id": f"s{i}", "complaint": i})
saw = {"rows": 0}
def counting_build(rows, **kw):
    saw["rows"] = len(rows)
    return BuildResult(tasks=[mktask("t")], rejected=[])
B.build = counting_build
p = stage_build(d, 1)
check("B-211", "a capped rebuild still builds every screened row, and says so",
      saw["rows"] == 3 and any("does not apply to build" in n for n in p.notes)
      and len(load(d.tasks)) == 1 and len(load(d.attempts)) == 1)

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
def delays_once():
    got, real = [], asyncio.sleep
    async def spy(d_, *a, **k): got.append(d_)
    asyncio.sleep = spy
    try:
        asyncio.run(reader.resilient(always_429, attempts=4, pause=10))
    except RuntimeError:
        pass
    finally:
        asyncio.sleep = real
    return got

# Twelve draws, not one. Three delays that merely differ from each other is
# what plain backoff gives, so a single sample passed with the jitter removed
# and printed [10, 20, 30] beside the words "not in lockstep".
runs = [delays_once() for _ in range(12)]
firsts, thirds = [r[0] for r in runs], [r[2] for r in runs]
check("B-129", f"each retry is drawn fresh and still backs off: first {min(firsts):.0f}-{max(firsts):.0f}s, third {min(thirds):.0f}-{max(thirds):.0f}s",
      all(len(r) == 3 for r in runs) and len(set(firsts)) == len(firsts)
      and min(thirds) > max(firsts))

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
sh = Path("scripts/attempt-rounds.sh").read_text() if Path("scripts/attempt-rounds.sh").exists() else ""
attempt_half = sh.split("--only grade")[0]
check("B-131", "the retry driver counts candidate errors in the file that holds them",
      bool(_re.search(r"--only attempt[\s\S]{0,400}?answers\.jsonl", sh))
      and "attempts.jsonl" not in attempt_half.split("# ")[-1]
      and "--only grade" in sh)
rg = Path("scripts/regrade-all.sh").read_text() if Path("scripts/regrade-all.sh").exists() else ""
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
    asyncio.run(run_stages(refuse.root, ("grade",), concurrency=2))
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
from errata_bench.stages.scoring import KEPT_STATE_CHARS, _capped
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
       "from pathlib import Path; from errata_bench.store import held;"
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
d = run_dir(("keeps", "loses-its-control", "orphan-me", "gets-rebuilt"))
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
ctl = [dict(r, ok=r["ok"] and r["task_id"] != "loses-its-control") for r in load(d.controls)]
P.replace(d.controls, ctl)
# orphan-me leaves the task list entirely: its answer is `build`'s to prune,
# not this stage's. Without an orphan in the fixture the branch that did the
# destroying was never entered with one, and the original bug passed.
write([mktask("keeps"), mktask("loses-its-control"),
       mktask("gets-rebuilt", "a different defect")], d.tasks)
prog = asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
left = {r["task_id"] for r in load(d.answers)}
check("B-150", "an answer survives its task failing a control this run",
      "loses-its-control" in left)
check("B-150b", "and an orphan is left for the rebuild to prune, not deleted here",
      "orphan-me" in left)
check("B-150c", "and the note counts exactly what was removed",
      any("dropped 1 answers" in n for n in prog.notes)
      and left == {"keeps", "loses-its-control", "orphan-me", "gets-rebuilt"})

# ---- B-151: the rewrite must not write back a stale snapshot ----------
# A peer appends while the stale rewrite is deciding what to keep. Taken from
# a snapshot read before the lock, its row is gone with the rename; read under
# the lock, it survives. The grep this replaces passed with the read moved back
# outside the lock and the string left in a comment.
d = run_dir(("keeps", "gets-rebuilt"))
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
write([mktask("keeps"), mktask("gets-rebuilt", "a different defect")], d.tasks)
# Patched where it is looked up. `stages/scoring.py` does `from ..store
# import load`, binding the function at import time, so replacing
# `store.load` after that intercepts nothing -- before the split this all
# lived in one module and patching it there worked.
from errata_bench.stages import scoring as _scoring
_load = _scoring.load
def slow_load(path):
    rows = _load(path)
    if path.name == "answers.jsonl" and not getattr(slow_load, "fired", False):
        slow_load.fired = True
        subprocess.run([sys.executable, "-c",
            "import sys; sys.path.insert(0, 'src');"
            "from pathlib import Path; from errata_bench.store import append;"
            f"append(Path({str(d.answers)!r}), {{'task_id': 'peer', 'run': 0}})"], check=True)
    return rows
_scoring.load = slow_load
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
_scoring.load = _load
check("B-151", "a row appended by another process during the rewrite is still there",
      "peer" in {r.get("task_id") for r in load(d.answers)})

# ---- B-152: the two gate files carry the task version -----------------
from errata_bench.stages import stage_calibrate, stage_control
from errata_bench.score import judge as JM
from errata_bench.instrument import control as CM
d = run_dir()
_cal, _chk = JM.calibrate, CM.check
async def fake_cal(t, *, model=None, conversations=None):
    from errata_bench.score.judge import Calibration
    return Calibration(t.task_id, "off_target", "solved", False, True,
                       "off_target", "solved", False, True)
async def fake_ctl(task, control, *, model=None, context="", action=None):
    from errata_bench.instrument.control import ControlResult
    return ControlResult(task.task_id, control.name, False, True, False, True)
JM.calibrate, CM.check = fake_cal, fake_ctl
# run_dir() pre-writes both gate files so the other checks have a gate; clear
# them, or these two stages find their work already done and stamp nothing.
d.calibration.write_text(""); d.controls.write_text("")
try:
    asyncio.run(stage_calibrate(d, 10**9, 2))
    asyncio.run(stage_control(d, 10**9, 2))
    stamped_cal = load(d.calibration) and load(d.calibration)[0].get("task_fingerprint")
    stamped_ctl = load(d.controls) and all(r.get("task_fingerprint") for r in load(d.controls))
finally:
    JM.calibrate, CM.check = _cal, _chk
check("B-152", "the two gate files carry the version of the task they judged",
      stamped_cal == fingerprint(mktask()) and stamped_ctl)
d.screened.write_text(json.dumps({"session_id": "s", "turn_number": 1}) + "\n")
class _B3:
    tasks = [mktask(defect="rebuilt")]
    rejected = []
B.build = lambda rows_: _B3()
stage_build(d, 10**9)
check("B-152b", "and a rebuild prunes both of them",
      not load(d.calibration) and not load(d.controls))

# ---- B-153: two tasks may not share one name --------------------------
# Two sessions landing on the same (repository, turn), which 93 of 400 moments
# in one run do. The grep this replaces passed with the refusal deleted and the
# sentence left in a comment.
seen_ids, dupes = set(), []
for row in ({"session_id": "s1", "repo_id": "r/r", "complaint": 7},
            {"session_id": "s2", "repo_id": "r/r", "complaint": 7}):
    tid = f"{row['repo_id'].replace('/', '-')}-{row['complaint']}"
    (dupes if tid in seen_ids else seen_ids).append(tid) if tid in seen_ids else seen_ids.add(tid)
# Asserted by running `build`, not by searching its source for a sentence.
# The old form passed on a comment and a line that could both survive the
# behaviour being reverted -- and it read a path that the restructure moved,
# which is how it was noticed.
from errata_bench.construct.build import build as _real_build

_twin = {"session_id": "s1", "repo_id": "r/r", "complaint": 7, "cut": 1,
         "kind": "none", "defect": "d" * 40}
_res = _real_build([dict(_twin), dict(_twin, session_id="s2")])
_names = [t.task_id for t in _res.tasks]
check("B-153", "two sessions cannot produce one task name",
      len(_names) == len(set(_names)) and len(seen_ids) == 1)

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
from errata_bench.stages import stage_report
from errata_bench.score import rejudge as RJ

# ---- B-158: an unsupportable reading is not a result ------------------

# ---- B-159 / B-160: the table counts only what it says it counts ------

# ---- B-161: a regrade's own rows carry the task version ---------------
check("B-160", "regraded rows are stamped and keyed on the stamp",
      'stamps[a["task_id"]]' in src(RJ.regrade_all) and
      'r.get("task_fingerprint")' in src(RJ.regrade_all))

# ---- B-162: an empty capture is not a searched tree -------------------
from errata_bench.score.structure import analyse
from errata_bench.score.attempt import Attempt as At
t = mktask(); t = dataclasses.replace(t, signature_token="2000")
# All three answers, not two: both clauses of the old assertion were satisfied
# by `token_removed = True` unconditionally, which reports every token task as
# fixed.
check("B-161", "an empty capture abstains, one holding the token says no, one without says yes",
      analyse(t, At("t", "m"), {}).token_removed is None
      and analyse(t, At("t", "m"), {"a": "timeout = 2000"}).token_removed is False
      and analyse(t, At("t", "m"), {"a": "no token here"}).token_removed is True)

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
check("B-155", "a task the original judge really read is not listed as one it could not",
      summary["known_pair"]["of_the_tasks_the_original_read"] == "1/1"
      and summary["known_pair"]["that_the_original_could_not"] == [])

# ---- B-157: the funnel's gate ----------------------------------------
srcp, _ = rejudge_fixture(sound_field=False)
stage_report(srcp)
check("B-156", "the funnel counts a task whose pass/fail line holds",
      json.loads(srcp.report.read_text())["funnel"]["tasks_calibrated"] == 1)

# ---- B-158: an unsupportable reading is not a result ------------------
srcp, outp = rejudge_fixture(scoreable=False)
check("B-157", "a reading the judge could not support is dropped from the rate",
      RJ.summarise(srcp, outp, "the-judge")["counted"]["attempts"] == 0)

# ---- B-163: a checker that failed its own control is untrusted --------
srcp, outp = rejudge_fixture(trace_ok=False)
check("B-162", "a task whose trace control failed is not counted",
      RJ.summarise(srcp, outp, "the-judge")["counted"]["attempts"] == 0)

# ---- B-159 / B-160: the table counts only what it says --------------
srcp, outp = rejudge_fixture(scoreable=False)
table = RJ.compare(srcp.root)
check("B-158", "an unsupportable cell is left out of the totals",
      "0/0" in table)
srcp, outp = rejudge_fixture(controls=False)
check("B-159", "a task with no control is untrusted in the table",
      "0/0" in RJ.compare(srcp.root))

# ---- B-164: a task with no scored attempt is named -------------------
srcp, _ = rejudge_fixture(scoreable=False)
stage_report(srcp)
check("B-163", "a task whose every attempt is unreadable is named as unscored",
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
check("B-187", f"a regrade after a rebuild leaves one grade, not two ({first} then {len(rows2)})",
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

# ======================================================================
# The four functions that decide what enters a published rate. A mutation
# audit found each of them revertible with all three scripts still green:
# `can_be_scored` returning true for everything, `line_holds` inverted, and
# either control gate removed. Nothing measured them until here.
# ======================================================================

from errata_bench.score.judge import can_be_scored, line_holds

HOLDS = {"failed_outcome": "off_target", "failed_outcome_swapped": "off_target",
         "resolution_outcome": "solved", "resolution_outcome_swapped": "solved"}
BREAKS = dict(HOLDS, resolution_outcome="off_target")
check("GATE-1", "a task whose known-right answer does not read as solved is refused",
      line_holds(HOLDS) is True and line_holds(BREAKS) is False)
check("GATE-2", "and a row claiming sound cannot override the line it fails",
      can_be_scored({"sound": True, **BREAKS}) is False
      and can_be_scored({"sound": True, **HOLDS}) is True
      and can_be_scored({"sound": False, **HOLDS}) is True)

# the calibration gate, through the stage that spends containers
d = run_dir()
P.replace(d.calibration, [{"task_id": "t", "sound": True, **BREAKS}])
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
check("GATE-3", "no candidate runs against a task whose known pair does not separate",
      not load(d.answers))

# the control gate, through both stages that apply it
for missing, label in ((True, "never ran"), (False, "half ran")):
    d = run_dir()
    rows = [] if missing else [{"task_id": "t", "control": "null", "ok": True}]
    P.replace(d.controls, rows)
    asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
    check(f"GATE-4 ({label})", f"no candidate runs against a task whose controls {label}",
          not load(d.answers))

d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
P.replace(d.controls, [{"task_id": "t", "control": "null", "ok": True}])
pg = asyncio.run(stage_grade(d, 10**9, concurrency=2))
check("GATE-5", "and an answer already collected is not graded once its controls lapse",
      not load(d.attempts) and any("controls" in n for n in pg.notes))

# and the report counts the same set
d = run_dir()
asyncio.run(stage_attempt(d, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(d, 10**9, concurrency=2))
P.replace(d.controls, [{"task_id": "t", "control": "null", "ok": False},
                       {"task_id": "t", "control": "overclaim", "ok": True}])
stage_report(d)
check("GATE-6", "and a task whose control fails leaves the report's rate",
      json.loads(d.report.read_text())["attempts"] == 0)

# Counted, because a stand-in nothing calls proves nothing: without it the
# stages' probes went to the real model wherever a credential was found.
check("A6", "every served-model probe the stages made went to the stand-in, not the network",
      bool(_served_calls))

bad = [b for b, ok in RESULTS if not ok]
print(f"\n  {len(RESULTS) - len(bad)} of {len(RESULTS)} fixes verified live"
      + (f"; STILL BROKEN: {bad}" if bad else "; none still present"))
sys.exit(1 if bad else 0)
