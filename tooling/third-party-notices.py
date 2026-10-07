#!/usr/bin/env python3
#
# Write the attributions for every third-party Python distribution installed in
# the running interpreter's environment: its name, version and declared licence,
# followed by the licence and notice files it ships. With --vendored, also the
# licence files kept beside third-party assets copied into the repository.
#
# Why this exists: the backend image redistributes a few hundred open-source
# packages, and most of their licences - the notice clauses of MIT and BSD,
# Apache-2.0 section 4(d) - make the copyright and licence text a condition of
# redistributing them. A hand-maintained list would rot with every dependency
# bump, so the image build runs this against the environment it has just
# installed (server/backend/Dockerfile), and the About tab shows the result
# (GET /api/version/third-party-notices). The web app's npm attributions come
# from the modules actually in its bundle instead
# (server/frontend/scripts/vite-plugin-legal.js): each image carries the notices
# for what it ships. That includes the user documentation site in the frontend
# image, whose build runs this with `--for docs` (server/frontend/Dockerfile):
# the packages that build the site copy their theme, scripts and styles into
# it, and the assets vendored under docs/user/assets keep their licence files.
# And the File Agent's Windows program, which PyInstaller assembles from the
# packages of its build environment together with the interpreter itself:
# agents/file/build.ps1 runs this with `--for agent --interpreter` in that
# environment, and the installer puts the result beside the program.
#
# It reads the environment rather than uv.lock, because the question is what
# ships, and uv.lock records no licence metadata anyway. uv.lock is read only to
# leave out the repository's own packages, which NOTICE already covers. Run it
# with the interpreter whose packages should be attributed:
#   /opt/uv/tools/mascope/bin/python tooling/third-party-notices.py -o FILE
#   python tooling/third-party-notices.py --for docs --vendored docs/user/assets
#   uv run python ../../tooling/third-party-notices.py --for agent --interpreter \
#       --lock uv.lock -o dist/THIRD_PARTY_NOTICES.txt   # in agents/file
#   uv run python tooling/third-party-notices.py \
#       -o "$MASCOPE_PATH/THIRD_PARTY_NOTICES.txt"   # a checkout; includes dev tools
# The backend serves the file from its runtime home, MASCOPE_PATH - in a
# worktree that is the shared home, not the checkout.
#
# A distribution can carry copies of other projects inside it, with their
# metadata, off the import path: setuptools does. Each copy gets an entry of
# its own. And what is redistributed without a licence file of its own has
# its text kept in tooling/licence-texts (the README there says which, and
# where each came from).
#
# Each entry names the licence the way the distribution declares it. Only the
# name normalisation and uv.lock's first-party packages come from
# check-licenses.py, not its licence reading: to decide what is allowed, the gate
# maps a coarse classifier onto one SPDX identifier ("BSD License" becomes
# BSD-3-Clause). In an attribution that mapping would state a licence the
# package never declared, so the classifier is quoted instead.
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import platform
import re
import sys
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_licence_checker():
    """check-licenses.py, imported by path - a hyphenated name is not a module."""
    spec = importlib.util.spec_from_file_location(
        "check_licenses", Path(__file__).with_name("check-licenses.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


licences = _load_licence_checker()

# The files a distribution ships to satisfy its licence. PEP 639 collects them
# under `.dist-info/licenses/`; wheels built before it put them straight into
# `.dist-info/` under whatever name the project used, so those match by name.
_METADATA_DIRS = (".dist-info", ".egg-info")
_LICENCE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice|copyright)([._-].*)?$", re.IGNORECASE
)

# Project-URL labels that name the project itself, best first.
_PROJECT_LABELS = ("homepage", "home", "source", "source code", "repository")

HEADERS = {
    "server": """\
Mascope server - third-party notices

The Mascope server image includes the open-source Python packages below. Each
entry gives the package, its version and declared licence, followed by the
licence and notice files the package itself ships. Generated at build time from
the installed distributions (tooling/third-party-notices.py) - do not edit.
""",
    "docs": """\
Mascope user documentation - third-party notices

The user documentation site is built with the open-source Python packages below,
which copy their theme, scripts and styles into it, and carries the third-party
assets vendored beside its pages. Each package entry gives its version and
declared licence, followed by the licence and notice files it ships; each
vendored asset, the licence files kept with it. Generated at build time
(tooling/third-party-notices.py) - do not edit.
""",
    "agent": """\
Mascope File Agent - third-party notices

The Mascope File Agent program is assembled by PyInstaller from the open-source
Python packages below and the Python interpreter that runs them, and carries
them inside its executable. Each entry gives the package, its version and
declared licence, followed by the licence and notice files the package itself
ships. Generated when the program is built, from the distributions installed in
its build environment (tooling/third-party-notices.py) - do not edit.

Every package installed in that environment has an entry, and so has every
other project that one of them carries a copy of. The program is made from
some of them, and a few are build tools of which it contains little or
nothing: of PyInstaller itself, the bootloader and the run-time hooks, which
its licence below provides for.
""",
}

# What the notices are refused for unless every package in them has a licence
# text: the installer hands this file on in place of the texts themselves.
WHOLE = {"agent"}

# Where an interpreter keeps the licence it ships under, relative to its base
# prefix: the root on Windows, the standard library's directory elsewhere.
# Which of the libraries built into its binaries that file names depends on
# who built the interpreter - OpenSSL and libffi in one build and not in
# another, Expat and libmpdec in none - so it is never the whole of what a
# frozen program carries along. The rest is INCORPORATED, below.
_INTERPRETER_LICENCES = (
    "LICENSE.txt",
    "LICENSE",
    "lib/python{major}.{minor}/LICENSE.txt",
)
INTERPRETER_LICENCE = "Python Software Foundation License Version 2 (PSF-2.0)"

# Licence texts kept in the repository for what ships none of its own; its
# README says what each is and where it came from.
KEPT_TEXTS = Path(__file__).with_name("licence-texts")
# A distribution's, by its normalised name and exact version.
KEPT_LICENCE = "{name}@{version}.txt"
KEPT_LICENCE_LABEL = "LICENSE (from the project's repository; the package ships none)"
# CPython's account of the software built into the interpreter and its
# standard library, by the Python it is for.
INCORPORATED = "cpython-{major}.{minor}.rst"
INCORPORATED_LABEL = (
    "Doc/license.rst (CPython {major}.{minor}: the software built into the "
    "interpreter and its standard library)"
)

RULE = "-" * 79
NO_FILES = "(The package ships no licence file; its declared licence is given above.)"
CARRIED = "A copy carried inside {owner}."
VENDORED = "(vendored)"
VENDORED_LICENCE = "as given in the files below"


def _read_text(path: Path) -> str:
    """A licence file's text, with one line-ending convention.

    Whatever each project committed, so the file reads the same everywhere and
    a rebuild reproduces it byte for byte.
    """
    text = path.read_bytes().decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _licence_files(dist) -> list[tuple[str, str]]:
    """Return ``(path, text)`` for each licence or notice file ``dist`` ships."""
    found = []
    for file in dist.files or ():
        parts = file.parts
        if len(parts) < 2 or not parts[0].endswith(_METADATA_DIRS):
            continue
        inside = parts[1:]
        pep639 = len(inside) > 1 and inside[0] == "licenses"
        legacy = len(inside) == 1 and _LICENCE_NAME.match(inside[0])
        if not (pep639 or legacy):
            continue
        location = Path(file.locate())
        if location.is_file():
            found.append(("/".join(inside), _read_text(location)))
    return sorted(found)


def _licence_files_in(directory: Path) -> list[tuple[str, str]]:
    """The same, read from a metadata directory itself.

    For a copy carried inside another distribution, whose own record of files
    may not have been kept.
    """
    found = []
    for path in sorted(directory.rglob("*")):
        inside = path.relative_to(directory).parts
        pep639 = len(inside) > 1 and inside[0] == "licenses"
        legacy = len(inside) == 1 and _LICENCE_NAME.match(inside[0])
        if path.is_file() and (pep639 or legacy):
            found.append(("/".join(inside), _read_text(path)))
    return sorted(found)


def _kept_licence(key: str, version: str) -> list[tuple[str, str]]:
    """The licence kept in the repository for a distribution that ships none."""
    path = KEPT_TEXTS / KEPT_LICENCE.format(name=key, version=version)
    return [(KEPT_LICENCE_LABEL, _read_text(path))] if path.is_file() else []


def _carried_inside(dist) -> list[Path]:
    """The metadata directories of the other projects ``dist`` carries copies of.

    A directory further down than the distribution's own, which is at the top
    of the import path: `setuptools/_vendor/<project>-<version>.dist-info`.
    Nothing on the import path points at those, so no listing of the
    installed distributions includes them.
    """
    found = set()
    for file in dist.files or ():
        parts = file.parts
        for depth in range(1, len(parts) - 1):
            if parts[depth].endswith(".dist-info") and parts[depth + 1] == "METADATA":
                found.add(Path(dist.locate_file(Path(*parts[: depth + 1]))))
    return sorted(path for path in found if path.is_dir())


def _declared(metadata) -> str:
    """The licence as ``metadata`` declares it, in its own words.

    A PEP 639 expression when there is one; otherwise every licence classifier,
    quoted whole; otherwise the first line of the free-text field, which is
    often the start of a pasted licence rather than a name.
    """
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = sorted(
        {c for c in metadata.get_all("Classifier") or () if c.startswith("License ::")}
    )
    if classifiers:
        # Several classifiers mean every one of them applies.
        return "; ".join(classifiers)
    free = (metadata.get("License") or "").strip()
    if free:
        return free.splitlines()[0].strip()
    return "not declared"


def _homepage(metadata) -> str | None:
    """The project's own URL, for a reader who wants more than the licence."""
    home = (metadata.get("Home-page") or "").strip()
    if home and home.upper() != "UNKNOWN":
        return home
    urls = []
    for entry in metadata.get_all("Project-URL") or ():
        label, _, url = entry.partition(",")
        if url.strip():
            urls.append((label.strip().lower(), url.strip()))
    for wanted in _PROJECT_LABELS:
        for label, url in urls:
            if label == wanted:
                return url
    return urls[0][1] if urls else None


def collect(first_party: set[str], distributions: Iterable | None = None) -> list[dict]:
    """Every installed third-party distribution, with what its entry needs.

    ``first_party`` holds PEP 503 normalised names to leave out. Sorted by
    normalised name so the output is stable from one build to the next.
    """
    if distributions is None:
        distributions = importlib.metadata.distributions()
    # Keyed for the order of the file: by name, a distribution before the
    # copies of it that others carry, and those by version.
    packages: dict[tuple[str, str], dict] = {}
    carried: list[tuple[dict, Path]] = []
    for dist in distributions:
        metadata = dist.metadata
        name = (metadata.get("Name") or "").strip()
        key = licences._normalize(name)
        # The first one wins, as it does on import: the same name further down
        # sys.path is shadowed and never runs.
        if not key or key in first_party or (key, "") in packages:
            continue
        version = (metadata.get("Version") or "?").strip()
        packages[(key, "")] = {
            "name": name,
            "version": version,
            "licence": _declared(metadata),
            "homepage": _homepage(metadata),
            "files": _licence_files(dist) or _kept_licence(key, version),
        }
        carried += [(packages[(key, "")], path) for path in _carried_inside(dist)]

    for owner, directory in carried:
        metadata = importlib.metadata.Distribution.at(directory).metadata
        name = (metadata.get("Name") or "").strip()
        key = licences._normalize(name)
        version = (metadata.get("Version") or "?").strip()
        installed = packages.get((key, ""))
        # The same version installed in its own right says all this entry
        # would, and one copy among several of a version is enough.
        same = installed is not None and installed["version"] == version
        if not key or same or (key, version) in packages:
            continue
        packages[(key, version)] = {
            "name": name,
            "version": version,
            "licence": _declared(metadata),
            "homepage": _homepage(metadata),
            "files": _licence_files_in(directory) or _kept_licence(key, version),
            "within": f"{owner['name']} {owner['version']}",
        }
    return [packages[key] for key in sorted(packages)]


def collect_interpreter(base: Path | None = None) -> dict:
    """An entry for the running Python interpreter, with the licence it ships.

    For a program that carries its interpreter with it, as a frozen executable
    does: no distribution's metadata describes CPython, so its entry comes from
    the licence file in the interpreter's own installation, and from CPython's
    account of the software built into it, kept in the repository for the
    Python in question because no installation can be relied on to have it.

    :param base: The interpreter's base prefix, by default the running one's -
        the installation a virtual environment was made from, not the
        environment, which holds no licence.
    """
    base = Path(sys.base_prefix) if base is None else base
    major, minor = sys.version_info[:2]
    for candidate in _INTERPRETER_LICENCES:
        path = base / candidate.format(major=major, minor=minor)
        if path.is_file():
            files = [(path.name, _read_text(path))]
            break
    else:
        # Asked for, so an entry without its text would be the gap this exists
        # to close, written out as if it were closed.
        sys.exit(f"{base}: the interpreter ships no licence file - refusing to write")
    incorporated = KEPT_TEXTS / INCORPORATED.format(major=major, minor=minor)
    if not incorporated.is_file():
        # The licence file alone would leave out libraries the program
        # carries, and say nothing of having done so.
        sys.exit(
            f"{incorporated}: not found - it is Doc/license.rst of CPython "
            f"{major}.{minor}, which names the software built into the "
            "interpreter - refusing to write"
        )
    label = INCORPORATED_LABEL.format(major=major, minor=minor)
    files.append((label, _read_text(incorporated)))
    return {
        "name": "Python",
        "version": platform.python_version(),
        "licence": INTERPRETER_LICENCE,
        "homepage": "https://www.python.org/",
        "files": files,
    }


def collect_vendored(root: Path) -> list[dict]:
    """An entry for each directory under ``root`` that keeps licence files.

    For third-party assets copied into the repository, which no package
    metadata describes: the licence files kept beside them are what travels.
    Named by their path from ``root``'s own name (``assets/katex``), sorted.
    """
    by_directory: dict[Path, list[tuple[str, str]]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and _LICENCE_NAME.match(path.name):
            by_directory.setdefault(path.parent, []).append(
                (path.name, _read_text(path))
            )
    return [
        {
            "name": (Path(root.name) / directory.relative_to(root)).as_posix(),
            "version": VENDORED,
            "licence": VENDORED_LICENCE,
            "homepage": None,
            "files": files,
        }
        for directory, files in sorted(by_directory.items())
    ]


def render(
    packages: list[dict],
    vendored: list[dict] = (),
    header: str = HEADERS["server"],
    interpreter: dict | None = None,
) -> str:
    """The notices file for ``packages`` and ``vendored``, as the About tab shows it.

    ``interpreter`` is the entry of an interpreter shipped with them, which
    comes first: everything else in the file runs on it.
    """
    count = f"{len(packages)} packages"
    if interpreter is not None:
        count = f"The Python interpreter, {count}"
    if vendored:
        count += f", {len(vendored)} vendored assets"
    lines = [header, f"{count}.", ""]
    leading = [interpreter] if interpreter is not None else []
    for package in [*leading, *packages, *vendored]:
        lines += [RULE, f"{package['name']} {package['version']}"]
        lines.append(f"License: {package['licence']}")
        if package["homepage"]:
            lines.append(package["homepage"])
        if package.get("within"):
            lines.append(CARRIED.format(owner=package["within"]))
        lines.append("")
        if not package["files"]:
            lines += [NO_FILES, ""]
        for path, text in package["files"]:
            lines += [f"--- {path}", text.rstrip(), ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the attributions for the installed third-party "
        "Python packages."
    )
    parser.add_argument(
        "-o", "--output", type=Path, help="file to write (default: stdout)"
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=ROOT / "uv.lock",
        help="uv.lock naming the first-party packages to leave out "
        "(default: the repository's)",
    )
    parser.add_argument(
        "--for",
        dest="subject",
        choices=sorted(HEADERS),
        default="server",
        help="what the notices describe, which sets their heading (default: server)",
    )
    parser.add_argument(
        "--vendored",
        type=Path,
        help="directory of vendored third-party assets whose licence files to "
        "carry as well",
    )
    parser.add_argument(
        "--interpreter",
        action="store_true",
        help="also attribute the Python interpreter running this, for a "
        "program that carries its interpreter with it",
    )
    args = parser.parse_args(argv)

    if not args.lock.is_file():
        sys.exit(f"{args.lock}: not found - it names the first-party packages")
    _, first_party = licences._locked(args.lock)
    packages = collect(first_party)
    if not packages:
        # An empty file would read as "no third-party code", which is never true.
        sys.exit("no third-party distributions installed here - refusing to write")
    bare = [f"{p['name']} {p['version']}" for p in packages if not p["files"]]
    if bare and args.subject in WHOLE:
        sys.exit(
            f"no licence text for {', '.join(bare)}: none in the package and none "
            f"kept in {KEPT_TEXTS.name} (its README says how) - refusing to write"
        )
    vendored = []
    if args.vendored is not None:
        if not args.vendored.is_dir():
            sys.exit(f"{args.vendored}: not a directory")
        vendored = collect_vendored(args.vendored)
        if not vendored:
            # Asked for, so a wrong path rather than assets that need nothing.
            sys.exit(f"{args.vendored}: no licence files found - refusing to write")

    interpreter = collect_interpreter() if args.interpreter else None

    text = render(packages, vendored, HEADERS[args.subject], interpreter)
    if args.output is None:
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        # newline="\n": the same bytes on every platform, so a rebuild of the
        # same lockfile produces the same file.
        args.output.write_text(text, encoding="utf-8", newline="\n")
        print(
            f"wrote {args.output}: {len(packages)} packages, "
            f"{len(vendored)} vendored assets"
            + (", the interpreter" if interpreter is not None else ""),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
