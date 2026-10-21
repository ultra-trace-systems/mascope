"""
Hermetic test environment for the Mascope CLI suite.

``mascope_cli`` creates its Runtime singleton on first use, not at import
(``mascope_cli.runtime``): the first command or fixture that touches it reads
``MASCOPE_PATH``, loads the ``*.mascope.toml`` layers from that path, and
persists mode/env state to ``.runtime/state.json``. To keep this suite
hermetic — runnable without a configured shell, and without clobbering a
developer's real ``.runtime/state.json`` — this conftest builds a throwaway
runtime home from the repo's real config layers and points ``MASCOPE_PATH``
at it before anything can create the Runtime.

The suite needs no Docker, Postgres, or network; commands that shell out are
tested against a mocked ``lib.run`` / ``subprocess.run``.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_home() -> Path:
    """Build a disposable MASCOPE_PATH home from the repo's config layers."""
    home = Path(tempfile.mkdtemp(prefix="mascope-cli-tests-"))
    for name in ("base.mascope.toml", "dev.mascope.toml", "prod.mascope.toml"):
        shutil.copy(REPO_ROOT / name, home / name)
    (home / ".runtime" / "env" / "default").mkdir(parents=True)
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    return home


TEST_HOME = _make_home()
os.environ["MASCOPE_PATH"] = str(TEST_HOME)

# Imported only after MASCOPE_PATH points at the temp home — the Runtime
# singleton resolves its config against whatever the env var says right now.
import pytest  # noqa: E402

from mascope_cli.runtime import runtime  # noqa: E402


@pytest.fixture
def mascope_home() -> Path:
    """The temp MASCOPE_PATH home directory backing the CLI singleton."""
    return TEST_HOME


@pytest.fixture
def cli_runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_state(monkeypatch):
    """
    Isolate tests from the process-level state the CLI mutates.

    The entrypoint callback writes env vars, and commands persist mode/env
    to the singleton's state.json (e.g. ``mascope test run`` sets the active
    mode to "test"). Start each test clean and undo any drift afterwards.
    """
    for var in (
        "MASCOPE_VERSION",
        "_MASCOPE_VERSION_PINNED",
        "MASCOPE_LOGLEVEL",
        "MASCOPE_LOGGREP",
        "MASCOPE_ENV",
        "MASCOPE_API_PORT",
        "MASCOPE_FRONTEND_PORT",
        "MASCOPE_INSTANCE",
    ):
        monkeypatch.delenv(var, raising=False)

    yield

    runtime.state.mode = "dev"
    runtime.state.env = "default"
    runtime.state.override("mode", None)
    runtime.state.override("env", None)
    runtime.reload_config()
