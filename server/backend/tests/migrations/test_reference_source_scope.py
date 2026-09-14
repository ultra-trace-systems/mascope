"""Seeded test for the reference source scope added in `a7d3e9c1f5b2`.

Stairway and drift walk the chain against an empty database, so the backfill
matches no row there and the NOT NULL that follows it holds trivially. Here it
runs over the two kinds of source a deployment can hold before the revision - a
database mirror and a shipped list - and the assertions pin that both are
backfilled with the one window every source was matched inside until then, with
no radicals and both polarities. A list's own values are the seed's to write,
not the migration's.

Rows are seeded at the previous revision through raw SQL rather than the ORM: at
PRIOR_REVISION the columns this suite is about do not exist yet.
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

REVISION = "a7d3e9c1f5b2"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_MIRROR_WINDOW = {
    "elements": ["C", "H", "N", "O", "S"],
    "max_carbon": 40,
    "max_mass": 700.0,
}

# (reference_source_id, name, version, licence, active)
_SOURCES = [
    (1, "pubchem", "2026-07", "public-domain", True),
    (2, "cyclic-siloxanes", "2026.09", "CC-BY-4.0", True),
    (3, "pubchem", "2026-01", "public-domain", False),
]

_SOURCE_SQL = """
    INSERT INTO reference_source (reference_source_id, name, version, license,
                                  record_count, is_active, ingested_at)
    VALUES (:id, :name, :version, :license, 4, :active, :ingested)
"""


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(_SOURCE_SQL),
            [
                {
                    "id": source_id,
                    "name": name,
                    "version": version,
                    "license": licence,
                    "active": active,
                    "ingested": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
                }
                for source_id, name, version, licence, active in _SOURCES
            ],
        )


def _scopes(engine: Engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT reference_source_id, known_window, allow_radicals, polarity "
                "FROM reference_source"
            )
        ).all()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def _columns(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'reference_source'"
            )
        ).all()
    return {name: nullable for name, nullable in rows}


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """The seeded database taken from PRIOR_REVISION through the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_every_existing_row_keeps_the_window_it_was_matched_inside(upgraded: Engine):
    expected = (_MIRROR_WINDOW, False, None)
    assert _scopes(upgraded) == {source[0]: expected for source in _SOURCES}


def test_a_row_must_say_its_window_and_its_allowance(upgraded: Engine):
    columns = _columns(upgraded)
    assert columns["known_window"] == "NO"
    assert columns["allow_radicals"] == "NO"
    assert columns["polarity"] == "YES"


def test_a_row_that_names_no_window_is_bounded_like_a_mirror(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reference_source (reference_source_id, name, version, "
                "license, record_count, is_active, ingested_at) "
                "VALUES (5, 'hmdb', '5.0', 'hmdb-attribution', 1, true, now())"
            )
        )
    assert _scopes(upgraded)[5] == (_MIRROR_WINDOW, False, None)


def test_a_load_that_says_nothing_of_radicals_allows_none(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reference_source (reference_source_id, name, version, "
                "license, record_count, is_active, ingested_at, known_window) "
                "VALUES (4, 'custom', 'v1', 'custom', 1, true, now(), "
                "CAST(:window AS json))"
            ),
            {"window": '{"elements": null, "max_carbon": null, "max_mass": null}'},
        )
    window, allow_radicals, polarity = _scopes(upgraded)[4]
    assert window == {"elements": None, "max_carbon": None, "max_mass": None}
    assert allow_radicals is False
    assert polarity is None


def test_the_downgrade_removes_the_columns(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it takes the database back below the revision.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    assert not {"known_window", "allow_radicals", "polarity"} & set(_columns(upgraded))
