#!/usr/bin/env python3
#
# The shape of the code: import cycles, imports made inside a function, and
# oversized modules and functions, in the packages the assignment pipeline
# lives in, may not grow.
#
# Why this exists: the 2026-11-17 review found the assignment pipeline
# answering the same questions in several places, and while that was being
# consolidated the shape of the code drifted with nothing to see it: the
# largest import cycle in the backend's peak_assignments package went from 8
# modules to 17 in four days, and the imports made inside a function to keep
# it importable from 8 to 26. A reviewer of one pull request sees neither:
# each adds two or three.
#
# Readings, per package, from source with `ast`, importing nothing:
#
#   cycles            the modules of each import cycle: the strongly connected
#                     components of the package's own import graph, imports
#                     made inside a function included
#   function_imports  how many import statements inside a function reach a
#                     module of the package (the way a cycle is kept
#                     importable, so it is the cycle read a second time)
#   modules           each module over MODULE_LINES lines, with its length
#   functions         each function over FUNCTION_LINES lines, by its
#                     qualified name, with its length
#
# `check` holds them to the committed baseline, tooling/structure_baseline.json:
# no new cycle and no cycle that takes in another module, no more imports
# inside a function, no module over the line growing by more than
# MODULE_GROWTH and none crossing it, no function over the line growing and
# none crossing it. A reading that got better passes, and `check` says how
# many readings have slack. The ratchet tightens on develop, not in pull
# requests: `tighten` lowers the baseline to the readings and never raises
# it, so feature pull requests do not all edit the same file. A pull request
# that has to make a reading worse runs `baseline` and commits the file, and
# its body says why under a `## Structure` heading.
#
# Commands - stdlib only, as tooling/changelog.py, whose Problem and report
# this shares:
#
#   measure   Print the readings of each package (or of the paths given).
#   check     Compare the readings with the baseline. CI runs it on every
#             pull request, in the "Lint and format" job.
#   baseline  Write the baseline from the tree as it is.
#   tighten   Lower each reading of the baseline that the tree beats; raise
#             none. "nothing to tighten" when it is already tight.
#
# Usage:
#   uv run python tooling/structure.py measure [PATH ...]
#   uv run python tooling/structure.py check
#   uv run python tooling/structure.py baseline
#   uv run python tooling/structure.py tighten
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import NamedTuple

from changelog import Problem, _plural, _write, report


ROOT = Path(__file__).resolve().parent.parent
BASELINE = "tooling/structure_baseline.json"

# The packages measured, as paths from the repository root.
PACKAGES = (
    "server/backend/src/mascope_backend/api/new/peak_assignments",
    "libraries/tools/src/mascope_tools/composition",
)

MODULE_LINES = 1500
# How much a module already over MODULE_LINES may grow, as a share of its
# length in the baseline.
MODULE_GROWTH = 0.02
FUNCTION_LINES = 100

ESCAPE = (
    "if this change has to make it worse, run "
    "`uv run python tooling/structure.py baseline`, commit "
    f"{BASELINE} in the same pull request, and say why in its body under a "
    "`## Structure` heading"
)
STALE = f"the baseline is stale, run `structure.py baseline` and commit {BASELINE}"


class StructureError(Exception):
    """A command that cannot go ahead; the message says why."""


class Function(NamedTuple):
    lines: int
    line: int  # where it starts, for the annotation


class Reading(NamedTuple):
    """What `measure` finds in one package."""

    modules: dict[str, int]  # every module, by its path in the package: lines
    cycles: list[list[str]]  # each sorted; the list sorted
    function_imports: list[tuple[str, int]]  # (module, line) of each
    functions: dict[str, Function]  # every function, by "module:qualname"


# ============= Reading the source =============


def _dotted(package: Path) -> str:
    """The package's import name: its path below the last `src` directory."""
    parts = package.parts
    if "src" in parts:
        below = parts[len(parts) - parts[::-1].index("src") :]
        if below:
            return ".".join(below)
    return package.name


def _module_name(dotted: str, relative: Path) -> tuple[str, bool]:
    """The import name of a module file and whether it is a package's __init__."""
    parts = list(relative.with_suffix("").parts)
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    return ".".join([dotted, *parts]), is_init


def _imported(node: ast.Import | ast.ImportFrom, name: str, is_init: bool) -> list[str]:
    """The dotted names an import statement asks for, relative ones resolved."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        base = name.split(".")
        if not is_init:
            base = base[:-1]
        if node.level > 1:
            base = base[: len(base) - (node.level - 1)]
        prefix = ".".join(base)
        source = f"{prefix}.{node.module}" if node.module else prefix
    else:
        source = node.module or ""
    # `from x import y` reads module x.y when there is one, else a name in x:
    # the longest name `_resolve` finds a module for.
    return [f"{source}.{alias.name}" for alias in node.names]


def _resolve(asked: list[str], names: dict[str, str]) -> set[str]:
    """The package's modules an import reaches, by the longest name it names."""
    reached = set()
    for dotted in asked:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            module = names.get(".".join(parts[:end]))
            if module is not None:
                reached.add(module)
                break
    return reached


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.depth = 0  # functions open around the node
        self.imports: list[tuple[ast.Import | ast.ImportFrom, bool]] = []
        self.functions: dict[str, Function] = {}

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        key = f"{self.module}:{qualname}"
        # A name defined twice (a property's setter, a def per branch) keeps
        # each, numbered in source order.
        number = 2
        while key in self.functions:
            key = f"{self.module}:{qualname}#{number}"
            number += 1
        self.functions[key] = Function(node.end_lineno - node.lineno + 1, node.lineno)
        self.scope.append(node.name)
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append((node, self.depth > 0))

    visit_ImportFrom = visit_Import


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """The strongly connected components of two or more modules (Tarjan's)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, edges = work[-1]
            for target in edges:
                if target not in index:
                    index[target] = low[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, iter(sorted(graph[target]))))
                    break
                if target in on_stack:
                    low[node] = min(low[node], index[target])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        components.append(sorted(component))
    return sorted(components)


def measure(package: Path) -> Reading:
    """The readings of the package at `package`, from its source alone."""
    if not package.is_dir():
        raise StructureError(f"{package}: no such package directory")
    dotted = _dotted(package)
    files = sorted(package.rglob("*.py"))
    modules: dict[str, int] = {}
    names: dict[str, str] = {}  # import name -> module path in the package
    trees: dict[str, tuple[ast.Module, str, bool]] = {}
    for path in files:
        relative = path.relative_to(package)
        module = relative.as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            raise StructureError(f"{path}: {error}") from error
        name, is_init = _module_name(dotted, relative)
        modules[module] = len(text.splitlines())
        names[name] = module
        trees[module] = (tree, name, is_init)

    graph: dict[str, set[str]] = {module: set() for module in modules}
    function_imports: list[tuple[str, int]] = []
    functions: dict[str, Function] = {}
    for module, (tree, name, is_init) in trees.items():
        walker = _Walker(module)
        walker.visit(tree)
        functions.update(walker.functions)
        for node, inside in walker.imports:
            reached = _resolve(_imported(node, name, is_init), names) - {module}
            graph[module] |= reached
            if inside and reached:
                function_imports.append((module, node.lineno))
    return Reading(modules, _cycles(graph), function_imports, functions)


def held(reading: Reading) -> dict:
    """The readings `check` holds, as the baseline writes them."""
    return {
        "cycles": reading.cycles,
        "function_imports": len(reading.function_imports),
        "modules": {
            module: lines
            for module, lines in sorted(reading.modules.items())
            if lines > MODULE_LINES
        },
        "functions": {
            key: function.lines
            for key, function in sorted(reading.functions.items())
            if function.lines > FUNCTION_LINES
        },
    }


# ============= Commands =============


def _package_problems(
    package: str, reading: Reading, base: dict
) -> tuple[list[Problem], list[str]]:
    """What got worse, as problems, and what got better, as short lines."""
    now = held(reading)
    worse: list[Problem] = []
    better: list[str] = []

    def at(module: str, line: int | None = None, message: str = "") -> None:
        worse.append(Problem(f"{package}/{module}", line, f"{message}; {ESCAPE}"))

    # A cycle is worse when it is new or takes in a module the baseline's
    # cycles did not join to it; one that split or shrank is better.
    base_cycles = [set(cycle) for cycle in base.get("cycles", [])]
    grown = False
    for cycle in now["cycles"]:
        members = set(cycle)
        if any(members <= old for old in base_cycles):
            continue
        grown = True
        old = max(base_cycles, key=lambda c: len(c & members), default=set())
        what = (
            f"takes in {', '.join(sorted(members - old))}"
            if old & members
            else "is new"
        )
        at(
            cycle[0],
            message=(
                f"an import cycle of {_plural(len(cycle), 'module')} {what} "
                f"({', '.join(cycle)})"
            ),
        )
    if not grown and sorted(sorted(c) for c in base_cycles) != now["cycles"]:
        better.append("an import cycle shrank or is gone")

    count, old_count = now["function_imports"], base.get("function_imports", 0)
    if count > old_count:
        where = reading.function_imports[-1]
        lines = ", ".join(f"{m}:{n}" for m, n in reading.function_imports)
        at(
            where[0],
            where[1],
            f"{count} imports of the package made inside a function, "
            f"{old_count} in the baseline ({lines})",
        )
    elif count < old_count:
        better.append(f"imports inside a function {old_count} -> {count}")

    old_modules = base.get("modules", {})
    for module, lines in now["modules"].items():
        old = old_modules.get(module)
        if old is None:
            at(module, message=f"{lines} lines, over the {MODULE_LINES}-line line")
        elif lines > int(old * (1 + MODULE_GROWTH)):
            at(
                module,
                message=(
                    f"{lines} lines, {old} in the baseline: more than "
                    f"{MODULE_GROWTH:.0%} longer"
                ),
            )
        elif lines < old:
            better.append(f"{module} {old} -> {lines} lines")
    for module, old in old_modules.items():
        if module not in now["modules"]:
            lines = reading.modules.get(module)
            better.append(
                f"{module} {old} -> {lines} lines"
                if lines is not None
                else f"{module} is gone"
            )

    old_functions = base.get("functions", {})
    for key, lines in now["functions"].items():
        module, qualname = key.split(":", 1)
        line = reading.functions[key].line
        old = old_functions.get(key)
        if old is None:
            at(
                module,
                line,
                f"{qualname} is {lines} lines, over the {FUNCTION_LINES}-line line",
            )
        elif lines > old:
            at(module, line, f"{qualname} is {lines} lines, {old} in the baseline")
        elif lines < old:
            better.append(f"{key} {old} -> {lines} lines")
    for key, old in old_functions.items():
        if key not in now["functions"]:
            function = reading.functions.get(key)
            better.append(
                f"{key} {old} -> {function.lines} lines"
                if function is not None
                else f"{key} is gone"
            )
    return worse, better


def _load_baseline(root: Path) -> dict:
    path = root / BASELINE
    if not path.is_file():
        raise StructureError(f"{BASELINE} is missing; run `structure.py baseline`")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StructureError(f"{BASELINE}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise StructureError(f"{BASELINE}: no `packages` map")
    return data


def check(root: Path, packages: tuple[str, ...] = PACKAGES) -> list[Problem]:
    """What got worse than the baseline, or a baseline of other limits or
    packages, as problems. A reading that got better is no problem."""
    return readings(root, packages)[0]


def readings(
    root: Path, packages: tuple[str, ...] = PACKAGES
) -> tuple[list[Problem], dict[str, list[str]]]:
    """The problems `check` reports, and per package what got better."""
    data = _load_baseline(root)
    recorded = data["packages"]
    problems: list[Problem] = []
    if data.get("limits") != baseline(root, ())["limits"]:
        problems.append(
            Problem(
                BASELINE,
                None,
                f"it was written with other limits than the check's; {STALE}",
            )
        )
        return problems, {}
    if sorted(recorded) != sorted(packages):
        problems.append(
            Problem(
                BASELINE,
                None,
                f"it records {', '.join(sorted(recorded)) or 'no package'}, "
                f"the check measures {', '.join(packages)}; {STALE}",
            )
        )
        return problems, {}
    better: dict[str, list[str]] = {}
    for package in packages:
        reading = measure(root / package)
        worse, improved = _package_problems(package, reading, recorded[package])
        problems += worse
        if improved:
            better[package] = improved
    return problems, better


def tightened(root: Path, packages: tuple[str, ...] = PACKAGES) -> dict:
    """The baseline with each reading the tree beats lowered to it.

    No reading goes up: one that got worse keeps its baseline value, a module
    or function that is new over the line is not added, and a cycle set that
    grew anywhere is kept as it was.
    """
    data = _load_baseline(root)
    problems, _ = readings(root, packages)
    if any(problem.path == BASELINE for problem in problems):
        raise StructureError(
            f"{BASELINE} does not match the check's limits or packages; "
            "run `structure.py baseline`"
        )
    out = json.loads(json.dumps(data))
    for package in packages:
        base = data["packages"][package]
        now = held(measure(root / package))
        base_cycles = [set(cycle) for cycle in base.get("cycles", [])]
        if all(any(set(c) <= old for old in base_cycles) for c in now["cycles"]):
            cycles = now["cycles"]
        else:
            cycles = base.get("cycles", [])
        modules = {
            module: min(old, now["modules"][module])
            for module, old in base.get("modules", {}).items()
            if module in now["modules"]
        }
        functions = {
            key: min(old, now["functions"][key])
            for key, old in base.get("functions", {}).items()
            if key in now["functions"]
        }
        out["packages"][package] = {
            "cycles": cycles,
            "function_imports": min(
                base.get("function_imports", 0), now["function_imports"]
            ),
            "modules": modules,
            "functions": functions,
        }
    return out


def baseline(root: Path, packages: tuple[str, ...] = PACKAGES) -> dict:
    """The baseline of the tree at `root`, as `baseline` writes it."""
    return {
        "limits": {
            "module_lines": MODULE_LINES,
            "module_growth": MODULE_GROWTH,
            "function_lines": FUNCTION_LINES,
        },
        "packages": {package: held(measure(root / package)) for package in packages},
    }


def summary(package: str, reading: Reading) -> list[str]:
    """The readings of one package, as `measure` prints them."""
    largest = max(reading.modules.items(), key=lambda item: item[1], default=None)
    over = sorted(
        (
            (function.lines, key)
            for key, function in reading.functions.items()
            if function.lines > FUNCTION_LINES
        ),
        reverse=True,
    )
    cycle = max(reading.cycles, key=len, default=[])
    lines = [
        package,
        f"  modules                              {len(reading.modules)}",
        f"  lines                                {sum(reading.modules.values()):,}",
        f"  functions over {FUNCTION_LINES} lines              {len(over)}",
        f"  import cycles                        {len(reading.cycles)}",
        f"  modules in the largest import cycle  {len(cycle)}",
        f"  imports of the package in functions  {len(reading.function_imports)}",
    ]
    if largest:
        lines.append(
            f"  largest module                       {largest[0]}, {largest[1]:,} lines"
        )
    for module, count in sorted(reading.modules.items(), key=lambda i: -i[1]):
        if count > MODULE_LINES:
            lines.append(f"    over {MODULE_LINES}: {module} {count:,}")
    for count, key in over:
        lines.append(f"    over {FUNCTION_LINES}: {key} {count}")
    for members in reading.cycles:
        lines.append(f"    cycle of {len(members)}: {', '.join(members)}")
    return lines


# ============= main =============


def main(
    argv: list[str] | None = None,
    root: Path = ROOT,
    packages: tuple[str, ...] = PACKAGES,
) -> int:
    parser = argparse.ArgumentParser(
        description="Import cycles and oversized modules and functions."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    measuring = commands.add_parser("measure", help="print the readings")
    measuring.add_argument(
        "paths", nargs="*", help="package directories (default: the checked ones)"
    )
    commands.add_parser("check", help="what CI checks")
    commands.add_parser("baseline", help=f"write {BASELINE} from the tree")
    commands.add_parser(
        "tighten", help=f"lower {BASELINE} to the readings the tree beats"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "measure":
            for path in args.paths or packages:
                print("\n".join(summary(path, measure(root / path))))
        elif args.command == "check":
            problems, better = readings(root, packages)
            for package, lines in better.items():
                print(
                    f"structure: {package}: "
                    f"{_plural(len(lines), 'reading')} better than the baseline "
                    f"({'; '.join(lines)}); `structure.py tighten` locks them in"
                )
            report(problems)
            if problems:
                print(
                    f"structure: {_plural(len(problems), 'problem')}", file=sys.stderr
                )
                return 1
            print(f"structure: ok ({_plural(len(packages), 'package')})")
        elif args.command == "baseline":
            (root / BASELINE).parent.mkdir(parents=True, exist_ok=True)
            _write(
                root / BASELINE, json.dumps(baseline(root, packages), indent=2) + "\n"
            )
            print(f"structure: wrote {BASELINE}")
        elif args.command == "tighten":
            path = root / BASELINE
            new = json.dumps(tightened(root, packages), indent=2) + "\n"
            if json.loads(new) == json.loads(path.read_text(encoding="utf-8")):
                print("structure: nothing to tighten")
            else:
                _write(path, new)
                print(f"structure: tightened {BASELINE}")
    except StructureError as error:
        print(f"structure: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
