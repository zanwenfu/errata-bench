"""D-40's spend so far, from the token counts its rows recorded, at Azure list prices.

    .venv/bin/python d40_spend.py [--prefix d40] [--stop 1600]

An upper bound, on purpose: gpt-6-sol is not on Azure's price list, so it is
priced as gpt-6-astra, and gpt-6-sol's own tests (calibration, controls,
probes), whose rows record no tokens, are priced at a first reading's cost
each, and counted once, in d40-soltests, where they are asked. Exits 3 when
the total reaches --stop.
"""
import argparse
import glob
import json
import sys
from datetime import datetime, timezone

PRICE = {  # USD per 1M tokens: input, cached input, output (Global Standard list prices, 09-24)
    "grok-4.6": (2.00, 0.50, 6.00), "Kimi-K2.7-Code": (0.95, 0.19, 4.00),
    "DeepSeek-V4-Pro": (1.74, 0.145, 3.48), "DeepSeek-V4-Flash": (0.44, 0.028, 1.32),
    "Mistral-Large-3": (0.50, 0.50, 1.50), "MAI-Thinking-1": (2.00, 0.20, 8.00),
    "gpt-6-astra": (10.00, 1.00, 50.00), "gpt-6-sol": (10.00, 1.00, 50.00),
}
TEST_ROW_USD = {"calibration": 0.40, "controls": 0.26, "instrument": 0.13}


def usd(model, u, reasoning_apart=False):
    if not u:
        return 0.0
    i, c, o = PRICE[model]
    cached = u.get("cached_tokens") or 0
    out = u.get("output_tokens", 0) + ((u.get("reasoning_tokens") or 0) if reasoning_apart else 0)
    return ((u.get("input_tokens", 0) - cached) * i + cached * c + out * o) / 1e6


def rows(path):
    try:
        with open(path, "rb") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        return


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="d40")
    ap.add_argument("--stop", type=float, default=1600.0)
    args = ap.parse_args(argv)
    cand = astra = sol = tests = 0.0
    answers = readings = 0
    for d in sorted(glob.glob(f"runs/{args.prefix}-*")):
        name = d.split(f"{args.prefix}-", 1)[1]
        if d.endswith((".log", ".done", ".failed", ".pid")) or name == "base":
            continue
        if name in PRICE:
            for r in rows(f"{d}/answers.jsonl"):
                answers += 1
                cand += usd(name, r.get("usage"), reasoning_apart=(name == "grok-4.6"))
        for r in rows(f"{d}/attempts.jsonl"):
            readings += 1
            astra += usd("gpt-6-astra", r.get("judge_usage")) + usd("gpt-6-astra", r.get("trace_usage"))
        for r in rows(f"{d}/rejudge/gpt-6-sol/attempts.jsonl"):
            sol += usd("gpt-6-sol", r.get("judge_usage")) + usd("gpt-6-sol", r.get("trace_usage"))
        # Counted where they were asked, never where TESTS_FROM copied them to.
        if name == "soltests":
            for kind, each in TEST_ROW_USD.items():
                tests += each * sum(1 for _ in rows(f"{d}/rejudge/gpt-6-sol/{kind}.jsonl"))
    total = cand + astra + sol + tests
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{now} answers {answers} readings {readings} | candidates ${cand:.2f} | gpt-6-astra ${astra:.2f} | "
          f"gpt-6-sol ${sol:.2f} | its tests ~${tests:.2f} | total ${total:.2f} of stop ${args.stop:.0f}", flush=True)
    return 3 if total >= args.stop else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
