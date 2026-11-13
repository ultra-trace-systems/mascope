"""Record the reagent profile a calibration curve was fit under

A curve is valid per instrument and per reagent chemistry, and the
``(instrument, score_version)`` key says only the first. The profiles
design's first calibration step (``docs/dev/chemistry_profiles.md``, 3.3) is
to record the profile on every fit and warn when a run under another profile
reads the curve; keying the lookup on it is a later step.
``assignment_calibration.reagent_profile`` holds the profile name every label
of the curve was scored under (MAS-63).

Every curve stored before this revision pooled its labels without recording
their profile, and NULL says so: no backfill. NULL also marks a curve fit on
labels of several profiles.

Downgrade drops the column.

Revision ID: 02867c7fba58
Revises: e192df92f0ce
Create Date: 2026-11-13 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "02867c7fba58"
down_revision: Union[str, Sequence[str], None] = "e192df92f0ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assignment_calibration",
        sa.Column("reagent_profile", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assignment_calibration", "reagent_profile")
