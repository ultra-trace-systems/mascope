"""Seeded test for the ratio-stability columns added in `c3e8a5f1d7b2`.

Stairway and drift walk the chain against an empty database, and the drift
test does not compare CHECK constraints, so neither says the one thing this
revision has to hold beyond its DDL: ``batch_peak.continuity_grade`` takes the
pass's five grades and nothing else. A batch peak seeded at the previous
revision is graded after the upgrade, refused a grade the pass does not
know, and loses both columns, on both tables, on the downgrade. No backfill:
the seeded row comes through with both columns NULL.

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


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "c3e8a5f1d7b2"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

GRADES = ("red", "drift", "stable", "inconclusive", "insufficient")
COLUMNS = {"continuity_grade", "continuity"}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-cont', 'Continuity')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-cont', 'ws-cont', 'Continuity')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-cont', 'ds-cont', 'Continuity')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz,"
                " mz_tol_ppm) VALUES ('bp-cont', 'sb-cont', 181.0707, 3.0)"
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


def _grade(engine: Engine, grade) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE batch_peak SET continuity_grade = :grade"
                " WHERE batch_peak_id = 'bp-cont'"
            ),
            {"grade": grade},
        )


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed a batch peak at the prior revision, upgrade, read it back."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    with seeded_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT continuity_grade, continuity FROM batch_peak"
                " WHERE batch_peak_id = 'bp-cont'"
            )
        ).one()
    return {
        "row": tuple(row),
        "anchor": _columns(seeded_engine, "batch_peak"),
        "snapshot": _columns(seeded_engine, "batch_peak_run_anchor"),
    }


def test_both_tables_gain_both_columns_and_nothing_is_backfilled(
    migrated: dict,
) -> None:
    assert COLUMNS <= migrated["anchor"]
    assert COLUMNS <= migrated["snapshot"]
    assert migrated["row"] == (None, None)


def test_every_grade_of_the_pass_is_accepted(migrated: dict, seeded_engine) -> None:
    for grade in (*GRADES, None):
        _grade(seeded_engine, grade)


def test_a_grade_the_pass_does_not_know_is_refused(
    migrated: dict, seeded_engine
) -> None:
    with pytest.raises(IntegrityError):
        _grade(seeded_engine, "green")


def test_downgrade_drops_both_columns_from_both_tables(
    migrated: dict, seeded_alembic_config: Config, seeded_engine: Engine
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert not COLUMNS & _columns(seeded_engine, "batch_peak")
    assert not COLUMNS & _columns(seeded_engine, "batch_peak_run_anchor")
