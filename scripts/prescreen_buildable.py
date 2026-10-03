"""Before screening, set aside the moments the build would refuse anyway (#16).

    .venv/bin/python scripts/prescreen_buildable.py runs/<run>            # write them
    .venv/bin/python scripts/prescreen_buildable.py runs/<run> --dry-run  # say what it would write
    .venv/bin/python scripts/prescreen_buildable.py runs/<run> --replay   # every signature row, screened or not,
                                                                          # against the build's own verdicts

Screening asks its gates over each whole conversation, at about $2 a moment on
long sessions. The build then refuses many of the moments screening passed, for
reasons visible as soon as the cut is chosen. On 10-02, 27 of the 59 rejections
among the 71 Entire moments were of this kind: the agent's git commands before
the cut, a sub-agent's unrecorded work, no commit before the session (6, all
false: G-86), an answer too short to test. With 10 more that screening refused but these checks also
fail, this sets aside 37 of the 71, whose screening cost $81.98 of $148.55.

This runs the build's checks that no screening verdict can change, in the
build's order (`construct.build.build`), on each signature row not yet
screened. For each that fails it writes a screened row: unusable, with the
build's own words for why. `stage_screen` then passes the moment by, and
`stage_build` rejects it with that reason. No model is called. Run it after
`--through signature` and before `--only screen`; it needs `ERRATA_CORPUS` set
to the corpus the run was drawn from. Its rows are done at any pass count:
`--passes 3` passes them by, as the build would refuse them whatever a
screening said (`stage_screen`; until 10-03 it screened them again).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AS = "set aside before screening, as the build would refuse it: "


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="say what it would write, and write nothing")
    ap.add_argument("--replay", action="store_true",
                    help="check every signature row against the build's verdicts in this run; write nothing")
    args = ap.parse_args(argv)

    from errata_bench.construct.build import checkouts_before, subagent_edits_before, unrecorded_subagents_before
    from errata_bench.construct.consistency import tree_changing_git
    from errata_bench.construct.edits import unreplayed_writes
    from errata_bench.corpus.recover import call_folders, foreign_transcript, has_transcript, recovered, start_of
    from errata_bench.corpus.session_time import session_starts
    from errata_bench.corpus.sessions import load_repos
    from errata_bench.corpus.turns import load_session_turns
    from errata_bench.spec import MIN_ORACLE_CHARS
    from errata_bench.store.rows import append, completed, key_of, load

    signatures = [r for r in load(args.run / "signatures.jsonl") if r.get("kind")]
    screened = args.run / "screened.jsonl"
    done = {key_of(r) for r in completed(screened)} if screened.exists() else set()
    todo = signatures if args.replay else [r for r in signatures if key_of(r) not in done]
    sessions = {r["session_id"] for r in todo}
    starts, repos = session_starts(sessions), load_repos()
    turns_by_session = recovered(load_session_turns(sessions))

    def refusal(row: dict) -> str | None:
        """The build's first refusal that no screening verdict changes, in its words, or None."""
        sid, cut = row["session_id"], row["cut"]
        turns = turns_by_session.get(sid) or []
        by_turn = {t.get("turn_number"): t for t in turns}
        stored = [t.get("turn_number") for t in turns]
        if len(stored) != len(set(stored)):
            return "the corpus holds rows of this session twice, under the same turn numbers"
        for field, what in (("failed", "failing"), ("resolved", "resolving")):
            turn = by_turn.get(row[field]) or {}
            if turn.get("turn_type") != "assistant_response":
                return f"the {what} turn is a {turn.get('turn_type') or 'missing turn'}, not an answer the agent wrote"
        oracle = (by_turn[row["failed"]].get("content") or "").strip()
        criterion = (by_turn[row["resolved"]].get("content") or "").strip()
        if len(oracle) < MIN_ORACLE_CHARS:
            return f"the failed answer is {len(oracle)} characters: too short to test against"
        if len(criterion) < MIN_ORACLE_CHARS:
            return f"the resolution is {len(criterion)} characters: too short to judge against"
        repo_id = row.get("repo_id") or ""
        if repos.get(repo_id) is None:
            return "repository not in the corpus"
        if starts.get(sid) is None:
            return "no turn in this session carries a timestamp, so its start is unknown"
        # The base needs the remote's history since 10-02 (G-86), so it is the
        # build's to choose, and not checked here.
        cwd, folders = start_of(sid)[1], call_folders(sid)
        git = tree_changing_git(turns, cut, cwd, folders)
        if git:
            return f"the agent changed its files with git before the cut, which no replay reproduces: {git[0][:90]}"
        unread = unreplayed_writes(turns, cut)
        if unread:
            return (f"the agent changed files before the cut with a tool the replay does not "
                    f"read: {', '.join(unread)[:90]}")
        by_subagent = subagent_edits_before(sid, turns, cut)
        if by_subagent:
            return (f"a sub-agent edited files before the cut, which the replay does not "
                    f"reproduce: {by_subagent[0][:90]}")
        unknown = unrecorded_subagents_before(sid, turns, cut)
        if unknown:
            return f"a sub-agent ran before the cut and left no record of what it did: {unknown[0][:60]}"
        elsewhere = checkouts_before(sid, turns, cut, cwd, folders)
        if elsewhere:
            return (f"the session worked in another checkout of the repository before the cut, so no "
                    f"one tree is its own: {elsewhere[0][-90:]}")
        if foreign_transcript(sid):
            return ("the session's transcript is not in Claude Code's format, so its calls "
                    "cannot be checked against the table")
        if not has_transcript(sid):
            return "the session has no transcript here, so the calls and text the table lost cannot be put back"
        return None

    verdicts = {key_of(r): refusal(r) for r in todo}
    set_aside = {k: v for k, v in verdicts.items() if v}
    why = Counter(v.split(":")[0] for v in set_aside.values())
    print(f"{args.run}: {len(todo)} signature rows checked; {len(set_aside)} the build would refuse whatever "
          f"screening says")
    for reason, n in why.most_common():
        print(f"  {n:4}  {reason}")

    if args.replay:
        # Against what the build decided in this run: a moment it built must not be set
        # aside, and one it refused for a reason visible before screening must be.
        structural = ("the corpus holds rows", "the failing turn is", "the resolving turn is", "the failed answer is",
                      "the resolution is", "repository not in the corpus", "no turn in this session carries",
                      "the agent changed its files with git",
                      "the agent changed files before the cut", "a sub-agent edited files",
                      "a sub-agent ran before the cut", "the session worked in another checkout",
                      "the session's transcript is not",
                      "the session has no transcript")
        # A task records its turn as `complaint_turn`, a rejection and a signature row as
        # `complaint`. Keyed on the wrong field, no built task matched any row and the
        # replay passed by comparing nothing, so every row must find its verdict.
        built = {(t.get("repo_id"), t.get("complaint_turn")) for t in load(args.run / "tasks.jsonl")}
        refused = {(r.get("repo_id"), r.get("complaint")): r.get("reason", "") for r in load(args.run / "rejections.jsonl")}
        at = {(r.get("repo_id"), r.get("complaint")): key_of(r) for r in todo}
        unmatched = [k for k in at if (k in built) == (k in refused)]
        if unmatched or len(at) != len(todo):
            print(f"replay: {len(unmatched)} of {len(at)} signature rows match no build verdict, or two; "
                  f"{len(todo) - len(at)} share a key: nothing compared", unmatched[:5])
            return 1
        wrongly = [k for k in at if k in built and set_aside.get(at[k])]
        should = [k for k in at if k in refused and refused[k].startswith(structural)]
        missed = [k for k in should if not set_aside.get(at[k])]
        also = [k for k in at if k in refused and not refused[k].startswith(structural) and set_aside.get(at[k])]
        print(f"replay: {len(built)} built, {len(wrongly)} of them set aside (must be 0); {len(should)} refused for a "
              f"reason visible before screening, {len(missed)} of them missed (must be 0); {len(also)} refused for a "
              f"screening reason that also fail a check here")
        for k in wrongly:
            print("  SET ASIDE BUT BUILT:", k, set_aside[at[k]][:120])
        for k in missed:
            print("  MISSED:", k, refused[k][:120])
        return 1 if wrongly or missed else 0

    if args.dry_run:
        return 0
    rows = {key_of(r): r for r in todo}
    for key, reason in set_aside.items():
        r = rows[key]
        # As a screened row reads to `stage_screen` (done: finished, with `text_recovered`,
        # at the default single pass) and to `stage_build` (unusable: rejected with `reason`).
        # `usage` is emptied: the signature row's own, copied, was priced a second time
        # under screened.jsonl (`harbor_spend.admitted`), and no call was made here.
        append(screened, {**r, "usable": False, "reason": AS + reason, "prescreened": True, "usage": {},
                          "screen_passes": 1, "text_recovered": has_transcript(r["session_id"])})
    print(f"wrote {len(set_aside)} rows to {screened}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
