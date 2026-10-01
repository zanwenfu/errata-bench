"""Label which developer messages push back, as SWE-chat labelled its own (#16, step 7; `crawl/label.py`).

    # what a run would cost, calling no model
    .venv/bin/python scripts/crawl_label.py calibrate --model gpt-5.6-luna --effort low --out-tokens 500 --estimate
    .venv/bin/python scripts/crawl_label.py label --corpus data/entire/corpus --model gpt-5.6-luna --effort low \\
        --out-tokens 500 --estimate

    # paid: the calibration on SWE-chat's own messages, then its report
    ERRATA_PROVIDER=azure .venv/bin/python scripts/crawl_label.py calibrate --model gpt-5.6-luna --effort low \\
        --out-tokens 500 --max-usd 1
    .venv/bin/python scripts/crawl_label.py report data/entire/label-calibration-gpt-5.6-luna-low.jsonl

    # paid: the collected corpus's messages, Claude 5 sessions first
    ERRATA_PROVIDER=azure .venv/bin/python scripts/crawl_label.py label --corpus data/entire/corpus \\
        --model gpt-5.6-luna --effort low --out-tokens <the calibration's mean> --max-usd 35

`calibrate` reads SWE-chat (the default corpus) and refuses any other; `label`
reads the collected corpus and refuses SWE-chat. A paid run first prices what it
will ask and refuses to start above `--max-usd`, and stops asking once what it
has spent reaches it; run again, it asks the rest. The estimate assumes
`--out-tokens` written per message: a reasoning model writes more than the 90 a
label and its reason take, so give it the calibration's measured mean. Every
row records its token use, so the spend can be reconciled after. Labels go on
the corpus's rows when it is next assembled (`crawl_entire.py assemble`).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT.parent / "errata-bench" / "release" / "v1.0.2-dataset" / "tasks"
PER_CLASS = {"non_pushback": 100, "correction": 100, "failure_report": 100, "rejection": 60, "takeover": 40}


def pricing(model: str):
    """(input, output) USD per 1M tokens, and what a call's usage cost, from the table the spend tallies use."""
    spec = importlib.util.spec_from_file_location("d40_spend", ROOT / "scripts" / "d40_spend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if model not in mod.PRICE:
        raise SystemExit(f"no list price for {model!r} in scripts/d40_spend.py; add it before running")
    price_in, _, price_out = mod.PRICE[model]
    return price_in, price_out, lambda usage: mod.usd(model, usage)


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
        s.add_argument("--model", required=True, help="the deployment that labels (gpt-5.6-luna, say)")
        s.add_argument("--effort", default=None, help="the reasoning effort asked for (low, say); the model's own if unset")
        s.add_argument("--estimate", action="store_true", help="price the run and stop; no model is called")
        s.add_argument("--out-tokens", type=int, default=90, help="output tokens per message the estimate assumes")
        s.add_argument("--max-usd", type=float, default=0.0,
                       help="refuse to start when the estimate is above this, and stop asking when the spend reaches it")
        s.add_argument("--concurrency", type=int, default=4)
        s.add_argument("--limit", type=int, default=0, help="label at most this many messages")
    c = sub.choices["calibrate"]
    c.add_argument("--out", type=Path, default=None,
                   help="default data/entire/label-calibration-<model>[-<effort>][-seed<seed>].jsonl")
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
        if args.out is None:
            args.out = Path("data/entire") / (f"label-calibration-{args.model}{'-' + args.effort if args.effort else ''}"
                                              f"{f'-seed{args.seed}' if args.seed else ''}.jsonl")
        picked, frequency = L.calibration_sample(CORPUS, PER_CLASS, args.seed, also=v1_moments(args.tasks))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".frequency.json").write_text(json.dumps(frequency) + "\n")
        sessions, only, extra = {s for s, _ in picked}, set(picked), picked
        print(f"calibration: {len(picked)} messages from {len(sessions)} sessions, into {args.out}; "
              f"SWE-chat's labels occur {dict(frequency)}")
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
    # What this run will ask, as `label_turns` chooses it: not a message this
    # model already answered at this effort, then at most `--limit`.
    answered = {(r["session_id"], r["turn_number"]) for r in (load(args.out) if args.out.exists() else [])
                if not r.get("error") and (r.get("model"), r.get("effort")) == (args.model, args.effort)}
    todo = [(s, t) for s in sorted(turns) for t in L.to_label(turns[s])
            if (only is None or (s, t["turn_number"]) in only) and (s, t["turn_number"]) not in answered]
    if args.limit:
        todo = todo[:args.limit]
    if answered:
        print(f"{len(answered)} messages already answered in {args.out}; the estimate is for the rest")
    price_in, price_out, cost_of = pricing(args.model)
    cost = L.estimate([(L.context_for(turns[s], t["turn_number"]), L.message_for(t)) for s, t in todo],
                      price_in=price_in, price_out=price_out, out_tokens=args.out_tokens)
    print(f"estimate at {args.model}'s list price (${price_in}/${price_out} per 1M, no cache, "
          f"{args.out_tokens} tokens written per message): {cost}")
    if args.estimate:
        return 0
    if cost["usd"] > args.max_usd:
        raise SystemExit(f"estimated ${cost['usd']} is above --max-usd {args.max_usd}: nothing was asked")
    os.environ.setdefault("ERRATA_MODEL", args.model)
    began = time.monotonic()
    counts = asyncio.run(L.label_turns(turns, args.out, model=args.model, effort=args.effort,
                                       concurrency=args.concurrency, limit=args.limit, only=only, extra=extra,
                                       spend=cost_of, max_usd=args.max_usd))
    minutes = (time.monotonic() - began) / 60
    rows = load(args.out)
    asked = [r for r in rows if r.get("usage")]
    used_in = sum(r["usage"].get("input_tokens", 0) for r in asked)
    used_out = sum(r["usage"].get("output_tokens", 0) for r in asked)
    thinking = sum(r["usage"].get("reasoning_tokens", 0) for r in asked)
    print(f"labelled this run: {dict(counts)} in {minutes:.1f} min ({sum(counts.values()) / max(minutes, 1e-9):.0f} "
          f"a minute at concurrency {args.concurrency})")
    print(f"the file: {len(asked)} answers; {used_in:,} tokens in, {used_out:,} out ({thinking:,} of them reasoning); "
          f"per answer {used_in / max(len(asked), 1):.0f} in, {used_out / max(len(asked), 1):.0f} out; "
          f"${sum(cost_of(r['usage']) for r in asked):.2f} at list price")
    if args.command == "calibrate":
        print(json.dumps(L.calibration_report(rows, frequency, expected=len(picked)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
