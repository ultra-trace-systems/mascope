"""
Tests for `mascope init` — runtime home creation.

The command materializes bundled config/compose files, creates the
`.runtime/` skeleton, and generates secrets. It must be safe to re-run
(idempotent), must never regenerate secrets, and must work without any
pre-existing environment.
"""

from mascope_cli.cmd.init import (
    CONFIG_FILES,
    GENERATED_SECRETS,
    PLACEHOLDER_SECRETS,
    RUNTIME_DIRS,
)
from mascope_cli.main import app


def _init(cli_runner, home, *args):
    return cli_runner.invoke(app, ["init", "--path", str(home), *args])


def test_init_creates_a_complete_home(cli_runner, tmp_path):
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    for name in CONFIG_FILES:
        assert (home / name).is_file(), f"missing config file {name}"
    for rel in RUNTIME_DIRS:
        assert (home / rel).is_dir(), f"missing directory {rel}"
    for name in GENERATED_SECRETS:
        secret = (home / ".runtime" / "secrets" / name).read_text().strip()
        assert len(secret) >= 32, f"secret {name} looks too short"


def test_init_creates_the_operator_supplied_ca_empty(cli_runner, tmp_path):
    # Compose refuses to start on a declared secret whose file is missing, and
    # the origin-pull CA is the operator's to bring: an empty file lets a site
    # without origin pulls start, and reads as "not set up" to the frontend.
    home = tmp_path / "home"

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    assert PLACEHOLDER_SECRETS == ("origin_pull_ca.pem",)
    ca = home / ".runtime" / "secrets" / "origin_pull_ca.pem"
    assert ca.is_file()
    assert ca.read_bytes() == b""


def test_init_keeps_an_operator_supplied_ca(cli_runner, tmp_path):
    home = tmp_path / "home"
    ca = home / ".runtime" / "secrets" / "origin_pull_ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")

    result = _init(cli_runner, home, "--force")

    assert result.exit_code == 0
    assert ca.read_bytes() == b"-----BEGIN CERTIFICATE-----\n"


def test_init_generates_unique_secrets(cli_runner, tmp_path):
    _init(cli_runner, tmp_path / "a")
    _init(cli_runner, tmp_path / "b")

    a = (tmp_path / "a" / ".runtime" / "secrets" / "jwt_secret_key.txt").read_text()
    b = (tmp_path / "b" / ".runtime" / "secrets" / "jwt_secret_key.txt").read_text()
    assert a != b


def test_init_keeps_existing_files(cli_runner, tmp_path):
    home = tmp_path / "home"
    _init(cli_runner, home)

    config = home / "base.mascope.toml"
    secret = home / ".runtime" / "secrets" / "postgres_password.txt"
    config.write_text("# operator override\n", encoding="utf-8")
    original_secret = secret.read_text()

    result = _init(cli_runner, home)

    assert result.exit_code == 0
    assert config.read_text(encoding="utf-8") == "# operator override\n"
    assert secret.read_text() == original_secret


def test_init_force_refreshes_config_but_never_secrets(cli_runner, tmp_path):
    home = tmp_path / "home"
    _init(cli_runner, home)

    config = home / "base.mascope.toml"
    secret = home / ".runtime" / "secrets" / "postgres_password.txt"
    config.write_text("# operator override\n", encoding="utf-8")
    original_secret = secret.read_text()

    result = _init(cli_runner, home, "--force")

    assert result.exit_code == 0
    assert "[meta]" in config.read_text(encoding="utf-8")  # bundled content is back
    assert secret.read_text() == original_secret


def test_init_defaults_to_platform_home(cli_runner, tmp_path, monkeypatch):
    monkeypatch.delenv("MASCOPE_PATH", raising=False)
    default = tmp_path / "default-home"
    monkeypatch.setattr("mascope_cli.cmd.init.default_home", lambda: default)

    result = cli_runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (default / "base.mascope.toml").is_file()


def test_init_hints_when_not_the_default_home(cli_runner, tmp_path):
    result = _init(cli_runner, tmp_path / "custom")

    assert result.exit_code == 0
    assert "MASCOPE_PATH" in result.output
