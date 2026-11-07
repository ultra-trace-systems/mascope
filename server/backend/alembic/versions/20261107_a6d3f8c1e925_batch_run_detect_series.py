"""The series detection as a batch run: action 'detect_series', snapshot series

Step 4.1 of the assignment quality plan adds a batch-level operation, "Detect
series" (``batch_series.py``), which walks the batch's homologous series,
measures what they propose against the members and records the series on the
anchors, under ``batch_peak.provenance.series``.

- Like the untargeted search it rewrites the ledger, so it opens a batch run,
  snapshotting the run before it, and the run's ``action`` has to be allowed
  to name it: the check constraint gains the one value.
- ``batch_peak_run_anchor.series`` keeps the record an anchor carried as a
  run left it, as ``curated`` keeps its pin, so the run selector shows which
  rows a series vouched for in an earlier run too. Nullable with no backfill:
  no anchor carried one before this revision.

Revision ID: a6d3f8c1e925
Revises: e5a1c7d3b902
Create Date: 2026-11-07 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a6d3f8c1e925"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d3b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = "action IN ('fold', 'rebuild', 'search_untargeted', 'import')"
AFTER = "action IN ('fold', 'rebuild', 'search_untargeted', 'import', 'detect_series')"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor", sa.Column("series", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "series")
    # A series run cannot be named once the value is gone: such runs go with it.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'detect_series'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
