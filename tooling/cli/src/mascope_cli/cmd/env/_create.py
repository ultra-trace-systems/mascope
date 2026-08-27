"""
Environment creation helpers for `mascope env` commands.

Used by `main.py` (env create command) and `_sync.py` (auto-create
missing target env during sync locally and on remote
machines). Contains no Typer commands.

An environment is simply a named directory under `.runtime/env/`. Creating
one makes it visible to `mascope env list`, `mascope env use`, and the
`validate_env` check used by db commands.

Callers (`main.py`) are responsible for argument parsing, confirmation
prompts, and error reporting.
"""

import subprocess

from mascope_cli.cmd.env._paths import get_remote_mascope_path, local_env_dir
from mascope_cli.cmd.env._ssh import cygwin_bin, get_identity_args
from mascope_cli.runtime import runtime
from mascope_runtime import is_valid_env_name


def validate_env_name(name: str) -> None:
    """
    Validate a runtime environment name.

    Names must be non-empty and match ``mascope_runtime.ENV_NAME_PATTERN``
    (ASCII letters, digits, underscores, and hyphens). That excludes
    whitespace, path separators, and shell metacharacters that would
    break SSH commands and filesystem operations. The rule is the runtime
    library's rather than this module's, because an env name also reaches a
    Postgres database name and the dev auth cookie's name - widening it in
    one place only would let those quietly disagree.

    :param name: Proposed environment name.
    :type name: str
    :raises ValueError: If the name is empty or contains disallowed characters.
    """
    if not name:
        raise ValueError("Environment name must not be empty.")
    if not is_valid_env_name(name):
        raise ValueError(
            f"Environment name '{name}' is invalid. "
            "Only letters, digits, underscores, and hyphens are allowed."
        )


def create_env_local(name: str) -> None:
    """
    Create a local runtime environment directory.

    Creates `.runtime/env/{name}/` under `MASCOPE_PATH`. Raises if the
    directory already exists.

    :param name: Name of the environment to create.
    :type name: str
    :raises ValueError: If the name is invalid.
    :raises FileExistsError: If the environment already exists.
    :raises OSError: If directory creation fails.
    """
    validate_env_name(name)
    env_dir = local_env_dir(name)
    if env_dir.exists():
        raise FileExistsError(f"Environment '{name}' already exists at {env_dir}.")
    env_dir.mkdir(parents=True, exist_ok=False)
    runtime.logger.info(f"Created environment '{name}' at {env_dir}")


def create_env_remote(
    remote: str,
    name: str,
    control_args: list[str] | None = None,
) -> None:
    """
    Create a runtime environment directory on a remote machine via SSH.

    Runs `mkdir -p` for `.runtime/env/{name}/` under the remote
    `MASCOPE_PATH`. Does not depend on the remote CLI — uses only
    standard shell commands.

    :param remote: Remote identifier in `USER@HOST` format.
    :type remote: str
    :param name: Name of the environment to create.
    :type name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :raises ValueError: If the name is invalid.
    :raises RuntimeError: If the SSH command fails.
    """
    validate_env_name(name)
    mascope_path = get_remote_mascope_path(remote, control_args)
    env_dir = f"{mascope_path}/.runtime/env/{name}"
    result = subprocess.run(
        [cygwin_bin("ssh")]
        + get_identity_args()
        + (control_args or [])
        + [remote, "bash", "-l", "-c", f"'mkdir -p {env_dir}'"],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create environment '{name}' on {remote} (exit {result.returncode})"
        )
    runtime.logger.info(f"Created environment '{name}' on {remote} at {env_dir}")
