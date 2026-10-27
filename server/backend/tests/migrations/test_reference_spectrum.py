"""Seeded test for `762236a8e559`, which adds the reference_spectrum table and
the kind of a reference source's load.

Stairway and drift walk the chain against an empty database, so the kind's
backfill matches no row there. Here it runs over the sources a deployment holds
before the revision - a database mirror's loads and a shipped list - and pins
that every one of them reads as a compound source afterwards. Then the table's
own rules: a spectrum goes with its load, and a spectrum without a precursor,
peaks or a licence is refused. The downgrade goes last: it drops the table and
the source rows of spectral loads, which without their kind would read as
compound sources holding records and no compounds.

Rows are seeded at the previous revision through raw SQL rather than the ORM: at
PRIOR_REVISION the column and the table this suite is about do not exist yet.
"""

from datetime import datetime, timezone
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

REVISION = "762236a8e559"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

# (reference_source_id, name, version, licence, active)
_SOURCES = [
    (1, "pubchem", "2026-07", "public-domain", True),
    (2, "cyclic-siloxanes", "2026.09", "CC-BY-4.0", True),
    (3, "pubchem", "2026-01", "public-domain", False),
]

_SOURCE_SQL = """
    INSERT INTO reference_source (reference_source_id, name, version, license,
                                  record_count, is_active, ingested_at)
    VALUES (:id, :name, :version, :license, 1, :active, :ingested)
"""

_SPECTRUM_SQL = """
    INSERT INTO reference_spectrum (reference_spectrum_id, reference_source_id,
                                    source_native_id, precursor_mz, peaks, license)
    VALUES (:id, :source, :native, :mz, CAST(:peaks AS json), :license)
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


def _kinds(engine: Engine) -> dict[int, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT reference_source_id, kind FROM reference_source")
        ).all()
    return dict(rows)


def _load_library(engine: Engine) -> None:
    """A spectral library's load, as ingest_spectra writes one."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reference_source (reference_source_id, name, version, "
                "license, record_count, is_active, ingested_at, kind) "
                "VALUES (10, 'massbank', '2026.03', 'massbank-unstated', 2, true, "
                "now(), 'spectrum')"
            )
        )
        conn.execute(
            text(_SPECTRUM_SQL),
            [
                {
                    "id": spectrum_id,
                    "source": 10,
                    "native": f"MSBNK-TEST-{spectrum_id}",
                    "mz": 195.0877,
                    "peaks": "[[138.0662, 999.0]]",
                    "license": "CC-BY",
                }
                for spectrum_id in (1, 2)
            ],
        )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """The seeded database taken from PRIOR_REVISION through the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_every_existing_source_is_a_compound_source(upgraded: Engine):
    assert _kinds(upgraded) == {source[0]: "compound" for source in _SOURCES}


def test_a_source_that_names_no_kind_is_a_compound_source(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reference_source (reference_source_id, name, version, "
                "license, record_count, is_active, ingested_at) "
                "VALUES (4, 'hmdb', '5.0', 'hmdb-attribution', 1, true, now())"
            )
        )
    assert _kinds(upgraded)[4] == "compound"


def test_a_spectrum_goes_with_its_load(upgraded: Engine):
    _load_library(upgraded)
    assert _count(upgraded, "reference_spectrum") == 2
    with upgraded.begin() as conn:
        conn.execute(
            text("DELETE FROM reference_source WHERE reference_source_id = 10")
        )
    assert _count(upgraded, "reference_spectrum") == 0


@pytest.mark.parametrize(
    "column", ["precursor_mz", "peaks", "license", "source_native_id"]
)
def test_a_spectrum_without_what_the_read_path_needs_is_refused(
    upgraded: Engine, column: str
):
    row = {
        "id": 99,
        "source": 1,
        "native": "X",
        "mz": 195.0877,
        "peaks": "[[138.0662, 999.0]]",
        "license": "CC0",
    }
    key = {"source_native_id": "native", "precursor_mz": "mz"}.get(column, column)
    row[key] = None
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(text(_SPECTRUM_SQL), row)


def test_a_spectrum_points_at_a_source_that_exists(upgraded: Engine):
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(_SPECTRUM_SQL),
                {
                    "id": 98,
                    "source": 12345,
                    "native": "X",
                    "mz": 195.0877,
                    "peaks": "[]",
                    "license": "CC0",
                },
            )


def test_the_downgrade_drops_the_table_and_the_spectral_loads(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it takes the database back below the revision.
    _load_library(upgraded)
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name = 'reference_spectrum'"
            )
        ).all()
        columns = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'reference_source'"
                )
            )
            .scalars()
            .all()
        )
        sources = (
            conn.execute(
                text("SELECT reference_source_id FROM reference_source ORDER BY 1")
            )
            .scalars()
            .all()
        )
    assert tables == []
    assert "kind" not in columns
    # The compound sources stay; the library's row goes with its spectra.
    assert sources == [1, 2, 3, 4]
