"""Seeded test for a pass of the batch stages as one run, added in `9c3e5a7b1d24`.

Stairway and drift walk the chain against an empty database, and the drift
test does not compare CHECK constraints, so neither says what this revision
has to hold beyond its DDL: a batch run may be named ``assign``, the action a
pass writes (``batch_runs.ACTION_ASSIGN``), and may end ``cancelled``
(``batch_runs.STATUS_CANCELLED``), and nothing else new; the run gains the
cancel flag. The downgrade drops the flag, files a cancelled run as failed and
drops the passes' runs, which the old constraints cannot name, keeping every
other run.

Rows are seeded through raw SQL rather than the ORM: a migration test has to
describe the schema as it was at PRIOR_REVISION.
"""

from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ASSIGN,
    STATUS_CANCELLED,
)


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "9c3e5a7b1d24"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

COLUMN = "cancel_requested_utc"


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-assign', 'Assign pass')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-assign', 'ws-assign', 'Assign pass')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-assign', 'ds-assign', 'Assign pass')"
            )
        )


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'batch_peak_run'"
                )
            ).all()
        }


def _run(engine: Engine, run_id: str, action: str, status: str = "running") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO batch_peak_run (batch_peak_run_id, sample_batch_id,"
                " action, status, engine_version, batch_peak_run_utc_created)"
                " VALUES (:run_id, 'sb-assign', :action, :status, 'test', now())"
            ),
            {"run_id": run_id, "action": action, "status": status},
        )


def _runs(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.batch_peak_run_id: row.status
            for row in conn.execute(
                text(
                    "SELECT batch_peak_run_id, status FROM batch_peak_run"
                    " WHERE sample_batch_id = 'sb-assign'"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> set[str]:
    """Seed a batch at the prior revision, upgrade, read the run table."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _columns(seeded_engine)


def test_the_run_gains_the_cancel_flag(migrated: set[str]) -> None:
    assert COLUMN in migrated


def test_the_action_and_status_a_pass_writes_are_the_ones_allowed() -> None:
    assert (ACTION_ASSIGN, STATUS_CANCELLED) == ("assign", "cancelled")


def test_a_pass_and_a_cancelled_run_are_accepted_and_earlier_kinds_still_are(
    migrated: set[str], seeded_engine: Engine
) -> None:
    _run(seeded_engine, "run-pass", "assign", "cancelled")
    _run(seeded_engine, "run-step", "detect_series", "cancelled")
    _run(seeded_engine, "run-search", "search_untargeted", "completed")
    assert {"run-pass", "run-step", "run-search"} <= set(_runs(seeded_engine))


def test_an_action_or_status_nothing_writes_is_refused(
    migrated: set[str], seeded_engine: Engine
) -> None:
    with pytest.raises(IntegrityError):
        _run(seeded_engine, "run-other", "assign_batch")
    with pytest.raises(IntegrityError):
        _run(seeded_engine, "run-other", "assign", "stopped")


def test_downgrade_drops_the_flag_and_the_passes_and_files_cancelled_as_failed(
    migrated: set[str], seeded_alembic_config: Config, seeded_engine: Engine
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert COLUMN not in _columns(seeded_engine)
    assert _runs(seeded_engine) == {"run-step": "failed", "run-search": "completed"}
