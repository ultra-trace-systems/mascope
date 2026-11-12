"""The isotopologue stamping as a batch run: action 'stamp_isotopologues', two snapshot columns

The second rider of step 4.2 of the assignment quality plan adds a batch-level
operation, "Stamp isotopologues" (``batch_isotopologues.py``), which predicts
the diagnostic isotopologues of every committed monoisotopic batch peak, judges
each one's height ratio to its parent in every sample that shows both, and
stamps the lines that hold it across the batch. It records what it read on the
anchors, under ``batch_peak.provenance.diagnostic_isotopologues`` on a parent
and ``batch_peak.provenance.stamped_isotopologue_of`` on a stamped line.

- It writes the ledger's anchors, so it opens a batch run, snapshotting the
  run before it, and the run's ``action`` has to be allowed to name it: the
  check constraint gains the one value.
- ``batch_peak_run_anchor.diagnostic_isotopologues`` and
  ``.stamped_isotopologue_of`` keep the records an anchor carried as a run left
  it, as ``co_variation`` keeps its reading, so the run selector shows an
  earlier run's stamps too. Nullable with no backfill: no anchor carried one
  before this revision.

Revision ID: 8707722ede38
Revises: c3e8a5f1d7b2
Create Date: 2026-11-11 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8707722ede38"
down_revision: Union[str, Sequence[str], None] = "c3e8a5f1d7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation')"
)
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("diagnostic_isotopologues", sa.JSON(), nullable=True),
    )
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("stamped_isotopologue_of", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "stamped_isotopologue_of")
    op.drop_column("batch_peak_run_anchor", "diagnostic_isotopologues")
    # A stamping run cannot be named once the value is gone: such runs go with
    # it.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'stamp_isotopologues'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
