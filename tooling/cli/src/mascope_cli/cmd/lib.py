"""
Shell command execution utilities for the Mascope CLI.

Provides a thin wrapper around `subprocess.run` that merges caller-supplied
environment variables on top of the current process environment, ensuring
all CLI-level env vars (log level, runtime path, etc.) are inherited by
subprocesses.
"""

import os
import shlex
import subprocess
from typing import Optional

from mascope_cli.runtime import runtime as cli_runtime
from mascope_runtime import Runtime


def run(
    command: str,
    runtime: Optional[Runtime] = None,
    env_vars: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Execute a shell command in a subprocess.

    Merges `env_vars` on top of the current `os.environ` so that all inherited
    process environment variables (including `MASCOPE_LOGLEVEL`,
    `MASCOPE_LOGGREP`, etc.) remain visible to the subprocess. Caller-supplied
    `env_vars` take precedence over inherited values.

    Returns the completed process result. Callers that need to detect failure
    should check `result.returncode`. Most callers can ignore the return value —
    the subprocess output is streamed directly to the terminal.

    The child inherits both streams as they are: its stdout goes to this
    process's stdout and its stderr, where a Python child's log goes, to this
    process's stderr. So redirecting a passthrough command's stdout to a file
    keeps only the child's output, as it does for the CLI's own commands.

    :param command: Shell command string to execute. Split via `shlex.split`
                    before passing to the subprocess — no shell interpolation.
    :type command: str
    :param runtime: Runtime instance used to resolve the working directory
                    when `cwd` is not provided. Defaults to the CLI singleton.
    :type runtime: Runtime, optional
    :param env_vars: Additional environment variables to inject into the subprocess
                 environment. Merged on top of `os.environ`. Defaults to empty.
    :type env_vars: dict[str, str], optional
    :param cwd: Working directory for the subprocess. Defaults to
                `runtime.path()` when not provided.
    :type cwd: str, optional
    :return: Completed process result.
    :rtype: subprocess.CompletedProcess
    """
    _runtime = runtime or cli_runtime
    _vars = env_vars or {}
    _cwd = cwd or _runtime.path()

    env = os.environ.copy()
    env.update(_vars)

    return subprocess.run(
        shlex.split(command),
        cwd=_cwd,
        env=env,
        check=False,  # callers handle errors via logged output
    )
