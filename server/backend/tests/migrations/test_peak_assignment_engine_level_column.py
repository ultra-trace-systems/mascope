"""Seeded test for the per-sample import's engine level added in `7d2f9b4e6a13`.

Stairway and drift walk the chain against an empty database; this says what
the revision has to hold on a database with a ledger in it: ``peak_assignment``
gains ``engine_level``, a row written before the upgrade reads it as NULL -
nothing is backfilled - and the downgrade drops it and keeps the row.

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

REVISION = "7d2f9b4e6a13"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-elevel', 'Engine level')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-elevel', 'ws-elevel', 'Engine level')",
            "INSERT INTO sample_file (sample_file_id, filename, instrument,"
            ' "datetime", datetime_utc, length, "range", polarity,'
            " instrument_type) VALUES ('sf-elevel', 'elevel.raw', 'instrument-x',"
            " '2026-11-01 12:00', '2026-11-01 12:00+00', 60.0,"
            " CAST('[100.0, 500.0]' AS json), '+', 'orbi')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-elevel', 'ds-elevel', 'Engine level')",
            "INSERT INTO sample_item (sample_item_id, sample_batch_id,"
            " sample_file_id, sample_item_name, sample_item_type)"
            " VALUES ('si-elevel', 'sb-elevel', 'sf-elevel', 'si-elevel', 'sample')",
            "INSERT INTO peak_assignment_run (peak_assignment_run_id,"
            " sample_item_id, engine, engine_version, status)"
            " VALUES ('run-elevel', 'si-elevel', 'peaky', '0.9.0', 'completed')",
            "INSERT INTO peak_assignment (peak_assignment_id,"
            " peak_assignment_run_id, sample_item_id, sample_peak_id,"
            " sample_peak_mz, sample_peak_intensity, role, tier, engine_tier)"
            " VALUES ('pa-elevel', 'run-elevel', 'si-elevel', 'peak-0', 150.09,"
            " 1.0, 'M0', 'assigned', 'candidate')",
        ):
            conn.execute(text(statement))


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'peak_assignment'"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed an imported row at the prior revision and upgrade."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_ledger_gains_the_column(migrated: Engine) -> None:
    assert "engine_level" in _columns(migrated)


def test_an_earlier_row_reads_none(migrated: Engine) -> None:
    with migrated.connect() as conn:
        row = conn.execute(
            text(
                "SELECT engine_tier, engine_level FROM peak_assignment"
                " WHERE peak_assignment_id = 'pa-elevel'"
            )
        ).one()
    assert (row.engine_tier, row.engine_level) == ("candidate", None)


def test_downgrade_drops_the_column_and_keeps_the_row(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert "engine_level" not in _columns(migrated)
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM peak_assignment"
                    " WHERE peak_assignment_id = 'pa-elevel'"
                )
            ).scalar_one()
            == 1
        )
