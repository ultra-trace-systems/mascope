"""Add n_scans and n_scans_total columns to match_isotope

Records the matched peak's scan presence counts on each stored line: how
many of the scans averaged into an Orbitrap peak hold it (n_scans) and how
many those are (n_scans_total). The targeted matcher's frame carries them
from the peak store, and the v2 fit score reads a dim line at its mean per
scan from them. Without them on the stored row, an aggregation that scores
from match_isotope read every line as a file without counts does, so the
same ion scored differently there than in Stage A.

Additive only: both columns are nullable with no server default and no
backfill, so on this large pre-existing table the ALTER takes only a brief
metadata lock. Pre-existing rows stay NULL and score as before, as do TOF
rows and lines whose peak's store keeps no counts.

Revision ID: 3b8d5e1f7a2c
Revises: 7c1d2e9a4b6f
Create Date: 2026-11-17 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3b8d5e1f7a2c"
down_revision: Union[str, Sequence[str], None] = "7c1d2e9a4b6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_isotope",
        sa.Column("n_scans", sa.Integer(), nullable=True),
    )
    op.add_column(
        "match_isotope",
        sa.Column("n_scans_total", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_isotope", "n_scans_total")
    op.drop_column("match_isotope", "n_scans")
