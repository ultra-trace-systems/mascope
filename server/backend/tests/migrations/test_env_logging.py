"""
Running Alembic in-process leaves the process's logging as it was.

`alembic/env.py` runs on every Alembic command, and `alembic.ini` carries a
logging setup meant for Alembic's own command line. Applied inside another
program, `logging.config.fileConfig` rewrites that program's logging: it
replaces the root handlers - in the backend, the runtime's bridge from stdlib
logging into loguru - raises the root level to WARNING, and by default disables
every logger that already exists. The migration tests run Alembic in-process,
in the same session as the tests after them, so there a loguru sink would see
nothing from a library that logs through `logging.getLogger`: a test that a
record arrives would fail in the full suite while passing alone, and a test
that no WARNING arrives would pass whatever the code logged.

env.py runs here in offline mode with a migration function that yields no
steps, so its module-level code runs in full, nothing is migrated, and no
database is needed.
"""

import argparse
import io
import logging
import logging.config
from pathlib import Path

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory

from mascope_backend.runtime import runtime


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# Offline mode never connects, so the URL only has to name a dialect.
_OFFLINE_URL = "postgresql+psycopg2://offline@localhost/offline"


def _run_env(cmd_opts: argparse.Namespace | None) -> None:
    """Run env.py the way an Alembic command does, migrating nothing.

    :param cmd_opts: The parsed command line, or None for an in-process caller
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


def test_an_in_process_run_leaves_logging_as_it_was():
    # Created here rather than at import, so that no earlier Alembic run in
    # the session can have disabled it before this one gets the chance.
    library_logger = logging.getLogger(f"{__name__}.library")
    before = _settings()

    _run_env(cmd_opts=None)

    after = _settings()
    changed = [logger.name for logger in before if after[logger] != before[logger]]
    assert changed == []

    # The property the rest of the suite relies on: a stdlib record still
    # reaches a sink on the runtime logger.
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        library_logger.info("logged after an in-process Alembic run")
    finally:
        runtime.logger.remove(sink_id)
    assert "logged after an in-process Alembic run" in [r["message"] for r in records]


def test_the_command_line_gets_the_ini_logging_and_keeps_existing_loggers(
    monkeypatch,
):
    # Recorded rather than applied: a real fileConfig would rewrite this
    # session's logging, closing pytest's own handlers along the way.
    calls = []
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    _run_env(cmd_opts=argparse.Namespace())

    assert calls == [((str(_ALEMBIC_INI),), {"disable_existing_loggers": False})]
