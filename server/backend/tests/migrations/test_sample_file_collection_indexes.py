"""Seeded test for the calibration trend's collection indexes in `dbe7955ef975`.

The revision only adds two partial expression indexes to ``sample_file``. The
stairway proves they apply and drop on an empty table, and the drift test
that the models declare the same expressions. Neither reads the indexes'
predicate, which the drift test does not compare, nor whether the trend's
looks back are read off them: an expression index is used only by a
statement that spells its expression. So rows are seeded, the upgrade runs
over them, each index's definition is read whole, and the trend's own
statements are EXPLAINed against what the migration built.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
a migration test has to describe the schema as it was, not as the models are
now.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from mascope_backend.api.controllers.calibration import calibration_trend


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "dbe7955ef975"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_ID = "(mz_calibration #>> '{quality,collection,id}'::text[])"
_SHA = "(mz_calibration #>> '{quality,collection,isotopes_sha256}'::text[])"
# Name -> what Postgres reports after the table name in the index's
# definition: the columns, an expression in parentheses of its own, and the
# predicate.
_INDEXES = {
    "ix_sample_file_instrument_polarity_collection_version": (
        f"USING btree (instrument, polarity, ({_ID}), ({_SHA}), datetime_utc)"
        f" WHERE ({_ID} IS NOT NULL)"
    ),
    "ix_sample_file_instrument_polarity_collection": (
        f"USING btree (instrument, polarity, ({_ID}), datetime_utc)"
        f" WHERE ({_ID} IS NOT NULL)"
    ),
}

_NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
_ROWS = 2000
_V1, _V2 = "a" * 64, "b" * 64


# --- Seed SQL --------------------------------------------------------------

# One file an hour of two TOF instruments: tof-named's records are fits with
# collection c-1, its first version and then its second; tof-bare's are fits
# from before records named a collection.
_SEED_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type, mz_calibration)
    SELECT 'sf-' || i, 'sf-' || i || '.h5',
           CASE WHEN i % 2 = 0 THEN 'tof-named' ELSE 'tof-bare' END,
           CAST(t AT TIME ZONE 'UTC' AS timestamp), t, 60.0,
           CAST('[10.0, 600.0]' AS json), '-', 'tof',
           CASE WHEN i % 2 = 0 THEN json_build_object(
               'status', 'ok',
               'quality', json_build_object('collection', json_build_object(
                   'id', 'c-1',
                   'isotopes_sha256',
                   CASE WHEN i < :rows / 2 THEN :v2 ELSE :v1 END)))
           ELSE json_build_object('status', 'ok',
                                  'quality', json_build_object('n_points', 3))
           END
    FROM (SELECT i, CAST(:now AS timestamptz) - i * interval '1 hour' AS t
          FROM generate_series(0, :rows - 1) AS i) AS s
"""


# --- The trend's looks back -------------------------------------------------


def _sql(statement) -> str:
    """The statement as the trend builds it, its values written in."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


_START = _NOW + timedelta(days=1)
_LOOKS = {
    "a new version": (
        calibration_trend._version_look("tof-named", "-", _START, ("c-1", "c" * 64)),
        "ix_sample_file_instrument_polarity_collection_version",
    ),
    "a new collection": (
        calibration_trend._collection_look("tof-named", "-", _START, "c-2"),
        "ix_sample_file_instrument_polarity_collection",
    ),
    "any collection, none named": (
        calibration_trend._named_look("tof-bare", "-", _START),
        "ix_sample_file_instrument_polarity_collection",
    ),
    "any collection, of a polarity with no files": (
        calibration_trend._named_look("tof-named", "+", _START),
        "ix_sample_file_instrument_polarity_collection",
    ),
}


def _indexes(conn) -> dict[str, str]:
    """Index name -> definition, for ``sample_file``."""
    rows = conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'sample_file'"
        )
    ).all()
    return {row.indexname: row.indexdef for row in rows}


def _plan(conn, sql: str) -> str:
    rows = conn.execute(text(f"EXPLAIN {sql}")).all()
    return "\n".join(row[0] for row in rows)


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed at the prior revision, upgrade, and read the indexes and plans.

    :return: The snapshot as the upgrade left it.
    :rtype: dict
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(_SEED_SQL), {"rows": _ROWS, "now": _NOW, "v1": _V1, "v2": _V2}
        )
        snapshot: dict = {"indexes_before": _indexes(conn)}
    upgrade(seeded_alembic_config, REVISION)

    with seeded_engine.connect() as conn:
        conn.execute(text("ANALYZE sample_file"))
        snapshot["version"] = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
        snapshot["rows"] = conn.execute(
            text("SELECT count(*) FROM sample_file")
        ).scalar()
        snapshot["indexes"] = _indexes(conn)
        # A few thousand rows are a sequential scan and a sort to the
        # planner; priced out, it is left the indexes, the walk back on
        # ix_sample_file_instrument_datetime_utc included.
        conn.execute(text("SET enable_seqscan = off"))
        snapshot["plans"] = {
            name: _plan(conn, _sql(statement))
            for name, (statement, _) in _LOOKS.items()
        }
        conn.execute(text("RESET enable_seqscan"))
    return snapshot


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    """Step back to the prior revision and read the table again.

    Depends on `migrated` so the upgrade is always the thing being undone.

    :return: What the downgrade left: indexes and row count.
    :rtype: dict
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        return {
            "indexes": _indexes(conn),
            "rows": conn.execute(text("SELECT count(*) FROM sample_file")).scalar(),
        }


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_the_upgrade_adds_the_indexes_and_changes_no_row(migrated: dict) -> None:
    assert not set(_INDEXES) & set(migrated["indexes_before"])
    for name, definition in _INDEXES.items():
        assert migrated["indexes"][name].endswith(definition), migrated["indexes"][name]
    assert set(migrated["indexes"]) - set(_INDEXES) == set(migrated["indexes_before"])
    assert migrated["rows"] == _ROWS


@pytest.mark.parametrize("look", list(_LOOKS))
def test_the_trends_looks_are_read_off_the_indexes(migrated: dict, look: str) -> None:
    """What the trend sends is what the migration indexed."""
    plan = migrated["plans"][look]
    assert f"Index Scan Backward using {_LOOKS[look][1]}" in plan, plan
    assert "Sort" not in plan, plan
    (cond,) = [line for line in plan.splitlines() if "Index Cond:" in line]
    assert "polarity" in cond, plan


# --- Downgrade -------------------------------------------------------------


def test_the_downgrade_drops_only_these_indexes(
    migrated: dict, downgraded: dict
) -> None:
    assert downgraded["indexes"] == migrated["indexes_before"]
    assert downgraded["rows"] == _ROWS
