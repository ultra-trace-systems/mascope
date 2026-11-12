"""Seeded test for the known-species decision's batch run added in `5d2c81f0e4a7`.

Stairway and drift walk the chain against an empty database, and the drift
test does not compare CHECK constraints, so neither says the one thing this
revision has to hold beyond its DDL: a batch run may be named
``decide_known_species``, the action ``batch_runs.ACTION_KNOWN_SPECIES``
writes, and nothing else new. A batch seeded at the previous revision takes
such a run after the upgrade, and a stripping as before; the snapshot table
gains ``known_species``. The downgrade drops the column and the decision's
runs, which the old constraint cannot name, and keeps every other run.

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

from mascope_backend.api.new.peak_assignments.batch_runs import ACTION_KNOWN_SPECIES


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "5d2c81f0e4a7"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

ACTION = "decide_known_species"
COLUMN = "known_species"


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-known', 'Known species')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-known', 'ws-known', 'Known species')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-known', 'ds-known', 'Known species')"
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
                " VALUES (:run_id, 'sb-known', :action, 'test', now())"
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
                    " WHERE sample_batch_id = 'sb-known'"
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


def test_the_action_the_decision_writes_is_the_one_allowed() -> None:
    assert ACTION_KNOWN_SPECIES == ACTION


def test_a_deciding_run_is_accepted_and_an_earlier_kind_still_is(
    migrated: set[str], seeded_engine: Engine
) -> None:
    _run(seeded_engine, "run-decide", ACTION)
    _run(seeded_engine, "run-strip", "strip_isotopologues")
    assert {"run-decide", "run-strip"} <= _runs(seeded_engine)


def test_an_action_nothing_writes_is_refused(
    migrated: set[str], seeded_engine: Engine
) -> None:
    with pytest.raises(IntegrityError):
        _run(seeded_engine, "run-other", "decide_species")


def test_downgrade_drops_the_column_and_the_deciding_runs_alone(
    migrated: set[str], seeded_alembic_config: Config, seeded_engine: Engine
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert COLUMN not in _columns(seeded_engine, "batch_peak_run_anchor")
    assert _runs(seeded_engine) == {"run-strip"}
