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
from mascope_runtime import is_release_tag, release_sort_key


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


@pytest.mark.parametrize(
    "tag", ["v2.0.0-rc.1", "v2.0.0-rc1", "v2.0.0-beta.2", "v2.0.0-alpha3"]
)
def test_prerelease_tag_at_head_is_the_version(git_repo, tag):
    # A pre-release deploys like any other release: the tag selects the
    # published image of that name and is the version the app reports.
    _git(git_repo, "tag", tag)
    version = runtime.parse_version()

    assert version == tag
    # It also has to survive as a Docker image tag.
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", version)


@pytest.mark.parametrize("tag", ["v2024.01.01-abcdef1", "v2026.09.01-9b9e54d"])
def test_dated_build_tag_is_not_a_release(git_repo, tag):
    # v{date}-{hash} tags must not be mistaken for a release - SemVer would
    # read one as {2026.9.1} plus the pre-release {9b9e54d}, and a release
    # build would then tag its images with a name no deployment pulls.
    _git(git_repo, "tag", tag)
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


# --- release precedence ---


@pytest.mark.parametrize(
    "lower, higher",
    [
        # a candidate ranks below the release it is a candidate for
        ("v2.0.0-rc.1", "v2.0.0"),
        # ... and the labels rank among themselves
        ("v2.0.0-alpha.1", "v2.0.0-beta.1"),
        ("v2.0.0-beta.1", "v2.0.0-rc.1"),
        # numerically, not lexically: rc.9 < rc.10
        ("v2.0.0-rc.9", "v2.0.0-rc.10"),
        # the dot is optional and must not change the ordering
        ("v2.0.0-rc1", "v2.0.0-rc2"),
        # ... and X.Y.Z still dominates the suffix
        ("v1.9.9", "v2.0.0-alpha.1"),
        ("v2.0.0", "v2.0.1-rc.1"),
    ],
)
def test_release_precedence(lower, higher):
    assert release_sort_key(lower) < release_sort_key(higher)


@pytest.mark.parametrize("tag", ["v2026.09.01-9b9e54d", "2.0.0", "v2.0.0-rc", "", None])
def test_release_sort_key_rejects_a_non_release(tag):
    with pytest.raises(ValueError):
        release_sort_key(tag)


@pytest.mark.parametrize("tag", ["v2.0.0-rc", "v2.0.0-rc.", "v2.0.0-alpha", "v2.0.0-"])
def test_a_suffix_without_a_number_is_not_a_release(tag):
    # A candidate is one of a numbered series. `v2.0.0-rc` names no image any
    # release build publishes, and `v2.0.0-rc.` is not even a legal git ref -
    # accepting either only defers the failure to `docker compose pull`.
    assert not is_release_tag(tag)


def test_the_highest_release_tag_at_head_is_the_version(git_repo):
    # git lists tags by refname, so the first match is the LOWEST candidate.
    # Created out of order on purpose: creation order must not decide it
    # either.
    _git(git_repo, "tag", "v2.0.0-rc.2")
    _git(git_repo, "tag", "v2.0.0-rc.1")

    assert runtime.parse_version() == "v2.0.0-rc.2"


def test_a_release_outranks_its_own_candidate_at_head(git_repo):
    # Promoting by tagging the same commit: the release is what runs, whatever
    # `tag.sort` is configured to do locally.
    _git(git_repo, "tag", "v2.0.0-rc.1")
    _git(git_repo, "tag", "v2.0.0")
    _git(git_repo, "config", "versionsort.suffix", "-rc")
    _git(git_repo, "config", "tag.sort", "version:refname")

    assert runtime.parse_version() == "v2.0.0"


def test_tags_at_head_reports_what_git_lists(git_repo):
    _git(git_repo, "tag", "v2.0.0-hotfix.1")
    _git(git_repo, "tag", "some-marker")

    assert sorted(runtime.tags_at_head()) == ["some-marker", "v2.0.0-hotfix.1"]
