"""The probable-blend reason in a batch run's snapshot: one column

Step 4.8 of the assignment quality plan reads, in every consensus pass, how a
batch peak's two leading formulas disagree: where their members separate
cleanly along intensity or acquisition time, two chemistries share the trace,
and the consensus records a probable blend under
``batch_peak.provenance.blend`` (``batch_blend.py``). It writes no new action:
the fold, the rebuild and every pass that recomputes a consensus write it.

- ``batch_peak_run_anchor.blend`` keeps the record an anchor carried as a run
  left it, as ``known_species`` keeps a decision, so the run selector shows an
  earlier run's blends too. Nullable with no backfill: no anchor carried one
  before this revision.

Revision ID: e192df92f0ce
Revises: 5d2c81f0e4a7
Create Date: 2026-11-13 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e192df92f0ce"
down_revision: Union[str, Sequence[str], None] = "5d2c81f0e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("blend", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "blend")
