"""The metadata field registry: metadata_field and attribute_template_field

Phase 2 of ``docs/dev/sample_filters_and_metadata.md`` (section 4.4, MAS-311).
Acquisition metadata needs typed fields that mean the same on every
instrument, so a filter across instruments reads "site" as one field.

- ``metadata_field``: one row per registry field - a stable key, a label, a
  type (text, number, datetime, enum, tags), a unit, the allowed values of an
  enum or tags field, a description, whether Mascope ships it, and when it was
  retired. Seeded with the one field Mascope ships, ``environment``, a tags
  field whose values the environment registry fills in phase 3.
- ``attribute_template_field``: the registry fields an attribute template
  shows, in order. Its key on the field restricts deletion: a field a template
  selects is in use, and is retired instead.

The free-form labels of existing templates (``attribute_template.template``)
are left as they are: they stay batch annotations and are not migrated.

Revision ID: fa0017bcd993
Revises: e2c7a4b9d6f3
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "fa0017bcd993"
down_revision: Union[str, Sequence[str], None] = "e2c7a4b9d6f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The shipped field's row. Its id is fixed so the seed is the same row on
#: every deployment.
ENVIRONMENT_FIELD = {
    "metadata_field_id": "sysfieldenvironm",
    "key": "environment",
    "label": "Environment",
    "type": "tags",
    "description": (
        "What was sampled: the environment tags that select the reference "
        "lists a sample is read against."
    ),
}


def upgrade() -> None:
    op.create_table(
        "metadata_field",
        sa.Column("metadata_field_id", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column(
            "allowed_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "system", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("modified_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("retired_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "type IN ('text', 'number', 'datetime', 'enum', 'tags')",
            name="ck_metadata_field_type_known",
        ),
        sa.CheckConstraint(
            "allowed_values IS NULL OR type IN ('enum', 'tags')",
            name="ck_metadata_field_allowed_values_for_a_choice",
        ),
        sa.PrimaryKeyConstraint("metadata_field_id", name="pk_metadata_field"),
        sa.UniqueConstraint("key", name="uq_metadata_field_key"),
    )
    op.create_table(
        "attribute_template_field",
        sa.Column("attribute_template_id", sa.String(length=16), nullable=False),
        sa.Column("metadata_field_id", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attribute_template_id"],
            ["attribute_template.attribute_template_id"],
            name="fk_attribute_template_field_attribute_template",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["metadata_field_id"],
            ["metadata_field.metadata_field_id"],
            name="fk_attribute_template_field_metadata_field_id_metadata_field",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "attribute_template_id",
            "metadata_field_id",
            name="pk_attribute_template_field",
        ),
    )
    op.create_index(
        "ix_attribute_template_field_metadata_field_id",
        "attribute_template_field",
        ["metadata_field_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO metadata_field (metadata_field_id, key, label, type, "
            "allowed_values, description, system, created_utc, modified_utc) "
            "VALUES (:metadata_field_id, :key, :label, :type, "
            "CAST('[]' AS jsonb), :description, true, now(), now())"
        ).bindparams(**ENVIRONMENT_FIELD)
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attribute_template_field_metadata_field_id",
        table_name="attribute_template_field",
    )
    op.drop_table("attribute_template_field")
    op.drop_table("metadata_field")
