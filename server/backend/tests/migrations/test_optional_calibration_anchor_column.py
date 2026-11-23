"""Seeded test for ``target_compound_in_target_collection.optional_anchor``,
added in `b5e3a9d2c7f1`.

A compound a collection holds when the revision runs is no optional anchor,
so every TOF fit is held to the lines it was held to before; one marked after
it keeps its mark; the downgrade drops the column and keeps the links.

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

REVISION = "b5e3a9d2c7f1"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


def _seed(engine: Engine, suffix: str) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO target_collection (target_collection_id,"
            " target_collection_name, target_collection_type)"
            " VALUES ('tc-oa{s}', 'Anchors{s}', 'CALIBRANTS')",
            "INSERT INTO target_compound (target_compound_id,"
            " target_compound_name, target_compound_formula)"
            " VALUES ('cp-oa{s}', 'Anchor{s}', 'C6H18O3Si3')",
            "INSERT INTO target_compound_in_target_collection"
            " (target_compound_id, target_collection_id)"
            " VALUES ('cp-oa{s}', 'tc-oa{s}')",
        ):
            conn.execute(text(statement.format(s=suffix)))


def _column(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            (row.data_type, row.is_nullable, row.column_default)
            for row in conn.execute(
                text(
                    "SELECT data_type, is_nullable, column_default"
                    " FROM information_schema.columns"
                    " WHERE table_name = 'target_compound_in_target_collection'"
                    " AND column_name = 'optional_anchor'"
                )
            )
        ]


def _mark(engine: Engine, suffix: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT optional_anchor FROM target_compound_in_target_collection"
                " WHERE target_collection_id = :id"
            ),
            {"id": f"tc-oa{suffix}"},
        ).scalar_one()


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Seed a link at the prior revision, upgrade, seed and mark another."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(seeded_engine) == []
    _seed(seeded_engine, "-b")
    upgrade(seeded_alembic_config, REVISION)
    _seed(seeded_engine, "-a")
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE target_compound_in_target_collection"
                " SET optional_anchor = true WHERE target_collection_id = 'tc-oa-a'"
            )
        )
    return seeded_engine


def test_the_column_is_a_boolean_false_by_default(migrated: Engine) -> None:
    assert _column(migrated) == [("boolean", "NO", "false")]


def test_a_link_from_before_the_revision_is_no_optional_anchor(
    migrated: Engine,
) -> None:
    assert _mark(migrated, "-b") is False


def test_a_link_after_it_keeps_its_mark(migrated: Engine) -> None:
    assert _mark(migrated, "-a") is True


def test_downgrade_drops_the_column_and_keeps_the_links(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert _column(migrated) == []
    with migrated.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM target_compound_in_target_collection"
                    " WHERE target_collection_id LIKE 'tc-oa-%'"
                )
            ).scalar_one()
            == 2
        )
    upgrade(seeded_alembic_config, REVISION)
    assert _column(migrated) == [("boolean", "NO", "false")]
