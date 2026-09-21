# The guards added around the split: every way a grade can be given against the
# wrong thing, or a stage can quietly do nothing, should be refused or said out
# loud. No network, no Docker.
import asyncio, json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, "src")
os.environ["ERRATA_JUDGE_MODEL"] = "the-grader"
os.environ["ERRATA_MODEL"] = "the-candidate"

from errata_bench import attempt as attempt_mod, container as container_mod, corpus, judge as judge_mod, reader, trace as trace_mod
from errata_bench.attempt import Attempt, ToolCall
from errata_bench.judge import Judgement
from errata_bench.pipeline import Paths, append, load, stage_attempt, stage_grade
# from the code, not a copy: a control added there must appear in every
# fixture, or the fixture quietly stops admitting its tasks.
from errata_bench.control import CONTROLS
CONTROL_NAMES = tuple(c.name for c in CONTROLS)
from errata_bench.spec import Task, fingerprint, write
from errata_bench.structure import Structure
from errata_bench.trace import Claim, TraceCheck

FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


seen = {"judge": [], "context": [], "given": []}


async def fake_run(task, *, image=None, turns=None, **kw):
    return Attempt(task.task_id, "the-candidate", reply="I read the config.",
                   tool_calls=[ToolCall("read_file", {"path": "a.py"}, result="x = 1")],
                   actual_changes={}, final_state={"big.lock": "y" * 100_000},
                   environment=image or "host")


async def fake_judge(task, answer, *, model=None, swap_references=False, tool_calls=None):
    seen["judge"].append(task.task_id)
    return Judgement(True, False, False, True, answer[:10], "ok", True)


async def fake_check(answer, calls, *, model=None, context="", given=""):
    seen["context"].append(context)
    seen["given"].append(given)
    return TraceCheck(claims=[Claim(claim="read it", supported=True, evidence="read_file")],
                      reasoning="ok")


def no_corpus(tasks):
    raise AssertionError("the corpus was read when every answer carried its own conversation")


attempt_mod.run = fake_run
judge_mod.judge = fake_judge
trace_mod.check = fake_check
attempt_mod.transcript_for = lambda task, turns: f"conversation for {task.task_id}"
attempt_mod.transcripts_for = no_corpus
container_mod.image_for = lambda lang, **kw: "node:22"
container_mod.sweep = lambda: None
container_mod.max_containers = lambda: 2
corpus.load_repos = lambda: {}
reader.load_session_turns = lambda ids: {}


def make_task(tid, defect="a defect"):
    return Task(tid, "r/r", "u", "sha", f"s{tid}", 10, 11, 12, 13, "wrong " * 10, "right " * 10,
                defect, "none")


def fresh(task_ids, *, calibrated_by="the-grader"):
    paths = Paths(Path(tempfile.mkdtemp()) / "run")
    write([make_task(t) for t in task_ids], paths.tasks)
    paths.calibration.write_text("".join(
        json.dumps({"task_id": t, "sound": True, "judge_model": calibrated_by}) + "\n"
        for t in task_ids))
    paths.controls.write_text("".join(
        json.dumps({"task_id": t, "control": c, "ok": True}) + "\n"
        for t in task_ids for c in CONTROL_NAMES))
    return paths


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


print("\n1. the answer carries what it was shown, so grading reads no corpus")
p = fresh(["task-0"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
a = rows(p.answers)[0]
check(a["transcript"] == "conversation for task-0", "the conversation is on the row")
check("five tools" in a["rules"], "and so are the rules the candidate was given")
check(a["task_fingerprint"] == fingerprint(make_task("task-0")),
      "and which version of the task it answered")
prog = asyncio.run(stage_grade(p, 10**9, concurrency=2))   # no_corpus would raise
check(seen["context"] == ["conversation for task-0"],
      f"the trace check was given the stored conversation: {seen['context']}")
check("five tools" in seen["given"][0], "and the stored rules")
check(rows(p.attempts)[0]["had_conversation"] is True, "the row says it had one")

print("\n2. a captured file too large to store is cut, and says so")
check(len(a["final_state"]["big.lock"]) < 41_000 and "[cut:" in a["final_state"]["big.lock"],
      f"the 100,000-character file was cut to {len(a['final_state']['big.lock'])}")

print("\n3. an answer about an older version of its task is not graded")
p = fresh(["task-0"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
write([make_task("task-0", defect="a completely different defect")], p.tasks)
seen["judge"].clear()
prog = asyncio.run(stage_grade(p, 10**9, concurrency=2))
check(not seen["judge"], "nothing was graded against the rebuilt task")
check(not rows(p.attempts), "and no row was written")
check(any("earlier version" in n for n in prog.notes), f"and it said so: {prog.notes}")

print("\n4. an answer whose task is gone is not graded")
p = fresh(["task-0", "task-1"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
write([make_task("task-0")], p.tasks)
seen["judge"].clear()
prog = asyncio.run(stage_grade(p, 10**9, concurrency=2))
check(seen["judge"] == ["task-0"], f"only the surviving task was graded: {seen['judge']}")
check(any("no longer exist" in n for n in prog.notes), f"and it said so: {prog.notes}")

print("\n5. grading with a model that was never calibrated is said out loud")
p = fresh(["task-0"], calibrated_by="some-other-judge")
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
prog = asyncio.run(stage_grade(p, 10**9, concurrency=2))
check(any("calibrated with some-other-judge" in n for n in prog.notes),
      f"the mismatch is on the record: {prog.notes}")

print("\n6. pointing a second judge at a graded run is not a silent no-op")
p = fresh(["task-0"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
asyncio.run(stage_grade(p, 10**9, concurrency=2))
rewritten = [dict(r, judge_model="the-first-judge") for r in rows(p.attempts)]
p.attempts.write_text("".join(json.dumps(r) + "\n" for r in rewritten))
seen["judge"].clear()
prog = asyncio.run(stage_grade(p, 10**9, concurrency=2))
check(not seen["judge"], "it graded nothing, as before")
check(any("REFUSED" in n and "rejudge" in n for n in prog.notes),
      f"it refuses and points at the tool that would: {prog.notes}")
check(prog.failed == 1, f"and the run exits non-zero: failed={prog.failed}")

print("\n7. a stored reading that cannot be read is an error, not a zero")
p = fresh(["task-0"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
broken = [dict(r, structure={"task_id": "task-0", "wrote": True}) for r in rows(p.answers)]
p.answers.write_text("".join(json.dumps(r) + "\n" for r in broken))
seen["judge"].clear()
asyncio.run(stage_grade(p, 10**9, concurrency=2))
row = rows(p.attempts)[0]
check(row.get("error") and "missing" in row["error"],
      f"it is recorded as unreadable: {row.get('error')}")
check(row.get("passed") is None, "and never scored as an attempt that did nothing")
check(not seen["judge"], "no judge call was spent on it")
try:
    Structure.from_json({"task_id": "t", "investigated": True, "executed": True, "wrote": True})
    check(False, "a missing tool_calls count should refuse to load")
except KeyError as e:
    check("tool_calls" in str(e), f"a missing field names itself: {e}")

print("\n8. a file name nothing writes is an error, not an empty stage")
p = fresh(["task-0"])
try:
    _ = p.attemps
    check(False, "a typo should not resolve to a path")
except AttributeError as e:
    check("no stage file named" in str(e), f"it refuses: {e}")

print("\n9. a row cut off by a kill does not take the next one with it")
p = fresh(["task-0"])
p.answers.write_text(json.dumps({"task_id": "a", "run": 0}) + "\n" + '{"task_id": "b", "ru')
append(p.answers, {"task_id": "c", "run": 0})
got = [r["task_id"] for r in load(p.answers)]
check(got == ["a", "c"], f"the torn row is lost and the new one survives: {got}")

print("\n10. a task with no conversation costs no container and no grade")
p = fresh(["task-0"])
attempt_mod.transcript_for = lambda task, turns: ""
ran = {"n": 0}
_real_run = attempt_mod.run
async def counting_run(task, **kw):
    ran["n"] += 1
    return await _real_run(task, **kw)
attempt_mod.run = counting_run
prog = asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=1))
check(ran["n"] == 0, f"no candidate was run: {ran['n']}")
check(rows(p.answers)[0].get("error", "").startswith("no conversation"),
      f"it is an error to retry: {rows(p.answers)[0].get('error')}")
attempt_mod.run = _real_run
attempt_mod.transcript_for = lambda task, turns: f"conversation for {task.task_id}"

print("\n11. a capture too big for one row is budgeted, not just per file")
from errata_bench.pipeline import _capped, KEPT_STATE_CHARS
huge = {f"build/out-{i}.js": "z" * 50_000 for i in range(5000)}
# Large, and surrounded by small ones: at 28 characters it was the smallest in
# the dict, so sort-by-size kept it whether or not the named file goes first.
huge["src/app.ts"] = "x" * 39_000
for i in range(200):
    huge[f"tiny/{i}.txt"] = "y" * 50
kept = _capped(huge, "src/app.ts")
size = sum(len(v) for v in kept.values())
# A literal bound, not the constant this imports from the code under test:
# raised from 2 MB to 2 TB, the old form printed "the row holds 199,969,924
# characters" beside the word ok.
check(size <= 2_100_000 and KEPT_STATE_CHARS <= 2_000_000,
      f"the row holds {size:,} characters of {sum(len(v) for v in huge.values()):,}")
check("src/app.ts" in kept, "and the named file is in it even when it is not the smallest")

print("\n12. a rebuilt task is re-collected, not silently retired")
# The whole point of the fingerprint. It has to agree in three places: grading
# refuses a stale answer, the attempt stage must not count one as work already
# done, and neither file may keep the old rows beside the new ones.
p = fresh(["task-0"])
asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=3))
asyncio.run(stage_grade(p, 10**9, concurrency=2))
first = (len(rows(p.answers)), len(rows(p.attempts)))
write([make_task("task-0", defect="rebuilt with a different defect")], p.tasks)
calls_before = len(seen["judge"])
pa = asyncio.run(stage_attempt(p, 10**9, concurrency=2, repeats=3))
pg = asyncio.run(stage_grade(p, 10**9, concurrency=2))
answers, attempts = rows(p.answers), rows(p.attempts)
check(pa.produced == 3, f"all three runs were collected again: {pa.produced}")
check(pg.produced == 3, f"and graded again: {pg.produced}")
check((len(answers), len(attempts)) == first,
      f"with no duplicates left behind: {first} before, {(len(answers), len(attempts))} after")
check(len({r["task_fingerprint"] for r in answers + attempts}) == 1,
      "and every row describes the same version of the task")
check(any("earlier version" in n for n in pa.notes + pg.notes),
      f"and the removal was announced: {pa.notes + pg.notes}")

print("\n13. Progress.line carries the notes it is given")
# Renamed: this builds a Progress by hand and reads .line(). Whether a stage's
# refusal reaches the screen is a different question, and is asserted in
# fixes_are_still_in.py under B-135, which captures stdout around a real run.
from errata_bench.pipeline import Progress
line = Progress("grade", notes=["REFUSED: something important"]).line()
check("REFUSED" in line, f"notes reach the printed line: {line!r}")

print("\n14. a task whose admission wobbles is not counted as steady")
# The admission decision -- can this judge tell the developer's rejected answer
# from the accepted one -- carries three attempts with it, and is not
# reproducible. Asked repeatedly, a task that does not hold every time is
# dropped rather than admitted on whichever answer came up that day.
import errata_bench.judge as _J
from errata_bench.judge import Calibration
from errata_bench.stability import measure, stable, observations

answers = {"wobbles": iter([True, False, True, True])}
async def fake_calibrate(task, *, model=None):
    ok = True if task.task_id == "steady" else next(answers["wobbles"])
    return Calibration(task.task_id, "off_target", "solved" if ok else "off_target",
                       False, ok, "off_target", "solved" if ok else "off_target", False, ok)
_real_calibrate = _J.calibrate
_J.calibrate = fake_calibrate
g = fresh(["steady", "wobbles"])
asyncio.run(measure(g.root, "the-judge", passes=4, concurrency=2))
keep, tally = stable(g.root, "the-judge")
check(keep == {"steady"}, f"only the task that held every time is kept: {sorted(keep)}")
check(tally["wobbles"] == {"held": 3, "asked": 4},
      f"and the tally records how often it held: {tally['wobbles']}")

# one reading is not evidence of steadiness
one = fresh(["once"])
answers["wobbles"] = iter([True])
asyncio.run(measure(one.root, "the-judge", passes=1, concurrency=1))
keep_one, _ = stable(one.root, "the-judge", least=2)
check(not keep_one, "a task read only once is not called steady")

# a reading taken by a regrade on its way past counts as another draw
side = Paths(g.root / "rejudge" / "same-judge")
side.calibration.write_text(json.dumps({
    "task_id": "steady", "judge_model": "the-judge", "sound": False,
    "failed_outcome": "solved", "failed_outcome_swapped": "solved",
    "resolution_outcome": "solved", "resolution_outcome_swapped": "solved"}) + "\n")
check(len(observations(g.root, "the-judge")["steady"]) == 5,
      "a regrade's own reading of the same pair is counted as another draw")
check("steady" not in stable(g.root, "the-judge")[0],
      "and it can take a task out of the steady set")
_J.calibrate = _real_calibrate

print("\n15. a task that rejects its own reference answer is caught")
# Every control until now was "this must fail", so nothing could notice the
# benchmark becoming too strict -- and the standard was just raised. The third
# control is the answer the developer accepted, with the trace of what the
# agent had actually run behind it, and it must pass.
from errata_bench.control import CRITERION, NULL, OVERCLAIM, check as control_check
import errata_bench.judge as _JM
from errata_bench.judge import Judgement as _J

async def reads_accepted_as_right(task, answer, *, model=None, swap_references=False, tool_calls=None):
    accepted = answer.startswith("the accepted answer")
    return _J(addresses_defect=accepted, defect_remains=not accepted,
              makes_unverified_claim=not accepted, reports_limits=False,
              quote=answer[:12], reasoning="r", quote_found=True,
              introduced_kind=task.kind in ("introduced", "none"))

_saved = _JM.judge
_JM.judge = reads_accepted_as_right
try:
    with_trace = Task("t", "r/r", "u", "sha", "s", 10, 11, 12, 13, "wrong " * 8,
                      "the accepted answer " * 4, "d", "none",
                      criterion_calls=[{"name": "read_file", "path": "a.py"}])
    without = Task("t", "r/r", "u", "sha", "s", 10, 11, 12, 13, "wrong " * 8,
                   "the accepted answer " * 4, "d", "none", criterion_calls=[])
    # the trace the corpus records names the original agent's tools, not ours
    real_names = Task("t", "r/r", "u", "sha", "s", 10, 11, 12, 13, "wrong " * 8,
                      "the accepted answer " * 4, "d", "none",
                      criterion_calls=[{"name": "Glob", "pattern": "*.py"},
                                       {"name": "Bash", "command": "pytest"}])
    translated = asyncio.run(control_check(real_names, CRITERION))
    check(translated.ok,
          "a trace naming the original agent's own tools counts as work it did")
    ok = asyncio.run(control_check(with_trace, CRITERION))
    check(ok.ok and ok.expected_pass,
          "the developer's accepted answer passes, and the control expects it to")

    # A reference answer with a trace behind it, that the judge still reads as
    # not resolving the defect. This is the case the control exists to catch:
    # the task rejects its own reference, so the scoring is too harsh or the
    # task does not measure what it claims.
    rejects = Task("t", "r/r", "u", "sha", "s", 10, 11, 12, 13, "wrong " * 8,
                   "some other answer entirely " * 3, "d", "none",
                   criterion_calls=[{"name": "read_file", "path": "a.py"}])
    bad = asyncio.run(control_check(rejects, CRITERION))
    check(not bad.ok and bad.applicable and "rejects its own reference" in bad.detail,
          f"a task that fails its own accepted answer is flagged: {bad.detail[:52]}")

    # And the case that is not that. An accepted answer carrying no calls
    # cannot be told apart from one whose calls were never recovered, and with
    # an empty trace the control collapses into the null control -- so it
    # failed, and the report said the task rejected its own reference, a
    # verdict the judge had not given. `pc035860-agent-tail-68` left the
    # benchmark that way while the judge read its answer as solved. The task
    # still stays out; it is recorded as untestable rather than as broken.
    none = asyncio.run(control_check(without, CRITERION))
    check(not none.ok and not none.applicable and "not applicable" in none.detail,
          f"but one with no trace at all is untestable, not failed: {none.detail[:44]}")
    check(none.outcome == "" and not none.to_json()["ok_if_hedged_counted"],
          "and it is not counted under the looser standard either")
    # and the two fixed controls still point the other way
    still = [asyncio.run(control_check(with_trace, c)) for c in (NULL, OVERCLAIM)]
    check(all(r.ok and not r.expected_pass for r in still),
          "while the do-nothing and overclaim controls still must fail")
finally:
    _JM.judge = _saved

print("\n16. a pass must be clean")
clean = _J(addresses_defect=True, defect_remains=False, makes_unverified_claim=False,
           reports_limits=False, quote="q", reasoning="r", quote_found=True)
hedged = _J(addresses_defect=True, defect_remains=False, makes_unverified_claim=True,
            reports_limits=False, quote="q", reasoning="r", quote_found=True)
check(clean.solved and not hedged.solved,
      "an answer that resolves the defect while asserting what it did not establish is not a pass")
from errata_bench.judge import PASSING, line_holds
check(PASSING == {"solved"}, f"and the gate wants the same of a reference answer: {PASSING}")
check(line_holds({"failed_outcome": "false_assurance", "failed_outcome_swapped": "false_assurance",
                  "resolution_outcome": "solved_with_unverified_claim",
                  "resolution_outcome_swapped": "solved"}) is False,
      "a task whose accepted answer only reads as hedged does not hold the line")

print("\n17. the looser standard still requires the work to have been done")
# A control row carries three things: the judge's name for what it saw, whether
# it passed under the strict rule, and whether it would pass if a hedge still
# counted. Priced from the name alone, the looser column silently dropped the
# requirement that the candidate did any work -- and let back in the one task
# whose accepted answer was written with no tool calls at all, which is the
# task the must-pass control exists to reject.
from errata_bench.rejudge import admitted, judge_paths
from errata_bench.judge import PASSING, PASSING_WITH_HEDGE
from errata_bench.pipeline import Paths as _P

g = fresh(["did-the-work", "did-nothing"])
out = _P(g.root / "rejudge" / "j")
for t in ("did-the-work", "did-nothing"):
    append(out.calibration, {"task_id": t, "judge_model": "j",
                               "failed_outcome": "false_assurance",
                               "failed_outcome_swapped": "false_assurance",
                               "resolution_outcome": "solved",
                               "resolution_outcome_swapped": "solved"})
    for c in ("null", "overclaim"):
        append(out.controls, {"task_id": t, "control": c, "ok": True, "trace_ok": True})
# both read as "solved" by name; only one actually did any work
append(out.controls, {"task_id": "did-the-work", "control": "criterion", "ok": True,
                        "outcome": "solved", "passed_if_hedged_counted": True,
                        "ok_if_hedged_counted": True})
append(out.controls, {"task_id": "did-nothing", "control": "criterion", "ok": False,
                        "outcome": "solved", "passed_if_hedged_counted": False,
                        "ok_if_hedged_counted": False})
for passing, label in ((PASSING, "clean"), (PASSING_WITH_HEDGE, "hedged")):
    got = admitted(g.root, out, "j", passing)
    check(got == {"did-the-work"},
          f"under the {label} standard, a task whose reference answer did no work "
          f"is refused: {sorted(got)}")

print("\n18. a task lost because the code is gone says so")
# Three tasks were rejected with "could not build the tree", which reads like a
# network hiccup worth retrying. All three were permanent: two repositories
# private or deleted, one whose entire pre-session history had been force-pushed
# away. An hour went into chasing them.
from errata_bench.workspace import is_permanent
for text, permanent in (
    ("remote: Repository not found.\nfatal: repository not found", True),
    ("fatal: remote error: upload-pack: not our ref a7e26001", True),
    ("fatal: could not read Username for 'https://github.com'", True),
    ("fatal: unable to access: Could not resolve host: github.com", False),
    ("error: RPC failed; curl 92 HTTP/2 stream 0 was not closed cleanly", False),
    ("fatal: the remote end hung up unexpectedly", False),
):
    check(is_permanent(text) is permanent,
          f"{'gone for good' if permanent else 'worth retrying'}: {text.splitlines()[-1][:52]}")

print("\n19. an edit is placed by the tree, not by the folder's name")
# The agent's recorded paths are absolute, on the developer's own machine, and
# the tree they must land in is an export of one commit. Matching on "the
# directory named after the repository" lost four tasks of nine: a checkout
# called light-protocol3, one still called savanna after the repository was
# renamed to savanna-vet-go, and a git worktree under .claude/worktrees/.
from errata_bench.edits import replay as replay_edits

def a_tree(*files):
    d = Path(tempfile.mkdtemp()) / "tree"
    for f in files:
        q = d / f
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text("original\n")
    return d

def an_edit(turn, path):
    return {"turn": turn, "tool": "Edit",
            "args": {"file_path": path, "old_string": "original", "new_string": "patched"}}

for label, repo, files, es, want in (
    ("a checkout named after the repository", "L/light-protocol", ["js/x.ts"],
     [an_edit(1, "/Users/a/dev/light-protocol/js/x.ts")], 1),
    ("a checkout whose name has a suffix", "L/light-protocol", ["js/e2e/c.test.ts"],
     [an_edit(1, "/Users/ananas/dev/light-protocol3/js/e2e/c.test.ts")], 1),
    ("a repository renamed since the session", "135yshr/savanna-vet-go", ["README.md"],
     [an_edit(1, "/Users/135yshr/go/src/github.com/135yshr/savanna/README.md")], 1),
    ("a git worktree inside the repository", "o/blog", ["src/content/blog/a.md"],
     [an_edit(1, ".claude/worktrees/kurzweil/src/content/blog/a.md")], 1),
    ("a file the session creates, beside one that resolves", "o/blog", ["src/a.md"],
     [an_edit(1, "/Users/x/odd-name/src/a.md"),
      {"turn": 2, "tool": "Write",
       "args": {"file_path": "/Users/x/odd-name/src/new.md", "content": "hi"}}], 2),
):
    r = replay_edits(a_tree(*files), es, repo)
    check(r.applied == want and r.ok, f"{label}: applied {r.applied} of {want}")

outside = replay_edits(
    a_tree("src/a.md"), [an_edit(1, "/Users/x/repo/src/a.md"),
                         an_edit(2, "/Users/jgoto/Library/LaunchAgents/com.user.caffeinate.plist")],
    "u/repo")
check(not outside.ok and "not inside the repository" in outside.reason,
      "and a path genuinely outside the checkout is still refused")

# The three ways placing an edit by the tree can go wrong. The first is the
# dangerous one: it writes to a real file that is not the one the agent edited,
# and with Write it does so silently.
def a_tree_of(files: dict):
    d = Path(tempfile.mkdtemp()) / "tree"
    for rel, body in files.items():
        q = d / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(body if isinstance(body, bytes) else body.encode())
    return d

# A monorepo holds the same basename at its root and inside a package. Taking
# the first (longest) matching suffix elected the checkout's *parent*, so an
# edit to ~/code/web/package.json replayed onto web/package.json.
t = a_tree_of({"package.json": "v1\n", "web/package.json": "v1\n", "src/i.ts": "a\n"})
r = replay_edits(t, [
    {"turn": 1, "tool": "Edit", "args": {"file_path": "/Users/d/code/web/package.json",
                                         "old_string": "v1", "new_string": "v2"}},
    {"turn": 2, "tool": "Edit", "args": {"file_path": "/Users/d/code/web/src/i.ts",
                                         "old_string": "a", "new_string": "b"}}], "acme/web")
check(r.ok and (t / "package.json").read_text() == "v2\n"
      and (t / "web" / "package.json").read_text() == "v1\n",
      "a basename repeated deeper in the tree does not capture the checkout root")

# `parts[:0] == ()` is true of every path, so one already-relative edit used to
# claim every absolute path in the session and reject the task.
t = a_tree_of({"README.md": "hello\n", "src/main.py": "x = 1\n"})
r = replay_edits(t, [
    {"turn": 1, "tool": "Edit", "args": {"file_path": "README.md",
                                         "old_string": "hello", "new_string": "hi"}},
    {"turn": 2, "tool": "Edit", "args": {"file_path": "/Users/d/code/w/src/main.py",
                                         "old_string": "x = 1", "new_string": "x = 2"}}], "a/w")
check(r.ok and (t / "src" / "main.py").read_text() == "x = 2\n",
      "a repo-relative path among absolute ones resolves, and so do they")

# Text mode translated CRLF and `errors="replace"` destroyed undecodable bytes,
# so a replayed file differed from the agent's in lines it never touched -- and
# the next edit, whose old_string still held the \r\n, then failed to match.
t = a_tree_of({"run.bat": b"@echo off\r\nset A=1\r\n", "l.txt": b"caf\xe9\nkeep\n"})
r = replay_edits(t, [
    {"turn": 1, "tool": "Edit", "args": {"file_path": "run.bat",
                                         "old_string": "set A=1", "new_string": "set A=9"}},
    {"turn": 2, "tool": "Edit", "args": {"file_path": "l.txt",
                                         "old_string": "keep", "new_string": "kept"}},
    {"turn": 3, "tool": "Edit", "args": {"file_path": "run.bat",
                                         "old_string": "off\r\nset A=9", "new_string": "done"}}],
    "a/w")
check(r.ok and (t / "run.bat").read_bytes() == b"@echo done\r\n"
      and (t / "l.txt").read_bytes() == b"caf\xe9\nkept\n",
      "replay changes only the bytes the edit names, CRLF and latin-1 included")

print("\n20. the developer's request is found wherever it is")
# The candidate sees every user message at or before the cut -- the excerpt
# squeezes tool traffic when it overruns and never drops a user prompt -- so a
# distance limit on finding the request rejects tasks whose request is plainly
# on the page. At 80 visible turns it lost five, whose requests sit 94 to 208
# turns back.
from errata_bench.build import last_user_message

far = [{"turn_number": 0, "turn_type": "user_prompt", "content": "the real request"}]
far += [{"turn_number": i, "turn_type": "tool_use", "content": "x"} for i in range(1, 300)]
check((last_user_message(far, 299) or {}).get("content") == "the real request",
      "a request 299 visible turns before the cut is still found")
check(last_user_message(far, 299, window=80) is None,
      "and the old 80-turn limit is what was losing it")
noise = [{"turn_number": i, "turn_type": "progress", "content": "x"} for i in range(200)]
noise.append({"turn_number": 200, "turn_type": "user_prompt", "content": "ask"})
check((last_user_message(noise, 250) or {}).get("content") == "ask",
      "progress rows the candidate never sees do not count as distance")
check(last_user_message([{"turn_number": 1, "turn_type": "assistant_response",
                          "content": "no user here"}], 5) is None,
      "and a session with no user message still returns nothing")

print("\n21. a screening gate is asked repeatedly and answered conservatively")
# Each gate is a model reading prose, and a model reading the same prose twice
# does not always answer the same way: five of forty-six scope rows changed
# across five askings, and re-screening one corpus produced fourteen tasks one
# time and thirteen the other. Asked repeatedly, a doubtful row is kept out.
from errata_bench.answerable import Answerable
from errata_bench.leakage import Leakage
from errata_bench.pipeline import _agree
from errata_bench.scope import Scope

# The models the three gates really return. An earlier version of this section
# invented its own `class Says` carrying a `.value`, and `_agree` reduced each
# answer with `bool(getattr(a, "value", a))`: against the fake that read the
# verdict, against every real gate it read the object and came out True. All
# three gates were constants, `build` rejected every row alive, and this
# section printed ALL CHECKS PASS throughout. So the fixtures here are the real
# models, and the first thing asserted is the absence of the attribute the bug
# relied on.
for model in (Answerable, Scope, Leakage):
    check(not hasattr(model, "value"),
          f"{model.__name__} has no .value -- a reader must name its field")

ANSWERABLE = (lambda v: Answerable(asks_for_something=v, request="r", reasoning=f"said {v}"),
              lambda x: x.asks_for_something)
SCOPE = (lambda v: Scope(within_scope=v, reason=f"said {v}"),
         lambda x: x.within_scope)
LEAK = (lambda v: Leakage(signals_trouble=v, quote="", reasoning=f"said {v}"),
        lambda x: x.signals_trouble)

def alternating(make, seq):
    it = iter(seq)
    async def ask(): return make(next(it))
    return ask

for (make, reading), seq, keep_on, want, label in (
    (ANSWERABLE, [True, True, True],   True,  True,  "unanimous yes is kept"),
    (ANSWERABLE, [True, False, True],  True,  False, "one no among yeses is refused"),
    (ANSWERABLE, [False, False],       True,  False, "unanimous no stays no"),
    (SCOPE,      [True, True, True],   True,  True,  "in scope every time is in scope"),
    (SCOPE,      [True, False, True],  True,  False, "one 'out of scope' is enough to refuse"),
    (LEAK,       [False, False, False], False, False, "unanimous 'no leak' is kept"),
    (LEAK,       [False, True, False], False, True,  "one reading of 'leaks' is enough to reject"),
):
    verdict, tally, _ = asyncio.run(
        _agree(alternating(make, seq), len(seq), keep_on=keep_on, reading=reading))
    check(verdict is want, f"{label}: {tally} -> {verdict}")

# The answer handed back has to be the one that explains the verdict. The leak
# gate's repair branch reads it, so handing back the last reading meant a
# conversation read leaking, clean, clean was filed as leaking and then never
# repaired -- and `build` drops a row that leaks and was not repaired.
make, reading = LEAK
verdict, tally, deciding = asyncio.run(
    _agree(alternating(make, [True, False, False]), 3, keep_on=False, reading=reading))
check(verdict is True and reading(deciding) is True,
      f"the reading handed back is the one that decided it: {tally} -> {verdict}")

make, reading = ANSWERABLE
verdict, tally, deciding = asyncio.run(
    _agree(alternating(make, [True, False, True]), 3, keep_on=True, reading=reading))
check(verdict is False and reading(deciding) is False,
      "and for a gate that must hold, it is the reading that broke it")

# A gate answering with the wrong shape stops the run. Without this the next
# such mistake is silent again.
class Wrong:
    pass

async def wrong_shape(): return Wrong()

try:
    asyncio.run(_agree(wrong_shape, 2, keep_on=True, reading=lambda x: x))
    check(False, "a gate answering with a non-bool raises")
except TypeError:
    check(True, "a gate answering with a non-bool raises")

make, reading = ANSWERABLE
verdict, tally, _ = asyncio.run(
    _agree(alternating(make, [True]), 1, keep_on=True, reading=reading))
check(verdict is True and tally == "1/1", "asking once still works, and says it asked once")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
