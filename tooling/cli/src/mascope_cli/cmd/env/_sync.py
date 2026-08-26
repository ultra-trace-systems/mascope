"""
Internal implementation for `mascope env sync`.

Handles filestore sync via rsync and database sync via pg_dump/pg_restore
across local and remote machines. Not a Typer module — contains no commands.

Callers (`main.py`) are responsible for argument parsing, orchestration,
and opening the SSH ControlMaster connection before calling these functions.

Transfer flow for database sync:
- local → local:  pg_dump source container → transfer/ → pg_restore target container
- remote → local: SSH `mascope {mode} db backup create --transfer` on remote →
                  scp remote transfer/ → local pg_restore from transfer/
- local → remote: local pg_dump → transfer/ → scp → SSH `mascope {mode} db restore --transfer`

Transfer dump lifecycle:
- On success: the specific dump is deleted, then 7-day retention pruning runs.
- On failure: the dump is kept for manual recovery.

Filestore sync:
- rsync transfers the whole env directory. A `from_date`/`to_date` window
  narrows it to the matching acquisition-date directories inside the
  filestore, via an rsync filter file built by `_filter.py`.
- rsync carries neither modes nor ownership across on its own, so modes are
  forced to `D755,F644` and the resulting ownership is checked afterwards by
  `_ownership.py`.

Windows note:
- All SSH invocations wrap the remote command in single quotes to prevent
  PowerShell from splitting multi-word arguments before they reach remote bash.
- rsync and scp use Cygwin binaries (C://cygwin64//bin//) on Windows.
"""

import datetime
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from mascope_cli.cmd import lib
from mascope_cli.cmd.env._filter import (
    build_filter_rules,
    select_date_dirs,
    source_date_dir_names,
)
from mascope_cli.cmd.env._ownership import check_after_sync
from mascope_cli.cmd.env._paths import (
    get_remote_mascope_path,
    parse_address,
)
from mascope_cli.cmd.env._ssh import cygwin_bin, get_identity_args
from mascope_cli.pg import (
    dirs,
    drop_database,
    is_database_ready,
    is_server_ready,
    pg_dump,
    pg_restore,
    purge_old_dumps,
)
from mascope_cli.pg.admin import create_database as admin_create_database
from mascope_cli.runtime import runtime


def _scp(
    args: list[str],
    control_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """
    Run an scp command, using Cygwin scp on Windows.

    :param args: scp arguments (source, destination, flags).
    :type args: list[str]
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: Completed process result.
    :rtype: subprocess.CompletedProcess
    """
    return subprocess.run(
        [cygwin_bin("scp")] + get_identity_args() + (control_args or []) + args,
        check=False,
    )


# --- Filestore sync ---


def _on_windows() -> bool:
    """
    Whether the local machine is Windows.

    Wrapped in a function so tests can drive both the Cygwin and the POSIX
    branch of the rsync command construction.

    :return: `True` on Windows.
    :rtype: bool
    """
    return os.name == "nt"


def _to_cygwin_path(path: str) -> str:
    """
    Convert a Windows absolute path to its Cygwin `/cygdrive/` equivalent.

    :param path: Windows path (e.g. `C:\\Users\\foo`).
    :type path: str
    :return: Cygwin-compatible path (e.g. `/cygdrive/c/Users/foo`).
    :rtype: str
    """
    cygwin = re.sub(r"^([A-Z]):\\", lambda m: f"/cygdrive/{m.group(1).lower()}/", path)
    cygwin = cygwin.replace("\\", "/")
    runtime.logger.info(f"Converting path {path} to Cygwin format: {cygwin}")
    return cygwin


def _resolve_rsync_path(
    remote: str | None,
    env_name: str,
    control_args: list[str] | None = None,
) -> str:
    """
    Resolve a sync address to an rsync-compatible path.

    For remote addresses, queries `MASCOPE_PATH` via `mascope path` over
    SSH to construct the full env path without hard-coding it locally.

    :param remote: Remote identifier (`USER@HOST`) or `None` for local.
    :type remote: str | None
    :param env_name: Name of the runtime environment.
    :type env_name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: rsync-compatible path string with trailing slash.
    :rtype: str
    """
    if remote is not None:
        mascope_path = get_remote_mascope_path(remote, control_args)
        return f"{remote}:{mascope_path}/.runtime/env/{env_name}/"
    return runtime.path(".runtime", "env", f"{env_name}/")


def _write_filter_file(
    source_remote: str | None,
    source_env: str,
    from_date: datetime.date | None,
    to_date: datetime.date | None,
    control_args: list[str] | None = None,
) -> Path:
    """
    Write the rsync filter rules for a date-filtered filestore transfer.

    A merge file rather than repeated `--filter=` arguments: a multi-year
    range produces hundreds of rules, and the joined command line is capped at
    32767 characters on Windows. A merge file is O(1) in command-line length.

    :param source_remote: Source remote identifier (`USER@HOST`) or `None`.
    :type source_remote: str | None
    :param source_env: Name of the source runtime environment.
    :type source_env: str
    :param from_date: Earliest acquisition date to sync, or `None`.
    :type from_date: datetime.date | None
    :param to_date: Latest acquisition date to sync, or `None`.
    :type to_date: datetime.date | None
    :param control_args: SSH multiplexing flags from `SshMux`.
    :type control_args: list[str] | None
    :return: Path to the rules file. Its parent directory is a temp directory
             owned by the caller, who must remove it.
    :rtype: Path
    :raises RuntimeError: If no filestore data falls inside the range.
    """
    window = f"{from_date or 'beginning'} .. {to_date or 'end'}"
    names = source_date_dir_names(source_remote, source_env, control_args)
    selected = select_date_dirs(names, from_date, to_date)
    runtime.logger.info(
        f"Filestore date filter {window}: {len(selected)} of {len(names)} "
        "source directories selected"
    )
    if not selected:
        raise RuntimeError(
            f"No filestore data matches the requested date range ({window}) — "
            "nothing to sync."
        )

    filter_file = Path(tempfile.mkdtemp(prefix="mascope-sync-filter-")) / "rules.txt"
    filter_file.write_text(
        "\n".join(build_filter_rules(selected)) + "\n", encoding="utf-8"
    )
    return filter_file


def sync_filestore(
    source: str,
    target: str,
    control_args: list[str] | None = None,
    from_date: datetime.date | None = None,
    to_date: datetime.date | None = None,
    chown: bool = False,
) -> None:
    """
    Sync the filestore from source to target using rsync.

    Resolves both addresses to rsync paths, applies Cygwin path conversion
    on Windows, and runs the rsync command.

    Modes are forced to `D755,F644` — the same bits the backend writes under
    the default umask. Without `--perms` rsync masks new files by the
    *receiving* login's umask and leaves existing files alone, so the result
    would otherwise vary per host and per invocation.

    Ownership is not carried across (no `-o`/`-g`); `check_after_sync`
    reports, and optionally fixes, a receiving uid that does not match the
    target env directory's owner.

    :param source: Source address (`ENV` or `USER@HOST:ENV`).
    :type source: str
    :param target: Target address (`ENV` or `USER@HOST:ENV`).
    :type target: str
    :param control_args: SSH multiplexing flags from `SshMux` passed to
                         rsync via its `-e` SSH transport. When provided,
                         rsync reuses the existing ControlMaster connection —
                         no additional password prompt. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :param from_date: Only sync filestore data acquired on or after this date.
                      Filters on the `YYYY.MM.DD` acquisition-date directory,
                      not on file modification time. `None` for no lower bound.
    :type from_date: datetime.date | None
    :param to_date: Only sync filestore data acquired on or before this date.
                    `None` for no upper bound.
    :type to_date: datetime.date | None
    :param chown: Attempt to fix a target ownership mismatch via `sudo -n`
                  instead of only reporting it.
    :type chown: bool
    :raises RuntimeError: If rsync exits non-zero, or if a date range was
                          given that matches no filestore data.
    """
    source_remote, source_env = parse_address(source)
    target_remote, target_env = parse_address(target)

    src = _resolve_rsync_path(source_remote, source_env, control_args)
    dest = _resolve_rsync_path(target_remote, target_env, control_args)

    on_windows = _on_windows()
    ssh_bin = cygwin_bin("ssh") if on_windows else "ssh"
    rsync_bin = cygwin_bin("rsync") if on_windows else "rsync"

    if on_windows:
        src = _to_cygwin_path(src)
        dest = _to_cygwin_path(dest)

    identity_opts = " ".join(get_identity_args())
    mux_opts = " ".join(control_args) if control_args else ""
    keepalive_opts = "-o ServerAliveInterval=30 -o ServerAliveCountMax=6"
    ssh_cmd = f"{ssh_bin} {identity_opts} {keepalive_opts} {mux_opts}".strip()

    flags = (
        "--progress --recursive --copy-links --keep-dirlinks --partial "
        "--timeout=60 --perms --chmod=D755,F644"
    )

    filter_file = None
    if from_date is not None or to_date is not None:
        filter_file = _write_filter_file(
            source_remote, source_env, from_date, to_date, control_args
        )
        filter_arg = (
            _to_cygwin_path(str(filter_file)) if on_windows else str(filter_file)
        )
        # `lib.run` splits the command string with posix-mode shlex, which
        # both requires the Cygwin forward-slash form and would eat the
        # backslashes of a raw Windows path.
        flags += " --filter " + shlex.quote(f"merge {filter_arg}")

    cmd = f"{rsync_bin} {flags} -e '{ssh_cmd}' {src} {dest}"

    runtime.logger.info(f"Syncing filestore: {src} → {dest}")
    runtime.logger.info(cmd)
    try:
        result = lib.run(cmd)
    finally:
        if filter_file is not None:
            shutil.rmtree(filter_file.parent, ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError(f"Filestore sync failed (exit {result.returncode})")

    check_after_sync(target_remote, target_env, control_args, chown=chown)


# --- Remote SSH helpers ---


def _ssh_run(
    remote: str,
    cmd: str,
    control_args: list[str] | None = None,
) -> None:
    """
    Execute a command on a remote machine via SSH using a login shell.

    Uses `bash -l -c` so `~/.bashrc` is sourced and `mascope` is
    available on PATH.

    The command string is single-quoted to prevent PowerShell on Windows
    from splitting multi-word arguments before they reach remote bash.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param cmd: Shell command to execute on the remote.
    :type cmd: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :raises RuntimeError: If the SSH command exits non-zero.
    """
    runtime.logger.info(f"SSH {remote}: bash -l -c '{cmd}'")
    result = subprocess.run(
        [cygwin_bin("ssh")]
        + get_identity_args()
        + (control_args or [])
        + [remote, "bash", "-l", "-c", f"'{cmd}'"],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"SSH command failed on {remote} (exit {result.returncode}): {cmd}"
        )


def _remote_transfer_dir(
    remote: str,
    control_args: list[str] | None = None,
) -> str:
    """
    Return the transfer directory path on a remote machine.

    Uses `PurePosixPath` to guarantee forward slashes regardless of local
    OS — the path targets a Linux remote via SSH/scp.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: Absolute POSIX path string on the remote machine.
    :rtype: str
    """
    mascope_path = get_remote_mascope_path(remote, control_args)
    path = runtime.full_config.backend.database.get_transfer_dir(
        mascope_path=mascope_path
    )
    return str(PurePosixPath(path))


# --- Database sync — local operations ---


def _local_transfer_dir() -> Path:
    """
    Resolve the local transfer directory host path from config.

    :return: Absolute host path to `.runtime/database/transfer/`.
    :rtype: Path
    """
    return runtime.full_config.backend.database.get_transfer_dir()


def _dump_local(mode: str, env_name: str) -> Path:
    """
    Create a transfer dump from a local PostgreSQL container.

    Writes the dump to the local transfer directory (bind-mounted as
    `/transfer` in the container). Uses label `"sync"` so the filename
    is identifiable in logs and directory listings.

    :param mode: Mode of the local container to dump from (`"dev"` or `"prod"`).
    :type mode: str
    :param env_name: Environment name whose database to dump.
    :type env_name: str
    :return: Host path to the created `.dump` file.
    :rtype: Path
    :raises RuntimeError: If the server or database is not ready, or dump fails.
    """
    if not is_server_ready(mode):
        raise RuntimeError(
            f"Local {mode} PostgreSQL is not running — run 'mascope {mode} up' first."
        )
    if not is_database_ready(mode, env_name):
        raise RuntimeError(
            f"Database for env '{env_name}' does not exist in local {mode} PostgreSQL."
        )

    db_cfg = runtime.full_config.backend.database
    container = db_cfg.get_postgres_container_name(mode=mode)
    database = db_cfg.get_postgres_database_name(env_name)
    transfer_dir, transfer_mount = dirs(transfer=True, mode=mode)

    runtime.logger.info(
        f"Dumping '{database}' from local {mode} container → transfer/..."
    )
    return pg_dump(
        container, db_cfg.user, database, transfer_dir, transfer_mount, label="sync"
    )


def _restore_local(mode: str, env_name: str, dump_file: Path) -> None:
    """
    Restore a database in a local PostgreSQL container from a transfer dump.

    Drops and recreates the target database before restoring. The dump file
    must reside in the local transfer directory (bind-mounted as `/transfer`).

    :param mode: Mode of the local container to restore into (`"dev"` or `"prod"`).
    :type mode: str
    :param env_name: Environment name whose database to restore into.
    :type env_name: str
    :param dump_file: Host path to the `.dump` file in the local transfer directory.
    :type dump_file: Path
    :raises RuntimeError: If the server is not ready or any step fails.
    :raises FileNotFoundError: If `dump_file` does not exist on the host.
    """
    if not is_server_ready(mode):
        raise RuntimeError(
            f"Local {mode} PostgreSQL is not running — run 'mascope {mode} up' first."
        )

    db_cfg = runtime.full_config.backend.database
    container = db_cfg.get_postgres_container_name(mode=mode)
    database = db_cfg.get_postgres_database_name(env_name)
    _, transfer_mount = dirs(transfer=True, mode=mode)

    runtime.logger.info(f"Dropping '{database}' in local {mode} container...")
    drop_database(container, db_cfg.user, database)

    runtime.logger.info(f"Creating empty '{database}'...")
    admin_create_database(container, db_cfg.user, database)

    runtime.logger.info(f"Restoring '{database}' from '{dump_file.name}'...")
    pg_restore(container, db_cfg.user, database, dump_file, transfer_mount)


# --- Database sync — remote operations ---


def _dump_remote(
    remote: str,
    mode: str,
    env_name: str,
    control_args: list[str] | None = None,
) -> None:
    """
    Trigger a transfer dump on a remote machine via SSH.

    Calls `mascope {mode} db backup create --env ENV --transfer --label sync`
    on the remote, writing the dump into the remote transfer directory.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param mode: Mode on the remote machine (`"dev"` or `"prod"`).
    :type mode: str
    :param env_name: Environment name to dump on the remote.
    :type env_name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :raises RuntimeError: If the SSH command fails.
    """
    cmd = f"mascope {mode} db backup create --env {env_name} --transfer --label sync"
    runtime.logger.info(f"Triggering remote dump on {remote}: {cmd}")
    _ssh_run(remote, cmd, control_args)


def _restore_remote(
    remote: str,
    mode: str,
    env_name: str,
    dump_file: Path,
    control_args: list[str] | None = None,
) -> None:
    """
    Trigger a transfer restore on a remote machine via SSH.

    Calls `mascope {mode} db restore` cmd on the remote server, which
    restores the `env_name` database in the specified `mode` postgres server
    from the pushed to remote server transfer directory `dump_file` .

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param mode: Mode on the remote machine (`"dev"` or `"prod"`).
    :type mode: str
    :param env_name: Environment name to restore into on the remote.
    :type env_name: str
    :param dump_file: Local host path to the dump file that was pushed to
                      the remote transfer directory. Only the filename is
                      used — the remote resolves the full path from its
                      own transfer directory config.
    :type dump_file: Path
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :raises RuntimeError: If the SSH command fails.
    """
    cmd = (
        f"mascope {mode} db restore {dump_file.name} --env {env_name} --transfer --yes"
    )
    runtime.logger.info(f"Triggering remote restore on {remote}: {cmd}")
    _ssh_run(remote, cmd, control_args)


def _scp_pull(
    remote: str,
    db_name: str,
    control_args: list[str] | None = None,
) -> Path:
    """
    Pull the latest transfer dump for `db_name` from a remote machine.

    Lists the remote transfer directory via SSH, finds the newest matching
    file, and copies it to the local transfer directory via scp.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param db_name: Database name prefix used to filter files
                    (e.g. `mascope_tof1`).
    :type db_name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: Local host path to the downloaded `.dump` file.
    :rtype: Path
    :raises RuntimeError: If no matching file is found on the remote or scp fails.
    """
    remote_dir = _remote_transfer_dir(remote, control_args)
    ls_cmd = f"ls -t {remote_dir}/{db_name}_*.dump 2>/dev/null | head -1"

    result = subprocess.run(
        [cygwin_bin("ssh")]
        + get_identity_args()
        + (control_args or [])
        + [remote, "bash", "-l", "-c", f"'{ls_cmd}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    runtime.logger.info(
        f"_scp_pull ls on {remote}: returncode={result.returncode} "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    remote_file = result.stdout.strip()
    if not remote_file:
        raise RuntimeError(
            f"No transfer dump found for '{db_name}' in {remote}:{remote_dir}/"
        )

    filename = Path(remote_file).name
    local_dir = _local_transfer_dir()
    local_dir.mkdir(parents=True, exist_ok=True)
    local_file = local_dir / filename

    local_file_arg = (
        _to_cygwin_path(str(local_file)) if os.name == "nt" else str(local_file)
    )

    runtime.logger.info(f"Pulling {remote}:{remote_file} → {local_file}")
    result = _scp([f"{remote}:{remote_file}", local_file_arg], control_args)
    if result.returncode != 0:
        raise RuntimeError(f"scp failed pulling '{remote_file}' from {remote}")
    return local_file


def _scp_push(
    remote: str,
    dump_file: Path,
    control_args: list[str] | None = None,
) -> None:
    """
    Push a local transfer dump to the remote machine's transfer directory.

    Creates the remote transfer directory if it does not exist, then copies
    the file via scp.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param dump_file: Local host path to the `.dump` file to push.
    :type dump_file: Path
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :raises RuntimeError: If scp fails.
    """
    remote_dir = _remote_transfer_dir(remote, control_args)
    _ssh_run(remote, f"mkdir -p {remote_dir}", control_args)

    dump_file_arg = (
        _to_cygwin_path(str(dump_file)) if os.name == "nt" else str(dump_file)
    )

    runtime.logger.info(f"Pushing {dump_file.name} → {remote}:{remote_dir}/")
    result = _scp(
        [dump_file_arg, f"{remote}:{remote_dir}/{dump_file.name}"], control_args
    )
    if result.returncode != 0:
        raise RuntimeError(f"scp failed pushing '{dump_file.name}' to {remote}")


# --- Transfer cleanup ---


def _cleanup_transfer(dump_file: Path, db_name: str, retention_days: int = 7) -> None:
    """
    Delete a specific transfer dump and prune older ones beyond the retention window.

    Called on successful restore. On failure, callers skip this so the dump
    is preserved for manual recovery.

    Only files whose name starts with `db_name` are considered for pruning —
    other databases' dumps in the shared transfer directory are not touched.

    :param dump_file: The specific dump file to delete immediately.
    :type dump_file: Path
    :param db_name: Database name prefix used to filter files for pruning
                    (e.g. `mascope_tof1`).
    :type db_name: str
    :param retention_days: Delete dumps for `db_name` older than this many
                           days. Default: 7.
    :type retention_days: int
    """
    if dump_file.exists():
        dump_file.unlink()
        runtime.logger.info(f"Deleted transfer dump: {dump_file.name}")

    transfer_dir = _local_transfer_dir()
    deleted = purge_old_dumps(transfer_dir, db_name, retention_days)
    for f in deleted:
        runtime.logger.info(f"Pruned old transfer dump: {f.name}")


# --- Database sync — orchestration ---


def sync_db(
    source: str,
    source_mode: str,
    target: str,
    target_mode: str,
    control_args: list[str] | None = None,
) -> None:
    """
    Sync a PostgreSQL database from source to target.

    Handles three topology cases:

    - local → local:  dump from source container → transfer/ → restore to target container
    - remote → local: SSH dump on remote → scp to local transfer/ → restore to local container
    - local → remote: dump from local container → transfer/ → scp to remote → SSH restore

    Remote → remote is not supported — run the command from one of the machines.

    `control_args` should come from the `SshMux` context opened in
    `main.py` before any remote operations begin. Passing them here
    ensures all SSH/scp calls in the sync reuse the same authenticated
    session — no additional prompts. For local → local sync, no SSH is
    involved and `control_args` is unused.

    On successful restore, the transfer dump is deleted and 7-day retention
    pruning runs for the same database. On failure, the dump is preserved for
    manual recovery.

    :param source: Source address (`ENV` or `USER@HOST:ENV`).
    :type source: str
    :param source_mode: Mode on the source machine (`"dev"` or `"prod"`).
    :type source_mode: str
    :param target: Target address (`ENV` or `USER@HOST:ENV`).
    :type target: str
    :param target_mode: Mode on the target machine (`"dev"` or `"prod"`).
    :type target_mode: str
    :param control_args: SSH multiplexing flags from the outer `SshMux`
                         context in `main.py`. Pass `[]` or `None` if
                         calling standalone (e.g. in tests) — each SSH
                         call will open its own connection in that case.
    :type control_args: list[str] | None
    :raises ValueError: If both source and target are remote (unsupported topology).
    :raises RuntimeError: If any step of the sync fails.
    """
    source_remote, source_env = parse_address(source)
    target_remote, target_env = parse_address(target)

    if source_remote is not None and target_remote is not None:
        raise ValueError(
            "Remote → remote sync is not supported. "
            "Run the sync from one of the machines directly."
        )

    db_cfg = runtime.full_config.backend.database
    source_db = db_cfg.get_postgres_database_name(source_env)

    runtime.logger.info(
        f"Syncing database: {source} ({source_mode}) → {target} ({target_mode})"
    )

    if source_remote is None and target_remote is None:
        # local → local — no SSH involved
        dump_file = _dump_local(source_mode, source_env)
        _restore_local(target_mode, target_env, dump_file)
        _cleanup_transfer(dump_file, source_db)
        return

    if source_remote is not None:
        # remote → local
        _dump_remote(source_remote, source_mode, source_env, control_args)
        dump_file = _scp_pull(source_remote, source_db, control_args)
        remote_file = (
            f"{_remote_transfer_dir(source_remote, control_args)}/{dump_file.name}"
        )
        _restore_local(target_mode, target_env, dump_file)
        _cleanup_transfer(dump_file, source_db)
        _ssh_run(source_remote, f"rm -f {remote_file}", control_args)
    else:
        # local → remote
        dump_file = _dump_local(source_mode, source_env)
        _scp_push(target_remote, dump_file, control_args)
        _restore_remote(target_remote, target_mode, target_env, dump_file, control_args)
        _cleanup_transfer(dump_file, source_db)
        remote_file = (
            f"{_remote_transfer_dir(target_remote, control_args)}/{dump_file.name}"
        )
        _ssh_run(target_remote, f"rm -f {remote_file}", control_args)
