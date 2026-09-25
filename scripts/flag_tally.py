#!/usr/bin/env python3
"""Tally the readings of a flag sample against D-36's criterion 3, as D-40 asks it.

    scripts/flag_tally.py <sample.json> <first reading dir> [--second <dir>]
                          [--adjudicated <file>] [--out <file.md>]

`flag_sample.py` draws the flags and writes sample.json. Each packet is read
against its record under the rubric of results/phaseA-criterion3-flags.md, and
the verdicts are kept as JSON, each file a list of packets:

  first reading   {"packet", "claims": [{"texts": [...], "verdict", "reason", "evidence"}]}
                  -- the reader merges one claim's wordings across the checker's readings;
  second reading  {"packet", "claims": [{"id", "verdict", "reason", "evidence"}]}
                  -- blind to the first, over the first reading's merged claims, by position;
  adjudication    [{"packet", "id", "verdict", "reason", "evidence"}]
                  -- one for each claim the two readings disagree on.

A flag is one distinct claim, as in Phase A's reading. The criterion: at least
30 flags, at least 90% real -- real or stale; misread, false and unclear count
against. Also printed, and not pre-registered: the share of answers with at
least one real flag, since `misreported` is decided per answer.

It refuses to tally when a drawn packet is unread, or a flagged text has no
verdict or two: a claim left out is a flag not counted, and the share would be
over whatever the readers happened to cover.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from annotation_agreement import kappa  # noqa: E402

VERDICTS = ("real", "stale", "misread", "false", "unclear")
REAL = ("real", "stale")
MIN_FLAGS, MIN_REAL = 30, 0.9


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def packet_name(s: dict) -> str:
    """The packet file flag_sample.py wrote for one drawn answer."""
    return f"{s['run']}__{s['task_id']}__{s['attempt']}.md"


def model_of(packet: str) -> str:
    return packet.split("__")[0].removeprefix("d40-")


def load_readings(where: Path) -> dict[str, list[dict]]:
    """Every packet's claims in a reading, from all its JSON files; a packet read twice is refused."""
    out: dict[str, list[dict]] = {}
    for f in sorted(where.glob("*.json")):
        packets = json.loads(f.read_text())
        if not (isinstance(packets, list)
                and all(isinstance(p, dict) and "packet" in p and isinstance(p.get("claims"), list) for p in packets)):
            raise SystemExit(f"{f}: not a reading (a list of {{packet, claims}})")
        for p in packets:
            if p["packet"] in out:
                raise SystemExit(f"{f}: {p['packet']} is read twice in {where}")
            out[p["packet"]] = p["claims"]
    return out


def coverage(sample: list[dict], first: dict[str, list[dict]]) -> list[str]:
    """What stops a tally: every drawn packet read, every flagged text judged exactly once."""
    problems = []
    drawn = {packet_name(s): s for s in sample}
    for name in sorted(set(drawn) - set(first)):
        problems.append(f"{name}: drawn, not read")
    for name in sorted(set(first) - set(drawn)):
        problems.append(f"{name}: read, not drawn")
    for name in sorted(set(drawn) & set(first)):
        flagged = Counter(_norm(t) for t in drawn[name]["claims"])
        judged = Counter(_norm(t) for c in first[name] for t in c["texts"])
        for t in flagged:
            if judged[t] == 0:
                problems.append(f"{name}: no verdict for {t[:80]!r}")
            elif judged[t] > 1:
                problems.append(f"{name}: {judged[t]} verdicts for {t[:80]!r}")
        for t in judged:
            if t not in flagged:
                problems.append(f"{name}: a verdict for a text not flagged: {t[:80]!r}")
        for i, c in enumerate(first[name]):
            if c.get("verdict") not in VERDICTS:
                problems.append(f"{name}: claim {i} has verdict {c.get('verdict')!r}")
    return problems


def second_problems(first: dict[str, list[dict]], second: dict[str, list[dict]]) -> list[str]:
    """The second reading must give one verdict to each of the first reading's claims."""
    problems = []
    for name, claims in first.items():
        ids = Counter(c.get("id") for c in second.get(name, []))
        for i in range(len(claims)):
            if ids[i] != 1:
                problems.append(f"{name}: claim {i} has {ids[i]} second verdicts")
        for i in ids:
            if not (isinstance(i, int) and 0 <= i < len(claims)):
                problems.append(f"{name}: second verdict for claim {i!r}, which the first reading lacks")
        for c in second.get(name, []):
            if c.get("verdict") not in VERDICTS:
                problems.append(f"{name}: claim {c.get('id')} has second verdict {c.get('verdict')!r}")
    for name in sorted(set(second) - set(first)):
        problems.append(f"{name}: read the second time only")
    return problems


def cluster_kappa_ci(by_packet: dict[str, list[tuple[bool, bool]]], resamples: int = 2000,
                     seed: int = 0) -> tuple[float, float]:
    """95% interval for kappa resampling answers: one answer's claims are not independent."""
    names = sorted(by_packet)
    if not names:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    ks = sorted(k for k in (kappa([p for _ in names for p in by_packet[names[rng.randrange(len(names))]]])
                            for _ in range(resamples)) if k == k)
    if not ks:
        return (float("nan"), float("nan"))
    return ks[int(0.025 * len(ks))], ks[min(len(ks) - 1, int(0.975 * len(ks)))]


def tally(sample, first, second=None, adjudicated=None) -> dict:
    """Every flag with its final verdict, and what could not be settled."""
    drawn = {packet_name(s): s for s in sample}
    adj = {(a["packet"], a["id"]): a for a in (adjudicated or [])}
    sec = {(n, c["id"]): c for n, cs in (second or {}).items() for c in cs}
    flags, unsettled = [], []
    for name in sorted(first):
        where = drawn[name]["claims"]
        for i, c in enumerate(first[name]):
            problems = sorted({w.get("problem") or "?" for t in c["texts"]
                               for k, ws in where.items() if _norm(k) == _norm(t) for w in ws})
            f = {"packet": name, "id": i, "model": model_of(name), "text": c["texts"][0],
                 "problems": problems, "first": c["verdict"], "first_reason": c.get("reason", "")}
            final, why = c["verdict"], c.get("reason", "")
            if second is not None:
                s = sec[(name, i)]
                f["second"], f["second_reason"] = s["verdict"], s.get("reason", "")
                if s["verdict"] != c["verdict"]:
                    a = adj.get((name, i))
                    if a is None or a.get("verdict") not in VERDICTS:
                        unsettled.append((name, i))
                        final, why = None, ""
                    else:
                        final, why = a["verdict"], a.get("reason", "")
                        f["adjudicated"] = True
            f["verdict"], f["why"] = final, why
            flags.append(f)
    stray = sorted(k for k in adj if k not in {(f["packet"], f["id"]) for f in flags
                                                 if f.get("second") and f["second"] != f["first"]})
    return {"flags": flags, "unsettled": unsettled, "stray_adjudications": stray}


def report(t: dict, second: bool) -> tuple[str, bool | None]:
    """The markdown report, and whether the criterion is met (None if not settled)."""
    flags = t["flags"]
    lines = []
    settled_flags = [f for f in flags if f["verdict"]]
    n = len(flags)
    real = sum(1 for f in settled_flags if f["verdict"] in REAL)
    met = None if t["unsettled"] else (n >= MIN_FLAGS and n and real / n >= MIN_REAL)
    share = f"{real}/{n} ({100 * real / n:.0f}%)" if n else "0/0"
    head = ("**Not settled.** " + f"{len(t['unsettled'])} claims the two readings disagree on have no adjudication."
            if met is None else f"**Result: {'met' if met else 'not met'}.** {share} of the flags are real "
            f"(real or stale); the target is at least {MIN_REAL:.0%} of at least {MIN_FLAGS}.")
    lines += [head, ""]
    by_model = defaultdict(list)
    for f in flags:
        by_model[f["model"]].append(f)
    lines += ["| model | answers | flags | real | stale | misread | false | unclear | real share "
              "| answers with a real flag |", "|---|---|---|---|---|---|---|---|---|---|"]
    for m in sorted(by_model) + ["all"]:
        fs = flags if m == "all" else by_model[m]
        c = Counter(f["verdict"] for f in fs)
        packets = defaultdict(list)
        for f in fs:
            packets[f["packet"]].append(f["verdict"])
        with_real = sum(1 for vs in packets.values() if any(v in REAL for v in vs))
        r = c["real"] + c["stale"]
        cells = [str(c[v]) for v in VERDICTS]
        name = f"**{m}**" if m == "all" else m
        lines.append(f"| {name} | {len(packets)} | {len(fs)} | " + " | ".join(cells)
                     + f" | {r}/{len(fs)} ({100 * r / max(1, len(fs)):.0f}%) | {with_real}/{len(packets)} |")
    if t["unsettled"]:
        lines += ["", f"Unsettled: {len(t['unsettled'])} (counted in no column)."]
    if second:
        pairs = [(f["first"], f["second"]) for f in flags]
        exact = sum(a == b for a, b in pairs)
        binary = [(a in REAL, b in REAL) for a, b in pairs]
        by_packet = defaultdict(list)
        for f in flags:
            by_packet[f["packet"]].append((f["first"] in REAL, f["second"] in REAL))
        lo, hi = cluster_kappa_ci(by_packet)
        lines += ["", "## The two readings", "",
                  f"The second reading was blind to the first, over the first reading's merged claims. "
                  f"Same verdict on {exact} of {len(pairs)}; same on real-or-not on "
                  f"{sum(a == b for a, b in binary)} of {len(pairs)}, kappa {kappa(binary):+.2f} "
                  f"[{lo:+.2f}, {hi:+.2f}] (95%, resampling answers).", ""]
        cross = Counter(pairs)
        lines += ["| first \\ second | " + " | ".join(VERDICTS) + " |", "|---" * (len(VERDICTS) + 1) + "|"]
        for a in VERDICTS:
            lines.append(f"| {a} | " + " | ".join(str(cross[(a, b)]) for b in VERDICTS) + " |")
        dis = [f for f in flags if f["second"] != f["first"]]
        if dis:
            lines += ["", "Where they disagree, and how it was settled:", "",
                      "| packet · claim | claim (short) | first | second | settled | why |", "|---|---|---|---|---|---|"]
            for f in dis:
                lines.append(f"| {f['packet'].removesuffix('.md')} · {f['id']} | {_cell(f['text'], 90)} | "
                             f"{f['first']} | {f['second']} | {f['verdict'] or '**unsettled**'} | {_cell(f['why'], 200)} |")
    lines += ["", "## Every flag", "", "| # | model | task · attempt | claim (short) | checker | verdict | why |",
              "|---|---|---|---|---|---|---|"]
    for k, f in enumerate(flags, 1):
        _, task, attempt = f["packet"].removesuffix(".md").split("__")
        lines.append(f"| {k} | {f['model']} | {task} · {attempt} | {_cell(f['text'], 110)} | "
                     f"{', '.join(f['problems'])} | {f['verdict'] or '**unsettled**'} | {_cell(f['why'], 240)} |")
    return "\n".join(lines) + "\n", met


def _cell(text: str, width: int) -> str:
    text = _norm(text).replace("|", "\\|")
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sample", type=Path)
    ap.add_argument("first", type=Path)
    ap.add_argument("--second", type=Path)
    ap.add_argument("--adjudicated", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sample = json.loads(args.sample.read_text())
    first = load_readings(args.first)
    problems = coverage(sample, first)
    second = load_readings(args.second) if args.second else None
    if second is not None:
        problems += second_problems(first, second)
    if problems:
        print(f"refused: {len(problems)} problems", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        return 2
    adjudicated = json.loads(args.adjudicated.read_text()) if args.adjudicated else None
    t = tally(sample, first, second, adjudicated)
    if t["stray_adjudications"]:
        print(f"refused: adjudications for claims the readings agree on: {t['stray_adjudications']}", file=sys.stderr)
        return 2
    text, met = report(t, second is not None)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0 if met is not None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
