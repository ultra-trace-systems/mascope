"""A hand-assigned isotope line stores its own ion

A peak assigned by hand as one of a formula's isotope lines (`set_assignment`
with an `M+n` label) stored the search hit's ion, which names the
monoisotopic line, in ``ion_formula``, and the line in ``isotope_formula``. It
names no owner, so its ion is all the batch fold reads it by: a refold filed
it as the monoisotopic ion at the line's m/z (mascope#3661). From here such a
row stores the line as its ion, as an untargeted run's line does
(``curation._committed_ion``).

The rows already written are put in that shape: a ``manual`` ``iso_child``
row with no owner takes its ``isotope_formula`` as its ion, or no ion when it
names no line, as a new one would. A batch ledger folded from them reads
them right at its next rebuild.

Downgrade leaves the rows as they are: the monoisotopic ion they stored is
not kept anywhere, and the old code reads either shape.

Revision ID: b81e376e5236
Revises: b5bc98c3e9c4
Create Date: 2026-11-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b81e376e5236"
down_revision: Union[str, Sequence[str], None] = "b5bc98c3e9c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE peak_assignment SET ion_formula = isotope_formula "
            "WHERE source = 'manual' AND role = 'iso_child' "
            "AND owner_peak_assignment_id IS NULL "
            "AND ion_formula IS DISTINCT FROM isotope_formula"
        )
    )


def downgrade() -> None:
    pass
