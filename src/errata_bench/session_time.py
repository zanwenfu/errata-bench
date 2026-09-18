"""When a session actually started.

``sessions.created_at`` is not the session start. Measured against the
timestamps of each session's own turns, across the eighteen sessions that
produced tasks, it falls after the last turn in twelve of them and mid-session in
the other six. It is never earlier than the first turn. It is a completion
timestamp.

That matters because the candidate's repository is built from "the last commit
before the session started". Using a completion timestamp selects the last
commit before the session *ended*, which includes every commit the agent made
during the session -- sometimes the fix the candidate is supposed to arrive at on
its own. One task was handed a tree already containing its own resolution and
passed three attempts out of three.

So the start is read from the turns: the earliest timestamp any turn in the
session carries. That is a fact about the conversation rather than a metadata
field whose meaning has to be guessed.
"""

from __future__ import annotations


def session_starts(session_ids: set[str]) -> dict[str, int]:
    """The earliest turn timestamp in each session, in nanoseconds.

    Sessions with no usable timestamp are absent from the result rather than
    given a default. A task whose start cannot be established has no defensible
    base commit, and guessing one reintroduces exactly the failure this module
    exists to remove.
    """
    import pyarrow.parquet as pq

    from .corpus import CORPUS

    earliest: dict[str, int] = {}
    parquet = pq.ParquetFile(CORPUS / "conversations.parquet")
    for batch in parquet.iter_batches(
        batch_size=200_000, columns=["session_id", "timestamp"]
    ):
        sessions = batch.column("session_id").to_pylist()
        stamps = batch.column("timestamp").to_pylist()
        for i, session in enumerate(sessions):
            if session not in session_ids:
                continue
            stamp = stamps[i]
            if stamp is None:
                continue
            ns = int(stamp.timestamp() * 1_000_000_000)
            if session not in earliest or ns < earliest[session]:
                earliest[session] = ns
    return earliest
