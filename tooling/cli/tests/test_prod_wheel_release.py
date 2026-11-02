"""
Tests for a wheel install deploying the release its CLI was cut with.

A wheel install's compose file and config layers are the copies its CLI
bundles, so it deploys `mascope_cli.release.RELEASE` alone: `prod up` and
`prod update` refuse another release and name the CLI to install for it, the
first `prod up` after a CLI upgrade brings the home's files to the CLI's
copies (`refresh_config_files`), and `--auto` hands a due update over to the
newest release's CLI with a dedicated exit code. A checkout install is left
as it was.
"""

import importlib
import json

import pytest
import typer

import mascope_cli.cmd.prod.auto_update as au
from mascope_cli.cmd import init as init_cmd


prod_main = importlib.import_module("mascope_cli.cmd.prod.main")

_HEAD = "abc123def456"
_OWN = "v1.12.0"
_NEXT = "v1.13.0"
_NEXT_CLI = "2026.11.2"


def _bundled(name):
    return init_cmd._bundled(name)


def _manifest_stub(cli_versions):
    """`download_manifest` serving a manifest per release, naming its CLI."""

    def _download(repo, tag, dest_dir):
        if tag not in cli_versions:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "mascope-manifest.json"
        data = {"schema_version": 1, "app_version": tag, "alembic_head": _HEAD}
        if cli_versions[tag] is not ...:
            data["cli_version"] = cli_versions[tag]
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _download


@pytest.fixture
def wheel(monkeypatch, tmp_path):
    """A wheel install's home whose CLI was cut with v1.12.0.

    Docker is stubbed; ``wheel.compose`` lists the compose calls made. The
    runtime singleton is built before MASCOPE_PATH moves: built from the
    empty directory, its config would not load.
    """
    prod_main.runtime.full_config  # noqa: B018
    monkeypatch.setenv("MASCOPE_PATH", str(tmp_path))
    monkeypatch.setattr(prod_main.mascope_release, "RELEASE", _OWN)
    monkeypatch.setattr(prod_main.mascope_release, "cli_version", lambda: "2026.10.1")
    monkeypatch.setattr(
        prod_main.auto_update,
        "download_manifest",
        _manifest_stub({_NEXT: _NEXT_CLI}),
    )
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    monkeypatch.setattr(prod_main, "_abort_if_low_disk", lambda **k: None)
    monkeypatch.setattr(prod_main, "_prune_images", lambda: None)
    monkeypatch.setattr(prod_main, "_align_checkout", lambda *a: None)
    monkeypatch.setattr(prod_main, "_warn_if_cli_stale", lambda **k: None)
    calls = []
    monkeypatch.setattr(
        prod_main,
        "_run_compose",
        lambda args, building=False: calls.append(
            (args[0], prod_main._deploy_version())
        ),
    )
    errors = []
    monkeypatch.setattr(prod_main.runtime.logger, "error", errors.append)
    monkeypatch.home = tmp_path
    monkeypatch.compose = calls
    monkeypatch.errors = errors
    return monkeypatch


def _pin(env, version):
    env.setenv("MASCOPE_VERSION", version)
    env.setenv("_MASCOPE_VERSION_PINNED", "1")


# --- what a wheel install is ---


def test_a_home_without_git_or_cli_source_is_a_wheel_install(wheel):
    assert prod_main._is_wheel_install(str(wheel.home))


def test_a_checkout_git_cannot_read_is_no_wheel_install(wheel):
    """Its files are the checkout's tracked ones, never the installed CLI's."""
    (wheel.home / "tooling" / "cli" / "src" / "mascope_cli").mkdir(parents=True)

    assert not prod_main._is_wheel_install(str(wheel.home))


def test_a_git_checkout_is_no_wheel_install(wheel):
    wheel.setattr(prod_main.runtime, "parse_version", lambda **kw: "v1.11.0")

    assert not prod_main._is_wheel_install(str(wheel.home))


# --- the refusal ---


def test_up_refuses_another_release_and_names_its_cli(wheel, cli_runner):
    _pin(wheel, _NEXT)

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--detach"])

    assert result.exit_code == 1
    assert wheel.compose == []
    assert any(
        f"uv tool install mascope-cli=={_NEXT_CLI}" in e for e in wheel.errors
    ), wheel.errors
    assert any(f"deploys {_OWN} only" in e for e in wheel.errors), wheel.errors


def test_update_refuses_another_release_before_touching_anything(wheel, cli_runner):
    def _never(mode):
        raise AssertionError("checked the data dirs of a refused update")

    wheel.setattr(prod_main, "check_data_dirs", _never)
    prod_main.deployed_release.save(str(wheel.home), _OWN)

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", _NEXT])

    assert result.exit_code == 1
    assert wheel.compose == []
    assert any(f"mascope-cli=={_NEXT_CLI}" in e for e in wheel.errors), wheel.errors
    assert prod_main.deployed_release.load(str(wheel.home)) == _OWN
    # Nothing of the next release's: the home keeps the files it had.
    assert not (wheel.home / "docker-compose.yaml").exists()


@pytest.mark.parametrize(
    ("target", "manifests"),
    [
        ("v1.13.0", {}),  # no manifest: a release before the field, or offline
        ("v1.13.0", {"v1.13.0": None}),  # cut without a CLI of its own
        ("v1.13.0", {"v1.13.0": ...}),  # a manifest from before cli_version
        ("latest", {}),  # the rolling master build
    ],
)
def test_a_release_no_cli_names_is_refused_without_an_install_command(
    wheel, cli_runner, target, manifests
):
    wheel.setattr(prod_main.auto_update, "download_manifest", _manifest_stub(manifests))

    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", target])

    assert result.exit_code == 1
    assert wheel.compose == []
    assert any("no published CLI names" in e for e in wheel.errors), wheel.errors
    assert not any("uv tool install" in e for e in wheel.errors), wheel.errors


def test_the_clis_own_release_is_deployed(wheel, cli_runner):
    result = cli_runner.invoke(prod_main.prod_app, ["update", "--version", _OWN])

    assert result.exit_code == 0, result.output
    assert wheel.compose == [("pull", _OWN), ("up", _OWN), ("ps", _OWN)]


def test_check_classifies_another_release_without_refusing(wheel, cli_runner):
    """--check changes nothing, so it may look at any release."""
    wheel.setattr(prod_main, "is_container_running", lambda mode: True)
    wheel.setattr(
        prod_main.preflight,
        "build_plan",
        lambda **k: prod_main.preflight.UpdatePlan(
            target=k["target"],
            classification="fast-update",
            image_changed=True,
            migration_pending=False,
            current_revision=_HEAD,
            target_revision=_HEAD,
        ),
    )

    result = cli_runner.invoke(
        prod_main.prod_app, ["update", "--check", "--no-pull", "--version", _NEXT]
    )

    assert result.exit_code == 10
    assert wheel.errors == []


def test_a_local_build_is_neither_refused_nor_refreshed(wheel, cli_runner):
    _pin(wheel, "sha-abc1234")

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--build", "--detach"])

    assert result.exit_code == 0, result.output
    assert not (wheel.home / "docker-compose.yaml").exists()


def test_a_checkout_install_is_unchanged(wheel, cli_runner):
    """Its tag selects the release, and its files are left alone."""
    wheel.setattr(prod_main.runtime, "parse_version", lambda **kw: "v1.9.0")
    (wheel.home / "base.mascope.toml").write_bytes(b"# the checkout's own\n")

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--detach"])

    assert result.exit_code == 0, result.output
    assert wheel.compose == [("up", "v1.9.0")]
    assert (wheel.home / "base.mascope.toml").read_bytes() == b"# the checkout's own\n"
    assert not (wheel.home / init_cmd.REFRESH_STAMP).exists()


# --- the refresh after a CLI upgrade ---


@pytest.fixture
def upgraded_home(tmp_path):
    """A home the previous CLI wrote: its files, an overlay and the secrets."""
    home = tmp_path
    for name in init_cmd.CONFIG_FILES:
        (home / name).write_bytes(b"# the previous release's " + name.encode() + b"\n")
    overlay = home / ".runtime" / "env" / "default" / "prod.mascope.toml"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b'[backend]\nworkers = 3\n# "edited by hand"\n')
    secrets = home / ".runtime" / "secrets"
    secrets.mkdir(parents=True)
    for name in init_cmd.GENERATED_SECRETS:
        (secrets / name).write_bytes(b"secret-" + name.encode() + b"\n")
    (secrets / "mascope.app.pem").write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    return home


def _snapshot(root):
    return {
        p.relative_to(root): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_refresh_keeps_the_overlay_and_the_secrets_byte_identical(upgraded_home):
    runtime_dir = upgraded_home / ".runtime"
    before = _snapshot(runtime_dir)

    refreshed = init_cmd.refresh_config_files(upgraded_home, "2026.11.2")

    after = _snapshot(runtime_dir)
    for rel, content in before.items():
        assert after[rel] == content, f"{rel} changed"
    for name in init_cmd.CONFIG_FILES:
        assert (upgraded_home / name).read_bytes() == _bundled(name), name
    assert refreshed.written == list(init_cmd.CONFIG_FILES)


def test_refresh_keeps_a_copy_of_what_it_replaced(upgraded_home):
    before = {n: (upgraded_home / n).read_bytes() for n in init_cmd.CONFIG_FILES}

    refreshed = init_cmd.refresh_config_files(upgraded_home, "2026.11.2")

    kept = refreshed.replaced_dir
    assert kept.parent == upgraded_home / ".runtime" / "replaced"
    assert {p.name: p.read_bytes() for p in kept.iterdir()} == before


def test_refresh_runs_once_per_cli_version(upgraded_home):
    init_cmd.refresh_config_files(upgraded_home, "2026.11.2")
    # A hand edit after the refresh lasts until the next upgrade.
    (upgraded_home / "prod.mascope.toml").write_bytes(b"# edited\n")

    assert init_cmd.refresh_config_files(upgraded_home, "2026.11.2") is None
    assert (upgraded_home / "prod.mascope.toml").read_bytes() == b"# edited\n"

    refreshed = init_cmd.refresh_config_files(upgraded_home, "2026.12.1")
    assert refreshed.written == ["prod.mascope.toml"]
    assert (upgraded_home / "prod.mascope.toml").read_bytes() == _bundled(
        "prod.mascope.toml"
    )
    assert (refreshed.replaced_dir / "prod.mascope.toml").read_bytes() == b"# edited\n"


def test_refresh_writes_a_missing_file_without_a_copy(tmp_path):
    for name in init_cmd.CONFIG_FILES[1:]:
        (tmp_path / name).write_bytes(_bundled(name))

    refreshed = init_cmd.refresh_config_files(tmp_path, "2026.11.2")

    assert refreshed.written == [init_cmd.CONFIG_FILES[0]]
    assert refreshed.replaced_dir is None
    assert not (tmp_path / init_cmd.REPLACED_DIR).exists()


def test_refresh_of_a_current_home_changes_nothing(tmp_path):
    for name in init_cmd.CONFIG_FILES:
        (tmp_path / name).write_bytes(_bundled(name))

    assert init_cmd.refresh_config_files(tmp_path, "2026.11.2") is None
    assert not (tmp_path / init_cmd.REPLACED_DIR).exists()
    assert json.loads((tmp_path / init_cmd.REFRESH_STAMP).read_text())[
        "cli_version"
    ] == ("2026.11.2")


def test_a_failed_refresh_is_tried_again(upgraded_home, monkeypatch):
    real = init_cmd._replace_bytes
    writes = []

    def _second_fails(target, content):
        writes.append(target.name)
        if len(writes) == 2:
            raise OSError("disk full")
        real(target, content)

    monkeypatch.setattr(init_cmd, "_replace_bytes", _second_fails)
    with pytest.raises(OSError):
        init_cmd.refresh_config_files(upgraded_home, "2026.11.2")
    monkeypatch.setattr(init_cmd, "_replace_bytes", real)

    assert not (upgraded_home / init_cmd.REFRESH_STAMP).exists()
    refreshed = init_cmd.refresh_config_files(upgraded_home, "2026.11.2")
    assert refreshed is not None
    for name in init_cmd.CONFIG_FILES:
        assert (upgraded_home / name).read_bytes() == _bundled(name), name


def test_the_first_up_after_an_upgrade_refreshes_the_home(wheel, cli_runner):
    (wheel.home / "docker-compose.yaml").write_bytes(b"# v1.11.0's compose file\n")
    warnings = []
    wheel.setattr(prod_main.runtime.logger, "warning", warnings.append)

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--detach"])

    assert result.exit_code == 0, result.output
    assert wheel.compose == [("up", _OWN)]
    assert (wheel.home / "docker-compose.yaml").read_bytes() == _bundled(
        "docker-compose.yaml"
    )
    assert any("prod.mascope.toml" in w and "replaced" in w for w in warnings), warnings


def test_an_update_refreshes_before_it_pulls(wheel, cli_runner):
    """The pull reads the compose file: the release's images, not the last's."""
    (wheel.home / "docker-compose.yaml").write_bytes(b"# v1.11.0's compose file\n")
    seen = []
    wheel.setattr(
        prod_main,
        "_run_compose",
        lambda args, building=False: seen.append(
            (args[0], (wheel.home / "docker-compose.yaml").read_bytes())
        ),
    )

    result = cli_runner.invoke(prod_main.prod_app, ["update"])

    assert result.exit_code == 0, result.output
    assert seen[0] == ("pull", _bundled("docker-compose.yaml"))


def test_a_failed_refresh_deploys_nothing(wheel, cli_runner):
    def _fail(home, cli_version):
        raise OSError("read-only file system")

    wheel.setattr(init_cmd, "refresh_config_files", _fail)

    result = cli_runner.invoke(prod_main.prod_app, ["up", "--detach"])

    assert result.exit_code == 1
    assert wheel.compose == []
    assert any("read-only file system" in e for e in wheel.errors), wheel.errors


# --- --auto hands a due update over to the newest release's CLI ---


def _plan(classification, target=_NEXT):
    return prod_main.preflight.UpdatePlan(
        target=target,
        classification=classification,
        image_changed=True,
        migration_pending=classification == "migration-update",
        current_revision="000000aaaaaa",
        target_revision=_HEAD,
    )


@pytest.fixture
def auto(wheel):
    """`_auto` on the wheel home: the newest release is v1.13.0, in window."""
    wheel.setattr(prod_main, "is_container_running", lambda mode: True)
    wheel.setattr(prod_main.auto_update, "latest_release_tag", lambda repo: _NEXT)
    wheel.setattr(prod_main.auto_update, "disk_precheck", lambda: None)
    wheel.setattr(prod_main.auto_update, "in_window", lambda now, window: True)
    wheel.setattr(prod_main.auto_update, "wait_healthy", lambda c: True)
    wheel.delenv("MASCOPE_UPDATE_WINDOW", raising=False)
    wheel.delenv("MASCOPE_UPDATE_GRACE_DAYS", raising=False)
    return wheel


def _run_auto():
    with pytest.raises(typer.Exit) as e:
        prod_main._auto(pull=True)
    return e.value.exit_code


def _handover(home):
    path = au.cli_handover_path(str(home))
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_a_due_fast_update_hands_over_to_the_newer_cli(auto):
    auto.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("fast-update"))

    assert _run_auto() == au.AUTO_INSTALL_CLI

    assert _handover(auto.home) == f"mascope-cli=={_NEXT_CLI}\n"
    assert auto.compose == []
    status = (au.update_dir(str(auto.home)) / "status.log").read_text("utf-8")
    assert f"uv tool install mascope-cli=={_NEXT_CLI}" in status


def test_a_fast_update_outside_the_window_hands_nothing_over(auto):
    """Installing the CLI early would let a reboot apply it out of the window."""
    auto.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("fast-update"))
    auto.setattr(prod_main.auto_update, "in_window", lambda now, window: False)

    assert _run_auto() == au.AUTO_OK

    assert _handover(auto.home) is None


def test_a_migration_update_not_yet_due_hands_nothing_over(auto):
    auto.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("migration-update")
    )

    assert _run_auto() == au.AUTO_MIGRATION_PENDING

    assert _handover(auto.home) is None
    assert au.load_pending(str(auto.home)).version == _NEXT


def test_a_confirmed_migration_update_hands_over_and_stays_confirmed(auto):
    """The next CLI's run finds the confirm, and applies the update itself."""
    au.record_pending(str(auto.home), _NEXT, _HEAD)
    au.confirm_pending(str(auto.home))
    auto.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("migration-update")
    )

    assert _run_auto() == au.AUTO_INSTALL_CLI

    assert _handover(auto.home) == f"mascope-cli=={_NEXT_CLI}\n"
    assert au.load_pending(str(auto.home)).confirmed


def test_a_held_release_is_not_handed_over(auto):
    """A failed apply holds the release: no new CLI to apply it again."""
    au.record_failure(str(auto.home), _NEXT, _HEAD, "fast")
    auto.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("fast-update"))

    assert _run_auto() == au.AUTO_ERROR

    assert _handover(auto.home) is None


@pytest.mark.parametrize("cli", [None, ..., "2026.11.2; rm -rf /"])
def test_a_newer_release_without_a_cli_is_an_error(auto, cli):
    auto.setattr(
        prod_main.auto_update, "download_manifest", _manifest_stub({_NEXT: cli})
    )

    def _never(**kwargs):
        raise AssertionError("classified a release it cannot follow")

    auto.setattr(prod_main.preflight, "build_plan", _never)

    assert _run_auto() == au.AUTO_ERROR

    assert _handover(auto.home) is None
    assert any("names no CLI" in e for e in auto.errors), auto.errors


def test_an_unwritable_handover_is_an_error(auto):
    def _refuse(path, requirement):
        raise PermissionError("read-only")

    auto.setattr(prod_main.auto_update, "record_cli_handover", _refuse)
    auto.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("fast-update"))

    assert _run_auto() == au.AUTO_ERROR
    assert auto.compose == []


def test_the_clis_own_release_is_applied_on_its_refreshed_files(auto):
    """After the hand-over: the new CLI's run applies its release."""
    auto.setattr(prod_main.mascope_release, "RELEASE", _NEXT)
    prod_main.deployed_release.save(str(auto.home), _OWN)
    (auto.home / "docker-compose.yaml").write_bytes(b"# v1.12.0's compose file\n")
    auto.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("fast-update", _NEXT)
    )

    assert _run_auto() == au.AUTO_OK

    assert [call[0] for call in auto.compose] == ["pull", "up", "ps"]
    assert _handover(auto.home) is None
    assert prod_main.deployed_release.load(str(auto.home)) == _NEXT
    assert (auto.home / "docker-compose.yaml").read_bytes() == _bundled(
        "docker-compose.yaml"
    )


def test_an_unhealthy_apply_records_nothing(auto):
    auto.setattr(prod_main.mascope_release, "RELEASE", _NEXT)
    prod_main.deployed_release.save(str(auto.home), _OWN)
    auto.setattr(prod_main.auto_update, "wait_healthy", lambda c: False)
    auto.setattr(
        prod_main.preflight, "build_plan", lambda **k: _plan("fast-update", _NEXT)
    )

    assert _run_auto() == au.AUTO_ERROR

    assert prod_main.deployed_release.load(str(auto.home)) == _OWN


def test_a_cli_ahead_of_the_newest_release_is_never_moved_back(auto):
    """A CLI cut with a candidate deploys it; `--auto` never goes back."""
    auto.setattr(prod_main.mascope_release, "RELEASE", "v2.0.0-rc.1")

    def _never(**kwargs):
        raise AssertionError("classified an older release as an update")

    auto.setattr(prod_main.preflight, "build_plan", _never)

    assert _run_auto() == au.AUTO_OK

    assert _handover(auto.home) is None


def test_auto_on_a_checkout_never_hands_over(auto):
    auto.setattr(prod_main.runtime, "parse_version", lambda **kw: "v1.12.0")
    auto.setattr(prod_main.preflight, "build_plan", lambda **k: _plan("fast-update"))

    assert _run_auto() == au.AUTO_OK

    assert _handover(auto.home) is None
    assert [call[0] for call in auto.compose] == ["pull", "up", "ps"]


@pytest.mark.parametrize("tag", ["latest", "sha-abc1234"])
def test_the_record_names_only_releases(wheel, tag):
    """What ran is a release: a wheel install deploys no other tag."""
    prod_main.deployed_release.save(str(wheel.home), _OWN)

    prod_main._record_deployed(tag, str(wheel.home))

    assert prod_main.deployed_release.load(str(wheel.home)) == _OWN
