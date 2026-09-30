"""Label which developer messages push back, as SWE-chat labelled its own (#16, step 7; `crawl/label.py`).

    # what a run would cost, calling no model
    .venv/bin/python scripts/crawl_label.py calibrate --model DeepSeek-V4-Flash --estimate
    .venv/bin/python scripts/crawl_label.py label --corpus data/entire/corpus --model DeepSeek-V4-Flash --estimate

    # paid: the calibration on SWE-chat's own messages, then its report
    ERRATA_PROVIDER=azure .venv/bin/python scripts/crawl_label.py calibrate --model DeepSeek-V4-Flash --max-usd 2
    .venv/bin/python scripts/crawl_label.py report data/entire/label-calibration.jsonl

    # paid: the collected corpus's messages, Claude 5 sessions first
    ERRATA_PROVIDER=azure .venv/bin/python scripts/crawl_label.py label --corpus data/entire/corpus \\
        --model DeepSeek-V4-Flash --max-usd 45

`calibrate` reads SWE-chat (the default corpus) and refuses any other; `label`
reads the collected corpus and refuses SWE-chat. A paid run first prices what it
will ask and refuses to start above `--max-usd`. Every row records its token
use, so the spend can be reconciled after. Labels go on the corpus's rows when
it is next assembled (`crawl_entire.py assemble`).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT.parent / "errata-bench" / "release" / "v1.0.2-dataset" / "tasks"
PER_CLASS = {"non_pushback": 100, "correction": 100, "failure_report": 100, "rejection": 60, "takeover": 40}


def prices(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens, from the price table the spend tallies use."""
    spec = importlib.util.spec_from_file_location("d40_spend", ROOT / "scripts" / "d40_spend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if model not in mod.PRICE:
        raise SystemExit(f"no list price for {model!r} in scripts/d40_spend.py; add it before running")
    price_in, _, price_out = mod.PRICE[model]
    return price_in, price_out


def v1_moments(tasks: Path) -> list[tuple[str, int]]:
    """The moments v1's tasks were built from: each task's session and the turn of its complaint."""
    out = []
    for p in sorted(tasks.glob("*/grading/task.json")):
        t = json.loads(p.read_text())
        out.append((t["session_id"], t["complaint_turn"]))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("calibrate", "label"):
        s = sub.add_parser(name)
        s.add_argument("--model", required=True, help="the deployment that labels (DeepSeek-V4-Flash, say)")
        s.add_argument("--estimate", action="store_true", help="price the run and stop; no model is called")
        s.add_argument("--max-usd", type=float, default=0.0, help="refuse to start when the estimate is above this")
        s.add_argument("--concurrency", type=int, default=4)
        s.add_argument("--limit", type=int, default=0, help="label at most this many messages")
    c = sub.choices["calibrate"]
    c.add_argument("--out", type=Path, default=Path("data/entire/label-calibration.jsonl"))
    c.add_argument("--tasks", type=Path, default=TASKS, help="v1's frozen tasks, for the moments they were built from")
    c.add_argument("--seed", type=int, default=0)
    lab = sub.choices["label"]
    lab.add_argument("--corpus", type=Path, required=True, help="the collected corpus (data/entire/corpus)")
    lab.add_argument("--out", type=Path, default=Path("data/entire/labels.jsonl"))
    lab.add_argument("--digests", type=Path, default=Path("data/entire/swechat-digests.json"),
                     help="SWE-chat's session ids, whose sessions are not labelled again")
    lab.add_argument("--since", default="2026-04-20")
    lab.add_argument("--models", default="claude5", help="'claude5' (the default), 'all', or a regex on the model")
    rep = sub.add_parser("report")
    rep.add_argument("rows", type=Path)
    args = ap.parse_args(argv)

    # Which corpus the pipeline's readers load is fixed when they are imported.
    if args.command == "label":
        os.environ["ERRATA_CORPUS"] = str(args.corpus.resolve())
    elif args.command == "calibrate" and os.environ.get("ERRATA_CORPUS"):
        raise SystemExit("calibration reads SWE-chat's own labels: unset ERRATA_CORPUS")
    sys.path.insert(0, str(ROOT / "src"))
    from errata_bench.corpus.recover import recovered
    from errata_bench.corpus.sessions import CORPUS
    from errata_bench.corpus.turns import load_session_turns
    from errata_bench.crawl import label as L
    from errata_bench.store.rows import load

    if args.command == "report":
        rows = load(args.rows)
        frequency = json.loads((args.rows.with_suffix(".frequency.json")).read_text())
        print(json.dumps(L.calibration_report(rows, L.Counter(frequency)), indent=1))
        return 0

    if args.command == "calibrate":
        if CORPUS.resolve() != (ROOT / "data" / "swe-chat").resolve():
            raise SystemExit(f"calibration reads SWE-chat, not {CORPUS}")
        picked, frequency = L.calibration_sample(CORPUS, PER_CLASS, args.seed, also=v1_moments(args.tasks))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".frequency.json").write_text(json.dumps(frequency) + "\n")
        sessions, only, extra = {s for s, _ in picked}, set(picked), picked
        print(f"calibration: {len(picked)} messages from {len(sessions)} sessions; SWE-chat's labels occur "
              f"{dict(frequency)}")
    else:
        if (args.corpus / "conversations.parquet").resolve() == (ROOT / "data" / "swe-chat" / "conversations.parquet").resolve():
            raise SystemExit("label reads the collected corpus, not SWE-chat")
        pattern = {"claude5": L.CLAUDE_5, "all": None}.get(args.models, args.models)
        exclude = set(json.loads(args.digests.read_text())) if args.digests.exists() else set()
        if not exclude:
            raise SystemExit(f"{args.digests} names no SWE-chat session: its sessions would be labelled again")
        chosen = L.entire_sessions(CORPUS, since=args.since, exclude=exclude, model_pattern=pattern)
        sessions, only, extra = set(chosen), None, None
        print(f"label: {len(chosen)} sessions (since {args.since}, not in SWE-chat, sandboxable, models "
              f"{args.models})")

    turns = recovered(load_session_turns(sessions))
    todo = [(s, t) for s in sorted(turns) for t in L.to_label(turns[s])
            if only is None or (s, t["turn_number"]) in only]
    if args.limit:
        todo = todo[:args.limit]
    price_in, price_out = prices(args.model)
    cost = L.estimate([(L.context_for(turns[s], t["turn_number"]), L.message_for(t)) for s, t in todo],
                      price_in=price_in, price_out=price_out)
    print(f"estimate at {args.model}'s list price (${price_in}/${price_out} per 1M, no cache): {cost}")
    if args.estimate:
        return 0
    if cost["usd"] > args.max_usd:
        raise SystemExit(f"estimated ${cost['usd']} is above --max-usd {args.max_usd}: nothing was asked")
    os.environ.setdefault("ERRATA_MODEL", args.model)
    counts = asyncio.run(L.label_turns(turns, args.out, model=args.model, concurrency=args.concurrency,
                                       limit=args.limit, only=only, extra=extra))
    rows = load(args.out)
    used_in = sum((r.get("usage") or {}).get("input_tokens", 0) for r in rows)
    used_out = sum((r.get("usage") or {}).get("output_tokens", 0) for r in rows)
    print(f"labelled: {dict(counts)}; tokens in the file: {used_in:,} in, {used_out:,} out, "
          f"${used_in / 1e6 * price_in + used_out / 1e6 * price_out:.2f} at list price")
    if args.command == "calibrate":
        print(json.dumps(L.calibration_report(rows, frequency), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
