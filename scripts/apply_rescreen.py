#!/usr/bin/env python3
"""Apply a release's re-screen to it: each task kept, repaired again, or set aside (G-81, G-83, #17).

    scripts/apply_rescreen.py <release dir> <re-screen dir> [--passes 3] [--dry-run]

Run it on a copy of a release, never the release itself:

    cp -R release/v1.1 release/v1.1-screened
    scripts/apply_rescreen.py release/v1.1-screened runs/v11-rescreen

Each task of <release dir>/tasks/ is decided by its row in
<re-screen dir>/screened.jsonl (`scripts/rescreen_release.py`):

  kept        a request, the defect within its scope, and no leak, or a leak
              repaired and checked again. Its repair becomes the one its row
              records, none when nothing leaks: a v1 repair is undone where
              its leak is gone, and replaced where it is not.
  set aside   no request, the defect outside it, a leak not repaired, or a
              conversation the screening model would not read.

A task whose repair changes is rendered again (`rerender_release.rerender`). A
Harbor task is written again (`release.harbor.export`) whenever it no longer
gives the instruction this code builds for its task, whole: rendered again here,
cut to fit under a note since reworded, or left unwritten, or written only in
part, by a run that stopped (`release.harbor.stale`). A
task set aside is moved to <release dir>/set-aside/<task>, and its Harbor task
and export.json row are removed. Any of these leaves harbor/digests.json
describing other tasks, so it is removed: record the digests again (`python -m
errata_harbor.digests <release dir>/harbor`, in Harbor's environment), and run
trials only on that. Each run's decisions are added to manifest.json under
"rescreened" as each task is done, with the verdicts each was read from, so a
run stopped part way, or applied again, keeps the record of what an earlier one
changed: each decision is taken against the release as it was screened (its
row), not as an earlier run left it. A task's fingerprint changes with its
repair, so a judge is admitted after this, not before.

Refused, changing nothing, unless every task has one finished row, screened at
--passes or more, of the task as it now is (its fingerprint) and of the
conversation it now shows (its digest). Needs the corpus to render again, and
refused, changing nothing, unless each task to render has here the session data
its re-screen read (`corpus.recover.session_fingerprint`): from another copy of
the corpus, a task could show a conversation the leak check never read. Makes
no model calls.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.project import code_version  # noqa: E402
from errata_bench.release.harbor import stale  # noqa: E402
from errata_bench.spec import Task, fingerprint  # noqa: E402
from errata_bench.store import load  # noqa: E402

# What the decision was read from, kept with it in the manifest.
VERDICTS = ("asks_for_something", "asks_for_something_held", "request_reason", "within_scope", "within_scope_held",
            "scope_reason", "signals_trouble", "clean_held", "leak_reason", "leak_quote", "leak_carried_by",
            "redaction_outcome", "redaction_recheck_held", "redaction_worked", "redacted_turns", "rewritten_turns",
            "gated_after_repair", "provider_refused", "screen_passes", "screen_model", "code_version")


def decide(row: dict) -> tuple[str, str]:
    """Whether a screened task is kept or set aside, and why."""
    if row.get("provider_refused"):
        return "set aside", f"the screening model would not read its conversation: {row['provider_refused'][:160]}"
    if not row.get("asks_for_something"):
        return "set aside", (f"no request for the candidate to answer ({row.get('asks_for_something_held', '-')}): "
                             f"{str(row.get('request_reason') or '')[:200]}")
    if not row.get("within_scope"):
        return "set aside", (f"the defect is outside what was asked ({row.get('within_scope_held', '-')}): "
                             f"{str(row.get('scope_reason') or '')[:200]}")
    if row.get("signals_trouble") and not row.get("redaction_worked"):
        return "set aside", (f"its conversation already signals the trouble ({row.get('clean_held', '-')} clean), "
                             f"and it was not repaired: {str(row.get('redaction_outcome') or '')[:200]}")
    return "kept", "its leak repaired and checked again" if row.get("redaction_worked") else "no leak"


def repair_of(row: dict) -> tuple[list, dict]:
    """The repair a kept task's row records: none unless its leak was repaired."""
    if not row.get("redaction_worked"):
        return [], {}
    return list(row.get("redacted_turns") or []), dict(row.get("rewritten_turns") or {})


def problems_of(tasks: dict[str, tuple[Task, Path]], rows: list[dict], passes: int) -> list[str]:
    """Why the re-screen cannot be applied to these tasks as they now are; empty when it can."""
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r.get("task_id"), []).append(r)
    found = []
    for tid, (task, folder) in sorted(tasks.items()):
        mine = by_task.get(tid) or []
        if not mine:
            found.append(f"{tid}: not screened")
            continue
        if len(mine) > 1:
            found.append(f"{tid}: screened {len(mine)} times over; one row each is expected")
            continue
        row = mine[0]
        if row.get("error"):
            found.append(f"{tid}: its screening failed ({str(row['error'])[:100]}); run the re-screen again")
        elif "text_recovered" not in row:
            found.append(f"{tid}: screened before the gates read the candidate's view (G-81)")
        elif int(row.get("screen_passes") or 1) < passes:
            found.append(f"{tid}: screened {row.get('screen_passes') or 1} time(s), fewer than --passes {passes}")
        # As screened, with the repair the release held then: a task this has
        # already repaired again is the same task, so a run stopped part way
        # can be run again.
        released = (list(row.get("released_redacted_turns") or []), dict(row.get("released_rewritten_turns") or {}))
        as_screened = dataclasses.replace(task, redacted_turns=released[0], rewritten_turns=released[1])
        if row.get("task_fingerprint") != fingerprint(as_screened):
            found.append(f"{tid}: screened as another version of the task")
        elif ((sorted(task.redacted_turns), sorted((task.rewritten_turns or {}).items()))
              == (sorted(released[0]), sorted(released[1].items()))
              and row.get("conversation_sha256")
              != hashlib.sha256((folder / "conversation.txt").read_bytes()).hexdigest()):
            found.append(f"{tid}: screened while it showed another conversation")
    return found


def _rerender_module():
    spec = importlib.util.spec_from_file_location("rerender_release", Path(__file__).resolve().parent /
                                                  "rerender_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("rescreen", type=Path)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="say what would be done, and change nothing")
    args = ap.parse_args(argv)
    folders = sorted(d for d in (args.release / "tasks").iterdir() if (d / "grading" / "task.json").is_file())
    if not folders:
        ap.error(f"no frozen tasks under {args.release / 'tasks'}")
    screened = args.rescreen / "screened.jsonl"
    if not screened.is_file():
        ap.error(f"{screened} does not exist: run scripts/rescreen_release.py first")
    tasks = {d.name: (Task.from_json(json.loads((d / "grading" / "task.json").read_text())), d) for d in folders}
    rows = [r for r in load(screened) if r.get("task_id") in tasks]
    problems = problems_of(tasks, rows, args.passes)
    if problems:
        print(f"refused: {len(problems)} problem(s), nothing was changed:"
              + "".join(f"\n  - {p}" for p in problems[:12]) + ("\n  ..." if len(problems) > 12 else ""),
              file=sys.stderr)
        return 2
    row_of = {r["task_id"]: r for r in rows}
    plan, render = [], {}
    for tid, (task, folder) in sorted(tasks.items()):
        row = row_of[tid]
        decision, why = decide(row)
        removed, rewritten = repair_of(row)
        # Against the release as it was screened, which its row records, not the
        # task as it now is: a run stopped after rendering a task again, before
        # recording it, left the new repair on disk, and the next run recorded it
        # as kept, its v1 repair gone from the record (10-01 review).
        released = (list(row.get("released_redacted_turns") or []), dict(row.get("released_rewritten_turns") or {}))
        target = (sorted(removed), sorted(rewritten.items()))
        changes = decision == "kept" and (sorted(released[0]), sorted(released[1].items())) != target
        if decision == "kept" and changes:
            decision = "repaired again" if removed or rewritten else "repair undone"
        plan.append({"task_id": tid, "decision": decision, "why": why,
                     "redacted_turns": removed if decision != "set aside" else task.redacted_turns,
                     "rewritten_turns": rewritten if decision != "set aside" else task.rewritten_turns,
                     "released_redacted_turns": released[0], "released_rewritten_turns": released[1],
                     "verdicts": {k: row[k] for k in VERDICTS if k in row}})
        # Rendered only while the task does not show its new repair yet: one an
        # earlier run rendered is left as it is, and its record says which.
        on_disk = (sorted(task.redacted_turns), sorted((task.rewritten_turns or {}).items()))
        render[tid] = changes and on_disk != target
        if changes:
            plan[-1]["rendered"] = render[tid]
    harbor = args.release / "harbor"
    # Written again whenever it no longer gives the instruction this code builds:
    # re-rendered here, cut to fit under a note since reworded (v1.1's 17 long
    # tasks), or a run stopped before it was written (10-01 review).
    exporting = harbor.is_dir()
    for p in plan:
        if p["decision"] != "set aside" and exporting:
            p["export_stale"] = stale(tasks[p["task_id"]][1], harbor / p["task_id"])
    for p in plan:
        print(f"  {p['decision']:15} {p['task_id']:46} {p['why'][:110]}"
              + (" (rendered by an earlier run)" if p["decision"] in ("repaired again", "repair undone")
                 and not render[p["task_id"]] else "")
              + (" (its Harbor task is out of date)" if p.get("export_stale") else ""))
    counts = {d: sum(1 for p in plan if p["decision"] == d)
              for d in ("kept", "repaired again", "repair undone", "set aside")}
    print("  " + ", ".join(f"{n} {d}" for d, n in counts.items())
          + (f"; {sum(1 for p in plan if p.get('export_stale'))} Harbor tasks to write again" if exporting else ""))
    if args.dry_run:
        return 0

    from errata_bench.corpus.turns import load_session_turns
    from errata_bench.release.environment import workspace_contents
    from errata_bench.release.harbor import export
    from errata_bench.score.attempt import turns_of

    listing = harbor / "export.json"
    exported = json.loads(listing.read_text()) if listing.is_file() else {"tasks": []}
    module = _rerender_module()
    rerender = module.rerender
    redo = [p for p in plan if render[p["task_id"]]]
    loaded = load_session_turns({tasks[p["task_id"]][0].session_id for p in redo}) if redo else {}
    from errata_bench.corpus.recover import session_fingerprint

    # Rendered again only from the session data the re-screen read: from another
    # copy of the corpus, a task could show its candidate a conversation the
    # re-screen's leak check never read (10-01 review).
    elsewhere = []
    for p in redo:
        sid = tasks[p["task_id"]][0].session_id
        if row_of[p["task_id"]].get("session_sha256") != session_fingerprint(sid, loaded.get(sid) or []):
            elsewhere.append(p["task_id"])
    if elsewhere:
        print(f"refused: {len(elsewhere)} task(s) would be rendered again from other session data than their "
              f"re-screen read; apply it where the re-screen ran. Nothing was changed:"
              + "".join(f"\n  - {t}" for t in elsewhere[:12]) + ("\n  ..." if len(elsewhere) > 12 else ""),
              file=sys.stderr)
        return 2
    now, version = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), code_version()
    # Each task recorded as it is done, so a run stopped part way keeps the record
    # of what it changed: one recorded at the end lost it (10-01 review).
    manifest_path = args.release / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    run = {"at": now, "code_version": version, "from": str(args.rescreen),
           "screened_sha256": hashlib.sha256(screened.read_bytes()).hexdigest(), "passes": args.passes,
           "complete": False, "tasks": []}
    manifest.setdefault("rescreened", {"runs": []})["runs"].append(run)
    # On record from its start: a run stopped before its first task was recorded
    # left no trace that it had begun, or changed anything (10-01 review).
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")

    def record(p: dict) -> None:
        run["tasks"].append(p)
        manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")

    def changing() -> None:
        # The digests describe the tasks as they were: gone at the first change,
        # so a run stopped after it leaves none behind that no longer hold.
        if (harbor / "digests.json").is_file():
            (harbor / "digests.json").unlink()
            print(f"  {harbor / 'digests.json'} removed: it described tasks that have changed; record the digests "
                  f"again (python -m errata_harbor.digests {harbor}, in Harbor's environment)")

    def save_listing() -> None:
        if exporting:
            exported["tasks"] = sorted(exported["tasks"], key=lambda r: r["task_id"])
            exported["rescreened_at"], exported["rescreened_code_version"] = now, version
            listing.write_text(json.dumps(exported, indent=1) + "\n")

    failed = []
    aside = args.release / "set-aside"
    for p in plan:
        task, folder = tasks[p["task_id"]]
        if p["decision"] == "set aside":
            changing()
            # Recorded before it is moved: stopped between the two, the next run
            # finds it still there and sets it aside again, where a task moved
            # first and recorded after could leave no record of why.
            record(p)
            aside.mkdir(exist_ok=True)
            shutil.move(str(folder), str(aside / p["task_id"]))
            if (harbor / p["task_id"]).exists():
                shutil.rmtree(harbor / p["task_id"])
            exported["tasks"] = [r for r in exported["tasks"] if r["task_id"] != p["task_id"]]
            save_listing()
            continue
        if render[p["task_id"]]:
            changing()
            new = dataclasses.replace(task, redacted_turns=p["redacted_turns"], rewritten_turns=p["rewritten_turns"])
            left = "not rendered again, left as it was"
            try:
                ok, why = rerender(new, turns_of(loaded, new), folder)
            except getattr(module, "PartlyRendered", ()) as e:
                ok, why, left = False, str(e), "rendered only in part, so make the copy again"
            except Exception as e:  # noqa: BLE001 - this task refused and left as it was; the others go on
                ok, why = False, f"{type(e).__name__}: {e}"
            if not ok:
                failed.append(f"{p['task_id']}: {why}")
                p["decision"], p["why"], p["rendered"] = "refused", f"{left}: {why}", False
                record(p)
                continue
        if exporting and (p.get("export_stale") or stale(folder, harbor / p["task_id"])):
            changing()
            try:
                row = export(folder, harbor / p["task_id"], *workspace_contents(folder / "workspace.tar.gz"))
            except Exception as e:  # noqa: BLE001 - recorded; run again to write it
                failed.append(f"{p['task_id']}: its Harbor task could not be written: {type(e).__name__}: {e}")
                p["exported"] = f"failed: {type(e).__name__}: {e}"
                record(p)
                continue
            exported["tasks"] = [r for r in exported["tasks"] if r["task_id"] != p["task_id"]] + [row]
            p["exported"] = True
            save_listing()
        record(p)
    run["complete"] = True
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(plan) - len(failed)} of {len(plan)} decisions applied to {args.release}; recorded in "
          f"{manifest_path}" + "".join(f"\n  refused: {f}" for f in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
