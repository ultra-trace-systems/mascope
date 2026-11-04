"""Seeded test for `7d4b2a9e6c13`, which makes a mode a versioned setup.

Every existing mode becomes its own version 1, active, superseding nothing,
and the references to it stay where they are (MAS-351, mint and repoint: the
rows they point at are the version 1s). The token and system-key uniqueness
moves to the active versions, so a superseded version may keep the token and
the key its successor has, and the downgrade refuses while one exists.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
at PRIOR_REVISION the columns this suite is about do not exist yet.
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

REVISION = "7d4b2a9e6c13"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_MODE_SQL = """
    INSERT INTO ionization_mode (ionization_mode_id, ionization_mode_name,
        ionization_mode_token, ionization_mode_polarity,
        ionization_mechanism_ids, system_key, version, is_active, supersedes_id)
    VALUES (:id, :name, :token, '-', CAST('[]' AS json), :key, :version,
            :active, :supersedes)
"""


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ionization_mode (ionization_mode_id,
                    ionization_mode_name, ionization_mode_token,
                    ionization_mode_polarity, ionization_mechanism_ids,
                    system_key)
                VALUES ('modeexisting0001', 'Existing', 'exist', '-',
                        CAST('[]' AS json), NULL),
                       ('modeshipped00001', 'Shipped', NULL, '-',
                        CAST('[]' AS json), 'shipped-key')
                """
            )
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_every_existing_mode_is_its_own_first_version(upgraded: Engine):
    with upgraded.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ionization_mode_id, version, is_active, supersedes_id"
                " FROM ionization_mode ORDER BY ionization_mode_id"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("modeexisting0001", 1, True, None),
        ("modeshipped00001", 1, True, None),
    ]


def test_a_superseded_version_keeps_its_token_and_key(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE ionization_mode SET is_active = false"
                " WHERE ionization_mode_id IN ('modeexisting0001', 'modeshipped00001')"
            )
        )
        for mode_id, token, key, supersedes in (
            ("modeexisting0002", "exist", None, "modeexisting0001"),
            ("modeshipped00002", None, "shipped-key", "modeshipped00001"),
        ):
            conn.execute(
                text(_MODE_SQL),
                {
                    "id": mode_id,
                    "name": mode_id,
                    "token": token,
                    "key": key,
                    "version": 2,
                    "active": True,
                    "supersedes": supersedes,
                },
            )


@pytest.mark.parametrize(
    ("token", "key"), [("exist", None), (None, "shipped-key")], ids=["token", "key"]
)
def test_two_active_versions_may_not_share_them(upgraded: Engine, token, key):
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(_MODE_SQL),
                {
                    "id": "modeclash0000001",
                    "name": "Clash",
                    "token": token,
                    "key": key,
                    "version": 1,
                    "active": True,
                    "supersedes": None,
                },
            )


def test_downgrade_refuses_while_a_version_is_superseded(
    upgraded: Engine, seeded_alembic_config: Config
):
    with pytest.raises(RuntimeError, match="superseded"):
        downgrade(seeded_alembic_config, PRIOR_REVISION)


def test_downgrade_restores_the_old_constraints_once_none_is(
    upgraded: Engine, seeded_alembic_config: Config
):
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM ionization_mode WHERE NOT is_active"))
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'ionization_mode'"
                )
            )
        }
    assert not {"version", "is_active", "supersedes_id"} & columns
