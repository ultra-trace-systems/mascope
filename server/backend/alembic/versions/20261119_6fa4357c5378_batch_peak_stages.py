"""Add the batch stages' standing (batch_peak_stage) and the batch's mark on a member

The batch passes run as stages of one fixed order (``batch_stages.py``, MAS-983):
each reads what the stages before it wrote, so each needs to know when what it
read has moved.

- ``batch_peak_stage`` holds one row per batch and stage, written the first
  time the stage runs on the batch: ``version`` counts its runs (each record a
  run writes carries it), ``state`` is ``current``, ``running`` or ``stale``,
  and ``stale_reason`` and ``utc_stale`` say what moved and when. A fold, a
  stage before it running again, its own run stopping partway or a consensus
  recompute that leaves one of its records behind marks it stale.
- ``batch_peak_occurrence.batch_written`` is the batch's mark on a member a
  stage rewrote: the stage, its version and what the member read before. It
  lives on the member, so it outlives the batch peak's record of the decision.
  NULL on every member from before this revision and on every member a fold
  writes: nothing is backfilled, since no earlier pass kept what a member read
  before it.

Revision ID: 6fa4357c5378
Revises: 5e8a3c1d9b47
Create Date: 2026-11-19 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6fa4357c5378"
down_revision: Union[str, Sequence[str], None] = "5e8a3c1d9b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_peak_stage",
        sa.Column("sample_batch_id", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'current'"),
            nullable=False,
        ),
        sa.Column("stale_reason", sa.String(length=32), nullable=True),
        sa.Column("utc_started", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("utc_ran", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("utc_stale", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("batch_peak_run_id", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "state IN ('current', 'running', 'stale')",
            name=op.f("ck_batch_peak_stage_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_batch_id"],
            ["sample_batch.sample_batch_id"],
            name=op.f("fk_batch_peak_stage_sample_batch_id_sample_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "sample_batch_id", "stage", name=op.f("pk_batch_peak_stage")
        ),
    )
    op.add_column(
        "batch_peak_occurrence", sa.Column("batch_written", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("batch_peak_occurrence", "batch_written")
    op.drop_table("batch_peak_stage")
