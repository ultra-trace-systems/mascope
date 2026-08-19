"""
Two-factor recovery from the host.

The escape hatch for the case nothing inside the application can reach: the
only account that could clear someone else's factor has lost its own
authenticator and its recovery codes. Deployments are self-hosted and there is
no support desk, so the last resort has to be a shell on the machine running
the database.

Clearing a factor is not a way in. The account's password is untouched and
still required; all this does is stop the second step being demanded, so the
holder can enrol a new authenticator.

Operates through `docker exec` on the postgres container, like every other
production database command here: the container publishes no host port.
"""

import subprocess
from typing import Annotated, Optional

import typer

from mascope_cli.pg.utils import (
    is_container_running,
    is_database_ready,
    validate_env,
)
from mascope_cli.runtime import runtime


mfa_app = typer.Typer(
    name="mfa",
    help="Recover accounts locked out of two-factor authentication.",
    no_args_is_help=True,
)

_MODE = "prod"


def _psql(sql: str, env: str) -> str:
    """
    Run one statement in the production database and return its output.

    :param sql: Statement to execute.
    :param env: Runtime environment whose database to target.
    :raises typer.Exit: With code 1 if psql fails.
    :return: Trimmed stdout.
    """
    db_cfg = runtime.full_config.backend.database
    result = subprocess.run(
        [
            "docker",
            "exec",
            db_cfg.get_postgres_container_name(mode=_MODE),
            "psql",
            "-U",
            db_cfg.user,
            "-d",
            db_cfg.get_postgres_database_name(env),
            "-tA",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        runtime.logger.error(f"Database command failed: {result.stderr.strip()}")
        raise typer.Exit(code=1)
    return result.stdout.strip()


def _resolve_env(env: Optional[str]) -> str:
    """
    Default to the active environment and refuse an unknown one.

    The ``prod db`` commands resolve ``--env`` this way; the recovery commands
    here must too, or on a deployment running a non-default environment the
    escape hatch would silently target ``mascope_default`` - the wrong database.

    :param env: The value passed on the command line, or ``None``.
    :raises typer.Exit: With code 1 when the environment is not known.
    :return: The resolved environment name.
    """
    resolved = env or runtime.env.name
    if not validate_env(resolved):
        runtime.logger.error(
            f"Environment '{resolved}' not found. "
            f"Available: {', '.join(e['name'] for e in runtime.env.list)}"
        )
        raise typer.Exit(code=1)
    return resolved


def _require_running(env: str) -> None:
    """
    Refuse before touching anything if the database is not reachable.

    :param env: Runtime environment whose database to target.
    :raises typer.Exit: With code 1 when the stack or database is not up.
    """
    if not is_container_running(_MODE):
        runtime.logger.error(
            "The PostgreSQL container is not running. "
            "Start the stack with `mascope prod up` first."
        )
        raise typer.Exit(code=1)
    if not is_database_ready(mode=_MODE, env=env):
        runtime.logger.error(
            f"No database for environment '{env}'. Check `mascope prod db status`."
        )
        raise typer.Exit(code=1)


def _quote(value: str) -> str:
    """
    SQL string literal for an identifier supplied on the command line.

    The email is doubled-quote escaped rather than interpolated raw: it arrives
    from a shell, and a stray apostrophe would otherwise break the statement or
    change what it does.

    :param value: Raw value from the command line.
    :return: A quoted SQL literal.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


@mfa_app.command("status")
def mfa_status(
    env: Annotated[
        Optional[str],
        typer.Option(
            "--env", help="Runtime environment to inspect. Defaults to active."
        ),
    ] = None,
) -> None:
    """
    List which accounts hold a second factor.

    \b
    Example:
        mascope prod mfa status
    """
    env = _resolve_env(env)
    _require_running(env)
    rows = _psql(
        "SELECT email, mfa_enabled, "
        "(SELECT count(*) FROM user_recovery_code r "
        " WHERE r.user_id = u.id AND r.used_at IS NULL) "
        'FROM "user" u ORDER BY email;',
        env,
    )
    if not rows:
        runtime.logger.info("No user accounts found.")
        return
    runtime.logger.info("email | two-factor | unused recovery codes")
    for line in rows.splitlines():
        email, enabled, codes = line.split("|")
        state = "on" if enabled == "t" else "off"
        runtime.logger.info(f"{email} | {state} | {codes}")


@mfa_app.command("reset")
def mfa_reset(
    email: Annotated[str, typer.Argument(help="Email address of the account.")],
    env: Annotated[
        Optional[str],
        typer.Option(
            "--env", help="Runtime environment to act on. Defaults to active."
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt.")
    ] = False,
) -> None:
    """
    Clear an account's second factor so its holder can enrol again.

    Use when an authenticator is lost together with its recovery codes and no
    administrator or owner who could reset it in the application can sign in.
    The account's password is unchanged and still required.

    \b
    Example:
        mascope prod mfa reset someone@example.org
    """
    env = _resolve_env(env)
    _require_running(env)

    literal = _quote(email)
    # Case-insensitive, matching how the app authenticates emails (fastapi-users
    # compares LOWER(email)): the escape hatch must find the same account the
    # login form does, whatever the capitalization.
    found = _psql(
        f'SELECT id, mfa_enabled FROM "user" WHERE LOWER(email) = LOWER({literal});',
        env,
    )
    if not found:
        runtime.logger.error(f"No account with email '{email}' in environment '{env}'.")
        raise typer.Exit(code=1)

    user_id, enabled = found.split("|")
    if enabled != "t":
        runtime.logger.info(
            f"'{email}' does not have two-factor authentication enabled. Nothing to do."
        )
        return

    if not yes:
        typer.confirm(
            f"Clear two-factor authentication for '{email}' in environment "
            f"'{env}'? Their password is unchanged.",
            abort=True,
        )

    # Both halves in one statement, so a failure between them cannot leave the
    # account with recovery codes for a factor it no longer has.
    _psql(
        "BEGIN; "
        f"DELETE FROM user_recovery_code WHERE user_id = {int(user_id)}; "
        'UPDATE "user" SET mfa_secret = NULL, mfa_enabled = false, '
        "mfa_confirmed_at = NULL, mfa_last_timestep = NULL "
        f"WHERE id = {int(user_id)}; COMMIT;",
        env,
    )
    runtime.logger.success(
        f"Cleared two-factor authentication for '{email}'. "
        "They sign in with their password, and can enrol a new authenticator "
        "from their account settings."
    )
    runtime.logger.warning(
        "Open sessions are not ended by this. Restart the backend if you need "
        "them closed."
    )
