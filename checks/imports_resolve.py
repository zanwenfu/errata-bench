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
stages. This resolves all of them without running any stage -- importing does execute
module bodies, which is what makes a broken one visible.

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


def resolve_from(node: ast.ImportFrom, package: str, where: str) -> None:
    label = "." * node.level + (node.module or "")
    try:
        target = importlib.import_module("." * node.level + (node.module or ""),
                                         package=package)
    except Exception as e:
        BAD.append(f"{where}: {label} -> {type(e).__name__}: {e}")
        return
    for alias in node.names:
        if alias.name == "*" or hasattr(target, alias.name):
            continue
        # `from . import leaf`: the name is a submodule Python will import on
        # demand, not an attribute the package already carries. Checking only
        # `hasattr` flagged every such line as broken until something else had
        # happened to import the leaf -- a verdict that depended on order.
        if node.module is None or hasattr(target, "__path__"):
            try:
                importlib.import_module(f"{target.__name__}.{alias.name}")
                continue
            except Exception:
                pass
        BAD.append(f"{where}: {label} has no {alias.name!r}")


def resolve_plain(node: ast.Import, where: str) -> None:
    """`import a.b.c`, which the restructure could break exactly like a from-import."""
    for alias in node.names:
        try:
            importlib.import_module(alias.name)
        except Exception as e:
            BAD.append(f"{where}: import {alias.name} -> {type(e).__name__}: {e}")


def optional(tree: ast.AST) -> set:
    """Imports inside a `try` whose handler catches ImportError: allowed to fail."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = any(
            (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError"))
            or (isinstance(h.type, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                for e in h.type.elts))
            for h in node.handlers)
        if catches:
            for sub in node.body:
                for n in ast.walk(sub):
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        out.add(n)
    return out


def main() -> int:
    top = deferred = 0
    for path in sorted(ROOT.rglob("*.py")):
        package = anchor(path)
        tree = ast.parse(path.read_text())
        inside = {
            n for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))
        }
        allowed_to_fail = optional(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node in allowed_to_fail:
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if node in inside:
                deferred += 1
            else:
                top += 1
            if isinstance(node, ast.ImportFrom):
                resolve_from(node, package, where)
            else:
                resolve_plain(node, where)

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
