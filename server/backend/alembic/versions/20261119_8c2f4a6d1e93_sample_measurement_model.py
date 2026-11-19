"""Add sample_measurement_model: the model a run-less pass fitted on a sample

A run records the measurement model it scored and capped at in its config
(``measurement_model``, with ``envelope_tolerance`` beside it), and every reader
outside the run reads it there. The ingest fold and the batch ledger's rebuild
assign a sample without a run, so the model they fitted had nowhere to be
kept: a reader of such a sample scored it at the class's width and today's
line tolerance, not at what the fold had scored its rows at (MAS-725).

One row per sample, the last run-less pass's, in the shape a run's config
keeps it. A reader takes it only where the sample has no completed in-app run.

Revision ID: 8c2f4a6d1e93
Revises: 9c3e5a7b1d24
Create Date: 2026-11-19 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8c2f4a6d1e93"
down_revision: Union[str, Sequence[str], None] = "9c3e5a7b1d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sample_measurement_model",
        sa.Column("sample_item_id", sa.String(length=16), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column(
            "sample_measurement_model_utc_recorded",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sample_item_id"], ["sample_item.sample_item_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sample_item_id"),
    )


def downgrade() -> None:
    op.drop_table("sample_measurement_model")
