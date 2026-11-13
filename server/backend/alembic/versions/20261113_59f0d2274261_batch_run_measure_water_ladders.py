"""The water ladders' measurement as a batch run: action 'measure_water_ladders', one snapshot column

The ninth rider of step 4.2 of the assignment quality plan adds a batch-level
operation, "Measure water ladders" (``batch_water_ladder.py``), which reads
each reagent core's water ladder on the batch's own series, per acquisition
segment, and where a rung passes claims its batch peak as the reagent's ion in
the samples of the segments it passed in. It rewrites the members and the
samples' rows it claims, and records the rung on the anchor under
``batch_peak.provenance.water_ladder``.

- It writes the ledger, so it opens a batch run, snapshotting the run before
  it, and the run's ``action`` has to be allowed to name it: the check
  constraint gains the one value.
- ``batch_peak_run_anchor.water_ladder`` keeps the record an anchor carried
  as a run left it, as ``known_species`` keeps a decision, so the run
  selector shows an earlier run's rungs too. Nullable with no backfill: no
  anchor carried one before this revision.

Revision ID: 59f0d2274261
Revises: 5d2c81f0e4a7
Create Date: 2026-11-13 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "59f0d2274261"
down_revision: Union[str, Sequence[str], None] = "5d2c81f0e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks', 'decide_known_species')"
)
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks', 'decide_known_species', "
    "'measure_water_ladders')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("water_ladder", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "water_ladder")
    # A measuring run cannot be named once the value is gone: such runs go with
    # it. The rungs it claimed stay claimed: their members and the samples'
    # rows say so, as a sample's own reagent rows do.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'measure_water_ladders'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
