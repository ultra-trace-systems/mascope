"""Clear a sample item TIC that is no finite number: sample_item.tic

A production server held three TOF sample items whose TIC was NaN. The
sample listing cannot send a NaN in JSON, so it sent it as null, with a
warning, on every listing of their batch. The likeliest source is a TOF file
that recorded no signal: its reader rescaled each scan to the file's total,
and 0/0 is NaN on every scan.

The reader now gives such a file a TIC of 0, and an item whose TIC still
reads as no finite number stores none. This clears the rows written before:
NULL is an item with no TIC, as the listing already sent it.

``fix_sample_item_tic`` reads such an item's TIC again from its file, which
now gives 0 for a file of zeros; it reads only items that store a TIC, so it
leaves the rows this clears alone.

Downgrade restores nothing: the NaN was never a value.

Revision ID: 566299655056
Revises: a7d4e2c9f163
Create Date: 2026-11-10 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "566299655056"
down_revision: Union[str, Sequence[str], None] = "a7d4e2c9f163"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres, unlike IEEE 754, holds NaN equal to itself: IN finds it.
    op.execute(
        "UPDATE sample_item SET tic = NULL"
        " WHERE tic IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)"
    )


def downgrade() -> None:
    pass
