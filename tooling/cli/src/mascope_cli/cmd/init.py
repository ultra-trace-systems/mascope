"""
The `mascope init` command: create a Mascope runtime home.

Materializes the config TOML layers and compose files bundled with the
package into a runtime home directory, creates the `.runtime/` skeleton,
and generates the app secrets. After `mascope init`, a machine with Docker
can run `mascope prod up` (or the demo stack) without a source checkout.

This command deliberately never touches the Runtime singleton — it must
work before any environment exists.
"""

import secrets as _secrets
from importlib import resources
from pathlib import Path
from typing import Annotated, Optional

import typer

from mascope_cli.home import default_home


# Config layers + compose files shipped as package data (mascope_cli/data/).
# In the monorepo these mirror the repo-root canonical copies; a CI test
# fails when they drift.
CONFIG_FILES = (
    "base.mascope.toml",
    "dev.mascope.toml",
    "prod.mascope.toml",
    "docker-compose.yaml",
    "docker-compose.demo.yaml",
    "docker-compose.dev.yaml",
)

# Secret files the stack expects under .runtime/secrets/ (see the `secrets:`
# section of docker-compose.yaml). Generated once, never overwritten.
GENERATED_SECRETS = (
    "postgres_password.txt",
    "jwt_secret_key.txt",
    "server_owner_secret_key.txt",
    # Encrypts stored TOTP seeds. Kept separate from the JWT secret rather than
    # derived from it: seeds outlive many signing-key rotations, and a rotation
    # that silently made every enrolled seed undecryptable would lock every user
    # of a deployment out at once.
    "mfa_encryption_key.txt",
)

# Secrets safe to auto-provision on an ALREADY-RUNNING deployment (`mascope prod
# up` / update), as opposed to a fresh `mascope init` which generates all of
# GENERATED_SECRETS. Only secrets introduced after a deployment's initial setup
# belong here: their absence means "this release added a secret", not "an
# existing secret was lost". The critical long-standing secrets are deliberately
# excluded - a missing postgres_password, jwt_secret_key, or
# server_owner_secret_key is an error to surface loudly, not to paper over with
# a fresh random value that would break against the existing database or end
# every session.
AUTO_PROVISIONED_SECRETS = ("mfa_encryption_key.txt",)

# Directories the containers bind-mount from the home. Docker creates missing
# mount sources as root, so pre-create them owned by the invoking user.
RUNTIME_DIRS = (
    ".runtime/env/default/filestore",
    ".runtime/env/default/logs",
    ".runtime/secrets",
    ".runtime/database/prod",
    ".runtime/database/backups/prod",
    ".runtime/database/transfer",
)


def _write_config_files(home: Path, force: bool) -> list[str]:
    """Copy bundled config/compose files into the home; returns files written."""
    written = []
    data_root = resources.files("mascope_cli") / "data"
    for name in CONFIG_FILES:
        target = home / name
        if target.exists() and not force:
            continue
        target.write_bytes((data_root / name).read_bytes())
        written.append(name)
    return written


def _write_secrets(home: Path) -> list[str]:
    """Generate missing app secrets; existing secrets are never touched."""
    written = []
    secrets_dir = home / ".runtime" / "secrets"
    for name in GENERATED_SECRETS:
        target = secrets_dir / name
        if target.exists():
            continue
        target.write_text(_secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        written.append(name)
    return written


def init(
    path: Annotated[
        Optional[Path],
        typer.Option(
            "--path",
            help="Directory to initialize. Defaults to MASCOPE_PATH if set, "
            "otherwise the platform default home.",
            envvar="MASCOPE_PATH",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite existing config and compose files with the bundled "
            "versions. Secrets are never overwritten.",
        ),
    ] = False,
) -> None:
    """
    Create (or update) a Mascope runtime home.

    Sets up everything a deployment needs next to the application source or
    on a machine without one: the config TOML layers, the docker compose
    files, the .runtime directory skeleton, and generated app secrets.
    Safe to re-run — existing files are kept unless --force is passed, and
    secrets are never regenerated.

    \b
    Typical first-time flow on a fresh machine:
        mascope init
        mascope cert gen       # or install real TLS certs into .runtime/secrets/
        mascope prod up
    """
    home = (path or default_home()).resolve()

    home.mkdir(parents=True, exist_ok=True)
    for rel in RUNTIME_DIRS:
        (home / Path(*rel.split("/"))).mkdir(parents=True, exist_ok=True)

    config_written = _write_config_files(home, force)
    secrets_written = _write_secrets(home)

    typer.echo(f"Initialized Mascope runtime home: {home}")
    for name in config_written:
        typer.echo(f"  wrote {name}")
    for name in secrets_written:
        typer.echo(f"  generated .runtime/secrets/{name}")
    if not config_written and not secrets_written:
        typer.echo("  everything already in place (use --force to refresh config)")

    if home != default_home().resolve():
        typer.echo(
            "\nThis is not the default home - set MASCOPE_PATH for other commands:"
            f'\n  MASCOPE_PATH="{home}"'
        )

    ssl_pem = home / ".runtime" / "secrets" / "mascope.app.pem"
    if not ssl_pem.exists():
        typer.echo(
            "\nNext steps:"
            "\n  mascope cert gen   # self-signed TLS cert (or install real"
            " certs as .runtime/secrets/mascope.app.pem/.key)"
            "\n  mascope prod up    # start the production stack"
        )
