"""Run the study: does an AI reviewer catch what the developer caught? (docs/study.md)

    .venv/bin/python scripts/study.py prepare  --out runs/study-pilot [--sessions 40] [--per-repo 2]
                                               [--max-reports 40] [--session ID ...]
    .venv/bin/python scripts/study.py estimate --run runs/study-pilot
    .venv/bin/python scripts/study.py review   --run runs/study-pilot --max-usd N [--concurrency 8] [--limit N]
    .venv/bin/python scripts/study.py human    --run runs/study-pilot --max-usd N [--concurrency 8] [--limit N]
    .venv/bin/python scripts/study.py merge    --run runs/study-pilot --max-usd N [--concurrency 8]
    .venv/bin/python scripts/study.py tally    --run runs/study-pilot
    .venv/bin/python scripts/study.py sheet    --run runs/study-pilot [--matches 150] [--alone 50]

`prepare` and `estimate` make no model call. `review` (thread A) and `human`
(thread B) are independent and can run side by side; `merge` reads both. Each
paid stage resumes: a row already in its file is not asked again, and a row that
errored is asked again. Each stops starting calls once the run's spend, priced
as the spend guard prices it (`harbor_spend.priced`, an upper bound), reaches
--max-usd; calls in flight finish. The model is ERRATA_MODEL (gpt-6-astra by
default); with ERRATA_PROVIDER=azure it is paid from the Azure credits. Claude is
refused by the client, as everywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from errata_bench.store.rows import append, completed, load, only_one  # noqa: E402
from errata_bench.study import human as B  # noqa: E402
from errata_bench.study import merge as M  # noqa: E402
from errata_bench.study import review as A  # noqa: E402
from errata_bench.study import sessions as S  # noqa: E402
from errata_bench.study import stats as T  # noqa: E402

_spec = importlib.util.spec_from_file_location("harbor_spend", Path(__file__).resolve().parent / "harbor_spend.py")
_spend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spend)

FILES = {"sessions": "sessions.jsonl", "reports": "reports.jsonl", "review": "reviews.jsonl",
         "human": "replies.jsonl", "merge": "merges.jsonl"}
# Output tokens assumed per call, for the estimate only; the real ones are on each row.
OUTPUT_GUESS = {"review": 700, "human": 350, "merge": 300}
CHARS_PER_TOKEN = 3.5


def path(run: Path, name: str, framing: str = "outside") -> Path:
    """A stage's file. The reviewer's and the merge's carry their framing when it is not the outside reviewer's."""
    f = FILES[name]
    if framing != "outside" and name in ("review", "merge"):
        f = f.replace(".jsonl", f"-{framing}.jsonl")
    return run / f


def key(row: dict) -> tuple:
    return (row["session_id"], row["index"])


def _in_runs(runs: Path = Path("runs")) -> set[str]:
    """Every session a study run under runs/ already holds (study-*/sessions.jsonl)."""
    return {r["session_id"] for f in runs.glob("study-*/sessions.jsonl") for r in load(f)}


# --------------------------------------------------------------------------- prepare (free)

def prepare(args) -> int:
    out = Path(args.out)
    if path(out, "reports").exists():
        print(f"refused: {path(out, 'reports')} exists; a run is prepared once. Use another --out.", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    if args.session:
        repo_of = {p["session_id"]: p["repo_id"] for p in S.pilot_sessions(n=10**9, per_repo=10**9)}
        picked = [{"session_id": s, "repo_id": repo_of.get(s, "")} for s in args.session]
    elif args.random:
        # Drawn without regard to pushback, leaving out only sessions a study run already holds.
        picked = S.random_sessions(n=args.random, per_repo=args.per_repo, exclude=_in_runs())
    elif args.batch:
        # The next sessions of the pushback-drawn order that no study run holds yet.
        held = _in_runs()
        picked = [p for p in S.pilot_sessions(n=10**9, per_repo=args.per_repo)
                  if p["session_id"] not in held][: args.batch]
    else:
        picked = S.pilot_sessions(n=args.sessions, per_repo=args.per_repo)
    from errata_bench.corpus.recover import has_transcript

    turns = S.load_turns([p["session_id"] for p in picked])
    n_reports = 0
    for p in picked:
        sid = p["session_id"]
        raw = turns.get(sid) or []
        if not raw:
            append(path(out, "sessions"), {**p, "turns": 0, "reports": 0, "error": "no rows in the corpus"})
            continue
        repo = p["repo_id"] or ""
        view = S.view_turns(sid, raw)
        transcript = S.transcript_prompts(sid)
        reps = S.reports(sid, view, repo, transcript)
        kinds_table, kinds = S.row_kinds(raw), S.row_kinds(raw, transcript)
        by_table = sum(1 for t in raw if t.get("turn_type") == "user_prompt" and t.get("turn_number") in kinds_table)
        by_transcript = sum(1 for t in raw if t.get("turn_type") == "user_prompt" and t.get("turn_number") in kinds)
        kept = reps[: args.max_reports] if args.max_reports else reps
        append(path(out, "sessions"), {**p, "turns": len(raw), "reports_found": len(reps), "reports": len(kept),
                                       "transcript": has_transcript(sid), "developer_rows_by_table": by_table,
                                       "developer_rows_by_transcript": by_transcript})
        for r in kept:
            text, stats = S.window(r, view)
            append(path(out, "reports"), S.report_row(r, text, stats))
            n_reports += 1
    print(f"prepared {len(picked)} sessions, {n_reports} reports, in {out}")
    return 0


# --------------------------------------------------------------------------- estimate (free)

def estimate(args) -> int:
    run = Path(args.run)
    reports = load(path(run, "reports"))
    instr = {"review": len(A.INSTRUCTIONS), "human": len(B.INSTRUCTIONS), "merge": len(M.INSTRUCTIONS)}
    tok = lambda chars: chars / CHARS_PER_TOKEN  # noqa: E731
    a_in = sum(tok(len(r["window"]) + instr["review"] + 1500) for r in reports)
    b_in = sum(tok(len(r["window"]) + len(r["reply"]) + instr["human"] + 1500) for r in reports)
    # The merge: about as many calls as SWE-chat's labelled pushbacks, each a few problems long.
    labelled = sum(r.get("reply_label") in S.PUSHBACK_KINDS for r in reports)
    m_in = labelled * tok(instr["merge"] + 3000)
    model = _model()
    rows = [("review (thread A)", len(reports), a_in, OUTPUT_GUESS["review"]),
            ("human (thread B)", len(reports), b_in, OUTPUT_GUESS["human"]),
            ("merge (about)", labelled, m_in, OUTPUT_GUESS["merge"])]
    if getattr(args, "self_framing", False):
        rows += [("review, self framing", len(reports), a_in, OUTPUT_GUESS["review"]),
                 ("merge, self (about)", labelled, m_in, OUTPUT_GUESS["merge"])]
    total = 0.0
    for name, calls, tin, tout in rows:
        usage = {"input_tokens": int(tin), "output_tokens": int(calls * tout), "cached_tokens": 0}
        usd = _spend.priced(model, usage, set())
        total += usd
        print(f"{name:20s} {calls:5d} calls  {tin / 1e6:6.2f}M in  {calls * tout / 1e6:5.2f}M out  ~${usd:,.0f}")
    print(f"{'total':20s} ~${total:,.0f} on {model} (upper bound: no cache, uncached input at the cache-write rate)")
    return 0


# --------------------------------------------------------------------------- the paid stages

def _model() -> str:
    from errata_bench.llm import MODEL
    return MODEL


def _spent(run: Path) -> float:
    """The run's spend so far: every row's usage in every paid stage's file, any framing, and their dropped rows."""
    total = 0.0
    files = {f for pattern in ("reviews*.jsonl", "replies*.jsonl", "merges*.jsonl") for f in run.glob(pattern)}
    for f in sorted(files):
        for r in load(f):
            if r.get("usage"):
                total += _spend.priced(r.get("model") or _model(), r.get("usage"), set())
    return total


async def _run(run: Path, name: str, todo: list, *, max_usd: float, concurrency: int, framing: str = "outside") -> int:
    """Make each item's call, at most `concurrency` at once, until done or the run's spend reaches max_usd."""
    from errata_bench.llm import usage_of

    out = path(run, name, framing)
    spent = _spent(run)
    print(f"{name}: {len(todo)} to do; run spend so far ${spent:,.2f} of ${max_usd:,.2f}")
    if spent >= max_usd:
        print(f"{name}: stopped before starting: the spend has reached --max-usd", file=sys.stderr)
        return 3
    gate = asyncio.Semaphore(concurrency)
    state = {"spent": spent, "stopped": False, "done": 0, "errors": 0}
    model = _model()

    async def one(item):
        async with gate:
            # Re-read from the files, so stages run side by side share one line. Calls in
            # flight in other processes are not counted until written; the overshoot is at
            # most each process's concurrency (10-03 review).
            if state["stopped"] or max(state["spent"], _spent(run)) >= max_usd:
                state["stopped"] = True
                return
            base, make = item
            try:
                result, extra = await make()
                usage = usage_of(result)
                row = {**base, **extra, "model": model, "usage": usage}
            except Exception as e:  # noqa: BLE001 - recorded as an errored row, asked again on resume
                usage = None
                row = {**base, "model": model, "usage": None, "error": f"{type(e).__name__}: {e}"[:500]}
                state["errors"] += 1
            state["spent"] += _spend.priced(model, usage, set())
            append(out, row)
            state["done"] += 1
            if state["done"] % 25 == 0:
                print(f"{name}: {state['done']}/{len(todo)} written, ${state['spent']:,.2f}", flush=True)

    await asyncio.gather(*(one(i) for i in todo))
    print(f"{name}: {state['done']} written ({state['errors']} errored), run spend ${state['spent']:,.2f}"
          + ("; stopped at --max-usd" if state["stopped"] else ""))
    return 3 if state["stopped"] else (1 if state["errors"] else 0)


def review(args) -> int:
    run = Path(args.run)
    framing = getattr(args, "framing", "outside")
    done = {key(r) for r in completed(path(run, "review", framing))}
    todo = []
    for r in load(path(run, "reports")):
        if key(r) in done:
            continue
        base = {"session_id": r["session_id"], "index": r["index"]}

        async def make(r=r):
            result = await A.review(r["window"], model=_model(), framing=framing)
            problems = A.checked(result.final_output, r["window"])
            return result, {"problems": problems, "framing": framing}
        todo.append((base, make))
    todo = todo[: args.limit] if args.limit else todo
    with only_one(run, f"running review ({framing})", name=f"review-{framing}.lock"):
        return asyncio.run(_run(run, "review", todo, max_usd=args.max_usd, concurrency=args.concurrency,
                                framing=framing))


def human(args) -> int:
    run = Path(args.run)
    done = {key(r) for r in completed(path(run, "human"))}
    todo = []
    for r in load(path(run, "reports")):
        if key(r) in done:
            continue
        base = {"session_id": r["session_id"], "index": r["index"], "reply_label": r.get("reply_label"),
                "reply_kind": r.get("reply_kind")}
        if r.get("reply_kind") == "plan":
            # A plan approved is never pushback (docs/study.md): no call.
            append(path(run, "human"), {**base, "pushback_kind": "non_pushback", "is_pushback": False,
                                        "approved_plan": True, "model": None, "usage": None})
            continue

        async def make(r=r):
            result = await B.classify(r["window"], r["reply"], r["handoff_turn"], model=_model())
            return result, B.checked(result.final_output, r["window"], r["reply"])
        todo.append((base, make))
    todo = todo[: args.limit] if args.limit else todo
    with only_one(run, "running human", name="human.lock"):
        return asyncio.run(_run(run, "human", todo, max_usd=args.max_usd, concurrency=args.concurrency))


def _by_session(rows: list[dict]) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        out[r["session_id"]][r["index"]] = r
    return out


def merge(args) -> int:
    run = Path(args.run)
    framing = getattr(args, "framing", "outside")
    reports = _by_session(load(path(run, "reports")))
    reviews = _by_session(completed(path(run, "review", framing)))
    replies = _by_session(completed(path(run, "human")))
    done = {key(r) for r in completed(path(run, "merge", framing))}
    todo, alone, waiting = [], 0, 0
    for sid, by_index in reports.items():
        for k, rep in by_index.items():
            rr = replies.get(sid, {}).get(k)
            if rr is None or (sid, k) in done:
                waiting += rr is None
                continue
            if not rr.get("is_pushback"):
                continue
            # Every report a pushback looks back on must have been reviewed first.
            needed = [j for j in range(max(1, k - M.LOOKBACK), k + 1) if j in by_index]
            if any(j not in reviews.get(sid, {}) for j in needed):
                waiting += 1
                continue
            cands = M.candidates({j: reviews[sid][j]["problems"] for j in needed}, k)
            base = {"session_id": sid, "index": k}
            if not cands:
                append(path(run, "merge", framing), {**base, "verdicts": [], "no_candidates": True, "model": None,
                                                     "usage": None})
                alone += 1
                continue

            async def make(rr=rr, rep=rep, cands=cands):
                result = await M.merge(rr, rep["reply"], cands, model=_model())
                verdicts = M.checked(result.final_output, cands, rep["reply"])
                if verdicts and all(v.get("missing") for v in verdicts):
                    # Every id came back wrong: an errored row, asked again on resume, not a miss.
                    raise RuntimeError(f"the merge named none of the {len(cands)} problems by their ids")
                return result, {"verdicts": verdicts, "no_candidates": False}
            todo.append((base, make))
    print(f"merge: {alone} pushbacks with no reviewer problem to compare (the developer's alone, no call); "
          f"{waiting} replies or reports not read yet")
    with only_one(run, f"running merge ({framing})", name=f"merge-{framing}.lock"):
        return asyncio.run(_run(run, "merge", todo, max_usd=args.max_usd, concurrency=args.concurrency,
                                framing=framing))


# --------------------------------------------------------------------------- tally and the hand-label sheet (free)

def _valid_same(v: dict) -> bool:
    """A 'same' that counts: a known verdict, not missing, its overlap words found on both sides."""
    return (v.get("match") == "same" and v.get("match_known", True) and not v.get("missing")
            and bool(v.get("developer_words_found", True)) and bool(v.get("reviewer_words_found", True)))


def tally(args) -> int:
    """The study's numbers for one framing, each share with a 95% interval resampling sessions."""
    run = Path(args.run)
    framing = getattr(args, "framing", "outside")
    reports = {key(r): r for r in load(path(run, "reports"))}
    reviews = {key(r): r for r in completed(path(run, "review", framing))}
    replies = {key(r): r for r in completed(path(run, "human"))}
    merges = {key(r): r for r in completed(path(run, "merge", framing))}
    if not reviews or not replies:
        print("nothing to tally yet: review and human must have run", file=sys.stderr)
        return 2
    pushbacks = {k: r for k, r in replies.items() if r.get("is_pushback")}
    outcome, matched = {}, set()
    for k in pushbacks:
        m = merges.get(k)
        if m is None:
            outcome[k] = "not merged"
            continue
        vs = m.get("verdicts") or []
        if any(_valid_same(v) for v in vs):
            outcome[k] = "same"
        elif any(v.get("missing") or not v.get("match_known", True) for v in vs):
            # A verdict missing or unknown, and no valid 'same': not read in full, so not a miss.
            outcome[k] = "incomplete"
        elif any(v.get("match") == "same" for v in vs):
            outcome[k] = "same, words not found"
        elif any(v.get("match") == "related" and not v.get("missing") for v in vs):
            outcome[k] = "related"
        else:
            outcome[k] = "none"
        matched |= {(k[0], v["report"], v["problem_id"]) for v in vs if _valid_same(v)}
    merged = {k: o for k, o in outcome.items() if o not in ("not merged", "incomplete")}

    def caught(keys) -> dict:
        """The share of merged pushbacks whose reviewer named the same problem; unmerged ones are left out."""
        return T.bootstrap([(k[0], merged[k] == "same") for k in keys if k in merged])

    def grouped(field: str, many: bool = False) -> dict:
        groups: dict[str, list] = defaultdict(list)
        for k, r in pushbacks.items():
            for g in (r.get(field) or ["(none)"]) if many else [r.get(field) or "?"]:
                groups[g].append(k)
        return {g: caught(keys) for g, keys in sorted(groups.items())}

    problems = [(k[0], k[1], f"r{k[1]}p{i}", p) for k, r in reviews.items()
                for i, p in enumerate(r.get("problems") or []) if p.get("quote_in_work")]
    alone = [x for x in problems if (x[0], x[1], x[2]) not in matched]
    pairs = [(str(r.get("reply_label") in S.PUSHBACK_KINDS), str(bool(r.get("is_pushback"))))
             for r in replies.values() if r.get("reply_label") and not r.get("approved_plan")]
    out = {
        "framing": framing,
        "reports": len(reports), "reviewed": len(reviews), "replies_read": len(replies),
        "pushbacks": len(pushbacks),
        "pushback_outcomes": dict(Counter(outcome.values())),
        "caught_same": {
            "every_pushback": caught(pushbacks),
            "real_error": caught([k for k, r in pushbacks.items() if r.get("objection_kind") == "real_error"]),
            "by_objection_kind": grouped("objection_kind"),
            "by_pushback_kind": grouped("pushback_kind"),
            "by_failure_mode": grouped("failure_modes", many=True),
            "after_a_report": caught([k for k in pushbacks if reports.get(k, {}).get("ends_with_report")]),
            "before_a_report": caught([k for k in pushbacks if not reports.get(k, {}).get("ends_with_report", True)]),
        },
        "reviewer": {
            "problems_counted": len(problems),
            "per_report": T.share(len(problems), len(reviews)),
            "reports_with_a_problem": T.bootstrap([(k[0], any(p.get("quote_in_work") for p in r.get("problems") or []))
                                                   for k, r in reviews.items()]),
            "quote_not_found": sum(1 for r in reviews.values() for p in r.get("problems") or []
                                   if not p.get("quote_found")),
            "quoting_context_only": sum(1 for r in reviews.values() for p in r.get("problems") or []
                                        if p.get("quote_found") and not p.get("quote_in_work")),
            "alone": len(alone),
            "alone_share": T.share(len(alone), len(problems)),
            "by_kind": dict(Counter(x[3].get("kind") for x in problems).most_common()),
            "alone_by_kind": dict(Counter(x[3].get("kind") for x in alone).most_common()),
        },
        "swe_chat_label": {
            "pushback_or_not_kappa": T.kappa(pairs),
            "label_vs_reading": {f"{a} / {b}": n for (a, b), n in sorted(Counter(
                (r.get("reply_label") or "none", r.get("pushback_kind") or "?") for r in replies.values()).items())},
        },
        "not_yet": {"reviews": len(reports) - len(reviews), "replies": len(reports) - len(replies),
                    "merges": sum(1 for o in outcome.values() if o == "not merged"),
                    "merges_incomplete": sum(1 for o in outcome.values() if o == "incomplete")},
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))
    (run / ("tally.json" if framing == "outside" else f"tally-{framing}.json")).write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def _fenced(text: str) -> str:
    """Text in a fence no line of it can close: tildes, more than any run in the text."""
    longest = max((len(m) for m in __import__("re").findall(r"~+", text)), default=0)
    fence = "~" * max(4, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def sheet(args) -> int:
    """The hand check's files: labels.csv to fill in blind, key.csv with the model's verdicts, alone.md."""
    run = Path(args.run)
    framing = getattr(args, "framing", "outside")
    tag = "" if framing == "outside" else f"-{framing}"
    reports = {key(r): r for r in load(path(run, "reports"))}
    reviews = {key(r): r for r in completed(path(run, "review", framing))}
    merges = completed(path(run, "merge", framing))
    rng = random.Random(20261003)
    pairs = []
    for m in merges:
        rep = reports[key(m)]
        for v in m.get("verdicts") or []:
            if v.get("missing"):
                continue
            j, i = v["report"], int(v["problem_id"].split("p")[1])
            p = (reviews.get((m["session_id"], j)) or {}).get("problems", [])[i]
            pairs.append((v, rep, p))
    # A uniform sample, not one stratified by the model's verdict: kappa on it is the
    # kappa of the merge as it runs (10-03 review).
    rng.shuffle(pairs)
    take = pairs[: args.matches]
    with open(run / f"labels{tag}.csv", "w", newline="") as f, open(run / f"key{tag}.csv", "w", newline="") as g:
        w, kw = csv.writer(f), csv.writer(g)
        w.writerow(["item", "developer_reply", "reviewer_problem", "reviewer_quote",
                    "your_label (same / related / different)", "note"])
        kw.writerow(["item", "session", "handback", "problem_id", "model_match", "model_reason"])
        for n, (v, rep, p) in enumerate(take, 1):
            w.writerow([n, rep["reply"][:6000], p["what_is_wrong"], p["quote"][:2000], "", ""])
            kw.writerow([n, rep["session_id"], rep["index"], v["problem_id"], v.get("match"), v.get("reason")])
    print(f"wrote {len(take)} merge decisions to label blind: {run / f'labels{tag}.csv'} "
          f"(the model's: {run / f'key{tag}.csv'})")

    matched = {(m["session_id"], v["report"], v["problem_id"]) for m in merges for v in m.get("verdicts") or []
               if _valid_same(v)}
    alone = [((k[0], k[1]), f"r{k[1]}p{i}", p) for k, r in reviews.items()
             for i, p in enumerate(r.get("problems") or [])
             if p.get("quote_in_work") and (k[0], k[1], f"r{k[1]}p{i}") not in matched]
    rng.shuffle(alone)
    alone = alone[: args.alone]
    with open(run / f"alone{tag}.md", "w") as f:
        f.write("# The reviewer's problems that no pushback matched\n\nFor each: is it a real problem the developer "
                "let pass, or a false alarm? Decide from the work, then write `real`, `false alarm` or `can't tell` "
                "after **Your call:**. Open what the developer said next only after you decide.\n")
        for n, ((sid, k), pid, p) in enumerate(alone, 1):
            rep = reports[(sid, k)]
            later = [reports[(sid, j)]["reply"][:1500] for j in range(k, k + M.LOOKBACK + 1) if (sid, j) in reports]
            f.write(f"\n---\n\n## {n}. session {sid[:8]}, handback {k} ({pid})\n\n"
                    f"**The reviewer:** {p['what_is_wrong']}\n\n**It quoted (turn {p['turn_number']:g}):** "
                    f"\"{p['quote']}\"\n\n**Your call:** \n\n<details><summary>The work it read</summary>\n\n"
                    f"{_fenced(rep['window'])}\n\n</details>\n\n<details><summary>What the developer said next "
                    f"(open only after you decide)</summary>\n\n" + "\n\n".join(_fenced(r) for r in later)
                    + "\n\n</details>\n")
    print(f"wrote {len(alone)} of the reviewer's unmatched problems to check: {run / f'alone{tag}.md'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", required=True)
    p.add_argument("--sessions", type=int, default=40)
    p.add_argument("--per-repo", type=int, default=2)
    p.add_argument("--max-reports", type=int, default=40,
                   help="the first N reports of each session (0: all); one session holds 426")
    p.add_argument("--session", action="append", help="a session id to prepare instead of the drawn sample")
    p.add_argument("--random", type=int, default=0,
                   help="draw this many sessions without regard to pushback instead (the comparison arm)")
    p.add_argument("--batch", type=int, default=0,
                   help="draw the next N sessions of the pushback-drawn order that no study run holds yet")
    e = sub.add_parser("estimate")
    e.add_argument("--run", required=True)
    e.add_argument("--self-framing", action="store_true", help="include the self framing's review and merge")
    t = sub.add_parser("tally")
    t.add_argument("--run", required=True)
    t.add_argument("--framing", choices=("outside", "self"), default="outside")
    for name in ("review", "human", "merge"):
        q = sub.add_parser(name)
        q.add_argument("--run", required=True)
        q.add_argument("--max-usd", type=float, required=True)
        q.add_argument("--concurrency", type=int, default=8)
        q.add_argument("--limit", type=int, default=0)
        if name in ("review", "merge"):
            q.add_argument("--framing", choices=("outside", "self"), default="outside",
                           help="the outside reviewer (default) or the same model told the work is its own")
    s = sub.add_parser("sheet")
    s.add_argument("--run", required=True)
    s.add_argument("--matches", type=int, default=150)
    s.add_argument("--alone", type=int, default=50)
    s.add_argument("--framing", choices=("outside", "self"), default="outside")
    args = ap.parse_args()
    if getattr(args, "concurrency", 1) < 1:
        ap.error("--concurrency must be at least 1")
    return {"prepare": prepare, "estimate": estimate, "review": review, "human": human, "merge": merge,
            "tally": tally, "sheet": sheet}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
