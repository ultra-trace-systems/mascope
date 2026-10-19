"""Seeded test for hashing the stored access tokens in `a7d3c91e5b26`.

The stairway runs the revision on an empty table, which says nothing about
whether a token issued before it still authenticates after it. So tokens are
seeded in clear at the previous revision, the upgrade runs over them, and each
must then be found by the digest the server computes for the token its holder
presents (``hash_access_token``, which every lookup goes through), with no
copy of the token left in the row.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
a migration test has to describe the schema as it was, not as the models are
now.
"""

import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mascope_backend.db import hash_access_token


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "a7d3c91e5b26"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

# Tokens as the strategy mints them (token_urlsafe, 43 characters), and the
# fixed shorter ones the demo seeder writes.
_TOKENS = {
    secrets.token_urlsafe(): "mascope_sdk",
    secrets.token_urlsafe(): "file-converter",
    secrets.token_urlsafe(): "file-agent",
    "mascope_demo_sdk_token": "mascope_sdk",
}


def _columns(conn) -> dict[str, tuple[str, int | None]]:
    """Column name -> (type, length), for ``access_token``."""
    rows = conn.execute(
        text(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns WHERE table_name = 'access_token'"
        )
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _primary_key(conn) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'access_token'::regclass AND i.indisprimary"
        )
    ).all()
    return [row[0] for row in rows]


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed tokens in clear at the prior revision, upgrade, read the table.

    :return: The rows and schema as the upgrade left them.
    :rtype: dict
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        role_id = conn.execute(
            text("INSERT INTO role (role_name) VALUES ('token-test') RETURNING role_id")
        ).scalar()
        user_id = conn.execute(
            text(
                'INSERT INTO "user" (email, username, hashed_password, is_active, '
                "is_superuser, is_verified, role_id, registered_at) VALUES ("
                "'token@test.com', "
                "'token-test', 'x', true, false, true, :role, :now) RETURNING id"
            ),
            {"role": role_id, "now": _NOW},
        ).scalar()
        for token, service in _TOKENS.items():
            conn.execute(
                text(
                    "INSERT INTO access_token (token, user_id, service_name, "
                    "created_at) VALUES (:token, :user, :service, :now)"
                ),
                {"token": token, "user": user_id, "service": service, "now": _NOW},
            )
    upgrade(seeded_alembic_config, REVISION)

    with seeded_engine.connect() as conn:
        return {
            "version": conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar(),
            "columns": _columns(conn),
            "primary_key": _primary_key(conn),
            "rows": [
                dict(row._mapping)
                for row in conn.execute(text("SELECT * FROM access_token")).all()
            ],
            "user_id": user_id,
        }


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    """Step back to the prior revision and read the table again.

    Depends on `migrated` so the upgrade is always the thing being undone.
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        return {
            "columns": _columns(conn),
            "primary_key": _primary_key(conn),
            "rows": conn.execute(text("SELECT count(*) FROM access_token")).scalar(),
        }


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_the_plaintext_column_goes_and_the_digest_is_the_key(migrated: dict) -> None:
    assert "token" not in migrated["columns"]
    assert migrated["columns"]["token_hash"] == ("character varying", 64)
    assert migrated["primary_key"] == ["token_hash"]


def test_a_token_issued_before_the_upgrade_is_found_by_its_digest(
    migrated: dict,
) -> None:
    """Each row is the one the server finds for the token its holder presents,
    with its owner, service and age kept."""
    by_hash = {row["token_hash"]: row for row in migrated["rows"]}
    assert len(by_hash) == len(_TOKENS)
    for token, service in _TOKENS.items():
        row = by_hash[hash_access_token(token)]
        assert row["service_name"] == service
        assert row["user_id"] == migrated["user_id"]
        assert row["created_at"] == _NOW


def test_no_row_holds_its_token(migrated: dict) -> None:
    for row in migrated["rows"]:
        for token in _TOKENS:
            assert token not in {str(value) for value in row.values()}


def test_the_downgrade_restores_the_column_and_revokes_every_token(
    downgraded: dict,
) -> None:
    """A digest gives no token back: the rows go, the schema comes back."""
    assert downgraded["columns"]["token"] == ("character varying", 43)
    assert "token_hash" not in downgraded["columns"]
    assert downgraded["primary_key"] == ["token"]
    assert downgraded["rows"] == 0
