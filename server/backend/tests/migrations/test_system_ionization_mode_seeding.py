"""Seeded test for `9a3c5e71b8d4`, which marks the modes Mascope ships.

The revision adds ``ionization_mode.system_key`` and nothing else. The rows
are seeded at startup instead, because which of them a server can hold depends
on the ionization mechanisms it already has, and a mechanism cannot be
inserted by a migration without also building the target ions of every
compound in the library.

What the migration must not do is disturb the deployment's own ionization
data, on the way up or on the way down. A downgrade in particular leaves the
rows in place: deleting them would take the chemistry off any sample or
batch-peak anchor bound to one.

Rows are inserted through raw SQL: at PRIOR_REVISION ``system_key`` does not
exist, and the ORM model has it.
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

REVISION = "9a3c5e71b8d4"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_OWN_MECHANISM_ID = "ownmechbromide01"
_OWN_MODE_ID = "ownmodebromide01"


def _seed_own_rows(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mechanism ("
                "  ionization_mechanism_id, ionization_mechanism_polarity,"
                "  ionization_mechanism"
                ") VALUES (:id, '-', '+Br-')"
            ),
            {"id": _OWN_MECHANISM_ID},
        )
        conn.execute(
            text(
                "INSERT INTO ionization_mode ("
                "  ionization_mode_id, ionization_mode_name,"
                "  ionization_mode_token, ionization_mode_polarity,"
                "  ionization_mechanism_ids"
                ") VALUES (:id, 'Bromide', 'BR', '-',"
                "          CAST(:mechanisms AS json))"
            ),
            {"id": _OWN_MODE_ID, "mechanisms": f'["{_OWN_MECHANISM_ID}"]'},
        )


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A deployment's own ionization rows, then the migration applied."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed_own_rows(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_column_arrives_empty(upgraded: Engine):
    """Every mode that existed before is the deployment's own."""
    with upgraded.connect() as conn:
        keyed = conn.execute(
            text("SELECT count(*) FROM ionization_mode WHERE system_key IS NOT NULL")
        ).scalar_one()
    assert keyed == 0


def test_the_migration_inserts_no_mechanisms(upgraded: Engine):
    """Inserting one would skip the target ions the API builds with it."""
    with upgraded.connect() as conn:
        mechanisms = (
            conn.execute(
                text("SELECT ionization_mechanism_id FROM ionization_mechanism")
            )
            .scalars()
            .all()
        )
    assert mechanisms == [_OWN_MECHANISM_ID]


def test_the_deployments_own_mode_is_untouched(upgraded: Engine):
    with upgraded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ionization_mode_name, ionization_mode_token, system_key"
                "  FROM ionization_mode WHERE ionization_mode_id = :id"
            ),
            {"id": _OWN_MODE_ID},
        ).one()
    assert row == ("Bromide", "BR", None)


def test_the_key_is_unique(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE ionization_mode SET system_key = 'nitrate' "
                "WHERE ionization_mode_id = :id"
            ),
            {"id": _OWN_MODE_ID},
        )
    with pytest.raises(Exception):
        with upgraded.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ionization_mode ("
                    "  ionization_mode_id, ionization_mode_name,"
                    "  ionization_mode_polarity, ionization_mechanism_ids,"
                    "  system_key"
                    ") VALUES ('dupekeymode01', 'Another', '-',"
                    "          CAST('[]' AS json), 'nitrate')"
                )
            )
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE ionization_mode SET system_key = NULL "
                "WHERE ionization_mode_id = :id"
            ),
            {"id": _OWN_MODE_ID},
        )


def test_the_downgrade_keeps_every_mode_and_drops_the_column(
    upgraded: Engine, seeded_alembic_config: Config
):
    """A seeded mode a sample took keeps that sample's chemistry."""
    # Runs last: it leaves the database at PRIOR_REVISION.
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mode ("
                "  ionization_mode_id, ionization_mode_name,"
                "  ionization_mode_polarity, ionization_mechanism_ids, system_key"
                ") VALUES ('sysNitrate', 'Nitrate, negative', '-',"
                "          CAST(:mechanisms AS json), 'nitrate')"
            ),
            {"mechanisms": f'["{_OWN_MECHANISM_ID}"]'},
        )

    downgrade(seeded_alembic_config, PRIOR_REVISION)

    with upgraded.connect() as conn:
        present = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'ionization_mode' "
                "AND column_name = 'system_key'"
            )
        ).scalar_one()
        modes = conn.execute(text("SELECT count(*) FROM ionization_mode")).scalar_one()
    assert present == 0
    assert modes == 2
