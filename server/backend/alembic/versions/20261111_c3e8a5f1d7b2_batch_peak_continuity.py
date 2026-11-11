"""The ratio-stability grade on the batch ledger: batch_peak.continuity_grade and .continuity

Step 4.11 of the assignment quality plan adds the continuity pass
(``continuity.py``): for every pair of related batch peaks - an M0 and its
isotope line, two channels of one neutral - it reads the within-sample ratio
across a time-ordered batch, its spread and its drift, and grades it. The grade
and its detail are anchor columns, because the ledger is read off
``batch_peak`` alone and never joins the occurrence table:

- ``batch_peak.continuity_grade``: one of ``red``, ``drift``, ``stable``,
  ``inconclusive``, ``insufficient``, held to those by a CHECK; NULL for an
  anchor in no pair.
- ``batch_peak.continuity``: the detail (JSON): the pairs, and what the pass
  graded, against which a reader marks a grade the ledger has moved past.
- ``batch_peak_run_anchor`` gains both, so a run's snapshot keeps the grade
  the run left, as it keeps its series and co-variation records.

All nullable, with no backfill: no pass has graded an anchor before this
revision. Downgrade drops them, and every grade with them.

Revision ID: c3e8a5f1d7b2
Revises: f7b795c6335b
Create Date: 2026-11-11 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3e8a5f1d7b2"
down_revision: Union[str, Sequence[str], None] = "f7b795c6335b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_batch_peak_continuity_grade_valid"
GRADES = "'red', 'drift', 'stable', 'inconclusive', 'insufficient'"


def upgrade() -> None:
    for table in ("batch_peak", "batch_peak_run_anchor"):
        op.add_column(
            table, sa.Column("continuity_grade", sa.String(length=16), nullable=True)
        )
        op.add_column(table, sa.Column("continuity", sa.JSON(), nullable=True))
    op.create_check_constraint(
        op.f(CONSTRAINT),
        "batch_peak",
        f"continuity_grade IS NULL OR continuity_grade IN ({GRADES})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "batch_peak", type_="check")
    for table in ("batch_peak_run_anchor", "batch_peak"):
        op.drop_column(table, "continuity")
        op.drop_column(table, "continuity_grade")
