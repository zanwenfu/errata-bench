"""Draw the README's system figure, in a light and a dark version.

    python docs/img/architecture.py        # writes architecture-{light,dark}.svg beside it

Standard library only. The numbered badges are the steps of "How it works" in the
README, and the counts are the release's (`docs/method.md`, the v1 funnel): change
one and change the other.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1126, 812
# Three columns: what feeds each row on the left, the row itself in the centre,
# what it hands on at the right.
LEFT, LEFT_W = 24, 160
MID, MID_W = 206, 714
RIGHT, RIGHT_W = 942, 160

SANS = "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

LIGHT = {
    "page": "#ffffff", "frame": "#e2e8f0", "ink": "#0f172a", "body": "#475569", "faint": "#94a3b8",
    "card": "#ffffff", "line": "#334155", "badge": "#0f172a", "badge_ink": "#ffffff",
    "build": ("#eef2ff", "#6366f1"), "run": ("#ecfdf5", "#10b981"), "grade": ("#fffbeb", "#f59e0b"),
    "source": ("#f8fafc", "#cbd5e1"), "release": ("#f0f9ff", "#0ea5e9"), "model": ("#faf5ff", "#a855f7"),
    "known": ("#fff1f2", "#e11d48"), "shadow": "0.10",
}
DARK = {
    "page": "#0d1117", "frame": "#30363d", "ink": "#f0f6fc", "body": "#b1bac4", "faint": "#6e7681",
    "card": "#161b22", "line": "#c9d1d9", "badge": "#f0f6fc", "badge_ink": "#0d1117",
    "build": ("#1b1f3a", "#818cf8"), "run": ("#0f2a22", "#34d399"), "grade": ("#2d2208", "#fbbf24"),
    "source": ("#161b22", "#30363d"), "release": ("#0c2333", "#38bdf8"), "model": ("#251535", "#c084fc"),
    "known": ("#2d1117", "#fb7185"), "shadow": "0.45",
}


class Figure:
    def __init__(self, palette: dict) -> None:
        self.p, self.out, self.heads = palette, [], {}

    # ---- primitives -------------------------------------------------------
    def add(self, markup: str) -> None:
        self.out.append(markup)

    def head(self, color: str) -> str:
        """The id of an arrowhead in this colour (`context-stroke` is not drawn everywhere)."""
        return self.heads.setdefault(color, f"head{len(self.heads)}")

    def box(self, x, y, w, h, fill, stroke, *, r=14, sw=1.5, shadow=False, dash=None) -> None:
        extra = ' filter="url(#shadow)"' if shadow else ""
        extra += f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"{extra}/>')

    def text(self, x, y, value, *, size=13, weight=400, fill=None, anchor="start", family=SANS,
             spacing=None) -> None:
        fill = fill or self.p["body"]
        extra = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
                 f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>{escape(value)}</text>')

    def lines(self, x, y, values, *, size=12.4, gap=17, **kwargs) -> None:
        for index, value in enumerate(values):
            self.text(x, y + index * gap, value, size=size, **kwargs)

    def path(self, points, *, color=None, dash=None, arrow=True, width=1.6) -> None:
        color = color or self.p["line"]
        d = "M" + " L".join(f"{x},{y}" for x, y in points)
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        extra += f' marker-end="url(#{self.head(color)})"' if arrow else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                 f'stroke-linejoin="round" stroke-linecap="round"{extra}/>')

    def badge(self, x, y, number) -> None:
        self.add(f'<circle cx="{x}" cy="{y}" r="11" fill="{self.p["badge"]}"/>')
        self.text(x, y + 4.5, str(number), size=12.5, weight=700, fill=self.p["badge_ink"], anchor="middle")

    def pill(self, right, y, label, stroke) -> None:
        width = 18 + len(label) * 7.3
        self.add(f'<rect x="{right - width}" y="{y - 13}" width="{width}" height="19" rx="9.5" fill="none" '
                 f'stroke="{stroke}" stroke-width="1.2"/>')
        self.text(right - width / 2, y + 0.5, label, size=10.5, weight=600, fill=stroke, anchor="middle",
                  spacing="0.4")

    def label(self, y, name) -> None:
        """A row's name, in small capitals."""
        self.text(LEFT, y, name, size=11, weight=700, fill=self.p["faint"], spacing="1.6")

    # ---- composite parts --------------------------------------------------
    def card(self, x, y, w, h, kind, title, body, *, dash=None) -> None:
        fill, stroke = self.p[kind]
        self.box(x, y, w, h, fill, stroke, shadow=True, dash=dash)
        self.text(x + 16, y + 28, title, size=15.5, weight=700, fill=self.p["ink"])
        self.lines(x + 16, y + 51, body)

    def section(self, y, h, kind, title, note, *, tag=None) -> None:
        fill, stroke = self.p[kind]
        self.box(MID, y, MID_W, h, fill, stroke, shadow=True)
        self.text(MID + 20, y + 30, title, size=17, weight=700, fill=self.p["ink"])
        self.text(MID + 28 + len(title) * 9.3, y + 30, note, size=12.6)
        if tag:
            self.pill(MID + MID_W - 18, y + 26, tag, stroke)

    def inner(self, x, y, w, h, stroke, title, body, *, tag=None, stat=None) -> None:
        self.box(x, y, w, h, self.p["card"], stroke, r=10, sw=1.2)
        self.text(x + 14, y + 24, title, size=14.5, weight=700, fill=self.p["ink"])
        if tag:
            self.pill(x + w - 10, y + 21, tag, stroke)
        self.lines(x + 14, y + 45, body, size=12.4, gap=17)
        if stat:
            self.text(x + 14, y + h - 14, stat, size=13, weight=700, fill=stroke)


def draw(palette: dict) -> str:
    f = Figure(palette)
    p = palette

    f.add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
          f'role="img" aria-labelledby="t d">')
    f.add('<title id="t">errata-bench: how a task is built, run and graded</title>')
    f.add('<desc id="d">Tasks are built once per release from real SWE-chat sessions: pushbacks are found, '
          'the conversation is cut and screened, the repository is rebuilt, and a task is admitted only if '
          'the judge grades its known answers correctly; 55 tasks are released, 51 of them official. An agent '
          'runs each task in a Harbor container that reaches model APIs and nothing else, and a recorder keeps '
          'every call. Outside the container, the judge reads the report against the record three times; the '
          'readings are settled by majority and scored. The known answers never enter the container.</desc>')
    f.box(6, 6, W - 12, H - 12, p["page"], p["frame"], r=20, sw=1.2)

    # ---- row 1: build the tasks ---------------------------------------------
    f.label(40, "BUILD THE TASKS")
    f.card(LEFT, 84, LEFT_W, 124, "source", "SWE-chat",
           ["5,851 real sessions", "205 repositories", "developers and", "their coding agents"])
    f.card(RIGHT, 84, RIGHT_W, 124, "release", "Release",
           ["v1.0.2, frozen", "51 graded officially", "on Hugging Face", "every file digested"])
    bfill, bstroke = p["build"]
    f.section(52, 196, "build", "Task construction", "from a real pushback to a task any agent can take",
              tag="ONCE PER RELEASE")
    gap, iw = 20, (MID_W - 32 - 3 * 20) / 4
    xs = [MID + 16 + i * (iw + gap) for i in range(4)]
    f.inner(xs[0], 96, iw, 132, bstroke, "Find",
            ["pushbacks on the", "agent's work, then", "read in full"], stat="1,040 of 2,458")
    f.inner(xs[1], 96, iw, 132, bstroke, "Cut and screen",
            ["cut before the", "faulty report; three", "gates, each read", "three times"], stat="249 pass")
    f.inner(xs[2], 96, iw, 132, bstroke, "Rebuild",
            ["the base commit,", "every edit replayed", "and compared"], stat="95 rebuilt")
    f.inner(xs[3], 96, iw, 132, bstroke, "Admit",
            ["the judge must", "grade the known", "answers right"], stat="55 tasks")
    for i, step in ((0, 2), (1, 3), (2, 4)):
        f.path([(xs[i] + iw, 178), (xs[i + 1] - 2, 178)])
        f.badge(xs[i] + iw + gap / 2, 154, step)
    f.path([(LEFT + LEFT_W, 146), (MID - 2, 146)])
    f.badge((LEFT + LEFT_W + MID) / 2, 126, 1)
    f.path([(MID + MID_W, 146), (RIGHT - 2, 146)])
    f.badge((MID + MID_W + RIGHT) / 2, 126, 5)

    # ---- row 2: run an agent ------------------------------------------------
    f.label(312, "RUN AN AGENT")
    rfill, rstroke = p["run"]
    f.section(324, 180, "run", "Harbor task container", "one per attempt, its network sealed",
              tag="MODEL APIS ONLY")
    iw3 = (MID_W - 32 - 2 * 20) / 3
    rx = [MID + 16 + i * (iw3 + 20) for i in range(3)]
    f.inner(rx[0], 368, iw3, 116, rstroke, "The task",
            ["the repository at the cut", "the conversation so far", "nothing says what is wrong"])
    f.inner(rx[1], 368, iw3, 116, rstroke, "Any agent",
            ["Claude Code, Codex, or the", "reference agent, with its", "own tools; web search off"],
            tag="3 ATTEMPTS")
    f.inner(rx[2], 368, iw3, 116, rstroke, "Recorder",
            ["every call, its output whole", "the diff, the final report", "the instruction's digest"],
            tag="NO GRADING")
    f.path([(rx[0] + iw3, 430), (rx[1] - 2, 430)])
    f.path([(rx[1] + iw3, 430), (rx[2] - 2, 430)])
    f.card(LEFT, 352, LEFT_W, 124, "model", "Model APIs",
           ["the agent's model;", "the only hosts", "the container", "can reach"])
    f.path([(MID - 2, 430), (LEFT + LEFT_W + 2, 430)], color=p["model"][1])
    kfill, kstroke = p["known"]
    f.card(RIGHT, 352, RIGHT_W, 124, "known", "Known answers",
           ["the rejected reply", "and the resolving", "one; never in", "the container"], dash="5 4")

    # the release hands each task to the container (6), and its known answers
    # to grading, around the container
    task_x = rx[0] + 60
    f.path([(RIGHT + 50, 208), (RIGHT + 50, 276), (task_x, 276), (task_x, 322)])
    f.badge(task_x + 80, 276, 6)
    f.text(task_x + 98, 270, "the task", size=11.5)
    f.path([(RIGHT + 120, 208), (RIGHT + 120, 350)], color=kstroke, dash="5 4")

    # ---- row 3: grade and score ---------------------------------------------
    f.label(584, "GRADE AND SCORE")
    gfill, gstroke = p["grade"]
    f.section(596, 180, "grade", "Grading", "each answer read against the attempt's whole record",
              tag="OUTSIDE THE CONTAINER")
    gx = [MID + 16 + i * (iw3 + 20) for i in range(3)]
    f.inner(gx[0], 640, iw3, 116, gstroke, "Judge",
            ["gpt-6-astra, admitted on", "each task; each verdict", "quotes the report"], tag="3 READINGS")
    f.inner(gx[1], 640, iw3, 116, gstroke, "Settle",
            ["a reading whose quote is", "not in the report does not", "vote; the majority decides"],
            tag="MAJORITY")
    f.inner(gx[2], 640, iw3, 116, gstroke, "Score",
            ["honest, fixed, and both", "per task, then over tasks", "95% intervals"])
    f.path([(gx[0] + iw3, 702), (gx[1] - 2, 702)])
    f.path([(gx[1] + iw3, 702), (gx[2] - 2, 702)])
    f.card(LEFT, 624, LEFT_W, 124, "source", "Trace check",
           ["a second grader:", "each claimed action", "looked up in the", "record; a diagnostic"])
    f.path([(MID - 2, 702), (LEFT + LEFT_W + 2, 702)], color=p["faint"], dash="4 4")
    f.card(RIGHT, 624, RIGHT_W, 124, "source", "Leaderboard",
           ["a comparison rule", "registered before", "it was computed;", "15 pairs of models"])
    f.path([(MID + MID_W, 702), (RIGHT - 2, 702)])

    # the recorder's report and record go to the judge (7); the known answers
    # come around the container to the judge too
    judge_x = gx[0] + 70
    rec_x = rx[2] + 90
    f.path([(rec_x, 484), (rec_x, 532), (judge_x, 532), (judge_x, 594)])
    f.badge(rec_x - 120, 532, 7)
    f.text(rec_x - 102, 526, "report and record", size=11.5)
    f.path([(RIGHT + 120, 476), (RIGHT + 120, 556), (judge_x + 60, 556), (judge_x + 60, 594)],
           color=kstroke, dash="5 4")
    f.text(RIGHT + 112, 550, "the known answers, kept apart", size=11.5, fill=kstroke, anchor="end")

    heads = "".join(
        f'<marker id="{name}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7.5" markerHeight="7.5" '
        f'orient="auto-start-reverse"><path d="M0,0.8 L9,5 L0,9.2 z" fill="{color}"/></marker>'
        for color, name in f.heads.items())
    defs = (f'<defs>{heads}<filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">'
            f'<feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="#000000" '
            f'flood-opacity="{p["shadow"]}"/></filter></defs>')
    f.out.insert(3, defs)  # after <svg>, <title> and <desc>
    f.add("</svg>")
    return "\n".join(f.out) + "\n"


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, palette in (("light", LIGHT), ("dark", DARK)):
        (here / f"architecture-{name}.svg").write_text(draw(palette), encoding="utf-8")


if __name__ == "__main__":
    main()
