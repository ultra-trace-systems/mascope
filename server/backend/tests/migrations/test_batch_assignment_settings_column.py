"""Seeded test for ``sample_batch.assignment_settings``, added in `4d7b2e9a6c15`.

A batch that exists when the revision runs saved no settings, so it comes
through with SQL NULL, the defaults; one written after it stores the settings
it is given; the downgrade drops the column and keeps the batches.

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

REVISION = "4d7b2e9a6c15"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


def _seed(engine: Engine, suffix: str) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-as{s}', 'As{s}')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-as{s}', 'ws-as{s}', 'Settings')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-as{s}', 'ds-as{s}',"
            " 'Settings')",
        ):
            conn.execute(text(statement.format(s=suffix)))


def _column(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [
            row.data_type
            for row in conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns"
                    " WHERE table_name = 'sample_batch'"
                    " AND column_name = 'assignment_settings'"
                )
            )
        ]


def _settings(engine: Engine, suffix: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT assignment_settings FROM sample_batch"
                " WHERE sample_batch_id = :id"
            ),
            {"id": f"sb-as{suffix}"},
        ).scalar_one()


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed a batch at the prior revision, upgrade, seed another after it."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(seeded_engine) == []
    _seed(seeded_engine, "-b")
    upgrade(seeded_alembic_config, REVISION)
    _seed(seeded_engine, "-a")
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE sample_batch SET assignment_settings ="
                " '{\"mz_precision_ppm\": 3.0}'::jsonb"
                " WHERE sample_batch_id = 'sb-as-a'"
            )
        )
    return seeded_engine


def test_the_column_is_jsonb(migrated: Engine) -> None:
    assert _column(migrated) == ["jsonb"]


def test_a_batch_from_before_the_revision_saved_none(migrated: Engine) -> None:
    assert _settings(migrated, "-b") is None


def test_a_batch_after_it_stores_its_settings(migrated: Engine) -> None:
    assert _settings(migrated, "-a") == {"mz_precision_ppm": 3.0}


def test_downgrade_drops_the_column_and_keeps_the_batches(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(migrated) == []
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM sample_batch"
                    " WHERE sample_batch_id LIKE 'sb-as-%'"
                )
            ).scalar_one()
            == 2
        )
    upgrade(seeded_alembic_config, REVISION)
    assert _column(migrated) == ["jsonb"]
