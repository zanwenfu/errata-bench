"""Every import in the package resolves, including the deferred ones.

Most imports here are inside function bodies. That began as a workaround for a
cycle between pipeline.py and spec.py, and outlived it: the stages import
their collaborators lazily so a stage that is not running costs nothing to
load. The cost is that a wrong one fails only when that branch runs, and the
branches are the expensive stages.

The 09-20 restructure moved every module in the package, and two of these were
rewritten to the wrong target -- `load_session_turns` from `..store`, which
does not have it, in `stage_triage` and `stage_locate`. Importing every module
did not catch it, because a deferred import is not executed at import time,
and the check suites did not either, because none of them runs those two
stages. This resolves all of them without running anything.

    .venv/bin/python checks/imports_resolve.py
"""
import ast
import importlib
import pathlib
import sys

sys.path.insert(0, "src")

ROOT = pathlib.Path("src/errata_bench")
BAD = []


def module_name(path: pathlib.Path) -> str:
    rel = str(path.relative_to(ROOT).with_suffix("")).replace("/", ".")
    return ("errata_bench." + rel).replace(".__init__", "")


def anchor(path: pathlib.Path) -> str:
    """The package a relative import in this file resolves against.

    For an `__init__.py` that is the package itself, not its parent: inside
    `store/__init__.py`, `from .rows import` means `errata_bench.store.rows`.
    """
    mod = module_name(path)
    return mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]


def resolve(node: ast.ImportFrom, package: str, where: str) -> None:
    try:
        target = importlib.import_module("." * node.level + (node.module or ""),
                                         package=package)
    except Exception as e:
        BAD.append(f"{where}: {'.' * node.level}{node.module} -> {type(e).__name__}: {e}")
        return
    for alias in node.names:
        if alias.name != "*" and not hasattr(target, alias.name):
            BAD.append(f"{where}: {'.' * node.level}{node.module} has no {alias.name!r}")


def main() -> int:
    top = deferred = 0
    for path in sorted(ROOT.rglob("*.py")):
        package = anchor(path)
        tree = ast.parse(path.read_text())
        inside = {
            n for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if node in inside:
                deferred += 1
            else:
                top += 1
            resolve(node, package, where)

    # The other thing a module move breaks silently: a path built by counting
    # parent directories. CORPUS was `parents[2]`, right for
    # errata_bench/corpus.py and one level short for
    # errata_bench/corpus/sessions.py -- so it pointed at src/data/swe-chat and
    # every corpus-reading stage died on a pyarrow FileNotFoundError.
    from errata_bench.corpus.sessions import CORPUS
    from errata_bench.project import ROOT as CHECKOUT

    if not (CHECKOUT / "pyproject.toml").is_file():
        BAD.append(f"project.ROOT is {CHECKOUT}, which is not this checkout")
    if not CORPUS.is_dir():
        BAD.append(f"CORPUS is {CORPUS}, which does not exist")
    for name in ("conversations.parquet", "sessions.parquet", "repositories.parquet"):
        if CORPUS.is_dir() and not (CORPUS / name).is_file():
            BAD.append(f"the corpus has no {name}")

    print(f"\n  {top} module-level and {deferred} deferred imports, "
          f"{len(BAD)} broken\n")
    for b in BAD:
        print(f"  BROKEN  {b}")
    if not BAD:
        print("  every import resolves, and the corpus is where the code looks")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
