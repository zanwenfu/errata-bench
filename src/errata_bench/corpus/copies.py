"""One moment in several sessions: copies of a conversation (G-91).

A conversation can sit in several sessions of a corpus. Claude Code writes it
into a new session when it is resumed or forked, or carried on after it was
compacted, and Entire records each session it saw, sometimes as a snapshot
taken before the conversation ended and again after. So can each of its
messages: 2,075 of the collected corpus's developer rows (2.2%), in 129
sessions, are held by another session too, and 1,638 of SWE-chat's (3.4% of
those with a time), in 54 (measured 10-02).

A copy keeps the message's text and its time to the millisecond, and gives it
a new uuid. Nothing else the developer says in two sessions shares both, so
the two together are the message's identity. Text alone is not: the same
command or template, sent again on another day, is a different message. The
copies were labelled apart, and 57 of the 950 messages the collected corpus
holds twice or more were called pushback in one copy and not in another.

Read twice, one moment is paid for twice at every stage and can be built into
two tasks. The first two runs on the collected corpus held 9 moments twice
each, and two of their 16 tasks (bertrandvidal-sound-map-61 and -62) were one
moment.
"""

from __future__ import annotations

import hashlib
from collections import Counter

COPY = "a copy of a moment another session holds, the copy kept there"
TAKEN = "a copy of a moment already collected"


def identity(timestamp, content) -> tuple | None:
    """A message's identity across the copies of its conversation: its time and its text. None without either."""
    text = (content or "").strip()
    if timestamp is None or not text:
        return None
    return (str(timestamp), hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16])


def identities(conversations, keys: set[tuple[str, int]]) -> dict[tuple[str, int], tuple]:
    """For each (session, turn) of ``keys``: its row's identity, its session's first time and its session's last turn.

    One pass over ``conversations`` (a `pyarrow.parquet.ParquetFile`), reading
    the text of the named sessions' rows only.
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    keys = {(s, n) for s, n in keys if isinstance(s, str) and n is not None}   # a row with no session names none
    sessions = sorted({s for s, _ in keys})
    if not sessions:
        return {}
    first: dict[str, object] = {}
    last: dict[str, int] = {}
    said: dict[tuple[str, int], tuple] = {}
    wanted = turns_wanted = None
    for batch in conversations.iter_batches(batch_size=200_000,
                                            columns=["session_id", "turn_number", "timestamp", "content"]):
        ids = batch.column("session_id")
        if wanted is None:
            wanted = pa.array(sessions, type=ids.type)
            turns_wanted = pa.array(sorted({n for _, n in keys}), type=batch.column("turn_number").type)
        mask = pc.is_in(ids, value_set=wanted)
        if not pc.any(mask).as_py():
            continue
        rows = batch.filter(mask)
        # Every row's time and number, for where its session starts and ends;
        # the text only of rows at a turn some key names.
        for s, n, ts in zip(*(rows.column(c).to_pylist() for c in ("session_id", "turn_number", "timestamp"))):
            if ts is not None and (s not in first or ts < first[s]):
                first[s] = ts
            if n is not None and n > last.get(s, n - 1):
                last[s] = n
        named = rows.filter(pc.is_in(rows.column("turn_number"), value_set=turns_wanted))
        for s, n, ts, text in zip(*(named.column(c).to_pylist() for c in ("session_id", "turn_number", "timestamp",
                                                                             "content"))):
            if (s, n) in keys:
                said[(s, n)] = identity(ts, text)
    return {k: (said[k], first.get(k[0]), last.get(k[0], k[1])) for k in said}


def keep_one(moments: list[dict], found: dict[tuple[str, int], tuple], taken: set) -> tuple[list[dict], Counter]:
    """``moments`` with one copy of each kept, and what was left out and why.

    A moment whose identity an earlier collection took (``taken``) is left out
    in every session. Of the copies in ``moments``, the one kept is in the
    session that starts first: it holds the most of the conversation before
    the moment, so the conversation shown and the edits replayed go back
    furthest. Among copies whose sessions start together, snapshots of one
    conversation, it is the one with the most turns after the moment, where
    its resolution is looked for; then the lowest session id. A moment with
    no identity (no time, or no text) is kept: it cannot be matched.
    """
    def ident(m):
        return (found.get((m["session_id"], m["turn_number"])) or (None, None, None))[0]

    def rank(m):
        _, start, end = found[(m["session_id"], m["turn_number"])]
        # Two starts are compared only when neither is None: the first place decides otherwise.
        return (start is None, start, -((end or 0) - m["turn_number"]), m["session_id"])

    left_out: Counter = Counter()
    groups: dict[tuple, list[dict]] = {}
    for m in moments:
        if ident(m) is not None:
            groups.setdefault(ident(m), []).append(m)
    keep = {id(min(group, key=rank)) for key, group in groups.items() if key not in taken}
    out = []
    for m in moments:
        key = ident(m)
        if key is None:
            out.append(m)
        elif key in taken:
            left_out[TAKEN] += 1
        elif id(m) in keep:
            out.append(m)
        else:
            left_out[COPY] += 1
    return out, left_out
