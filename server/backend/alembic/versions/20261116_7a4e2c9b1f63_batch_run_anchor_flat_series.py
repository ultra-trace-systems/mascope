"""The flat-series reading in the run snapshot: one column

Step 4.2e of the assignment quality plan reads each committed batch peak's
trace against its partition's normaliser in *Read co-variation*
(``batch_coherence.py``): a trace that holds a constant ratio to the source is
flagged as background, an origin flag that moves no tier. The run records the
reading on the anchor under ``batch_peak.provenance.flat_series``.

- ``batch_peak_run_anchor.flat_series`` keeps it as a run left it, as
  ``persistence`` keeps the search's admission, so the run selector shows an
  earlier run's flags beside its tiers. Nullable with no backfill: no anchor
  carried one before this revision.

No action is added: the reading runs under ``read_co_variation``.

Revision ID: 7a4e2c9b1f63
Revises: 9e4b2c7d1a35
Create Date: 2026-11-16 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7a4e2c9b1f63"
down_revision: Union[str, Sequence[str], None] = "9e4b2c7d1a35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("flat_series", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "flat_series")
