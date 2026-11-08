"""Stamp the batch ledger's last write on its current run: ledger_utc_modified

A client that keeps a copy of a batch's ledger (``mascope_viz``'s campaign
store) refetched it when the batch's modified time, its sample count or its
current run changed. Some writes to the live ledger move none of these: a
by-hand curation of a batch peak, a sample folded again under the implicit
``fold`` run, a search's or an import's rows written while a run is open. Such
a copy kept serving the ledger as it was before the edit.

``batch_peak_run.ledger_utc_modified`` is when the live ledger was last
written while that run was current. Every write to the live ledger sets it on
the current run in the write's own transaction (``batch_runs.touch_ledger``),
and a run that completes sets it on itself as it becomes current. The runs
listing serves it, so a client reads it without a series call.

Nullable with no backfill: NULL is a run whose ledger nothing has written
since this revision. A backfill would move the stamp of every batch at once
and send every client's copy to fetch every batch again for no edit.

Revision ID: a7d4e2c9f163
Revises: a6d3f8c1e925
Create Date: 2026-11-08 15:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7d4e2c9f163"
down_revision: Union[str, Sequence[str], None] = "a6d3f8c1e925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_run",
        sa.Column("ledger_utc_modified", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run", "ledger_utc_modified")
