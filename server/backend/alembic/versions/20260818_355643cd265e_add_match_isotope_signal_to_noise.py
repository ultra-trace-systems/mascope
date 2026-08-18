"""Add signal_to_noise column to match_isotope

Persists the per-peak signal-to-noise that compute_match_isotopes already
measures, so the v2 fit score's detectability gate runs on real data on the
DB-read scoring paths instead of the abundance-heuristic fallback.

Additive only: the column is nullable with no server default and no backfill,
so on this large pre-existing table the ALTER takes only a brief metadata
lock. Pre-existing rows stay NULL ("no SNR for this row") and are scored in
the v2 no-SNR fallback mode per row.

Revision ID: 355643cd265e
Revises: b7e4c9a2f1d3
Create Date: 2026-08-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "355643cd265e"
down_revision: Union[str, Sequence[str], None] = "b7e4c9a2f1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_isotope",
        sa.Column("signal_to_noise", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_isotope", "signal_to_noise")
