"""Batch-level verdicts: one judgment per species at a batch peak.

A user's verdict on a batch peak's consensus formula, covering every sample in the
batch whose peak folded into that anchor and that carries no verdict of its own
(docs/dev/peak_assignment_continuity.md section 4, peak_assignment_batch_primary.md
section 6.3). Its own table rather than rows in assignment_verification, so the
calibration label pool - which selects from that table alone - can never count a
batch-level judgment, let alone once per member sample.

batch_peak_id carries no foreign key on purpose: a re-fold that leaves an anchor
memberless deletes it, and anchor ids are minted once and never reused, so a
dangling id cannot re-attach to another species and a verdict outlives the machine
lifecycle of the anchor it judged. One live row per (anchor, formula, mechanism):
the same partial unique index, NULLS NOT DISTINCT, as the per-sample table.

The feature is not in production, so there is nothing to backfill.

Revision ID: b8f3d7c2e9a1
Revises: e7b1c9d4a2f6
Create Date: 2026-09-03 22:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8f3d7c2e9a1"
down_revision: Union[str, None] = "e7b1c9d4a2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_peak_verification",
        sa.Column("batch_peak_verification_id", sa.String(length=32), nullable=False),
        sa.Column("sample_batch_id", sa.String(length=16), nullable=False),
        sa.Column("batch_peak_id", sa.String(length=16), nullable=False),
        sa.Column("assigned_formula", sa.String(length=256), nullable=False),
        sa.Column("ionization_mechanism_id", sa.String(length=16), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("evidence_level", sa.String(length=24), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("superseded_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "verdict IN ('confirmed', 'rejected', 'unsure')",
            name=op.f("ck_batch_peak_verification_verdict_valid"),
        ),
        sa.CheckConstraint(
            "evidence_level IS NULL OR evidence_level IN "
            "('reference_standard', 'msms', 'orthogonal', 'pattern', 'visual')",
            name=op.f("ck_batch_peak_verification_evidence_level_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_batch_id"],
            ["sample_batch.sample_batch_id"],
            name=op.f("fk_batch_peak_verification_sample_batch_id_sample_batch"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["user.id"],
            name=op.f("fk_batch_peak_verification_verified_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "batch_peak_verification_id",
            name=op.f("pk_batch_peak_verification"),
        ),
    )
    op.create_index(
        op.f("ix_batch_peak_verification_sample_batch_id"),
        "batch_peak_verification",
        ["sample_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_verification_batch_peak_id"),
        "batch_peak_verification",
        ["batch_peak_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_peak_verification_verified_by"),
        "batch_peak_verification",
        ["verified_by"],
        unique=False,
    )
    # One live verdict per claim at an anchor. NULLS NOT DISTINCT so a null
    # mechanism is one claim, not infinitely many - as on the per-sample table.
    op.create_index(
        "uq_batch_peak_verification_current",
        "batch_peak_verification",
        ["batch_peak_id", "assigned_formula", "ionization_mechanism_id"],
        unique=True,
        postgresql_where=sa.text("superseded_utc IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_batch_peak_verification_current", table_name="batch_peak_verification"
    )
    op.drop_index(
        op.f("ix_batch_peak_verification_verified_by"),
        table_name="batch_peak_verification",
    )
    op.drop_index(
        op.f("ix_batch_peak_verification_batch_peak_id"),
        table_name="batch_peak_verification",
    )
    op.drop_index(
        op.f("ix_batch_peak_verification_sample_batch_id"),
        table_name="batch_peak_verification",
    )
    op.drop_table("batch_peak_verification")
