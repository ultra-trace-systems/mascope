"""Seeded test for the identification level's columns added in
`5e8a3c1d9b47` (step 5.8).

Stairway and drift walk the chain against an empty database; this says what
the revision has to hold on a database with a ledger in it: ``peak_assignment``
gains ``level``, ``batch_peak`` and ``batch_peak_run_anchor`` gain
``engine_level``, rows written before the upgrade read them as NULL - the level
is stated when a run is made, never backfilled - and the downgrade drops them
and keeps the rows.

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


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "5e8a3c1d9b47"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

#: Table -> the columns the revision adds to it.
COLUMNS = {
    "peak_assignment": {"level"},
    "batch_peak": {"engine_level", "engine_evidence", "engine_lift"},
    "batch_peak_run_anchor": {"engine_level", "engine_evidence", "engine_lift"},
}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-level', 'Level')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-level', 'ws-level', 'Level')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-level', 'ds-level', 'Level')",
            "INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm)"
            " VALUES ('bp-level', 'sb-level', 150.09, 3.0)",
            "INSERT INTO batch_peak_run (batch_peak_run_id, sample_batch_id,"
            " action, engine_version, batch_peak_run_utc_created)"
            " VALUES ('run-level', 'sb-level', 'read_co_variation', 'test', now())",
            "INSERT INTO batch_peak_run_anchor (batch_peak_run_id, batch_peak_id,"
            " mz, consensus_tier, n_present) VALUES ('run-level', 'bp-level',"
            " 150.09, 'candidate', 2)",
        ):
            conn.execute(text(statement))


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


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed the ledger at the prior revision and upgrade."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_each_table_gains_its_column(migrated: Engine) -> None:
    for table, columns in COLUMNS.items():
        assert columns <= _columns(migrated, table), table


def test_an_earlier_row_reads_none(migrated: Engine) -> None:
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT engine_level FROM batch_peak WHERE batch_peak_id = 'bp-level'"
                )
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text(
                    "SELECT engine_level FROM batch_peak_run_anchor"
                    " WHERE batch_peak_id = 'bp-level'"
                )
            ).scalar_one()
            is None
        )


def test_downgrade_drops_the_columns_and_keeps_the_rows(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    for table, columns in COLUMNS.items():
        assert not columns & _columns(migrated, table), table
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM batch_peak WHERE batch_peak_id = 'bp-level'")
            ).scalar_one()
            == 1
        )
