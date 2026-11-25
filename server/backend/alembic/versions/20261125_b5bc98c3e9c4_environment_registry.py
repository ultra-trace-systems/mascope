"""The environment registry, and where a reference source applies

Phase 3 of ``docs/dev/sample_filters_and_metadata.md`` (section 4.6,
MAS-336). What was sampled is recorded as environment tags, and the tags
select the reference lists through a mapping: people tag samples with
environments, list curators declare regimes in the list headers, and the
registry joins them.

- ``environment``: one row per environment - a key, the value a sample is
  tagged with under the shipped ``environment`` field, a label, the chemistry
  context it reads under (or none), its regimes, whether Mascope ships it,
  and when it was retired. Empty here: the starting set Mascope ships is
  seeded at every start (``api.new.metadata.environment_service``), which also
  writes the registry's keys as the ``environment`` field's allowed values,
  so a release can add an environment with no migration. The downgrade
  leaves the field's values open again, as it was seeded.
- ``reference_source.applies_to_contexts`` (JSON) and ``always_active``
  (Boolean): the two facts of a list's header that say where it applies,
  written at load by the seed and by ``reference sync``.

**NULL on every existing source row.** NULL is what a header that says
neither leaves, and reads as a source that declares no regimes, read for
every sample, as Stage A reads every source today. ``reference seed`` writes
a shipped list's facts on its active row without reloading it.

Revision ID: b5bc98c3e9c4
Revises: 7d2f9b4e6a13
Create Date: 2026-11-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b5bc98c3e9c4"
down_revision: Union[str, Sequence[str], None] = "7d2f9b4e6a13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "environment",
        sa.Column("environment_id", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("chemistry_context", sa.String(length=64), nullable=True),
        sa.Column(
            "regimes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "system", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("modified_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("retired_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("environment_id", name="pk_environment"),
        sa.UniqueConstraint("key", name="uq_environment_key"),
    )
    op.add_column(
        "reference_source",
        sa.Column("applies_to_contexts", sa.JSON(), nullable=True),
    )
    op.add_column(
        "reference_source", sa.Column("always_active", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    # The shipped field's values are the registry's keys; without the registry
    # they are open again, as the field was seeded.
    op.execute(
        sa.text(
            "UPDATE metadata_field SET allowed_values = CAST('[]' AS jsonb) "
            "WHERE key = 'environment' AND system"
        )
    )
    op.drop_column("reference_source", "always_active")
    op.drop_column("reference_source", "applies_to_contexts")
    op.drop_table("environment")
