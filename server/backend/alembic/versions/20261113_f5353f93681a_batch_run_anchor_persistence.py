"""The batch search's persistence admission in the run snapshot: two columns

Step 4.2b of the assignment quality plan lets the batch's untargeted search
take a batch peak whose peak is present in more of its partition's spectra
than the batch's split of their occurrence distribution, however faint
(``batch_untargeted.py``). The search records that admission on the anchor
under ``batch_peak.provenance.persistence``, and the consensus holds the
reading it elected there at candidate until a second observation corroborates
it, recording why under ``batch_peak.provenance.tier_reasons``.

- ``batch_peak_run_anchor.persistence`` and ``batch_peak_run_anchor.tier_reasons``
  keep both as a run left them, as ``known_species`` keeps a decision, so the
  run selector shows an earlier run's admissions and caps beside its tiers.
  Nullable with no backfill: no anchor carried either before this revision.

No action is added: the search writes under its own, ``search_untargeted``.

Revision ID: f5353f93681a
Revises: e192df92f0ce
Create Date: 2026-11-13 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f5353f93681a"
down_revision: Union[str, Sequence[str], None] = "e192df92f0ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("persistence", sa.JSON(), nullable=True),
    )
    op.add_column(
        "batch_peak_run_anchor",
        sa.Column("tier_reasons", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run_anchor", "tier_reasons")
    op.drop_column("batch_peak_run_anchor", "persistence")
