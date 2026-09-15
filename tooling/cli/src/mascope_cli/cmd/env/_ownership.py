"""
Ownership checks for the `mascope env sync` filestore transfer.

rsync is invoked without `-o`/`-g`, so nothing is carried across from the
sender: the receiving rsync creates every new file owned by whoever runs it on
the *receiving* side — the SSH login user for a push, the invoking user for a
pull or a local sync. The prod stack runs as uid 1000 by default
(`ARG MASCOPE_UID` in `server/backend/Dockerfile`), so when those two differ
the app cannot read or write what was just synced.

Chowning to another uid needs root, which `env sync` does not have — and
`_ssh_run` invokes ssh without `-t`, so an interactive sudo prompt would fail
or hang. The default is therefore to detect and report the exact remediation
command; `--chown` opts into a non-interactive `sudo -n` attempt and falls
back to printing the command when that is unavailable.

The check compares two things it can measure exactly — the owner of the target
env directory (rsync never chowns a directory that already existed) and the
uid the receiving rsync ran as — so it makes no guess about the container's
uid. Nothing here raises: a failed check must not fail an otherwise good sync.

Contains no Typer commands — implementation only.
"""

import os
import subprocess
from pathlib import Path

from mascope_cli.cmd.env._paths import local_env_dir, remote_env_dir
from mascope_cli.cmd.env._ssh import cygwin_bin, get_identity_args
from mascope_cli.runtime import runtime


def _on_windows() -> bool:
    """
    Whether the local machine is Windows.

    Wrapped in a function so tests can drive both platform branches.

    :return: `True` on Windows.
    :rtype: bool
    """
    return os.name == "nt"


def _local_state(env_name: str) -> tuple[int, int, int, str] | None:
    """
    Measure ownership of a local target env directory.

    :param env_name: Name of the target runtime environment.
    :type env_name: str
    :return: `(owner_uid, owner_gid, receiver_uid, env_dir)`, or `None` when
             there is nothing to check (Windows has no POSIX ownership, and a
             missing env dir means the sync never got that far).
    :rtype: tuple[int, int, int, str] | None
    """
    if _on_windows():
        return None
    env_dir = local_env_dir(env_name)
    try:
        stat = env_dir.stat()
    except OSError as e:
        runtime.logger.debug(f"Ownership check skipped for {env_dir}: {e}")
        return None
    return stat.st_uid, stat.st_gid, os.getuid(), str(env_dir)


def _remote_state(
    remote: str,
    env_name: str,
    control_args: list[str] | None = None,
) -> tuple[int, int, int, str] | None:
    """
    Measure ownership of a remote target env directory over one SSH round trip.

    The `%u:%g` format string is deliberately space-free so the remote command
    needs no nested quoting.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param env_name: Name of the target runtime environment.
    :type env_name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: `(owner_uid, owner_gid, receiver_uid, env_dir)`, or `None` if the
             remote command failed or its output could not be parsed.
    :rtype: tuple[int, int, int, str] | None
    """
    env_dir = remote_env_dir(remote, env_name, control_args)
    cmd = f"stat -c %u:%g {env_dir}; id -u"
    result = subprocess.run(
        [cygwin_bin("ssh")]
        + get_identity_args()
        + (control_args or [])
        + [remote, "bash", "-l", "-c", f"'{cmd}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    try:
        owner_uid, owner_gid = (int(part) for part in lines[0].split(":"))
        receiver_uid = int(lines[1])
    except (IndexError, ValueError):
        runtime.logger.debug(
            f"Ownership check skipped for {remote}:{env_dir}: "
            f"returncode={result.returncode} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        return None
    return owner_uid, owner_gid, receiver_uid, env_dir


def _try_chown(
    remote: str | None,
    owner_uid: int,
    owner_gid: int,
    env_dir: str,
    manual_command: str,
    control_args: list[str] | None = None,
) -> None:
    """
    Attempt the chown non-interactively via `sudo -n`, reporting on failure.

    `chown -R` does not follow a symlinked `<env>/filestore` (GNU default
    `-P`), and that symlink is a documented deployment layout
    (docs/maintaining.md → "The filestore on a data volume"), so the resolved
    filestore path is passed as a second operand. It is the same path when the
    filestore is an ordinary directory, which only costs a second pass.

    :param remote: Remote identifier (`USER@HOST`) or `None` for a local target.
    :type remote: str | None
    :param owner_uid: Uid to chown to.
    :type owner_uid: int
    :param owner_gid: Gid to chown to.
    :type owner_gid: int
    :param env_dir: Absolute path to the target env directory.
    :type env_dir: str
    :param manual_command: Command to print when `sudo -n` is unavailable.
    :type manual_command: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    """
    owner = f"{owner_uid}:{owner_gid}"
    if remote is not None:
        # `$(...)` expands to nothing when there is no filestore, leaving the
        # env dir as the only operand.
        cmd = (
            f"sudo -n chown -R {owner} {env_dir} "
            f"$(test -e {env_dir}/filestore && readlink -f {env_dir}/filestore)"
        )
        runtime.logger.info(f"SSH {remote}: bash -l -c '{cmd}'")
        args = (
            [cygwin_bin("ssh")]
            + get_identity_args()
            + (control_args or [])
            + [remote, "bash", "-l", "-c", f"'{cmd}'"]
        )
        where = remote
    else:
        targets = [env_dir]
        filestore = Path(env_dir) / "filestore"
        if filestore.exists():
            targets.append(str(filestore.resolve()))
        args = ["sudo", "-n", "chown", "-R", owner] + targets
        runtime.logger.info(f"Running: {' '.join(args)}")
        where = "this machine"

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as e:
        runtime.logger.warning(
            f"Could not run sudo on {where} ({e}) — run this yourself:"
            f"\n    {manual_command}"
        )
        return

    if result.returncode != 0:
        runtime.logger.warning(
            f"Passwordless sudo is not available on {where} "
            f"(exit {result.returncode}) — run this yourself:"
            f"\n    {manual_command}"
        )
        return
    runtime.logger.success(f"Changed ownership of {env_dir} to {owner}.")


def check_after_sync(
    target_remote: str | None,
    target_env: str,
    control_args: list[str] | None = None,
    chown: bool = False,
) -> None:
    """
    Warn — and optionally fix — when the synced files are unusable by the app.

    Compares the uid the receiving rsync ran as against the owner of the
    target env directory. A difference means every file just transferred is
    owned by the wrong user; the warning names the exact `sudo chown` to run.

    Never raises: a failed check must not fail an otherwise good sync.

    :param target_remote: Remote identifier (`USER@HOST`) of the target, or
                          `None` when the target is local.
    :type target_remote: str | None
    :param target_env: Name of the target runtime environment.
    :type target_env: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :param chown: Attempt the chown via `sudo -n` instead of only reporting it.
    :type chown: bool
    """
    try:
        if target_remote is not None:
            state = _remote_state(target_remote, target_env, control_args)
        else:
            state = _local_state(target_env)
    except Exception as e:  # never fail a good sync over a failed check
        runtime.logger.debug(f"Ownership check failed: {e}")
        return

    if state is None:
        return

    owner_uid, owner_gid, receiver_uid, env_dir = state
    if owner_uid == receiver_uid:
        runtime.logger.info(
            f"Synced files are owned by uid {owner_uid}:{owner_gid} — "
            f"matching {env_dir}."
        )
        return

    fix = f"sudo chown -R {owner_uid}:{owner_gid} {env_dir}"
    runtime.logger.warning(
        f"Filestore files were written as uid {receiver_uid}, but {env_dir} is "
        f"owned by uid {owner_uid}. Mascope's containers run as the "
        f"deployment's own uid (1000 by default, MASCOPE_UID in "
        f"server/backend/Dockerfile), so the new files may not be readable or "
        f"writable by the app. Fix ownership with:"
        f"\n    {fix}"
        f"\nIf the filestore is a symlink to a data volume, chown its target "
        f"too — `chown -R` does not follow symlinks."
    )

    if chown:
        try:
            _try_chown(
                target_remote,
                owner_uid,
                owner_gid,
                env_dir,
                fix,
                control_args,
            )
        except Exception as e:  # same contract as the check itself
            runtime.logger.warning(
                f"Could not change ownership ({e}) — run this yourself:\n    {fix}"
            )
