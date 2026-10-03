#!/usr/bin/env python3
"""v1's round-3 preflight (research log 09-23), on an Entire run's built tasks, before any admission call is paid.

    ERRATA_CORPUS=data/entire/corpus python scripts/preflight_entire.py <run> [--corpus-scan]

No model calls. Checks, each as round 3 ran it on v1's grid:
- The overclaim control's invented name (`instrument.control`, OVERCLAIM's marker) is in no built task's
  conversation and in no raw transcript of its session. Where it is, the control is marked not applicable and the
  task cannot be admitted (D-29), so it is found here, before the gate and the controls are paid for.
  --corpus-scan also counts it over every row of the corpus, as round 3 did.
- Every accepted answer has the record its control is read against: the calls the agent made for it
  (`criterion_calls`). A task without them cannot pass its third control (D-29), and is named here.
- Every task was built from a conversation with its lost calls and text put back (`calls_recovered`,
  `text_recovered`), as the gates and the candidate read it.

Exit 1 if any task fails the first or the third check: they are to be read before the run goes on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from errata_bench.corpus.recover import recovered, transcript_path  # noqa: E402
from errata_bench.corpus.turns import load_session_turns  # noqa: E402
from errata_bench.instrument.control import OVERCLAIM  # noqa: E402
from errata_bench.score.attempt import transcript_for  # noqa: E402
from errata_bench.spec import read  # noqa: E402
from errata_bench.store import Paths  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--corpus-scan", action="store_true", help="count the invented name over every corpus row too")
    args = ap.parse_args(argv)
    marker = OVERCLAIM.marker
    if not marker:
        print("refused: the overclaim control names no invented file")
        return 2
    tasks = read(Paths(args.run).tasks)
    print(f"{args.run.name}: {len(tasks)} built tasks; the overclaim's invented name is {marker!r}")
    turns = recovered(load_session_turns({t.session_id for t in tasks}))
    bad = 0
    for t in tasks:
        shown = transcript_for(t, turns.get(t.session_id, []))
        raw = transcript_path(t.session_id)
        in_raw = raw.exists() and marker in raw.read_text(errors="replace")
        problems = []
        if marker in shown:
            problems.append("the invented name is in its conversation")
        if in_raw:
            problems.append("the invented name is in its raw transcript")
        if not t.criterion_calls:
            problems.append("no calls behind its accepted answer (its third control cannot pass)")
        if not (getattr(t, "calls_recovered", False) and getattr(t, "text_recovered", False)):
            problems.append("built without its lost calls and text put back")
        bad += bool(problems and (marker in shown or in_raw
                                  or not (getattr(t, "calls_recovered", False) and getattr(t, "text_recovered", False))))
        print(f"  {t.task_id}: {'; '.join(problems) if problems else 'clear'}")
    if args.corpus_scan:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        from errata_bench.corpus.sessions import CORPUS

        hits = 0
        for batch in pq.ParquetFile(CORPUS / "conversations.parquet").iter_batches(batch_size=200_000,
                                                                                   columns=["content"]):
            hits += pc.sum(pc.fill_null(pc.match_substring(batch.column("content"), marker), False)).as_py() or 0
        print(f"  corpus rows holding the invented name: {hits}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
