"""The merged-isotopologue reading as a batch run: action 'strip_isotopologues', two snapshot columns

The fifth rider of step 4.2 of the assignment quality plan adds a batch-level
operation, "Strip isotopologue rows" (``batch_merged_isotopologues.py``), which
reads every merged row of the batch ledger against the 13C and diagnostic
isotope lines of the merged ions below it and strips a row that holds a
line's share across the batch to that parent's isotopologue. It rewrites the
row's members and the samples' rows, and records what it read on the anchors,
under ``batch_peak.provenance.merged_isotopologue_of`` on a stripped row and
``batch_peak.provenance.merged_isotopologues`` on its parent.

- It writes the ledger, so it opens a batch run, snapshotting the run before
  it, and the run's ``action`` has to be allowed to name it: the check
  constraint gains the one value.
- ``batch_peak_run_anchor.merged_isotopologue_of`` and
  ``.merged_isotopologues`` keep the records an anchor carried as a run left
  it, as ``stamped_isotopologue_of`` keeps a stamp, so the run selector shows
  an earlier run's strips too. Nullable with no backfill: no anchor carried one
  before this revision.

Revision ID: 094b03740346
Revises: 8707722ede38
Create Date: 2026-11-12 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "094b03740346"
down_revision: Union[str, Sequence[str], None] = "8707722ede38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues')"
)
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("merged_isotopologue_of", sa.JSON(), nullable=True),
    )
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("merged_isotopologues", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "merged_isotopologues")
    op.drop_column("batch_peak_run_anchor", "merged_isotopologue_of")
    # A stripping run cannot be named once the value is gone: such runs go
    # with it. The rows it stripped stay stripped: their members and the
    # samples' rows say so, as a sample's own isotopologue rows do.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'strip_isotopologues'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
