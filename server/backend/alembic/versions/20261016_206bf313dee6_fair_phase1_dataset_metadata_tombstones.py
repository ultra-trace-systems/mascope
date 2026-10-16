"""FAIR Phase 1: dataset metadata, dataset tombstones, compound identity

The one schema change of FAIR Phase 1 (``docs/dev/handoff_fair_phase1.md``
section 5). Three parts, none of which the code reads yet:

- ``dataset`` gains its descriptive metadata: ``license`` (free text) and the
  JSON lists ``authors``, ``keywords``, ``related_identifiers`` and
  ``funding``.
- ``target_compound`` gains its structure identity: ``inchikey`` (indexed,
  the link to ``reference_compound.inchikey``), ``inchi`` and ``smiles``.
- ``dataset_tombstone`` is created: a snapshot of each deleted dataset, with
  no foreign keys so that it outlives the dataset and its workspace.

Every added column is nullable and has no default, so each ``ADD COLUMN`` is
a catalogue change without a table rewrite, and there is no backfill: an
existing dataset has no metadata and an existing compound no identity until
someone supplies them.

Revision ID: 206bf313dee6
Revises: e3a9c5f17d42
Create Date: 2026-10-16 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "206bf313dee6"
down_revision: Union[str, Sequence[str], None] = "e3a9c5f17d42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DATASET_JSON = ("authors", "keywords", "related_identifiers", "funding")

# What the models' naming convention produces (``models.NAMING_CONVENTION``),
# so the drift test sees one schema. op.create_index takes the name as
# written. Inside op.create_table the convention is applied, as it is to the
# models: there the check constraint is named bare and becomes
# ``ck_dataset_tombstone_deleted_via_known``, and the primary key
# ``pk_dataset_tombstone``; a ``ck_`` name written there would be prefixed
# twice.
_INCHIKEY_INDEX = "ix_target_compound_inchikey"


def upgrade() -> None:
    op.add_column("dataset", sa.Column("license", sa.String(length=64), nullable=True))
    for name in _DATASET_JSON:
        op.add_column("dataset", sa.Column(name, sa.JSON(), nullable=True))

    op.add_column(
        "target_compound", sa.Column("inchikey", sa.String(length=27), nullable=True)
    )
    op.add_column("target_compound", sa.Column("inchi", sa.Text(), nullable=True))
    op.add_column("target_compound", sa.Column("smiles", sa.Text(), nullable=True))
    op.create_index(_INCHIKEY_INDEX, "target_compound", ["inchikey"])

    op.create_table(
        "dataset_tombstone",
        sa.Column("dataset_id", sa.String(length=16), nullable=False),
        sa.Column("workspace_id", sa.String(length=16), nullable=True),
        sa.Column("workspace_name", sa.String(length=256), nullable=True),
        sa.Column("dataset_name", sa.String(length=256), nullable=True),
        sa.Column("dataset_description", sa.Text(), nullable=True),
        sa.Column("dataset_type", sa.String(length=64), nullable=True),
        sa.Column("instrument", sa.String(length=64), nullable=True),
        sa.Column("descriptive_metadata", sa.JSON(), nullable=True),
        sa.Column("n_sample_batches", sa.Integer(), nullable=True),
        sa.Column("n_sample_items", sa.Integer(), nullable=True),
        sa.Column("n_sample_files", sa.Integer(), nullable=True),
        sa.Column("dataset_utc_created", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dataset_utc_modified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_by", sa.Text(), nullable=True),
        sa.Column("deleted_via", sa.String(length=16), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("dataset_id"),
        sa.CheckConstraint(
            "deleted_via IN ('dataset', 'workspace')", name="deleted_via_known"
        ),
    )


def downgrade() -> None:
    op.drop_table("dataset_tombstone")

    op.drop_index(_INCHIKEY_INDEX, table_name="target_compound")
    op.drop_column("target_compound", "smiles")
    op.drop_column("target_compound", "inchi")
    op.drop_column("target_compound", "inchikey")

    for name in reversed(_DATASET_JSON):
        op.drop_column("dataset", name)
    op.drop_column("dataset", "license")
