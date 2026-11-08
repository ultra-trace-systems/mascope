"""The co-variation reading as a batch run: action 'read_co_variation', snapshot co_variation

Step 4.2 of the assignment quality plan adds a batch-level operation, "Read
co-variation" (``batch_coherence.py``), which reads the trace of every
neutral seen through two channels against the other channel's and records
the reading on the anchors, under ``batch_peak.provenance.co_variation``.

- It writes the ledger's anchors, so it opens a batch run, snapshotting the
  run before it, and the run's ``action`` has to be allowed to name it: the
  check constraint gains the one value.
- ``batch_peak_run_anchor.co_variation`` keeps the record an anchor carried as
  a run left it, as ``series`` keeps its series, so the run selector shows the
  reading of an earlier run too. Nullable with no backfill: no anchor carried
  one before this revision.

Revision ID: b7e4c2d9f1a3
Revises: 566299655056
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7e4c2d9f1a3"
down_revision: Union[str, Sequence[str], None] = "566299655056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = "action IN ('fold', 'rebuild', 'search_untargeted', 'import', 'detect_series')"
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor", sa.Column("co_variation", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "co_variation")
    # A co-variation run cannot be named once the value is gone: such runs go
    # with it.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'read_co_variation'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
