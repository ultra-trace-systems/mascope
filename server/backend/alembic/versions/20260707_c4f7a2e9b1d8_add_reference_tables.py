"""Add reference chemistry database tables

Adds reference_source (one row per ingested public-database source + version)
and reference_compound (one row per compound per source version) for the public
database integration. reference_compound indexes formula and monoisotopic_mass
(annotation is an indexed lookup) and inchikey (cross-source dedup key), and
carries a nullable charge column so permanently charged species (choline,
quaternary ammoniums) can later be represented - recorded, not matched; see
issue #1726. No data is ingested by this migration - schema only.

Revision ID: c4f7a2e9b1d8
Revises: e4b7a2c8d1f6
Create Date: 2026-07-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4f7a2e9b1d8"
down_revision: Union[str, Sequence[str], None] = "e4b7a2c8d1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reference_source",
        sa.Column(
            "reference_source_id", sa.Integer(), autoincrement=True, nullable=False
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "reference_source_id", name=op.f("pk_reference_source")
        ),
    )
    op.create_index(
        op.f("ix_reference_source_name"),
        "reference_source",
        ["name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reference_source_is_active"),
        "reference_source",
        ["is_active"],
        unique=False,
    )
    op.create_table(
        "reference_compound",
        sa.Column(
            "reference_compound_id", sa.Integer(), autoincrement=True, nullable=False
        ),
        sa.Column("reference_source_id", sa.Integer(), nullable=False),
        sa.Column("formula", sa.String(length=512), nullable=False),
        sa.Column("monoisotopic_mass", sa.Float(), nullable=True),
        sa.Column("charge", sa.Integer(), nullable=True),
        sa.Column("inchikey", sa.String(length=27), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("smiles", sa.Text(), nullable=True),
        sa.Column("inchi", sa.Text(), nullable=True),
        sa.Column("source_native_id", sa.String(length=128), nullable=False),
        sa.Column("xrefs", sa.JSON(), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["reference_source_id"],
            ["reference_source.reference_source_id"],
            name=op.f("fk_reference_compound_reference_source_id_reference_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "reference_compound_id", name=op.f("pk_reference_compound")
        ),
    )
    op.create_index(
        op.f("ix_reference_compound_reference_source_id"),
        "reference_compound",
        ["reference_source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reference_compound_formula"),
        "reference_compound",
        ["formula"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reference_compound_monoisotopic_mass"),
        "reference_compound",
        ["monoisotopic_mass"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reference_compound_inchikey"),
        "reference_compound",
        ["inchikey"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_reference_compound_inchikey"), table_name="reference_compound"
    )
    op.drop_index(
        op.f("ix_reference_compound_monoisotopic_mass"),
        table_name="reference_compound",
    )
    op.drop_index(
        op.f("ix_reference_compound_formula"), table_name="reference_compound"
    )
    op.drop_index(
        op.f("ix_reference_compound_reference_source_id"),
        table_name="reference_compound",
    )
    op.drop_table("reference_compound")
    op.drop_index(op.f("ix_reference_source_is_active"), table_name="reference_source")
    op.drop_index(op.f("ix_reference_source_name"), table_name="reference_source")
    op.drop_table("reference_source")
