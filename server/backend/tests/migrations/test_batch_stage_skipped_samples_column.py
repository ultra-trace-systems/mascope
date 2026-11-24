"""Seeded test for ``batch_peak_stage.skipped_samples``, added in `e2c7a4b9d6f3`.

A stage row that exists when the revision runs comes through with SQL NULL:
its run is read as one that skipped no sample, as it stood before; one
written after it records the samples it skipped; the downgrade drops the
column and keeps the stage rows.

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

REVISION = "e2c7a4b9d6f3"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


def _seed(engine: Engine, suffix: str) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-sk{s}', 'Sk{s}')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-sk{s}', 'ws-sk{s}', 'Skipped')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-sk{s}', 'ds-sk{s}', 'Skipped')",
            "INSERT INTO batch_peak_stage (sample_batch_id, stage, version, state)"
            " VALUES ('sb-sk{s}', 'search', 1, 'current')",
        ):
            conn.execute(text(statement.format(s=suffix)))


def _column(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            (row.data_type, row.is_nullable)
            for row in conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns"
                    " WHERE table_name = 'batch_peak_stage'"
                    " AND column_name = 'skipped_samples'"
                )
            )
        ]


def _skipped(engine: Engine, suffix: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT skipped_samples FROM batch_peak_stage"
                " WHERE sample_batch_id = :id"
            ),
            {"id": f"sb-sk{suffix}"},
        ).scalar_one()


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed a stage row at the prior revision, upgrade, seed another after."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(seeded_engine) == []
    _seed(seeded_engine, "-b")
    upgrade(seeded_alembic_config, REVISION)
    _seed(seeded_engine, "-a")
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE batch_peak_stage SET skipped_samples ="
                ' \'[{"sample_item_id": "s-2", "peaks_read": false}]\'::jsonb'
                " WHERE sample_batch_id = 'sb-sk-a'"
            )
        )
    return seeded_engine


def test_the_column_is_nullable_jsonb(migrated: Engine) -> None:
    assert _column(migrated) == [("jsonb", "YES")]


def test_a_stage_from_before_the_revision_skipped_none(migrated: Engine) -> None:
    assert _skipped(migrated, "-b") is None


def test_a_stage_after_it_records_what_it_skipped(migrated: Engine) -> None:
    assert _skipped(migrated, "-a") == [{"sample_item_id": "s-2", "peaks_read": False}]


def test_downgrade_drops_the_column_and_keeps_the_stages(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(migrated) == []
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM batch_peak_stage"
                    " WHERE sample_batch_id LIKE 'sb-sk-%'"
                )
            ).scalar_one()
            == 2
        )
    upgrade(seeded_alembic_config, REVISION)
    assert _column(migrated) == [("jsonb", "YES")]
