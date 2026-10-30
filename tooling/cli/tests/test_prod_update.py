"""
Tests for the `mascope prod update` command wiring.

Verifies that the preflight is guarded by a running Postgres container, that
its classification propagates to the process exit code, and — most importantly
— that `--check` never touches the running stack (no `docker compose`). Also
covers what a manual update leaves behind: the deployment checkout aligned to
the release it deployed, and the pending migration record `--auto` wrote
settled once that release (or a later one) runs.
"""

import importlib
import json
import os
import shutil
import subprocess

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au


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


def test_align_checkout_moves_to_a_prerelease(release_repo):
    """A pre-release is a release here: the checkout must name what runs."""
    _git(release_repo, "checkout", "--quiet", "v9.0.2")
    (release_repo / "file.txt").write_text("three")
    _git(release_repo, "commit", "--quiet", "-am", "three")
    _git(release_repo, "tag", "v9.1.0-rc.1")
    _git(release_repo, "checkout", "--quiet", "v9.0.1")

    prod_main._align_checkout("v9.1.0-rc.1", str(release_repo))

    tag = _git(
        release_repo, "rev-parse", "refs/tags/v9.1.0-rc.1^{commit}"
    ).stdout.strip()
    assert _head(release_repo) == tag


def test_align_checkout_ignores_a_dated_build_tag(release_repo):
    """`v{date}-{sha}` names a build, not a release - nothing to align to.

    The tag is created here on purpose: were it accepted as a release, the
    checkout would move to it, so this fails rather than passing by accident
    on a tag git could not resolve anyway.
    """
    _git(release_repo, "tag", "v2026.09.01-9b9e54d", "v9.0.2")
    before = _head(release_repo)

    prod_main._align_checkout("v2026.09.01-9b9e54d", str(release_repo))

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


# --- a manual update settles the pending migration record --auto left ---


def _record_pending(root, version):
    au.save_pending(
        str(root),
        au.PendingUpdate(
            version=version,
            alembic_head="abc123def456",
            first_seen_at="2026-10-16T00:39:31",
        ),
    )


@pytest.mark.parametrize(
    ("deployed", "pending", "settled"),
    [
        ("v1.12.0", "v1.12.0", True),  # the pending release itself
        ("v1.13.0", "v1.12.0", True),  # a later release carries its migration
        ("v1.13.0-rc.1", "v1.12.0", True),  # so does a later release's candidate
        ("v1.11.0", "v1.12.0", False),  # a rollback leaves the update outstanding
        ("v1.12.0-rc.1", "v1.12.0", False),  # a candidate ranks below its release
        ("latest", "v1.12.0", False),  # names no release to order against
        ("v1.12.0", "v1.12", False),  # a recorded tag that is not a release
    ],
)
def test_settle_pending_orders_by_release(tmp_path, deployed, pending, settled):
    _record_pending(tmp_path, pending)

    prod_main._settle_pending(deployed, str(tmp_path))

    assert (au.load_pending(str(tmp_path)) is None) is settled


def test_settle_pending_without_a_record_is_a_noop(tmp_path):
    prod_main._settle_pending("v1.12.0", str(tmp_path))  # must not raise

    assert au.load_pending(str(tmp_path)) is None


@pytest.fixture
def manual_update(monkeypatch, mascope_home):
    """`prod update` with every docker and git step stubbed out."""
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    monkeypatch.setattr(prod_main, "_abort_if_low_disk", lambda **k: None)
    monkeypatch.setattr(prod_main, "_run_compose", lambda *a, **k: None)
    monkeypatch.setattr(prod_main, "_prune_images", lambda: None)
    monkeypatch.setattr(prod_main, "_align_checkout", lambda *a: None)
    monkeypatch.setattr(prod_main, "_warn_if_cli_stale", lambda **k: None)
    yield monkeypatch
    shutil.rmtree(au.update_dir(str(mascope_home)), ignore_errors=True)


def test_manual_update_settles_the_pending_release(
    manual_update, cli_runner, mascope_home
):
    _record_pending(mascope_home, "v1.12.0")

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", "v1.12.0"])

    assert result.exit_code == 0, result.output
    assert au.load_pending(str(mascope_home)) is None
    status_log = au.update_dir(str(mascope_home)) / "status.log"
    last = status_log.read_text(encoding="utf-8").splitlines()[-1]
    assert "Pending migration update v1.12.0 settled" in last


def test_unreadable_pending_record_does_not_fail_the_update(
    manual_update, cli_runner, mascope_home
):
    """A state file that parses but lacks a field is reported, not raised.

    The stack already runs the release by then, so the update succeeds and the
    checkout is still aligned to it.
    """
    state = au.update_dir(str(mascope_home)) / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"pending": {"version": "v1.12.0"}}', encoding="utf-8")
    aligned = []
    manual_update.setattr(
        prod_main, "_align_checkout", lambda target, path: aligned.append(target)
    )
    # Captured at the logger: loguru's queued sink writes after CliRunner has
    # closed its stream, so result.output never shows the warning.
    warnings = []
    manual_update.setattr(prod_main.runtime.logger, "warning", warnings.append)

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", "v1.12.0"])

    assert result.exit_code == 0, result.output
    assert aligned == ["v1.12.0"]
    assert any("state.json" in w for w in warnings), warnings


def test_failed_manual_update_keeps_the_pending_record(
    manual_update, cli_runner, mascope_home
):
    _record_pending(mascope_home, "v1.12.0")

    def _compose(args, building=False):
        if args[0] == "up":
            raise typer.Exit(1)  # what _run_compose raises when compose fails

    manual_update.setattr(prod_main, "_run_compose", _compose)

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", "v1.12.0"])

    assert result.exit_code == 1
    assert au.load_pending(str(mascope_home)).version == "v1.12.0"


# --- a wheel deployment keeps the release an update deployed ---


@pytest.fixture
def wheel_home(manual_update, tmp_path):
    """`prod update` and `prod up` on a runtime home that is no git checkout.

    MASCOPE_PATH moves to a fresh directory, so no record reaches the shared
    test home. The runtime singleton is built first: built from the empty
    directory, its config would not load.
    """
    prod_main.runtime.full_config  # noqa: B018
    manual_update.setenv("MASCOPE_PATH", str(tmp_path))
    return tmp_path


def _forget_the_pin():
    """What the end of the update's process does to its --version pin."""
    os.environ.pop("MASCOPE_VERSION", None)
    os.environ.pop("_MASCOPE_VERSION_PINNED", None)


def _up(monkeypatch, cli_runner, *args):
    """Run `prod up`; return the tag each compose call would deploy."""
    deployed = []
    monkeypatch.setattr(
        prod_main,
        "_run_compose",
        lambda a, building=False: deployed.append(prod_main._deploy_version()),
    )
    result = cli_runner.invoke(prod_main.prod_app, ["up", *args])
    assert result.exit_code == 0, result.output
    return deployed


def _update(cli_runner, *args):
    result = cli_runner.invoke(prod_main.prod_app, ["update", *args])
    assert result.exit_code == 0, result.output


def test_a_wheel_update_is_redeployed_by_the_next_up(
    manual_update, wheel_home, cli_runner
):
    _update(cli_runner, "--version", "v1.12.0")
    _forget_the_pin()

    assert _up(manual_update, cli_runner, "--detach") == ["v1.12.0"]


def test_a_wheel_update_records_the_release_atomically(
    manual_update, wheel_home, cli_runner
):
    writes = []
    real = prod_main.deployed_release.write_json
    manual_update.setattr(
        prod_main.deployed_release,
        "write_json",
        lambda path, data, **k: (writes.append(path), real(path, data, **k)),
    )

    _update(cli_runner, "--version", "v1.12.0")

    record = wheel_home / ".runtime" / "deployed-release.json"
    assert writes == [str(record)]
    assert json.loads(record.read_text(encoding="utf-8"))["version"] == "v1.12.0"


def test_a_wheel_update_without_version_redeploys_the_recorded_release(
    manual_update, wheel_home, cli_runner
):
    prod_main.deployed_release.save(str(wheel_home), "v1.12.0")
    targets = []
    manual_update.setattr(
        prod_main, "_settle_pending", lambda target, path: targets.append(target)
    )

    _update(cli_runner)

    assert targets == ["v1.12.0"]


def test_a_wheel_update_to_latest_clears_the_record(
    manual_update, wheel_home, cli_runner
):
    prod_main.deployed_release.save(str(wheel_home), "v1.12.0")

    _update(cli_runner, "--version", "latest")
    _forget_the_pin()

    assert prod_main.deployed_release.load(str(wheel_home)) is None
    assert _up(manual_update, cli_runner, "--detach") == ["latest"]


def test_a_failed_wheel_update_records_nothing(manual_update, wheel_home, cli_runner):
    prod_main.deployed_release.save(str(wheel_home), "v1.11.0")

    def _compose(args, building=False):
        if args[0] == "up":
            raise typer.Exit(1)  # what _run_compose raises when compose fails

    manual_update.setattr(prod_main, "_run_compose", _compose)

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", "v1.12.0"])

    assert result.exit_code == 1
    assert prod_main.deployed_release.load(str(wheel_home)) == "v1.11.0"


def test_an_unwritable_record_does_not_fail_the_update(
    manual_update, wheel_home, cli_runner
):
    def _refuse(*a, **k):
        raise PermissionError("read-only")

    manual_update.setattr(prod_main.deployed_release, "write_json", _refuse)
    # Captured at the logger: loguru's queued sink writes after CliRunner has
    # closed its stream, so result.output never shows the warning.
    warnings = []
    manual_update.setattr(prod_main.runtime.logger, "warning", warnings.append)

    _update(cli_runner, "--version", "v1.12.0")

    assert any("MASCOPE_VERSION=v1.12.0" in w for w in warnings), warnings


def test_a_checkout_update_writes_no_record(manual_update, cli_runner, release_repo):
    """A checkout keeps the release itself: _align_checkout moves it."""
    manual_update.setenv("MASCOPE_PATH", str(release_repo))

    _update(cli_runner, "--version", "v9.0.2")

    assert not (release_repo / ".runtime" / "deployed-release.json").exists()


def test_a_detached_up_records_a_pinned_release(manual_update, wheel_home, cli_runner):
    manual_update.setenv("MASCOPE_VERSION", "v1.12.0")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "1")

    assert _up(manual_update, cli_runner, "--detach") == ["v1.12.0"]
    _forget_the_pin()

    assert prod_main.deployed_release.load(str(wheel_home)) == "v1.12.0"


@pytest.mark.parametrize(
    "args",
    [
        (),  # foreground: compose returns once the stack has stopped again
        ("--build", "--detach"),  # a local build, not a release
    ],
)
def test_other_ups_record_nothing(manual_update, wheel_home, cli_runner, args):
    manual_update.setenv("MASCOPE_VERSION", "v1.12.0")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "1")

    _up(manual_update, cli_runner, *args)

    assert prod_main.deployed_release.load(str(wheel_home)) is None


def test_a_failed_up_records_nothing(manual_update, wheel_home, cli_runner):
    manual_update.setenv("MASCOPE_VERSION", "v1.12.0")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "1")

    def _compose(args, building=False):
        raise typer.Exit(1)

    manual_update.setattr(prod_main, "_run_compose", _compose)

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--detach"])

    assert result.exit_code == 1
    assert prod_main.deployed_release.load(str(wheel_home)) is None


def test_a_non_release_pin_leaves_the_record(manual_update, wheel_home, cli_runner):
    """A pin to another image tag is temporary: the release stays to return to."""
    prod_main.deployed_release.save(str(wheel_home), "v1.12.0")
    manual_update.setenv("MASCOPE_VERSION", "sha-abc1234")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "1")

    assert _up(manual_update, cli_runner, "--detach") == ["sha-abc1234"]

    assert prod_main.deployed_release.load(str(wheel_home)) == "v1.12.0"


def test_a_boot_on_the_recorded_release_leaves_the_record_alone(
    manual_update, wheel_home, cli_runner
):
    """Every boot is a detached `up`: it must not rewrite the file each time."""
    prod_main.deployed_release.save(str(wheel_home), "v1.12.0")

    def _no_write(*a, **k):
        raise AssertionError("rewrote the record it deployed from")

    manual_update.setattr(prod_main.deployed_release, "write_json", _no_write)

    assert _up(manual_update, cli_runner, "--detach") == ["v1.12.0"]


def test_an_update_replaces_an_unreadable_record(manual_update, wheel_home, cli_runner):
    record = prod_main.deployed_release.path(str(wheel_home))
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{torn", encoding="utf-8")

    _update(cli_runner, "--version", "v1.12.0")

    assert prod_main.deployed_release.load(str(wheel_home)) == "v1.12.0"


def test_a_detached_up_keeps_an_unreadable_record(
    manual_update, wheel_home, cli_runner
):
    """The fallback past a bad record must leave the file its warning names."""
    record = prod_main.deployed_release.path(str(wheel_home))
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"version": "1.12.0"}', encoding="utf-8")

    assert _up(manual_update, cli_runner, "--detach") == ["latest"]

    assert record.read_text(encoding="utf-8") == '{"version": "1.12.0"}'


def test_a_latest_pin_on_a_detached_up_keeps_the_record(
    manual_update, wheel_home, cli_runner
):
    """Only `update --version latest` clears the record, not a passing pin."""
    prod_main.deployed_release.save(str(wheel_home), "v1.12.0")
    manual_update.setenv("MASCOPE_VERSION", "latest")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "1")

    assert _up(manual_update, cli_runner, "--detach") == ["latest"]
    _forget_the_pin()

    assert prod_main.deployed_release.load(str(wheel_home)) == "v1.12.0"


def test_a_detached_up_without_a_record_warns_once(
    manual_update, wheel_home, cli_runner
):
    warnings = []
    manual_update.setattr(prod_main.runtime.logger, "warning", warnings.append)

    assert _up(manual_update, cli_runner, "--detach") == ["latest"]

    assert len(warnings) == 1, warnings


def test_an_unpinned_up_does_not_record_the_cli_version(
    manual_update, wheel_home, cli_runner
):
    """Unpinned, MASCOPE_VERSION holds the CLI's own calver, never a release."""
    manual_update.setenv("MASCOPE_VERSION", "v2026.9.30")
    manual_update.setenv("_MASCOPE_VERSION_PINNED", "0")

    assert _up(manual_update, cli_runner, "--detach") == ["latest"]

    assert prod_main.deployed_release.load(str(wheel_home)) is None
