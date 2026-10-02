"""
Runtime names the CLI defines itself rather than importing.

The published CLI must import against every runtime its pyproject.toml floor
admits, and it imports all its command groups up front, so importing a runtime
name newer than that floor breaks every ``mascope`` command. Such a name is
defined in ``mascope_cli.runtime`` instead. These tests keep each one equal to
the runtime's, and check that the CLI imports without it.
"""

import subprocess
import sys

from mascope_cli import runtime as cli_runtime
from mascope_runtime import config


def test_the_deployment_file_is_the_one_the_backend_writes():
    assert cli_runtime.DEPLOYMENT_FILE == config.DEPLOYMENT_FILE


def test_the_cli_imports_against_a_runtime_without_the_deployment_file():
    # As far as the CLI can tell, a runtime published before the name existed
    code = (
        "import mascope_runtime.config as config\n"
        "del config.DEPLOYMENT_FILE\n"
        "import mascope_cli.main\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )

    assert result.returncode == 0, result.stderr
