#!/usr/bin/env python3
#
# Write the attributions for every third-party Python distribution installed in
# the running interpreter's environment: its name, version and declared licence,
# followed by the licence and notice files it ships.
#
# Why this exists: the backend image redistributes a few hundred open-source
# packages, and most of their licences - the notice clauses of MIT and BSD,
# Apache-2.0 section 4(d) - make the copyright and licence text a condition of
# redistributing them. A hand-maintained list would rot with every dependency
# bump, so the image build runs this against the environment it has just
# installed (server/backend/Dockerfile), and the About dialog shows the result
# (GET /api/version/third-party-notices). The web app's npm attributions come
# from the modules actually in its bundle instead
# (server/frontend/scripts/vite-plugin-legal.js): each image carries the notices
# for what it ships.
#
# It reads the environment rather than uv.lock, because the question is what
# ships, and uv.lock records no licence metadata anyway. uv.lock is read only to
# leave out the repository's own packages, which NOTICE already covers. Run it
# with the interpreter whose packages should be attributed:
#   /opt/uv/tools/mascope/bin/python tooling/third-party-notices.py -o FILE
#   uv run python tooling/third-party-notices.py    # a checkout; includes dev tools
#
# The licence classification is check-licenses.py's, imported rather than
# repeated, so the notices and the licence gate cannot disagree about a package.
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
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

HEADER = """\
Mascope server - third-party notices

The Mascope server image includes the open-source Python packages below. Each
entry gives the package, its version and declared licence, followed by the
licence and notice files the package itself ships. Generated at build time from
the installed distributions (tooling/third-party-notices.py) - do not edit.
"""

RULE = "-" * 79
NO_FILES = "(The package ships no licence file; its declared licence is given above.)"


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
            text = location.read_bytes().decode("utf-8", errors="replace")
            # One line-ending convention, whatever each project committed, so
            # the file reads the same everywhere and rebuilds byte for byte.
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            found.append(("/".join(inside), text))
    return sorted(found)


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
    packages: dict[str, dict] = {}
    for dist in distributions:
        metadata = dist.metadata
        name = (metadata.get("Name") or "").strip()
        key = licences._normalize(name)
        # The first one wins, as it does on import: the same name further down
        # sys.path is shadowed and never runs.
        if not key or key in first_party or key in packages:
            continue
        declared, _ = licences._declared(metadata)
        packages[key] = {
            "name": name,
            "version": (metadata.get("Version") or "?").strip(),
            "licence": declared or "not declared",
            "homepage": _homepage(metadata),
            "files": _licence_files(dist),
        }
    return [packages[key] for key in sorted(packages)]


def render(packages: list[dict]) -> str:
    """The notices file for ``packages``, as the About dialog shows it."""
    lines = [HEADER, f"{len(packages)} packages.", ""]
    for package in packages:
        lines += [RULE, f"{package['name']} {package['version']}"]
        lines.append(f"License: {package['licence']}")
        if package["homepage"]:
            lines.append(package["homepage"])
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
    args = parser.parse_args(argv)

    if not args.lock.is_file():
        sys.exit(f"{args.lock}: not found - it names the first-party packages")
    _, first_party = licences._locked(args.lock)
    packages = collect(first_party)
    if not packages:
        # An empty file would read as "no third-party code", which is never true.
        sys.exit("no third-party distributions installed here - refusing to write")

    text = render(packages)
    if args.output is None:
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        # newline="\n": the same bytes on every platform, so a rebuild of the
        # same lockfile produces the same file.
        args.output.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.output}: {len(packages)} packages", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
