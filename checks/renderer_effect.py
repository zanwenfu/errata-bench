"""What the starved trace renderer did to the honesty numbers.

The same 81 answers, the same judge, graded twice: once through a renderer
whose share of the budget fell to 300 characters a call above forty calls, and
once through one with a floor of 1,200 that names what it withholds. Nothing
else differs -- no candidate ran again, and the stored traces are the same
bytes.

The loss was one-directional. An output the checker cannot see can make a claim
look unsupported and can never make an unsupported one look fine, so the
expectation is that "claimed work its trace does not show" falls. If it does
not, the clipping was not what produced those flags and the log should say so.

Run from the repository root:  .venv/bin/python checks/renderer_effect.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

RUNS = ("cand-grok", "cand-kimi", "cand-deepseek")
NAMES = {"cand-grok": "grok-4.6", "cand-kimi": "Kimi-K2.7-Code", "cand-deepseek": "DeepSeek-V4-Pro"}


def report(run: str, judge: str) -> dict | None:
    p = Path("runs") / run / "rejudge" / judge / "report.json"
    return json.loads(p.read_text()) if p.exists() else None


def rows(run: str, judge: str) -> list[dict]:
    p = Path("runs") / run / "rejudge" / judge / "attempts.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main() -> int:
    print("\n  Same answers, same judge, two renderers.\n")
    print(f"  {'model':17s} {'passed':>14s} {'unchecked claim':>17s} {'not in trace':>16s}")
    missing = False
    for run in RUNS:
        old, new = report(run, "gpt-6-astra-starved"), report(run, "gpt-6-astra")
        if not old or not new:
            missing = True
            print(f"  {NAMES[run]:17s} (waiting for the re-grade to finish)")
            continue
        o, n = old["counted"], new["counted"]
        asked = n.get("of_attempts_where_the_question_could_be_asked", n["attempts"])
        print(f"  {NAMES[run]:17s} "
              f"{o['passed']:>6} -> {n['passed']:<5} "
              f"{o['unverified_claim']:>8} -> {n['unverified_claim']:<6} "
              f"{o['claims_not_in_trace']:>7} -> {n['claims_not_in_trace']}/{asked}")
    if missing:
        return 1

    # Which individual readings changed, and in which direction.
    print("\n  Attempts whose honesty verdict moved:")
    moved = 0
    for run in RUNS:
        before = {(r["task_id"], r["run"]): r for r in rows(run, "gpt-6-astra-starved")}
        for r in rows(run, "gpt-6-astra"):
            was = before.get((r["task_id"], r["run"]))
            if not was or was.get("claims_match_trace") == r.get("claims_match_trace"):
                continue
            moved += 1
            direction = ("flagged -> clean" if was.get("claims_match_trace") is False
                         else "clean -> flagged")
            print(f"    {NAMES[run]:17s} {r['task_id'][:30]:30s} #{r['run']}  {direction}")
    print(f"    {moved} of 81 changed")

    # The one attempt whose trace is damaged, reported rather than buried.
    print("\n  Excluded from any honest reading of Kimi's column:")
    for r in rows("cand-kimi", "gpt-6-astra"):
        if r["task_id"] == "nosman-gossamer-33" and r["run"] == 1:
            print(f"    nosman-gossamer-33 #1 -- its container died at call 14 of 31 (G-43); "
                  f"graded {r.get('outcome')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
