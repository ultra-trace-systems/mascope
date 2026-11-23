"""An anchor a file need not hold: target_compound_in_target_collection.optional_anchor

The shipped Uronium, positive calibrants carry a background tier behind the
urea series: the cyclic siloxanes and caprolactam, which one instrument's
spectra hold and another's do not (decision MAS-811: an anchor a file does
not hold is skipped, not an error). A TOF fit's kept-line share counted every
line its collection offers, so a TOF file without the background failed the
bar it cleared on the urea series alone (decided in MAS-1139).

- ``target_compound_in_target_collection.optional_anchor`` marks a compound
  of a collection as such an anchor. The share counts its lines only where
  the fit kept them. Not null, false by default, so every link this revision
  finds keeps the bar it had; the seed sets it on the shipped background tier
  when it brings the collection up to date.

Revision ID: b5e3a9d2c7f1
Revises: 4d7b2e9a6c15
Create Date: 2026-11-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b5e3a9d2c7f1"
down_revision: Union[str, Sequence[str], None] = "4d7b2e9a6c15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "target_compound_in_target_collection",
        sa.Column(
            "optional_anchor",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("target_compound_in_target_collection", "optional_anchor")
