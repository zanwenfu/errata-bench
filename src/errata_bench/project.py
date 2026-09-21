"""Where this checkout is, found rather than counted.

Both the corpus and the `.env` file live beside the package, not inside it, so
two modules need the repository root. Both got it with
`Path(__file__).resolve().parents[2]`, which is correct only for a file exactly
two directories down -- and the 09-20 restructure moved `corpus.py` to
`corpus/sessions.py`, one level deeper. `CORPUS` then pointed at
`src/data/swe-chat`, which does not exist, and every stage that reads the
corpus died on a pyarrow FileNotFoundError: triage, read, locate, screen and
build, which is most of the pipeline.

Nothing caught it. The check suites stub `load_session_turns` and
`load_repos` precisely so they need no corpus, and `checks/imports_resolve.py`
resolves imports rather than running module bodies.

So the root is located by looking for a file that marks it, and moving a module
cannot change the answer.
"""

from __future__ import annotations

from pathlib import Path

# pyproject.toml rather than .git: a source checkout without git history, or a
# worktree whose .git is a file, both still have it.
MARKER = "pyproject.toml"


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / MARKER).is_file():
            return parent
    # Installed somewhere without the marker. Fall back to the old arithmetic
    # rather than raising at import time, and let whoever needs a file under it
    # report a missing file instead.
    return here.parents[2]


ROOT = _find_root()
