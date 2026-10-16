"""Seeded test for the sample file list's indexes in `1658f7bece0a`.

The revision only adds three indexes to ``sample_file``. The stairway proves
they apply and drop on an empty table, and the drift test that the models
declare the same ones. Neither says whether an index is in the order the list
reads it in: an index on ``datetime_utc`` in the default order is still an
index on ``datetime_utc``, and the planner bounds the scan with it and then
sorts anyway. So rows are seeded, the upgrade runs over them, and the list's
statements are EXPLAINed with sorting priced out - a plan with no sort in it
is one an index delivers in order.

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
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "1658f7bece0a"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

# Name -> the column list Postgres reports in the index's definition.
_INDEXES = {
    "ix_sample_file_datetime_utc": "(datetime_utc DESC NULLS LAST, sample_file_id)",
    "ix_sample_file_instrument_datetime_utc": (
        "(instrument, datetime_utc DESC NULLS LAST, sample_file_id)"
    ),
    "ix_sample_file_processing_updated_utc": "(processing_updated_utc)",
}

_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
_SINCE = _NOW - timedelta(days=7)
_ROWS = 2000


# --- Seed SQL --------------------------------------------------------------

# One file an hour, one in ten of them from instrument-1: on a table this small
# the planner reads an even split of two instruments off the time index with a
# filter, which is in order too, so the instrument's own index has to be the
# plainly cheaper read for its plan to show it. The older half has no
# processing time, as files processed before the column existed have none.
_SEED_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type, processing_updated_utc)
    SELECT 'sf-' || i, 'sf-' || i || '.raw',
           CASE WHEN i % 10 = 0 THEN 'instrument-1' ELSE 'instrument-0' END,
           CAST(t AT TIME ZONE 'UTC' AS timestamp), t, 60.0,
           CAST('[100.0, 500.0]' AS json), '+', 'orbi',
           CASE WHEN i < :rows / 2 THEN t + interval '5 minutes' END
    FROM (SELECT i, CAST(:now AS timestamptz) - i * interval '1 hour' AS t
          FROM generate_series(0, :rows - 1) AS i) AS s
"""


# --- The list's statements -------------------------------------------------

# `get_sample_files` in its default order: newest first, rows without a time
# last, the id breaking ties. The count wraps the ordered statement whole,
# ORDER BY and all, which is how the controller counts it.
_ORDER = "ORDER BY datetime_utc DESC NULLS LAST, sample_file_id"
_RECENT = f"SELECT * FROM sample_file WHERE datetime_utc >= :since {_ORDER}"
_ONE_INSTRUMENT = (
    "SELECT * FROM sample_file"
    f" WHERE datetime_utc >= :since AND instrument = 'instrument-1' {_ORDER}"
)
_STATEMENTS = {
    "recent page": f"{_RECENT} LIMIT 50",
    "recent count": f"SELECT count(*) FROM ({_RECENT}) AS anon_1",
    "instrument page": f"{_ONE_INSTRUMENT} LIMIT 50",
    "instrument count": f"SELECT count(*) FROM ({_ONE_INSTRUMENT}) AS anon_1",
}
_BY_PROCESSING = (
    f"SELECT * FROM sample_file WHERE processing_updated_utc >= :since {_ORDER}"
)


def _indexes(conn) -> dict[str, str]:
    """Index name -> definition, for ``sample_file``."""
    rows = conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'sample_file'"
        )
    ).all()
    return {row.indexname: row.indexdef for row in rows}


def _plan(conn, sql: str) -> str:
    rows = conn.execute(text(f"EXPLAIN {sql}"), {"since": _SINCE}).all()
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
        conn.execute(text(_SEED_SQL), {"rows": _ROWS, "now": _NOW})
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
        # With sorting priced out, the planner sorts only where no index
        # gives the order; with sequential scans priced out too, it reaches
        # for whichever index applies.
        conn.execute(text("SET enable_seqscan = off"))
        conn.execute(text("SET enable_sort = off"))
        snapshot["plans"] = {
            name: _plan(conn, sql) for name, sql in _STATEMENTS.items()
        }
        conn.execute(text("RESET enable_sort"))
        snapshot["by_processing_plan"] = _plan(conn, _BY_PROCESSING)
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
    for name, columns in _INDEXES.items():
        assert migrated["indexes"][name].endswith(f"USING btree {columns}")
    assert set(migrated["indexes"]) - set(_INDEXES) == set(migrated["indexes_before"])
    assert migrated["rows"] == _ROWS


@pytest.mark.parametrize(
    ("statement", "index"),
    [
        ("recent page", "ix_sample_file_datetime_utc"),
        ("recent count", "ix_sample_file_datetime_utc"),
        ("instrument page", "ix_sample_file_instrument_datetime_utc"),
        ("instrument count", "ix_sample_file_instrument_datetime_utc"),
    ],
)
def test_the_list_is_read_in_order_off_its_index(
    migrated: dict, statement: str, index: str
) -> None:
    """The count sorts too: the statement it counts carries the ORDER BY."""
    plan = migrated["plans"][statement]
    assert index in plan, plan
    assert "Sort" not in plan, plan


def test_recent_by_processing_uses_its_index(migrated: dict) -> None:
    plan = migrated["by_processing_plan"]
    assert "ix_sample_file_processing_updated_utc" in plan, plan


# --- Downgrade -------------------------------------------------------------


def test_the_downgrade_drops_only_these_indexes(
    migrated: dict, downgraded: dict
) -> None:
    assert downgraded["indexes"] == migrated["indexes_before"]
    assert downgraded["rows"] == _ROWS
