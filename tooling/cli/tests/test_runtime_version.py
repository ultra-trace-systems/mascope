"""
Tests for `Runtime.parse_version` — the git-derived version string.

The version doubles as the Docker image tag CI pushes and prod deploys pull,
so the exact shape (release tag detection, branch sanitization, fixed-width
hash) is a contract, not a cosmetic detail. Exercised against disposable git
repos; the checkout running the tests is never consulted.
"""

import re
import subprocess

import pytest

from mascope_cli import version as version_mod
from mascope_cli.runtime import runtime
from mascope_cli.version import resolve_version


BUILD_ID = r"\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7}"


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A fresh git repo with one commit on master, set as the cwd."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    monkeypatch.chdir(repo)
    return repo


def _head_short_hash(repo) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()[:7]


def test_release_tag_at_head_is_the_version(git_repo):
    _git(git_repo, "tag", "v1.2.3")
    assert runtime.parse_version() == "v1.2.3"


def test_dated_build_tag_is_not_a_release(git_repo):
    # v{date}-{hash} tags must not be mistaken for a semver release.
    _git(git_repo, "tag", "v2024.01.01-abcdef1")
    assert re.fullmatch(BUILD_ID, runtime.parse_version())


def test_master_build_id_has_no_branch_prefix(git_repo):
    version = runtime.parse_version()
    assert re.fullmatch(BUILD_ID, version)
    assert version.endswith(_head_short_hash(git_repo))


def test_branch_name_is_sanitized_for_docker_tags(git_repo):
    _git(git_repo, "checkout", "-b", "feat/new-stuff")
    version = runtime.parse_version()
    # "/" is invalid in a Docker tag; it must become "-".
    assert re.fullmatch(rf"feat-new-stuff-{BUILD_ID}", version)


def test_detached_head_has_no_branch_prefix(git_repo):
    _git(git_repo, "checkout", "--detach")
    assert re.fullmatch(BUILD_ID, runtime.parse_version())


def test_outside_a_repo_falls_back_to_unknown(tmp_path, monkeypatch):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert runtime.parse_version() == "unknown-version"


def test_cwd_argument_resolves_another_checkout(git_repo, tmp_path, monkeypatch):
    # A prod deploy resolves the deployment checkout explicitly, because the
    # process it runs in (a systemd unit) starts somewhere else entirely.
    _git(git_repo, "tag", "v1.2.3")
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert runtime.parse_version() == "unknown-version"
    assert runtime.parse_version(cwd=str(git_repo)) == "v1.2.3"


def test_cwd_argument_pointing_nowhere_is_unknown(tmp_path):
    assert runtime.parse_version(cwd=str(tmp_path / "missing")) == "unknown-version"


# --- resolve_version: the CLI-level wrapper with a package-version fallback ---


@pytest.fixture
def no_repo(tmp_path, monkeypatch):
    """A cwd where git yields nothing, forcing the package fallback."""
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    monkeypatch.chdir(empty)


def test_resolve_version_prefers_git(git_repo):
    _git(git_repo, "tag", "v1.2.3")
    assert resolve_version(runtime) == "v1.2.3"


def test_resolve_version_falls_back_to_package_version(no_repo, monkeypatch):
    # A pip-installed CLI has no checkout; the wheel version becomes the
    # deploy tag (formatted like a release tag).
    monkeypatch.setattr(version_mod.metadata, "version", lambda name: "9.9.9")
    assert resolve_version(runtime) == "v9.9.9"


def test_resolve_version_ignores_workspace_placeholder(no_repo, monkeypatch):
    # The monorepo workspace pins 0.0.0 — not a meaningful deploy tag.
    monkeypatch.setattr(version_mod.metadata, "version", lambda name: "0.0.0")
    assert resolve_version(runtime) == "unknown-version"
