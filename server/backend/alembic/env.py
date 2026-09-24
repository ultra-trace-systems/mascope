"""Alembic migration environment - Mascope database migrations

Configures Alembic for PostgreSQL schema management using Mascope runtime config.

URL resolution: honors `sqlalchemy.url` set on the Alembic Config object
when present (test fixtures, --x args), and falls back to the URL derived
from the active runtime env. The override is required so that the stairway
test (and any other programmatic Alembic invocation) can target a dedicated
ephemeral test database without depending on whichever Mascope env happens
to be active.

Logging: `alembic.ini`'s logging sections apply only when Alembic runs from
its own command line, which is how the db-init container and `mascope dev
migrate` run it. A program that runs Alembic in-process - the migration
tests - owns its process's logging, and `fileConfig` would rewrite it: it
replaces the root handlers (the runtime's bridge from stdlib logging into
loguru among them), raises the root level to WARNING, and by default disables
every logger that already exists. In the backend test session that would
leave the log assertions of every later test blind to stdlib loggers: a test
that a record arrives would fail, and a test that no WARNING arrives would
pass whatever the code logged. On the command line the backend and its
libraries are imported before `fileConfig` runs, so their loggers are kept
enabled.
"""

from logging.config import fileConfig
from typing import cast

from alembic import context
from sqlalchemy import engine_from_config, pool

from mascope_backend.db.models import Base
from mascope_backend.db.secrets import postgres_password
from mascope_backend.runtime import runtime
from mascope_runtime.config import BackendConfig


# --- Module-level configuration ---
config = context.config  # Alembic config from alembic.ini
db_cfg = cast(BackendConfig, runtime.config).database  # Mascope database config
target_metadata = Base.metadata  # SQLAlchemy models metadata ('autogenerate' support)


def _started_from_command_line() -> bool:
    """Whether Alembic was started from its own command line.

    Its argument parser records the chosen command on `cmd_opts` as `cmd`, and
    nothing else does. `cmd_opts` alone does not tell the two apart: an
    in-process caller sets it as well to pass `-x` arguments, which
    `EnvironmentContext.get_x_argument` reads from there.

    :return: True for the `alembic` command, False for an in-process caller
    :rtype: bool
    """
    return getattr(config.cmd_opts, "cmd", None) is not None


# Interpret the config file for Python logging - see the module docstring.
if config.config_file_name is not None and _started_from_command_line():
    fileConfig(config.config_file_name, disable_existing_loggers=False)


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _resolve_url() -> str:
    """Resolve the PostgreSQL URL Alembic will run against.

    Honors `sqlalchemy.url` set on the Alembic Config first — this is how
    tests (and any other programmatic invocation) can override the target
    database without touching `runtime.env`. Falls back to the runtime-derived
    URL when nothing is set on the Config, which preserves the behavior
    used by the CLI (`mascope dev migrate upgrade`) and `db-init.sh`.

    :return: PostgreSQL sync connection URL
    :rtype: str
    """
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return db_cfg.get_postgres_url_sync(
        password=postgres_password, env_name=runtime.env.name
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL output to stdout).

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    Generates SQL migration scripts without executing them.
    Useful for review or manual execution on production databases.
    """
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (execute against database).

    Creates engine and executes migrations against live database.
    This is the standard mode for applying schema changes.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _resolve_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
