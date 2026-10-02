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
from errata_bench.crawl.shape import CONVERSATIONS, claude_code_rows  # noqa: E402
from errata_bench.crawl.corpus import assemble  # noqa: E402
from errata_bench.crawl.fetch import fetch_all, fetch_repo  # noqa: E402
from errata_bench.crawl.link import PATCH_CAP, link_repo  # noqa: E402
from errata_bench.store.rows import append, load  # noqa: E402

FAIL = []


def check(ok, message):
    (print(f"  ok    {message}") if ok else (FAIL.append(message), print(f"  FAIL  {message}")))


def sh(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                          "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                          "GIT_COMMITTER_EMAIL": "t@t"}).stdout.decode()


def transcript(n: int, tag: str, call: str | None = None) -> bytes:
    lines = [json.dumps({"type": "user", "n": i, "tag": tag}).encode() + b"\n" for i in range(n)]
    if call:  # the main agent's call that spawned a subagent
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": call, "name": "Agent", "input": {}}]}}).encode() + b"\n")
    return b"".join(lines)


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
        "a3/b2c4d5e6f7/tasks/toolu_s1/agent-a1.jsonl": b'{"sub": 1}\n',
        "a3/b2c4d5e6f7/tasks/toolu_s1/task.json": b'{"tool_use_id": "toolu_s1"}',
        "a3/b2c4d5e6f7/1/metadata.json": session_meta("s-imported", "2026-05-01T09:00:00Z", kind="imported"),
        "a3/b2c4d5e6f7/1/full.jsonl": transcript(2, "imp"),
        "c4/d5e6f7a8b9/metadata.json": json.dumps({"checkpoint_id": "c4d5e6f7a8b9"}),
        "c4/d5e6f7a8b9/0/metadata.json": session_meta("s1", "2026-05-01T11:00:00Z"),
        "c4/d5e6f7a8b9/0/full.jsonl": transcript(5, "late", call="toolu_s1"),
        "c4/d5e6f7a8b9/1/metadata.json": session_meta("../evil", "2026-05-01T11:00:00Z"),
        "c4/d5e6f7a8b9/1/full.jsonl": transcript(1, "evil"),
    })
    cp1 = build_tree(works, {"metadata.json": json.dumps({"checkpoint_id": ulid}),
                             "0/metadata.json": session_meta("s3", "2026-08-01T10:00:00Z"),
                             "0/full.jsonl": transcript(4, "refs", call="toolu_s3"),
                             "tasks/toolu_s3/agent-b2.jsonl": b'{"sub": 3}\n',
                             "tasks/toolu_zz/agent-c3.jsonl": b'{"sub": "not this session"}\n'})
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
    check((tr / "s1.jsonl").read_bytes() == transcript(5, "late", call="toolu_s1") and sessions["s1"]["checkpoint_id"] == "c4d5e6f7a8b9"
          and sessions["s1"]["checkpoint_ids"] == ["a3b2c4d5e6f7", "c4d5e6f7a8b9"],
          "a session in two checkpoints is taken whole, from the later, byte for byte")
    check((tr / "s3.jsonl").read_bytes() == transcript(4, "refs", call="toolu_s3") and sessions["s3"]["layout"] == "refs",
          "a per-checkpoint ref's session is read from the ref's root")
    check(row["skipped"] == {"imported": 1, "unsafe_session_id": 1} and not (tr / "s-imported.jsonl").exists()
          and not any(p.name.startswith("..") for p in tr.iterdir()) and not (out / "raw" / "evil.jsonl").exists(),
          f"imported history and an id unsafe as a file name are left out, and counted: {row['skipped']}")
    sub = out / "raw" / "o__both" / "subagents"
    a1 = sub / "s1" / "toolu_s1" / "agent-a1.jsonl"
    check(a1.exists() and a1.read_bytes() == b'{"sub": 1}\n'
          and (sub / "s1" / "toolu_s1" / "task.json").exists() and sessions["s1"]["subagent_tasks"] == 1,
          "a session's subagent transcript is collected from an earlier checkpoint it was in, with its task record")
    check((sub / "s3" / "toolu_s3" / "agent-b2.jsonl").exists() and not (sub / "s3" / "toolu_zz").exists()
          and not any(p.name == "agent-c3.jsonl" for p in sub.rglob("*")),
          "only subagents the session's own calls spawned are filed under it")
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


print("5. A Claude Code transcript as SWE-chat's rows, nothing dropped")
T = "2026-05-01T10:00:0{}Z"
entries = [
    {"type": "user", "timestamp": T.format(0), "message": {"role": "user", "content": "please fix the parser"}},
    {"type": "assistant", "timestamp": T.format(1), "message": {"id": "m1", "model": "x", "usage": {"output_tokens": 7},
     "content": [{"type": "text", "text": "\n\nI'll read it first."}]}},
    {"type": "assistant", "timestamp": T.format(2), "message": {"id": "m1", "model": "x", "usage": {"output_tokens": 9},
     "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/r/p.py"}}]}},
    {"type": "assistant", "timestamp": T.format(3), "message": {"id": "m1", "model": "x", "usage": {"output_tokens": 11},
     "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest", "note": "caf\u00e9"}}]}},
    {"type": "user", "timestamp": T.format(4), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "x = 1"},
        {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "1 passed"}]}]}},
    {"type": "queue-operation", "timestamp": T.format(5), "operation": "enqueue", "content": "no, don't touch tests"},
    {"type": "queue-operation", "timestamp": T.format(5), "operation": "enqueue", "content": "<task-notification>done</task-notification>"},
    {"type": "user", "timestamp": T.format(6), "message": {"role": "user", "content": "<command-name>/model</command-name>"}},
    {"type": "user", "timestamp": T.format(6), "message": {"role": "user", "content":
        "<command-message>review</command-message>\n<command-name>/review</command-name>"}},
    {"type": "user", "timestamp": T.format(6), "isMeta": True, "message": {"role": "user", "content": [
        {"type": "text", "text": "Review the diff carefully."}]}},
    {"type": "user", "timestamp": T.format(7), "message": {"role": "user", "content": [
        {"type": "text", "text": "see this"}, {"type": "image", "source": {"media_type": "image/png", "data": "AAAA"}}]}},
    {"type": "file-history-snapshot", "messageId": "m1", "snapshot": {}, "timestamp": T.format(8)},
    {"type": "custom-title", "title": "t"},
]
rows = claude_code_rows("s", "o/r", "o/r#abc", entries)
kinds = [(r["turn_type"], r["tool_call_id"] or (r["content"] or "")[:20]) for r in rows]
check([r["turn_number"] for r in rows] == list(range(len(rows))) and all(r["turn_id"] == f"s#{r['turn_number']}" for r in rows),
      "rows are numbered from 0 in order, each with its own id")
check(sum(r["turn_type"] == "tool_use" for r in rows) == 2 and ("assistant_response", "I'll read it first.") in kinds,
      "every block of a message is a row: both batched calls and the text before them (G-76, G-79), text stripped")
check([r["output_tokens"] for r in rows if r["role"] in ("assistant", "tool_use")] == [None, None, 11],
      "a message's token counts go on its last row only, so they are counted once")
res = {r["tool_call_id"]: r for r in rows if r["turn_type"] == "tool_result"}
check(res["t1"]["tool_name"] == "Read" and res["t2"]["content"] == "1 passed" and res["t2"]["tool_name"] == "Bash",
      "a result carries its call's name, and its text")
call = next(r for r in rows if r["tool_call_id"] == "t2")
read = next(r for r in rows if r["tool_call_id"] == "t1")
check(read["file_path"] == "/r/p.py" and call["command"] == "pytest" and call["tool_input_json"] == json.dumps(
      {"command": "pytest", "note": "caf\u00e9"}) and "\\u00e9" in call["tool_input_json"],
      "a call's path, command and input are extracted, the input in SWE-chat's escaped form")
user = [(r["turn_type"], r["content"]) for r in rows if r["role"] == "user"]
check(("user_prompt", "no, don't touch tests") in user and ("system_injected", "<task-notification>done</task-notification>") in user,
      "a message typed while the agent was busy is the developer's; a queued task notice is not")
# G-84. Newer versions write a queued message again where it is delivered: as
# the developer's own entry, or mid-turn as a queued_command attachment.
queued = [
    {"type": "user", "timestamp": T.format(0), "message": {"role": "user", "content": "start"}},
    *[{"type": "queue-operation", "timestamp": T.format(1), "operation": "enqueue", "content": text}
      for text in ("use the other branch", "keep the tests", "wait", "wait", "again", "again", "not delivered")],
    {"type": "assistant", "timestamp": T.format(2), "message": {"id": "m9", "model": "x",
     "content": [{"type": "text", "text": "working"}]}},
    {"type": "attachment", "timestamp": T.format(3), "attachment": {"type": "queued_command", "prompt": "keep the tests",
                                                                     "commandMode": "prompt"}},
    {"type": "queue-operation", "timestamp": T.format(4), "operation": "dequeue"},
    {"type": "user", "timestamp": T.format(4), "message": {"role": "user", "content": "use  the other branch"}},
    {"type": "user", "timestamp": T.format(5), "message": {"role": "user", "content": "wait"}},
    {"type": "user", "timestamp": T.format(6), "message": {"role": "user", "content": "wait"}},
    {"type": "user", "timestamp": T.format(7), "message": {"role": "user", "content": "again"}},
]
qrows = claude_code_rows("q", "o/r", "o/r#q", queued)
said = {}
for r in qrows:
    if r["turn_type"] == "user_prompt":
        said[" ".join(r["content"].split())] = said.get(" ".join(r["content"].split()), 0) + 1
working = next(i for i, r in enumerate(qrows) if r["turn_type"] == "assistant_response")
branch = [i for i, r in enumerate(qrows) if r["turn_type"] == "user_prompt" and "other branch" in r["content"]]
check(said == {"start": 1, "use the other branch": 1, "keep the tests": 1, "wait": 2, "again": 2, "not delivered": 1}
      and len(branch) == 1 and branch[0] > working,
      f"a queued message written again on delivery is one row, where it was delivered; one queued twice is "
      f"two, delivered once or twice; never delivered, the queue entry is its row (G-84): {said}")
# A slash command is queued as typed and delivered as Claude Code's command form;
# an attachment whose prompt is content blocks makes no row of its own.
commands = [
    {"type": "user", "timestamp": T.format(0), "message": {"role": "user", "content": "start"}},
    {"type": "queue-operation", "timestamp": T.format(1), "operation": "enqueue", "content": "/ship-it  merge it to main"},
    {"type": "queue-operation", "timestamp": T.format(1), "operation": "enqueue", "content": "/compact"},
    {"type": "queue-operation", "timestamp": T.format(1), "operation": "enqueue", "content": "hold on"},
    {"type": "attachment", "timestamp": T.format(2), "attachment": {"type": "queued_command", "commandMode": "prompt",
                                                                     "prompt": [{"type": "text", "text": "hold on"}]}},
    {"type": "user", "timestamp": T.format(3), "message": {"role": "user", "content":
        "<command-message>ship-it</command-message>\n<command-name>/ship-it</command-name>\n"
        "<command-args>merge it to main</command-args>"}},
    {"type": "user", "timestamp": T.format(4), "message": {"role": "user", "content":
        "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args></command-args>"}},
    {"type": "user", "timestamp": T.format(5), "message": {"role": "user", "content": "hold on"}},
    {"type": "queue-operation", "timestamp": T.format(6), "operation": "enqueue", "content": "/notes ok"},
    {"type": "user", "timestamp": T.format(7), "message": {"role": "user", "content":
        "<command-message>notes</command-message>\n<command-name>notes</command-name>\n<command-args>ok</command-args>"}},
]
said = {}
for r in claude_code_rows("c", "o/r", "o/r#c", commands):
    if r["turn_type"] == "user_prompt":
        said[" ".join(r["content"].split())] = said.get(" ".join(r["content"].split()), 0) + 1
check(said == {"start": 1, "<command-message>ship-it</command-message> <command-name>/ship-it</command-name> "
               "<command-args>merge it to main</command-args>": 1, "hold on": 2,
               "<command-message>notes</command-message> <command-name>notes</command-name> <command-args>ok</command-args>": 1},
      f"a queued slash command is paired with its command form by name and arguments, a built-in one leaves no "
      f"developer row, and a queue entry delivered as content blocks stays the row for its message (G-84): {said}")
# G-85. A transcript can hold its history twice (the same uuids), and a call can be
# re-sent in another message under the id of one already written.
history = [
    {"type": "user", "uuid": "u1", "timestamp": T.format(0), "message": {"role": "user", "content": "go"}},
    {"type": "assistant", "uuid": "a1", "timestamp": T.format(1), "message": {"id": "m1", "model": "x", "content": [
        {"type": "tool_use", "id": "c1", "name": "Edit", "input": {"file_path": "/r/a.py"}}]}},
    {"type": "user", "uuid": "u2", "timestamp": T.format(2), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]}},
]
twice = [*history, *history,
         {"type": "assistant", "uuid": "a2", "timestamp": T.format(3), "message": {"id": "m2", "model": "x", "content": [
             {"type": "tool_use", "id": "c1", "name": "Edit", "input": {"file_path": "/r/a.py"}}]}},
         {"type": "user", "uuid": "u3", "timestamp": T.format(4), "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]}},
         {"type": "user", "uuid": "u4", "timestamp": T.format(5), "message": {"role": "user", "content": "go"}}]
kinds2 = [r["turn_type"] for r in claude_code_rows("t", "o/r", "o/r#t", twice)]
check(kinds2.count("tool_use") == 1 and kinds2.count("tool_result") == 1 and kinds2.count("user_prompt") == 2,
      f"an entry written twice is read once, a call re-sent under its own id is one call with one result, and a "
      f"message said again in an entry of its own stays (G-85): {kinds2}")
check(("system_injected", "<command-name>/model</command-name>") in user and ("user_prompt", "Review the diff carefully.") in user,
      "a built-in command is injected; a command's expanded instructions are the developer's request")
check(("user_prompt", "see this\n[Image: image/png]") in user, "an image is kept as SWE-chat wrote it")
# G-90. Who wrote a user message: the developer, another agent, or Claude Code.
# By the marks Claude Code writes on the entry, and by the text where it wrote
# none. Each entry names the one before it, as Claude Code's do.
U90 = lambda uid, parent, content, **kw: {"type": "user", "uuid": uid, "parentUuid": parent, "timestamp": T.format(0),
                                          "message": {"role": "user", "content": content}, **kw}
M90 = lambda uid, parent, text, **kw: U90(uid, parent, [{"type": "text", "text": text}], isMeta=True, **kw)
A90 = lambda uid, parent, *blocks: {"type": "assistant", "uuid": uid, "parentUuid": parent, "timestamp": T.format(1),
                                    "message": {"id": "m-" + uid, "model": "x", "content": list(blocks)}}
said90 = [
    U90("u1", None, "please fix the parser"),
    A90("a1", "u1", {"type": "text", "text": "On it."}),
    U90("p1", "a1", '<teammate-message teammate_id="tester" color="green">P1 done</teammate-message>'),
    U90("p2", "p1", "please review the API", origin={"kind": "peer", "from": "lead", "body": "please review the API"}),
    U90("p3", "p2", "the tests pass now", turnOrigin="peer"),
    {"type": "queue-operation", "timestamp": T.format(2), "operation": "enqueue",
     "content": '<agent-message from="a9">[Subagent hand-back] done</agent-message>'},
    {"type": "attachment", "uuid": "q1", "parentUuid": "p3", "timestamp": T.format(2), "attachment": {
        "type": "queued_command", "commandMode": "prompt",
        "prompt": '<cross-session-message from="uds:/tmp/s">fix the lint</cross-session-message>'}},
    A90("a2", "q1", {"type": "tool_use", "id": "s1", "name": "Skill", "input": {"skill": "review"}}),
    U90("r1", "a2", [{"type": "tool_result", "tool_use_id": "s1", "content": "Launching skill: review"}]),
    M90("k1", "r1", "Base directory for this skill: /x\n# Review", sourceToolUseID="s1"),
    U90("c1", "k1", "<command-message>ship</command-message>\n<command-name>/ship</command-name>"),
    M90("k2", "c1", "Ship it: run the tests,"),
    M90("k2b", "k2", "then push."),
    M90("k1b", "c1", "Base directory for this skill: /y\n# Lint", sourceToolUseID="s9"),
    A90("a3", "k2b", {"type": "text", "text": "Pushed."}),
    M90("k4", "a3", "Check the build again."),
    U90("c2", "k4", "<command-name>/context</command-name>"),
    M90("k3", "c2", "## Context Usage 12k of 200k"),
    M90("k5", "k3", "Stop hook feedback: run the linter first"),
    M90("k6", "k5", "Continue from where you left off."),
    U90("d1", "k6", "Continue from where you left off."),
    M90("k7", "d1", "[Image: original 100x80, displayed at 100x80.]"),
    A90("a4", "k7", {"type": "tool_use", "id": "ts", "name": "ToolSearch", "input": {"query": "select:Read"}}),
    U90("r2", "a4", [{"type": "tool_result", "tool_use_id": "ts", "content": "Read"}, {"type": "text", "text": "Tool loaded."}]),
    A90("a5", "r2", {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/r/a.py"}}),
    U90("r3", "a5", [{"type": "tool_result", "tool_use_id": "e1", "content": "ok"},
                     {"type": "text", "text": "use the other file"}]),
    U90("h1", "r3", "update", promptSource="system"),
    U90("h2", "h1", "Background agent X was stopped", origin={"kind": "task-notification"}),
    U90("h3", "h2", "Goal set: finish the roadmap", origin={"kind": "auto-continuation"}),
    U90("h4", "h3", "Check the CI run", turnOrigin="task_notification"),
    U90("h5", "h4", "the nightly job finished", turnOrigin="system"),
    U90("c3", "h5", "<command-message>good-night</command-message>\n<command-name>/good-night</command-name>",
        turnOrigin="scheduled"),
    M90("k8", "c3", "Run the overnight improvements."),
    U90("d2", "k8", "now the docs", promptSource="typed", origin={"kind": "human"}),
    M90("k9", "d2", "<command-message>brainstorm</command-message>\n<command-name>brainstorm</command-name>"),
    M90("k10", "k9", "# Brainstorming\nAsk one question at a time."),
    U90("cs", "k10", "This session is being continued from a previous conversation. Summary: ...",
        isCompactSummary=True, turnOrigin="task_notification"),
]
rows90 = claude_code_rows("w", "o/r", "o/r#w", said90)
got90 = [(r["turn_type"], r["content"]) for r in rows90 if r["role"] == "user"]
kind90 = lambda text: [k for k, c in got90 if c == text]
check(kind90('<teammate-message teammate_id="tester" color="green">P1 done</teammate-message>') == ["peer_message"]
      and kind90("please review the API") == ["peer_message"] and kind90("the tests pass now") == ["peer_message"]
      and kind90('<agent-message from="a9">[Subagent hand-back] done</agent-message>') == ["peer_message"]
      and kind90('<cross-session-message from="uds:/tmp/s">fix the lint</cross-session-message>') == ["peer_message"],
      f"another agent's message is another agent's, marked as one (`origin`, `turnOrigin`) or only tagged, written as "
      f"an entry, a queue entry or an attachment (G-90): {[(k, c[:20]) for k, c in got90[:6]]}")
check(kind90("Ship it: run the tests,") == ["user_prompt"] and kind90("then push.") == ["user_prompt"]
      and kind90("please fix the parser") == ["user_prompt"] and kind90("now the docs") == ["user_prompt"]
      and kind90("Continue from where you left off.") == ["system_injected", "user_prompt"],
      f"a command's expansion is the developer's, in one meta entry or two; what they typed is theirs, the resume "
      f"line too when they type it (G-90): {[(k, c[:20]) for k, c in got90 if k == 'user_prompt']}")
check(all(kind90(text) == ["system_injected"] for text in (
          "Base directory for this skill: /x\n# Review", "Base directory for this skill: /y\n# Lint",
          "Check the build again.", "## Context Usage 12k of 200k", "Stop hook feedback: run the linter first",
          "[Image: original 100x80, displayed at 100x80.]", "Run the overnight improvements.",
          "<command-message>brainstorm</command-message>\n<command-name>brainstorm</command-name>",
          "# Brainstorming\nAsk one question at a time.")),
      f"every other meta entry is Claude Code's: a skill the agent's call loaded, even right after the developer's "
      f"command; a timer's prompt after the agent's turn; a built-in command's output; a hook's feedback; the resume "
      f"line; a note on an image; what follows a command a timer ran, or a skill that loaded itself (G-90): "
      f"{[(k, c[:24]) for k, c in got90 if c.startswith(('Base', 'Check', '##', 'Stop', '[Image', 'Run', '<command-message>b', '# B'))]}")
check(all(kind90(text) == ["system_injected"] for text in (
          "update", "Background agent X was stopped", "Goal set: finish the roadmap", "Check the CI run",
          "the nightly job finished", "<command-message>good-night</command-message>\n<command-name>/good-night</command-name>",
          "Tool loaded.")) and kind90("use the other file") == ["user_prompt"],
      f"a prompt the harness or a timer sent, a notice and an automatic continuation are Claude Code's by the marks "
      f"on the entry; beside a tool's result, \"Tool loaded.\" is Claude Code's and what the developer typed is "
      f"theirs (G-90): {[(k, c[:20]) for k, c in got90 if c in ('update', 'Tool loaded.', 'use the other file')]}")
summary90 = next((r for r in rows90 if (r["content"] or "").startswith("This session is being continued")), {})
check(summary90.get("turn_type") == "user_prompt" and summary90.get("is_continuation") is True
      and [r["turn_number"] for r in rows90] == list(range(len(rows90)))
      # every entry but r1, which holds only a result, is a row
      and len(got90) == sum(1 for e in said90 if e["type"] in ("user", "queue-operation", "attachment")) - 1,
      f"the summary that opens a continued session stays as it was, whatever turn it came in on, and every message "
      f"is still a row in its place, only who wrote it decided (G-90): {summary90.get('turn_type')}, {len(got90)}")
snap = next(r for r in rows if r["turn_type"] == "file_snapshot")
check(snap["role"] == "metadata" and snap["timestamp"] is None and not any(r["content"].startswith('{"type": "custom-title"')
      for r in rows), "a snapshot is a metadata row without a time; a title is no row")
import pyarrow as pa  # noqa: E402
check(pa.Table.from_pylist(rows, schema=CONVERSATIONS).num_rows == len(rows),
      "the rows fit SWE-chat's conversations schema exactly")


print("6. Linking code commits, assembling the corpus, and the pipeline reading it")


def cc_transcript(sid: str, ask: str, call: str) -> bytes:
    """A small Claude Code transcript: a request, text and an edit in one message, its result, an answer."""
    base = {"sessionId": sid, "version": "2.1.246", "uuid": "u"}
    lines = [
        {**base, "type": "bridge-session", "bridgeSessionId": "b"},
        {**base, "type": "user", "timestamp": "2026-05-01T10:00:00Z", "message": {"role": "user", "content": ask}},
        {**base, "type": "assistant", "timestamp": "2026-05-01T10:00:01Z", "message": {"role": "assistant", "id": "m",
         "content": [{"type": "text", "text": "Editing a.txt now."}]}},
        {**base, "type": "assistant", "timestamp": "2026-05-01T10:00:02Z", "message": {"role": "assistant", "id": "m",
         "content": [{"type": "tool_use", "id": call, "name": "Edit", "input": {"file_path": "/w/a.txt"}}]}},
        {**base, "type": "user", "timestamp": "2026-05-01T10:00:03Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call, "content": "edited"}]}},
        {**base, "type": "assistant", "timestamp": "2026-05-01T10:00:04Z", "message": {"role": "assistant", "id": "n",
         "content": [{"type": "text", "text": "Done, and the tests pass."}]}},
    ]
    return b"".join(json.dumps(x).encode() + b"\n" for x in lines)


with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    srv = tmp / "srv"
    code = srv / "o" / "full"
    code.mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", cwd=code)
    (code / "a.txt").write_text("one\n")
    sh("git", "add", "-A", cwd=code)
    sh("git", "commit", "-q", "-m", "start", cwd=code)
    (code / "a.txt").write_text("one\ntwo\n")
    sh("git", "commit", "-q", "-am", "fix\n\nEntire-Checkpoint: a3b2c4d5e6f7", cwd=code)
    sh("git", "checkout", "-q", "-b", "feature", cwd=code)
    (code / "b.txt").write_text("b\n")
    sh("git", "add", "-A", cwd=code)
    sh("git", "commit", "-q", "-m", f"feature work\n\nEntire-Checkpoint: {ulid}", cwd=code)
    (code / "big.txt").write_text(("y" * 99 + "\n") * (PATCH_CAP // 100 + 200))  # a patch past the cap
    sh("git", "add", "-A", cwd=code)
    sh("git", "commit", "-q", "-m", f"a large file\n\nEntire-Checkpoint: {ulid}", cwd=code)
    sh("git", "checkout", "-q", "main", cwd=code)
    (code / "a.txt").write_text("one\ntwo\nthree\n")
    sh("git", "commit", "-q", "-am", "mentions the Entire-Checkpoint: a3b2c4d5e6f7 idea in prose", cwd=code)
    works = tmp / "works"
    works.mkdir()
    v1 = build_tree(works, {
        "a3/b2c4d5e6f7/metadata.json": json.dumps({"checkpoint_id": "a3b2c4d5e6f7"}),
        "a3/b2c4d5e6f7/0/metadata.json": session_meta("cc1", "2026-05-01T10:00:05Z", strategy="manual-commit"),
        "a3/b2c4d5e6f7/0/full.jsonl": cc_transcript("cc1", "please fix a.txt", "toolu_e1"),
        "a3/b2c4d5e6f7/1/metadata.json": session_meta("codex1", "2026-05-01T11:00:00Z", agent="Codex"),
        "a3/b2c4d5e6f7/1/full.jsonl": b'{"type": "session_meta", "payload": {}}\n',
        "a3/b2c4d5e6f7/tasks/toolu_e1/agent-s1.jsonl": json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_sub1", "name": "Write", "input": {"file_path": "/w/c.txt"}}]}}).encode() + b"\n",
    })
    sh("git", "fetch", "-q", str(v1[0]), v1[1], cwd=code)
    sh("git", "update-ref", entire.V1_BRANCH, v1[1], cwd=code)
    sh("git", "config", "uploadpack.allowFilter", "true", cwd=code)
    sh("git", "config", "uploadpack.allowAnySHA1InWant", "true", cwd=code)
    where = {"base": srv.as_uri()}

    out = tmp / "out"
    row = link_repo("o/full", out, **where)
    linked = {r["commit_message"].split("\n")[0]: r for r in load(out / "link" / "o__full" / "commits.jsonl")}
    check(row["status"] == "ok" and set(linked) == {"fix", "feature work", "a large file"},
          f"every trailer commit on any branch is linked, and prose is not: {sorted(linked)}")
    check(linked["fix"]["files_changed"] == "M\ta.txt" and linked["fix"]["total_additions"] == 1
          and linked["fix"]["checkpoint_ids"] == ["a3b2c4d5e6f7"] and "+two" in linked["fix"]["patch"],
          "a linked commit carries its name-status, line counts, patch and checkpoint")
    check(linked["a large file"]["patch_cut"] and len(linked["a large file"]["patch"]) <= PATCH_CAP,
          "a patch over the cap is cut and marked")
    check(not (out / "git-link").exists() or not any((out / "git-link").iterdir()), "the clone is deleted after linking")

    append(out / "fetch.jsonl", fetch_repo("o/full", out, settings=lambda r: None, **where))
    (out / "discover").mkdir(parents=True, exist_ok=True)
    (out / "discover" / "repos.jsonl").write_text(json.dumps({"query_repo": "o/full", "full_name": "o/full",
                                                             "license": "MIT", "language": "Go", "fork": False}) + "\n")
    (out / "discover" / "commits.jsonl").write_text("")
    summary = assemble(out, log=lambda *_: None)
    corpus = out / "corpus"
    check(summary["sessions"] == 1 and summary["left_out"] == {"not a Claude Code transcript (for later)": 1},
          f"only Claude Code sessions are written, the others counted, a new format included: {summary}")
    cc1 = corpus / "transcripts" / "cc1.jsonl"
    check(cc1.exists() and cc1.read_bytes() == cc_transcript("cc1", "please fix a.txt", "toolu_e1")
          and (corpus / "subagents" / "cc1" / "toolu_e1" / "agent-s1.jsonl").exists(),
          "the transcript and its subagents' are linked into the corpus")
    (corpus / "transcripts" / "codex9.jsonl").write_text('{"type": "session_meta", "payload": {}}\n')
    probe = subprocess.run([sys.executable, "-c", """
import json, sys
sys.path.insert(0, "src")
from errata_bench.corpus import sessions, turns, timeline, recover
from errata_bench.construct import build
rows = turns.load_session_turns({"cc1"})["cc1"]
spawn = [{"turn_type": "tool_use", "tool_name": "Agent", "tool_call_id": "toolu_gone", "turn_number": 1},
         {"turn_type": "tool_use", "tool_name": "Agent", "tool_call_id": "toolu_e1", "turn_number": 2}]
print(json.dumps({"has": [recover.has_transcript("cc1"), recover.has_transcript("codex9")],
                  "sub": [(e["file_path"], e["spawned_by"]) for e in recover.subagent_edits("cc1")],
                  "unrecorded": recover.unrecorded_subagents("cc1", spawn),
                  "before_cut": [build.unrecorded_subagents_before("cc1", spawn, 1),
                                 build.unrecorded_subagents_before("cc1", spawn, 0)],
                  "repos": {k: [v.language, v.license_type] for k, v in sessions.load_repos().items()},
                  "commits": sessions.session_commits(),
                  "by_repo": {k: len(v) for k, v in timeline.load_commits_by_repo().items()},
                  "turns": [[r["turn_type"], r["content"]] for r in rows if r["turn_type"] in
                            ("user_prompt", "assistant_response", "tool_use", "tool_result")]}))
"""], capture_output=True, text=True, env={**os.environ, "ERRATA_CORPUS": str(corpus)})
    got = json.loads(probe.stdout or "{}")
    check(got.get("repos") == {"o/full": ["Go", "MIT"]} and got.get("by_repo") == {"o/full": 3},
          f"the pipeline's readers load the corpus from ERRATA_CORPUS: {probe.stderr[-200:] or 'repositories, commits'}")
    check(sorted(got.get("commits", {}).get("cc1", [])) == [linked["fix"]["commit_sha"]],
          "a session reaches the commit its checkpoint's trailer names")
    check(got.get("turns") == [["user_prompt", "please fix a.txt"], ["assistant_response", "Editing a.txt now."],
                               ["tool_use", json.dumps({"file_path": "/w/a.txt"})], ["tool_result", "edited"],
                               ["assistant_response", "Done, and the tests pass."]],
          "and read its turns whole: the text before the edit is there")
    check(got.get("has") == [True, False], "a 2.1 transcript is Claude Code's to the pipeline; another agent's is foreign")
    check(got.get("sub") == [["/w/c.txt", "toolu_e1"]],
          "a sub-agent's edit is read from its own transcript and placed at the call that spawned it")
    check(got.get("unrecorded") == ["toolu_gone"], "a sub-agent call with no record at all is named, one with a record is not")
    check(got.get("before_cut") == [["toolu_gone"], []],
          "and the build refuses it only when the call came at or before the cut")

print("7. A session several repositories hold is credited to its original, not to a copy (G-80)")
# Every repository whose settings name one checkpoint repository is given all of
# its sessions. A copy of entireio/cli under another account, not a GitHub
# fork, kept its settings, and its copied history kept the trailers too. It
# sorted first by name, and name order credited 1,512 of Entire's sessions to
# it. Here seven code repositories name one checkpoint repository holding seven
# sessions, and an eighth holds one of them in its own refs.
with tempfile.TemporaryDirectory() as t:
    import pyarrow.parquet as pq  # noqa: E402

    tmp = Path(t)
    srv, works, out = tmp / "srv", tmp / "works", tmp / "out"
    works.mkdir()
    # Each session's checkpoints in the checkpoint repository, oldest first.
    cps = {"g80": ["a1b2c3d4e5f6"], "pair": ["b2c3d4e5f6a1"], "orphan": ["c3d4e5f6a1b2"], "forkonly": ["d4e5f6a1b2c3"],
           "early": ["e5f6a1b2c3d4", "f6a1b2c3d4e5"], "late": ["a2b3c4d5e6f7", "b3c4d5e6f7a2"], "multi": ["c4d5e6f7a2b3"]}
    own = "d5e6f7a2b3c4"  # "multi"'s checkpoint in s/own's own refs

    def checkpoint(cid: str, sid: str, when: str) -> dict:
        """One checkpoint of the v1 layout, holding one session."""
        return {f"{cid[:2]}/{cid[2:]}/metadata.json": json.dumps({"checkpoint_id": cid}),
                f"{cid[:2]}/{cid[2:]}/0/metadata.json": session_meta(sid, when),
                f"{cid[:2]}/{cid[2:]}/0/full.jsonl": cc_transcript(sid, f"please fix {sid}", f"toolu_{sid}")}

    cp_files = {}
    for sid, ids in cps.items():
        for i, cid in enumerate(ids):
            cp_files.update(checkpoint(cid, sid, f"2026-05-01T1{i}:00:05Z"))
    serve(srv, "o/cps", {entire.V1_BRANCH: build_tree(works, cp_files)})

    def code_repo(name: str, trailers: list[str], refs: dict | None = None) -> Path:
        """A code repository served at srv/name, with one commit per checkpoint trailer."""
        repo = serve(srv, name, refs or {})
        for cid in trailers:
            (repo / "x.txt").write_text(cid + "\n")
            sh("git", "add", "-A", cwd=repo)
            sh("git", "commit", "-q", "-m", f"work\n\nEntire-Checkpoint: {cid}", cwd=repo)
        return repo

    orig = code_repo("o/orig", cps["g80"])
    trailer_sha = sh("git", "rev-parse", "HEAD", cwd=orig).strip()
    code_repo("z/late", cps["pair"] + cps["late"][1:])  # "late"'s latest checkpoint
    back = code_repo("b/back", cps["early"][:1] + cps["late"][:1])  # only earlier checkpoints
    early_sha = sh("git", "rev-parse", "HEAD~1", cwd=back).strip()
    code_repo("a/early", [])
    code_repo("f/fork", cps["forkonly"])
    code_repo("s/own", [own], {entire.V1_BRANCH: build_tree(works, checkpoint(own, "multi", "2026-05-01T12:00:05Z"))})
    for name in ("C/copy", "A/nometa"):  # the original's history, trailers and all; both sort first
        copy = srv / name
        copy.parent.mkdir(parents=True, exist_ok=True)
        sh("git", "clone", "-q", "--bare", str(orig), str(copy))
        sh("git", "config", "uploadpack.allowFilter", "true", cwd=copy)
        sh("git", "config", "uploadpack.allowAnySHA1InWant", "true", cwd=copy)
    # Created, fork, and the name search found it under ("a/early" was renamed since). A/nometa has
    # no metadata, as when fetched by name; s/own names no checkpoint repository.
    held = {"C/copy": ("2026-09-06T00:00:00Z", False, "C/copy"), "o/orig": ("2026-01-01T00:00:00Z", False, "o/orig"),
            "z/late": ("2026-05-01T00:00:00Z", False, "z/late"), "b/back": ("2025-06-01T00:00:00Z", False, "b/back"),
            "a/early": ("2025-01-01T00:00:00Z", False, "a/early-was"),
            "f/fork": ("2024-01-01T00:00:00Z", True, "f/fork"), "s/own": ("2026-02-01T00:00:00Z", False, "s/own")}
    names_cps = json.dumps({"strategy_options": {"checkpoint_remote": {"provider": "github", "repo": "o/cps"}}})
    where = {"base": srv.as_uri()}
    for name in [*held, "A/nometa"]:
        append(out / "fetch.jsonl", fetch_repo(name, out, settings=lambda r: None if r == "s/own" else names_cps,
                                               **where))
        link_repo(name, out, **where)
    (out / "discover").mkdir(parents=True, exist_ok=True)
    (out / "discover" / "repos.jsonl").write_text("".join(json.dumps({
        "query_repo": query, "full_name": name, "license": "MIT", "language": "Go", "fork": fork,
        "created_at": when}) + "\n" for name, (when, fork, query) in held.items())
        + json.dumps({"query_repo": "gone/x", "missing": True, "error": None}) + "\n")  # a 404: no full_name
    (out / "discover" / "commits.jsonl").write_text("")
    summary = assemble(out, log=lambda *_: None)
    corpus = out / "corpus"
    credited = {r["session_id"]: r["repo_id"] for r in pq.read_table(corpus / "sessions.parquet").to_pylist()}
    check(credited.get("g80") == "o/orig",
          "a session two copies also hold, with the original's settings and its trailers, one of them with no "
          f"metadata, is credited to the original, the earliest-created holder that is not a fork (G-80): "
          f"{credited.get('g80')}")
    check(credited.get("pair") == "z/late",
          f"a session only one holder's commits link stays with that holder, however new: {credited.get('pair')}")
    check(credited.get("orphan") == "a/early",
          "a session no holder's commits link goes to the earliest-created holder, never to a fork, its "
          f"creation read under the name it is fetched by: {credited.get('orphan')}")
    check(credited.get("forkonly") == "f/fork", f"a session only a fork's commits link goes to the fork: "
          f"{credited.get('forkonly')}")
    check(credited.get("early") == "b/back",
          "a session no holder links by its latest checkpoint goes to one that links an earlier checkpoint: "
          f"{credited.get('early')}")
    check(credited.get("late") == "z/late",
          "and a holder linking its latest checkpoint comes before an older one linking only an earlier "
          f"checkpoint: {credited.get('late')}")
    check(credited.get("multi") == "s/own",
          "each holder is judged by its own copy's checkpoints: a session a repository also holds in its own "
          f"refs, which its commits link, is credited to it: {credited.get('multi')}")
    repos = {r["repo_id"] for r in pq.read_table(corpus / "repositories.parquet").to_pylist()}
    check(summary["sessions"] == 7 and summary["left_out"] == {"the same session under a second repository": 43}
          and repos == {"a/early", "b/back", "f/fork", "o/orig", "s/own", "z/late"},
          f"each session is written once and every second copy counted; a repository credited with none is not "
          f"in the corpus: {summary['sessions']} sessions, {summary['left_out']}, {sorted(repos)}")
    rows = {(r["session_id"], r["repo_id"]) for r in
            pq.read_table(corpus / "conversations.parquet", columns=["session_id", "repo_id"]).to_pylist()}
    shas = {r["checkpoint_pk"]: json.loads(r["commit_shas"]) for r in
            pq.read_table(corpus / "checkpoints.parquet").to_pylist()}
    check(set(credited) == set(cps) and rows == {(s, credited[s]) for s in cps}
          and shas.get(f"o/orig#{cps['g80'][0]}") == [trailer_sha] and shas.get(f"b/back#{cps['early'][0]}") == [early_sha]
          and not any(pk.startswith(("C/copy#", "A/nometa#")) for pk in shas),
          "every table files a session under the repository credited, its checkpoints linked to that "
          "repository's commits")

print("\n" + ("ALL CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED"))
sys.exit(1 if FAIL else 0)
