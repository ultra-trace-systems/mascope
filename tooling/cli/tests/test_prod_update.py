"""
Tests for the `mascope prod update --check` command wiring.

Verifies that the preflight is guarded by a running Postgres container, that
its classification propagates to the process exit code, and — most importantly
— that `--check` never touches the running stack (no `docker compose`).
"""

import importlib
import subprocess

import pytest
import typer


# The prod package re-exports a `main` function that shadows the module of the
# same name, so import the module explicitly (as test_prod_compose does).
prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


def _fake_plan(classification="migration-update"):
    return prod_main.preflight.UpdatePlan(
        target="v1.3.0",
        classification=classification,
        image_changed=True,
        migration_pending=classification == "migration-update",
        current_revision="000000aaaaaa",
        target_revision="abc123def456",
    )


def test_preflight_requires_running_postgres(monkeypatch):
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: False)

    with pytest.raises(typer.Exit) as excinfo:
        prod_main._preflight("v1.2.0", pull=False, as_json=False)

    assert excinfo.value.exit_code == prod_main.preflight.ERROR_EXIT_CODE


def test_preflight_exits_with_plan_code(monkeypatch):
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(
        prod_main.preflight,
        "build_plan",
        lambda **kwargs: _fake_plan("migration-update"),
    )

    with pytest.raises(typer.Exit) as excinfo:
        prod_main._preflight("v1.3.0", pull=True, as_json=False)

    assert excinfo.value.exit_code == 20


def test_preflight_error_exits_error_code(monkeypatch):
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)

    def _boom(**kwargs):
        raise prod_main.preflight.PreflightError("pull failed")

    monkeypatch.setattr(prod_main.preflight, "build_plan", _boom)

    with pytest.raises(typer.Exit) as excinfo:
        prod_main._preflight("v1.3.0", pull=True, as_json=False)

    assert excinfo.value.exit_code == prod_main.preflight.ERROR_EXIT_CODE


def test_update_check_does_not_apply(monkeypatch, cli_runner):
    """--check must classify and exit without running any docker compose."""
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(
        prod_main.preflight, "build_plan", lambda **kwargs: _fake_plan("fast-update")
    )

    def _fail(*args, **kwargs):
        raise AssertionError("docker compose must not run under --check")

    monkeypatch.setattr(prod_main, "_run_compose", _fail)
    monkeypatch.setattr(prod_main, "check_data_dirs", _fail)

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--check", "--no-pull"])

    assert result.exit_code == 10


def test_update_check_json_output(monkeypatch, cli_runner):
    monkeypatch.setattr(prod_main, "is_container_running", lambda mode: True)
    monkeypatch.setattr(
        prod_main.preflight,
        "build_plan",
        lambda **kwargs: _fake_plan("migration-update"),
    )
    monkeypatch.setattr(prod_main, "_run_compose", lambda *a, **k: None)

    result = cli_runner.invoke(
        prod_main.prod_app, ["update", "--check", "--no-pull", "--json"]
    )

    assert result.exit_code == 20
    assert '"classification": "migration-update"' in result.stdout


# --- _align_checkout: the applied release must be what a reboot redeploys ---


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def release_repo(tmp_path):
    """A deployment checkout with two tagged releases, sitting on the older one."""
    repo = tmp_path / "deploy"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "test@test.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("one")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "--quiet", "-m", "one")
    _git(repo, "tag", "v9.0.1")
    (repo / "file.txt").write_text("two")
    _git(repo, "commit", "--quiet", "-am", "two")
    _git(repo, "tag", "v9.0.2")
    _git(repo, "checkout", "--quiet", "v9.0.1")
    return repo


def test_align_checkout_moves_a_clean_checkout(release_repo):
    prod_main._align_checkout("v9.0.2", str(release_repo))

    tag = _git(release_repo, "rev-parse", "refs/tags/v9.0.2^{commit}").stdout.strip()
    assert _head(release_repo) == tag


def test_align_checkout_never_discards_local_changes(release_repo):
    """A dirty checkout stays put (warned, not forced) and keeps its edits."""
    before = _head(release_repo)
    (release_repo / "file.txt").write_text("operator edit")

    prod_main._align_checkout("v9.0.2", str(release_repo))

    assert _head(release_repo) == before
    assert (release_repo / "file.txt").read_text() == "operator edit"


def test_align_checkout_noop_when_already_on_the_release(release_repo):
    _git(release_repo, "checkout", "--quiet", "v9.0.2")
    before = _head(release_repo)

    prod_main._align_checkout("v9.0.2", str(release_repo))

    assert _head(release_repo) == before


def test_align_checkout_ignores_latest(release_repo):
    """`latest` tracks master - there is no tag to align to."""
    before = _head(release_repo)

    prod_main._align_checkout("latest", str(release_repo))

    assert _head(release_repo) == before


def test_align_checkout_survives_a_non_repo(tmp_path):
    prod_main._align_checkout("v9.0.2", str(tmp_path))  # must not raise


def test_align_checkout_survives_an_unfetchable_tag(release_repo):
    """A tag that neither exists nor can be fetched warns instead of failing."""
    before = _head(release_repo)

    prod_main._align_checkout("v9.9.9", str(release_repo))  # no `origin` remote

    assert _head(release_repo) == before
