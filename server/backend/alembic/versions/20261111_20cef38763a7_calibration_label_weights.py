"""Record the evidence-level weights a calibration curve was fit under

A refit from verdicts weighs each label by the evidence its verdict states
(MAS-329; ``mascope_tools.composition.calibration.EVIDENCE_LEVEL_WEIGHTS``):
a reference standard or an MS/MS spectrum above a pattern judgment or a
visual look. ``assignment_calibration.label_weights`` records the map a curve
was fit under, ``{evidence level: weight}``, so a later change of the weights
stays traceable per curve.

Every curve stored before this revision was fit on unweighted labels, and
NULL says so: no backfill.

Downgrade drops the column.

Revision ID: 20cef38763a7
Revises: 094b03740346
Create Date: 2026-11-11 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20cef38763a7"
down_revision: Union[str, Sequence[str], None] = "094b03740346"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assignment_calibration",
        sa.Column("label_weights", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assignment_calibration", "label_weights")
