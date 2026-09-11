"""
Tests for `tooling/third-party-notices.py`, which writes the attributions the
backend image serves for the Python packages it redistributes.

What matters is that the licence text a package ships actually makes it into
the output, from both places wheels keep it - PEP 639's `.dist-info/licenses/`
and the older habit of dropping LICENSE straight into `.dist-info/` - and that
the repository's own packages, which NOTICE already covers, stay out. A name
and an SPDX identifier alone would not satisfy the licences that ask for their
text to travel with the code.
"""

import importlib.util
from importlib.metadata import PathDistribution
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "third-party-notices.py"
LOCK = REPO_ROOT / "uv.lock"

# Guarded like test_check_licenses.py: repo-root tooling/ is not present in
# every checkout or packaged layout.
if not SCRIPT.is_file():
    pytest.skip(
        "repo-root tooling/third-party-notices.py not available",
        allow_module_level=True,
    )


def _load():
    """Import the script by path - a hyphenated filename is not a module name."""
    spec = importlib.util.spec_from_file_location("third_party_notices", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notices = _load()


def _dist(site: Path, name: str, version: str, metadata: str = "", files=None):
    """A minimal installed distribution under ``site``, as a wheel leaves it."""
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{metadata}",
        encoding="utf-8",
    )
    record = [f"{info.name}/METADATA,,"]
    for relative, text in (files or {}).items():
        path = info / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exactly these bytes, not the platform's line endings.
        path.write_bytes(text.encode("utf-8"))
        record.append(f"{info.name}/{relative},,")
    record.append(f"{info.name}/RECORD,,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return PathDistribution(info)


def test_pep639_licence_files_are_carried_verbatim(tmp_path):
    text = "MIT License\n\nCopyright (c) 2024 Alpha authors\n"
    dist = _dist(
        tmp_path,
        "alpha",
        "1.0",
        metadata="License-Expression: MIT\n",
        files={"licenses/LICENSE": text, "licenses/vendored/NOTICE": "Vendored"},
    )

    [package] = notices.collect(set(), [dist])

    assert package["licence"] == "MIT"
    assert package["files"] == [
        ("licenses/LICENSE", text),
        ("licenses/vendored/NOTICE", "Vendored"),
    ]


def test_legacy_top_level_licence_files_are_found_and_metadata_is_not(tmp_path):
    dist = _dist(
        tmp_path,
        "beta",
        "2.0",
        metadata="Classifier: License :: OSI Approved :: BSD License\n",
        files={"LICENSE.txt": "BSD text", "NOTICE": "Beta", "top_level.txt": "beta"},
    )

    [package] = notices.collect(set(), [dist])

    # The classifier is read the way the licence gate reads it.
    assert package["licence"] == "BSD-3-Clause"
    assert [path for path, _ in package["files"]] == ["LICENSE.txt", "NOTICE"]


def test_line_endings_are_normalised(tmp_path):
    """Licence files arrive with whatever line endings their project committed.
    Carried as-is they would make one file of mixed conventions, and a rebuild
    on another platform would not reproduce it byte for byte."""
    dist = _dist(
        tmp_path,
        "eta",
        "1.0",
        files={"licenses/LICENSE": "BSD\r\n\r\nCopyright (c) Eta\r\nOld Mac\r"},
    )

    [package] = notices.collect(set(), [dist])

    assert package["files"] == [
        ("licenses/LICENSE", "BSD\n\nCopyright (c) Eta\nOld Mac\n")
    ]


def test_first_party_packages_are_left_out(tmp_path):
    ours = _dist(tmp_path, "mascope_runtime", "0.1", files={"licenses/LICENSE": "x"})
    theirs = _dist(tmp_path, "gamma", "3.0", metadata="License-Expression: ISC\n")

    # uv.lock names ours in normalised form; the installed name need not match.
    packages = notices.collect({"mascope-runtime"}, [ours, theirs])

    assert [package["name"] for package in packages] == ["gamma"]


def test_a_shadowed_second_install_is_not_listed_again(tmp_path):
    first = _dist(tmp_path / "a", "delta", "1.0")
    second = _dist(tmp_path / "b", "delta", "0.9")

    [package] = notices.collect(set(), [first, second])

    assert package["version"] == "1.0"


def test_a_package_declaring_nothing_says_so(tmp_path):
    [package] = notices.collect(set(), [_dist(tmp_path, "epsilon", "1.0")])

    assert package["licence"] == "not declared"
    assert package["files"] == []


def test_the_project_url_is_found_among_project_urls(tmp_path):
    dist = _dist(
        tmp_path,
        "zeta",
        "1.0",
        metadata=(
            "Project-URL: Documentation, https://docs.example.org\n"
            "Project-URL: Source, https://github.com/example/zeta\n"
        ),
    )

    [package] = notices.collect(set(), [dist])

    assert package["homepage"] == "https://github.com/example/zeta"


def test_rendering_carries_each_package_and_its_licence_text(tmp_path):
    packages = notices.collect(
        set(),
        [
            _dist(
                tmp_path,
                "alpha",
                "1.0",
                metadata="License-Expression: MIT\nHome-page: https://alpha.example\n",
                files={"licenses/LICENSE": "Copyright (c) Alpha authors\n"},
            ),
            _dist(tmp_path, "omega", "9.9", metadata="License-Expression: ISC\n"),
        ],
    )

    text = notices.render(packages)

    assert text.startswith("Mascope server - third-party notices")
    assert "2 packages." in text
    assert "alpha 1.0\nLicense: MIT\nhttps://alpha.example\n" in text
    assert "--- licenses/LICENSE\nCopyright (c) Alpha authors\n" in text
    assert f"omega 9.9\nLicense: ISC\n\n{notices.NO_FILES}" in text
    assert text.index("alpha 1.0") < text.index("omega 9.9")


@pytest.mark.skipif(not LOCK.is_file(), reason="needs the repository's uv.lock")
def test_the_real_environment_yields_licence_text(tmp_path):
    """Run end to end against the environment the tests run in - real wheels
    and real metadata, the same kind of thing the image build points it at.
    Most of them must come back with licence text, or the file matching has
    stopped fitting how wheels are actually laid out."""
    output = tmp_path / "THIRD_PARTY_NOTICES.txt"

    assert notices.main(["-o", str(output), "--lock", str(LOCK)]) == 0

    _, first_party = notices.licences._locked(LOCK)
    packages = notices.collect(first_party)
    assert len(packages) > 20
    assert sum(1 for package in packages if package["files"]) > len(packages) // 2
    assert not {notices.licences._normalize(p["name"]) for p in packages} & first_party
    text = output.read_text(encoding="utf-8")
    assert text.startswith("Mascope server - third-party notices")
    assert "\r\n" not in text
