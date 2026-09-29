"""What Entire writes into a repository, and how to read it (#16).

Read from Entire's CLI source (github.com/entireio/cli at bc287bb,
``docs/architecture/sessions-and-checkpoints.md`` and
``ref-checkpoint-backend.md``) and checked on real repositories on 09-29.

A **checkpoint** is written when a commit is made during an agent session. Its
tree holds ``metadata.json`` (the checkpoint's summary) and one numbered folder
per session, ``0/``, ``1/``, ..., each with the session's ``metadata.json``,
``full.jsonl`` (the agent's own transcript, secrets redacted), ``prompt.txt``
and a normalized ``transcript.jsonl``. Every checkpoint holds the whole session
up to that point, so a session in several checkpoints is read from its latest.

Where the checkpoints live depends on the repository's setup:

- ``refs/heads/entire/checkpoints/v1``: one branch, each checkpoint a subtree
  at ``<id[:2]>/<id[2:]>/``, with 12-hex ids. The default until v0.9.0
  (27 July 2026), and SWE-chat's only source.
- ``refs/entire/checkpoints/<shard>/<id>``: one ref per checkpoint, the tree's
  root being the checkpoint, with ULID ids (or hex ids when migrated). The
  shard is the id's last two characters. The default for new setups since
  v0.9.0. Not a branch: invisible to branch lists, to code search and to an
  ordinary clone, but listed by ``git ls-remote`` and fetched ref by ref.
- ``refs/entire/checkpoints/v2/...``: an April--May dual-write, reverted; the
  same checkpoints are on the v1 branch. Not read.
- A separate repository named by ``strategy_options.checkpoint_remote`` in
  ``.entire/settings.json`` (``{"provider": "github", "repo": "org/repo"}``),
  holding either layout. Often private.

A code commit links to its checkpoint with an ``Entire-Checkpoint: <id>``
trailer. Per-turn snapshots of the working tree are never pushed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

V1_BRANCH = "refs/heads/entire/checkpoints/v1"
REF_PREFIX = "refs/entire/checkpoints/"

# The id is 12 hex characters (the branch's format, or migrated into refs) or a
# 26-character Crockford ULID (minted under refs), and the shard is its last
# two characters (`id.ShardFor`). Anything else under the prefix -- the v2
# layout's `v2/main`, `v2/full/<n>` -- is not a checkpoint ref.
_ID = r"(?:[0-9a-f]{12}|[0-9A-HJKMNP-TV-Z]{26})"
PER_CHECKPOINT_REF = re.compile(rf"^refs/entire/checkpoints/([0-9A-Za-z]{{2}})/({_ID})$")
# A checkpoint's subtree on the v1 branch: <first two>/<the other ten>/metadata.json.
V1_CHECKPOINT = re.compile(r"^([0-9a-f]{2})/([0-9a-f]{10})/metadata\.json$")
TRAILER = re.compile(rf"^Entire-Checkpoint:[ \t]*({_ID})[ \t]*$", re.M)
# Session ids become file names. Entire validates them at its own boundaries
# (#1365, #2405); this is the same caution on ours.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def per_checkpoint_id(ref: str) -> str | None:
    """The checkpoint id of a per-checkpoint ref, when its shard is the id's own."""
    m = PER_CHECKPOINT_REF.match(ref)
    if not m or m[1] != m[2][-2:]:
        return None
    return m[2]


@dataclass(frozen=True)
class Layout:
    """Which of Entire's layouts a repository's refs hold."""

    v1_branch: bool
    per_checkpoint: tuple[str, ...]  # checkpoint ids
    v2: bool

    @property
    def has_checkpoints(self) -> bool:
        return self.v1_branch or bool(self.per_checkpoint)


def layout_of(refs: dict[str, str]) -> Layout:
    """Read a repository's layout from its advertised refs (`git ls-remote`)."""
    ids = tuple(sorted(i for i in (per_checkpoint_id(r) for r in refs) if i))
    return Layout(v1_branch=V1_BRANCH in refs, per_checkpoint=ids,
                  v2=any(r.startswith(REF_PREFIX + "v2/") for r in refs))


def v1_checkpoints(paths: list[str]) -> list[tuple[str, str]]:
    """(checkpoint id, subtree prefix) for every checkpoint in a v1 branch's tree listing."""
    out = []
    for path in paths:
        m = V1_CHECKPOINT.match(path)
        if m:
            out.append((m[1] + m[2], f"{m[1]}/{m[2]}/"))
    return out


def trailer_ids(message: str) -> list[str]:
    """The checkpoint ids a commit message links to, in order, without repeats."""
    seen: dict[str, None] = {}
    for cid in TRAILER.findall(message or ""):
        seen.setdefault(cid, None)
    return list(seen)


def checkpoint_remote(settings_json: str | None) -> str | None:
    """The public-GitHub checkpoint repository a settings file names, as owner/name, or None.

    Only a GitHub repository is followed. The setting also accepts GitLab, and
    a local-only settings file can name one; neither is ours to find.
    """
    if not settings_json:
        return None
    try:
        settings = json.loads(settings_json)
    except ValueError:
        return None
    value = ((settings.get("strategy_options") or {}) if isinstance(settings, dict) else {}).get("checkpoint_remote")
    if isinstance(value, dict) and str(value.get("provider", "")).lower() == "github":
        repo = str(value.get("repo") or "")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return repo
    return None


def created(value: str | None) -> datetime:
    """A metadata timestamp as UTC; the earliest possible time when absent or unreadable."""
    if value:
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Copy:
    """One session's entry in one checkpoint."""

    checkpoint_id: str
    tree: str  # where the session's folder is: "<rev>:<prefix><index>"
    metadata: dict

    @property
    def session_id(self) -> str:
        return str(self.metadata.get("session_id") or "")

    @property
    def imported(self) -> bool:
        return self.metadata.get("kind") == "imported"


def latest_copies(copies: list[Copy]) -> dict[str, tuple[Copy, list[str]]]:
    """Each session's latest copy, with every checkpoint it appears in, oldest first.

    Every checkpoint holds the whole session so far, so the latest holds it all.
    On the pilot's repository the latest copy was also the longest in every
    session, ties included. SWE-chat kept "the record with the highest
    output_tokens", but a checkpoint's token counts are for its own slice of
    the session, not the whole: the longest copy there (194 lines) carried
    fewer output tokens than the shortest (75).
    """
    by_session: dict[str, list[Copy]] = {}
    for c in copies:
        by_session.setdefault(c.session_id, []).append(c)
    out = {}
    for sid, cs in by_session.items():
        ordered = sorted(cs, key=lambda c: (created(c.metadata.get("created_at")), c.checkpoint_id))
        out[sid] = (ordered[-1], [c.checkpoint_id for c in ordered])
    return out
