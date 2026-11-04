"""Add the located precursor's intensity and energy, and each group's own peak, to ms2_group

The MS2 store now records each group's precursor
(``docs/dev/ms2_analysis.md``, section 2.5): located once per parent peak, in
the lowest-energy spectrum of its groups that holds a candidate, and followed
across the others. ``ms2_group`` lists what the store records:

- ``located_precursor_mz`` (already there, NULL until now),
  ``located_precursor_intensity`` and ``located_precursor_activation``: where
  the parent peak's precursor was found, at what intensity, and in the
  spectrum of which activation; the same on every group of one parent peak;
- ``precursor_mz`` and ``precursor_intensity``: the group's own peak followed
  from it, NULL where the precursor is spent.

All on the instrument's axis, as the store holds them. **Every new column
starts NULL**, and so does ``located_precursor_mz``: the rows are read from
the stores, which a migration does not open, and a store written before this
records no precursor. Such a store is of the previous layout version, which
the backfill (``backfill_ms2_store``) writes again and lists.

Revision ID: c4e7a2d91b38
Revises: 5f2d8c1a7b94
Create Date: 2026-11-04 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4e7a2d91b38"
down_revision: Union[str, Sequence[str], None] = "5f2d8c1a7b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ms2_group", sa.Column("located_precursor_intensity", sa.Float(), nullable=True)
    )
    op.add_column(
        "ms2_group",
        sa.Column("located_precursor_activation", sa.String(length=256), nullable=True),
    )
    op.add_column("ms2_group", sa.Column("precursor_mz", sa.Float(), nullable=True))
    op.add_column(
        "ms2_group", sa.Column("precursor_intensity", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    # Read again from the stores: an upgrade and a backfill run restore them.
    op.drop_column("ms2_group", "precursor_intensity")
    op.drop_column("ms2_group", "precursor_mz")
    op.drop_column("ms2_group", "located_precursor_activation")
    op.drop_column("ms2_group", "located_precursor_intensity")
