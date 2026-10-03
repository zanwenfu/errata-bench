"""Run the study: does an AI reviewer catch what the developer caught? (docs/study.md)

    .venv/bin/python scripts/study.py prepare  --out runs/study-pilot [--sessions 40] [--per-repo 2]
                                               [--max-reports 40] [--session ID ...]
    .venv/bin/python scripts/study.py estimate --run runs/study-pilot
    .venv/bin/python scripts/study.py review   --run runs/study-pilot --max-usd N [--concurrency 8] [--limit N]
    .venv/bin/python scripts/study.py human    --run runs/study-pilot --max-usd N [--concurrency 8] [--limit N]
    .venv/bin/python scripts/study.py merge    --run runs/study-pilot --max-usd N [--concurrency 8] [--limit N] [--rules 2]
    .venv/bin/python scripts/study.py tally    --run runs/study-pilot
    .venv/bin/python scripts/study.py sheet    --run runs/study-pilot [--alone 50]
    .venv/bin/python scripts/study.py caught-sheet  --run runs/study-pushback-120 --also runs/study-pushback-120-rules2
    .venv/bin/python scripts/study.py replies-sheet --run runs/study-pushback-120 [--n 40]
    .venv/bin/python scripts/study.py agreement --run runs/study-pushback-120

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
import io
import itertools
import json
import math
import random
import re
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

def estimate_rows(reports: list[dict], self_framing: bool = False) -> list[tuple[str, int, float, int]]:
    """Each paid stage's (name, calls, input tokens, output tokens), estimated before any call."""
    instr = {"review": len(A.INSTRUCTIONS), "human": len(B.INSTRUCTIONS), "merge": len(M.INSTRUCTIONS[M.RULES])}
    tok = lambda chars: chars / CHARS_PER_TOKEN  # noqa: E731
    a_in = sum(tok(len(r["window"]) + instr["review"] + 1500) for r in reports)
    b_in = sum(tok(len(r["window"]) + len(r["reply"]) + instr["human"] + 1500) for r in reports)
    # The merge: one call for each pushback by either reading, each a few problems long. Before thread B has
    # run only SWE-chat's label is known; our reading adds about a fifth (pilot 10-03: 252 merged, 215 by label).
    labelled = round(1.2 * sum(r.get("reply_label") in S.PUSHBACK_KINDS for r in reports))
    m_in = labelled * tok(instr["merge"] + 3000)
    rows = [("review (thread A)", len(reports), a_in, OUTPUT_GUESS["review"]),
            ("human (thread B)", len(reports), b_in, OUTPUT_GUESS["human"]),
            ("merge (about)", labelled, m_in, OUTPUT_GUESS["merge"])]
    if self_framing:
        rows += [("review, self framing", len(reports), a_in, OUTPUT_GUESS["review"]),
                 ("merge, self (about)", labelled, m_in, OUTPUT_GUESS["merge"])]
    return rows


def estimate(args) -> int:
    run = Path(args.run)
    rows = estimate_rows(load(path(run, "reports")), getattr(args, "self_framing", False))
    model = _model()
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
    rules = getattr(args, "rules", M.RULES)
    reports = _by_session(load(path(run, "reports")))
    reviews = _by_session(completed(path(run, "review", framing)))
    replies = _by_session(completed(path(run, "human")))
    merged = completed(path(run, "merge", framing))
    held = {r.get("rules", 1) for r in merged}
    if held - {rules}:
        print(f"refused: {path(run, 'merge', framing)} holds merges made under rules {sorted(held)}; one folder holds "
              f"one version of the merge's rules. Use another folder for rules {rules}.", file=sys.stderr)
        return 2
    done = {key(r) for r in merged}
    todo, alone, waiting = [], 0, 0
    for sid, by_index in reports.items():
        for k, rep in by_index.items():
            rr = replies.get(sid, {}).get(k)
            if rr is None or (sid, k) in done:
                waiting += rr is None
                continue
            # Pushback by our reading or by SWE-chat's label: the two disagree on about a quarter of
            # replies (pilot, 10-03), so the result is reported under each.
            if rr.get("approved_plan") or not (rr.get("is_pushback") or rr.get("reply_label") in S.PUSHBACK_KINDS):
                continue
            # Every report a pushback looks back on must have been reviewed first.
            needed = [j for j in range(max(1, k - M.LOOKBACK), k + 1) if j in by_index]
            if any(j not in reviews.get(sid, {}) for j in needed):
                waiting += 1
                continue
            cands = M.candidates({j: reviews[sid][j]["problems"] for j in needed}, k)
            base = {"session_id": sid, "index": k, "rules": rules}
            if not cands:
                append(path(run, "merge", framing), {**base, "verdicts": [], "no_candidates": True, "model": None,
                                                     "usage": None})
                alone += 1
                continue

            async def make(rr=rr, rep=rep, cands=cands):
                result = await M.merge(rr, rep["reply"], cands, model=_model(), rules=rules)
                verdicts = M.checked(result.final_output, cands, rep["reply"])
                if verdicts and all(v.get("missing") for v in verdicts):
                    # Every id came back wrong: an errored row, asked again on resume, not a miss.
                    raise RuntimeError(f"the merge named none of the {len(cands)} problems by their ids")
                return result, {"verdicts": verdicts, "no_candidates": False}
            todo.append((base, make))
    print(f"merge: {alone} pushbacks with no reviewer problem to compare (the developer's alone, no call); "
          f"{waiting} replies or reports not read yet")
    todo = todo[: args.limit] if getattr(args, "limit", 0) else todo
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
    labelled = {k: r for k, r in replies.items() if r.get("reply_label") in S.PUSHBACK_KINDS
                and not r.get("approved_plan")}
    outcome, matched = {}, set()
    for k in {**pushbacks, **labelled}:
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
        elif any(v.get("match") == "related" and not v.get("missing") for v in vs):
            outcome[k] = "related"
        elif any(v.get("match") == "same" for v in vs):
            outcome[k] = "same, words not found"
        else:
            outcome[k] = "none"
        matched |= {(k[0], v["report"], v["problem_id"]) for v in vs if _valid_same(v)}
    merged = {k: o for k, o in outcome.items() if o not in ("not merged", "incomplete")}

    def caught(keys) -> dict:
        """The share of merged pushbacks whose reviewer named the same problem; unmerged ones are left out."""
        return T.bootstrap([(k[0], merged[k] == "same") for k in keys if k in merged])

    def flagged(keys) -> dict:
        """The lenient share: the reviewer named the fault, or flagged the same work for another ('related')."""
        return T.bootstrap([(k[0], merged[k] in ("same", "related")) for k in keys if k in merged])

    def grouped(field: str, many: bool = False) -> dict:
        groups: dict[str, list] = defaultdict(list)
        for k, r in pushbacks.items():
            for g in (r.get(field) or ["(none)"]) if many else [r.get(field) or "?"]:
                groups[g].append(k)
        return {g: caught(keys) for g, keys in sorted(groups.items())}

    problems = [(k[0], k[1], f"r{k[1]}p{i}", p) for k, r in reviews.items()
                for i, p in enumerate(r.get("problems") or []) if p.get("quote_in_work")]
    # A problem is the reviewer's alone if no pushback, by either reading, matched it.
    alone = [x for x in problems if (x[0], x[1], x[2]) not in matched]
    pairs = [(str(r.get("reply_label") in S.PUSHBACK_KINDS), str(bool(r.get("is_pushback"))))
             for r in replies.values() if r.get("reply_label") and not r.get("approved_plan")]
    out = {
        "framing": framing,
        "reports": len(reports), "reviewed": len(reviews), "replies_read": len(replies),
        "pushbacks": len(pushbacks),
        "pushbacks_by_swe_chat_label": len(labelled),
        "merge_rules": dict(Counter(m.get("rules", 1) for m in merges.values())),
        "pushback_outcomes": dict(Counter(o for k, o in outcome.items() if k in pushbacks)),
        "caught_same": {
            "every_pushback": caught(pushbacks),
            "real_error": caught([k for k, r in pushbacks.items() if r.get("objection_kind") == "real_error"]),
            "by_objection_kind": grouped("objection_kind"),
            "by_pushback_kind": grouped("pushback_kind"),
            "by_failure_mode": grouped("failure_modes", many=True),
            "by_swe_chat_label": caught(labelled),
            "both_say_pushback": caught([k for k in pushbacks if k in labelled]),
            "after_a_report": caught([k for k in pushbacks if reports.get(k, {}).get("ends_with_report")]),
            "same_or_related_every_pushback": flagged(pushbacks),
            "same_or_related_real_error": flagged([k for k, r in pushbacks.items()
                                                   if r.get("objection_kind") == "real_error"]),
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
    """The hand check's alone.md: the reviewer's problems that no pushback matched, to call real or false alarm.

    It also wrote labels.csv, a uniform sample of single merge decisions, until the 10-03 review: most were
    plain 'different', and its kappa could pass with every 'same' wrong. caught-sheet replaced it. The sample
    is still drawn, unwritten, so alone.md draws the same problems it always has.
    """
    run = Path(args.run)
    framing = getattr(args, "framing", "outside")
    tag = "" if framing == "outside" else f"-{framing}"
    # A sheet may already hold a person's calls, or translations added to it: never written over.
    if (run / f"alone{tag}.md").exists():
        print(f"refused: {run / f'alone{tag}.md'} exists and may hold calls; move it away to write a new sheet",
              file=sys.stderr)
        return 2
    reports = {key(r): r for r in load(path(run, "reports"))}
    reviews = {key(r): r for r in completed(path(run, "review", framing))}
    merges = completed(path(run, "merge", framing))
    rng = random.Random(20261003)
    pairs = [v for m in merges for v in m.get("verdicts") or [] if not v.get("missing")]
    rng.shuffle(pairs)   # the old labels.csv draw, kept for alone.md's sake (see the docstring)
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


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def caught_sheet(args) -> int:
    """The hand check of the main measure: real-error pushbacks, each with every problem the reviewer raised
    before it, for the person to say which name the developer's fault, if any.

    Drawn by what the merge decided under the rules of --run and of --also (another version): every pushback
    the two decide differently, and --per-stratum each of those both call caught and of those neither does, in
    one random order. caught-key.json holds each item's letters, both versions' verdicts and the strata's sizes,
    so `agreement` can weigh each stratum back to its size (docs/study.md).
    """
    run, other = Path(args.run), Path(args.also)
    out_path, key_path = run / "caught.md", run / "caught-key.json"
    if out_path.exists():
        print(f"refused: {out_path} exists and may hold calls; move it away to write a new sheet", file=sys.stderr)
        return 2
    reports = {key(r): r for r in load(path(run, "reports"))}
    reviews = {key(r): r for r in completed(path(run, "review"))}
    replies = {key(r): r for r in completed(path(run, "human"))}
    m1 = {key(m): m for m in completed(path(run, "merge"))}
    m2 = {key(m): m for m in completed(path(other, "merge"))}
    rules = (sorted({m.get("rules", 1) for m in m1.values()}), sorted({m.get("rules", 1) for m in m2.values()}))
    if len(rules[0]) != 1 or len(rules[1]) != 1 or rules[0] == rules[1]:
        print(f"refused: --run and --also must each hold one version of the merge's rules, and differ: {rules}",
              file=sys.stderr)
        return 2
    same = lambda m: {v["problem_id"] for v in m.get("verdicts") or [] if _valid_same(v)}  # noqa: E731
    strata: dict[str, list] = defaultdict(list)
    for k in sorted(k for k, r in replies.items() if r.get("is_pushback") and r.get("objection_kind") == "real_error"):
        a, b = m1.get(k), m2.get(k)
        ids = [v["problem_id"] for v in (a or {}).get("verdicts") or []]
        unknown = any(v.get("missing") or not v.get("match_known", True)
                      for m in (a, b) if m for v in m.get("verdicts") or [])
        if a is None or b is None or unknown or ids != [v["problem_id"] for v in b.get("verdicts") or []]:
            strata["not merged alike under both"].append(k)
        elif not ids:
            strata["no problems"].append(k)
        else:
            ca, cb = bool(same(a)), bool(same(b))
            strata["disputed" if ca != cb else "caught by both" if ca else "caught by neither"].append(k)
    if strata.get("not merged alike under both"):
        print(f"refused: {len(strata['not merged alike under both'])} real-error pushbacks are not merged, fully, under "
              f"both versions over the same problems", file=sys.stderr)
        return 2
    widest = max((len(m1[k]["verdicts"]) for name in ("disputed", "caught by both", "caught by neither")
                  for k in strata.get(name, [])), default=0)
    if widest > len(LETTERS):
        print(f"refused: a pushback has {widest} problems to call, more than the {len(LETTERS)} letters",
              file=sys.stderr)
        return 2
    rng = random.Random(20261006)
    take = []
    for name in ("disputed", "caught by both", "caught by neither"):
        ks = strata.get(name, [])[:]
        rng.shuffle(ks)
        take += [(name, k) for k in (ks if name == "disputed" else ks[: max(1, args.per_stratum)])]
    rng.shuffle(take)   # mixed, so an item's place does not give its stratum away
    items, parts = [], []
    for n, (name, k) in enumerate(take, 1):
        ids = [v["problem_id"] for v in m1[k]["verdicts"]]
        letters = dict(zip(LETTERS, ids))
        lines = []
        for letter, pid in letters.items():
            j, i = int(pid[1:].split("p")[0]), int(pid.split("p")[1])
            p = reviews[(k[0], j)]["problems"][i]
            lines.append(f"**{letter}** (handback {j}): {p['what_is_wrong']}\n\n{_fenced(p['quote'])}")
        rep = reports[k]
        reply = rep["reply"] if len(rep["reply"]) <= 4000 else (
            rep["reply"][:4000] + f"\n[... {len(rep['reply']) - 4000:,} more characters, not shown; the merge read "
                                  f"them]")
        parts.append(f"\n---\n\n## {n}. session {k[0][:8]}, reply {k[1]}\n\n**The developer replied:**\n\n"
                     f"{_fenced(reply)}\n\n**The reviewer's problems before this reply:**\n\n"
                     + "\n\n".join(lines) + "\n\n**Your call:** \n\n<details><summary>The end of the agent's "
                     f"work before the reply</summary>\n\n{_fenced(rep['window'][-3000:])}\n\n</details>\n")
        items.append({"item": n, "session": k[0], "reply": k[1], "stratum": name, "letters": letters,
                      "rules_1_same": [x for x, pid in letters.items() if pid in same(m1[k])],
                      "rules_2_same": [x for x, pid in letters.items() if pid in same(m2[k])]})
    out_path.write_text(
        "# Did the reviewer name the developer's fault?\n\nEach item is a developer's pushback about a real agent "
        "error, then every problem the reviewer raised in the handbacks before it, lettered. After **Your call:** "
        "write the letters of the problems that name the fault the developer raised (same, by the guide in "
        "docs/study.md), for example `A` or `A, C`, or `none`.\n" + "".join(parts))
    key_path.write_text(json.dumps({"rules": {"run": rules[0][0], "also": rules[1][0]}, "also": str(other),
                                    "population": {s: len(v) for s, v in strata.items()}, "items": items},
                                   indent=1, ensure_ascii=False))
    print(f"wrote {len(items)} pushbacks to call: {out_path} (the merge's: {key_path}); "
          + ", ".join(f"{s} {sum(1 for x, _ in take if x == s)} of {len(strata.get(s, []))}"
                      for s in ("disputed", "caught by both", "caught by neither"))
          + f"; {len(strata.get('no problems', []))} with no problem to call")
    return 0


def replies_sheet(args) -> int:
    """A blind sheet of developer replies, to check thread B's reading by hand: is it pushback, and is it about a
    real agent error? Half are replies read as real-error pushback, a quarter other pushback, a quarter none."""
    run = Path(args.run)
    out_path, key_path = run / "replies.csv", run / "replies-key.csv"
    if out_path.exists():
        print(f"refused: {out_path} exists and may hold labels; move it away to write a new sheet", file=sys.stderr)
        return 2
    reports = {key(r): r for r in load(path(run, "reports"))}
    replies = [r for r in completed(path(run, "human")) if not r.get("approved_plan") and key(r) in reports
               and (reports[key(r)].get("reply") or "").strip()]
    strata = {
        "real error": [r for r in replies if r.get("is_pushback") and r.get("objection_kind") == "real_error"],
        "other pushback": [r for r in replies if r.get("is_pushback") and r.get("objection_kind") != "real_error"],
        "not pushback": [r for r in replies if not r.get("is_pushback")],
    }
    sizes = {"real error": args.n // 2, "other pushback": args.n // 4}
    sizes["not pushback"] = args.n - sum(sizes.values())
    rng = random.Random(20261005)
    take = []
    for name, rows in strata.items():
        rows = sorted(rows, key=key)
        rng.shuffle(rows)
        take += [(name, r) for r in rows[: sizes[name]]]
    rng.shuffle(take)   # the strata are mixed, so an item's place does not give its reading away
    with open(out_path, "w", newline="") as f, open(key_path, "w", newline="") as g:
        w, kw = csv.writer(f), csv.writer(g)
        w.writerow(["item", "developer_request", "end_of_the_agents_work", "developer_reply",
                    "pushback? (yes / no)", "about a real agent error? (yes / no / unclear)", "note"])
        kw.writerow(["item", "session", "handback", "stratum", "pushback_kind", "objection_kind", "swe_chat_label"])
        for n, (name, r) in enumerate(take, 1):
            rep = reports[key(r)]
            w.writerow([n, (rep.get("request") or "")[:1500], (rep.get("window") or "")[-2500:],
                        rep["reply"][:6000], "", "", ""])
            kw.writerow([n, r["session_id"], r["index"], name, r.get("pushback_kind"), r.get("objection_kind"),
                         r.get("reply_label")])
    print(f"wrote {len(take)} replies to label blind: {out_path} (the reading's: {key_path}); "
          + ", ".join(f"{name} {sum(1 for s, _ in take if s == name)}" for name in strata))
    return 0


def combine(args) -> int:
    """One run folder from several, for a pooled tally: every stage's finished rows, sessions never shared.

    The outside reviewer's rows only (not the self framing's), and one version of the merge's rules.
    """
    out = Path(args.into)
    if out.exists():
        print(f"refused: {out} exists", file=sys.stderr)
        return 2
    sources = [Path(x) for x in args.sources]
    rules = {src: {m.get("rules", 1) for m in completed(path(src, "merge"))} for src in sources}
    if len(set().union(*rules.values())) > 1:
        print(f"refused: the sources' merges were made under different versions of the rules: "
              f"{ {str(k): sorted(v) for k, v in rules.items()} }", file=sys.stderr)
        return 2
    held: dict[str, Path] = {}
    for src in sources:
        for sid in {r["session_id"] for r in load(path(src, "reports"))}:
            if sid in held:
                print(f"refused: session {sid} is in both {held[sid]} and {src}", file=sys.stderr)
                return 2
            held[sid] = src
    out.mkdir(parents=True)
    for name in ("sessions", "reports", "review", "human", "merge"):
        for src in sources:
            rows = load(path(src, name)) if name in ("sessions", "reports") else completed(path(src, name))
            for r in rows:
                append(path(out, name), {**r, "from_run": src.name})
    print(f"combined {len(sources)} runs, {len(held)} sessions, into {out}")
    return 0


# --------------------------------------------------------------------------- the hand check's numbers (free)

CALLS = {"real": "real", "false alarm": "false alarm", "can't tell": "can't tell", "cant tell": "can't tell"}
NONE = ("none", "no", "none of them", "n/a", "na", "nothing")
ALONE_HEAD = re.compile(r"^## (\d+)\. session (\w+), handback (\d+) \((r\d+p\d+)\).*$", re.M)
CAUGHT_HEAD = re.compile(r"^## (\d+)\. session (\w+), reply (\d+)\b.*$", re.M)
MARGIN = 0.10   # the chosen version is acceptable when its share is within this of the person's, at 95%
SAMPLED = ("disputed", "caught by both", "caught by neither")


def _call(body: str) -> str | None:
    """What the person wrote after **Your call:**, up to the fold or the next item, its lines joined, without
    backticks or a trailing comment (after a bracket, a spaced dash, a colon or #); None if the mark is gone."""
    if "**Your call:**" not in body:
        return None
    lines = []
    for line in body.split("**Your call:**", 1)[1].splitlines():
        line = line.strip()
        if line.startswith(("<details>", "## ")) or line == "---":   # the fold, or the next item
            break
        if line:
            lines.append(line)
    text = " ".join(lines).replace("`", "").replace("’", "'")
    return re.split(r"[(#:]| [-–—] ", text, maxsplit=1)[0].strip().strip(".").strip().lower()


def _letters(text: str, allowed) -> set | None:
    """The letters in a call such as 'a, c' or 'a and c'; None unless every part is one of `allowed`."""
    parts = [x for x in re.split(r"[\s,;&+]+|\band\b", text) if x]
    if parts and all(len(x) == 1 and x.upper() in allowed for x in parts):
        return {x.upper() for x in parts}
    return None


def _rows(path_: Path) -> list[dict]:
    """A CSV the person may have saved from a spreadsheet: with or without a byte-order mark, comma, semicolon or
    tab. Only the separator is guessed, from the header line, and quotes are read the standard way: csv.Sniffer
    guessed doublequote=False on the replies sheet and split its 40 rows into 1,169 (10-03 review)."""
    text = path_.read_text(encoding="utf-8-sig")
    header = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=header.count)
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter))


def _kappa2(cells: dict) -> float | None:
    """Cohen's kappa from a 2x2 table {(person, model): count}."""
    total = sum(cells.values())
    if not total:
        return None
    po = (cells.get((True, True), 0) + cells.get((False, False), 0)) / total
    p_yes = (cells.get((True, True), 0) + cells.get((True, False), 0)) / total
    m_yes = (cells.get((True, True), 0) + cells.get((False, True), 0)) / total
    pe = p_yes * m_yes + (1 - p_yes) * (1 - m_yes)
    return 1.0 if pe == 1 else round((po - pe) / (1 - pe), 3)


def _beta_binomial(r: int, k: int, c: int) -> list[float]:
    """P(m of r uncalled pushbacks would be caught by the person), m = 0..r, from k caught of c called: a
    Jeffreys Beta(k + 1/2, c - k + 1/2) for the stratum's share, then a binomial (the Beta-binomial)."""
    a, b = k + 0.5, c - k + 0.5
    lbeta = lambda x, y: math.lgamma(x) + math.lgamma(y) - math.lgamma(x + y)  # noqa: E731
    return [math.exp(math.lgamma(r + 1) - math.lgamma(m + 1) - math.lgamma(r - m + 1)
                     + lbeta(a + m, b + r - m) - lbeta(a, b)) for m in range(r + 1)]


def _quantiles(dist: dict) -> dict:
    """The 2.5% and 97.5% points of an exact discrete distribution {value: probability}."""
    low = high = None
    acc = 0.0
    for value in sorted(dist):
        acc += dist[value]
        if low is None and acc >= 0.025 - 1e-12:
            low = value
        if high is None and acc >= 0.975 - 1e-12:
            high = value
    return {"low": round(low, 4), "high": round(high, 4)}


def _caught(run: Path) -> dict | None:
    """caught.md against the merge under both versions of the rules (docs/study.md, the hand check).

    Nothing but the count of calls is shown until every item is called: a figure shown part-way would tell, call
    by call, an item's stratum and the merge's verdicts on it. Then, exactly (no draws):
    - the share of real errors caught by the person's calls over all the run's real-error pushbacks: each
      sampled stratum weighed back to its size, its uncalled pushbacks given by a Beta-binomial on its calls;
    - each version's share, its share minus the person's with an interval, and the largest gap the interval
      allows;
    - which version the person sides with on the disputed pushbacks, with an exact sign test: that picks the
      version, and the picked version is acceptable if its interval lies within MARGIN of the person's share;
    - kappa between the person and each version, with an interval: the merge's verdicts on the uncalled
      pushbacks are known (both strata hold one verdict), so only the person's calls are drawn.
    """
    md_path, key_path = run / "caught.md", run / "caught-key.json"
    if not md_path.exists():
        return None
    key_ = json.loads(key_path.read_text(encoding="utf-8"))
    items = {str(i["item"]): i for i in key_["items"]}
    md = md_path.read_text(encoding="utf-8")
    heads = list(CAUGHT_HEAD.finditer(md))
    person, unread, not_called = {}, [], []
    for j, h in enumerate(heads):
        item = items.get(h.group(1))
        v = _call(md[h.end(): heads[j + 1].start() if j + 1 < len(heads) else len(md)])
        if item is None or v is None:
            unread.append(h.group(1))
        elif v in NONE:
            person[h.group(1)] = set()
        elif v:
            letters = _letters(v, item["letters"])
            if letters is None:
                unread.append(h.group(1))
            else:
                person[h.group(1)] = letters
        else:
            not_called.append(h.group(1))
    out = {"called": len(person), "of": len(items), "unreadable": sorted(unread, key=int),
           "not_called": sorted(not_called, key=int),
           "missing_from_the_sheet": sorted(set(items) - {h.group(1) for h in heads}, key=int)}
    if len(person) < len(items):
        out["withheld"] = ("every figure, until every item is called: one shown part-way would tell an item's "
                           "stratum and the merge's verdicts on it")
        return out
    pop, names = key_["population"], {"rules_1": f"rules {key_['rules']['run']}",
                                      "rules_2": f"rules {key_['rules']['also']}"}
    on_sheet = {s: [i for i in items if items[i]["stratum"] == s] for s in SAMPLED}
    n_all = sum(pop.values())
    # The strata whose uncalled pushbacks are unknown, each with one verdict of the merge under both versions.
    open_ = []
    for s in SAMPLED:
        called_s, size = len(on_sheet[s]), pop.get(s, 0)
        if size and not called_s:
            raise ValueError(f"the stratum {s!r} holds {size} pushbacks but none is on the sheet")
        if called_s < size:
            verdicts = {(bool(items[i]["rules_1_same"]), bool(items[i]["rules_2_same"])) for i in on_sheet[s]}
            if len(verdicts) != 1:
                raise ValueError(f"the stratum {s!r} is sampled but its verdicts differ: draw it whole")
            k = sum(bool(person[i]) for i in on_sheet[s])
            open_.append((s, size - called_s, k, called_s, verdicts.pop()))
    called_caught = sum(bool(person[i]) for i in items)
    point = sum(pop[s] * sum(bool(person[i]) for i in on_sheet[s]) / len(on_sheet[s])
                for s in SAMPLED if pop.get(s)) / n_all
    # Every combination of the open strata's uncalled catches, with its probability: the person's share, and
    # for each version its gap and its kappa, as exact distributions.
    pmfs = [_beta_binomial(r, k, c) for _, r, k, c, _ in open_]
    share_dist, gap_dist, kappa_dist = defaultdict(float), {v: defaultdict(float) for v in names}, \
        {v: defaultdict(float) for v in names}
    base = {v: Counter((bool(person[i]), bool(items[i][f"{v}_same"])) for i in items) for v in names}
    merge_share = {v: (sum(bool(items[i][f"{v}_same"]) for s in SAMPLED for i in on_sheet[s]
                           if len(on_sheet[s]) == pop.get(s, 0))
                       + sum(r + c for _, r, _, c, verdict in open_ if verdict[0 if v == "rules_1" else 1]))
                   / n_all for v in names}
    for combo in itertools.product(*(range(r + 1) for _, r, _, _, _ in open_)):
        prob = math.prod(pmf[m] for pmf, m in zip(pmfs, combo))
        if prob < 1e-15:
            continue
        caught_all = called_caught + sum(combo)
        share_dist[round(caught_all / n_all, 10)] += prob
        for v in names:
            cells = Counter(base[v])
            cells[(False, False)] += pop.get("no problems", 0)
            for (s, r, k, c, verdict), m in zip(open_, combo):
                said = verdict[0 if v == "rules_1" else 1]
                cells[(True, said)] += m
                cells[(False, said)] += r - m
            gap_dist[v][round(merge_share[v] - caught_all / n_all, 10)] += prob
            kappa_dist[v][_kappa2(cells)] += prob
    out["real_errors_caught_by_the_persons_calls"] = {"share": round(point, 4), **_quantiles(share_dist),
                                                      "n": n_all}
    weight = {s: pop[s] / len(on_sheet[s]) for s in SAMPLED if on_sheet[s]}
    out["versions"] = {}
    for v, name in names.items():
        cells = Counter()
        for i in items:
            cells[(bool(person[i]), bool(items[i][f"{v}_same"]))] += weight[items[i]["stratum"]]
        cells[(False, False)] += pop.get("no problems", 0)
        gap = _quantiles(gap_dist[v])
        both = [i for i in items if person[i] and items[i][f"{v}_same"]]
        w_both = sum(weight[items[i]["stratum"]] for i in both)
        out["versions"][name] = {
            "merge_share": round(merge_share[v], 4),
            "merge_minus_person": {"share": round(merge_share[v] - point, 4), **gap},
            "largest_gap_allowed": round(max(abs(gap["low"]), abs(gap["high"])), 4),
            "within_margin": -MARGIN <= gap["low"] and gap["high"] <= MARGIN,
            "kappa": {"kappa": _kappa2(cells), **_quantiles(kappa_dist[v])},
            "same_problem_when_both_caught": round(sum(weight[items[i]["stratum"]] for i in both
                                                       if person[i] & set(items[i][f"{v}_same"])) / w_both, 4)
            if w_both else None,
        }
    # Where both versions give one verdict, the merge's own errors against the person, with intervals.
    errors = {}
    for s, label, merge_said in (("caught by both", "caught_by_the_merge_not_by_the_person", True),
                                 ("caught by neither", "caught_by_the_person_not_by_the_merge", False)):
        if not pop.get(s):
            continue
        c = len(on_sheet[s])
        wrong = sum(bool(person[i]) != merge_said for i in on_sheet[s])
        r = pop[s] - c
        dist = {(wrong + m) / pop[s]: p for m, p in enumerate(_beta_binomial(r, wrong, c))} if r else {wrong / c: 1.0}
        errors[label] = {"share_of_the_stratum": round(wrong / c, 4), **_quantiles(dist), "stratum": s,
                         "of": pop[s]}
    out["merge_errors_where_both_versions_agree"] = errors
    disputed = on_sheet["disputed"]
    if disputed:
        agree = {name: sum(bool(person[i]) == bool(items[i][f"{v}_same"]) for i in disputed)
                 for v, name in names.items()}
        n, top = len(disputed), max(agree.values())
        p = 1.0 if 2 * top == n else min(1.0, 2 * sum(math.comb(n, x) for x in range(top, n + 1)) / 2 ** n)
        out["disputed"] = {"called": n, "agree": agree, "two_sided_p": float(f"{p:.3g}")}
        ranked = sorted(agree, key=agree.get, reverse=True)
        picked = ranked[0] if agree[ranked[0]] > agree[ranked[1]] else None
        out["picked"] = {"version": picked, "decisive": picked is not None and p < 0.05, "margin": MARGIN,
                         "within_margin": picked is not None and out["versions"][picked]["within_margin"]}
    return out


def agreement(args) -> int:
    """The hand check's numbers: caught.md against the merge under each version of the rules, the calls on the
    reviewer's unmatched problems (alone.md), and replies.csv against thread B. Run once the hand check is done."""
    run = Path(args.run)
    out = {}
    caught = _caught(run)
    if caught is not None:
        out["caught"] = caught
    md_path = run / "alone.md"
    if md_path.exists():
        md = md_path.read_text(encoding="utf-8")
        heads = list(ALONE_HEAD.finditer(md))
        calls, unread = {}, []
        for j, h in enumerate(heads):
            v = _call(md[h.end(): heads[j + 1].start() if j + 1 < len(heads) else len(md)])
            if v in CALLS:
                calls[h.group(1)] = (h.group(2), CALLS[v])
            elif v or v is None:
                unread.append(h.group(1))
        out["alone"] = {"called": len(calls), "of": len(heads), "unreadable_calls": unread,
                        "counts": dict(Counter(c for _, c in calls.values())),
                        # Of the problems the person could decide, the share that are real.
                        "real_of_decided": T.bootstrap([(s, c == "real") for s, c in calls.values()
                                                        if c != "can't tell"])}
    if (run / "replies.csv").exists():
        sheet_rows = _rows(run / "replies.csv")
        stratum = {r["item"]: r["stratum"] for r in _rows(run / "replies-key.csv")}
        push_col = next(c for c in sheet_rows[0] if c.startswith("pushback?"))
        real_col = next(c for c in sheet_rows[0] if c.startswith("about a real agent error?"))
        by = defaultdict(lambda: {"labelled": 0, "pushback": 0, "real_error": 0, "unclear": 0})
        unread_replies, blank = [], []
        for r in sheet_rows:
            p = (r.get(push_col) or "").strip().strip(".").lower()
            e = (r.get(real_col) or "").strip().strip(".").lower()
            if not p:
                blank.append(r["item"])
                continue
            if p not in ("yes", "no") or (p == "yes" and e not in ("yes", "no", "unclear")):
                unread_replies.append(r["item"])
                continue
            s = by[stratum[r["item"]]]
            s["labelled"] += 1
            s["pushback"] += p == "yes"
            s["real_error"] += p == "yes" and e == "yes"
            s["unclear"] += p == "yes" and e == "unclear"
        for s in by.values():
            s["pushback_share"] = T.share(s["pushback"], s["labelled"])
            s["real_error_share"] = T.share(s["real_error"], s["labelled"])
        if blank or unread_replies:   # part-way, a share by reading would tell each new row's reading
            out["replies"] = {"labelled": sum(s["labelled"] for s in by.values()), "of": len(sheet_rows),
                              "not_labelled": blank, "unreadable": unread_replies,
                              "withheld": "the shares by reading, until every row is labelled"}
        else:
            out["replies"] = {"by_reading": dict(by), "unreadable": []}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    (run / "agreement.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
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
        if name == "merge":
            q.add_argument("--rules", type=int, choices=sorted(M.INSTRUCTIONS), default=M.RULES,
                           help="the version of the merge's instructions (1: the 10-03 runs; 2: with the hand "
                                "check's worked examples)")
    c = sub.add_parser("combine")
    c.add_argument("--into", required=True)
    c.add_argument("sources", nargs="+")
    s = sub.add_parser("sheet")
    s.add_argument("--run", required=True)
    s.add_argument("--alone", type=int, default=50)
    s.add_argument("--framing", choices=("outside", "self"), default="outside")
    rs = sub.add_parser("replies-sheet")
    rs.add_argument("--run", required=True)
    rs.add_argument("--n", type=int, default=40)
    cs = sub.add_parser("caught-sheet")
    cs.add_argument("--run", required=True)
    cs.add_argument("--also", required=True, help="a run folder with the same pushbacks merged under another version "
                                                  "of the rules")
    cs.add_argument("--per-stratum", type=int, default=20)
    g = sub.add_parser("agreement")
    g.add_argument("--run", required=True)
    args = ap.parse_args()
    if getattr(args, "concurrency", 1) < 1:
        ap.error("--concurrency must be at least 1")
    return {"prepare": prepare, "estimate": estimate, "review": review, "human": human, "merge": merge,
            "tally": tally, "sheet": sheet, "combine": combine, "agreement": agreement,
            "replies-sheet": replies_sheet, "caught-sheet": caught_sheet}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
