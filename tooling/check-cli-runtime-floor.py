#!/usr/bin/env python3
#
# Import the built mascope-cli wheel against the oldest mascope-runtime its
# requirement admits.
#
# Why this exists: the CLI and the runtime are published to PyPI separately,
# and the CLI states the oldest runtime it works with as a floor in
# tooling/cli/pyproject.toml. `pip install -U mascope-cli` upgrades an
# installed runtime only when the new CLI's requirement excludes it, and the
# CLI imports every command group when it starts - so a CLI module importing a
# runtime name newer than the floor stops every `mascope` command for anyone
# left on an older runtime. The packaging job's other checks cannot see that:
# they install the runtime wheel built from the same checkout, which has every
# name the CLI could import.
#
# Which runtime: the oldest version the requirement admits among those PyPI
# serves - plus this checkout's own version while PyPI has never had it,
# because a release then publishes the runtime under it from this source. One
# version number can name two runtimes: PyPI's copy of the checkout's version
# predates whatever the runtime gained since, and a release skips a version
# PyPI already has. Once PyPI has the version, its copy is the one checked,
# never the wheel built here.
#
# CI runs it after building both wheels into dist/; the same works locally:
#   uv build --package mascope_cli --out-dir dist
#   uv build --package mascope_runtime --out-dir dist
#   uv run --no-project --python 3.12 --with packaging \
#     python tooling/check-cli-runtime-floor.py dist
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path
from typing import NamedTuple

from packaging.requirements import Requirement
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version


ROOT = Path(__file__).resolve().parent.parent
CLI_PYPROJECT = "tooling/cli/pyproject.toml"
RUNTIME_PYPROJECT = "libraries/runtime/pyproject.toml"
MIRROR_MODULE = "tooling/cli/src/mascope_cli/runtime.py"
MIRROR_TEST = "tooling/cli/tests/test_runtime_mirror.py"
# Depends on the runtime by path, so its lockfile records the runtime's version.
AGENT_PROJECT = "agents/file"

RUNTIME = "mascope-runtime"
INDEX_URL = f"https://pypi.org/simple/{RUNTIME}/"
# PEP 691: the JSON form of the simple index, the API PyPI points clients at.
INDEX_ACCEPT = "application/vnd.pypi.simple.v1+json"

USAGE = """usage: check-cli-runtime-floor.py [DIST]

  DIST  the directory holding the built mascope_cli wheel, and the
        mascope_runtime wheel built from the same checkout (default: dist)
"""

HOWTO = f"""
`pip install -U mascope-cli` upgrades an installed runtime only when the new
CLI's requirement excludes it, and the CLI imports every command group when it
starts, so this stops every `mascope` command for anyone on that runtime. A
runtime name the CLI imports must be one of these:

  * defined in {MIRROR_MODULE}
    instead of imported, and pinned equal to the runtime's in
    {MIRROR_TEST};

  * covered by the floor in {CLI_PYPROJECT}: raise it to a runtime
    that has the name. When PyPI has none yet, also give
    {RUNTIME_PYPROJECT} a version PyPI does not have, relock
    (`uv lock`, then `uv lock --directory {AGENT_PROJECT}`) and raise the floor
    to that version. A release publishes this checkout's runtime under it, so
    this check then imports against the wheel built here.

Raising the floor to a version PyPI already has does not help when PyPI's copy
predates the name: that copy is the one users install.
"""


class Target(NamedTuple):
    """The runtime to import against, and whether it is the checkout's own."""

    version: Version
    from_checkout: bool


def runtime_requirement(pyproject: Path) -> Requirement:
    """The CLI's requirement on the runtime, as its pyproject declares it."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    for spec in project.get("dependencies", []):
        requirement = Requirement(spec)
        if canonicalize_name(requirement.name) == RUNTIME:
            return requirement
    sys.exit(f"{pyproject}: no {RUNTIME} dependency - nothing to check")


def project_version(pyproject: Path) -> Version:
    """The version a package's pyproject declares."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return Version(project["version"])


def _file_version(filename: str) -> Version | None:
    try:
        if filename.endswith(".whl"):
            return parse_wheel_filename(filename)[1]
        return parse_sdist_filename(filename)[1]
    except (InvalidWheelFilename, InvalidSdistFilename):
        return None


def index_versions(page: dict) -> tuple[set[Version], set[Version]]:
    """
    The versions PyPI has taken, and the ones it serves, from a PEP 691 page.

    A version is taken once PyPI lists it at all. The publish workflow skips
    such a version, so a release never publishes the checkout's runtime under
    it. A version is served while one of its files is not yanked; only those
    install through a version range.
    """
    taken = {Version(version) for version in page.get("versions", [])}
    served = set()
    for file in page.get("files", []):
        version = _file_version(file["filename"])
        if version is None:
            continue
        taken.add(version)
        if not file.get("yanked"):
            served.add(version)
    return taken, served


def oldest_admitted(
    requirement: Requirement,
    taken: set[Version],
    served: set[Version],
    checkout: Version,
) -> Target | None:
    """
    The oldest runtime `requirement` admits, or None when it admits none.

    The candidates are what PyPI serves, and the checkout's own version while
    PyPI has not taken it.
    """
    pool = set(served)
    if checkout not in taken:
        pool.add(checkout)
    admitted = list(requirement.specifier.filter(sorted(pool)))
    if not admitted:
        return None
    oldest = admitted[0]
    return Target(oldest, from_checkout=oldest == checkout and checkout not in taken)


def install_requirement(requirement: Requirement, target: Target, dist: Path) -> str:
    """
    The requirement that installs exactly `target`, with the CLI's extras.

    PyPI's copy is named by its version alone and the checkout's own by the
    path of its wheel in `dist`, so the resolver is never offered both: they
    can share a version number and still differ.
    """
    extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
    if target.from_checkout:
        wheel = find_wheel(dist, RUNTIME, target.version)
        return f"{requirement.name}{extras} @ {wheel.resolve().as_uri()}"
    return f"{requirement.name}{extras}=={target.version}"


def find_wheel(dist: Path, name: str, version: Version | None = None) -> Path:
    """The one wheel of `name` (at `version`, when given) in `dist`."""
    found = []
    for path in sorted(dist.glob("*.whl")):
        wheel_name, wheel_version, _, _ = parse_wheel_filename(path.name)
        if wheel_name != canonicalize_name(name):
            continue
        if version is None or wheel_version == version:
            found.append(path)
    if len(found) != 1:
        wanted = f"{name} {version}" if version else name
        sys.exit(f"expected one {wanted} wheel in {dist}, found {len(found)}")
    return found[0]


def fetch_index(url: str = INDEX_URL) -> dict:
    """PyPI's PEP 691 page for the runtime, retried through brief outages."""
    request = urllib.request.Request(url, headers={"Accept": INDEX_ACCEPT})
    for delay in (5, 15, None):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except OSError as error:  # URLError, timeouts and dropped connections
            if delay is None:
                sys.exit(f"could not read {url}: {error}")
            print(f"could not read {url} ({error}), retrying in {delay}s", flush=True)
            time.sleep(delay)


def _fail(summary: str, detail: str) -> int:
    print(f"\nFAIL  {summary}\n{detail}", flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error title=CLI runtime floor::{summary}", flush=True)
    return 1


def _venv_bin(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def import_against(cli_wheel: Path, runtime: str, work: Path) -> tuple[str, str] | None:
    """
    Install the CLI wheel beside `runtime` in a fresh venv and import it.

    The import and `mascope --help` run as a fresh install would: with an
    empty home and no MASCOPE_PATH. uv keeps the caller's environment, so its
    cache still serves the downloads.

    :return: None when both pass, else the step that failed and its output.
    """
    venv = work / "venv"
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run(
        ["uv", "--quiet", "venv", "--python", python_version, str(venv)],
        cwd=work,
        check=True,
    )
    python = _venv_bin(venv, "python")
    install = ["uv", "pip", "install", "--python", str(python), str(cli_wheel), runtime]
    if subprocess.run(install, cwd=work).returncode != 0:
        return "installing the CLI wheel beside the runtime", ""

    home = work / "home"
    home.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("MASCOPE_PATH", "PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home), PYTHONUTF8="1"
    )
    for label, command in (
        ("import mascope_cli.main", [str(python), "-c", "import mascope_cli.main"]),
        ("mascope --help", [str(_venv_bin(venv, "mascope")), "--help"]),
    ):
        result = subprocess.run(
            command, cwd=work, env=env, capture_output=True, text=True
        )
        if result.returncode != 0:
            return label, result.stdout + result.stderr
        print(f"  ok  {label}", flush=True)
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1 or any(arg.startswith("-") for arg in argv):
        sys.exit(USAGE)
    # Absolute: the installs run in a scratch directory.
    dist = Path(argv[0] if argv else "dist").resolve()

    requirement = runtime_requirement(ROOT / CLI_PYPROJECT)
    checkout = project_version(ROOT / RUNTIME_PYPROJECT)
    taken, served = index_versions(fetch_index())
    target = oldest_admitted(requirement, taken, served, checkout)
    if target is None:
        listed = ", ".join(str(version) for version in sorted(served)) or "nothing"
        return _fail(
            f"no {RUNTIME} satisfies the CLI's requirement {requirement}",
            f"PyPI serves {listed}, and this checkout's runtime is {checkout}"
            + (", a version PyPI already has." if checkout in taken else ".")
            + f"\nPoint the floor in {CLI_PYPROJECT} at a runtime that exists, or"
            f"\ngive {RUNTIME_PYPROJECT} a version the floor admits that PyPI"
            "\ndoes not have.",
        )

    cli_wheel = find_wheel(dist, "mascope_cli")
    runtime = install_requirement(requirement, target, dist)
    checked = f"{RUNTIME} {target.version} " + (
        "built from this checkout (PyPI has never had it)"
        if target.from_checkout
        else "from PyPI"
    )
    print(
        f"{cli_wheel.name} requires {requirement}. The oldest runtime that "
        f"admits is {checked}.",
        flush=True,
    )

    with tempfile.TemporaryDirectory(
        prefix="cli-floor-", ignore_cleanup_errors=True
    ) as work:
        failure = import_against(cli_wheel, runtime, Path(work))
    if failure is None:
        print(f"The CLI imports against {RUNTIME} {target.version}.")
        return 0

    step, output = failure
    summary = (
        f"{step} failed against {checked}, the oldest runtime the CLI's "
        f"requirement {requirement} admits"
    )
    if step.startswith("installing"):
        return _fail(
            summary,
            "uv's output above says why. If the CLI's other requirements exclude "
            "that runtime, the floor is lower than the CLI can run with: raise it "
            f"in {CLI_PYPROJECT}.",
        )
    if target.from_checkout:
        return _fail(summary, output)
    return _fail(summary, output + HOWTO)


if __name__ == "__main__":
    raise SystemExit(main())
