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

    assert package["licence"] == "License :: OSI Approved :: BSD License"
    assert [path for path, _ in package["files"]] == ["LICENSE.txt", "NOTICE"]


def test_the_licence_is_named_as_declared_not_as_the_gate_maps_it(tmp_path):
    """The licence gate reads a "BSD License" classifier as BSD-3-Clause, which
    is fine for deciding what is allowed. In an attribution it would state a
    licence the package never declared - nest-asyncio says so, and ships a
    2-clause text - so the notices quote what the metadata says."""
    classifier = _dist(
        tmp_path / "a",
        "iota",
        "1.0",
        metadata="Classifier: License :: OSI Approved :: BSD License\n",
        files={"LICENSE": "BSD 2-Clause License\n"},
    )
    several = _dist(
        tmp_path / "b",
        "kappa",
        "1.0",
        metadata=(
            "Classifier: License :: OSI Approved :: MIT License\n"
            "Classifier: License :: OSI Approved :: GNU Lesser General Public "
            "License v3 (LGPLv3)\n"
            "Classifier: Programming Language :: Python :: 3\n"
        ),
    )
    free_text = _dist(tmp_path / "c", "lambda", "1.0", metadata="License: BSD\n")

    iota, kappa, lam = notices.collect(set(), [classifier, several, free_text])

    assert iota["licence"] == "License :: OSI Approved :: BSD License"
    # Every licence classifier applies, so none is dropped for another.
    assert kappa["licence"] == (
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3); "
        "License :: OSI Approved :: MIT License"
    )
    assert lam["licence"] == "BSD"


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


def test_vendored_assets_carry_the_licence_files_kept_beside_them(tmp_path):
    """The docs site ships KaTeX, mermaid and fonts copied into the repository.
    No package metadata describes them, so the licence file next to each is
    what the notices carry - and an asset without one is simply not listed."""
    assets = tmp_path / "assets"
    for relative, text in {
        "katex/LICENSE": "MIT\r\nKhan Academy\r\n",
        "katex/katex.min.js": "code",
        "fonts/LICENSE": "SIL Open Font License",
        "fonts/plex.woff2": "font",
        "logo.png": "not third-party",
    }.items():
        (assets / relative).parent.mkdir(parents=True, exist_ok=True)
        (assets / relative).write_bytes(text.encode("utf-8"))

    vendored = notices.collect_vendored(assets)

    assert [(asset["name"], asset["files"]) for asset in vendored] == [
        ("assets/fonts", [("LICENSE", "SIL Open Font License")]),
        ("assets/katex", [("LICENSE", "MIT\nKhan Academy\n")]),
    ]

    text = notices.render([], vendored, notices.HEADERS["docs"])
    assert text.startswith("Mascope user documentation - third-party notices")
    assert "0 packages, 2 vendored assets." in text
    assert "assets/katex (vendored)\nLicense: as given in the files below\n" in text


def test_docs_notices_include_the_vendored_assets(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "mermaid").mkdir(parents=True)
    (assets / "mermaid" / "LICENSE").write_text("MIT mermaid", encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    site = tmp_path / "site"
    installed = [_dist(site, "mkdocs-material", "9.7.0", "License-Expression: MIT\n")]
    monkeypatch.setattr(notices.importlib.metadata, "distributions", lambda: installed)
    output = tmp_path / "DOCS_THIRD_PARTY_NOTICES.txt"

    argv = ["--for", "docs", "--vendored", str(assets), "--lock", str(lock)]
    assert notices.main([*argv, "-o", str(output)]) == 0

    text = output.read_text(encoding="utf-8")
    assert text.startswith("Mascope user documentation - third-party notices")
    assert "mkdocs-material 9.7.0\nLicense: MIT\n" in text
    assert "--- LICENSE\nMIT mermaid\n" in text


def test_a_vendored_directory_without_licence_files_is_refused(tmp_path, monkeypatch):
    """Asked for and empty means a wrong path, not assets that need nothing."""
    (tmp_path / "assets").mkdir()
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    installed = [_dist(tmp_path / "site", "mkdocs", "1.6.1")]
    monkeypatch.setattr(notices.importlib.metadata, "distributions", lambda: installed)

    with pytest.raises(SystemExit, match="no licence files"):
        notices.main(["--vendored", str(tmp_path / "assets"), "--lock", str(lock)])


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
