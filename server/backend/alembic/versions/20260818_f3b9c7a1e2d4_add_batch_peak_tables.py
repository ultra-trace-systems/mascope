"""Add batch peak tables

Introduces the batch-level layer of the peak-centric paradigm
(docs/dev/peak_assignment_batch.md):

- batch_peak: a frozen cross-sample m/z anchor -- the stable identity the batch
  overview draws one trace per (the peak-centric replacement for target_ion_id).
  Partitioned per ionization mode; carries the evidence-weighted consensus of its
  members' per-sample assignments (formula, tier, support, prevalence).
- batch_peak_occurrence: the sparse per-sample matrix. One row per observed peak
  folded into a batch peak; sample_peak_id equals PeakAssignment.sample_peak_id so
  a member's per-sample assignment joins for free. Unique on
  (batch_peak_id, sample_item_id): one member (one y-value) per sample per peak.

Revision ID: f3b9c7a1e2d4
Revises: b7e4c9a2f1d3
Create Date: 2026-07-12 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f3b9c7a1e2d4"
down_revision: Union[str, Sequence[str], None] = "b7e4c9a2f1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_peak",
        sa.Column("batch_peak_id", sa.String(length=16), nullable=False),
        sa.Column("sample_batch_id", sa.String(length=16), nullable=False),
        sa.Column("ionization_mode_id", sa.String(length=16), nullable=True),
        sa.Column("mz", sa.Float(), nullable=False),
        sa.Column("mz_tol_ppm", sa.Float(), nullable=False),
        sa.Column("intensity_variable", sa.String(length=32), nullable=True),
        sa.Column("consensus_formula", sa.String(length=256), nullable=True),
        sa.Column("consensus_ion_formula", sa.String(length=4096), nullable=True),
        sa.Column("ionization_mechanism_id", sa.String(length=16), nullable=True),
        sa.Column(
            "consensus_tier",
            sa.String(length=24),
            server_default=sa.text("'unassigned'"),
            nullable=False,
        ),
        sa.Column("best_fit_score", sa.Float(), nullable=True),
        sa.Column("support_fraction", sa.Float(), nullable=True),
        sa.Column(
            "n_present", sa.Integer(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column(
            "is_ambiguous", sa.Integer(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column("alternatives", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("batch_peak_utc_created", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "batch_peak_utc_modified", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.CheckConstraint(
            "best_fit_score IS NULL OR best_fit_score BETWEEN 0 AND 1",
            name=op.f("ck_batch_peak_best_fit_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["ionization_mechanism_id"],
            ["ionization_mechanism.ionization_mechanism_id"],
            name=op.f("fk_batch_peak_ionization_mechanism_id_ionization_mechanism"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ionization_mode_id"],
            ["ionization_mode.ionization_mode_id"],
            name=op.f("fk_batch_peak_ionization_mode_id_ionization_mode"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sample_batch_id"],
            ["sample_batch.sample_batch_id"],
            name=op.f("fk_batch_peak_sample_batch_id_sample_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_peak_id", name=op.f("pk_batch_peak")),
    )
    op.create_index(
        op.f("ix_batch_peak_ionization_mechanism_id"),
        "batch_peak",
        ["ionization_mechanism_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_ionization_mode_id"),
        "batch_peak",
        ["ionization_mode_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_sample_batch_id"),
        "batch_peak",
        ["sample_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_sample_batch_id_mz"),
        "batch_peak",
        ["sample_batch_id", "ionization_mode_id", "mz"],
        unique=False,
    )
    op.create_table(
        "batch_peak_occurrence",
        sa.Column("batch_peak_occurrence_id", sa.String(length=32), nullable=False),
        sa.Column("batch_peak_id", sa.String(length=16), nullable=False),
        sa.Column("sample_item_id", sa.String(length=16), nullable=False),
        sa.Column("sample_peak_id", sa.String(length=20), nullable=False),
        sa.Column("peak_assignment_id", sa.String(length=32), nullable=True),
        sa.Column("sample_peak_mz", sa.Float(), nullable=False),
        sa.Column("intensity", sa.Float(), nullable=True),
        sa.Column("tier", sa.String(length=24), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("assigned_formula", sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_peak_id"],
            ["batch_peak.batch_peak_id"],
            name=op.f("fk_batch_peak_occurrence_batch_peak_id_batch_peak"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["peak_assignment_id"],
            ["peak_assignment.peak_assignment_id"],
            name=op.f("fk_batch_peak_occurrence_peak_assignment_id_peak_assignment"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sample_item_id"],
            ["sample_item.sample_item_id"],
            name=op.f("fk_batch_peak_occurrence_sample_item_id_sample_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "batch_peak_occurrence_id", name=op.f("pk_batch_peak_occurrence")
        ),
        sa.UniqueConstraint(
            "batch_peak_id",
            "sample_item_id",
            name="uq_batch_peak_occurrence_batch_peak_id_sample_item_id",
        ),
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_batch_peak_id"),
        "batch_peak_occurrence",
        ["batch_peak_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"),
        "batch_peak_occurrence",
        ["peak_assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_sample_item_id"),
        "batch_peak_occurrence",
        ["sample_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_batch_peak_occurrence_sample_item_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_index(
        op.f("ix_batch_peak_occurrence_batch_peak_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_table("batch_peak_occurrence")
    op.drop_index(op.f("ix_batch_peak_sample_batch_id_mz"), table_name="batch_peak")
    op.drop_index(op.f("ix_batch_peak_sample_batch_id"), table_name="batch_peak")
    op.drop_index(op.f("ix_batch_peak_ionization_mode_id"), table_name="batch_peak")
    op.drop_index(
        op.f("ix_batch_peak_ionization_mechanism_id"), table_name="batch_peak"
    )
    op.drop_table("batch_peak")
