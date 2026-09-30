#!/usr/bin/env python3
"""Render a frozen release's conversations again, its working copies untouched (G-79, #17).

    scripts/rerender_release.py <release dir> [--text-recovered] [--only <task id>...]

Run it on a copy of a release, never the release itself:

    cp -Rc release/v1 release/v1.1
    scripts/rerender_release.py release/v1.1 --text-recovered

A frozen task's working copy is the repository with the session's edits
replayed, and the words around those edits change none of them. So a task is
not frozen again, which would fetch every repository from GitHub (the build has
lost tasks to deleted repositories and force-pushes); only what is rendered from
the corpus is written again. For each task under <release dir>/tasks/, the task
row is read from its own grading/task.json -- the release's record, not a run
directory's -- and set to show the agent's text SWE-chat's table lost when
--text-recovered is given. Then:

  conversation.txt        rendered again (`transcript_for`);
  shown_turns.json        written again, and checked to render it exactly;
  grading/controls.json   the controls' conversations, rendered again;
  grading/turns.json      the turns to the resolution, with what was put back;
  grading/task.json       the task row, with its new flag;
  task.json               its fingerprint and the two digests.

workspace.tar.gz and grading/references.json are not touched. A task whose
replayed edits would change is refused and left as it was. The manifest records
when and by which code. Needs the corpus, not GitHub; makes no model calls.
The exit code is 0 only if every task was written and checked.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from errata_bench.construct.edits import edits_before  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402
from errata_bench.project import code_version  # noqa: E402
from errata_bench.release.freeze import controls_of, write_shown_turns  # noqa: E402
from errata_bench.score.attempt import transcript_for, turns_of, with_lost_blocks  # noqa: E402
from errata_bench.spec import Task, fingerprint  # noqa: E402


def rerender(task: Task, turns: list[dict], folder: Path) -> tuple[bool, str]:
    """Write one frozen task's conversation files again; whether it was written and checked, and why not."""
    before = edits_before(with_lost_blocks(dataclasses.replace(task, text_recovered=False), turns), task.cut_turn)
    full = with_lost_blocks(task, turns)
    if edits_before(full, task.cut_turn) != before:
        return False, "its replayed edits would change, so its working copy would no longer match"
    (folder / "conversation.txt").write_bytes(transcript_for(task, turns).encode("utf-8"))
    if not write_shown_turns(task, turns, folder):
        return False, "the turns kept do not render the conversation the candidate is shown"
    grading = folder / "grading"
    for name, body in (("controls.json", controls_of(task, turns)), ("task.json", task.to_json()),
                       ("turns.json", [t for t in full
                                       if (t.get("turn_number") or 0) <= (task.resolved_turn or task.cut_turn)])):
        (grading / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    meta = json.loads((folder / "task.json").read_text())
    meta["fingerprint"] = fingerprint(task)
    meta["conversation_sha256"] = hashlib.sha256((folder / "conversation.txt").read_bytes()).hexdigest()
    meta["shown_turns_sha256"] = hashlib.sha256((folder / "shown_turns.json").read_bytes()).hexdigest()
    (folder / "task.json").write_text(json.dumps(meta, indent=1) + "\n")
    return True, ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("release", type=Path)
    ap.add_argument("--text-recovered", action="store_true",
                    help="show each task's candidate the agent's text SWE-chat's table lost (G-79)")
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args(argv)
    folders = sorted(d for d in (args.release / "tasks").iterdir() if (d / "grading" / "task.json").is_file())
    if args.only:
        folders = [d for d in folders if d.name in set(args.only)]
    if not folders:
        ap.error(f"no frozen tasks under {args.release / 'tasks'}")
    tasks = {d.name: Task.from_json(json.loads((d / "grading" / "task.json").read_text())) for d in folders}
    if args.text_recovered:
        tasks = {k: dataclasses.replace(t, text_recovered=True) for k, t in tasks.items()}
    loaded = load_session_turns({t.session_id for t in tasks.values()})
    bad, rows = 0, []
    for d in folders:
        task = tasks[d.name]
        shown_before = (d / "conversation.txt").read_bytes()
        ok, why = rerender(task, turns_of(loaded, task), d)
        shown = json.loads((d / "shown_turns.json").read_text()) if ok else []
        put_back = sum(1 for t in shown if t.get("recovered") and t.get("turn_type") == "assistant_response")
        grew = len((d / "conversation.txt").read_bytes()) - len(shown_before) if ok else 0
        rows.append({"task_id": d.name, "ok": ok, "text_put_back": put_back, "characters_added": grew,
                     **({"reason": why} if not ok else {})})
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {d.name:42} {put_back:4} texts put back, {grew:+8,} characters"
              + ("" if ok else f"\n       {why}"))
    manifest_path = args.release / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest["rerendered"] = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_version": code_version(),
        "text_recovered": args.text_recovered,
        "tasks": sorted(rows + [r for r in (manifest.get("rerendered") or {}).get("tasks", [])
                                if r["task_id"] not in {x["task_id"] for x in rows}], key=lambda r: r["task_id"])}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(folders) - bad} of {len(folders)} tasks written and checked in {args.release / 'tasks'}; "
          f"{sum(r['text_put_back'] for r in rows)} texts put back, {sum(r['characters_added'] for r in rows):,} "
          f"characters added")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
