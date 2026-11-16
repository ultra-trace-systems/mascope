"""Seeded test for the seeding columns added in `9e4b2c7d1a35`.

Every row that exists when the revision runs predates seeding: a batch that
never asked for it, an anchor a sample's peak minted, a snapshot of such an
anchor. So each comes through false, and a row written after the upgrade that
names none of the three columns is false too - the server default the ORM's
inserts and the raw ones share. The downgrade drops all three.

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

REVISION = "9e4b2c7d1a35"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_READ = {
    "sample_batch": "SELECT seed_batch_peaks FROM sample_batch"
    " WHERE sample_batch_id = :id",
    "batch_peak": "SELECT seeded FROM batch_peak WHERE batch_peak_id = :id",
    "batch_peak_run_anchor": "SELECT seeded FROM batch_peak_run_anchor"
    " WHERE batch_peak_id = :id",
}


def _seed(engine: Engine, suffix: str) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-seed{s}', 'Seeding{s}')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-seed{s}', 'ws-seed{s}', 'Seeding')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-seed{s}', 'ds-seed{s}', 'Seeding')",
            "INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz,"
            " mz_tol_ppm) VALUES ('bp-seed{s}', 'sb-seed{s}', 181.0707, 3.0)",
            "INSERT INTO batch_peak_run (batch_peak_run_id, sample_batch_id,"
            " action, status, engine_version, batch_peak_run_utc_created)"
            " VALUES ('run-seed{s}', 'sb-seed{s}',"
            " 'fold', 'completed', '0.1.0', now())",
            "INSERT INTO batch_peak_run_anchor (batch_peak_run_id, batch_peak_id,"
            " mz, consensus_tier, n_present) VALUES ('run-seed{s}', 'bp-seed{s}',"
            " 181.0707, 'unassigned', 0)",
        ):
            conn.execute(text(statement.format(s=suffix)))


def _read(engine: Engine, suffix: str) -> dict:
    ids = {
        "sample_batch": f"sb-seed{suffix}",
        "batch_peak": f"bp-seed{suffix}",
        "batch_peak_run_anchor": f"bp-seed{suffix}",
    }
    with engine.connect() as conn:
        return {
            table: conn.execute(text(sql), {"id": ids[table]}).scalar_one()
            for table, sql in _READ.items()
        }


def _columns(engine: Engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        return {
            (row.table_name, row.column_name)
            for row in conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns"
                    " WHERE column_name IN ('seeded', 'seed_batch_peaks')"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed rows at the prior revision, upgrade, seed again after it."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine, "-before")
    upgrade(seeded_alembic_config, REVISION)
    _seed(seeded_engine, "-after")
    return seeded_engine


def test_every_row_from_before_the_revision_reads_false(migrated: Engine) -> None:
    assert _read(migrated, "-before") == dict.fromkeys(_READ, False)


def test_a_row_written_without_the_columns_reads_false(migrated: Engine) -> None:
    assert _read(migrated, "-after") == dict.fromkeys(_READ, False)


def test_downgrade_drops_all_three_columns(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _columns(migrated) == set()
