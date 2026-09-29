"""Collect agent sessions recorded with Entire from public repositories (#16).

    .venv/bin/python scripts/crawl_entire.py discover --from 2026-01-01 --to 2026-10-01
    .venv/bin/python scripts/crawl_entire.py meta
    .venv/bin/python scripts/crawl_entire.py select --since 2026-04-20 --policy v1
    .venv/bin/python scripts/crawl_entire.py fetch --workers 4 [--limit N] [--repo OWNER/NAME ...]
    .venv/bin/python scripts/crawl_entire.py status

Every stage writes under --out (default data/entire, which git ignores) and
resumes where it stopped. discover and meta use the `gh` CLI's login (GitHub's
search needs one); fetch is anonymous git and needs no login, so it can run on
a machine without one. Read-only throughout: nothing is written to GitHub and
no model is called.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from errata_bench.crawl import discover, fetch  # noqa: E402
from errata_bench.store.rows import load  # noqa: E402


def day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("data/entire"))
    sub = ap.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--from", dest="start", type=day, required=True)
    d.add_argument("--to", dest="end", type=day, required=True)
    sub.add_parser("meta")
    s = sub.add_parser("select")
    s.add_argument("--since", required=True, help="keep repositories with a trailer commit on or after this date")
    s.add_argument("--policy", choices=sorted(discover.POLICIES), default="v1")
    f = sub.add_parser("fetch")
    f.add_argument("--workers", type=int, default=4)
    f.add_argument("--limit", type=int, default=0, help="only the first N selected repositories, by most recent sessions")
    f.add_argument("--repo", action="append", default=[], help="fetch these repositories instead of the selection")
    f.add_argument("--keep-git", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.stage == "discover":
        discover.discover(args.out, args.start, args.end)
    elif args.stage == "meta":
        discover.fetch_meta(args.out)
    elif args.stage == "select":
        rows = discover.select(args.out, args.since, args.policy)
        why = collections.Counter(r["why_not"] or "selected" for r in rows)
        print(f"{len(rows)} repositories own a trailer commit; " + "; ".join(f"{k}: {v}" for k, v in why.most_common(8)))
    elif args.stage == "fetch":
        repos = args.repo
        if not repos:
            chosen = [r for r in load(args.out / "discover" / "selected.jsonl") if r["selected"]]
            chosen.sort(key=lambda r: (-r["owned_since"], r["repo"]))
            repos = [r["repo"] for r in chosen][: args.limit or None]
        fetch.fetch_all(repos, args.out, workers=args.workers, keep_git=args.keep_git)
    elif args.stage == "status":
        rows = load(args.out / "fetch.jsonl")
        by = collections.Counter(r["status"] for r in rows)
        print(f"fetched: {dict(by)}; sessions {sum(r.get('sessions', 0) for r in rows)}; "
              f"{sum(r.get('bytes', 0) for r in rows) / 1e9:.2f} GB of transcripts")
        skipped = collections.Counter()
        for r in rows:
            skipped.update(r.get("skipped") or {})
        print(f"sessions left out: {dict(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
