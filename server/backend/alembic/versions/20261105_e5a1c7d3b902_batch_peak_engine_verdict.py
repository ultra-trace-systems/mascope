"""Add the importing engine's own verdict to the batch ledger: engine_tier, engine_claim

A batch import row (``batch_import.py``) carried the m/z, the formulas and the
mechanism and no verdict, so every identity an external engine held only as a
candidate landed in the batch ledger looking exactly like one it held as
assigned: the ledger's ``consensus_tier`` is this server's measurement of the
members, never the engine's word. The per-sample import solved the same gap
with ``peak_assignment.engine_tier`` (revision d4b8f2c60a13); this is the batch
counterpart.

- ``batch_peak.engine_tier`` is the tier the importing engine stated for the
  identity the anchor's consensus names, in the tier vocabulary;
  ``batch_peak.engine_claim`` is the engine's own words about it, free text.
  Both are written by a batch import alone, on the anchors whose consensus the
  import's identity won, and cleared from every other anchor of the batch.
  The consensus writer clears them on an anchor whose identity then moves, and
  a rebuild clears them across the batch, so a verdict is never shown beside a
  formula the engine did not give it for.
- ``batch_peak_run_anchor`` carries the same two columns, so the run selector
  shows an import's verdicts as that run left them.

Read by no roll-up and by no fold: the derived ``consensus_tier`` stays the
ledger's tier (decision 20 of the assignment quality plan). Nullable with no
backfill: no anchor written before this revision came from an import that sent
a verdict. ``engine_tier`` matches ``consensus_tier``'s width because it holds
the same vocabulary; ``engine_claim`` is bounded by the payload field to the
column's length.

Revision ID: e5a1c7d3b902
Revises: c39f8f2c0b3b
Create Date: 2026-11-05 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5a1c7d3b902"
down_revision: Union[str, Sequence[str], None] = "c39f8f2c0b3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("batch_peak", "batch_peak_run_anchor")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table, sa.Column("engine_tier", sa.String(length=24), nullable=True)
        )
        op.add_column(
            table, sa.Column("engine_claim", sa.String(length=1024), nullable=True)
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "engine_claim")
        op.drop_column(table, "engine_tier")
