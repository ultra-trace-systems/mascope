"""The samples a batch stage could not read: batch_peak_stage.skipped_samples

A stage that could not read one of the batch's samples (a moved peak file, a
spectrum that does not read) was stale until a run read every sample, so
every later step ran it again first, with the same sample failing each time
(mascope#3356). Decided in MAS-1060: such a stage is current, and it records
the samples it skipped, so the batch's status names them and a sample that
reads again marks the stage stale.

- ``batch_peak_stage.skipped_samples``: the samples the stage's latest run
  that finished could not read, each with whether its peak list read as the
  run ended. Null where it read every sample, as on every row this revision
  finds.

Revision ID: e2c7a4b9d6f3
Revises: 2989ee6e6bde
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e2c7a4b9d6f3"
down_revision: Union[str, Sequence[str], None] = "2989ee6e6bde"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_stage",
        sa.Column(
            "skipped_samples",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_stage", "skipped_samples")
