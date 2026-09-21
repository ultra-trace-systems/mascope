"""Seeded test for ``sample_file.sample_file_utc_created`` in `5b9e2c7d1a40`.

The column says when the converter registered a file, and a list of the files
added recently (#482) will read it. A file registered before the column
existed has no known registration time, and must not look added on the day
of the upgrade: Postgres gives existing rows a default that is present when
a column is added, so the migration adds the column bare and sets the default
afterwards. A file registered after the upgrade gets the time from the
database, with nothing in the code setting it.

Rows are inserted through raw SQL rather than the ORM: at PRIOR_REVISION the
column does not exist, and the point after it is that an insert which does
not name the column still gets a time.
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

REVISION = "5b9e2c7d1a40"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type)
    VALUES (:id, :id, 'Orbi-Lab2', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '-', 'orbi')
"""


def _insert(engine: Engine, sample_file_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(_SAMPLE_FILE_SQL),
            {
                "id": sample_file_id,
                "local": datetime(2026, 9, 1, 12, 0),
                "utc": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            },
        )


def _created(engine: Engine, sample_file_id: str) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT sample_file_utc_created FROM sample_file "
                "WHERE sample_file_id = :id"
            ),
            {"id": sample_file_id},
        ).scalar_one()


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A file registered before the migration, then the migration applied."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _insert(seeded_engine, "sf-before")
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_a_file_registered_before_the_column_has_no_registration_time(
    upgraded: Engine,
):
    assert _created(upgraded, "sf-before") is None


def test_a_file_registered_after_it_gets_the_time_from_the_database(
    upgraded: Engine,
):
    with upgraded.connect() as conn:
        before = conn.execute(text("SELECT now()")).scalar_one()

    _insert(upgraded, "sf-after")

    with upgraded.connect() as conn:
        after = conn.execute(text("SELECT now()")).scalar_one()
    created = _created(upgraded, "sf-after")
    assert created is not None
    assert before <= created <= after


def test_the_downgrade_removes_the_column(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it leaves the database at PRIOR_REVISION.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        present = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'sample_file' "
                "AND column_name = 'sample_file_utc_created'"
            )
        ).scalar_one()
    assert present == 0
