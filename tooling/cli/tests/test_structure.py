"""
Guards for `tooling/structure.py`, which holds the shape of the assignment
packages to a committed baseline: no new import cycle, no more imports made
inside a function, no oversized module or function that grows, and none that
crosses the line.

What it is for is that the shape cannot get worse one pull request at a time
without the pull request saying so: each reading that got worse fails the
check and names the file. A reading that got better passes, so feature pull
requests do not all edit the baseline; `tighten` lowers it on develop, and
never raises a reading. What it must not
do is import the code it measures: it reads source with `ast` alone.

Each test writes a small package into a temporary directory, takes its
baseline, changes the package and checks it again.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLING = REPO_ROOT / "tooling"
SCRIPT = TOOLING / "structure.py"

# Guarded the same way as test_changelog.py: repo-root tooling/ is not present
# in every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip("repo-root tooling/structure.py not available", allow_module_level=True)


def _load():
    # structure.py imports changelog.py from beside it, as
    # `python3 tooling/structure.py` finds it when CI runs the script.
    sys.path.insert(0, str(TOOLING))
    try:
        spec = importlib.util.spec_from_file_location("structure", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOLING))
    return module


structure = _load()

PACKAGE = "src/pkg"
PACKAGES = (PACKAGE,)


def _body(lines: int) -> str:
    """The statements of a function `lines` long, its `def` line included."""
    return "\n".join(f"    x{n} = {n}" for n in range(lines - 1))


def _function(name: str, lines: int) -> str:
    return f"def {name}():\n{_body(lines)}\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / PACKAGE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _tree(root: Path, **changes: str) -> None:
    """A package with no cycle, one long function and one long module."""
    files = {
        "__init__.py": "",
        "a.py": "from . import b\n\n" + _function("long", 120),
        "b.py": "import json\n",
        "c.py": "from pkg import a\n",
        "big.py": "\n".join(f"x{n} = {n}" for n in range(1600)) + "\n",
    }
    files.update(changes)
    _write(root, files)


def _take_baseline(root: Path) -> None:
    assert structure.main(["baseline"], root=root, packages=PACKAGES) == 0


def _messages(root: Path) -> list[str]:
    return [problem.message for problem in structure.check(root, PACKAGES)]


def _better(root: Path) -> list[str]:
    """What got better, which must pass the check."""
    problems, better = structure.readings(root, PACKAGES)
    assert problems == []
    return better.get(PACKAGE, [])


@pytest.fixture
def root(tmp_path):
    _tree(tmp_path)
    _take_baseline(tmp_path)
    return tmp_path


def test_an_unchanged_tree_passes(root, capsys):
    assert structure.main(["check"], root=root, packages=PACKAGES) == 0
    assert "structure: ok" in capsys.readouterr().out


def test_an_unchanged_cycle_passes(tmp_path, capsys):
    _tree(tmp_path, **{"b.py": "from . import a\n"})
    _take_baseline(tmp_path)
    assert structure.main(["check"], root=tmp_path, packages=PACKAGES) == 0
    assert "structure: ok" in capsys.readouterr().out


def test_an_async_function_is_measured_as_a_function_is(tmp_path):
    async_long = "async " + _function("handler", 120)
    late = "async def late():\n    from . import a\n"
    _tree(tmp_path, **{"b.py": async_long + "\n" + late})
    _take_baseline(tmp_path)
    data = json.loads((tmp_path / structure.BASELINE).read_text())
    held = data["packages"][PACKAGE]
    assert held["functions"]["b.py:handler"] == 120
    assert held["function_imports"] == 1
    # The import inside the async def closes the cycle a.py -> b.py -> a.py.
    assert held["cycles"] == [["a.py", "b.py"]]


def test_the_baseline_records_each_held_reading(root):
    data = json.loads((root / structure.BASELINE).read_text())
    assert data["packages"][PACKAGE] == {
        "cycles": [],
        "function_imports": 0,
        "modules": {"big.py": 1600},
        "functions": {"a.py:long": 120},
    }


def test_a_new_cycle_fails_and_names_the_way_out(root, capsys):
    _tree(root, **{"b.py": "from .a import long\n"})
    assert structure.main(["check"], root=root, packages=PACKAGES) == 1
    out = "".join(capsys.readouterr())  # stderr here, stdout in Actions
    assert "an import cycle of 2 modules is new (a.py, b.py)" in out
    assert "structure.py baseline" in out
    assert "## Structure" in out


def test_a_cycle_through_a_function_level_import_counts(root):
    _tree(root, **{"b.py": "def late():\n    from . import a\n"})
    messages = _messages(root)
    assert any("an import cycle of 2 modules is new" in m for m in messages)
    assert any("1 imports of the package made inside a function" in m for m in messages)


def test_a_cycle_that_takes_in_another_module_fails(tmp_path):
    _tree(tmp_path, **{"b.py": "from . import a\n"})
    _take_baseline(tmp_path)
    _tree(tmp_path, **{"b.py": "from . import a\nfrom . import c\n"})
    (message,) = _messages(tmp_path)
    assert "an import cycle of 3 modules takes in c.py (a.py, b.py, c.py)" in message


def test_a_cycle_that_shrinks_passes_with_slack(tmp_path):
    _tree(tmp_path, **{"b.py": "from . import a\nfrom . import c\n"})
    _take_baseline(tmp_path)
    _tree(tmp_path, **{"b.py": "from . import a\n"})
    assert _better(tmp_path) == ["an import cycle shrank or is gone"]


def test_a_new_function_level_import_fails(root):
    _tree(root, **{"c.py": "from pkg import a\n\ndef late():\n    from . import b\n"})
    (problem,) = structure.check(root, PACKAGES)
    assert problem.path == f"{PACKAGE}/c.py"
    assert problem.line == 4
    assert "1 imports of the package made inside a function, 0 in the baseline" in (
        problem.message
    )


def test_a_function_level_import_from_outside_the_package_is_not_counted(root):
    _tree(root, **{"c.py": "from pkg import a\n\ndef late():\n    import json\n"})
    assert _messages(root) == []


def test_fewer_function_level_imports_pass_with_slack(tmp_path):
    _tree(tmp_path, **{"c.py": "def late():\n    from . import b\n"})
    _take_baseline(tmp_path)
    _tree(tmp_path)
    assert _better(tmp_path) == ["imports inside a function 1 -> 0"]


def test_a_function_that_grows_past_the_line_fails(root):
    _tree(root, **{"b.py": _function("grew", 101)})
    (problem,) = structure.check(root, PACKAGES)
    assert problem.path == f"{PACKAGE}/b.py"
    assert problem.line == 1
    assert "grew is 101 lines, over the 100-line line" in problem.message


def test_a_function_of_exactly_the_line_passes(root):
    _tree(root, **{"b.py": _function("held", 100)})
    assert _messages(root) == []


def test_a_long_function_that_grows_by_one_line_fails(root):
    _tree(root, **{"a.py": "from . import b\n\n" + _function("long", 121)})
    (message,) = _messages(root)
    assert "long is 121 lines, 120 in the baseline" in message


def test_methods_and_nested_functions_go_by_their_qualified_name(root):
    method = "class K:\n" + "\n".join(
        "    " + line for line in _function("m", 101).splitlines()
    )
    _tree(root, **{"b.py": method + "\n"})
    (message,) = _messages(root)
    assert "K.m is 101 lines" in message


def test_a_long_function_that_shrinks_passes_with_slack(root):
    _tree(root, **{"a.py": "from . import b\n\n" + _function("long", 119)})
    assert _better(root) == ["a.py:long 120 -> 119 lines"]


def test_a_long_function_that_is_gone_passes_with_slack(root):
    _tree(root, **{"a.py": "from . import b\n"})
    assert _better(root) == ["a.py:long is gone"]


def test_a_long_module_may_grow_by_two_percent(root):
    _tree(root, **{"big.py": "x = 0\n" * 1632})
    assert _messages(root) == []


def test_a_long_module_that_grows_by_more_fails(root):
    _tree(root, **{"big.py": "x = 0\n" * 1633})
    (message,) = _messages(root)
    assert "1633 lines, 1600 in the baseline: more than 2% longer" in message


def test_a_new_module_over_the_line_fails(root):
    _tree(root, **{"b.py": "x = 0\n" * 1501})
    (problem,) = structure.check(root, PACKAGES)
    assert problem.path == f"{PACKAGE}/b.py"
    assert "1501 lines, over the 1500-line line" in problem.message


def test_a_module_that_shrinks_passes_and_says_so(root, capsys):
    _tree(root, **{"big.py": "x = 0\n" * 1599})
    assert structure.main(["check"], root=root, packages=PACKAGES) == 0
    out = capsys.readouterr().out
    assert (
        f"structure: {PACKAGE}: 1 reading better than the baseline "
        "(big.py 1600 -> 1599 lines); `structure.py tighten` locks them in"
    ) in out
    assert "structure: ok" in out


def test_a_better_reading_does_not_excuse_a_worse_one(root, capsys):
    _tree(root, **{"big.py": "x = 0\n" * 1599, "b.py": _function("grew", 101)})
    (message,) = _messages(root)
    assert "grew is 101 lines" in message
    assert structure.main(["check"], root=root, packages=PACKAGES) == 1
    assert "1 reading better" in capsys.readouterr().out


def _baseline_data(root: Path) -> dict:
    return json.loads((root / structure.BASELINE).read_text())


def test_tighten_on_a_tight_baseline_changes_nothing(root, capsys):
    before = (root / structure.BASELINE).read_bytes()
    assert structure.main(["tighten"], root=root, packages=PACKAGES) == 0
    assert "nothing to tighten" in capsys.readouterr().out
    assert (root / structure.BASELINE).read_bytes() == before


def test_tighten_lowers_each_better_reading(tmp_path, capsys):
    _tree(
        tmp_path,
        **{
            "b.py": "from . import a\n",
            "c.py": "def late():\n    from . import b\n",
            "d.py": _function("other", 130),
        },
    )
    _take_baseline(tmp_path)
    _tree(
        tmp_path,
        **{
            "a.py": "from . import b\n\n" + _function("long", 110),
            "big.py": "x = 0\n" * 1550,
            "d.py": "",
        },
    )
    assert structure.main(["tighten"], root=tmp_path, packages=PACKAGES) == 0
    assert "tightened" in capsys.readouterr().out
    held = _baseline_data(tmp_path)["packages"][PACKAGE]
    assert held == {
        "cycles": [],
        "function_imports": 0,
        "modules": {"big.py": 1550},
        "functions": {"a.py:long": 110},
    }
    assert _better(tmp_path) == []


def test_tighten_drops_a_module_under_the_line(root):
    _tree(root, **{"big.py": "x = 0\n" * 1400})
    structure.main(["tighten"], root=root, packages=PACKAGES)
    assert _baseline_data(root)["packages"][PACKAGE]["modules"] == {}


def test_tighten_raises_no_reading(tmp_path):
    _tree(tmp_path, **{"b.py": "from . import a\n"})
    _take_baseline(tmp_path)
    before = _baseline_data(tmp_path)["packages"][PACKAGE]
    # Every reading worse, plus one better to make tighten write.
    _tree(
        tmp_path,
        **{
            "a.py": "from . import b\n\n" + _function("long", 119),
            "b.py": "from . import a\nfrom . import c\n" + _function("new", 101),
            "c.py": "def late():\n    from . import b\n",
            "big.py": "x = 0\n" * 1700,
            "huge.py": "x = 0\n" * 1600,
        },
    )
    structure.main(["tighten"], root=tmp_path, packages=PACKAGES)
    after = _baseline_data(tmp_path)["packages"][PACKAGE]
    assert after["cycles"] == before["cycles"]
    assert after["function_imports"] == before["function_imports"]
    assert after["modules"] == before["modules"]
    assert after["functions"] == {"a.py:long": 119}
    assert len(_messages(tmp_path)) == 5


def test_tighten_refuses_a_baseline_of_other_limits(root, capsys):
    path = root / structure.BASELINE
    data = json.loads(path.read_text())
    data["limits"]["function_lines"] = 200
    path.write_text(json.dumps(data))
    assert structure.main(["tighten"], root=root, packages=PACKAGES) == 1
    assert "run `structure.py baseline`" in capsys.readouterr().err


def test_relative_imports_resolve_through_subpackages(tmp_path):
    _write(
        tmp_path,
        {
            "__init__.py": "",
            "top.py": "from .sub.leaf import thing\n",
            "sub/__init__.py": "",
            "sub/leaf.py": "from .. import top\nthing = 1\n",
        },
    )
    reading = structure.measure(tmp_path / PACKAGE)
    assert reading.cycles == [["sub/leaf.py", "top.py"]]


def test_importing_a_name_from_the_package_reaches_its_init(tmp_path):
    _write(
        tmp_path,
        {
            "__init__.py": "from .a import x\n",
            "a.py": "from pkg import y\nx = 1\n",
        },
    )
    assert structure.measure(tmp_path / PACKAGE).cycles == [["__init__.py", "a.py"]]


def test_importing_a_module_of_the_package_does_not_reach_its_init(tmp_path):
    # `from . import b` asks for module b; the package's __init__ is loaded
    # first whatever a module imports, so it is no edge of the graph.
    _write(
        tmp_path,
        {"__init__.py": "from . import a\n", "a.py": "from . import b\n", "b.py": ""},
    )
    assert structure.measure(tmp_path / PACKAGE).cycles == []


def test_measure_imports_nothing(tmp_path, capsys):
    _write(
        tmp_path,
        {"__init__.py": "raise SystemExit('imported')\n", "a.py": "import nowhere\n"},
    )
    assert structure.main(["measure", PACKAGE], root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "modules                              2" in out


def test_a_missing_baseline_says_how_to_write_one(tmp_path, capsys):
    _tree(tmp_path)
    assert structure.main(["check"], root=tmp_path, packages=PACKAGES) == 1
    assert "run `structure.py baseline`" in capsys.readouterr().err


def test_a_baseline_of_other_packages_is_stale(root):
    other = ("src/pkg", "src/other")
    (root / "src/other").mkdir()
    (message,) = [p.message for p in structure.check(root, other)]
    assert "the baseline is stale" in message


def test_a_baseline_written_with_other_limits_is_stale(root):
    path = root / structure.BASELINE
    data = json.loads(path.read_text())
    data["limits"]["function_lines"] = 200
    path.write_text(json.dumps(data))
    (message,) = _messages(root)
    assert "other limits" in message


def test_the_repository_is_measured_in_under_five_seconds():
    start = time.monotonic()
    data = structure.baseline(REPO_ROOT)
    assert time.monotonic() - start < 5
    assert sorted(data["packages"]) == sorted(structure.PACKAGES)


def test_a_module_that_drops_under_the_line_passes_with_slack(root):
    _tree(root, **{"big.py": "x = 0\n" * 1400})
    assert _better(root) == ["big.py 1600 -> 1400 lines"]


def test_a_module_of_exactly_the_line_passes(root):
    _tree(root, **{"b.py": "x = 0\n" * 1500})
    assert _messages(root) == []


def test_a_name_defined_twice_keeps_each_definition(tmp_path):
    getter = "    @property\n" + "\n".join(
        "    " + line for line in _function("value", 101).splitlines()
    )
    setter = "    @value.setter\n" + "\n".join(
        "    " + line for line in _function("value", 102).splitlines()
    )
    _write(tmp_path, {"__init__.py": "", "a.py": f"class K:\n{getter}\n{setter}\n"})
    functions = structure.measure(tmp_path / PACKAGE).functions
    assert {key: f.lines for key, f in functions.items()} == {
        "a.py:K.value": 101,
        "a.py:K.value#2": 102,
    }


def test_a_module_importing_itself_is_no_function_level_import(root):
    _tree(root, **{"b.py": "def late():\n    from pkg import b\n"})
    assert _messages(root) == []


def test_absolute_imports_go_by_the_name_below_src(tmp_path):
    # The package's import name is its path below `src`, not its directory's
    # name alone: mascope_backend.api.new.peak_assignments.
    package = tmp_path / "lib/src/ns/deep"
    package.mkdir(parents=True)
    (package / "a.py").write_text("from ns.deep import b\n")
    (package / "b.py").write_text("def late():\n    from ns.deep.a import x\n")
    reading = structure.measure(package)
    assert reading.cycles == [["a.py", "b.py"]]
    assert reading.function_imports == [("b.py", 2)]
