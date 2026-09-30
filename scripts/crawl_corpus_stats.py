"""What an assembled Entire corpus holds that SWE-chat does not (#16).

    .venv/bin/python scripts/crawl_corpus_stats.py data/entire/corpus data/entire/swechat-digests.json

The digests file is `crawl_roundtrip.py digests`'s: SWE-chat's session ids.
Prints the sessions by month, those created after SWE-chat's last session
(19 April 2026) and not in SWE-chat, and how many of those are in a language
the pipeline can sandbox, with their developer messages: what pushback
labelling would have to read. Reads parquet only; no network.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

SANDBOX = {"TypeScript", "JavaScript", "Go", "Python", "Shell", "Astro"}
CUTOFF = "2026-04-19"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    corpus, digests = Path(argv[0]), Path(argv[1])
    sessions = pq.read_table(corpus / "sessions.parquet",
                             columns=["session_id", "repo_id", "created_at", "prompt_count"]).to_pylist()
    swe = set(json.loads(digests.read_text()))
    meta = pq.read_table(corpus / "repositories.parquet", columns=["repo_id", "repo_github_metadata"]).to_pylist()
    lang = {r["repo_id"]: json.loads(r["repo_github_metadata"] or "{}").get("language") for r in meta}

    def day(x: dict) -> str:
        return x["created_at"].strftime("%Y-%m-%d") if x["created_at"] else ""

    after = [x for x in sessions if day(x) > CUTOFF]
    new = [x for x in after if x["session_id"] not in swe]
    print(f"sessions {len(sessions)}; created after {CUTOFF}: {len(after)}; of those not in SWE-chat: {len(new)}; "
          f"in SWE-chat overall: {sum(1 for x in sessions if x['session_id'] in swe)}")
    print("by month:", sorted(collections.Counter(day(x)[:7] for x in sessions if day(x)).items()))
    sandboxed = [x for x in new if lang.get(x["repo_id"]) in SANDBOX]
    print(f"new sessions in a sandbox language: {len(sandboxed)} from {len({x['repo_id'] for x in sandboxed})} "
          f"repositories; their developer messages: {sum(x['prompt_count'] or 0 for x in sandboxed)}")
    print("languages of new sessions:", collections.Counter(lang.get(x["repo_id"]) for x in new).most_common(8))
    print("new sessions in the five largest repositories:",
          [n for _, n in collections.Counter(x["repo_id"] for x in new).most_common(5)])
    print("conversation rows:", pq.ParquetFile(corpus / "conversations.parquet").metadata.num_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
