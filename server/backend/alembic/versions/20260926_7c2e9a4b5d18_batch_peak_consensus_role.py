"""Add the consensus role of a batch peak

A per-sample ledger row the reagent or artifact pre-pass claims carries a role
and no formula: the peak is the source's own ion, or the detector's ringing,
and no compound of the sample. The batch ledger's consensus is decided over the
members that carry a formula, so an anchor whose members are all such rows had
nothing to vote on and read as unassigned - the one reading the per-sample
ledger takes care never to give it (step 3.3c of the assignment quality plan,
its follow-up built in 3.4d).

``consensus_role`` is the role that accounts for an anchor most of whose
members were claimed, beside the ``consensus_ion_formula`` the anchor already
has room for; the tier stays ``unassigned``, as the per-sample row's does. It goes onto ``batch_peak`` and
onto the snapshot a batch run keeps of it (``batch_peak_run_anchor``), whose
columns mirror the anchor's.

No backfill. An anchor folded before this revision cannot say which ion its
reagent members were: the fold recorded no registry entry for a row without a
formula, and so no ion. A ledger reads its reagent anchors once its samples are
folded again (*Rebuild batch ledger*), which is also what brings the owner
links of the ions' isotope lines across.

Revision ID: 7c2e9a4b5d18
Revises: 5193d1e942e0
Create Date: 2026-09-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7c2e9a4b5d18"
down_revision: Union[str, Sequence[str], None] = "5193d1e942e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("batch_peak", "batch_peak_run_anchor")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("consensus_role", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "consensus_role")
