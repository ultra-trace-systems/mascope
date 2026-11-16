"""Let a dataset and a sample batch name the chemistry context they were drawn from

A run whose context is ``auto`` took the reagent profile's default, and the
engine resolved "ambient air" for a chamber experiment because nothing told it
otherwise (step 3.6 of the assignment quality plan). ``dataset`` and
``sample_batch`` each gain ``chemistry_context``: the name of a shipped context
(``mascope_tools.composition.profiles.CHEMISTRY_CONTEXTS``), which a run on
``auto`` takes before the profile's default, the batch's before the dataset's.

NULL names none, which is every row stored before this revision: no backfill,
so every existing run resolves as it did.

Downgrade drops both columns.

Revision ID: 7c1d2e9a4b6f
Revises: 59f0d2274261
Create Date: 2026-11-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7c1d2e9a4b6f"
down_revision: Union[str, Sequence[str], None] = "59f0d2274261"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset",
        sa.Column("chemistry_context", sa.String(64), nullable=True),
    )
    op.add_column(
        "sample_batch",
        sa.Column("chemistry_context", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sample_batch", "chemistry_context")
    op.drop_column("dataset", "chemistry_context")
