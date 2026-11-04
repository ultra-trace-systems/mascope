"""Seeded test for `9b5c3e7a1f60`, the labelled reagent's purity on a setup.

Every existing mode takes the figure the engine read for every run until now,
the library's default, so no result moves.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
at PRIOR_REVISION the column this suite is about does not exist yet.
"""

from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "9b5c3e7a1f60"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mode (ionization_mode_id,"
                " ionization_mode_name, ionization_mode_polarity,"
                " ionization_mechanism_ids)"
                " VALUES ('modepurity00001', 'Nitrate 15N', '-', CAST('[]' AS json))"
            )
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_an_existing_mode_reads_the_library_default(upgraded: Engine):
    with upgraded.connect() as conn:
        purity = conn.execute(
            text("SELECT label_purity FROM ionization_mode")
        ).scalar_one()
    assert purity == CUSTOM_ELEMENTS["^N"].default_purity


def test_downgrade_drops_the_column(upgraded: Engine, seeded_alembic_config: Config):
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    columns = {c["name"] for c in inspect(upgraded).get_columns("ionization_mode")}
    assert "label_purity" not in columns
