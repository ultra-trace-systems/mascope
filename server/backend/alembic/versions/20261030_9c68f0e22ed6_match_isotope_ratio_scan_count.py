"""Add ratio_scan_count column to match_isotope

Records the number of scans an Orbitrap line's ratio to its anchor was read
over: the scans where the line and its anchor are both present. A scan keeps
a centroid only where it clears that scan's floor, so a dim line is read over
the scans that kept it, not diluted by the ones that censored it, and the
count says how many that was.

Additive only: the column is nullable with no server default and no backfill,
so on this large pre-existing table the ALTER takes only a brief metadata
lock. Pre-existing rows stay NULL, as do TOF rows, whose ratio is still the
mean over every scan.

Revision ID: 9c68f0e22ed6
Revises: 5f2d8c1a7b94
Create Date: 2026-10-30 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9c68f0e22ed6"
down_revision: Union[str, Sequence[str], None] = "5f2d8c1a7b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_isotope",
        sa.Column("ratio_scan_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_isotope", "ratio_scan_count")
