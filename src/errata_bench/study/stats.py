"""The study's intervals: a share and its 95% interval, resampling whole sessions.

Moments from one session are not independent (the same developer, the same
agent, the same work), so the interval resamples sessions, not moments. The
seed is fixed, so a tally gives the same interval every time it is run.
"""

from __future__ import annotations

import random
from collections import defaultdict


def share(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def bootstrap(items: list[tuple[str, bool]], *, n: int = 2000, seed: int = 20261003) -> dict:
    """The share of True among (session, outcome) pairs, with a 95% interval from resampling sessions.

    Returns {"share", "low", "high", "n", "sessions"}; share, low and high are
    None when there is nothing to count.
    """
    by: dict[str, list[bool]] = defaultdict(list)
    for session, ok in items:
        by[session].append(bool(ok))
    sessions = sorted(by)
    total = sum(len(v) for v in by.values())
    hits = sum(sum(v) for v in by.values())
    out = {"share": share(hits, total), "low": None, "high": None, "n": total, "sessions": len(sessions)}
    if not total or len(sessions) < 2:
        return out
    rng = random.Random(seed)
    shares = []
    for _ in range(n):
        h = t = 0
        for s in (sessions[rng.randrange(len(sessions))] for _ in sessions):
            h += sum(by[s])
            t += len(by[s])
        if t:
            shares.append(h / t)
    shares.sort()
    out["low"] = round(shares[int(0.025 * len(shares))], 3)
    out["high"] = round(shares[min(len(shares) - 1, int(0.975 * len(shares)))], 3)
    return out


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two raters' labels on the same items: 1 is perfect, 0 is chance."""
    if not pairs:
        return None
    labels = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    total = len(pairs)
    observed = sum(a == b for a, b in pairs) / total
    expected = sum((sum(a == x for a, _ in pairs) / total) * (sum(b == x for _, b in pairs) / total) for x in labels)
    if expected == 1:
        return 1.0
    return round((observed - expected) / (1 - expected), 3)
