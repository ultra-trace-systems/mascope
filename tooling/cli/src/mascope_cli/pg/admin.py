"""
PostgreSQL administration utilities via Docker.

Low-level operations executed inside the PostgreSQL container via `docker exec`.
These functions have no dependency on SQLAlchemy, psycopg2, or application models —
they shell out to PostgreSQL client tools bundled with the container image.

Design constraints:
- Dump/restore operations require the container to have a directory mounted as a
  host bind mount. The default mount point is `/backups`, but this can be
  overridden via `mount` for alternative directories (e.g. `/transfer`
  for cross-server sync staging)
- All functions raise RuntimeError on failure with stderr included — callers decide
  whether to abort or warn.
- No application runtime context is accepted as parameters — callers resolve
  container names, paths, and credentials from config before calling.
"""

import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path


# --- Internal helpers ---


def _docker_exec(
    container: str,
    cmd: list[str],
    stdin_input: str = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """
    Execute a command inside a running Docker container.

    :param container: Container name or ID.
    :type container: str
    :param cmd: Command and arguments to run inside the container.
    :type cmd: list[str]
    :param stdin_input: Optional string to pass to stdin (enables -i flag automatically).
    :type stdin_input: str | None
    :param timeout: Maximum seconds to wait for the command. Default: 600
                    (10 min). Pass a larger value for dump/restore of large
                    databases on slow disks.
    :type timeout: int
    :return: CompletedProcess with stdout, stderr, returncode.
    :raises subprocess.TimeoutExpired: If the command exceeds `timeout`.
    """
    docker_cmd = ["docker", "exec"]
    if stdin_input is not None:
        docker_cmd.append("-i")
    docker_cmd += [container] + cmd

    return subprocess.run(
        docker_cmd,
        input=stdin_input,
        capture_output=True,
        text=True,
        check=False,  # callers handle returncode with context-specific errors
        timeout=timeout,
    )


def _wait_for_dump_in_container(
    container: str,
    container_path: str,
    timeout: int = 60,
) -> bool:
    """
    Poll until a dump file is visible inside the container.

    Docker on Windows uses a virtual filesystem layer (WSL2/VirtioFS)
    that can introduce a short delay between a file appearing on the host
    bind-mount and becoming visible inside the container. This function polls
    via `docker exec test -f` to confirm the file is accessible from the
    container's perspective.

    :param container: PostgreSQL container name.
    :type container: str
    :param container_path: Absolute path to the dump file inside the container
                           (e.g. `/transfer/mascope_tof1_20260325_120000_sync.dump`).
    :type container_path: str
    :param timeout: Maximum seconds to wait before giving up. Default: 60.
    :type timeout: int
    :return: `True` if the file becomes visible within `timeout` seconds,
             `False` otherwise.
    :rtype: bool
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _docker_exec(container, ["test", "-f", container_path])
        if result.returncode == 0:
            return True
        time.sleep(1)
    return False


# --- Exceptions ---


class DatabaseExistsError(Exception):
    """
    Raised when the target database already exists.

    Separated from RuntimeError so CLI callers can handle the
    "overwrite existing database" case with an explicit confirmation
    prompt rather than treating it as an unrecoverable failure.

    :param database: Name of the already-existing target database.
    :type database: str
    """

    def __init__(self, database: str) -> None:
        self.database = database
        super().__init__(f"Target database '{database}' already exists.")


# --- Public API functions ---


def skip_backup_log_level(unattended: bool) -> str:
    """
    Log level for the notice that a script is running without a pre-backup.

    An interactive operator gets WARNING: it prints beside the extra
    confirmation prompt, while there is still a chance to back out. An
    unattended run (``--yes``) gets INFO instead - nobody is at the terminal
    to react, and there the flag is a settled configuration choice rather than
    a moment of risk. The nightly ``mascope-assignment-prune`` unit passes
    ``--yes --skip-backup`` on every firing by design, so warning would mint
    an error-monitoring event per server per night that no one can act on.

    :param unattended: True when the run was started with ``--yes``.
    :return: Loguru level name for the notice.
    :rtype: str
    """
    return "INFO" if unattended else "WARNING"


def pg_dump(
    container: str,
    user: str,
    database: str,
    target_dir: Path,
    mount: str,
    label: str = "",
    timeout: int = 7200,
    compression: str = "",
) -> Path:
    """
    Create a compressed custom-format dump of a PostgreSQL database.

    Uses `pg_dump -Fc` (custom format): compressed, supports parallel restore
    via `pg_restore -j`, and allows selective table restore. Superior to plain
    SQL dumps for operational backups.

    The dump is written to `{mount}/<filename>` inside the container, which
    must be bind-mounted from `target_dir` on the host. The returned path
    points to the file on the host.

    Filename format: `{database}_{timestamp}_{label}.dump`
    or `{database}_{timestamp}.dump` when label is empty.

    :param container: PostgreSQL container name (e.g. `mascope_prod_postgres`).
    :type container: str
    :param user: PostgreSQL user with read access to the database.
    :type user: str
    :param database: Name of the database to dump.
    :type database: str
    :param target_dir: Host directory bind-mounted as `mount` in the container.
                       Created automatically if it does not exist.
    :type target_dir: Path
    :param mount: Mount point inside the container corresponding to `target_dir`
                  on the host (e.g. `/backups` or `/transfer`).
    :type mount: str
    :param label: Optional descriptive label embedded in the filename
                  (e.g. `"pre-migration"`, `"manual"`). Sanitized to
                  alphanumeric + hyphens before use.
    :type label: str
    :param timeout: Maximum seconds to allow pg_dump to run. Default: 7200
                    (2 hours) — dumps of large databases on slow disks can
                    take far longer than the generic 10 min ceiling.
    :type timeout: int
    :param compression: Value passed to `pg_dump --compress`: a level
                        (`"0"`..`"9"`) or method:level (`"zstd:1"`, PG16+).
                        Empty string uses pg_dump's default. `"0"` skips the
                        single-core gzip stage (much faster on large
                        databases) at the cost of a larger dump file.
    :type compression: str
    :raises RuntimeError: If pg_dump exits non-zero, exceeds `timeout`, or the
                          output file is not found on the host after the dump
                          completes.
    :return: Absolute path to the created `.dump` file on the host.
    :rtype: Path
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c == "-" else "-" for c in label).strip(
        "-"
    )
    name_parts = (
        [database, timestamp, safe_label] if safe_label else [database, timestamp]
    )
    filename = "_".join(name_parts) + ".dump"
    container_path = f"{mount}/{filename}"

    dump_args = [
        "pg_dump",
        "--username",
        user,
        "--format",
        "custom",  # -Fc equivalent, explicit for readability
        "--no-password",
    ]
    if compression:
        dump_args += ["--compress", compression]
    dump_args += [
        "--file",
        container_path,
        database,  # positional: database name last
    ]

    try:
        result = _docker_exec(
            container,
            dump_args,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"pg_dump for database '{database}' exceeded the {timeout}s timeout. "
            f"For very large databases, increase the timeout or skip the backup."
        ) from e

    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed for database '{database}'.\n"
            f"stderr: {result.stderr.strip()}"
        )

    host_path = target_dir / filename
    if not host_path.exists():
        raise RuntimeError(
            f"pg_dump reported success but output file not found on host: {host_path}\n"
            f"Verify that '{target_dir}' is correctly bind-mounted as '{mount}' in '{container}'."
        )

    # Files written via `docker exec` are owned by the container's root user.
    # Chown back to the host user so the CLI can delete/prune them without sudo.
    # Only needed on Linux — on Windows scp creates the file directly as the
    # current user so no chown is required.
    if os.name != "nt":
        uid, gid = os.getuid(), os.getgid()
        _docker_exec(
            container,
            ["chown", f"{uid}:{gid}", container_path],
        )

    return host_path


def pg_restore(
    container: str,
    user: str,
    database: str,
    dump_file: Path,
    mount: str,
) -> None:
    """
    Restore a PostgreSQL database from a custom-format dump.

    The target database must already exist and be empty. This function does
    not create or drop the database — use `create_database` / `drop_database`
    before calling this.

    Uses `--no-owner` and `--no-acl` to strip ownership and privilege grants
    from the dump. This makes restores portable across environments regardless
    of role configuration.

    The dump file must reside in the directory bind-mounted as `mount` in
    the container — only `dump_file.name` is used to build the container
    path. Before invoking pg_restore, waits up to 60s for the file to become
    visible inside the container view.

    :param container: PostgreSQL container name (e.g. `mascope_prod_postgres`).
    :type container: str
    :param user: PostgreSQL user with write access to `database`.
    :type user: str
    :param database: Name of the target database to restore into (must be empty).
    :type database: str
    :param dump_file: Host path to the `.dump` file. Must reside in the
                      directory mounted as `mount` in the container.
    :type dump_file: Path
    :param mount: Mount point inside the container corresponding to the directory
                  containing `dump_file` on the host (e.g. `/backups` or `/transfer`).
    :type mount: str
    :raises FileNotFoundError: If `dump_file` does not exist on the host.
    :raises RuntimeError: If pg_restore exits non-zero.
    :rtype: None
    """
    if not dump_file.exists():
        raise FileNotFoundError(f"Dump file not found: {dump_file}")

    container_path = f"{mount}/{dump_file.name}"

    if not _wait_for_dump_in_container(container, container_path):
        raise RuntimeError(
            f"Dump file '{dump_file.name}' is not visible inside container "
            f"'{container}' at '{container_path}' after 60s. "
            f"Verify that the transfer directory is correctly bind-mounted as '{mount}'."
        )

    result = _docker_exec(
        container,
        [
            "pg_restore",
            "--username",
            user,
            "--dbname",
            database,
            "--no-owner",  # strip ownership — portability across envs
            "--no-acl",  # strip GRANT/REVOKE — same reason
            "--exit-on-error",  # fail fast: partial restores are worse than no restore
            "--no-password",
            container_path,
        ],
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"pg_restore failed for database '{database}' from '{dump_file.name}'.\n"
            f"stderr: {result.stderr.strip()}"
        )


def drop_database(
    container: str,
    user: str,
    database: str,
) -> None:
    """
    Terminate all active connections to a database, then drop it.

    PostgreSQL refuses to drop a database with active connections — terminating
    first is mandatory. Uses `pg_terminate_backend` scoped to the target
    database, excluding the current backend process.

    :param container: PostgreSQL container name.
    :type container: str
    :param user: PostgreSQL superuser or owner of `database`.
    :type user: str
    :param database: Name of the database to drop.
    :type database: str
    :raises RuntimeError: If terminating connections or dropping the database fails.
    :rtype: None
    """
    terminate_sql = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname = '{database}' AND pid <> pg_backend_pid();"
    )
    result = _docker_exec(
        container,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",  # connect to default admin db
            "--no-password",
            "--command",
            terminate_sql,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to terminate connections to '{database}'.\n"
            f"stderr: {result.stderr.strip()}"
        )

    result = _docker_exec(
        container,
        [
            "dropdb",
            "--username",
            user,
            "--no-password",
            "--if-exists",
            database,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dropdb failed for database '{database}'.\nstderr: {result.stderr.strip()}"
        )


def create_database(
    container: str,
    user: str,
    database: str,
) -> None:
    """
    Create an empty PostgreSQL database inside the container.

    Intended for use in production context where the PostgreSQL port is not
    exposed to the host, making direct psycopg2 connections impossible.
    For development, prefer the psycopg2-based `create_database()` in
    `cmd/dev/postgres.py` which provides idempotency checking.

    This function is NOT idempotent — it will raise if the database already
    exists. Check existence before calling if needed.

    :param container: PostgreSQL container name.
    :type container: str
    :param user: PostgreSQL superuser or user with CREATEDB privilege.
    :type user: str
    :param database: Name of the database to create.
    :type database: str
    :raises RuntimeError: If createdb exits non-zero (including if database exists).
    """
    result = _docker_exec(
        container,
        [
            "createdb",
            "--username",
            user,
            "--no-password",
            database,
        ],
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"createdb failed for database '{database}'.\n"
            f"stderr: {result.stderr.strip()}"
        )


def clone_database(
    container: str,
    user: str,
    source_db: str,
    target_db: str,
) -> None:
    """
    Clone a PostgreSQL database server-side.

    This is a block-level copy performed entirely inside PostgreSQL — no data
    is serialized or leaves the server. Orders of magnitude faster than
    dump + restore for large databases.

    Checks before cloning:
    1. Source database exists — raises `RuntimeError` if not.
    2. Target database does not exist — raises `DatabaseExistsError`
       if it does, allowing the caller to prompt for confirmation and drop it
       before retrying.
    3. No active connections to source — PostgreSQL enforces this at the SQL
       level, but we check early to provide actionable error message.

    :param container: PostgreSQL container name.
    :type container: str
    :param user: PostgreSQL superuser or user with CREATEDB privilege.
    :type user: str
    :param source_db: Name of the existing database to use as template.
    :type source_db: str
    :param target_db: Name of the new database to create.
    :type target_db: str
    :raises RuntimeError: If the source database does not exist, active
                          connections to `source_db` prevent cloning,
                          or the SQL statement fails for any other reason.
    :raises DatabaseExistsError: If `target_db` already exists on the server.
    :rtype: None
    """
    # --- Verify source database exists ---
    exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{source_db}';"
    result = _docker_exec(
        container,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",
            "--no-password",
            "--tuples-only",
            "--command",
            exists_sql,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to query pg_database for '{source_db}'.\n"
            f"stderr: {result.stderr.strip()}"
        )
    if "1" not in result.stdout:
        raise RuntimeError(f"Source database '{source_db}' does not exist.")

    # --- Check target db does not already exist ---
    target_exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{target_db}';"
    result = _docker_exec(
        container,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",
            "--no-password",
            "--tuples-only",
            "--command",
            target_exists_sql,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to query pg_database for target '{target_db}'.\n"
            f"stderr: {result.stderr.strip()}"
        )
    if "1" in result.stdout:
        raise DatabaseExistsError(target_db)

    # --- Check for active connections ---
    check_sql = (
        "SELECT count(*) FROM pg_stat_activity "
        f"WHERE datname = '{source_db}' AND pid <> pg_backend_pid();"
    )
    result = _docker_exec(
        container,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",
            "--no-password",
            "--tuples-only",  # suppress headers — we only want the count value
            "--command",
            check_sql,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to check active connections on '{source_db}'.\n"
            f"stderr: {result.stderr.strip()}"
        )

    count = int(result.stdout.strip() or "0")
    if count > 0:
        raise RuntimeError(
            f"Cannot clone '{source_db}': {count} active connection(s) exist.\n"
            f"Disconnect all clients from '{source_db}' before cloning."
        )

    # --- Clone target database ---
    clone_sql = f'CREATE DATABASE "{target_db}" TEMPLATE "{source_db}";'
    result = _docker_exec(
        container,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            "postgres",
            "--no-password",
            "--command",
            clone_sql,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to clone '{source_db}' → '{target_db}'.\n"
            f"stderr: {result.stderr.strip()}"
        )


def list_dumps(
    target_dir: Path,
    db_name_filter: str = None,
) -> list[Path]:
    """
    List `.dump` files in a backup directory, sorted newest first.

    When `db_name_filter` is provided, only files belonging to that exact
    database are returned. Matching is anchored to the timestamp separator
    (`_YYYYMMDD_HHMMSS`)

    Expected filename formats (produced by :func:`pg_dump`)::
        {database}_{YYYYMMDD}_{HHMMSS}.dump
        {database}_{YYYYMMDD}_{HHMMSS}_{label}.dump

    :param target_dir: Directory to search for dump files.
    :type target_dir: Path
    :param db_name_filter: If provided, only return files whose database name
                           matches this string exactly
    :type db_name_filter: str | None
    :return: List of `.dump` file paths, newest first. Empty list if
             directory does not exist or contains no matching files.
    :rtype: list[Path]
    """
    if not target_dir.exists():
        return []

    files = sorted(
        target_dir.glob("*.dump"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if db_name_filter:
        pattern = re.compile(rf"^{re.escape(db_name_filter)}_\d{{8}}_\d{{6}}[_.]")
        files = [f for f in files if pattern.match(f.name)]

    return files


def purge_old_dumps(
    target_dir: Path,
    db_name: str,
    retention_days: int,
) -> list[Path]:
    """
    Delete dump files older than `retention_days` for a given database.

    Only files matching `db_name` prefix are considered — other databases'
    dumps in the same directory are not touched.

    Works for both `backups/` and `transfer/` directories.

    :param target_dir: Directory containing dump files.
    :type target_dir: Path
    :param db_name: Database name prefix to filter files for purging.
    :type db_name: str
    :param retention_days: Files older than this many days are deleted.
    :type retention_days: int
    :return: List of deleted file paths.
    :rtype: list[Path]
    """
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = []

    for f in list_dumps(target_dir, db_name_filter=db_name):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            deleted.append(f)

    return deleted
