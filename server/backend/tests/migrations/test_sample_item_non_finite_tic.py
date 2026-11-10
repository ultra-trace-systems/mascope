"""Seeded test for clearing a non-finite sample item TIC in `566299655056`.

Stairway and drift run the chain against an empty database, where the
``UPDATE`` matches nothing. Here a NaN, both infinities, a finite TIC and a
NULL one are seeded at the previous revision: the three that are no finite
number come out NULL, and the other two as they were.

Rows are seeded through raw SQL rather than the ORM: a migration test has to
describe the schema as it was at PRIOR_REVISION.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "566299655056"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

# item id -> the TIC it is seeded with
_TICS = {
    "si-nan": float("nan"),
    "si-inf": float("inf"),
    "si-neg-inf": float("-inf"),
    "si-finite": 1234.5,
    "si-zero": 0.0,
    "si-null": None,
}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-tic', 'Non-finite TIC')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-tic', 'ws-tic', 'Non-finite TIC')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_file (sample_file_id, filename, instrument,
                                         instrument_type,
                                         "datetime", datetime_utc, length,
                                         "range", polarity)
                VALUES ('sf-tic', 'non-finite-tic.h5', 'instrument-x', 'tof', :local,
                        :utc, 60.0, CAST('[100.0, 500.0]' AS json), '-')
            """),
            {
                "local": datetime(2026, 10, 1, 12, 0),
                "utc": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            },
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-tic', 'ds-tic', 'Non-finite TIC')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_item (sample_item_id, sample_batch_id,
                                         sample_file_id, sample_item_name,
                                         sample_item_type, polarity, tic)
                VALUES (:id, 'sb-tic', 'sf-tic', :id, 'ACQUISITION', '-', :tic)
            """),
            [{"id": item_id, "tic": tic} for item_id, tic in _TICS.items()],
        )


def _tics(engine: Engine) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(text("SELECT sample_item_id, tic FROM sample_item")).all()
        )


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed the TICs at the prior revision, apply the migration, read back."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    seeded = _tics(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return {"seeded": seeded, "migrated": _tics(seeded_engine)}


def test_the_seed_holds_what_the_migration_clears(migrated: dict) -> None:
    """Postgres keeps a NaN and both infinities in a float column, or the
    test below would pass on rows that never held one."""
    seeded = migrated["seeded"]
    assert seeded["si-nan"] != seeded["si-nan"]
    assert seeded["si-inf"] == float("inf")
    assert seeded["si-neg-inf"] == float("-inf")


def test_a_tic_that_is_no_finite_number_is_cleared(migrated: dict) -> None:
    tics = migrated["migrated"]
    assert tics["si-nan"] is None
    assert tics["si-inf"] is None
    assert tics["si-neg-inf"] is None


def test_every_other_tic_is_left_as_it_was(migrated: dict) -> None:
    tics = migrated["migrated"]
    assert tics["si-finite"] == 1234.5
    assert tics["si-zero"] == 0.0
    assert tics["si-null"] is None


def test_it_downgrades(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == PRIOR_REVISION
