"""The per-sample import's engine level: peak_assignment.engine_level

The identification level (step 5.8, revision 5e8a3c1d9b47) gave the batch
import an ``engine_level`` beside its ``engine_tier``, on ``batch_peak``. The
per-sample import had none: an external engine's per-sample run could state
its tier in Mascope's vocabulary but not its level, and the row's own
``level`` is this server's statement, never an import's.

- ``peak_assignment.engine_level`` is the level the producing engine stated
  for the row, in the shared scale, stored as the import supplied it beside
  ``engine_tier``. NULL where the engine stated none, which is every in-app
  row and every row from before this revision: nothing is backfilled.

Read by no roll-up: the level moves no tier. Four characters, as ``level``.

Revision ID: 7d2f9b4e6a13
Revises: fa0017bcd993
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7d2f9b4e6a13"
down_revision: Union[str, Sequence[str], None] = "fa0017bcd993"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "peak_assignment",
        sa.Column("engine_level", sa.String(length=4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("peak_assignment", "engine_level")
