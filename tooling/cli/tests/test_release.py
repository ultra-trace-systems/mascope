"""
Tests for the release the CLI was cut with (`mascope_cli.release`).

`RELEASE` is set by hand at release prep, beside the CLI's calver. These pin
it to the version the same prep writes into `CITATION.cff`, so a prep pull
request that forgets it fails here rather than shipping a CLI that refuses
the release it was published with.
"""

import re
from importlib import metadata
from pathlib import Path

import pytest

from mascope_cli import release
from mascope_runtime import is_release_tag, release_sort_key


REPO_ROOT = Path(__file__).resolve().parents[3]


def _citation_release() -> str:
    text = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match, "CITATION.cff has no top-level version"
    return f"v{match.group(1)}"


def test_release_is_a_release_tag():
    assert is_release_tag(release.RELEASE)


def test_release_is_the_cited_release_or_a_candidate_for_the_next():
    """The release prep that bumps CITATION.cff bumps RELEASE with it.

    A pre-release is cut without the citation bump, so a CLI cut with a
    candidate names one ahead of the cited release.
    """
    cited = _citation_release()
    if "-" in release.RELEASE:
        assert release_sort_key(release.RELEASE) > release_sort_key(cited)
    else:
        assert release.RELEASE == cited, (
            f"mascope_cli.release.RELEASE is {release.RELEASE} but CITATION.cff "
            f"cites {cited}: set RELEASE at release prep, beside the CLI's calver"
        )


def test_cli_version_is_the_installed_distribution():
    assert release.cli_version() == metadata.version("mascope_cli")


@pytest.mark.parametrize("version", ["0.0.0", ""])
def test_a_placeholder_version_is_none(monkeypatch, version):
    monkeypatch.setattr(release.metadata, "version", lambda name: version)

    assert release.cli_version() is None


def test_install_command_names_the_distribution():
    assert release.install_command("2026.11.2") == (
        "uv tool install mascope-cli==2026.11.2"
    )
