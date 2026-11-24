"""Seeded test for `fa0017bcd993`, the metadata field registry.

The revision makes ``metadata_field`` and ``attribute_template_field`` and
seeds the one field Mascope ships, ``environment``. What there is to pin: the
seed is the shipped field as the registry's service reads one (a tags field,
its values open until the environment registry fills them, marked
``system``); a deployment's existing attribute templates come through as they
were, their labels batch annotations still and selecting no field; the
constraints refuse what the service refuses; and the downgrade drops both
tables and leaves the templates.

Rows are seeded through raw SQL, as the other seeded migration tests are: the
ORM maps the schema at head.
"""

import json
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

REVISION = "fa0017bcd993"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_TEMPLATE_ID = "tmplregistry0001"
_LABELS = [{"label": "Group", "required": True, "value": "A"}]


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A deployment's own attribute template, then the migration applied."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO attribute_template (attribute_template_id, name, "
                "type, template) VALUES (:id, 'Groups', 'sample_item', "
                "CAST(:template AS json))"
            ),
            {"id": _TEMPLATE_ID, "template": json.dumps(_LABELS)},
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_shipped_field_is_seeded(upgraded: Engine):
    with upgraded.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT metadata_field_id, key, label, type, unit, "
                    "allowed_values, system, retired_utc, created_utc "
                    "FROM metadata_field"
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    (row,) = rows
    assert row["metadata_field_id"] == "sysfieldenvironm"
    assert row["key"] == "environment"
    assert row["label"] == "Environment"
    assert row["type"] == "tags"
    assert row["unit"] is None
    assert row["allowed_values"] == []
    assert row["system"] is True
    assert row["retired_utc"] is None
    assert row["created_utc"] is not None


def test_an_existing_template_selects_no_field(upgraded: Engine):
    with upgraded.connect() as conn:
        template = conn.execute(
            text(
                "SELECT template FROM attribute_template WHERE attribute_template_id = :id"
            ),
            {"id": _TEMPLATE_ID},
        ).scalar_one()
        selected = conn.execute(
            text("SELECT count(*) FROM attribute_template_field")
        ).scalar_one()
    assert template == _LABELS
    assert selected == 0


def test_a_new_field_is_not_a_system_one(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO metadata_field (metadata_field_id, key, label, type, "
                "created_utc, modified_utc) VALUES ('fieldsite0000001', 'site', "
                "'Site', 'text', now(), now())"
            )
        )
        system = conn.execute(
            text("SELECT system FROM metadata_field WHERE key = 'site'")
        ).scalar_one()
        conn.execute(text("DELETE FROM metadata_field WHERE key = 'site'"))
    assert system is False


@pytest.mark.parametrize(
    "values",
    [
        # A key the shipped field already has
        "'fielddupekey0001', 'environment', 'Again', 'tags', NULL",
        # A type the registry does not know
        "'fieldcolour00001', 'colour', 'Colour', 'colour', NULL",
        # Allowed values on a field that is not a choice
        "'fieldtextvals001', 'site', 'Site', 'text', CAST('[\"a\"]' AS jsonb)",
    ],
)
def test_the_constraints_refuse_a_bad_field(upgraded: Engine, values: str):
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO metadata_field (metadata_field_id, key, label, "
                    "type, allowed_values, created_utc, modified_utc) "
                    f"VALUES ({values}, now(), now())"
                )
            )


def test_a_selected_field_cannot_be_deleted_and_a_template_can(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO attribute_template_field (attribute_template_id, "
                "metadata_field_id, position) VALUES (:template, "
                "'sysfieldenvironm', 0)"
            ),
            {"template": _TEMPLATE_ID},
        )
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(text("DELETE FROM metadata_field WHERE key = 'environment'"))

    # Deleting the template takes its selection with it.
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO attribute_template (attribute_template_id, name, "
                "type, template) VALUES ('tmplregistry0002', 'Gone', "
                "'sample_item', CAST('[]' AS json))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO attribute_template_field (attribute_template_id, "
                "metadata_field_id, position) VALUES ('tmplregistry0002', "
                "'sysfieldenvironm', 0)"
            )
        )
        conn.execute(
            text(
                "DELETE FROM attribute_template "
                "WHERE attribute_template_id = 'tmplregistry0002'"
            )
        )
        left = conn.execute(
            text(
                "SELECT count(*) FROM attribute_template_field "
                "WHERE attribute_template_id = 'tmplregistry0002'"
            )
        ).scalar_one()
    assert left == 0


def test_the_downgrade_drops_the_registry_and_keeps_the_templates(
    upgraded: Engine, seeded_alembic_config: Config
):
    """Run last: it leaves the database at the prior revision."""
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    tables = set(inspect(upgraded).get_table_names())
    assert "metadata_field" not in tables
    assert "attribute_template_field" not in tables
    with upgraded.connect() as conn:
        template = conn.execute(
            text(
                "SELECT template FROM attribute_template WHERE attribute_template_id = :id"
            ),
            {"id": _TEMPLATE_ID},
        ).scalar_one()
    assert template == _LABELS

    # And up again, from the state the downgrade left.
    upgrade(seeded_alembic_config, REVISION)
    with upgraded.connect() as conn:
        keys = conn.execute(text("SELECT key FROM metadata_field")).scalars().all()
    assert keys == ["environment"]
