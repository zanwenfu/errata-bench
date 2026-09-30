"""The collector against SWE-chat on SWE-chat's own repositories: are the sessions the same (#16, step 6)?

    # where SWE-chat's corpus is:
    .venv/bin/python scripts/crawl_roundtrip.py digests --out swechat-digests.json
    # where the crawl ran (`crawl_entire.py fetch --repo ...` over SWE-chat's repositories):
    .venv/bin/python scripts/crawl_roundtrip.py compare swechat-digests.json --crawl data/entire-swe

A transcript line's digest is taken over its JSON, not its text: SWE-chat
re-spaced every line it stored, so bytes never match, while the JSON does
(the 09-29 pilot). `digests` writes, for each SWE-chat session, its line count
and the digest of its canonical lines. `compare` reads the crawl's transcript
for each session, takes as many lines as SWE-chat has, and says:

- `identical`: the same JSON, line for line;
- `continued`: identical, with lines written after SWE-chat's snapshot;
- `differs`: something in SWE-chat's lines is different in ours;
- `not collected`: SWE-chat has it and the crawl does not (the repository
  took its data down, or sends it elsewhere).

The two digest files hold no transcript text. No network, no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def canonical_lines(path: Path, limit: int | None = None):
    """Each line's JSON in one canonical form; a line that will not parse, as its own text."""
    with open(path, "rb") as f:
        for n, line in enumerate(f):
            if limit is not None and n >= limit:
                return
            try:
                yield json.dumps(json.loads(line), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            except ValueError:
                yield line.rstrip(b"\n")


def digest(path: Path, limit: int | None = None) -> tuple[int, str]:
    h, n = hashlib.sha256(), 0
    for line in canonical_lines(path, limit):
        h.update(line + b"\n")
        n += 1
    return n, h.hexdigest()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("digests")
    d.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("digests", type=Path)
    c.add_argument("--crawl", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.mode == "digests":
        import pyarrow.parquet as pq

        from errata_bench.corpus.sessions import CORPUS

        rows = pq.read_table(CORPUS / "sessions.parquet", columns=["session_id", "repo_id"]).to_pylist()
        out = {}
        for r in rows:
            p = CORPUS / "transcripts" / f"{r['session_id']}.jsonl"
            if p.is_file():
                lines, dg = digest(p)
                out[r["session_id"]] = {"repo": r["repo_id"], "lines": lines, "digest": dg}
        args.out.write_text(json.dumps(out) + "\n")
        print(f"{len(out)} SWE-chat sessions from {len({v['repo'] for v in out.values()})} repositories")
        return 0

    swe = json.loads(args.digests.read_text())
    crawled = {}
    for p in (args.crawl / "raw").glob("*/transcripts/*.jsonl"):
        crawled.setdefault(p.stem, p)
    verdict: Counter = Counter()
    by_repo: dict[str, Counter] = {}
    for sid, s in swe.items():
        p = crawled.get(sid)
        if p is None:
            v = "not collected"
        else:
            n, dg = digest(p, s["lines"])
            if n == s["lines"] and dg == s["digest"]:
                total, _ = digest(p)
                v = "identical" if total == n else "continued"
            else:
                v = "differs"
        verdict[v] += 1
        by_repo.setdefault(s["repo"], Counter())[v] += 1
    fetched = [json.loads(line) for line in open(args.crawl / "fetch.jsonl")] if (args.crawl / "fetch.jsonl").exists() else []
    status = Counter(r["status"] for r in fetched)
    print(f"SWE-chat sessions: {len(swe)}; the crawl's repositories: {dict(status)}")
    for k in ("identical", "continued", "differs", "not collected"):
        print(f"  {k}: {verdict[k]}")
    reached = [r for r, c in by_repo.items() if c["identical"] + c["continued"] + c["differs"]]
    print(f"  repositories with any session collected: {len(reached)} of {len(by_repo)}")
    print(f"  sessions in those repositories not collected: {sum(by_repo[r]['not collected'] for r in reached)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
