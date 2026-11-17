"""Add the identification level: peak_assignment.level and the engine's level on the batch ledger

Step 5.8 of the assignment quality plan states an identification level on every
committed row of a run, in the scale peaky and Mascope share (decision 38):
Schymanski's numbers with a letter for the kind of evidence (``4a``, ``5a``,
...). The level, its one-line evidence, its lift, the facts it was read from
and the rivals with their tests are in the row's provenance
(``identification_level``).

- ``peak_assignment.level`` is the level alone, as a column beside ``tier``, so
  the ledger filters on it and the batch ledger counts the levels its members'
  runs stated without reading every member's provenance. NULL on a row that
  commits no formula, on an imported row and on every row from before this
  revision: the level is stated when a run is made, never backfilled.
- ``batch_peak.engine_level`` is the identification level an importing engine
  stated for the identity the anchor's consensus names, and ``engine_evidence``
  and ``engine_lift`` the engine's two lines about it as it wrote them, beside
  its ``engine_tier`` and ``engine_claim`` (revision e5a1c7d3b902), written and
  cleared with them. ``batch_peak_run_anchor`` carries it too, so the run
  selector shows an import's levels as that run left them.

Read by no roll-up and by no fold: the level moves no tier. Four characters
hold the longest level of the scale with room.

Revision ID: 5e8a3c1d9b47
Revises: 3b8d5e1f7a2c
Create Date: 2026-11-17 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5e8a3c1d9b47"
down_revision: Union[str, Sequence[str], None] = "3b8d5e1f7a2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENGINE_LEVEL_TABLES = ("batch_peak", "batch_peak_run_anchor")


def upgrade() -> None:
    op.add_column(
        "peak_assignment", sa.Column("level", sa.String(length=4), nullable=True)
    )
    for table in ENGINE_LEVEL_TABLES:
        op.add_column(
            table, sa.Column("engine_level", sa.String(length=4), nullable=True)
        )
        for column in ("engine_evidence", "engine_lift"):
            op.add_column(
                table, sa.Column(column, sa.String(length=1024), nullable=True)
            )


def downgrade() -> None:
    for table in ENGINE_LEVEL_TABLES:
        op.drop_column(table, "engine_lift")
        op.drop_column(table, "engine_evidence")
        op.drop_column(table, "engine_level")
    op.drop_column("peak_assignment", "level")
