"""Seeded test for the persistence admission's snapshot columns added in
`f5353f93681a`.

Stairway and drift walk the chain against an empty database; this says what
the revision has to hold on a database with a batch in it: the snapshot table
gains ``persistence`` and ``tier_reasons``, a snapshot row written before the
upgrade reads both as NULL, and the downgrade drops them and keeps the row.

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

REVISION = "f5353f93681a"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

COLUMNS = {"persistence", "tier_reasons"}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-persist', 'Persistence')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-persist', 'ws-persist', 'Persistence')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-persist', 'ds-persist', 'Persistence')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO batch_peak_run (batch_peak_run_id, sample_batch_id,"
                " action, engine_version, batch_peak_run_utc_created)"
                " VALUES ('run-persist', 'sb-persist', 'search_untargeted', 'test',"
                " now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO batch_peak_run_anchor (batch_peak_run_id, batch_peak_id,"
                " mz, consensus_tier, n_present) VALUES ('run-persist',"
                " 'bp-persist', 150.09, 'candidate', 11)"
            )
        )


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'batch_peak_run_anchor'"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> set[str]:
    """Seed a snapshot row at the prior revision, upgrade, read the table."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _columns(seeded_engine)


def test_the_snapshot_table_gains_the_columns(migrated: set[str]) -> None:
    assert COLUMNS <= migrated


def test_an_earlier_snapshot_row_reads_none(
    migrated: set[str], seeded_engine: Engine
) -> None:
    with seeded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT persistence, tier_reasons FROM batch_peak_run_anchor"
                " WHERE batch_peak_id = 'bp-persist'"
            )
        ).one()
    assert (row.persistence, row.tier_reasons) == (None, None)


def test_downgrade_drops_the_columns_and_keeps_the_row(
    migrated: set[str], seeded_alembic_config: Config, seeded_engine: Engine
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert not COLUMNS & _columns(seeded_engine)
    with seeded_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM batch_peak_run_anchor"
                    " WHERE batch_peak_id = 'bp-persist'"
                )
            ).scalar_one()
            == 1
        )
