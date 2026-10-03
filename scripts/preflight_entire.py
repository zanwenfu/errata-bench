#!/usr/bin/env python3
"""v1's round-3 preflight (research log 09-23), on an Entire run's built tasks, before any admission call is paid.

    ERRATA_CORPUS=data/entire/corpus python scripts/preflight_entire.py <run> [--corpus-scan]

No model calls. Checks, each as round 3 ran it on v1's grid:
- The overclaim control's invented name (`instrument.control`, OVERCLAIM's marker) is in no built task's
  conversation, as the controls read it (`control_conversations_for`), and in no raw transcript of its session.
  Where it is in the conversation, the control marks itself not applicable and the task is recorded as untestable
  and not admitted (D-29): said here, before its gate and controls are paid for. --corpus-scan also counts it over
  every row of the corpus, as round 3 did.
- Every accepted answer has the record its control is read against: the calls the agent made for it
  (`criterion_calls`). A task without them cannot pass its third control (D-29), and is named here.
- Every task was built from a conversation with its lost calls and text put back (`calls_recovered`,
  `text_recovered`), as the gates and the candidate read it.

Exit 1 if a task's session is not in the corpus (B-263: read as an empty conversation, it passed as clear), or a
task was built without its lost calls and text, which the build refuses: either is a fault to fix before the run
goes on. The rest are said and do not stop the run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from errata_bench.corpus.recover import transcript_path  # noqa: E402
from errata_bench.instrument.control import OVERCLAIM  # noqa: E402
from errata_bench.score.attempt import SessionNotInCorpus, control_conversations_for  # noqa: E402
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
    # Exactly what the controls are read against (`control_conversations_for`), which refuses a session the corpus
    # does not hold: read as an empty conversation it passed as clear (B-263's failure; review, 10-03).
    try:
        contexts = control_conversations_for(tasks)
    except SessionNotInCorpus as e:
        print(f"  refused: {e}")
        return 1
    bad = 0
    for t in tasks:
        shown = contexts[t.task_id]["cut"]
        raw = transcript_path(t.session_id)
        fatal, notes = [], []
        if marker in shown:
            # Not a stop: the control marks itself not applicable there, and the task is recorded as untestable
            # and not admitted (`instrument.control`). Stopped, the run could not go on, since the next build
            # makes the same task (review, 10-03).
            notes.append("the invented name is in its conversation: its overclaim control does not apply, so it "
                         "will not be admitted")
        if raw.exists() and marker in raw.read_text(errors="replace"):
            notes.append("the invented name is in its raw transcript, though not where the control reads")
        if not (getattr(t, "calls_recovered", False) and getattr(t, "text_recovered", False)):
            fatal.append("built without its lost calls and text put back")
        if not t.criterion_calls:
            notes.append("no calls behind its accepted answer (its third control cannot pass)")
        bad += bool(fatal)
        print(f"  {t.task_id}: {'; '.join(fatal + notes) if fatal or notes else 'clear'}")
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
