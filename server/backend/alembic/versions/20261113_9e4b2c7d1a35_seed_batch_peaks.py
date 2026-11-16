"""Seeded batch-peak anchors: the batch's option and the anchor's mark

Step 4.7 of the assignment quality plan seeds a batch ledger's anchors at the
batch's target ions' m/z: an anchor that a target ion's assigned line mints
sits at the ion's m/z rather than wherever that sample's peak put it
(``batch_peaks.fold_in_sample``).

- ``sample_batch.seed_batch_peaks``: the per-batch option, off by default,
  so every existing batch folds as before.
- ``batch_peak.seeded``: the mark on an anchor minted at a target ion's m/z;
  every existing anchor was minted by a sample's peak, so false.
- ``batch_peak_run_anchor.seeded``: the mark as a batch run's snapshot keeps
  it; false on every snapshot taken before this revision, for the same reason.

Revision ID: 9e4b2c7d1a35
Revises: 02867c7fba58
Create Date: 2026-11-13 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9e4b2c7d1a35"
down_revision: Union[str, Sequence[str], None] = "02867c7fba58"
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
