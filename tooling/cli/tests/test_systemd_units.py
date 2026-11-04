"""
Guards for the systemd units and `mascope prod units`, which installs them.

A unit is only as good as its installation: `mascope prod units install`
substitutes the `@@TOKEN@@` placeholders, so a placeholder it does not fill
would reach the server verbatim and systemd would silently ignore the
directive. `tooling/ubuntu.sh install` calls the command rather than filling
them itself, so a checkout install and a wheel install get the same units.

`WorkingDirectory` is asserted by name because losing it is not visible on the
server: the units run the CLI, which reads the deployment checkout to resolve
which release is deployed, and systemd's default working directory is `/`.

The privileged steps are recorded through the `_run` seam instead of run:
nothing here needs systemd or sudo. `mascope-update.sh`, the update unit's
hand-over to a newer CLI, is run with a stub `mascope` and `uv` on PATH.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest
import yaml
from loguru import logger
from typer.testing import CliRunner

from mascope_cli.cmd.prod import units


REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEMD_DIR = REPO_ROOT / "tooling" / "systemd"
INSTALLER = REPO_ROOT / "tooling" / "ubuntu.sh"
UPDATE_SCRIPT = SYSTEMD_DIR / "mascope-update.sh"
BUNDLED_DATA = Path(str(resources.files("mascope_cli") / "data"))

# Docker's stop timeout for a service that sets no stop_grace_period.
DOCKER_DEFAULT_GRACE_S = 10

# The units that invoke the `mascope` CLI, which resolves the deployed release
# from the checkout it runs in.
CLI_UNITS = ["mascope.service", "mascope-update.service"]

runner = CliRunner()

needs_checkout = pytest.mark.skipif(
    not SYSTEMD_DIR.is_dir(), reason="repo-root tooling/systemd not available"
)


def _render(home: Path, sources: units.Sources | None = None) -> dict[str, str]:
    return units.render(
        sources or units.sources_for(home),
        user="deploy",
        mascope_bin="/home/deploy/.local/bin/mascope",
        home=home,
    )


# --- The templates and what fills them ---


@needs_checkout
def test_every_template_in_the_checkout_is_installed():
    on_disk = {
        p.name for p in SYSTEMD_DIR.iterdir() if p.suffix in (".service", ".timer")
    }
    assert on_disk == set(units.UNITS)


@pytest.mark.parametrize("origin", ["checkout", "bundled"])
def test_every_placeholder_is_filled(origin, mascope_home):
    if origin == "checkout" and not SYSTEMD_DIR.is_dir():
        pytest.skip("repo-root tooling/systemd not available")
    home = REPO_ROOT if origin == "checkout" else mascope_home
    sources = units.sources_for(home)
    assert sources.origin == origin
    for name, text in _render(home, sources).items():
        assert not re.search(r"@@\w+@@", text), f"{name} keeps a placeholder"


def test_a_placeholder_nothing_fills_is_refused(tmp_path):
    files = dict(units.sources_for(tmp_path).files)
    odd = tmp_path / "mascope.service"
    odd.write_text("ExecStart=@@SOMETHING_NEW@@\n", encoding="utf-8")
    files["mascope.service"] = odd
    with pytest.raises(ValueError, match="mascope.service"):
        _render(tmp_path, units.Sources(files, "bundled"))


def test_the_units_name_the_account_the_binary_and_the_home(mascope_home):
    rendered = _render(mascope_home)
    boot = rendered["mascope.service"]
    assert "User=deploy\n" in boot
    assert f"WorkingDirectory={mascope_home}\n" in boot
    assert "ExecStart=/home/deploy/.local/bin/mascope prod up --detach\n" in boot
    prune = rendered["mascope-assignment-prune.service"]
    assert "ExecStart=/home/deploy/.local/bin/mascope prod db script run" in prune


def test_a_wheel_install_runs_copies_of_the_bundled_scripts(mascope_home):
    """
    Not the bundled files themselves: they live in the CLI's venv, which a
    rollback to an older CLI or another Python minor replaces.
    """
    rendered = _render(mascope_home)
    assert (
        "ExecStart=/bin/bash /usr/local/lib/mascope/disk-check.sh\n"
        in rendered["mascope-disk-check.service"]
    )
    assert (
        "ExecStart=/bin/bash /usr/local/lib/mascope/mascope-update.sh "
        f"/home/deploy/.local/bin/mascope {mascope_home}\n"
    ) in rendered["mascope-update.service"]
    sources = units.sources_for(mascope_home)
    assert units.copied_scripts(sources) == {
        "disk-check.sh": Path("/usr/local/lib/mascope/disk-check.sh"),
        "mascope-update.sh": Path("/usr/local/lib/mascope/mascope-update.sh"),
    }


@needs_checkout
def test_a_checkout_runs_its_disk_check_and_a_copy_of_the_update_script():
    """
    The disk check where `tooling/ubuntu.sh` always pointed it, the checkout;
    the update script from a copy, as a checkout of an older release lacks it.
    """
    rendered = _render(REPO_ROOT)
    assert (
        f"ExecStart=/bin/bash {REPO_ROOT / 'tooling' / 'disk-check.sh'}\n"
        in rendered["mascope-disk-check.service"]
    )
    assert (
        "ExecStart=/bin/bash /usr/local/lib/mascope/mascope-update.sh "
        in rendered["mascope-update.service"]
    )
    assert units.copied_scripts(units.sources_for(REPO_ROOT)) == {
        "mascope-update.sh": Path("/usr/local/lib/mascope/mascope-update.sh"),
    }


@needs_checkout
def test_a_timer_is_installed_as_it_is():
    rendered = _render(REPO_ROOT)
    for name in units.PLAIN_UNITS:
        assert rendered[name] == (SYSTEMD_DIR / name).read_text(encoding="utf-8")


@needs_checkout
@pytest.mark.parametrize("name", CLI_UNITS)
def test_cli_units_run_from_the_deployment_checkout(name):
    unit = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
    assert "WorkingDirectory=@@MASCOPE_PATH@@" in unit, (
        f"{name} must run from the checkout; without it systemd starts the "
        "unit in '/', where the CLI cannot resolve the checked-out release "
        "and falls back to deploying `latest`"
    )


def _seconds(duration: str) -> int:
    """A compose duration such as "90s" or "1m30s", in seconds."""
    parts = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert parts and any(parts.groups()), f"unreadable duration {duration!r}"
    minutes, seconds = (int(part or 0) for part in parts.groups())
    return 60 * minutes + seconds


@pytest.mark.parametrize("origin", ["checkout", "bundled"])
def test_the_boot_unit_outlasts_the_longest_compose_down(origin):
    """
    `prod down` is the boot unit's ExecStop, and systemd kills an ExecStop that
    runs past TimeoutStopSec - and with it the stop of every service it has not
    reached yet. `docker compose down` stops a service only once everything that
    depends on it has stopped, each given its own stop_grace_period, so the
    longest it can take is the longest chain of grace periods down the
    dependency graph: the file converter's, then the backend's, then what the
    backend runs on. A checkout install runs the checkout's unit and compose
    file, a wheel install the bundled copies, so both pairs are read.
    """
    if origin == "checkout":
        if not SYSTEMD_DIR.is_dir():
            pytest.skip("repo-root tooling/systemd not available")
        compose = REPO_ROOT / "docker-compose.yaml"
        unit_file = SYSTEMD_DIR / "mascope.service"
    else:
        compose = BUNDLED_DATA / "docker-compose.yaml"
        unit_file = units.bundled_dir() / "mascope.service"

    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    dependents = {name: [] for name in services}
    for name, service in services.items():
        for dependency in service.get("depends_on") or {}:
            dependents[dependency].append(name)

    def stopped_after(name: str) -> int:
        """Seconds into `compose down` by which `name` has stopped, at worst."""
        grace = services[name].get("stop_grace_period", f"{DOCKER_DEFAULT_GRACE_S}s")
        first = max((stopped_after(other) for other in dependents[name]), default=0)
        return first + _seconds(grace)

    longest = max(stopped_after(name) for name in services)

    unit = unit_file.read_text(encoding="utf-8")
    setting = re.search(r"^TimeoutStopSec=(.*)$", unit, re.MULTILINE)
    assert setting, (
        "mascope.service sets no TimeoutStopSec, so systemd's 90 s default "
        f"applies to a `prod down` that can take {longest} s"
    )
    assert setting[1].isdigit(), "write TimeoutStopSec in plain seconds"
    assert int(setting[1]) > longest, (
        f"mascope.service gives `prod down` {setting[1]} s, but its compose file "
        f"lets `compose down` wait {longest} s for its grace periods; systemd "
        "would kill it part-way and leave the rest of the stack running"
    )


# --- tooling/ubuntu.sh ---


@needs_checkout
def test_the_installer_leaves_the_units_to_the_cli():
    installer = INSTALLER.read_text(encoding="utf-8")
    assert '"${MASCOPE_BIN}" prod units install' in installer
    assert "@@" not in installer, (
        "tooling/ubuntu.sh fills a unit placeholder itself; "
        "`mascope prod units install` does that"
    )


@needs_checkout
def test_the_installer_removes_every_unit():
    """Its uninstall works without a CLI to call, so it lists them itself."""
    installer = INSTALLER.read_text(encoding="utf-8")
    loop = re.search(r"for unit in (.*?); do", installer, re.DOTALL)
    assert loop, "uninstall_mascope's unit loop not found"
    listed = tuple(loop.group(1).replace("\\\n", " ").split())
    assert listed == units.REMOVE_ORDER
    assert f"rm -rf {units.SCRIPT_DIR}\n" in installer, (
        "uninstall leaves the scripts `units install` copied"
    )


@needs_checkout
def test_installing_does_not_clear_the_runtime_state():
    """
    Re-running `ubuntu.sh install` is the documented way to pick up a change to
    the unit templates, so it must leave `.runtime/state.json` alone: that file
    carries the active environment, and every deployed server runs a named env
    rather than `default`. Clearing it points the boot service at a different
    database, which only shows up on the next restart.
    """
    installer = INSTALLER.read_text(encoding="utf-8")
    body = installer[installer.index("function main()") :]
    main_body = body[: body.index("\n}")]

    for block in re.findall(
        r"if \[ \"\$\(action_in ([^)]*)\)\" \][^\n]*\n(.*?)\n    fi",
        main_body,
        re.DOTALL,
    ):
        actions, statements = block
        if "clear_state" in statements:
            assert "'uninstall'" in actions, (
                "clear_state runs in a block reachable by a bare `install`; it "
                "deletes .runtime/state.json and would reset the server's "
                "active environment to `default`"
            )


# --- `mascope prod units install` / `remove` ---


@pytest.fixture
def host(monkeypatch, tmp_path, mascope_home):
    """
    A Linux host whose /etc lives under ``tmp_path``, with every step recorded.

    ``/etc/environment`` names the test home, as `ubuntu.sh` leaves it.
    """
    etc = tmp_path / "etc"
    etc.mkdir()
    environment = etc / "environment"
    environment.write_text(f"PATH=/usr/bin\nMASCOPE_PATH={mascope_home}\n")
    monkeypatch.setattr(units, "ENVIRONMENT_FILE", environment)
    monkeypatch.setattr(units, "CONFIG_DIR", etc / "mascope")
    monkeypatch.setattr(units.platform, "system", lambda: "Linux")
    monkeypatch.setattr(units.getpass, "getuser", lambda: "deploy")
    monkeypatch.setattr(
        units, "running_executable", lambda: "/home/deploy/.local/bin/mascope"
    )
    monkeypatch.setattr(units, "update_timer_enabled", lambda: False)
    steps: list[units.Step] = []

    def run(step):
        steps.append(step)
        return subprocess.CompletedProcess(step.argv, 0)

    monkeypatch.setattr(units, "_run", run)
    return {"steps": steps, "environment": environment, "etc": etc}


@pytest.fixture
def logs():
    """The messages the command logs, joined."""
    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG", format="{message}")
    yield lambda: "".join(messages)
    logger.remove(sink)


def _argvs(steps):
    return [" ".join(step.argv) for step in steps]


def test_install_writes_every_unit_as_rendered(host, mascope_home):
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    written = {
        Path(step.argv[2]).name: step.stdin
        for step in host["steps"]
        if step.argv[:2] == ["sudo", "tee"]
    }
    assert written == _render(mascope_home)
    for name in units.UNITS:
        assert f"sudo tee /etc/systemd/system/{name}" in _argvs(host["steps"])


def test_install_enables_boot_disk_check_and_retention_but_not_updates(host):
    runner.invoke(units.units_app, ["install"])
    argvs = _argvs(host["steps"])
    assert argvs[-4:] == [
        "sudo systemctl daemon-reload",
        "sudo systemctl enable mascope.service",
        "sudo systemctl enable --now mascope-disk-check.timer",
        "sudo systemctl enable --now mascope-assignment-prune.timer",
    ]
    assert not any("mascope-update.timer" in a for a in argvs if "enable" in a)


def test_install_seeds_the_settings_files_once(host):
    config = host["etc"] / "mascope"
    config.mkdir()
    (config / "update.env").write_text("MASCOPE_UPDATE_WINDOW=1-2\n")
    runner.invoke(units.units_app, ["install"])
    seeded = [s.argv for s in host["steps"] if str(config) in " ".join(s.argv)]
    assert seeded[0] == ["sudo", "install", "-d", "-m", "755", str(config)]
    bundled = units.bundled_dir()
    assert seeded[1:] == [
        [
            "sudo",
            "install",
            "-m",
            "600",
            "-o",
            "deploy",
            "-g",
            "deploy",
            str(bundled / template),
            str(config / name),
        ]
        for name, template in units.ENV_FILES.items()
        if name != "update.env"
    ]


def test_install_copies_the_scripts_before_writing_the_units(host):
    runner.invoke(units.units_app, ["install"])
    argvs = _argvs(host["steps"])
    bundled = units.bundled_dir()
    copies = [
        "sudo install -d -m 755 /usr/local/lib/mascope",
        f"sudo install -m 755 {bundled / 'disk-check.sh'} "
        "/usr/local/lib/mascope/disk-check.sh",
        f"sudo install -m 755 {bundled / 'mascope-update.sh'} "
        "/usr/local/lib/mascope/mascope-update.sh",
    ]
    first_unit = argvs.index("sudo tee /etc/systemd/system/mascope.service")
    assert argvs[first_unit - 3 : first_unit] == copies


def test_install_dry_run_prints_the_copies(host):
    result = runner.invoke(units.units_app, ["install", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert (
        "# copy mascope-update.sh to /usr/local/lib/mascope/mascope-update.sh"
        in result.output
    )


def test_install_says_which_settings_files_it_kept(host, logs):
    config = host["etc"] / "mascope"
    config.mkdir()
    kept = config / "update.env"
    kept.write_text("MASCOPE_UPDATE_WINDOW=1-2\n")
    result = runner.invoke(units.units_app, ["install", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert f"# kept existing {kept}\n" in result.output
    assert result.output.count("kept existing") == 1
    assert host["steps"] == []

    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    assert f"kept existing {kept}" in logs()
    assert all(s.argv for s in host["steps"]), "a message step was run"


def test_install_dry_run_prints_and_does_nothing(host, mascope_home):
    result = runner.invoke(units.units_app, ["install", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert host["steps"] == []
    assert "sudo tee /etc/systemd/system/mascope.service" in result.output
    assert f"    WorkingDirectory={mascope_home}" in result.output
    assert "sudo systemctl enable mascope.service" in result.output


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def _boot_unit(steps):
    return next(s for s in steps if s.argv[-1].endswith("/mascope.service")).stdin


def test_install_runs_the_named_binary(host, tmp_path):
    named = _executable(tmp_path / "opt" / "bin" / "mascope")
    result = runner.invoke(units.units_app, ["install", "--bin", str(named)])
    assert result.exit_code == 0, result.output
    assert f"ExecStart={named} prod up --detach\n" in _boot_unit(host["steps"])


def test_install_makes_a_relative_binary_absolute(host, tmp_path, monkeypatch):
    named = _executable(tmp_path / "venv" / "bin" / "mascope")
    monkeypatch.chdir(tmp_path)
    relative = os.path.join("venv", "bin", "mascope")
    result = runner.invoke(units.units_app, ["install", "--bin", relative])
    assert result.exit_code == 0, result.output
    assert f"ExecStart={named} prod up --detach\n" in _boot_unit(host["steps"])


@pytest.mark.parametrize("kind", ["missing", "not executable", "directory"])
def test_install_refuses_a_binary_that_is_not_an_executable_file(
    host, tmp_path, kind, logs
):
    named = tmp_path / "bin" / "mascope"
    if kind == "not executable":
        named.parent.mkdir()
        named.write_text("#!/bin/sh\n")
        named.chmod(0o644)
    elif kind == "directory":
        named.mkdir(parents=True)
    result = runner.invoke(units.units_app, ["install", "--bin", str(named)])
    assert result.exit_code == 1
    assert host["steps"] == []
    assert f"--bin {named} is not an executable file" in logs()


def test_named_executable_looks_a_bare_name_up_on_path(monkeypatch, tmp_path):
    named = _executable(tmp_path / "bin" / "mascope")
    monkeypatch.setenv("PATH", str(named.parent))
    monkeypatch.chdir(tmp_path)
    assert units.named_executable("mascope") == str(named)
    assert units.named_executable("not-mascope") is None


def test_install_tells_how_to_get_alerted(host, logs):
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    assert f"Set HEALTHCHECK_URL in {host['etc']}/mascope/disk-check.env" in logs()


def test_install_tells_nothing_about_alerts_once_they_are_set(host, logs):
    config = host["etc"] / "mascope"
    config.mkdir()
    (config / "disk-check.env").write_text(
        "# HEALTHCHECK_URL=https://hc-ping.com/<uuid>\n"
        "HEALTHCHECK_URL=https://hc-ping.com/abc\n"
    )
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    assert "Set HEALTHCHECK_URL" not in logs()


@pytest.mark.parametrize(
    "text, expected",
    [
        (None, False),
        ("# HEALTHCHECK_URL=https://hc-ping.com/<uuid>\n", False),
        ("HEALTHCHECK_URL=\n", False),
        ('HEALTHCHECK_URL="https://hc-ping.com/abc"\n', True),
    ],
)
def test_healthcheck_url_set(monkeypatch, tmp_path, text, expected):
    monkeypatch.setattr(units, "CONFIG_DIR", tmp_path)
    if text is not None:
        (tmp_path / "disk-check.env").write_text(text)
    assert units.healthcheck_url_set() is expected


def test_healthcheck_url_set_on_an_unreadable_file(monkeypatch, tmp_path):
    monkeypatch.setattr(units, "CONFIG_DIR", tmp_path)

    def unreadable(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(units, "env_file_value", unreadable)
    assert units.healthcheck_url_set() is False


def test_install_says_updates_are_off_when_the_timer_is(host, logs):
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    assert "Auto-updates are INSTALLED but DISABLED" in logs()
    assert "stay ENABLED" not in logs()


def test_install_says_updates_stay_on_when_the_timer_is(host, logs, monkeypatch):
    monkeypatch.setattr(units, "update_timer_enabled", lambda: True)
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    assert "Auto-updates stay ENABLED" in logs()
    assert "DISABLED" not in logs()


@pytest.mark.parametrize(
    "stdout, code, expected",
    [
        ("enabled\n", 0, True),
        ("enabled-runtime\n", 0, True),
        # is-enabled exits 0 for these too, yet nothing starts the timer.
        ("static\n", 0, False),
        ("indirect\n", 0, False),
        ("disabled\n", 1, False),
        ("", 1, False),
    ],
)
def test_update_timer_enabled(monkeypatch, stdout, code, expected):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout, "")

    monkeypatch.setattr(units.subprocess, "run", run)
    assert units.update_timer_enabled() is expected
    assert seen == [["systemctl", "is-enabled", "mascope-update.timer"]]


def test_update_timer_enabled_without_systemctl(monkeypatch):
    def run(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(units.subprocess, "run", run)
    assert units.update_timer_enabled() is False


def test_install_is_refused_off_linux(host, monkeypatch):
    monkeypatch.setattr(units.platform, "system", lambda: "Windows")
    result = runner.invoke(units.units_app, ["install", "--dry-run"])
    assert result.exit_code == 1
    assert host["steps"] == []


def test_remove_is_refused_off_linux(host, monkeypatch):
    monkeypatch.setattr(units.platform, "system", lambda: "Windows")
    result = runner.invoke(units.units_app, ["remove", "--dry-run"])
    assert result.exit_code == 1
    assert host["steps"] == []


def test_install_is_refused_as_root(host, monkeypatch):
    monkeypatch.setattr(units.getpass, "getuser", lambda: "root")
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 1
    assert host["steps"] == []


def test_install_refuses_when_etc_environment_names_another_home(host):
    host["environment"].write_text("MASCOPE_PATH=/srv/other\n")
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 1
    assert host["steps"] == []


def test_install_sets_mascope_path_when_etc_environment_lacks_it(host, mascope_home):
    """A wheel install's home: no `ubuntu.sh` put it in /etc/environment."""
    host["environment"].write_text("PATH=/usr/bin")
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 0, result.output
    first = host["steps"][0]
    assert first.argv == ["sudo", "tee", "-a", str(host["environment"])]
    assert first.stdin == f"\nMASCOPE_PATH={mascope_home}\n"


def test_install_leaves_etc_environment_that_names_this_home(host):
    runner.invoke(units.units_app, ["install"])
    assert not any("-a" in s.argv for s in host["steps"])


def test_install_stops_at_a_failed_step(host, monkeypatch):
    seen = []

    def failing(step):
        seen.append(step)
        return subprocess.CompletedProcess(step.argv, 1)

    monkeypatch.setattr(units, "_run", failing)
    result = runner.invoke(units.units_app, ["install"])
    assert result.exit_code == 1
    assert len(seen) == 1


def test_remove_stops_disables_and_deletes_every_unit(host):
    result = runner.invoke(units.units_app, ["remove"])
    assert result.exit_code == 0, result.output
    argvs = _argvs(host["steps"])
    for unit in units.UNITS:
        assert f"sudo systemctl disable {unit}" in argvs
        assert f"sudo rm -f /etc/systemd/system/{unit}" in argvs
    assert argvs[-2:] == [
        "sudo systemctl daemon-reload",
        "sudo rm -rf /usr/local/lib/mascope",
    ]
    assert not any("/etc/mascope" in a or "environment" in a for a in argvs)


def test_remove_carries_on_past_a_unit_that_is_not_loaded(host, monkeypatch):
    seen = []

    def run(step):
        seen.append(step)
        failed = step.argv[2:3] in (["stop"], ["disable"])
        return subprocess.CompletedProcess(step.argv, 5 if failed else 0)

    monkeypatch.setattr(units, "_run", run)
    result = runner.invoke(units.units_app, ["remove"])
    assert result.exit_code == 0, result.output
    assert len(seen) == len(units.remove_steps())


def test_units_are_a_prod_subcommand(host):
    from mascope_cli.main import app

    result = runner.invoke(app, ["prod", "units", "install", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "sudo systemctl enable mascope.service" in result.output


def test_remove_dry_run_does_nothing(host):
    result = runner.invoke(units.units_app, ["remove", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert host["steps"] == []
    assert "sudo systemctl disable mascope.service" in result.output


def test_running_executable_keeps_the_path_it_was_started_by(monkeypatch, tmp_path):
    """Not resolved through the link: the link is what a reinstall keeps."""
    target = tmp_path / "venv-mascope"
    target.write_text("")
    link = tmp_path / "mascope"
    link.symlink_to(target)
    monkeypatch.setattr(units.sys, "argv", [str(link)])
    monkeypatch.setattr(units.shutil, "which", lambda name: None)
    assert units.running_executable() == str(link)


def test_running_executable_resolves_a_bare_name(monkeypatch, tmp_path):
    exe = tmp_path / "mascope"
    exe.write_text("")
    monkeypatch.setattr(units.sys, "argv", ["mascope"])
    monkeypatch.setattr(units.shutil, "which", lambda name: str(exe))
    assert units.running_executable() == str(exe)


def test_running_executable_falls_back_to_path(monkeypatch, tmp_path):
    exe = tmp_path / "mascope"
    exe.write_text("")
    monkeypatch.setattr(units.sys, "argv", ["/usr/lib/python3/runpy.py"])
    monkeypatch.setattr(
        units.shutil, "which", lambda name: str(exe) if name == "mascope" else None
    )
    assert units.running_executable() == str(exe)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("MASCOPE_PATH=/srv/mascope\n", "/srv/mascope"),
        ('MASCOPE_PATH="/srv/mascope"\n', "/srv/mascope"),
        ("PATH=/usr/bin\n", None),
        ("MASCOPE_PATH=/a\nMASCOPE_PATH=/b\n", "/b"),
    ],
)
def test_environment_mascope_path(tmp_path, text, expected):
    path = tmp_path / "environment"
    path.write_text(text)
    assert units.environment_mascope_path(path) == expected


def test_environment_mascope_path_without_the_file(tmp_path):
    assert units.environment_mascope_path(tmp_path / "missing") is None


# --- mascope-update.sh: the update unit's hand-over to a newer CLI ---


def _bash() -> str | None:
    if sys.platform == "win32":
        return None
    return shutil.which("bash")


needs_bash = pytest.mark.skipif(
    _bash() is None or not UPDATE_SCRIPT.is_file(), reason="needs bash and the checkout"
)


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def update_run(tmp_path):
    """
    Run the script with a stub `mascope` and a stub `uv`.

    The stub `mascope` exits with the codes listed in ``codes`` (one per run)
    and logs each run; `uv` logs its arguments and exits ``uv_code``.
    """
    home = tmp_path / "home"
    (home / ".runtime" / "update").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    codes = tmp_path / "codes"
    mascope = bin_dir / "mascope"
    _stub(
        mascope,
        f'echo "mascope $*" >> {log}\n'
        f"code=$(head -n 1 {codes}); sed -i 1d {codes}; exit $code\n",
    )

    def run(sequence, *, handover="mascope-cli==2026.11.2", uv_code=0, uv=True):
        codes.write_text("".join(f"{c}\n" for c in sequence))
        if handover is not None:
            (home / ".runtime" / "update" / "cli-to-install").write_text(
                handover + "\n"
            )
        if uv:
            _stub(bin_dir / "uv", f'echo "uv $*" >> {log}\nexit {uv_code}\n')
        elif (bin_dir / "uv").exists():
            (bin_dir / "uv").unlink()
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "nohome"),
        }
        result = subprocess.run(
            [_bash(), str(UPDATE_SCRIPT), str(mascope), str(home)],
            env=env,
            capture_output=True,
            text=True,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return result, calls

    return run


@needs_bash
@pytest.mark.parametrize("code", [0, 2, 30])
def test_update_passes_an_ordinary_exit_on(update_run, code):
    result, calls = update_run([code])
    assert result.returncode == code
    assert calls == ["mascope prod update --auto"]


@needs_bash
def test_update_installs_the_named_cli_and_runs_again(update_run):
    result, calls = update_run([40, 0])
    assert result.returncode == 0, result.stderr
    assert calls == [
        "mascope prod update --auto",
        "uv tool install mascope-cli==2026.11.2",
        "mascope prod update --auto",
    ]


@needs_bash
def test_update_passes_the_second_runs_exit_on(update_run):
    result, _ = update_run([40, 2])
    assert result.returncode == 2


@needs_bash
def test_update_fails_when_the_new_cli_still_asks_for_itself(update_run):
    """The CLI at the unit's path is not the one `uv tool install` replaced."""
    result, calls = update_run([40, 40])
    assert result.returncode == 2
    assert len(calls) == 3


@needs_bash
def test_update_fails_when_the_install_fails(update_run):
    result, calls = update_run([40], uv_code=1)
    assert result.returncode == 2
    assert calls == [
        "mascope prod update --auto",
        "uv tool install mascope-cli==2026.11.2",
    ]


@needs_bash
def test_update_fails_without_uv(update_run):
    if os.path.exists("/snap/bin/uv") or shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("a uv outside the stub PATH would be found")
    result, calls = update_run([40], uv=False)
    assert result.returncode == 2
    assert calls == ["mascope prod update --auto"]
    assert "uv not found" in result.stderr


@needs_bash
@pytest.mark.parametrize(
    "handover", [None, "", "mascope-cli", "evil==1.0", "mascope-cli==1.0 --index-url x"]
)
def test_update_installs_nothing_but_a_pinned_mascope_cli(update_run, handover):
    result, calls = update_run([40], handover=handover)
    assert result.returncode == 2
    assert calls == ["mascope prod update --auto"]
