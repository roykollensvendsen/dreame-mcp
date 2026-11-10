#!/usr/bin/env python3
"""Measure the code's quality levels, and refuse a change that makes one worse.

The levels are the ones Roy's phone page shows for every voice plugin:
coverage, the mutation tally (scripts/mutate.py), the number of tests, lint and
type errors, import cycles, the share of copied code, the most tangled function
in each module, and the suite's time. A push to the voice branch measures them
and keeps them with its run as the artifact `quality-levels`. A pull request is
compared with the levels its base kept, and fails when one gets worse, unless
its text has a line of its own naming the measure:

    Ratchet-Exception: coverage - the reason

    uv run python scripts/ratchet.py measure --out levels.json
    uv run python scripts/ratchet.py check --base REF [--levels FILE] [--skip-mutation]

`check` reads the pull request's text from RATCHET_EXCEPTIONS. When the base kept
no levels, it is measured in a copy of itself; if that cannot be done, the check
says so and passes, since there is nothing to compare with. `--skip-mutation`
takes the base's mutation tally instead of running mutate.py again: the
required `mutations` job already demands a full score on a pull request.
quality.json holds the suite's time budget, a limit set by hand.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

#: The tree to measure: this one, or a copy of the base (RATCHET_ROOT, see measure_at).
ROOT = Path(os.environ.get("RATCHET_ROOT") or Path(__file__).resolve().parent.parent)
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "dreame_mcp"
#: Percentage points coverage may drop by: it differs a little between machines.
COVERAGE_WOBBLE = 0.5
HIGHER = ("coverage", "mutation", "tests")
LOWER = ("lint_errors", "type_errors", "cycles", "duplication")
#: The most tangled function a new module may start with.
NEW_MODULE_CEILING = 15
#: Lines in a row, indentation ignored, that count as a copied block.
BLOCK = 6
KEYS = (*HIGHER, *LOWER, "complexity", "test_seconds")
EXCEPTION = re.compile(r"^\s*ratchet-exception:\s*([a-z_]+)", re.IGNORECASE | re.MULTILINE)
TALLY = re.compile(r"(\d+)/(\d+) rules failed the test named after them")
DEFAULT_BUDGET = {"seconds": 90, "tolerance": 0.25}


# -- measuring ----------------------------------------------------------------


def _branches(fn: ast.AST) -> int:
    """McCabe's count for one function: one, plus one per decision in it."""
    count = 1
    for node in ast.walk(fn):
        if isinstance(node, ast.If | ast.For | ast.AsyncFor | ast.While | ast.IfExp | ast.ExceptHandler):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += len(node.ifs)
        elif isinstance(node, ast.match_case):
            count += 1
    return count


def complexity(folder: Path) -> dict[str, int]:
    """For each module under ``folder``, its most tangled function's count."""
    found = {}
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scores = [_branches(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        if scores:
            found[path.relative_to(folder).as_posix()] = max(scores)
    return found


def _module_names(package_dir: Path) -> dict[str, Path]:
    base = package_dir.parent
    names = {}
    for path in package_dir.rglob("*.py"):
        parts = list(path.relative_to(base).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        names[".".join(parts)] = path
    return names


def _imports(name: str, path: Path, names: set[str]) -> set[str]:
    """The package's own modules that ``name`` imports."""
    is_package = path.name == "__init__.py"
    here = name if is_package else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = here.rsplit(".", node.level - 1)[0] if node.level > 1 else here
                module = f"{anchor}.{node.module}" if node.module else anchor
            else:
                module = node.module or ""
            targets = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        found |= {t for t in targets if t in names and t != name}
    return found


def cycles(package_dir: Path) -> int:
    """How many groups of the package's modules import each other in a ring."""
    modules = _module_names(package_dir)
    graph = {n: _imports(n, p, set(modules)) for n, p in modules.items()}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    rings = 0

    def visit(node: str) -> None:  # Tarjan's strongly connected components
        nonlocal rings
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            group = []
            while True:
                top = stack.pop()
                on_stack.discard(top)
                group.append(top)
                if top == node:
                    break
            rings += len(group) > 1

    for name in sorted(graph):
        if name not in index:
            visit(name)
    return rings


def duplication(folder: Path) -> float:
    """The share, in percent, of non-blank lines that sit in a block found more than once."""
    files = {
        p: [" ".join(line.split()) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        for p in sorted(folder.rglob("*.py"))
    }
    seen: dict[tuple[str, ...], list[tuple[Path, int]]] = {}
    for path, lines in files.items():
        for i in range(len(lines) - BLOCK + 1):
            seen.setdefault(tuple(lines[i : i + BLOCK]), []).append((path, i))
    copied = {(p, j) for spots in seen.values() if len(spots) > 1 for p, i in spots for j in range(i, i + BLOCK)}
    total = sum(len(lines) for lines in files.values())
    return round(100 * len(copied) / total, 2) if total else 0.0


def mutation_tally(output: str, returncode: int) -> dict[str, int]:
    """mutate.py's last line, as killed and total; a run that failed is never a full score."""
    tally = TALLY.search(output)
    killed, total = (int(tally[1]), int(tally[2])) if tally else (0, 1)
    return {"killed": killed if returncode == 0 else min(killed, total - 1), "total": total}


def _run(*command: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)


def measure(*, skip_mutation: bool = False, recorded: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every level, from the tree as it is now."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "coverage.json"
        started = time.monotonic()
        suite = _run(
            "uv", "run", "pytest", "-q", "-p", "no:cacheprovider", "--cov=dreame_mcp", f"--cov-report=json:{report}"
        )
        seconds = round(time.monotonic() - started, 1)
        if suite.returncode:
            raise SystemExit("The tests fail; only a green tree is measured.\n" + suite.stdout[-2000:])
        coverage = round(json.loads(report.read_text())["totals"]["percent_covered"], 2)
    passed = re.search(r"(\d+) passed", suite.stdout)
    lint = _run(
        "uv",
        "run",
        "ruff",
        "check",
        "src",
        "tests",
        "scripts/mutate.py",
        "scripts/ratchet.py",
        "--output-format",
        "json",
    )
    types = _run(
        "uv", "run", "pyright", "src", "--outputjson", env={**os.environ, "PYRIGHT_PYTHON_FORCE_VERSION": "latest"}
    )
    type_summary = json.loads(types.stdout or "{}").get("summary", {})
    if skip_mutation and recorded:
        mutation = recorded["mutation"]
    else:
        mutate = _run("uv", "run", "python", "scripts/mutate.py")
        mutation = mutation_tally(mutate.stdout, mutate.returncode)
    return {
        "coverage": coverage,
        "mutation": mutation,
        "tests": int(passed[1]) if passed else 0,
        "lint_errors": len(json.loads(lint.stdout or "[]")),
        "type_errors": int(type_summary.get("errorCount", 0)),
        "cycles": cycles(PACKAGE),
        "duplication": duplication(SOURCE),
        "complexity": complexity(SOURCE),
        "test_seconds": seconds,
    }


def measure_at(ref: str) -> dict[str, Any] | None:
    """The levels of ``ref``, measured in a copy of it; None when it cannot be measured."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "base"
        if subprocess.run(["git", "worktree", "add", "--detach", str(copy), ref], cwd=ROOT, check=False).returncode:
            return None
        try:
            done = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "measure", "--out", str(Path(tmp) / "levels.json")],
                cwd=copy,
                env={**os.environ, "RATCHET_ROOT": str(copy)},
                capture_output=True,
                text=True,
                check=False,
            )
            if done.returncode:
                sys.stderr.write(done.stdout[-1000:] + done.stderr[-1000:])
                return None
            return json.loads((Path(tmp) / "levels.json").read_text())
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(copy)], cwd=ROOT, check=False)


# -- comparing ----------------------------------------------------------------


def _score(value: Any) -> float:
    if isinstance(value, dict):  # the mutation tally: the share of rules whose test noticed
        return value["killed"] / value["total"] if value.get("total") else 0.0
    return float(value)


def _worse(metric: str, base: Any, now: Any) -> bool:
    wobble = COVERAGE_WOBBLE if metric == "coverage" else 0.0
    if metric in HIGHER:
        return _score(now) < _score(base) - wobble
    return _score(now) > _score(base)


def compare(base: dict[str, Any], now: dict[str, Any], budget: dict[str, Any], allowed: set[str]) -> list[str]:
    """What got worse than ``base`` and is not excused, one line each."""
    found = [
        f"{m} got worse: {base[m]} -> {now[m]}"
        for m in (*HIGHER, *LOWER)
        if m in base and m not in allowed and _worse(m, base[m], now[m])
    ]
    if "complexity" not in allowed:
        before = base.get("complexity", {})
        for module, score in now["complexity"].items():
            if module in before and score > before[module]:
                found.append(f"complexity of {module} got worse: {before[module]} -> {score}")
            elif module not in before and score > NEW_MODULE_CEILING:
                found.append(f"complexity of new {module} is {score}; keep it at {NEW_MODULE_CEILING} or under")
    limit = budget["seconds"] * (1 + budget["tolerance"])
    if "test_seconds" not in allowed and now["test_seconds"] > limit:
        found.append(
            f"test_seconds is {now['test_seconds']}, over the budget of {budget['seconds']} (+{budget['tolerance']:.0%})"
        )
    return found


def exceptions(text: str | None) -> set[str]:
    return {m.lower() for m in EXCEPTION.findall(text or "")}


def budget(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())["test_budget"]
    except (OSError, KeyError, ValueError):
        return dict(DEFAULT_BUDGET)


def write_levels(path: Path, now: dict[str, Any]) -> None:
    path.write_text(json.dumps({k: now[k] for k in KEYS}, indent=2, sort_keys=True) + "\n")


# -- the command line -----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["measure", "check"])
    parser.add_argument("--out", type=Path, help="write the levels here")
    parser.add_argument("--base", help="check: the commit this change lands on")
    parser.add_argument("--levels", type=Path, help="check: the levels the base kept")
    parser.add_argument("--skip-mutation", action="store_true", help="check: keep the base's mutation tally")
    args = parser.parse_args()

    if args.action == "measure":
        now = measure()
    else:
        if not args.base:
            parser.error("check needs --base")
        base = None
        if args.levels and args.levels.is_file():
            base = json.loads(args.levels.read_text())
        if base is None:
            print(f"The base {args.base} kept no levels; measuring it in a copy.")
            base = measure_at(args.base)
        now = measure(skip_mutation=args.skip_mutation and base is not None, recorded=base)
        if base is None:
            print("The base could not be measured, so there is nothing to compare with.")
        else:
            found = compare(base, now, budget(ROOT / "quality.json"), exceptions(os.environ.get("RATCHET_EXCEPTIONS")))
            for line in found:
                print(f"WORSE: {line}")
            if found:
                print("Make it better, or add a line 'Ratchet-Exception: <measure> - why' to the pull request.")
                return 1
    if args.out:
        write_levels(args.out, now)
    print(json.dumps({k: v for k, v in now.items() if k != "complexity"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
