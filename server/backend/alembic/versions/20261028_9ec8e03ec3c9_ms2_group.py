"""Add ms2_group, the inventory of each file's MS2 groups

A file's MS2 scans are grouped by polarity, parent peak and activation in its
MS2 store (``mascope_signal.ms2_store``), and which samples of a batch have
MS2, of which precursors, at what energies, cost one read per sample: the
store, or the raw file for a file that has none. ``ms2_group`` holds one row
per group of a file, so that it is one query (``docs/dev/ms2_analysis.md``,
section 2.3).

**The table starts empty.** Its rows are read from a file's MS2 store, which a
migration does not open: they are written when the store is - at a file's
registration after its conversion, and when re-processing writes it again -
and ``backfill_ms2_store`` fills them for every file whose store was written
before. Until it has run, a file with a store lists no group.

**Where a row belongs.** One row per (file, polarity, group key): the key is
the group's one string form, the parent peak m/z with four decimals, ``@``,
the activation, and is unique within a polarity of a file because the store
merges parent peaks closer than its tolerance into one. The key leads with the
file, so it is also the index that finds a file's and a sample's groups, and
that the file's ON DELETE CASCADE walks. A file's rows are replaced whole
whenever its store is written.

``located_precursor_mz`` is NULL on every row: the step that locates a
group's precursor among the MS1 peaks writes it.

Revision ID: 9ec8e03ec3c9
Revises: f08b30f61bac
Create Date: 2026-10-28 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9ec8e03ec3c9"
down_revision: Union[str, Sequence[str], None] = "f08b30f61bac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ms2_group",
        sa.Column("sample_file_id", sa.String(length=16), nullable=False),
        sa.Column("polarity", sa.String(length=1), nullable=False),
        sa.Column("precursor_key", sa.String(length=256), nullable=False),
        sa.Column("parent_peak_mz", sa.Float(), nullable=False),
        # "" when the scans' filters name none
        sa.Column("activation", sa.String(length=256), nullable=False),
        sa.Column("activation_kind", sa.String(length=64), nullable=True),
        sa.Column("nce", sa.Float(), nullable=True),
        # The HCD energies [V], one per step within a scan
        sa.Column("hcd_energy", sa.JSON(), nullable=False),
        sa.Column("isolation_widths", sa.JSON(), nullable=False),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("t_min", sa.Float(), nullable=False),
        sa.Column("t_max", sa.Float(), nullable=False),
        sa.Column("low_mass", sa.Float(), nullable=True),
        sa.Column("high_mass", sa.Float(), nullable=True),
        sa.Column("located_precursor_mz", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sample_file_id"],
            ["sample_file.sample_file_id"],
            name="fk_ms2_group_sample_file_id_sample_file",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "sample_file_id", "polarity", "precursor_key", name="pk_ms2_group"
        ),
    )


def downgrade() -> None:
    # The inventory is read again from the stores: an upgrade and a backfill
    # run restore it.
    op.drop_table("ms2_group")
