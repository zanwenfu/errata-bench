"""The study's two figures, drawn as SVG from the run folders (docs/paper-draft.md). No model call.

    .venv/bin/python scripts/study_figures.py

Writes docs/img/study-arms.svg (real errors caught, by arm) and docs/img/study-modes.svg
(real errors caught, by kind of pushback and failure mode, under each version of the merge's
rules). Every share has its 95% interval from resampling whole sessions, as in the tally.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

_spec = importlib.util.spec_from_file_location("study", Path(__file__).resolve().parent / "study.py")
study = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(study)
T = study.T

RUNS = Path("runs")
OUT = Path("docs/img")
INK, MUTED, RULE, ACCENT, SECOND = "#1f2328", "#6e7781", "#d0d7de", "#0969da", "#bf3989"


def rows(run: str, framing: str = "outside"):
    """(replies, merges) of a run folder, the merges of the given framing."""
    d = RUNS / run
    replies = {study.key(r): r for r in study.completed(study.path(d, "human"))}
    merges = {study.key(r): r for r in study.completed(study.path(d, "merge", framing))}
    return replies, merges


def caught(replies, merges, keep=lambda r: True) -> dict:
    """The share of merged real-error pushbacks (passing `keep`) with a valid 'same', over sessions."""
    items = []
    for k, r in replies.items():
        if not (r.get("is_pushback") and r.get("objection_kind") == "real_error" and keep(r)):
            continue
        m = merges.get(k)
        if m is None:
            continue
        vs = m.get("verdicts") or []
        if any(v.get("missing") or not v.get("match_known", True) for v in vs) and not any(
                study._valid_same(v) for v in vs):
            continue
        items.append((k[0], any(study._valid_same(v) for v in vs)))
    return T.bootstrap(items)


def forest(path: Path, title: str, groups: list[tuple[str, list[tuple[str, list[tuple[str, dict, str]]]]]]):
    """A dot-and-interval chart: groups of rows, each row one or more (label, share, colour) marks."""
    width, left, right, top, row_h, gap = 760, 300, 48, 60, 26, 22
    n_rows = sum(len(g[1]) for g in groups)
    height = top + n_rows * row_h + len(groups) * gap + 48
    plot_w = width - left - right
    x = lambda v: left + v * plot_w
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif" font-size="13">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
           f'<text x="12" y="26" fill="{INK}" font-size="15" font-weight="600">{escape(title)}</text>']
    for t in range(0, 101, 20):
        out.append(f'<line x1="{x(t / 100):.1f}" y1="{top - 8}" x2="{x(t / 100):.1f}" y2="{height - 40}" '
                   f'stroke="{RULE}" stroke-width="1"/>')
        out.append(f'<text x="{x(t / 100):.1f}" y="{height - 22}" fill="{MUTED}" text-anchor="middle">{t}%</text>')
    y = top
    for name, items in groups:
        out.append(f'<text x="12" y="{y + 6}" fill="{MUTED}" font-size="12" font-weight="600">{escape(name)}</text>')
        y += gap
        for label, marks in items:
            out.append(f'<text x="{left - 12}" y="{y + 5}" fill="{INK}" text-anchor="end">{escape(label)}</text>')
            for j, (what, b, colour) in enumerate(marks):
                if not b.get("n") or b.get("share") is None:
                    continue
                dy = (j - (len(marks) - 1) / 2) * 7
                if b.get("low") is not None:
                    out.append(f'<line x1="{x(b["low"]):.1f}" y1="{y + dy:.1f}" x2="{x(b["high"]):.1f}" '
                               f'y2="{y + dy:.1f}" stroke="{colour}" stroke-width="2"/>')
                out.append(f'<circle cx="{x(b["share"]):.1f}" cy="{y + dy:.1f}" r="4.5" fill="{colour}">'
                           f'<title>{escape(what)}: {b["share"]:.0%}, n = {b["n"]}</title></circle>')
            n = max((b.get("n") or 0) for _, b, _ in marks)
            out.append(f'<text x="{width - right + 6}" y="{y + 5}" fill="{MUTED}" font-size="11">n={n}</text>')
            y += row_h
    out.append("</svg>")
    path.write_text("\n".join(out) + "\n")
    print(f"wrote {path}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pooled = rows("study-pushback-120")
    rules2 = rows("study-pushback-120-rules2")
    pilot = rows("study-pilot")
    self_ = rows("study-pilot", "self")
    sol = rows("study-pilot-sol")
    deepseek = rows("study-pilot-deepseek")
    random_ = rows("study-random")
    rules1_mark = lambda b: ("strict rules (version 1)", b, ACCENT)
    forest(OUT / "study-arms.svg", "Real-error pushbacks the reviewer caught, by arm (95% intervals over sessions)", [
        ("120 SESSIONS DRAWN FOR PUSHBACK", [
            ("gpt-6-astra, strict rules", [rules1_mark(caught(*pooled))]),
            ("gpt-6-astra, claim-level rules", [("claim-level rules (version 2)", caught(*rules2), SECOND)]),
        ]),
        ("PILOT, 40 SESSIONS (STRICT RULES)", [
            ("gpt-6-astra", [rules1_mark(caught(*pilot))]),
            ("gpt-6-astra, told the work is its own", [rules1_mark(caught(*self_))]),
            ("gpt-6-sol reviewing", [rules1_mark(caught(*sol))]),
            ("DeepSeek-V4-Pro reviewing", [rules1_mark(caught(*deepseek))]),
        ]),
        ("40 SESSIONS DRAWN AT RANDOM", [
            ("gpt-6-astra, strict rules", [rules1_mark(caught(*random_))]),
        ]),
    ])
    groups = []
    for field, many, name in (("pushback_kind", False, "BY KIND OF PUSHBACK"),
                              ("failure_modes", True, "BY FAILURE MODE OF THE ERROR")):
        values = defaultdict(int)
        for r in pooled[0].values():
            if r.get("is_pushback") and r.get("objection_kind") == "real_error":
                for g in ((r.get(field) or []) if many else [r.get(field)]):
                    values[g] += 1
        items = []
        for g, n in sorted(values.items(), key=lambda kv: -kv[1]):
            if n < 15 or g in ("other",):
                continue
            keep = (lambda r, g=g: g in (r.get(field) or [])) if many else (lambda r, g=g: r.get(field) == g)
            items.append((g.replace("_", " "), [rules1_mark(caught(*pooled, keep=keep)),
                                                ("claim-level rules (version 2)", caught(*rules2, keep=keep), SECOND)]))
        groups.append((name, items))
    forest(OUT / "study-modes.svg",
           "Real errors caught, strict (blue) and claim-level (pink) rules; 120 sessions", groups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
