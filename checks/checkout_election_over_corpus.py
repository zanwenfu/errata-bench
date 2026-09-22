"""What the checkout election would elect, over every edit call in the corpus.

G-55 records a rule nobody has tested and a sentence saying why: *"It is not to
be implemented without the corpus harness the rewrites were judged by."* This
is that harness. It is a measurement, not a guard -- like
`checks/oracle_over_real_runs.py` it is run deliberately, prints numbers, and
exits 0 whatever it finds. It changes nothing and writes nothing outside its
cache directory.

    .venv/bin/python checks/checkout_election_over_corpus.py            # ~7 min
    .venv/bin/python checks/checkout_election_over_corpus.py --limit 300  # ~30s

WHAT IT MEASURES

`_checkout_root` elects, from the absolute paths in one session's recorded edit
calls, the prefix that was the developer's checkout. The election is a function
of two things: the paths, and which of their suffixes exist in the base tree.
Both can be reconstructed from the corpus, so every candidate election can be
run against all 4,452 sessions that hold an edit call without cloning a single
repository.

  THE PATHS come from `conversations.parquet`: every `tool_use` row whose
  `tool_name` is Write, Edit or MultiEdit. The path is parsed out of `content`
  as JSON, which is what `edits_before` does in production -- not read from the
  `file_path` column, which is null on all 134 MultiEdit rows.

  THE TREE is synthesised. Production checks out the base commit; here a base
  tree is built out of real files at real repo-relative paths: the repository's
  file universe (every path in `commits.files_changed` for that repo, plus every
  path in `sessions.files_touched` for its sessions), minus the files this
  session created. Only the suffixes of the session's own recorded paths are
  materialised, because those are the only paths the election ever stats --
  which makes the synthetic tree answer identically to a full one for every
  question `_checkout_root` and `_target` ask it, at a few files per session.

  THE ANSWER comes from `sessions.files_touched`: the repo-relative paths the
  session's commits touched. If a recorded absolute path ends with one of them,
  the part in front is the developer's checkout, measured rather than guessed.
  A session whose paths all agree on one such prefix has a ground truth; one
  where two paths imply different prefixes is counted `ambiguous` and left out
  of every score, because the harness cannot say which is right.

WHAT IT CANNOT SEE, stated plainly because it bounds every number below:

  - The universe is a union over the repository's whole recorded history, so a
    file created after the base commit can appear in a session's base tree.
    `--universe narrow` drops commits and keeps only `files_touched`; the
    SENSITIVITY section runs both over a sample and counts the elections that
    move. If that count is large the numbers here are soft, and it says so.
  - `files_touched` is what the session *committed*. Edits that were never
    committed have no ground truth, and sessions with none are skipped.
  - The election is scored; the replay is not. Whether an `old_string` still
    matches at the elected path is G-37's question, not this one.

ADDING A CANDIDATE: write a function taking (tree, calls, repo_id) that returns
a root tuple or None, and add it to the dict `make_candidates` returns. `calls`
are Call records carrying the tool, the path, and what the tool result said
happened -- updated, created, error or unknown -- which is what G-55's proposed
rule needs and the tree cannot supply.

A NOTE ON SPEED: `_inside` resolves two paths per call and the election asks it
once per suffix per path -- 771k lstats per fifty sessions, and three quarters
of an hour for the corpus. So the production `_inside` is wrapped in a
per-session memo that delegates to it on a miss. The tree is fixed while a
session is scored, so the memo cannot change an answer. `--no-memo` turns it
off: over the first 300 sessions the two runs print the same page, and it takes
187 seconds instead of 24.
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, "src")

CORPUS = Path("data/swe-chat")
EDIT_TOOLS = ("Write", "Edit", "MultiEdit")


# --------------------------------------------------------------------------
# the corpus, read once and cached
# --------------------------------------------------------------------------
def extract(cache: Path) -> Path:
    """Every edit call and its tool result, pulled out of the 1.3 GB parquet.

    Kept: the session, the turn, the tool, the path as production parses it,
    what the result said happened -- classified here, from the whole result
    text, because the phrase that carries it sits behind an absolute path and
    fell off the end when this stored a fixed-length head (12,888 results
    landed in "unknown" that way, four fifths of them plain successes) -- and a
    90-character head, so the cache can be read by eye. Dropped: the file
    bodies, which are the reason the column is 1.3 GB.

    The cache name carries CLASSIFIER, so editing `classify` invalidates it
    rather than silently reusing verdicts the old rules produced.
    """
    out = cache / f"edit_calls.{CLASSIFIER}.jsonl"
    if out.exists():
        return out
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    cols = ["session_id", "turn_number", "turn_type", "tool_name", "tool_call_id",
            "file_path", "content"]
    data = ds.dataset(CORPUS / "conversations.parquet", format="parquet")
    tmp = out.with_suffix(".partial")
    with tmp.open("w") as fh:
        for batch in data.to_batches(columns=cols,
                                     filter=pc.field("tool_name").isin(list(EDIT_TOOLS)),
                                     batch_size=20_000):
            if not batch.num_rows:
                continue
            col = {k: batch.column(k).to_pylist() for k in cols}
            for i in range(batch.num_rows):
                row = {"s": col["session_id"][i], "t": col["turn_number"][i],
                       "k": col["turn_type"][i], "tool": col["tool_name"][i],
                       "id": col["tool_call_id"][i]}
                body = col["content"][i] or ""
                if row["k"] == "tool_use":
                    # Production reads the path out of `content`, so this does
                    # too; `file_path` is a fallback for the rows it is null on.
                    try:
                        args = json.loads(body)
                    except (ValueError, TypeError):
                        args = None
                    row["path"] = (args.get("file_path") if isinstance(args, dict) else None) \
                        or col["file_path"][i] or ""
                    row["parsed"] = isinstance(args, dict)
                else:
                    row["out"] = classify(col["tool_name"][i], body)
                    row["head"] = body[:90]
                fh.write(json.dumps(row) + "\n")
    tmp.rename(out)
    return out


@dataclass
class Call:
    """One recorded edit call, and what its tool result said happened."""
    turn: int
    tool: str
    path: str
    outcome: str  # updated | created | error | unknown


CLASSIFIER = "v2"


def classify(tool: str, result: str | None) -> str:
    """What the agent's own tool said it did.

    This is the evidence G-55's rule turns on and it is not in the tree: a
    Write that says "File created successfully at" proves its path pointed at
    *nothing*, so any tree file its suffix happens to match is a different
    file, while "has been updated" proves the file was already there.
    """
    if result is None:
        return "unknown"
    if result.startswith("<tool_use_error>") or "tool_use_error" in result[:40]:
        return "error"
    if result.startswith("File created successfully at"):
        return "created"
    if result.startswith("The file ") and "has been updated" in result:
        return "updated"
    if tool == "MultiEdit" and result.startswith("Applied "):
        return "updated"
    return "unknown"


def load_calls(cache: Path) -> dict[str, list[Call]]:
    rows = extract(cache)
    uses: dict[str, list[dict]] = collections.defaultdict(list)
    results: dict[str, str] = {}
    unparsed = 0
    for line in rows.open():
        r = json.loads(line)
        if r["k"] == "tool_use":
            uses[r["s"]].append(r)
            unparsed += 0 if r.get("parsed") else 1
        else:
            results[r["id"]] = r["out"]
    out: dict[str, list[Call]] = {}
    for sid, rs in uses.items():
        rs.sort(key=lambda r: (r["t"] or 0))
        out[sid] = [Call(turn=r["t"] or 0, tool=r["tool"], path=r["path"] or "",
                         outcome=results.get(r["id"], "unknown"))
                    for r in rs]
    if unparsed:
        print(f"  note: {unparsed} edit calls have content that is not a JSON object")
    return out


def load_sessions() -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """repo_id and committed files per session, from the authoritative table.

    `sessions.parquet`, not the `repo_id` denormalised onto conversations: they
    disagree for 397 of 5,785 sessions and pair a repo with its fork, which
    would hand this harness the wrong file universe and the wrong answer.
    """
    import pyarrow.parquet as pq

    t = pq.read_table(CORPUS / "sessions.parquet",
                      columns=["session_id", "repo_id", "files_touched"])
    repo, touched = {}, {}
    for sid, rid, ft in zip(t.column("session_id").to_pylist(),
                            t.column("repo_id").to_pylist(),
                            t.column("files_touched").to_pylist()):
        repo[sid] = rid or ""
        try:
            touched[sid] = tuple(json.loads(ft) or [])
        except (ValueError, TypeError):
            touched[sid] = ()
    return repo, touched


def load_universe(repo: dict[str, str], touched: dict[str, tuple[str, ...]],
                  *, narrow: bool) -> dict[str, set[str]]:
    """Every repo-relative path each repository is known to have held."""
    import pyarrow.dataset as ds

    uni: dict[str, set[str]] = collections.defaultdict(set)
    if not narrow:
        data = ds.dataset(CORPUS / "commits.parquet", format="parquet")
        for batch in data.to_batches(columns=["repo_id", "files_changed", "status"],
                                     batch_size=2_000):
            for rid, files, status in zip(batch.column("repo_id").to_pylist(),
                                          batch.column("files_changed").to_pylist(),
                                          batch.column("status").to_pylist()):
                if status != "ok" or not files:
                    continue
                for line in files.split("\n"):
                    if "\t" in line:
                        uni[rid].add(line.split("\t")[-1].strip())
    for sid, files in touched.items():
        rid = repo.get(sid)
        if rid:
            uni[rid].update(files)
    return uni


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def parts_of(path: str) -> tuple[str, ...]:
    return tuple(PurePosixPath(path).parts)


def splits(path: str) -> list[tuple[tuple[str, ...], str]]:
    """Every (prefix, suffix) the election can consider, longest suffix first."""
    parts = parts_of(path)
    return [(parts[:i], "/".join(parts[i:])) for i in range(len(parts))]


def render(root) -> str:
    """A root tuple as the path it is: ('/', 'Users', 'x') is /Users/x."""
    if root is None:
        return "(elected nothing)"
    return str(PurePosixPath(*root)) if root else "(the whole path)"


def shape(path: str) -> str:
    """The census bucket a recorded path falls in."""
    if not path:
        return "empty"
    if path.startswith("/"):
        return "posix-absolute"
    if len(path) > 2 and path[1] == ":":
        return "windows-absolute"
    if path.startswith("REDACTED"):
        return "redacted"
    if path.startswith(("..", "./", "~")):
        return "dotted"
    return "repo-relative"


# --------------------------------------------------------------------------
# the candidates
# --------------------------------------------------------------------------
def make_candidates(edits):
    """The elections to compare, all against the same trees.

    Each takes (tree, calls, repo_id) and returns a root tuple or None, where
    None means "elect nothing", which sends production to the repository's
    name and the leftmost match. `original` is the production function itself,
    imported and called -- not a copy of it. If it is changed, this harness
    scores the change.
    """
    from errata_bench.corpus.timeline import to_repo_relative

    inside = edits._inside

    def vote_first(tree, path, votes):
        """The original's inner loop: the longest suffix that exists, once."""
        for prefix, rel in splits(path):
            if inside(tree, rel) and (tree / rel).exists():
                votes[prefix] = votes.get(prefix, 0) + 1
                return True
        return False

    def rewrite1(tree, calls, repo_id):
        """09-20, rewrite 1: every suffix that exists votes; longer root wins."""
        votes: dict[tuple[str, ...], int] = {}
        for c in calls:
            for prefix, rel in splits(c.path):
                if inside(tree, rel) and (tree / rel).exists():
                    votes[prefix] = votes.get(prefix, 0) + 1
        if not votes:
            return None
        return max(votes.items(), key=lambda kv: (kv[1], len(kv[0])))[0]

    def rewrite2(tree, calls, repo_id):
        """09-20, rewrite 2: only Edit votes, and a tie elects nothing."""
        votes: dict[tuple[str, ...], int] = {}
        for c in calls:
            if c.tool == "Edit":
                vote_first(tree, c.path, votes)
        if not votes:
            return None
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        return ranked[0][0]

    def evidence(tree, calls, *, strict, longer_tie):
        """G-55's rule: a Write that created its file is not evidence.

        `strict` also silences a call whose result the corpus does not hold;
        lenient lets it vote, which is what the original does today.
        """
        votes: dict[tuple[str, ...], int] = {}
        for c in calls:
            if c.outcome in ("created", "error"):
                continue
            if strict and c.outcome != "updated":
                continue
            vote_first(tree, c.path, votes)
        if not votes:
            return None
        key = (lambda kv: (kv[1], len(kv[0]))) if longer_tie else \
              (lambda kv: (kv[1], -len(kv[0])))
        return max(votes.items(), key=key)[0]

    def original(tree, calls, repo_id):
        return edits._checkout_root(tree, [c.path for c in calls])

    def proposed_then_original(tree, calls, repo_id):
        """The rule, with the original as its own fallback rather than None.

        Worth measuring separately: silencing the creating Writes leaves some
        sessions with no voter at all, and whether that should become "elect
        nothing" (and fall through to the repository's name, which is wrong in
        hundreds of sessions) or "fall back to counting everything" is the
        whole difference between rewrite 2's failure and a usable rule.
        """
        root = evidence(tree, calls, strict=False, longer_tie=True)
        return root if root is not None else original(tree, calls, repo_id)

    def proposed_then_name(tree, calls, repo_id):
        """The rule, then the repository's name, then the original.

        Electing nothing is only safe where the fallback can do the work, and
        whether it can is checkable before the fact: if every recorded path
        holds the repository's name, `_target` will place them all. Where it
        does not -- a renamed checkout, a worktree, `light-protocol3` -- a
        silenced Write becomes a rejected task, which is what costs the bare
        rule six sessions, so the original election is better than nothing
        there. This is the only candidate that is ahead of the original on
        every column at once.
        """
        root = evidence(tree, calls, strict=False, longer_tie=True)
        if root is not None:
            return root
        if all(to_repo_relative(c.path, repo_id) for c in calls if c.path):
            return None
        return original(tree, calls, repo_id)

    return {
        "original": original,
        "rewrite1-longer-tie": rewrite1,
        "rewrite2-edit-only": rewrite2,
        "proposed": lambda tree, calls, rid: evidence(tree, calls, strict=False,
                                                      longer_tie=False),
        "proposed+longer-tie": lambda tree, calls, rid: evidence(tree, calls, strict=False,
                                                                 longer_tie=True),
        "proposed-strict": lambda tree, calls, rid: evidence(tree, calls, strict=True,
                                                             longer_tie=False),
        "proposed-then-original": proposed_then_original,
        "proposed-then-name": proposed_then_name,
    }


# --------------------------------------------------------------------------
# the answer, per session
# --------------------------------------------------------------------------
def truth_for(calls: list[Call], committed: set[str]):
    """The checkout prefix the session's own commits imply, and the per-path map.

    A recorded path that ends with a committed repo-relative path pins the
    prefix in front of it. Every such path must pin the same one; if two
    disagree the session has no usable answer and is reported as ambiguous
    rather than scored against a guess.
    """
    per_path: dict[str, str] = {}
    roots: collections.Counter = collections.Counter()
    for c in calls:
        if not c.path or c.path in per_path:
            continue
        hits = [t for t in committed if c.path.endswith("/" + t) or c.path == t]
        if not hits:
            continue
        best = max(hits, key=len)
        per_path[c.path] = best
        roots[parts_of(c.path)[:len(parts_of(c.path)) - len(parts_of(best))]] += 1
    if not roots:
        return None, per_path
    if len(roots) > 1:
        return "ambiguous", per_path
    return next(iter(roots)), per_path


def build_tree(root: Path, calls: list[Call], universe: set[str],
               per_path: dict[str, str]) -> None:
    """The base commit, as far as this session's paths can tell.

    Only the suffixes the election will stat are materialised. Files the
    session created are left out: they are not in the base commit, and putting
    them in would erase the exact phenomenon G-55's rule is about.
    """
    born = {per_path[c.path] for c in calls
            if c.outcome == "created" and c.path in per_path}
    want = set()
    for c in calls:
        for _prefix, rel in splits(c.path):
            if rel in universe and rel not in born:
                want.add(rel)
    for rel in want:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")


def landings(edits, tree: Path, calls: list[Call], repo_id: str,
             root: tuple[str, ...] | None, per_path: dict[str, str]) -> list[str]:
    """Where each call's file would actually land, through production `_target`.

    The election is only half the story: when it elects nothing, production
    falls through to the repository's name and the leftmost match. This scores
    the whole path from recorded string to file on disk, which is what a
    replayed task is built out of, and it separates the two ways of being
    wrong. Landing on a path the tree does not hold leaves a stray file, and
    the next Edit to it fails loudly. Landing on a file that *is* there
    overwrites a real one and nothing in the run ever says so -- that is the
    shape G-55 is about, and counting both as "wrong" hides it.
    """
    out: list[str] = []
    resolved = tree.resolve()
    for c in calls:
        want = per_path.get(c.path)
        if want is None:
            continue
        target = edits._target(tree, c.path, repo_id, root)
        if target is None:
            out.append("rejected")
        elif str(target.relative_to(resolved)) == want:
            out.append("right")
        elif target.exists():
            out.append("wrong, onto a real file")
        else:
            out.append("wrong, onto nothing")
    return out


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def census(by_session: dict[str, list[Call]]) -> None:
    """The numbers G-55 cites, so they can be checked rather than believed."""
    calls = [c for cs in by_session.values() for c in cs]
    print(f"  edit calls                          {len(calls):7d}   (G-55 says 73,549)")
    print(f"  sessions holding one                {len(by_session):7d}")
    per_tool = collections.Counter(c.tool for c in calls)
    for tool in EDIT_TOOLS:
        print(f"    {tool:10s}                      {per_tool[tool]:7d}")
    opens_write = sum(1 for cs in by_session.values() if cs and cs[0].tool == "Write")
    tied = sum(1 for cs in by_session.values()
               if cs and any(c.tool == "Write" for c in cs if c.turn == cs[0].turn))
    print(f"  sessions opening with a Write       {opens_write:7d}   (G-55 says 1,321)")
    print(f"    ... counting a tied first turn    {tied:7d}")
    shapes = collections.Counter(shape(c.path) for c in calls)
    print("  path shapes, over calls and over the sessions that hold one:")
    for name, n in shapes.most_common():
        holders = len({s for s, cs in by_session.items() if any(shape(c.path) == name for c in cs)})
        note = "   (G-55 says 9 sessions)" if name == "repo-relative" else ""
        print(f"    {name:18s}              {n:7d}  in {holders:5d} sessions{note}")
    outcomes = collections.Counter(c.outcome for c in calls)
    print("  what the agent's own tool result said happened:")
    for name, n in outcomes.most_common():
        print(f"    {name:18s}              {n:7d}")
    writes = collections.Counter(c.outcome for c in calls if c.tool == "Write")
    print(f"  of the Writes: {writes['created']} created a file, {writes['updated']} "
          f"overwrote an existing one, {writes['error']} failed, {writes['unknown']} unknown")


def leftmost_disagreement(by_session, repo, touched) -> None:
    """How often the fallback -- the repository's name -- lands somewhere else.

    Counted three ways, because the one number G-55 records (675) is
    reproducible under none of them exactly and the definition is what moves
    it: a path the fallback maps to the wrong place is a silently misplaced
    file, while a path it cannot map at all is a rejected task.
    """
    from errata_bench.corpus.timeline import to_repo_relative

    wrong = none = either = scored = 0
    for sid, calls in by_session.items():
        rid = repo.get(sid)
        committed = set(touched.get(sid) or ())
        if not rid or not committed:
            continue
        _root, per_path = truth_for(calls, committed)
        if not per_path:
            continue
        scored += 1
        w = n = False
        for path, want in per_path.items():
            got = to_repo_relative(path, rid)
            if got is None:
                n = True
            elif got != want:
                w = True
        wrong += w
        none += n
        either += (w or n)
    print(f"  sessions with at least one committed path        {scored:7d}")
    print(f"  ... fallback maps a path to the wrong place      {wrong:7d}")
    print(f"  ... fallback cannot map a path at all            {none:7d}")
    print(f"  ... either                                       {either:7d}   (G-55 says 675)")


def fate(verdicts: list[str]) -> str:
    """What becomes of the whole session, which is what a task is.

    One call is enough to decide it. A path production cannot place stops the
    replay and rejects the task, which is a loss anyone can see in the
    rejection file. A path it places on the wrong existing file ships a tree
    that is half one thing and half another, and says nothing -- which is the
    failure `construct/edits.py` was written to prevent, so it is counted apart
    from the harmless-looking stray file.
    """
    if "rejected" in verdicts:
        return "rejected"
    if "wrong, onto a real file" in verdicts:
        return "silently overwrote a file"
    if "wrong, onto nothing" in verdicts:
        return "left a stray file"
    return "clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="score only the first N sessions")
    ap.add_argument("--cache", default=str(Path(tempfile.gettempdir()) / "errata-g55-cache"))
    ap.add_argument("--universe", choices=("broad", "narrow"), default="broad")
    ap.add_argument("--sens", type=int, default=400,
                    help="sessions to re-score under the other universe (0 to skip)")
    ap.add_argument("--examples", type=int, default=20,
                    help="how many sessions to print in full under each heading")
    ap.add_argument("--no-memo", action="store_true", help="do not memoise _inside")
    args = ap.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    import errata_bench.construct.edits as edits

    started = time.time()
    print("=" * 72)
    print("CENSUS: what is in the corpus")
    print("=" * 72)
    by_session = load_calls(cache)
    repo, touched = load_sessions()
    census(by_session)

    print()
    print("=" * 72)
    print("THE FALLBACK: the repository's name, against the files committed")
    print("=" * 72)
    leftmost_disagreement(by_session, repo, touched)

    universes = {args.universe: load_universe(repo, touched, narrow=args.universe == "narrow")}
    order = sorted(by_session)
    if args.limit:
        order = order[:args.limit]

    if not args.no_memo:
        real_inside = edits._inside
        memo: dict[tuple[str, str], Path | None] = {}

        def memoised(tree, rel, _real=real_inside, _memo=memo):
            key = (str(tree), rel)
            if key not in _memo:
                _memo[key] = _real(tree, rel)
            return _memo[key]

        edits._inside = memoised
    else:
        memo = None

    candidates = make_candidates(edits)
    elected_by = {name: collections.Counter() for name in candidates}
    landed_by = {name: collections.Counter() for name in candidates}
    moves = {name: collections.Counter() for name in candidates}
    fate_by = {name: collections.Counter() for name in candidates}
    fate_moves = {name: collections.Counter() for name in candidates}
    overwritten = {name: [] for name in candidates}
    skipped = collections.Counter()
    misplaced: list[str] = []   # the original elected a root, and it is wrong
    differ: list[str] = []      # the proposed rule elects something else
    n_differ = 0
    sens_rows: dict[str, tuple] = {}

    work = Path(tempfile.mkdtemp(prefix="errata-g55-trees-"))
    try:
        for n, sid in enumerate(order):
            calls = by_session[sid]
            rid = repo.get(sid)
            committed = set(touched.get(sid) or ())
            if not rid:
                skipped["session not in sessions.parquet"] += 1
                continue
            if not committed:
                skipped["session committed nothing"] += 1
                continue
            true_root, per_path = truth_for(calls, committed)
            if true_root is None:
                skipped["no recorded path ends in a committed file"] += 1
                continue
            if true_root == "ambiguous":
                skipped["two paths imply different checkouts"] += 1
                continue

            tree = work / "tree"
            if tree.exists():
                shutil.rmtree(tree)
            tree.mkdir(parents=True)
            if memo is not None:
                memo.clear()
            build_tree(tree, calls, universes[args.universe].get(rid, set()), per_path)

            roots, verdicts = {}, {}
            for name, elect in candidates.items():
                root = elect(tree, calls, rid)
                roots[name] = root
                if root is None:
                    elected_by[name]["elected nothing"] += 1
                elif root == true_root:
                    elected_by[name]["right"] += 1
                else:
                    elected_by[name]["wrong"] += 1
                verdicts[name] = landings(edits, tree, calls, rid, root, per_path)
                landed_by[name].update(verdicts[name])
            for name in candidates:
                if name == "original":
                    continue
                for was, now in zip(verdicts["original"], verdicts[name]):
                    if was != now:
                        moves[name][(was, now)] += 1
            for name in candidates:
                mine = fate(verdicts[name])
                fate_by[name][mine] += 1
                if mine == "silently overwrote a file":
                    overwritten[name].append(sid)
                if name != "original":
                    was = fate(verdicts["original"])
                    if was != mine:
                        fate_moves[name][(was, mine)] += 1

            if roots["original"] is not None and roots["original"] != true_root:
                bucket = misplaced
            elif roots["proposed"] != roots["original"]:
                bucket = differ
                n_differ += 1
            else:
                bucket = None
            if bucket is not None and len(bucket) < args.examples:
                bucket.append(
                    f"  {sid}  {rid}\n"
                    f"    the checkout was      {render(true_root)}\n"
                    + "".join(f"    {name:26s}{render(r)}\n" for name, r in roots.items())
                    + f"    paths     {sorted({c.path for c in calls})[:3]}\n"
                    + f"    outcomes  {dict(collections.Counter(c.outcome for c in calls))}\n"
                    + f"    committed {sorted(committed)[:3]}\n")

            if args.sens and n < args.sens:
                sens_rows[sid] = (true_root, roots["original"], roots["proposed"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if not args.no_memo:
            edits._inside = real_inside

    print()
    print("=" * 72)
    print("THE ELECTION: one row per session that has an answer")
    print("=" * 72)
    scored = sum(elected_by["original"].values())
    print(f"  sessions scored {scored}, skipped {sum(skipped.values())}:")
    for why, n in skipped.most_common():
        print(f"    {n:6d}  {why}")
    print(f"  {'candidate':28s}{'right':>8}{'wrong':>8}{'nothing':>9}")
    for name, tally in elected_by.items():
        print(f"  {name:28s}{tally['right']:8d}{tally['wrong']:8d}{tally['elected nothing']:9d}")

    print()
    print("=" * 72)
    print("THE LANDING: one row per edit call, election and fallback together")
    print("=" * 72)
    print(f"  {'candidate':28s}{'right':>8}{'onto a real file':>18}"
          f"{'onto nothing':>14}{'rejected':>10}")
    for name, tally in landed_by.items():
        print(f"  {name:28s}{tally['right']:8d}{tally['wrong, onto a real file']:18d}"
              f"{tally['wrong, onto nothing']:14d}{tally['rejected']:10d}")

    print()
    print("=" * 72)
    print("THE TASK: the same, rolled up to the session, which is what is built")
    print("=" * 72)
    fates = ["clean", "left a stray file", "silently overwrote a file", "rejected"]
    print(f"  {'candidate':28s}{'clean':>8}{'stray file':>12}{'overwrote':>11}{'rejected':>10}")
    for name, tally in fate_by.items():
        print(f"  {name:28s}" + "".join(f"{tally[f]:{w}d}"
                                        for f, w in zip(fates, (8, 12, 11, 10))))
    print("  the sessions each one silently overwrites, by name, so they can be read:")
    for name, sids in overwritten.items():
        if sids:
            print(f"    {name:28s}{' '.join(sids[:args.examples])}")
    print("  against the original, session by session:")
    for name, tally in fate_moves.items():
        if name == "original" or not tally:
            continue
        print(f"    {name}")
        for (was, now), n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"      {n:5d}  {was:26s} -> {now}")

    print()
    print("=" * 72)
    print("WHAT MOVED, call by call, against the original")
    print("=" * 72)
    for name, tally in moves.items():
        if name == "original":
            continue
        print(f"  {name}")
        if not tally:
            print("    nothing moved")
        for (was, now), n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"    {n:6d}  {was:24s} -> {now}")

    for title, blocks, total in (
            ("MISPLACED: the original elected a root that is not the checkout",
             misplaced, elected_by["original"]["wrong"]),
            ("MOVED: sessions where the proposed rule elects something else",
             differ, n_differ)):
        print()
        print("=" * 72)
        print(title)
        print("=" * 72)
        print(f"  {total} sessions; {len(blocks)} printed (--examples)")
        for block in blocks:
            print(block)

    if args.sens and sens_rows:
        print()
        print("=" * 72)
        print("SENSITIVITY: the same sessions under the other file universe")
        print("=" * 72)
        other = "narrow" if args.universe == "broad" else "broad"
        uni = load_universe(repo, touched, narrow=other == "narrow")
        moved = collections.Counter()
        work = Path(tempfile.mkdtemp(prefix="errata-g55-sens-"))
        if not args.no_memo:
            real_inside = edits._inside
            edits._inside = memoised
        try:
            for sid, (true_root, was_original, was_proposed) in sens_rows.items():
                calls = by_session[sid]
                rid = repo[sid]
                _t, per_path = truth_for(calls, set(touched.get(sid) or ()))
                tree = work / "tree"
                if tree.exists():
                    shutil.rmtree(tree)
                tree.mkdir(parents=True)
                if memo is not None:
                    memo.clear()
                build_tree(tree, calls, uni.get(rid, set()), per_path)
                now_original = candidates["original"](tree, calls, rid)
                now_proposed = candidates["proposed"](tree, calls, rid)
                moved["sessions"] += 1
                moved["original moved"] += now_original != was_original
                moved["proposed moved"] += now_proposed != was_proposed
                moved["original right"] += now_original == true_root
                moved["proposed right"] += now_proposed == true_root
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if not args.no_memo:
                edits._inside = real_inside
        print(f"  {args.universe} -> {other}: {dict(moved)}")

    print()
    print(f"({time.time() - started:.0f}s; cache in {cache})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
