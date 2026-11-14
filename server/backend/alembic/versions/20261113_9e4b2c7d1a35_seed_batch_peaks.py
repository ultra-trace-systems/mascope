"""Seeded batch-peak anchors: the batch's option and the anchor's mark

Step 4.7 of the assignment quality plan seeds a batch ledger's anchors from
the batch's target ions (``batch_peaks.seed_anchors``), so a target's trace
sits at the ion's m/z rather than wherever the first sample's peak put it.

- ``sample_batch.seed_batch_peaks``: the per-batch option, off by default,
  so every existing batch folds as before.
- ``batch_peak.seeded``: the mark on an anchor minted from a target ion;
  every existing anchor was minted by a sample's peak, so false.
- ``batch_peak_run_anchor.seeded``: the mark as a batch run's snapshot keeps
  it; false on every snapshot taken before this revision, for the same reason.

Revision ID: 9e4b2c7d1a35
Revises: e192df92f0ce
Create Date: 2026-11-13 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9e4b2c7d1a35"
down_revision: Union[str, Sequence[str], None] = "e192df92f0ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table, column in (
        ("sample_batch", "seed_batch_peaks"),
        ("batch_peak", "seeded"),
        ("batch_peak_run_anchor", "seeded"),
    ):
        op.add_column(
            table,
            sa.Column(
                column, sa.Boolean(), server_default=sa.text("false"), nullable=False
            ),
        )


def downgrade() -> None:
    # A seeded anchor stays: it is an anchor like any other once the mark is
    # gone, with its members and its frozen m/z.
    op.drop_column("batch_peak_run_anchor", "seeded")
    op.drop_column("batch_peak", "seeded")
    op.drop_column("sample_batch", "seed_batch_peaks")
