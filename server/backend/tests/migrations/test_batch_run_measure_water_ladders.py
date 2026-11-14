"""Seeded test for the water ladders' batch run added in `59f0d2274261`.

Stairway and drift walk the chain against an empty database, and the drift
test does not compare CHECK constraints, so neither says the one thing this
revision has to hold beyond its DDL: a batch run may be named
``measure_water_ladders``, the action ``batch_runs.ACTION_WATER_LADDERS``
writes, and nothing else new. A batch seeded at the previous revision takes
such a run after the upgrade, and a known-species decision as before; the
snapshot table gains ``water_ladder``. The downgrade drops the column and the
measurement's runs, which the old constraint cannot name, and keeps every
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

from mascope_backend.api.new.peak_assignments.batch_runs import ACTION_WATER_LADDERS


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "59f0d2274261"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

ACTION = "measure_water_ladders"
COLUMN = "water_ladder"


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-ladder', 'Water ladders')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-ladder', 'ws-ladder', 'Water ladders')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-ladder', 'ds-ladder', 'Water ladders')"
            )
        )


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = :table"
                ),
                {"table": table},
            ).all()
        }


def _run(engine: Engine, run_id: str, action: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO batch_peak_run (batch_peak_run_id, sample_batch_id,"
                " action, engine_version, batch_peak_run_utc_created)"
                " VALUES (:run_id, 'sb-ladder', :action, 'test', now())"
            ),
            {"run_id": run_id, "action": action},
        )


def _runs(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row.batch_peak_run_id
            for row in conn.execute(
                text(
                    "SELECT batch_peak_run_id FROM batch_peak_run"
                    " WHERE sample_batch_id = 'sb-ladder'"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> set[str]:
    """Seed a batch at the prior revision, upgrade, read the snapshot table."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _columns(seeded_engine, "batch_peak_run_anchor")


def test_the_snapshot_table_gains_the_column(migrated: set[str]) -> None:
    assert COLUMN in migrated


def test_the_action_the_measurement_writes_is_the_one_allowed() -> None:
    assert ACTION_WATER_LADDERS == ACTION


def test_a_measuring_run_is_accepted_and_an_earlier_kind_still_is(
    migrated: set[str], seeded_engine: Engine
) -> None:
    _run(seeded_engine, "run-measure", ACTION)
    _run(seeded_engine, "run-decide", "decide_known_species")
    assert {"run-measure", "run-decide"} <= _runs(seeded_engine)


def test_an_action_nothing_writes_is_refused(
    migrated: set[str], seeded_engine: Engine
) -> None:
    with pytest.raises(IntegrityError):
        _run(seeded_engine, "run-other", "measure_ladders")


def test_downgrade_drops_the_column_and_the_measuring_runs_alone(
    migrated: set[str], seeded_alembic_config: Config, seeded_engine: Engine
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert COLUMN not in _columns(seeded_engine, "batch_peak_run_anchor")
    assert _runs(seeded_engine) == {"run-decide"}
