"""The Entire collector (#16), on fixtures: no network, no GitHub, no model.

    .venv/bin/python checks/crawl_holds.py

Fixture repositories are built in a temporary directory and served over
file://, laid out as Entire lays out a real one: the v1 branch, per-checkpoint
refs, a reverted v2 ref, a separate checkpoint repository named in settings.
Commit search is a fake that holds more results than one query may return.
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "src")

from errata_bench.crawl import discover, entire  # noqa: E402
from errata_bench.crawl.fetch import fetch_all, fetch_repo  # noqa: E402
from errata_bench.store.rows import load  # noqa: E402

FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


def sh(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                          "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                          "GIT_COMMITTER_EMAIL": "t@t"}).stdout.decode()


def transcript(n: int, tag: str) -> bytes:
    return b"".join(json.dumps({"type": "user", "n": i, "tag": tag}).encode() + b"\n" for i in range(n))


def session_meta(sid: str, created: str, **extra) -> str:
    return json.dumps({"session_id": sid, "created_at": created, "agent": "Claude Code", "model": "m",
                       "checkpoints_count": 1, "files_touched": [], **extra})


def build_tree(tmp: Path, files: dict[str, bytes | str]) -> str:
    """A commit holding exactly `files`, on no branch; its id."""
    work = tmp / f"w{len(list(tmp.iterdir()))}"
    work.mkdir()
    sh("git", "init", "-q", cwd=work)
    for path, data in files.items():
        p = work / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    sh("git", "add", "-A", cwd=work)
    sh("git", "commit", "-q", "-m", "checkpoint", cwd=work)
    return work, sh("git", "rev-parse", "HEAD", cwd=work).strip()


def serve(root: Path, name: str, refs: dict[str, tuple[Path, str]], main_files: dict[str, str] | None = None) -> Path:
    """A repository at root/name holding `refs` (ref -> (work dir, commit)) and a main branch."""
    repo = root / name
    repo.mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", cwd=repo)
    (repo / "README").write_text("code\n")
    for path, text in (main_files or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    sh("git", "add", "-A", cwd=repo)
    sh("git", "commit", "-q", "-m", "code", cwd=repo)
    for ref, (work, commit) in refs.items():
        sh("git", "fetch", "-q", str(work), commit, cwd=repo)
        sh("git", "update-ref", ref, commit, cwd=repo)
    # What GitHub allows and a local upload-pack does not by default: filtered
    # fetches, and wanting a blob by id.
    sh("git", "config", "uploadpack.allowFilter", "true", cwd=repo)
    sh("git", "config", "uploadpack.allowAnySHA1InWant", "true", cwd=repo)
    return repo


print("0. What a ref, a tree listing, a trailer and a settings file mean")
ulid = "01KXGTTNGCEACC83QZEJ5YAF0D"
check(entire.per_checkpoint_id(f"refs/entire/checkpoints/0D/{ulid}") == ulid, "a per-checkpoint ref is read by its id")
check(entire.per_checkpoint_id(f"refs/entire/checkpoints/AB/{ulid}") is None,
      "a ref whose shard is not its id's last two characters is not a checkpoint")
check(entire.per_checkpoint_id("refs/entire/checkpoints/v2/main") is None
      and entire.per_checkpoint_id("refs/entire/checkpoints/v2/full/0000000000032") is None,
      "the reverted v2 layout's refs are not checkpoints")
check(entire.per_checkpoint_id("refs/entire/checkpoints/ab/zzzzab") is None
      and entire.per_checkpoint_id("refs/entire/checkpoints/IL/01KXGTTNGCEACC83QZEJ5YAFIL") is None,
      "an id that is neither 12 hex characters nor a ULID is not a checkpoint, whatever its shard")
check(entire.per_checkpoint_id("refs/entire/checkpoints/f7/a3b2c4d5e6f7") == "a3b2c4d5e6f7",
      "a hex checkpoint migrated into refs is read too")
lay = entire.layout_of({entire.V1_BRANCH: "x", f"refs/entire/checkpoints/0D/{ulid}": "y",
                        "refs/entire/checkpoints/v2/main": "z", "refs/heads/main": "w"})
check(lay.v1_branch and lay.per_checkpoint == (ulid,) and lay.v2, f"a layout is read from advertised refs: {lay}")
check(entire.v1_checkpoints(["a3/b2c4d5e6f7/metadata.json", "a3/b2c4d5e6f7/0/metadata.json", "README.md",
                             "zz/b2c4d5e6f7/metadata.json"]) == [("a3b2c4d5e6f7", "a3/b2c4d5e6f7/")],
      "a v1 branch's checkpoints are its <2>/<10>/metadata.json subtrees, nothing else")
msg = f"Fix it\n\nEntire-Checkpoint: a3b2c4d5e6f7\nEntire-Checkpoint: {ulid}\nEntire-Checkpoint: a3b2c4d5e6f7\n"
check(entire.trailer_ids(msg) == ["a3b2c4d5e6f7", ulid], "a message's trailers, in order, each once")
check(entire.trailer_ids("save the Entire-Checkpoint: a3b2c4d5e6f7 in prose") == [],
      "a trailer only at the start of a line")
check(entire.checkpoint_remote(json.dumps({"strategy_options": {"checkpoint_remote":
                                                               {"provider": "github", "repo": "org/cp"}}})) == "org/cp"
      and entire.checkpoint_remote(json.dumps({"strategy_options": {"checkpoint_remote":
                                                                   {"provider": "gitlab", "repo": "org/cp"}}})) is None
      and entire.checkpoint_remote("not json") is None and entire.checkpoint_remote("[]") is None,
      "a separate checkpoint repository is followed only on GitHub, and a bad settings file names none")

print("1. Each session's latest copy")
old = entire.Copy("aaaaaaaaaaaa", "t", {"session_id": "s", "created_at": "2026-05-01T10:00:00Z"})
new = entire.Copy("bbbbbbbbbbbb", "t", {"session_id": "s", "created_at": "2026-05-01T10:00:00.5Z"})
chosen = entire.latest_copies([new, old])
check(chosen["s"][0] is new and chosen["s"][1] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
      "the latest-created copy is kept, and every checkpoint the session was in is listed oldest first")

print("2. Fetching from repositories laid out as Entire lays them out")
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    works = tmp / "works"
    works.mkdir()
    srv = tmp / "srv"
    # v1: checkpoint a3.. holds session s1 at 3 lines and an imported session;
    # checkpoint c4.. holds s1 at 5 lines (later) and s2 (unsafe id).
    v1 = build_tree(works, {
        "a3/b2c4d5e6f7/metadata.json": json.dumps({"checkpoint_id": "a3b2c4d5e6f7", "files_touched": ["x"]}),
        "a3/b2c4d5e6f7/0/metadata.json": session_meta("s1", "2026-05-01T10:00:00Z"),
        "a3/b2c4d5e6f7/0/full.jsonl": transcript(3, "early"),
        "a3/b2c4d5e6f7/1/metadata.json": session_meta("s-imported", "2026-05-01T09:00:00Z", kind="imported"),
        "a3/b2c4d5e6f7/1/full.jsonl": transcript(2, "imp"),
        "c4/d5e6f7a8b9/metadata.json": json.dumps({"checkpoint_id": "c4d5e6f7a8b9"}),
        "c4/d5e6f7a8b9/0/metadata.json": session_meta("s1", "2026-05-01T11:00:00Z"),
        "c4/d5e6f7a8b9/0/full.jsonl": transcript(5, "late"),
        "c4/d5e6f7a8b9/1/metadata.json": session_meta("../evil", "2026-05-01T11:00:00Z"),
        "c4/d5e6f7a8b9/1/full.jsonl": transcript(1, "evil"),
    })
    cp1 = build_tree(works, {"metadata.json": json.dumps({"checkpoint_id": ulid}),
                             "0/metadata.json": session_meta("s3", "2026-08-01T10:00:00Z"),
                             "0/full.jsonl": transcript(4, "refs")})
    v2 = build_tree(works, {"main.txt": "v2 data, not read"})
    serve(srv, "o/both", {entire.V1_BRANCH: v1, f"refs/entire/checkpoints/0D/{ulid}": cp1,
                          "refs/entire/checkpoints/v2/main": v2})
    serve(srv, "o/none", {})
    remote_cp = build_tree(works, {"metadata.json": json.dumps({"checkpoint_id": "e5f6a7b8c9d0"}),
                                   "0/metadata.json": session_meta("s9", "2026-09-01T10:00:00Z"),
                                   "0/full.jsonl": transcript(6, "remote")})
    serve(srv, "o/cps", {"refs/entire/checkpoints/d0/e5f6a7b8c9d0": remote_cp})
    serve(srv, "o/uses-remote", {})
    settings = {"o/uses-remote": json.dumps({"strategy_options": {"checkpoint_remote":
                                                                  {"provider": "github", "repo": "o/cps"}}})}
    where = {"base": srv.as_uri(), "settings": lambda r: settings.get(r)}

    out = tmp / "out"
    row = fetch_repo("o/both", out, **where)
    sessions = {r["session_id"]: r for r in load(out / "raw" / "o__both" / "sessions.jsonl")}
    tr = out / "raw" / "o__both" / "transcripts"
    check(row["status"] == "ok" and row["checkpoints"] == 3 and set(sessions) == {"s1", "s3"},
          f"a repository with both layouts yields both's sessions, the v2 ref unread: {row['status']}, "
          f"{row['checkpoints']} checkpoints, sessions {sorted(sessions)}")
    check((tr / "s1.jsonl").read_bytes() == transcript(5, "late") and sessions["s1"]["checkpoint_id"] == "c4d5e6f7a8b9"
          and sessions["s1"]["checkpoint_ids"] == ["a3b2c4d5e6f7", "c4d5e6f7a8b9"],
          "a session in two checkpoints is taken whole, from the later, byte for byte")
    check((tr / "s3.jsonl").read_bytes() == transcript(4, "refs") and sessions["s3"]["layout"] == "refs",
          "a per-checkpoint ref's session is read from the ref's root")
    check(row["skipped"] == {"imported": 1, "unsafe_session_id": 1} and not (tr / "s-imported.jsonl").exists()
          and not any(p.name.startswith("..") for p in tr.iterdir()) and not (out / "raw" / "evil.jsonl").exists(),
          f"imported history and an id unsafe as a file name are left out, and counted: {row['skipped']}")
    check(not any((out / "git").rglob("*.pack")), "no git data is left behind once a repository is done")
    check(json.loads((out / "raw" / "o__both" / "checkpoints.jsonl").read_text().splitlines()[0])["files_touched"] == 1,
          "one row per checkpoint, from its own metadata")

    row = fetch_repo("o/none", out, **where)
    check(row["status"] == "no_checkpoints" and not (out / "raw" / "o__none").exists(),
          "a repository with trailers but no checkpoints is recorded as such, with nothing written")
    row = fetch_repo("o/uses-remote", out, **where)
    check(row["status"] == "ok" and row["sessions"] == 1 and row["checkpoint_remote"] == {"repo": "o/cps", "reachable": True}
          and (out / "raw" / "o__uses-remote" / "transcripts" / "s9.jsonl").read_bytes() == transcript(6, "remote"),
          "a separate checkpoint repository named in settings is followed, and its sessions filed under the code's")
    row = fetch_repo("o/gone", out, **where)
    check(row["status"] == "unreachable", "a repository that cannot be listed is unreachable, not an error")

    out2 = tmp / "out2"
    fetch_all(["o/both", "o/none"], out2, workers=2, log=lambda *_: None, **where)
    first = load(out2 / "fetch.jsonl")
    fetch_all(["o/both", "o/none"], out2, workers=2, log=lambda *_: None, **where)
    check(len(first) == 2 and len(load(out2 / "fetch.jsonl")) == 2,
          "a second run skips repositories already done: one status row each")

print("3. Commit search, sliced under its cap")


class FakeSearch:
    """Trailer commits spread over two days, 2,500 of them: more than one query may return."""

    def __init__(self):
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.commits = []
        for i in range(2500):
            when = base + timedelta(seconds=i * 60)
            message = "work\n\nEntire-Checkpoint: %012x\n" % i if i % 50 else "the Entire-Checkpoint idea, in prose"
            self.commits.append({"sha": "%040x" % i, "parents": [{}], "repository":
                                 {"full_name": f"o/r{i % 7}", "fork": False, "private": False},
                                 "commit": {"message": message, "committer": {"date": when.isoformat()},
                                            "author": {"date": when.isoformat()}}})
        self.calls = 0

    def get(self, path, fields):
        self.calls += 1
        q = fields["q"]
        lo, hi = q.split("committer-date:")[1].split("..")
        lo, hi = datetime.fromisoformat(lo.replace("Z", "+00:00")), datetime.fromisoformat(hi.replace("Z", "+00:00"))
        hits = [c for c in self.commits if lo <= datetime.fromisoformat(c["commit"]["committer"]["date"]) <= hi]
        page, per = int(fields["page"]), int(fields["per_page"])
        if page * per > discover.CAP:
            raise AssertionError("asked for a page past the cap")
        return {"total_count": len(hits), "incomplete_results": False, "items": hits[(page - 1) * per: page * per]}


with tempfile.TemporaryDirectory() as t:
    out = Path(t)
    fake = FakeSearch()
    searcher = discover.Searcher(get=fake.get, gap_s=0)
    discover.discover(out, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc),
                      searcher=searcher, log=lambda *_: None)
    rows = load(out / "discover" / "commits.jsonl")
    want = {c["sha"] for i, c in enumerate(fake.commits) if i % 50}
    got = {r["sha"] for r in rows}
    slices = load(out / "discover" / "slices.jsonl")
    check(got == want and len(rows) == len(want),
          f"every trailer commit is read once and none in prose kept: {len(got)} of {len(want)}, {len(rows)} rows")
    leaves = [s for s in slices if not s.get("split")]
    check(all(s["total_count"] <= discover.CAP for s in leaves) and len(leaves) >= 3
          and all(s["total_count"] > discover.CAP for s in slices if s.get("split")),
          f"a range over the cap is halved until each slice read is under it: {len(leaves)} read, "
          f"{len(slices) - len(leaves)} halved")
    calls = fake.calls
    discover.discover(out, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc),
                      searcher=searcher, log=lambda *_: None)
    check(fake.calls - calls == 0 and len(load(out / "discover" / "commits.jsonl")) == len(rows),
          f"a resumed run asks nothing it already knows, split or read: {fake.calls - calls} calls")

print("4. Owners and the selection")
commits = [{"repo": "copy/a", "sha": "1", "committer_date": "2026-05-02T00:00:00Z"},
           {"repo": "orig/a", "sha": "1", "committer_date": "2026-05-02T00:00:00Z"},
           {"repo": "fork/a", "sha": "1", "committer_date": "2026-05-02T00:00:00Z"}]
meta = {"copy/a": {"created_at": "2026-06-01T00:00:00Z", "fork": False},
        "orig/a": {"created_at": "2026-01-01T00:00:00Z", "fork": False},
        "fork/a": {"created_at": "2025-01-01T00:00:00Z", "fork": True}}
check(discover.owners(commits, meta) == {"1": "orig/a"},
      "a commit held by several repositories belongs to its earliest-created holder that is not a fork")
with tempfile.TemporaryDirectory() as t:
    out = Path(t)
    d = out / "discover"
    d.mkdir()
    cs = [("ok/mit", "MIT", "2026-05-01T00:00:00Z"), ("ok/gpl", "GPL-3.0", "2026-05-01T00:00:00Z"),
          ("no/lic", None, "2026-05-01T00:00:00Z"), ("no/assert", "NOASSERTION", "2026-05-01T00:00:00Z"),
          ("old/mit", "MIT", "2026-03-01T00:00:00Z"), ("gone/x", None, "2026-05-01T00:00:00Z")]
    with open(d / "commits.jsonl", "w") as f:
        for i, (repo, _, when) in enumerate(cs):
            f.write(json.dumps({"repo": repo, "sha": str(i), "committer_date": when, "checkpoint_ids": ["x"]}) + "\n")
    with open(d / "repos.jsonl", "w") as f:
        for repo, lic, _ in cs:
            f.write(json.dumps({"query_repo": repo, "full_name": repo, "license": lic, "private": False,
                                "missing": repo == "gone/x", "created_at": "2026-01-01T00:00:00Z"}) + "\n")
    v1 = {r["repo"]: r["why_not"] for r in discover.select(out, "2026-04-20", "v1")}
    perm = {r["repo"]: r["why_not"] for r in discover.select(out, "2026-04-20", "permissive")}
    check(v1["ok/mit"] is None and v1["ok/gpl"] is None and v1["no/lic"] == "no licence"
          and v1["no/assert"] == "no licence" and v1["old/mit"] == "no commit since 2026-04-20" and v1["gone/x"] == "gone",
          f"the v1 policy keeps permissive and copyleft licences since the date, and says why not: {v1}")
    check(perm["ok/mit"] is None and perm["ok/gpl"].startswith("licence GPL-3.0 outside"),
          "the permissive policy leaves copyleft out")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
