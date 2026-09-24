"""
Running Alembic in-process leaves the process's logging as it was.

`alembic/env.py` applies `alembic.ini`'s logging setup only when Alembic runs
from its own command line; its module docstring says what that setup would do
to a process that runs Alembic in-process, as the migration tests do.

`fileConfig` is recorded rather than applied throughout. A real call would
rewrite this session's logging - the very harm under test - for every test
that runs after it, and close pytest's own handlers along the way.
"""

import argparse
import io
import logging
import logging.config
from pathlib import Path

import pytest
from alembic.config import CommandLine, Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from test_utils import captured_logs


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# Offline mode never connects, so the URL only has to name a dialect.
_OFFLINE_URL = "postgresql+psycopg2://offline@localhost/offline"


@pytest.fixture
def file_config_calls(monkeypatch) -> list[tuple[tuple, dict]]:
    """Record every `fileConfig` call env.py makes, instead of applying it."""
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    return calls


def _run_env_in_process(cmd_opts: argparse.Namespace | None) -> None:
    """Run env.py the way an in-process Alembic command does, migrating nothing.

    Offline mode, with a migration function that yields no steps: env.py's
    module-level code runs in full, and no database is needed.

    :param cmd_opts: What the caller put on `Config.cmd_opts`
    """
    cfg = Config(str(_ALEMBIC_INI), output_buffer=io.StringIO(), cmd_opts=cmd_opts)
    cfg.set_main_option("sqlalchemy.url", _OFFLINE_URL)
    script = ScriptDirectory.from_config(cfg)
    with EnvironmentContext(cfg, script, fn=lambda _rev, _context: [], as_sql=True):
        script.run_env()


def _settings() -> dict[logging.Logger, tuple]:
    """Everything fileConfig can change, for the root and every logger."""
    loggers = [logging.root] + [
        logger
        for logger in list(logging.root.manager.loggerDict.values())
        if isinstance(logger, logging.Logger)
    ]
    return {
        logger: (logger.level, logger.handlers[:], logger.propagate, logger.disabled)
        for logger in loggers
    }


@pytest.mark.parametrize(
    "cmd_opts",
    [
        pytest.param(None, id="no-cmd-opts"),
        # `-x` arguments are read from `cmd_opts`, so a caller passing them
        # sets it without being Alembic's command line.
        pytest.param(argparse.Namespace(x=["probe=1"]), id="x-arguments"),
    ],
)
def test_an_in_process_run_leaves_logging_as_it_was(file_config_calls, cmd_opts):
    library_logger = logging.getLogger(f"{__name__}.library")
    before = _settings()

    _run_env_in_process(cmd_opts)

    assert file_config_calls == []
    after = _settings()
    changed = [logger.name for logger in before if after[logger] != before[logger]]
    assert changed == []

    # The property the rest of the suite relies on: a stdlib record still
    # reaches a sink on the runtime logger.
    with captured_logs() as records:
        library_logger.info("logged after an in-process Alembic run")
    assert "logged after an in-process Alembic run" in [r["message"] for r in records]


def test_the_command_line_gets_the_ini_logging_and_keeps_existing_loggers(
    file_config_calls,
):
    # Alembic's real entry point, so that this fails if it ever stops marking
    # its runs as the command line. A `head:head` range in --sql mode runs
    # env.py in full and generates nothing.
    CommandLine(prog="alembic").main(
        ["--raiseerr", "-c", str(_ALEMBIC_INI), "upgrade", "head:head", "--sql"]
    )

    assert len(file_config_calls) == 1
    args, kwargs = file_config_calls[0]
    assert Path(args[0]) == _ALEMBIC_INI
    assert kwargs.get("disable_existing_loggers") is False
