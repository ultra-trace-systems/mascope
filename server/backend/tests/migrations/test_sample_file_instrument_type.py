"""Seeded test for the instrument-type backfill in `c2d9f4a71b3e`.

Stairway and drift walk the chain against an empty database, so the backfill
matches nothing there and the NOT NULL that follows it holds trivially. Here
it runs over rows that carry the names the old rule read the class from, and
the assertions pin that rule: "orbi" anywhere in the lower-cased name wins,
then "tof" or "api". A name the rule cannot read would leave a NULL and fail
the NOT NULL - which is right, since no such row can exist: every existing
row passed that rule when it was uploaded.

The view is recreated by the same migration, so the test also reads the new
columns back through `sample_view`, and checks the downgrade leaves a view
without them rather than no view at all.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
at PRIOR_REVISION the columns this suite is about do not exist yet.
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

REVISION = "c2d9f4a71b3e"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_BASE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

# (sample_file_id, instrument, expected class)
_FILES = [
    ("sf-type-orbi", "Orbi-Lab2", "orbi"),
    ("sf-type-upper", "KORBI2", "orbi"),
    ("sf-type-tof", "TOF-1", "tof"),
    ("sf-type-api", "api-3000", "tof"),
    ("sf-type-both", "orbi-tof", "orbi"),  # "orbi" wins, as the code did
]

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, :filename, :instrument, :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(_SAMPLE_FILE_SQL),
            [
                {
                    "id": file_id,
                    "filename": f"{instrument}_2026.09.01-12h00m00s_{file_id}",
                    "instrument": instrument,
                    "local": datetime(2026, 9, 1, 12, 0),
                    "utc": _BASE_TIME,
                }
                for file_id, instrument, _ in _FILES
            ],
        )


def _types(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT sample_file_id, instrument_type FROM sample_file")
        ).all()
    return {file_id: instrument_type for file_id, instrument_type in rows}


def _view_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sample_view'"
            )
        ).all()
    return {row[0] for row in rows}


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """The seeded database taken from PRIOR_REVISION through the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_backfill_applies_the_rule_the_code_used_to(upgraded: Engine):
    assert _types(upgraded) == {file_id: expected for file_id, _, expected in _FILES}


def test_the_column_is_required_after_the_backfill(upgraded: Engine):
    with upgraded.connect() as conn:
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'sample_file' AND column_name = 'instrument_type'"
            )
        ).scalar_one()
    assert nullable == "NO"


def test_the_view_carries_the_new_columns(upgraded: Engine):
    assert {"instrument_type", "source_filename"} <= _view_columns(upgraded)


def test_the_downgrade_removes_the_columns_and_keeps_the_view(
    upgraded: Engine, seeded_alembic_config: Config
):
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    columns = _view_columns(upgraded)
    assert columns, "the downgrade must leave sample_view in place"
    assert "instrument_type" not in columns
    assert "source_filename" not in columns
    with upgraded.connect() as conn:
        present = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'sample_file' "
                "AND column_name IN ('instrument_type', 'source_filename')"
            )
        ).scalar_one()
    assert present == 0
