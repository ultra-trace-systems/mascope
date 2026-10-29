"""Keep the mass-error width an imported assignment run disclosed

An in-app run records the width it scored at in its config's
``pattern_scoring`` snapshot, and the runs listing now serves it as
``sigma_ppm``, ``sigma_source``, ``mu_ppm`` and ``mu_source``. An import's
config is opaque, so the width its engine discloses goes in a column of its
own (``peak_assignment_run.scoring_width``). Nothing is backfilled: no import
disclosed one before the field existed.

Downgrade drops the column.

Revision ID: 5f2d8c1a7b94
Revises: 9b5c3e7a1f60
Create Date: 2026-10-29 19:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5f2d8c1a7b94"
down_revision: Union[str, Sequence[str], None] = "9b5c3e7a1f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "peak_assignment_run",
        sa.Column("scoring_width", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("peak_assignment_run", "scoring_width")
