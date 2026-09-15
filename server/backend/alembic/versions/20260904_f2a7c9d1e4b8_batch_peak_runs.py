"""Batch runs: the batch ledger's history, with per-anchor snapshots.

One batch_peak_run per batch-level operation that rewrote a batch's ledger (a
rebuild, an untargeted search with its parameters, an import) or, implicitly,
the folds that built it; exactly one is current per batch, the one whose state
the live anchors and members hold. When a new run starts, the current run's
state is captured into batch_peak_run_anchor: one row per anchor with its
consensus and its members as parallel arrays - columnar, because a snapshot is
written once and read whole, and the arrays cost a small fraction of a row per
member (docs/dev/peak_assignment_batch_primary.md section 6.5).

batch_peak_id on the snapshot carries no foreign key on purpose: the anchor may
be deleted by a later re-fold, and the snapshot is exactly what should outlive
that. The feature is not in production, so there is nothing to backfill.

Revision ID: f2a7c9d1e4b8
Revises: b8f3d7c2e9a1
Create Date: 2026-09-04 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2a7c9d1e4b8"
down_revision: Union[str, None] = "b8f3d7c2e9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_peak_run",
        sa.Column("batch_peak_run_id", sa.String(length=16), nullable=False),
        sa.Column("sample_batch_id", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "engine",
            sa.String(length=64),
            server_default=sa.text("'mascope'"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column(
            "is_current", sa.Integer(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "batch_peak_run_utc_created", sa.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column(
            "batch_peak_run_utc_completed", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("snapshot_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('fold', 'rebuild', 'search_untargeted', 'import')",
            name=op.f("ck_batch_peak_run_action_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_batch_peak_run_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_batch_id"],
            ["sample_batch.sample_batch_id"],
            name=op.f("fk_batch_peak_run_sample_batch_id_sample_batch"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_batch_peak_run_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("batch_peak_run_id", name=op.f("pk_batch_peak_run")),
    )
    op.create_index(
        op.f("ix_batch_peak_run_sample_batch_id"),
        "batch_peak_run",
        ["sample_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_run_created_by"),
        "batch_peak_run",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_batch_peak_run_batch_current",
        "batch_peak_run",
        ["sample_batch_id", "is_current"],
        unique=False,
    )
    op.create_table(
        "batch_peak_run_anchor",
        sa.Column("batch_peak_run_id", sa.String(length=16), nullable=False),
        sa.Column("batch_peak_id", sa.String(length=16), nullable=False),
        sa.Column("mz", sa.Float(), nullable=False),
        sa.Column("ionization_mode_id", sa.String(length=16), nullable=True),
        sa.Column("consensus_formula", sa.String(length=256), nullable=True),
        sa.Column("consensus_ion_formula", sa.String(length=4096), nullable=True),
        sa.Column("ionization_mechanism_id", sa.String(length=16), nullable=True),
        sa.Column("consensus_tier", sa.String(length=24), nullable=False),
        sa.Column("best_fit_score", sa.Float(), nullable=True),
        sa.Column("support_fraction", sa.Float(), nullable=True),
        sa.Column("n_present", sa.Integer(), nullable=False),
        sa.Column(
            "is_ambiguous", sa.Integer(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column("intensity_variable", sa.String(length=32), nullable=True),
        sa.Column("max_intensity", sa.Float(), nullable=True),
        sa.Column("isotopologue_of", sa.String(length=16), nullable=True),
        sa.Column(
            "curated", sa.Integer(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column("candidates", sa.JSON(), nullable=True),
        sa.Column("members", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_peak_run_id"],
            ["batch_peak_run.batch_peak_run_id"],
            name=op.f("fk_batch_peak_run_anchor_batch_peak_run_id_batch_peak_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "batch_peak_run_id",
            "batch_peak_id",
            name=op.f("pk_batch_peak_run_anchor"),
        ),
    )


def downgrade() -> None:
    op.drop_table("batch_peak_run_anchor")
    op.drop_index("ix_batch_peak_run_batch_current", table_name="batch_peak_run")
    op.drop_index(op.f("ix_batch_peak_run_created_by"), table_name="batch_peak_run")
    op.drop_index(
        op.f("ix_batch_peak_run_sample_batch_id"), table_name="batch_peak_run"
    )
    op.drop_table("batch_peak_run")
