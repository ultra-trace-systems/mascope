"""The known-species decision as a batch run: action 'decide_known_species', one snapshot column

The seventh rider of step 4.2 of the assignment quality plan adds a
batch-level operation, "Decide known species" (``batch_known_species.py``),
which pools the evidence for each known species - an ion of the source's
library, or a compound a reference list tags as background - over the files of
its partition, decides once whether the batch holds it, and where it does
claims it in every file its peak is present in. It rewrites the members and
the samples' rows it claims, and records the decision on the anchor under
``batch_peak.provenance.known_species``.

- It writes the ledger, so it opens a batch run, snapshotting the run before
  it, and the run's ``action`` has to be allowed to name it: the check
  constraint gains the one value.
- ``batch_peak_run_anchor.known_species`` keeps the record an anchor carried
  as a run left it, as ``merged_isotopologue_of`` keeps a strip, so the run
  selector shows an earlier run's decisions too. Nullable with no backfill: no
  anchor carried one before this revision.

Revision ID: 5d2c81f0e4a7
Revises: ce8834b0168e
Create Date: 2026-11-12 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5d2c81f0e4a7"
down_revision: Union[str, Sequence[str], None] = "ce8834b0168e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_run_action_valid"
BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks')"
)
AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks', 'decide_known_species')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", AFTER)
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("known_species", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "known_species")
    # A deciding run cannot be named once the value is gone: such runs go with
    # it. The species it claimed stay claimed: their members and the samples'
    # rows say so, as a sample's own reagent rows do.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'decide_known_species'")
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "batch_peak_run", BEFORE)
