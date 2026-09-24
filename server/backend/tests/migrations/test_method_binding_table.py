"""Seeded test for `4f8b2e6d9c17`, which adds the method_binding table.

The table starts empty and nothing else changes, so what there is to pin is
that it can hold what the learner writes: one row per binding key and no
more, a row that survives the mode it points at being deleted, and a
signature class as wide as a pooled multi-stream method makes it.
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

REVISION = "4f8b2e6d9c17"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_MODE_ID = "bindingmodeid001"


def _insert(conn, binding_id: str, key: str, mode_id: str | None = _MODE_ID) -> None:
    conn.execute(
        text(
            "INSERT INTO method_binding ("
            "  method_binding_id, binding_key, instrument, method_key,"
            "  signature_class, ionization_mode_id, chemistry_keys, state,"
            "  source, first_seen, last_seen, n_streams, n_disagreements"
            ") VALUES (:id, :key, 'instrument-A', 'nitrate.meth',"
            "          'FTMS - p NSI Full ms', :mode, CAST(:chem AS json),"
            "          'learned', 'token', now(), now(), 1, 0)"
        ),
        {"id": binding_id, "key": key, "mode": mode_id, "chem": '["mech-a"]'},
    )


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A mode to bind to, then the migration applied."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mode ("
                "  ionization_mode_id, ionization_mode_name,"
                "  ionization_mode_polarity, ionization_mechanism_ids"
                ") VALUES (:id, 'Nitrate binding test', '-',"
                "          CAST('[]' AS json))"
            ),
            {"id": _MODE_ID},
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_table_arrives_empty(upgraded: Engine):
    with upgraded.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM method_binding")).scalar_one() == 0
        )


def test_one_row_per_binding_key(upgraded: Engine):
    """The key is what keeps two workers from learning the same method twice."""
    with upgraded.begin() as conn:
        _insert(conn, "mbfirstrow000001", "digest-one")
    with pytest.raises(Exception):
        with upgraded.begin() as conn:
            _insert(conn, "mbsecondrow00001", "digest-one")
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM method_binding"))


def test_deleting_the_mode_keeps_the_history(upgraded: Engine):
    """chemistry_keys still says what the method was seen running."""
    with upgraded.begin() as conn:
        _insert(conn, "mbkeepshistory01", "digest-two")
        conn.execute(
            text("DELETE FROM ionization_mode WHERE ionization_mode_id = :id"),
            {"id": _MODE_ID},
        )
        row = conn.execute(
            text(
                "SELECT ionization_mode_id, chemistry_keys FROM method_binding"
                " WHERE binding_key = 'digest-two'"
            )
        ).one()
    assert row[0] is None
    assert row[1] == ["mech-a"]
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM method_binding"))
        # Put it back: the fixture is module-scoped, so a later test in this
        # file would otherwise find the mode gone.
        conn.execute(
            text(
                "INSERT INTO ionization_mode ("
                "  ionization_mode_id, ionization_mode_name,"
                "  ionization_mode_polarity, ionization_mechanism_ids"
                ") VALUES (:id, 'Nitrate binding test', '-',"
                "          CAST('[]' AS json))"
            ),
            {"id": _MODE_ID},
        )


def test_a_pooled_method_fits_its_signature_class(upgraded: Engine):
    """Two scan ranges in one polarity are one class, and it is long."""
    signature = " + ".join(
        [
            f"FTMS - p NSI Full ms [{low}.0000-{low + 50}.0000] R=120000"
            for low in range(50, 400, 50)
        ]
    )
    assert len(signature) > 256
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO method_binding ("
                "  method_binding_id, binding_key, instrument, method_key,"
                "  signature_class, ionization_mode_id, chemistry_keys, state,"
                "  source, first_seen, last_seen, n_streams, n_disagreements"
                ") VALUES ('mbwidesig0000001', 'digest-wide', 'instrument-A',"
                "          'nitrate.meth', :signature, NULL,"
                "          CAST('[]' AS json), 'learned', 'token', now(),"
                "          now(), 1, 0)"
            ),
            {"signature": signature},
        )
        stored = conn.execute(
            text(
                "SELECT signature_class FROM method_binding"
                " WHERE binding_key = 'digest-wide'"
            )
        ).scalar_one()
    assert stored == signature
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM method_binding"))


def test_the_downgrade_drops_the_table(upgraded: Engine, seeded_alembic_config: Config):
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        present = conn.execute(
            text("SELECT to_regclass('public.method_binding')")
        ).scalar_one()
    assert present is None
    upgrade(seeded_alembic_config, REVISION)
