"""Seeded test for the batch member's own target compound added in
`9c4e7a2b5d18` (MAS-992).

Stairway and drift walk the chain against an empty database; this says what
the revision has to hold on a database with a batch ledger in it:
``batch_peak_occurrence`` gains a nullable ``target_compound_id``, a member
folded before the upgrade reads it as NULL and keeps everything it had - the
column is never backfilled and nothing is re-folded - the column follows its
compound's deletion (``ON DELETE SET NULL``) through a partial index, and the
downgrade drops all three and keeps the rows.

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

REVISION = "9c4e7a2b5d18"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

INDEX = "ix_batch_peak_occurrence_target_compound_id"
FOREIGN_KEY = "fk_batch_peak_occurrence_target_compound_id_target_compound"


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in (
            "INSERT INTO workspace (workspace_id, workspace_name)"
            " VALUES ('ws-mtc', 'Member target')",
            "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
            " VALUES ('ds-mtc', 'ws-mtc', 'Member target')",
            "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
            " sample_batch_name) VALUES ('sb-mtc', 'ds-mtc', 'Member target')",
            "INSERT INTO sample_file (sample_file_id, filename, instrument,"
            ' instrument_type, "datetime", datetime_utc, length, "range", polarity)'
            " VALUES ('sf-mtc', 'member-target.raw', 'instrument-x', 'orbi', now(),"
            " now(), 60.0, CAST('[100.0, 500.0]' AS json), '+')",
            "INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,"
            " sample_item_name, sample_item_type)"
            " VALUES ('si-mtc', 'sb-mtc', 'sf-mtc', 'si-mtc', 'sample')",
            "INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm,"
            " candidates) VALUES ('bp-mtc', 'sb-mtc', 181.07, 3.0,"
            ' CAST(\'[{"formula": "C6H12O6", "target_compound_id": "tc-mtc"}]\''
            " AS json))",
            "INSERT INTO batch_peak_occurrence (batch_peak_id, sample_item_id,"
            " sample_peak_id, intensity, tier, candidate)"
            " VALUES ('bp-mtc', 'si-mtc', 'p-mtc', 5000.0, 3, 0)",
            "INSERT INTO target_compound (target_compound_id,"
            " target_compound_name, target_compound_formula)"
            " VALUES ('tc-mtc', 'glucose', 'C6H12O6')",
        ):
            conn.execute(text(statement))


def _member(engine: Engine) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT * FROM batch_peak_occurrence"
                    " WHERE batch_peak_id = 'bp-mtc' AND sample_item_id = 'si-mtc'"
                )
            )
            .mappings()
            .one()
        )


def _index_definition(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": INDEX},
        ).scalar_one_or_none()


def _foreign_key_action(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT confdeltype FROM pg_constraint WHERE conname = :name"),
            {"name": FOREIGN_KEY},
        ).scalar_one_or_none()


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine):
    """Seed the ledger at the prior revision and upgrade; the member as it read
    before the upgrade rides along."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    before = _member(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine, before


@pytest.fixture
def migrated(upgraded) -> Engine:
    return upgraded[0]


def test_an_earlier_member_reads_none_and_keeps_what_it_had(upgraded) -> None:
    migrated, before = upgraded
    member = _member(migrated)
    assert member.pop("target_compound_id") is None
    assert member == before
    # The registry is not touched: its entry still answers for the member.
    with migrated.connect() as conn:
        assert conn.execute(
            text(
                "SELECT candidates::text FROM batch_peak WHERE batch_peak_id = 'bp-mtc'"
            )
        ).scalar_one() == ('[{"formula": "C6H12O6", "target_compound_id": "tc-mtc"}]')


def test_the_column_is_a_partially_indexed_reference_cleared_with_its_compound(
    migrated: Engine,
) -> None:
    definition = _index_definition(migrated)
    assert definition is not None
    assert "(target_compound_id)" in definition
    assert "WHERE (target_compound_id IS NOT NULL)" in definition
    assert _foreign_key_action(migrated) == "n"  # SET NULL
    with migrated.begin() as conn:
        conn.execute(
            text(
                "UPDATE batch_peak_occurrence SET target_compound_id = 'tc-mtc'"
                " WHERE batch_peak_id = 'bp-mtc'"
            )
        )
    assert _member(migrated)["target_compound_id"] == "tc-mtc"
    with migrated.begin() as conn:
        conn.execute(
            text("DELETE FROM target_compound WHERE target_compound_id = 'tc-mtc'")
        )
    assert _member(migrated)["target_compound_id"] is None


def test_downgrade_drops_the_column_and_keeps_the_rows(
    migrated: Engine, seeded_alembic_config: Config
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert "target_compound_id" not in _member(migrated)
    assert _index_definition(migrated) is None
    assert _foreign_key_action(migrated) is None
