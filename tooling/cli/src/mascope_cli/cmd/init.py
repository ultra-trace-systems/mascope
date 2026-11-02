"""
The `mascope init` command: create a Mascope runtime home.

Materializes the config TOML layers and compose files bundled with the
package into a runtime home directory, creates the `.runtime/` skeleton,
and generates the app secrets. After `mascope init`, a machine with Docker
can run `mascope prod up` (or the demo stack) without a source checkout.

This command deliberately never touches the Runtime singleton — it must
work before any environment exists.
"""

import datetime
import json
import os
import secrets as _secrets
import shutil
import uuid
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Optional

import typer

from mascope_cli.home import default_home
from mascope_runtime.atomic import replace_with_retry, write_json


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

# Secret files the operator supplies that compose still needs present to start,
# so both `mascope init` and every compose "up" create them EMPTY when absent.
# Empty means "not set up": nothing reads the file until the operator fills it
# and turns its feature on, and the frontend refuses to start with the feature
# on and the file still empty. Existing files are never touched.
#   origin_pull_ca.pem - the CA of the CDN's origin-pull certificate for the
#                        zone, read with MASCOPE_ORIGIN_PULL=on
#                        (docs/maintaining.md, "The edge").
#   turnstile_secret_key.txt - the secret key of the site's Turnstile widget,
#                        read with MASCOPE_TURNSTILE_SITE_KEY set; the backend
#                        refuses every sign-in with the key set and this empty.
PLACEHOLDER_SECRETS = ("origin_pull_ca.pem", "turnstile_secret_key.txt")

# The placeholders that hold a secret once the operator fills them in. They are
# created owner-only (0600), as create_secret writes the generated ones: the
# operator writes into the file, which keeps its mode. The CA holds nothing
# secret and is left at the umask.
PRIVATE_PLACEHOLDER_SECRETS = ("turnstile_secret_key.txt",)

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


# Under the home: which CLI version last brought the CONFIG_FILES to its own
# copies (`refresh_config_files`), and where the copies it replaced are kept.
REFRESH_STAMP = Path(".runtime") / "config-files.json"
REPLACED_DIR = Path(".runtime") / "replaced"


def _bundled(name: str) -> bytes:
    """The bytes of the bundled config or compose file ``name``."""
    return (resources.files("mascope_cli") / "data" / name).read_bytes()


def _write_config_files(home: Path, force: bool) -> list[str]:
    """Copy bundled config/compose files into the home; returns files written."""
    written = []
    for name in CONFIG_FILES:
        target = home / name
        if target.exists() and not force:
            continue
        target.write_bytes(_bundled(name))
        written.append(name)
    return written


@dataclass
class Refresh:
    """What `refresh_config_files` changed in a home."""

    # The CONFIG_FILES rewritten with this CLI's copies, in CONFIG_FILES order.
    written: list[str]
    # Where the copies they replaced were kept; None when none was replaced
    # (every written file was missing).
    replaced_dir: Optional[Path]


def _refreshed_by(home: Path) -> Optional[str]:
    """The CLI version the stamp names, or None (no stamp, or unreadable)."""
    try:
        data = json.loads((home / REFRESH_STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("cli_version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def _replace_bytes(target: Path, content: bytes) -> None:
    """Write ``content`` over ``target`` by a rename, so it is never half-written."""
    temp = target.with_name(f".tmp.{target.name}.{uuid.uuid4().hex}")
    try:
        temp.write_bytes(content)
        replace_with_retry(str(temp), str(target))
    finally:
        temp.unlink(missing_ok=True)


def refresh_config_files(home: Path, cli_version: str) -> Optional[Refresh]:
    """
    Bring a wheel install's config layers and compose files to this CLI's.

    A wheel install deploys only the release its CLI was cut with, and that
    release's compose file and config layers are the copies the CLI bundles:
    after the CLI is upgraded, the home's copies are the previous release's.
    This rewrites each of ``CONFIG_FILES`` that differs from the bundled copy,
    once per CLI version (the stamp under ``.runtime/`` records which), so a
    hand edit made after the refresh lasts until the next upgrade. What it
    replaces is copied first into ``.runtime/replaced/<when>/``.

    Never touched: anything under ``.runtime/``, so neither the env overlay
    (``.runtime/env/<env>/<mode>.mascope.toml``), where operator settings
    belong, nor the secrets.

    :param home: The runtime home.
    :param cli_version: The running CLI's version, for the stamp.
    :return: What was rewritten, or None when this CLI version already
        refreshed the home or nothing differed.
    :raises OSError: A copy or a write failed. Files already rewritten stay
        rewritten and their old copies kept; the stamp is not written, so
        the next run tries again.
    """
    if _refreshed_by(home) == cli_version:
        return None
    stale = [
        name
        for name in CONFIG_FILES
        if not (home / name).is_file() or (home / name).read_bytes() != _bundled(name)
    ]
    replaced_dir = None
    present = [name for name in stale if (home / name).is_file()]
    if present:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        replaced_dir = home / REPLACED_DIR / f"{stamp}-{uuid.uuid4().hex[:8]}"
        replaced_dir.mkdir(parents=True)
        for name in present:
            shutil.copy2(home / name, replaced_dir / name)
    for name in stale:
        _replace_bytes(home / name, _bundled(name))
    (home / REFRESH_STAMP).parent.mkdir(parents=True, exist_ok=True)
    write_json(
        str(home / REFRESH_STAMP),
        {
            "cli_version": cli_version,
            "refreshed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
        },
        indent=2,
    )
    return Refresh(written=stale, replaced_dir=replaced_dir) if stale else None


def create_secret(path: Path) -> None:
    """
    Write a new random secret to `path`, readable by its owner alone (0600).

    Compose mounts a file secret into each container as the file is on the
    host, mode included, so a secret written at the usual umask (0644) could
    be read by every user on the server and by every uid in the containers it
    is mounted into, not just by the app. The file is created 0600 in the
    same call that creates it, so it is never open even for a moment, and
    exclusively: an existing file raises rather than being overwritten.

    :param path: The secret file to create; it must not exist.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(_secrets.token_urlsafe(48) + "\n")


def create_placeholder(path: Path) -> None:
    """
    Create an operator-supplied secret file empty, unless it exists.

    Owner-only (0600) for a placeholder in ``PRIVATE_PLACEHOLDER_SECRETS``, so
    the secret the operator writes into it later is never readable by others;
    at the umask otherwise.

    :param path: The placeholder file. An existing file is left as it is.
    """
    if path.exists():
        return
    if path.name in PRIVATE_PLACEHOLDER_SECRETS:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    else:
        path.touch()


def _write_secrets(home: Path) -> list[str]:
    """Generate missing app secrets; existing secrets are never touched."""
    written = []
    secrets_dir = home / ".runtime" / "secrets"
    for name in GENERATED_SECRETS:
        target = secrets_dir / name
        if target.exists():
            continue
        create_secret(target)
        written.append(name)
    for name in PLACEHOLDER_SECRETS:
        create_placeholder(secrets_dir / name)
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
