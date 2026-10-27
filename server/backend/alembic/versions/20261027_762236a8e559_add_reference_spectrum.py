"""Add the reference_spectrum table, and the kind of a reference source's load

Adds ``reference_spectrum``, one row per MS/MS spectrum of an open spectral
library (MassBank, MoNA, GNPS) per source version, beside
``reference_compound``: the compound's name, canonical formula, InChIKey and
structure, the precursor m/z, adduct and polarity, the collision energy as a
number with its kind and as the library wrote it, the centroid list as JSON
and the per-record licence. ``precursor_mz`` and ``inchikey`` are indexed for
the two lookups a scorer makes, ``formula`` for a third, ``reference_source_id``
for the loads. No data is loaded by this migration.

A spectral library is loaded as a versioned load on a ``reference_source``
row, as a compound source is, so the row now records which it holds:
``kind``, ``compound`` or ``spectrum``. Every existing row is a compound
source, which the column's default backfills.

The downgrade drops the table and, before the column, the source rows of
spectral loads: without their kind they would read as compound sources with
records and no compounds.

Revision ID: 762236a8e559
Revises: a7d3c91e5b26
Create Date: 2026-10-27 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "762236a8e559"
down_revision: Union[str, Sequence[str], None] = "a7d3c91e5b26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reference_source",
        sa.Column(
            "kind",
            sa.String(length=16),
            server_default=sa.text("'compound'"),
            nullable=False,
        ),
    )
    op.create_table(
        "reference_spectrum",
        sa.Column(
            "reference_spectrum_id", sa.Integer(), autoincrement=True, nullable=False
        ),
        sa.Column("reference_source_id", sa.Integer(), nullable=False),
        sa.Column("source_native_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("formula", sa.String(length=512), nullable=True),
        sa.Column("inchikey", sa.String(length=27), nullable=True),
        sa.Column("smiles", sa.Text(), nullable=True),
        sa.Column("inchi", sa.Text(), nullable=True),
        sa.Column("precursor_mz", sa.Float(), nullable=False),
        sa.Column("adduct", sa.String(length=64), nullable=True),
        sa.Column("polarity", sa.String(length=8), nullable=True),
        sa.Column("collision_energy", sa.Float(), nullable=True),
        sa.Column("collision_energy_kind", sa.String(length=8), nullable=True),
        sa.Column("collision_energy_text", sa.String(length=128), nullable=True),
        sa.Column("peaks", sa.JSON(), nullable=False),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["reference_source_id"],
            ["reference_source.reference_source_id"],
            name=op.f("fk_reference_spectrum_reference_source_id_reference_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "reference_spectrum_id", name=op.f("pk_reference_spectrum")
        ),
    )
    for column in ("reference_source_id", "formula", "inchikey", "precursor_mz"):
        op.create_index(
            op.f(f"ix_reference_spectrum_{column}"),
            "reference_spectrum",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in ("precursor_mz", "inchikey", "formula", "reference_source_id"):
        op.drop_index(
            op.f(f"ix_reference_spectrum_{column}"), table_name="reference_spectrum"
        )
    op.drop_table("reference_spectrum")
    op.execute("DELETE FROM reference_source WHERE kind = 'spectrum'")
    op.drop_column("reference_source", "kind")
