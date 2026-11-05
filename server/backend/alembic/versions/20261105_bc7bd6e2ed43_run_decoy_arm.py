"""Mark a decoy assignment run with the arm it ran

A decoy run is the in-app engine re-run on a sample with its chemistry made
wrong on purpose (the m/z axis shifted, or the adducts swapped), so that what
it still assigns bounds the engine's false assignments (assignment quality
plan, step 5.2). It is written by the same engine as the sample's real run, so
the engine column cannot tell the two apart, and every reader that takes a
sample's latest run - the batch fold, the ledger read, the sample status, the
recalibration pool, the retention budget - has to. The arm goes in a column of
its own (``peak_assignment_run.decoy``: ``mz_shift_ppm=+9``, ``swap_adducts``),
NULL for every run that is not a decoy. Nothing is backfilled: no decoy run
existed before the column.

Downgrade drops the column, and with it the only mark that tells a decoy run
from the sample's result: delete the decoy runs first.

Revision ID: bc7bd6e2ed43
Revises: 13278d0d892f
Create Date: 2026-11-05 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "bc7bd6e2ed43"
down_revision: Union[str, Sequence[str], None] = "13278d0d892f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "peak_assignment_run",
        sa.Column("decoy", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("peak_assignment_run", "decoy")
