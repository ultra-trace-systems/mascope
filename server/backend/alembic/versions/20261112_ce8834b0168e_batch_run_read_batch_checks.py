"""The batch isotope checks as a batch run: action 'read_batch_checks', one snapshot column

The sixth rider of step 4.2 of the assignment quality plan adds a batch-level
operation, "Read batch checks" (``batch_checks.py``), which reads the facts only
a batch can state of a batch peak's reading - the halogen lock, the label twin
and the neutral pair - and records them on the anchor under
``batch_peak.provenance.batch_checks``. It writes no member and moves no tier.

- It writes the ledger's records, so it opens a batch run, snapshotting the
  run before it, and the run's ``action`` has to be allowed to name it: the
  check constraint gains the one value.
- ``batch_peak_run_anchor.batch_checks`` keeps the facts an anchor carried as a
  run left them, as ``co_variation`` keeps a reading, so the run selector
  shows an earlier run's facts too. Nullable with no backfill: no anchor
  carried one before this revision.

Revision ID: ce8834b0168e
Revises: 20cef38763a7
Create Date: 2026-11-12 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ce8834b0168e"
down_revision: Union[str, Sequence[str], None] = "20cef38763a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues')"
)
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("batch_checks", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "batch_checks")
    # A checks run cannot be named once the value is gone: such runs go with
    # it. The facts it recorded on the live anchors stay in their provenance,
    # where nothing older reads them.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'read_batch_checks'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
