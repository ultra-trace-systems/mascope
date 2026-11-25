"""Seeded test for `b5bc98c3e9c4`, the environment registry.

The revision makes ``environment`` and adds ``applies_to_contexts`` and
``always_active`` to ``reference_source``. What there is to pin: a
deployment's existing source rows come through with both null, which reads
as a source that declares nothing (the seed writes a shipped list's facts);
the registry starts empty, its shipped set being a start's to seed, and the
shipped field's values stay as they were; a key is taken once; and the
downgrade drops both, leaving the field's values open again.

Rows are seeded through raw SQL, as the other seeded migration tests are: the
ORM maps the schema at head.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "b5bc98c3e9c4"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A deployment's loaded list, then the migration applied."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reference_source (reference_source_id, name, version, "
                "license, record_count, is_active, ingested_at) VALUES (1, "
                "'monoterpene-hom-kang2021', '2026.09', 'CC-BY-4.0', 4, true, "
                ":ingested)"
            ),
            {"ingested": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)},
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_an_existing_source_row_declares_nothing(upgraded: Engine):
    with upgraded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT applies_to_contexts IS NULL AS no_regimes, "
                "always_active IS NULL AS not_said FROM reference_source"
            )
        ).one()
    assert row.no_regimes is True
    assert row.not_said is True


def test_the_registry_starts_empty_and_the_field_as_it_was(upgraded: Engine):
    with upgraded.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM environment")).scalar_one()
        values = conn.execute(
            text("SELECT allowed_values FROM metadata_field WHERE key = 'environment'")
        ).scalar_one()
    assert count == 0
    assert values == []


def test_an_environment_defaults_and_a_key_is_taken_once(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO environment (environment_id, key, label, created_utc, "
                "modified_utc) VALUES ('envcoast00000001', 'coast', 'Coast', now(), "
                "now())"
            )
        )
        row = conn.execute(
            text(
                "SELECT regimes, system, chemistry_context, retired_utc "
                "FROM environment WHERE key = 'coast'"
            )
        ).one()
    assert row.regimes == []
    assert row.system is False
    assert row.chemistry_context is None
    assert row.retired_utc is None
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO environment (environment_id, key, label, "
                    "created_utc, modified_utc) VALUES ('envcoast00000002', "
                    "'coast', 'Again', now(), now())"
                )
            )


def test_the_downgrade_drops_the_registry_and_the_columns(
    upgraded: Engine, seeded_alembic_config: Config
):
    """Run last: it leaves the database at the prior revision."""
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE metadata_field SET allowed_values = "
                "CAST('[\"coast\"]' AS jsonb) WHERE key = 'environment'"
            )
        )
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    inspector = inspect(upgraded)
    assert "environment" not in set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("reference_source")}
    assert not {"applies_to_contexts", "always_active"} & columns
    with upgraded.connect() as conn:
        values = conn.execute(
            text("SELECT allowed_values FROM metadata_field WHERE key = 'environment'")
        ).scalar_one()
        sources = conn.execute(
            text("SELECT count(*) FROM reference_source")
        ).scalar_one()
    assert values == []
    assert sources == 1

    # And up again, from the state the downgrade left.
    upgrade(seeded_alembic_config, REVISION)
    assert "environment" in set(inspect(upgraded).get_table_names())
